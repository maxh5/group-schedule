from flask import Flask, render_template, request, redirect, flash, url_for
from flask_login import logout_user, login_required, login_user, current_user
import os
from werkzeug.utils import secure_filename
from dotenv import load_dotenv
from sqlalchemy.exc import IntegrityError
from sqlalchemy import or_


# ----- Load environment -----
load_dotenv()


# ----- App config -----
app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'dev-secret')
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL', 'sqlite:///dev.db') # Default to SQLite for local dev
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['UPLOAD_FOLDER'] = 'static/profile_pics'
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024 # 16 MB max upload


# ----- Imports -----
"""Flask extensions"""
from extras.extensions import db, login_manager
db.init_app(app)
login_manager.init_app(app)
"""ASU API"""
from extras.api import fetch_class_by_section, parse_class_item
"""Models"""
import models


# ----- Functions -----
@login_manager.user_loader
def load_user(user_id):
    return models.User.query.get(int(user_id))


# ----- Routes -----
@app.route('/', methods=['GET'])
def index():
    if not current_user.is_authenticated:
        return redirect("/login")

    # 1. Fetch Friends (ids)
    friends_query = models.Friendship.query.filter(
        (models.Friendship.status == 'accepted') &
        or_(
            models.Friendship.requester_id == current_user.id,
            models.Friendship.receiver_id == current_user.id
        )
    ).all()
    
    friend_users = []
    for f in friends_query:
        if f.requester_id == current_user.id:
            friend_users.append(f.receiver)
        else:
            friend_users.append(f.requester)
    
    friends_ids = [u.id for u in friend_users]

    # 2. Fetch Groups
    my_memberships = models.GroupMember.query.filter_by(user_id=current_user.id).all()
    groups_data = [] # [{id, name, member_ids}]
    all_related_user_ids = set(friends_ids)
    all_related_user_ids.add(current_user.id) # Include self

    for m in my_memberships:
        group = m.group
        member_ids = [gm.user_id for gm in group.members]
        groups_data.append({
            "id": group.id,
            "name": group.name,
            "members": member_ids
        })
        # Add group members to the pool of users we need to fetch info for
        for uid in member_ids:
            all_related_user_ids.add(uid)

    # 3. Fetch User Info for everyone involved
    # We need a map of id -> {name, color}
    # Simple consistent color hash based on id
    def get_color(uid):
        colors = ["#FF8A80", "#FFD180", "#FFFF8D", "#B9F6CA", "#80D8FF", "#B388FF", "#CFD8DC", "#FF80AB", "#EA80FC"]
        return colors[uid % len(colors)]

    related_users = models.User.query.filter(models.User.id.in_(all_related_user_ids)).all()
    people_map = []
    for u in related_users:
        people_map.append({
            "id": u.id,
            "name": u.first_name, # or full_name
            "color": get_color(u.id),
            "profile_image": u.profile_image # for UI
        })

    # 4. Fetch Events for current week
    #   Calculate Start/End of relative week
    #   For "Weekly Availability", we might want to look at a typical week, 
    #   but our DB stores specific dates. Let's just grab "Next 7 Days" or "Current Mon-Sun".
    #   Let's do "Current Mon-Sun" of the *server time*.
    from datetime import datetime, timedelta
    today = datetime.now().date()
    start_of_week = today - timedelta(days=today.weekday()) # Monday
    end_of_week = start_of_week + timedelta(days=7) # Next Monday

    # Query events
    relevant_events = models.Event.query.filter(
        models.Event.user_id.in_(all_related_user_ids),
        models.Event.start >= start_of_week,
        models.Event.start < end_of_week
    ).all()

    events_data = []
    for ev in relevant_events:
        # Convert to 0-6 day index and HH:MM
        # We assume database stores timezone-naive or UTC, aligning with server.
        # For a prototype, naive is fine.
        day_index = ev.start.weekday() # 0 = Mon
        start_str = ev.start.strftime("%H:%M")
        end_str = ev.end.strftime("%H:%M")
        
        events_data.append({
            "day": day_index,
            "start": start_str,
            "end": end_str,
            "person": ev.user_id,
            "title": ev.title
        })

    return render_template(
        'calendar.html',
        people=people_map,
        events=events_data,
        friends_ids=friends_ids,
        groups=groups_data,
        current_user_id=current_user.id
    )

