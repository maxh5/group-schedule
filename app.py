from flask import Flask, render_template, request, redirect, flash, url_for, jsonify
from flask_login import logout_user, login_required, login_user, current_user
import os
from werkzeug.utils import secure_filename
from dotenv import load_dotenv
from sqlalchemy.exc import IntegrityError
from sqlalchemy import or_
from datetime import datetime, timedelta


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
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL', 'sqlite:///dev.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['MAX_CONTENT_LENGTH'] = MAX_FILE_SIZE


# ----- Imports -----
"""Flask extensions"""
from extras.extensions import db, login_manager
db.init_app(app)
login_manager.init_app(app)
"""ASU API"""
from extras.api import fetch_class_by_section, parse_class_item
"""Models"""
import models


# ----- Helper Functions -----
def get_user_color(user_id):
    """Generate consistent color for a user based on their ID."""
    return USER_COLORS[user_id % len(USER_COLORS)]


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


@login_manager.user_loader
def load_user(user_id):
    return models.User.query.get(int(user_id))


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

    # Query events for the week
    relevant_events = models.Event.query.filter(
        models.Event.user_id.in_(all_related_user_ids),
        models.Event.start >= start_of_week,
        models.Event.start < end_of_week
    ).all()

    events_data = [
        {
            "day": ev.start.weekday(),
            "start": ev.start.strftime("%H:%M"),
            "end": ev.end.strftime("%H:%M"),
            "person": ev.user_id,
            "title": ev.title
        }
        for ev in relevant_events
    ]

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
    
    # Query events for the specified week
    relevant_events = models.Event.query.filter(
        models.Event.user_id.in_(all_related_user_ids),
        models.Event.start >= week_start,
        models.Event.start < week_end
    ).all()
    
    events_data = [
        {
            "day": ev.start.weekday(),
            "start": ev.start.strftime("%H:%M"),
            "end": ev.end.strftime("%H:%M"),
            "person": ev.user_id,
            "title": ev.title
        }
        for ev in relevant_events
    ]
    
    return jsonify({"events": events_data})

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
        # Add Friend Logic
        username = request.form.get("username", "").strip()
        if not username:
            flash("Please enter a username.", "danger")
            return redirect(url_for("friends"))

        target_user = models.User.query.filter_by(username=username).first()
        if not target_user:
            flash("User not found.", "danger")
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

    username = request.form.get("username", "").strip()
    if not username:
        flash("Please enter a username.", "danger")
        return redirect(url_for("view_group", group_id=group_id))

    user_to_add = models.User.query.filter_by(username=username).first()
    
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
        flash(f"Invite sent to @{username}!", "success")
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
        flash(f"{target_member.user.username} promoted to Admin!", "success")
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
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == "POST":
        username = request.form["username"]
        password = request.form["password"]
        
        user = models.User.query.filter_by(username=username).first()

        if not user or not user.check_password(password):
            flash("Invalid username or password.", "danger")
            return render_template("login.html")

        login_user(user)
        flash("Logged in successfully!", "success")
        return redirect("/")
    # --- GET ---
    return render_template('login.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == "POST":

        first_name = request.form["first_name"]
        last_name = request.form["last_name"]
        username = request.form["username"]
        password = request.form["password"]
        confirm = request.form["confirm_password"]

        if password != confirm:
            flash("Passwords do not match.", "danger")
            return render_template("register.html")

        # Check if username exists
        if models.User.query.filter_by(username=username).first():
            flash("Username already taken.", "danger")
            return render_template("register.html")

        user = models.User(
            first_name=first_name, 
            last_name=last_name,
            username=username
        )
        user.set_password(password)

        try:
            db.session.add(user)
            db.session.commit()
            flash("Account created successfully.", "success")
            login_user(user)
            return redirect("/")
        except Exception as e:
            db.session.rollback()
            flash("An error occurred while creating your account.", "danger")
            return render_template("register.html")
    
    return render_template('register.html')

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