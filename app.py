from flask import Flask, render_template, request, redirect, flash, url_for, jsonify, session
from flask_login import logout_user, login_required, login_user, current_user
import os
import re
import secrets
from werkzeug.utils import secure_filename
from dotenv import load_dotenv
from sqlalchemy.exc import IntegrityError
from sqlalchemy import or_, inspect, text
from datetime import datetime, timedelta
from authlib.integrations.flask_client import OAuth


# ----- Load environment -----
load_dotenv()


# ----- Constants -----
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif'}
MAX_FILE_SIZE = 16 * 1024 * 1024  # 16 MB
UPLOAD_FOLDER = 'static/profile_pics'

# User color palette for calendar display
USER_COLORS = [
    "#FF8A80", "#FFD180", "#FFFF8D", "#B9F6CA", 
    "#80D8FF", "#B388FF", "#CFD8DC", "#FF80AB", "#EA80FC"
]


# ----- App config -----
app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'dev-secret')
_database_url = (os.environ.get('DATABASE_URL') or '').strip()
app.config['SQLALCHEMY_DATABASE_URI'] = _database_url or 'sqlite:///dev.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['MAX_CONTENT_LENGTH'] = MAX_FILE_SIZE
app.config['GOOGLE_CLIENT_ID'] = os.environ.get('GOOGLE_CLIENT_ID', '')
app.config['GOOGLE_CLIENT_SECRET'] = os.environ.get('GOOGLE_CLIENT_SECRET', '')
if os.environ.get('FLASK_SESSION_COOKIE_SECURE', '').lower() in ('1', 'true', 'yes'):
    app.config['SESSION_COOKIE_SECURE'] = True


# ----- Imports -----
"""Flask extensions"""
from extras.extensions import db, login_manager
db.init_app(app)
login_manager.init_app(app)
login_manager.login_view = 'login'


@login_manager.unauthorized_handler
def _login_manager_unauthorized():
    return redirect(url_for('login', next=request.path))


oauth = OAuth(app)
if app.config['GOOGLE_CLIENT_ID'] and app.config['GOOGLE_CLIENT_SECRET']:
    oauth.register(
        name='google',
        client_id=app.config['GOOGLE_CLIENT_ID'],
        client_secret=app.config['GOOGLE_CLIENT_SECRET'],
        server_metadata_url='https://accounts.google.com/.well-known/openid-configuration',
        client_kwargs={
            'scope': (
                'openid email profile '
                'https://www.googleapis.com/auth/calendar.readonly'
            ),
        },
    )
"""ASU API"""
from extras.api import fetch_class_by_section, parse_class_item
"""Google Calendar"""
from extras import google_calendar as gcal
"""iCloud Calendar (CalDAV)"""
from extras import icloud_calendar as icloud
"""Models"""
import models


# ----- Helper Functions -----
def get_user_color(user_id):
    """Generate consistent color for a user based on their ID."""
    return USER_COLORS[user_id % len(USER_COLORS)]


def _google_oauth_configured():
    return bool(app.config.get('GOOGLE_CLIENT_ID') and app.config.get('GOOGLE_CLIENT_SECRET'))


def get_related_user_ids(user_id):
    """Get all user IDs related to the given user (friends + group members).
    
    Returns:
        tuple: (set of user_ids, list of friend_user_ids, list of groups_data)
    """
    # Get friends
    friends_query = models.Friendship.query.filter(
        (models.Friendship.status == 'accepted') &
        or_(
            models.Friendship.requester_id == user_id,
            models.Friendship.receiver_id == user_id
        )
    ).all()
    
    friend_users = []
    for f in friends_query:
        if f.requester_id == user_id:
            friend_users.append(f.receiver)
        else:
            friend_users.append(f.requester)
    
    friends_ids = [u.id for u in friend_users]
    
    # Get groups and their members (only accepted memberships), ordered by display_order
    my_memberships = models.GroupMember.query.filter_by(user_id=user_id, status='accepted').order_by(models.GroupMember.display_order).all()
    groups_data = []
    all_related_user_ids = set(friends_ids)
    all_related_user_ids.add(user_id)
    
    for m in my_memberships:
        group = m.group
        # Only include accepted members in the group
        member_ids = [gm.user_id for gm in group.members if gm.status == 'accepted']
        groups_data.append({
            "id": group.id,
            "name": group.name,
            "members": member_ids,
            "profile_image": group.profile_image
        })
        all_related_user_ids.update(member_ids)
    
    return all_related_user_ids, friends_ids, groups_data


def _ensure_calendar_schema():
    """Add new columns/tables on existing SQLite/Postgres DBs (create_all alone is insufficient for ALTER)."""
    if getattr(app, '_calendar_schema_ensured', False):
        return
    try:
        inspector = inspect(db.engine)
        tables = inspector.get_table_names()
        if 'users' in tables:
            cols = {c['name'] for c in inspector.get_columns('users')}
            if 'google_refresh_token' not in cols:
                with db.engine.begin() as conn:
                    conn.execute(text('ALTER TABLE users ADD COLUMN google_refresh_token TEXT'))

        # Add oauth_token_id to user_linked_calendars if it doesn't exist yet
        if 'user_linked_calendars' in tables:
            cal_cols = {c['name'] for c in inspector.get_columns('user_linked_calendars')}
            if 'oauth_token_id' not in cal_cols:
                with db.engine.begin() as conn:
                    conn.execute(text(
                        'ALTER TABLE user_linked_calendars '
                        'ADD COLUMN oauth_token_id INTEGER REFERENCES user_oauth_tokens(id) ON DELETE CASCADE'
                    ))

        db.create_all()

        # Backfill UserOAuthToken from legacy google_refresh_token on User rows
        users_with_token = models.User.query.filter(
            models.User.google_refresh_token.isnot(None)
        ).all()
        for u in users_with_token:
            existing = models.UserOAuthToken.query.filter_by(
                user_id=u.id, provider='google', provider_account_id=u.google_sub
            ).first()
            if not existing:
                db.session.add(models.UserOAuthToken(
                    user_id=u.id,
                    provider='google',
                    provider_account_id=u.google_sub,
                    email=u.email,
                    refresh_token=u.google_refresh_token,
                    is_login_account=True,
                ))
        db.session.commit()
    except Exception:
        app.logger.exception('Calendar schema migration failed')
        db.session.rollback()
    app._calendar_schema_ensured = True


def _local_tzinfo():
    return datetime.now().astimezone().tzinfo