@app.route('/api', methods=['GET'])
def api():
    section = request.args.get('section')
    result = None
    if section:
        result = fetch_class_by_section(section)
    return render_template('api.html', result=result, query=section)

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
            # Add events from UserCourse
            user_section.create_events(db.session)
            db.session.commit()
            flash("Section added successfully.", "success")
        except IntegrityError:
            db.session.rollback()
            flash("You have already added this section.", "info")

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
        sections=sections
    )


@app.route('/classes/remove/<int:section_id>', methods=['POST'])
@login_required
def remove_class(section_id):
    # Retrieve the UserCourse entry linking the current user and the section
    # Note: section_id here refers to the CourseSection.id (database primary key), not ASU ID
    user_course = models.UserCourse.query.filter_by(
        user_id=current_user.id,
        section_id=section_id
    ).first_or_404()
    
    db.session.delete(user_course)
    db.session.commit()
    flash("Class removed.", "info")
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
        db.session.add(req)
        db.session.commit()
        flash(f"Friend request sent to @{target_user.username}!", "success")
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
        if link.requester_id == current_user.id:
            friends_list.append(link.receiver)
        else:
            friends_list.append(link.requester)

    return render_template("friends.html", requests=incoming_requests, friends=friends_list)


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
    
    # Verify status is accepted (or pending)
    if friendship.status != 'accepted':
         # If not accepted, it might be a pending request. Deleting it cancels the request.
         pass

    db.session.delete(friendship)
    db.session.commit()
    flash("Friend removed.", "info")
    return redirect(url_for("friends"))


@app.route('/friends/respond/<int:request_id>/<action>')
@login_required
def friend_respond(request_id, action):
    req = models.Friendship.query.get_or_404(request_id)
    
    if req.receiver_id != current_user.id:
        flash("Unauthorized action.", "danger")
        return redirect(url_for("friends"))

    if action == 'accept':
        req.status = 'accepted'
        db.session.commit()
        flash(f"You are now friends with @{req.requester.username}!", "success")
    elif action == 'decline':
        db.session.delete(req)
        db.session.commit()
        flash("Friend request declined.", "info")
    
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
        
        new_group = models.Group(name=name, description=description, created_by=current_user.id)
        db.session.add(new_group)
        db.session.commit() # Commit to get ID
        
        # Add creator as admin
        membership = models.GroupMember(group_id=new_group.id, user_id=current_user.id, role='admin')
        db.session.add(membership)
        db.session.commit()
        
        flash("Group created!", "success")
        return redirect(url_for("groups"))

    # GET: List my groups
    memberships = models.GroupMember.query.filter_by(user_id=current_user.id).all()
    my_groups = [m.group for m in memberships]
    
    return render_template("groups.html", groups=my_groups)


@app.route('/groups/leave/<int:group_id>', methods=['POST'])
@login_required
def leave_group(group_id):
    membership = models.GroupMember.query.filter_by(
        group_id=group_id, 
        user_id=current_user.id
    ).first_or_404()
    
    group = membership.group
    
    # Check if admin constraint
    if membership.role == 'admin':
        # Check if there are other members
        other_members_count = models.GroupMember.query.filter(
            models.GroupMember.group_id == group_id,
            models.GroupMember.user_id != current_user.id
        ).count()

        if other_members_count > 0:
            # Check if there is another admin
            other_admin = models.GroupMember.query.filter(
                models.GroupMember.group_id == group_id,
                models.GroupMember.user_id != current_user.id,
                models.GroupMember.role == 'admin'
            ).first()
            
            if not other_admin:
                flash("You cannot leave the group as the only admin while other members exist. Promote someone else first.", "danger")
                return redirect(url_for("view_group", group_id=group_id))

    db.session.delete(membership)
    db.session.commit()
    
    # Clean up empty group
    if not group.members:
        db.session.delete(group)
        db.session.commit()
        
    flash("You have left the group.", "info")
    return redirect(url_for("groups"))