def build_week_events_for_users(user_ids, week_start, week_end):
    """
    Merge DB Event rows (class-derived) with Google Calendar events for enabled linked calendars.
    week_end is exclusive (first day after the displayed week).

    Refresh tokens are resolved via UserOAuthToken rows; legacy rows with oauth_token_id=NULL
    fall back to user.google_refresh_token.
    """
    _ensure_calendar_schema()
    if not user_ids:
        return []
    uid_set = set(user_ids)
    out = []

    db_events = models.Event.query.filter(
        models.Event.user_id.in_(uid_set),
        models.Event.start >= week_start,
        models.Event.start < week_end,
    ).all()
    for ev in db_events:
        out.append({
            'day': ev.start.weekday(),
            'start': ev.start.strftime('%H:%M'),
            'end': ev.end.strftime('%H:%M'),
            'person': ev.user_id,
            'title': ev.title,
        })

    cid = app.config.get('GOOGLE_CLIENT_ID')
    csec = app.config.get('GOOGLE_CLIENT_SECRET')
    if not cid or not csec:
        return out

    users = models.User.query.filter(models.User.id.in_(uid_set)).all()
    local_tz = _local_tzinfo()
    t_min, t_max = gcal.week_bounds_rfc3339_utc(week_start, week_end)

    for user in users:
        # Collect (refresh_token, [calendar_external_ids]) pairs, one per linked account
        token_calendar_pairs = []

        google_tokens = models.UserOAuthToken.query.filter_by(
            user_id=user.id, provider='google'
        ).all()

        for oauth_tok in google_tokens:
            if not oauth_tok.refresh_token:
                continue
            cal_rows = models.UserLinkedCalendar.query.filter_by(
                user_id=user.id, provider='google',
                oauth_token_id=oauth_tok.id, included_in_main_view=True
            ).all()
            cal_ids = [r.external_id for r in cal_rows]
            if cal_ids:
                token_calendar_pairs.append((oauth_tok.refresh_token, cal_ids))

        # Legacy: calendars with no oauth_token_id → use user.google_refresh_token
        if user.google_refresh_token:
            legacy_rows = models.UserLinkedCalendar.query.filter_by(
                user_id=user.id, provider='google', included_in_main_view=True
            ).filter(models.UserLinkedCalendar.oauth_token_id.is_(None)).all()
            legacy_ids = [r.external_id for r in legacy_rows]
            if legacy_ids:
                token_calendar_pairs.append((user.google_refresh_token, legacy_ids))

        # If user has no linked calendars at all, fall back to "primary" using login token
        if not token_calendar_pairs:
            has_any = models.UserLinkedCalendar.query.filter_by(
                user_id=user.id, provider='google'
            ).first()
            if not has_any:
                login_tok = next(
                    (t for t in google_tokens if t.is_login_account and t.refresh_token),
                    None,
                )
                fallback_token = (
                    (login_tok.refresh_token if login_tok else None)
                    or user.google_refresh_token
                )
                if fallback_token:
                    token_calendar_pairs.append((fallback_token, ['primary']))

        for refresh_token, calendar_ids in token_calendar_pairs:
            try:
                access = gcal.refresh_google_access_token(cid, csec, refresh_token)
            except Exception:
                app.logger.warning(
                    'Google token refresh failed for user %s', user.id, exc_info=True
                )
                continue
            for cal_id in calendar_ids:
                try:
                    raw = gcal.events_list_for_calendar(access, cal_id, t_min, t_max)
                except Exception:
                    app.logger.warning(
                        'Google events.list failed user=%s calendar=%s', user.id, cal_id,
                        exc_info=True,
                    )
                    continue
                for slot in gcal.google_events_to_week_slots(raw, week_start, week_end, local_tz):
                    out.append({
                        'day': slot['day'],
                        'start': slot['start'],
                        'end': slot['end'],
                        'person': user.id,
                        'title': slot['title'],
                    })

        # ---- iCloud (CalDAV) ----
        apple_tokens = models.UserOAuthToken.query.filter_by(
            user_id=user.id, provider='apple'
        ).all()
        if apple_tokens:
            apple_t_min, apple_t_max = icloud.week_bounds_utc(week_start, week_end)
            for oauth_tok in apple_tokens:
                if not oauth_tok.refresh_token:
                    continue
                cal_rows = models.UserLinkedCalendar.query.filter_by(
                    user_id=user.id, provider='apple',
                    oauth_token_id=oauth_tok.id, included_in_main_view=True,
                ).all()
                if not cal_rows:
                    continue
                try:
                    client = icloud.make_client(oauth_tok.email, oauth_tok.refresh_token)
                except Exception:
                    app.logger.warning(
                        'iCloud client init failed user=%s', user.id, exc_info=True
                    )
                    continue
                for row in cal_rows:
                    try:
                        events = icloud.events_list_for_calendar(
                            client, row.external_id, apple_t_min, apple_t_max
                        )
                    except Exception:
                        app.logger.warning(
                            'iCloud events fetch failed user=%s calendar=%s',
                            user.id, row.external_id, exc_info=True,
                        )
                        continue
                    for slot in icloud.icloud_events_to_week_slots(
                        events, week_start, week_end, local_tz
                    ):
                        out.append({
                            'day': slot['day'],
                            'start': slot['start'],
                            'end': slot['end'],
                            'person': user.id,
                            'title': slot['title'],
                        })
    return out



def sync_google_calendar_list_rows(user, oauth_token):
    """Upsert UserLinkedCalendar from Google calendarList for a given UserOAuthToken."""
    cid = app.config.get('GOOGLE_CLIENT_ID')
    csec = app.config.get('GOOGLE_CLIENT_SECRET')
    if not cid or not csec or not oauth_token.refresh_token:
        return False, 'Google Calendar is not connected. Use "Grant calendar access" below.'
    access = gcal.refresh_google_access_token(cid, csec, oauth_token.refresh_token)
    items = gcal.fetch_calendar_list(access)
    for item in items:
        eid = item.get('id')
        if not eid:
            continue
        summary = (item.get('summary') or item.get('summaryOverride') or eid)[:512]
        bg = item.get('backgroundColor')
        selected = item.get('selected', True)
        row = models.UserLinkedCalendar.query.filter_by(
            user_id=user.id, provider='google', external_id=eid
        ).first()
        if row:
            row.summary = summary
            row.background_color = (bg or '')[:32] if bg else None
            row.oauth_token_id = oauth_token.id
        else:
            db.session.add(
                models.UserLinkedCalendar(
                    user_id=user.id,
                    oauth_token_id=oauth_token.id,
                    provider='google',
                    external_id=eid,
                    summary=summary,
                    background_color=(bg or '')[:32] if bg else None,
                    included_in_main_view=bool(selected),
                )
            )
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return False, 'Could not save calendar list (try sync again).'
    return True, None


def sync_apple_calendar_list_rows(user, oauth_token):
    """Upsert UserLinkedCalendar from iCloud CalDAV for a given UserOAuthToken."""
    if not oauth_token.refresh_token:
        return False, 'iCloud is not connected. Reconnect this account.'
    try:
        client = icloud.make_client(oauth_token.email, oauth_token.refresh_token)
        items = icloud.fetch_calendar_list(client)
    except Exception as e:
        app.logger.exception('iCloud calendar list fetch failed for user %s', user.id)
        return False, f'Could not load calendars from iCloud: {e}'

    for item in items:
        eid = item.get('external_id')
        if not eid:
            continue
        summary = (item.get('summary') or eid)[:512]
        row = models.UserLinkedCalendar.query.filter_by(
            user_id=user.id, provider='apple', external_id=eid
        ).first()
        if row:
            row.summary = summary
            row.oauth_token_id = oauth_token.id
        else:
            db.session.add(
                models.UserLinkedCalendar(
                    user_id=user.id,
                    oauth_token_id=oauth_token.id,
                    provider='apple',
                    external_id=eid,
                    summary=summary,
                    background_color=None,
                    included_in_main_view=True,
                )
            )
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return False, 'Could not save calendar list (try sync again).'
    return True, None


@login_manager.user_loader
def load_user(user_id):
    return models.User.query.get(int(user_id))


_ONBOARDING_ALLOWED_ENDPOINTS = frozenset({
    'google_login', 'google_callback', 'add_google_account', 'complete_profile', 'logout', 'static',
})


@app.before_request
def _redirect_incomplete_profile():
    if request.endpoint == 'static':
        return
    if not current_user.is_authenticated:
        return
    if current_user.username:
        return
    if request.endpoint not in _ONBOARDING_ALLOWED_ENDPOINTS:
        return redirect(url_for('complete_profile'))


# ----- Routes -----
@app.route('/', methods=['GET'])
def index():
    if not current_user.is_authenticated:
        return redirect("/login")

    # Get related users (friends + group members)
    all_related_user_ids, friends_ids, groups_data = get_related_user_ids(current_user.id)

    # Fetch user info for everyone involved
    related_users = models.User.query.filter(models.User.id.in_(all_related_user_ids)).all()
    people_map = [
        {
            "id": u.id,
            "name": f"{u.first_name} {u.last_name}",
            "color": get_user_color(u.id),
            "profile_image": u.profile_image
        }
        for u in related_users
    ]

    # Calculate current week (Mon-Sun)
    today = datetime.now().date()
    start_of_week = today - timedelta(days=today.weekday())
    end_of_week = start_of_week + timedelta(days=7)

    events_data = build_week_events_for_users(all_related_user_ids, start_of_week, end_of_week)

    return render_template(
        'calendar.html',
        people=people_map,
        events=events_data,
        friends_ids=friends_ids,
        groups=groups_data,
        current_user_id=current_user.id,
        active_page='calendar'
    )

@app.route('/api', methods=['GET'])
def api():
    section = request.args.get('section')
    result = None
    if section:
        result = fetch_class_by_section(section)
    return render_template('api.html', result=result, query=section, active_page='api')

@app.route('/api/events', methods=['GET'])
@login_required
def get_events():
    """API endpoint to fetch events for a specific week"""
    week_start_str = request.args.get('week_start')
    if not week_start_str:
        return jsonify({"error": "week_start parameter required"}), 400
    
    try:
        week_start = datetime.strptime(week_start_str, '%Y-%m-%d').date()
    except ValueError:
        return jsonify({"error": "Invalid date format. Use YYYY-MM-DD"}), 400
    
    week_end = week_start + timedelta(days=7)
    
    # Get all related user IDs using helper function
    all_related_user_ids, _, _ = get_related_user_ids(current_user.id)
    
    events_data = build_week_events_for_users(all_related_user_ids, week_start, week_end)
    
    return jsonify({"events": events_data})