@app.route('/groups/invite/<int:group_id>', methods=['POST'])
@login_required
def invite_member(group_id):
    group = models.Group.query.get_or_404(group_id)
    # Check if current user is member (assume any member can invite for now?)
    # or strict to admin? The prompt didn't specify restrictive invite permissions, but implied general "allow for users to be invited".
    # Let's verify membership at least.
    me_member = models.GroupMember.query.filter_by(group_id=group.id, user_id=current_user.id).first()
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
    
    # Check if already member
    exists = models.GroupMember.query.filter_by(group_id=group.id, user_id=user_to_add.id).first()
    if exists:
        flash("User is already in the group.", "info")
        return redirect(url_for("view_group", group_id=group_id))

    new_member = models.GroupMember(group_id=group.id, user_id=user_to_add.id, role='member')
    db.session.add(new_member)
    db.session.commit()
    
    flash(f"@{username} added to the group!", "success")
    return redirect(url_for("view_group", group_id=group_id))


@app.route('/groups/kick/<int:group_id>/<int:user_id>', methods=['POST'])
@login_required
def kick_member(group_id, user_id):
    # Check permissions
    me_member = models.GroupMember.query.filter_by(group_id=group_id, user_id=current_user.id).first()
    if not me_member or me_member.role != 'admin':
        flash("Only admins can kick members.", "danger")
        return redirect(url_for("view_group", group_id=group_id))
    
    target_member = models.GroupMember.query.filter_by(group_id=group_id, user_id=user_id).first_or_404()
    if target_member.role == 'admin':
         flash("Cannot kick another admin.", "danger") # Simple rule, or allow if multiple admins? Let's stay safe.
         return redirect(url_for("view_group", group_id=group_id))

    db.session.delete(target_member)
    db.session.commit()
    flash("Member removed.", "success")
    return redirect(url_for("view_group", group_id=group_id))


@app.route('/groups/promote/<int:group_id>/<int:user_id>', methods=['POST'])
@login_required
def promote_member(group_id, user_id):
    # Check permissions
    me_member = models.GroupMember.query.filter_by(group_id=group_id, user_id=current_user.id).first()
    if not me_member or me_member.role != 'admin':
        flash("Only admins can promote members.", "danger")
        return redirect(url_for("view_group", group_id=group_id))
    
    target_member = models.GroupMember.query.filter_by(group_id=group_id, user_id=user_id).first_or_404()
    target_member.role = 'admin'
    db.session.commit()
    flash(f"{target_member.user.username} promoted to Admin!", "success")
    return redirect(url_for("view_group", group_id=group_id))


@app.route('/groups/<int:group_id>')
@login_required
def view_group(group_id):
    group = models.Group.query.get_or_404(group_id)
    # Check membership
    member = models.GroupMember.query.filter_by(group_id=group.id, user_id=current_user.id).first()
    if not member:
        flash("You are not a member of this group.", "danger")
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

    return render_template(
        "group_detail.html", 
        group=group, 
        membership=member,
        is_admin=is_admin,
        friend_ids=friend_ids,
        pending_ids=pending_ids
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

        db.session.add(user)
        db.session.commit()

        flash("Account created successfully.", "success")

        login_user(user)
        
        return redirect("/")
    
    return render_template('register.html')

@app.route('/me', methods=['GET', 'POST'])
@login_required
def me():
    if request.method == 'POST':
        if 'profile_pic' in request.files:
            file = request.files['profile_pic']
            if file.filename:
                filename = secure_filename(file.filename)
                # Prefix with user_id to keep it unique per user
                unique_filename = f"u{current_user.id}_{filename}"
                
                # Ensure directory exists
                if not os.path.exists(app.config['UPLOAD_FOLDER']):
                    os.makedirs(app.config['UPLOAD_FOLDER'])
                
                file_path = os.path.join(app.config['UPLOAD_FOLDER'], unique_filename)
                file.save(file_path)
                
                current_user.profile_image = unique_filename
                db.session.commit()
                flash("Profile picture updated!", "success")
        
        # Determine if we have other fields to update (not requested, but good practice)
        return redirect(url_for('me'))

    return render_template('me.html')

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