@app.route('/calendar-settings', methods=['GET', 'POST'])
@login_required
def calendar_settings():
    _ensure_calendar_schema()
    if not _google_oauth_configured():
        flash('Google OAuth is not configured.', 'danger')
        return redirect(url_for('index'))

    if request.method == 'POST':
        action = request.form.get('action') or ''
        account_id = request.form.get('account_id') or ''

        if action == 'sync_google':
            oauth_tok = None
            if account_id:
                oauth_tok = models.UserOAuthToken.query.filter_by(
                    id=account_id, user_id=current_user.id, provider='google'
                ).first()
            if not oauth_tok:
                # Fall back to the login account token
                oauth_tok = models.UserOAuthToken.query.filter_by(
                    user_id=current_user.id, provider='google', is_login_account=True
                ).first()
            if not oauth_tok and current_user.google_refresh_token:
                # Last-resort legacy path
                oauth_tok = models.UserOAuthToken(
                    user_id=current_user.id,
                    provider='google',
                    provider_account_id=current_user.google_sub,
                    email=current_user.email,
                    refresh_token=current_user.google_refresh_token,
                    is_login_account=True,
                )
            if oauth_tok:
                ok, err = sync_google_calendar_list_rows(current_user, oauth_tok)
                if ok:
                    flash('Calendars synced from Google.', 'success')
                else:
                    flash(err or 'Could not sync calendars.', 'danger')
            else:
                flash('Google Calendar is not connected.', 'danger')

        elif action == 'toggle_calendar':
            eid = request.form.get('external_id') or ''
            on = request.form.get('included') == '1'
            row = models.UserLinkedCalendar.query.filter_by(
                user_id=current_user.id, provider='google', external_id=eid
            ).first()
            if row:
                row.included_in_main_view = on
                db.session.commit()

        elif action == 'add_google_calendar':
            raw = (request.form.get('calendar_id') or '').strip()
            if not raw:
                flash('Enter a calendar ID.', 'danger')
            else:
                # Resolve which token to use for validating the calendar
                oauth_tok = None
                if account_id:
                    oauth_tok = models.UserOAuthToken.query.filter_by(
                        id=account_id, user_id=current_user.id, provider='google'
                    ).first()
                if not oauth_tok:
                    oauth_tok = models.UserOAuthToken.query.filter_by(
                        user_id=current_user.id, provider='google', is_login_account=True
                    ).first()
                refresh = (
                    oauth_tok.refresh_token if oauth_tok else current_user.google_refresh_token
                )
                if not refresh:
                    flash('Connect Google first.', 'danger')
                else:
                    cid_cfg = app.config.get('GOOGLE_CLIENT_ID')
                    csec = app.config.get('GOOGLE_CLIENT_SECRET')
                    try:
                        access = gcal.refresh_google_access_token(cid_cfg, csec, refresh)
                        meta = gcal.fetch_calendar_metadata(access, raw)
                        if not meta:
                            flash('Calendar not found or this account does not have access.', 'danger')
                        else:
                            eid = meta.get('id') or raw
                            summary = (meta.get('summary') or eid)[:512]
                            existing = models.UserLinkedCalendar.query.filter_by(
                                user_id=current_user.id, provider='google', external_id=eid
                            ).first()
                            if existing:
                                flash('That calendar is already in your list.', 'info')
                            else:
                                db.session.add(
                                    models.UserLinkedCalendar(
                                        user_id=current_user.id,
                                        oauth_token_id=oauth_tok.id if oauth_tok else None,
                                        provider='google',
                                        external_id=eid,
                                        summary=summary,
                                        background_color=None,
                                        included_in_main_view=True,
                                    )
                                )
                                db.session.commit()
                                flash('Calendar added.', 'success')
                    except Exception:
                        app.logger.exception('add_google_calendar failed')
                        flash('Could not add calendar. Check the ID and try again.', 'danger')

        elif action == 'add_apple_account':
            apple_id = (request.form.get('apple_id') or '').strip().lower()
            app_password = (request.form.get('app_password') or '').strip()
            if not apple_id or not app_password:
                flash('Enter both your Apple ID and an app-specific password.', 'danger')
            else:
                ok, err = icloud.validate_credentials(apple_id, app_password)
                if not ok:
                    flash(err or 'Could not connect to iCloud.', 'danger')
                else:
                    existing = models.UserOAuthToken.query.filter_by(
                        user_id=current_user.id, provider='apple',
                        provider_account_id=apple_id,
                    ).first()
                    if existing:
                        existing.refresh_token = app_password
                        existing.email = apple_id
                        tok = existing
                    else:
                        tok = models.UserOAuthToken(
                            user_id=current_user.id,
                            provider='apple',
                            provider_account_id=apple_id,
                            email=apple_id,
                            refresh_token=app_password,
                            is_login_account=False,
                        )
                        db.session.add(tok)
                    try:
                        db.session.commit()
                    except IntegrityError:
                        db.session.rollback()
                        flash('Could not save iCloud credentials.', 'danger')
                    else:
                        sync_ok, sync_err = sync_apple_calendar_list_rows(current_user, tok)
                        if sync_ok:
                            flash(f'iCloud account {apple_id} connected.', 'success')
                        else:
                            flash(
                                f'iCloud connected, but calendar sync failed: {sync_err}',
                                'warning',
                            )

        elif action == 'sync_apple':
            oauth_tok = None
            if account_id:
                oauth_tok = models.UserOAuthToken.query.filter_by(
                    id=account_id, user_id=current_user.id, provider='apple'
                ).first()
            if not oauth_tok:
                flash('iCloud account not found.', 'danger')
            else:
                ok, err = sync_apple_calendar_list_rows(current_user, oauth_tok)
                if ok:
                    flash('Calendars synced from iCloud.', 'success')
                else:
                    flash(err or 'Could not sync iCloud calendars.', 'danger')

        elif action == 'disconnect_account':
            if account_id:
                tok = models.UserOAuthToken.query.filter_by(
                    id=account_id, user_id=current_user.id,
                ).first()
                if tok:
                    if tok.is_login_account:
                        flash('Cannot disconnect the account used to log in.', 'danger')
                    else:
                        db.session.delete(tok)
                        db.session.commit()
                        flash('Account disconnected.', 'success')

        return redirect(url_for('calendar_settings'))

    # ---- GET ----
    google_tokens = (
        models.UserOAuthToken.query.filter_by(user_id=current_user.id, provider='google')
        .order_by(models.UserOAuthToken.is_login_account.desc(), models.UserOAuthToken.email)
        .all()
    )

    google_accounts = []
    for tok in google_tokens:
        cals = (
            models.UserLinkedCalendar.query
            .filter_by(user_id=current_user.id, provider='google', oauth_token_id=tok.id)
            .order_by(models.UserLinkedCalendar.summary)
            .all()
        )
        google_accounts.append({
            'id': tok.id,
            'connected': bool(tok.refresh_token),
            'email': tok.email,
            'is_login_account': tok.is_login_account,
            'calendars': cals,
        })

    # Show an unconnected placeholder if no tokens exist yet
    if not google_accounts:
        google_accounts = [{'id': None, 'connected': False, 'email': None,
                            'is_login_account': True, 'calendars': []}]

    apple_tokens = (
        models.UserOAuthToken.query.filter_by(user_id=current_user.id, provider='apple')
        .order_by(models.UserOAuthToken.email)
        .all()
    )
    apple_accounts = []
    for tok in apple_tokens:
        cals = (
            models.UserLinkedCalendar.query
            .filter_by(user_id=current_user.id, provider='apple', oauth_token_id=tok.id)
            .order_by(models.UserLinkedCalendar.summary)
            .all()
        )
        apple_accounts.append({
            'id': tok.id,
            'connected': bool(tok.refresh_token),
            'email': tok.email,
            'is_login_account': False,
            'calendars': cals,
        })

    reconnect_url = url_for(
        'google_login',
        next=url_for('calendar_settings'),
        reconsent='1',
    )
    add_account_url = url_for('add_google_account')
    return render_template(
        'calendar_settings.html',
        google_accounts=google_accounts,
        apple_accounts=apple_accounts,
        reconnect_url=reconnect_url,
        add_account_url=add_account_url,
        active_page='calendar_settings',
    )



@app.route('/classes', methods=['GET', 'POST'])
@login_required
def classes():
    if request.method == "POST":
        '''
        Takes the form, if the section (and class) are not already stored, makes them. Then adds user
        '''
        asu_section_id = int(request.form.get("asu_section_id", "").strip())

        if not asu_section_id:
            flash("Section ID is required.", "danger")
            return redirect("/classes")

        section = models.CourseSection.query.filter_by(
            asu_section_id=asu_section_id
        ).first()

        if not section:
            # Fetch section data from API
            class_raw = fetch_class_by_section(asu_section_id)
            if not class_raw:
                flash("Section does not exist.", "danger")
                return redirect("/classes")
            section_data = parse_class_item(class_raw)

            # Query course; create if it doesn't exist
            course = models.Course.query.filter_by(
                asu_course_id=section_data["asu_course_id"],
                title=section_data["title"]
            ).first()

            if not course:
                course = models.Course(asu_course_id=section_data["asu_course_id"], title=section_data["title"])

            # Add section
            SECTION_FIELDS = {"asu_section_id", "term", "location", "days_of_week", "start_time", "end_time", "start_date", "end_date"} # <-- Arguments to pass into the section
            section_kwargs = {
                field: section_data[field]
                for field in SECTION_FIELDS
                if field in section_data
            }
            # Ensure numeric section id is an int
            section_kwargs["asu_section_id"] = int(section_kwargs["asu_section_id"])
            section = models.CourseSection(**section_kwargs)
            course.sections.append(section)
            db.session.add(course)

        # Associate user with section
        user_section = models.UserCourse(
            user_id=current_user.id,
            section=section
        )

        try:
            db.session.add(user_section)
            user_section.create_events(db.session)
            db.session.commit()
            flash("Section added successfully.", "success")
        except IntegrityError:
            db.session.rollback()
            flash("You have already added this section.", "info")
        except Exception as e:
            db.session.rollback()
            flash("An error occurred while adding the section.", "danger")

        return redirect("/classes")
    # GET
    user_sections = (
        models.UserCourse.query
        .filter_by(user_id=current_user.id)
        .join(models.CourseSection)
        .all()
    )
    sections = [uc.section for uc in user_sections]
    return render_template(
        "classes.html",
        sections=sections,
        active_page='classes'
    )


@app.route('/classes/remove/<int:section_id>', methods=['POST'])
@login_required
def remove_class(section_id):
    user_course = models.UserCourse.query.filter_by(
        user_id=current_user.id,
        section_id=section_id
    ).first_or_404()
    
    try:
        db.session.delete(user_course)
        db.session.commit()
        flash("Class removed.", "info")
    except Exception as e:
        db.session.rollback()
        flash("An error occurred while removing the class.", "danger")
    
    return redirect(url_for("classes"))


# ----- Routes: SOCIAL -----
@app.route('/friends', methods=['GET', 'POST'])
@login_required
def friends():
    if request.method == 'POST':
        # Add Friend Logic: by @handle, or by user id (e.g. from group member list)
        friend_user_id = request.form.get("friend_user_id", type=int)
        handle = (request.form.get("handle") or request.form.get("username") or "").strip().lstrip("@")

        if not friend_user_id and not handle:
            flash("Please enter a handle.", "danger")
            return redirect(url_for("friends"))

        if friend_user_id:
            target_user = models.User.query.get(friend_user_id)
        else:
            target_user = models.User.query.filter_by(username=handle).first()

        if not target_user:
            flash("User not found.", "danger")
            return redirect(url_for("friends"))

        if not target_user.username:
            flash("That account has not finished setup yet.", "danger")
            return redirect(url_for("friends"))

        if target_user.id == current_user.id:
            flash("You cannot add yourself.", "danger")
            return redirect(url_for("friends"))

        # Check existing friendship
        existing = models.Friendship.query.filter(
            or_(
                (models.Friendship.requester_id == current_user.id) & (models.Friendship.receiver_id == target_user.id),
                (models.Friendship.requester_id == target_user.id) & (models.Friendship.receiver_id == current_user.id)
            )
        ).first()

        if existing:
            if existing.status == 'accepted':
                flash("You are already friends!", "info")
            elif existing.requester_id == current_user.id:
                flash("Request already sent.", "info")
            else:
                flash("They already sent you a request. Check your pending requests!", "info")
            return redirect(url_for("friends"))

        # Create request
        req = models.Friendship(requester_id=current_user.id, receiver_id=target_user.id)
        try:
            db.session.add(req)
            db.session.commit()
            flash(f"Friend request sent to @{target_user.username}!", "success")
        except Exception as e:
            db.session.rollback()
            flash("An error occurred while sending the friend request.", "danger")
        
        return redirect(url_for("friends"))

    # GET: List friends and requests
    # 1. Incoming Requests
    incoming_requests = models.Friendship.query.filter_by(
        receiver_id=current_user.id, 
        status='pending'
    ).all()

    # 2. Friends (Accepted, either direction)
    accepted_links = models.Friendship.query.filter(
        (models.Friendship.status == 'accepted') &
        or_(
            models.Friendship.requester_id == current_user.id,
            models.Friendship.receiver_id == current_user.id
        )
    ).all()
    
    friends_list = []
    for link in accepted_links:
        friend_user = link.receiver if link.requester_id == current_user.id else link.requester
        # Calculate days as friends
        days_as_friends = (datetime.utcnow() - link.created_at).days
        friends_list.append({
            'user': friend_user,
            'friendship': link,
            'days': days_as_friends
        })

    return render_template("friends.html", requests=incoming_requests, friends=friends_list, active_page='friends')


@app.route('/friends/remove/<int:friend_id>', methods=['POST'])
@login_required
def remove_friend(friend_id):
    # Find existing friendship
    friendship = models.Friendship.query.filter(
        or_(
            (models.Friendship.requester_id == current_user.id) & (models.Friendship.receiver_id == friend_id),
            (models.Friendship.requester_id == friend_id) & (models.Friendship.receiver_id == current_user.id)
        )
    ).first_or_404()
    
    try:
        db.session.delete(friendship)
        db.session.commit()
        flash("Friend removed.", "info")
    except Exception as e:
        db.session.rollback()
        flash("An error occurred while removing the friend.", "danger")
    
    return redirect(url_for("friends"))


@app.route('/friends/respond/<int:request_id>/<action>')
@login_required
def friend_respond(request_id, action):
    req = models.Friendship.query.get_or_404(request_id)
    
    if req.receiver_id != current_user.id:
        flash("Unauthorized action.", "danger")
        return redirect(url_for("friends"))

    try:
        if action == 'accept':
            req.status = 'accepted'
            db.session.commit()
            flash(f"You are now friends with @{req.requester.username}!", "success")
        elif action == 'decline':
            db.session.delete(req)
            db.session.commit()
            flash("Friend request declined.", "info")
    except Exception as e:
        db.session.rollback()
        flash("An error occurred while processing the request.", "danger")
    
    return redirect(url_for("friends"))


@app.route('/groups', methods=['GET', 'POST'])
@login_required
def groups():
    if request.method == 'POST':
        # Create Group
        name = request.form.get("name")
        description = request.form.get("description")
        
        if not name:
            flash("Group name is required.", "danger")
            return redirect(url_for("groups"))
        
        try:
            new_group = models.Group(name=name, description=description, created_by=current_user.id)
            db.session.add(new_group)
            db.session.flush()  # Get ID without committing
            
            # Add creator as admin with accepted status
            membership = models.GroupMember(group_id=new_group.id, user_id=current_user.id, role='admin', status='accepted')
            db.session.add(membership)
            db.session.commit()
            flash("Group created!", "success")
        except Exception as e:
            db.session.rollback()
            flash("An error occurred while creating the group.", "danger")
        
        return redirect(url_for("groups"))

    # GET: List my groups (accepted only) and pending invites
    memberships = models.GroupMember.query.filter_by(user_id=current_user.id, status='accepted').order_by(models.GroupMember.display_order).all()
    my_groups = [m.group for m in memberships]
    
    # Get pending invites
    pending_invites = models.GroupMember.query.filter_by(user_id=current_user.id, status='pending').all()
    
    return render_template("groups.html", groups=my_groups, pending_invites=pending_invites, active_page='groups')


@app.route('/groups/reorder', methods=['POST'])
@login_required
def reorder_groups():
    """API endpoint to save new group order"""
    try:
        data = request.get_json()
        group_ids = data.get('group_ids', [])
        
        if not group_ids:
            return jsonify({'success': False, 'error': 'No group IDs provided'}), 400
        
        # Update display_order for each group membership
        for index, group_id in enumerate(group_ids):
            membership = models.GroupMember.query.filter_by(
                user_id=current_user.id, 
                group_id=group_id
            ).first()
            
            if membership:
                membership.display_order = index
        
        db.session.commit()
        return jsonify({'success': True})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/groups/leave/<int:group_id>', methods=['POST'])
@login_required
def leave_group(group_id):
    membership = models.GroupMember.query.filter_by(
        group_id=group_id, 
        user_id=current_user.id,
        status='accepted'
    ).first_or_404()
    
    group = membership.group
    
    # Check if admin constraint - only consider other accepted members
    if membership.role == 'admin':
        # Check if there are other accepted members
        other_members_count = models.GroupMember.query.filter(
            models.GroupMember.group_id == group_id,
            models.GroupMember.user_id != current_user.id,
            models.GroupMember.status == 'accepted'
        ).count()

        if other_members_count > 0:
            # Check if there is another admin
            other_admin = models.GroupMember.query.filter(
                models.GroupMember.group_id == group_id,
                models.GroupMember.user_id != current_user.id,
                models.GroupMember.role == 'admin',
                models.GroupMember.status == 'accepted'
            ).first()
            
            if not other_admin:
                flash("You cannot leave the group as the only admin while other members exist. Promote someone else first.", "danger")
                return redirect(url_for("view_group", group_id=group_id))

    try:
        db.session.delete(membership)
        db.session.commit()
        
        # Clean up empty group (check both accepted members and pending invites)
        remaining_count = models.GroupMember.query.filter_by(group_id=group_id).count()
        if remaining_count == 0:
            db.session.delete(group)
            db.session.commit()
        
        flash("You have left the group.", "info")
    except Exception as e:
        db.session.rollback()
        flash("An error occurred while leaving the group.", "danger")
    
    return redirect(url_for("groups"))


@app.route('/groups/invite/<int:group_id>', methods=['POST'])
@login_required
def invite_member(group_id):
    group = models.Group.query.get_or_404(group_id)
    # Check if current user is an accepted member
    me_member = models.GroupMember.query.filter_by(group_id=group.id, user_id=current_user.id, status='accepted').first()
    if not me_member:
        flash("Unauthorized.", "danger")
        return redirect(url_for("groups"))

    handle = (request.form.get("handle") or request.form.get("username") or "").strip().lstrip("@")
    if not handle:
        flash("Please enter a handle.", "danger")
        return redirect(url_for("view_group", group_id=group_id))

    user_to_add = models.User.query.filter_by(username=handle).first()

    if not user_to_add:
        flash("User not found.", "danger")
        return redirect(url_for("view_group", group_id=group_id))
    
    # Check if already member or has pending invite
    exists = models.GroupMember.query.filter_by(group_id=group.id, user_id=user_to_add.id).first()
    if exists:
        if exists.status == 'pending':
            flash("User already has a pending invite.", "info")
        else:
            flash("User is already in the group.", "info")
        return redirect(url_for("view_group", group_id=group_id))

    try:
        new_member = models.GroupMember(group_id=group.id, user_id=user_to_add.id, role='member', status='pending')
        db.session.add(new_member)
        db.session.commit()
        flash(f"Invite sent to @{handle}!", "success")
    except Exception as e:
        db.session.rollback()
        flash("An error occurred while sending the invite.", "danger")
    
    return redirect(url_for("view_group", group_id=group_id))


@app.route('/groups/invite/<int:group_id>/accept', methods=['POST'])
@login_required
def accept_invite(group_id):
    membership = models.GroupMember.query.filter_by(
        group_id=group_id,
        user_id=current_user.id,
        status='pending'
    ).first_or_404()
    
    try:
        membership.status = 'accepted'
        db.session.commit()
        flash("You have joined the group!", "success")
        return redirect(url_for("view_group", group_id=group_id))
    except Exception as e:
        db.session.rollback()
        flash("An error occurred while accepting the invite.", "danger")
        return redirect(url_for("groups"))


@app.route('/groups/invite/<int:group_id>/decline', methods=['POST'])
@login_required
def decline_invite(group_id):
    membership = models.GroupMember.query.filter_by(
        group_id=group_id,
        user_id=current_user.id,
        status='pending'
    ).first_or_404()
    
    try:
        db.session.delete(membership)
        db.session.commit()
        flash("Invite declined.", "info")
    except Exception as e:
        db.session.rollback()
        flash("An error occurred while declining the invite.", "danger")
    
    return redirect(url_for("groups"))


@app.route('/groups/kick/<int:group_id>/<int:user_id>', methods=['POST'])
@login_required
def kick_member(group_id, user_id):
    # Check permissions
    me_member = models.GroupMember.query.filter_by(group_id=group_id, user_id=current_user.id, status='accepted').first()
    if not me_member or me_member.role != 'admin':
        flash("Only admins can kick members or revoke invites.", "danger")
        return redirect(url_for("view_group", group_id=group_id))
    
    target_member = models.GroupMember.query.filter_by(group_id=group_id, user_id=user_id).first_or_404()
    
    # Don't allow kicking other admins (but can revoke pending admin invites)
    if target_member.role == 'admin' and target_member.status == 'accepted':
         flash("Cannot kick another admin.", "danger")
         return redirect(url_for("view_group", group_id=group_id))

    try:
        db.session.delete(target_member)
        db.session.commit()
        if target_member.status == 'pending':
            flash("Invite revoked.", "success")
        else:
            flash("Member removed.", "success")
    except Exception as e:
        db.session.rollback()
        flash("An error occurred while removing the member.", "danger")
    
    return redirect(url_for("view_group", group_id=group_id))

@app.route('/groups/promote/<int:group_id>/<int:user_id>', methods=['POST'])
@login_required
def promote_member(group_id, user_id):
    # Check permissions
    me_member = models.GroupMember.query.filter_by(group_id=group_id, user_id=current_user.id, status='accepted').first()
    if not me_member or me_member.role != 'admin':
        flash("Only admins can promote members.", "danger")
        return redirect(url_for("view_group", group_id=group_id))
    
    target_member = models.GroupMember.query.filter_by(group_id=group_id, user_id=user_id, status='accepted').first_or_404()
    
    try:
        target_member.role = 'admin'
        db.session.commit()
        label = target_member.user.username or target_member.user.email.split("@")[0]
        flash(f"{label} promoted to Admin!", "success")
    except Exception as e:
        db.session.rollback()
        flash("An error occurred while promoting the member.", "danger")
    
    return redirect(url_for("view_group", group_id=group_id))


@app.route('/groups/<int:group_id>/update_description', methods=['POST'])
@login_required
def update_group_description(group_id):
    # Check if user is an accepted admin
    me_member = models.GroupMember.query.filter_by(group_id=group_id, user_id=current_user.id, status='accepted').first()
    if not me_member or me_member.role != 'admin':
        flash("Only admins can update the group description.", "danger")
        return redirect(url_for("view_group", group_id=group_id))
    
    group = models.Group.query.get_or_404(group_id)
    description = request.form.get('description', '').strip()
    
    try:
        group.description = description
        db.session.commit()
        flash("Group description updated successfully!", "success")
    except Exception as e:
        db.session.rollback()
        flash("An error occurred while updating the description.", "danger")
    
    return redirect(url_for("view_group", group_id=group_id))


@app.route('/groups/<int:group_id>/update_photo', methods=['POST'])
@login_required
def update_group_photo(group_id):
    # Check if user is an accepted admin
    me_member = models.GroupMember.query.filter_by(group_id=group_id, user_id=current_user.id, status='accepted').first()
    if not me_member or me_member.role != 'admin':
        flash("Only admins can update the group photo.", "danger")
        return redirect(url_for("view_group", group_id=group_id))
    
    group = models.Group.query.get_or_404(group_id)
    
    if 'group_photo' not in request.files:
        flash("No file selected.", "danger")
        return redirect(url_for("view_group", group_id=group_id))
    
    file = request.files['group_photo']
    if not file.filename:
        flash("No file selected.", "danger")
        return redirect(url_for("view_group", group_id=group_id))
    
    # Validate file extension
    file_ext = file.filename.rsplit('.', 1)[1].lower() if '.' in file.filename else ''
    if file_ext not in ALLOWED_EXTENSIONS:
        flash(f"Invalid file type. Allowed types: {', '.join(ALLOWED_EXTENSIONS)}", "danger")
        return redirect(url_for("view_group", group_id=group_id))
    
    filename = secure_filename(file.filename)
    unique_filename = f"g{group.id}_{filename}"
    
    # Ensure directory exists
    if not os.path.exists(app.config['UPLOAD_FOLDER']):
        os.makedirs(app.config['UPLOAD_FOLDER'])
    
    try:
        file_path = os.path.join(app.config['UPLOAD_FOLDER'], unique_filename)
        file.save(file_path)
        group.profile_image = unique_filename
        db.session.commit()
        flash("Group photo updated!", "success")
    except Exception as e:
        db.session.rollback()
        flash("An error occurred while uploading the group photo.", "danger")
    
    return redirect(url_for("view_group", group_id=group_id))


@app.route('/groups/<int:group_id>')
@login_required
def view_group(group_id):
    group = models.Group.query.get(group_id)
    # Check if group exists and user is a member (use same message for both to prevent enumeration)
    if not group:
        flash("Group not found.", "danger")
        return redirect(url_for("groups"))
    
    member = models.GroupMember.query.filter_by(group_id=group.id, user_id=current_user.id).first()
    if not member:
        flash("Group not found.", "danger")
        return redirect(url_for("groups"))
    
    # If invite is still pending, redirect to groups page to accept/decline
    if member.status == 'pending':
        flash("You have a pending invite to this group. Please accept or decline.", "info")
        return redirect(url_for("groups"))
    
    # Context Logic
    is_admin = (member.role == 'admin')
    
    # Get my friends to check status
    friends_query = models.Friendship.query.filter(
        (models.Friendship.status == 'accepted') &
        or_(
            models.Friendship.requester_id == current_user.id,
            models.Friendship.receiver_id == current_user.id
        )
    ).all()
    friend_ids = set()
    for f in friends_query:
        if f.requester_id == current_user.id:
            friend_ids.add(f.receiver_id)
        else:
            friend_ids.add(f.requester_id)

    # Check pending sent requests to avoid showing "Add Friend" if already sent
    pending_ids = set()
    pending_query = models.Friendship.query.filter(
        (models.Friendship.requester_id == current_user.id) & 
        (models.Friendship.status == 'pending')
    ).all()
    for f in pending_query:
        pending_ids.add(f.receiver_id)

    # Sort members by seniority (earliest joined first) - only accepted members
    accepted_members = [m for m in group.members if m.status == 'accepted']
    sorted_members = sorted(accepted_members, key=lambda m: m.joined_at)
    
    # Get pending invites for admin view
    pending_invites = [m for m in group.members if m.status == 'pending']
    
    return render_template(
        "group_detail.html", 
        group=group, 
        membership=member,
        is_admin=is_admin,
        friend_ids=friend_ids,
        pending_ids=pending_ids,
        sorted_members=sorted_members,
        pending_invites=pending_invites,
        active_page='groups'
    )


# ----- Routes: AUTH -----
@app.route('/login', methods=['GET'])
def login():
    if current_user.is_authenticated:
        if current_user.username:
            return redirect(url_for('index'))
        return redirect(url_for('complete_profile'))
    return render_template(
        'login.html',
        google_configured=_google_oauth_configured(),
    )


@app.route('/auth/google')
def google_login():
    if not _google_oauth_configured():
        flash("Google sign-in is not configured. Set GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET.", "danger")
        return redirect(url_for('login'))
    google = oauth.create_client('google')
    redirect_uri = os.environ.get('GOOGLE_OAUTH_REDIRECT_URI') or url_for('google_callback', _external=True)
    session_nonce = secrets.token_urlsafe(16)
    session['oidc_nonce'] = session_nonce
    next_raw = request.args.get('next') or '/'
    if not (isinstance(next_raw, str) and next_raw.startswith('/') and not next_raw.startswith('//')):
        next_raw = '/'
    session['oauth_next'] = next_raw
    oauth_kwargs = {
        'redirect_uri': redirect_uri,
        'nonce': session_nonce,
        'code_challenge_method': 'S256',
        'access_type': 'offline',
    }
    if request.args.get('reconsent') == '1':
        oauth_kwargs['prompt'] = 'consent'
    elif current_user.is_authenticated and not getattr(current_user, 'google_refresh_token', None):
        oauth_kwargs['prompt'] = 'consent'
    return google.authorize_redirect(**oauth_kwargs)


@app.route('/auth/google/add-account')
@login_required
def add_google_account():
    """Start an OAuth flow to link an additional Google account (calendar access only)."""
    if not _google_oauth_configured():
        flash("Google OAuth is not configured.", "danger")
        return redirect(url_for('calendar_settings'))
    google = oauth.create_client('google')
    redirect_uri = os.environ.get('GOOGLE_OAUTH_REDIRECT_URI') or url_for('google_callback', _external=True)
    session_nonce = secrets.token_urlsafe(16)
    session['oidc_nonce'] = session_nonce
    session['oauth_mode'] = 'add_account'
    return google.authorize_redirect(
        redirect_uri=redirect_uri,
        nonce=session_nonce,
        code_challenge_method='S256',
        access_type='offline',
        prompt='select_account consent',
    )


@app.route('/auth/google/callback')
def google_callback():
    if not _google_oauth_configured():
        flash("Google sign-in is not configured.", "danger")
        return redirect(url_for('login'))
    google = oauth.create_client('google')
    try:
        token = google.authorize_access_token()
    except Exception:
        app.logger.exception("Google OAuth token exchange failed")
        flash("Sign in with Google was cancelled or failed. Try again.", "danger")
        return redirect(url_for('login'))

    nonce = session.pop('oidc_nonce', None)
    oauth_mode = session.pop('oauth_mode', 'login')

    user_info = token.get('userinfo')
    if not user_info:
        try:
            user_info = google.parse_id_token(token, nonce=nonce)
        except Exception:
            app.logger.exception("Google id_token parse failed")
            flash("Could not verify Google sign-in.", "danger")
            return redirect(url_for('login'))

    google_sub = user_info.get('sub')
    email = (user_info.get('email') or '').strip().lower()
    if not google_sub or not email:
        flash("Google did not return enough account information.", "danger")
        return redirect(url_for('login'))

    if not user_info.get('email_verified', True):
        flash("Please verify your email with Google before signing in.", "danger")
        return redirect(url_for('login'))

    # ---- add-account flow: link a new calendar account to the logged-in user ----
    if oauth_mode == 'add_account':
        if not current_user.is_authenticated:
            flash("You must be logged in to add a calendar account.", "danger")
            return redirect(url_for('login'))
        refresh_tok = token.get('refresh_token')
        existing = models.UserOAuthToken.query.filter_by(
            user_id=current_user.id, provider='google', provider_account_id=google_sub
        ).first()
        if existing:
            if refresh_tok:
                existing.refresh_token = refresh_tok
            existing.email = email
        else:
            db.session.add(models.UserOAuthToken(
                user_id=current_user.id,
                provider='google',
                provider_account_id=google_sub,
                email=email,
                refresh_token=refresh_tok,
                is_login_account=False,
            ))
        try:
            db.session.commit()
            flash(f"Google account {email} connected.", "success")
        except IntegrityError:
            db.session.rollback()
            flash("Could not link that account. It may already be connected.", "danger")
        return redirect(url_for('calendar_settings'))

    # ---- normal login flow ----
    first_name = (user_info.get('given_name') or email.split('@')[0])[:25]
    last_name = (user_info.get('family_name') or '')[:50]

    user = models.User.query.filter_by(google_sub=google_sub).first()
    if user:
        user.email = email
        user.first_name = first_name or user.first_name
        user.last_name = last_name
    else:
        user = models.User(
            google_sub=google_sub,
            email=email,
            first_name=first_name or 'User',
            last_name=last_name,
            username=None,
        )
        db.session.add(user)

    refresh_tok = token.get('refresh_token')
    if refresh_tok:
        user.google_refresh_token = refresh_tok

    try:
        db.session.flush()
    except IntegrityError:
        db.session.rollback()
        flash("Account conflict. Try again or contact support.", "danger")
        return redirect(url_for('login'))

    # Keep UserOAuthToken in sync with the login account
    login_token = models.UserOAuthToken.query.filter_by(
        user_id=user.id, provider='google', provider_account_id=google_sub
    ).first()
    if login_token:
        if refresh_tok:
            login_token.refresh_token = refresh_tok
        login_token.email = email
        login_token.is_login_account = True
    elif refresh_tok:
        db.session.add(models.UserOAuthToken(
            user_id=user.id,
            provider='google',
            provider_account_id=google_sub,
            email=email,
            refresh_token=refresh_tok,
            is_login_account=True,
        ))

    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        flash("Account conflict. Try again or contact support.", "danger")
        return redirect(url_for('login'))

    login_user(user)

    next_path = session.pop('oauth_next', '/') or '/'
    if not current_user.username:
        return redirect(url_for('complete_profile'))
    if next_path.startswith('/'):
        return redirect(next_path)
    return redirect(url_for('index'))


@app.route('/complete-profile', methods=['GET', 'POST'])
@login_required
def complete_profile():
    if current_user.username:
        return redirect(url_for('index'))

    if request.method == 'POST':
        handle = (request.form.get('handle') or '').strip().lstrip('@')
        first_name = (request.form.get('first_name') or '').strip()[:25]
        last_name = (request.form.get('last_name') or '').strip()[:50]

        if not first_name:
            flash("First name is required.", "danger")
            return render_template('onboarding.html')
        if not re.match(r'^[a-zA-Z0-9_]{3,30}$', handle):
            flash("Handle must be 3–30 characters: letters, numbers, and underscores only.", "danger")
            return render_template('onboarding.html')

        if models.User.query.filter(models.User.username == handle, models.User.id != current_user.id).first():
            flash("That handle is already taken.", "danger")
            return render_template('onboarding.html')

        current_user.username = handle
        current_user.first_name = first_name
        current_user.last_name = last_name
        try:
            db.session.commit()
            flash("Profile saved.", "success")
            return redirect(url_for('index'))
        except IntegrityError:
            db.session.rollback()
            db.session.refresh(current_user)
            flash("That handle is already taken.", "danger")

    return render_template('onboarding.html')


@app.route('/register')
def register():
    return redirect(url_for('login'))


@app.route('/me', methods=['GET', 'POST'])
@login_required
def me():
    if request.method == 'POST':
        if 'profile_pic' in request.files:
            file = request.files['profile_pic']
            if file.filename:
                # Validate file extension
                file_ext = file.filename.rsplit('.', 1)[1].lower() if '.' in file.filename else ''
                if file_ext not in ALLOWED_EXTENSIONS:
                    flash(f"Invalid file type. Allowed types: {', '.join(ALLOWED_EXTENSIONS)}", "danger")
                    return redirect(url_for('me'))
                
                filename = secure_filename(file.filename)
                unique_filename = f"u{current_user.id}_{filename}"
                
                # Ensure directory exists
                if not os.path.exists(app.config['UPLOAD_FOLDER']):
                    os.makedirs(app.config['UPLOAD_FOLDER'])
                
                try:
                    file_path = os.path.join(app.config['UPLOAD_FOLDER'], unique_filename)
                    file.save(file_path)
                    current_user.profile_image = unique_filename
                    db.session.commit()
                    flash("Profile picture updated!", "success")
                except Exception as e:
                    db.session.rollback()
                    flash("An error occurred while uploading your profile picture.", "danger")
        
        return redirect(url_for('me'))

    return render_template('me.html', active_page='profile')

@app.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect("/")


# ----- Main -----
if __name__ == "__main__":
    with app.app_context(): # DB init for local dev
        db.create_all()
    app.run(host="0.0.0.0", port=5000, debug=True)