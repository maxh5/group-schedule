from flask import Flask, render_template, request, redirect, flash
from flask_login import logout_user, login_required, login_user, current_user
import os
from dotenv import load_dotenv
from sqlalchemy.exc import IntegrityError


# ----- Load environment -----
load_dotenv()


# ----- App config -----
app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'dev-secret')
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL', 'sqlite:///dev.db') # Default to SQLite for local dev
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False


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
    events = None
    if current_user.is_authenticated:
        events = (
            models.Event.query
            .filter_by(user_id=current_user.id)
            .order_by(models.Event.start)
            .all()
        )
    return render_template('index.html', events=events)

@app.route('/api', methods=['GET'])
def api():
    section = request.args.get('section')
    result = None
    if section:
        result = fetch_class_by_section(section)
    return render_template('api.html', result=result, query=section)

@login_required
@app.route('/view', methods=['GET'])
def view():
    return render_template('view.html')

@app.route('/view2', methods=['GET'])
def view2():
    return render_template('view2.html')

@app.route('/view3', methods=['GET'])
def view3():
    # Sample data for now
    people = [
        {"id": "p1", "name": "Aisha", "color": "#FF8A80"},
        {"id": "p2", "name": "Ben", "color": "#FFD180"},
        {"id": "p3", "name": "Carmen", "color": "#FFFF8D"},
        {"id": "p4", "name": "Diego", "color": "#B9F6CA"},
        {"id": "p5", "name": "Eve", "color": "#80D8FF"},
        {"id": "p6", "name": "Farah", "color": "#B388FF"},
        {"id": "p7", "name": "Gus", "color": "#CFD8DC"}
    ]
    events = [
        {"day": 0, "start": "08:00", "end": "09:50", "person": "p1"},
        {"day": 0, "start": "09:30", "end": "11:00", "person": "p2"},
        {"day": 0, "start": "10:45", "end": "12:15", "person": "p3"},
        {"day": 0, "start": "11:30", "end": "13:00", "person": "p4"},
        {"day": 0, "start": "15:00", "end": "16:30", "person": "p5"},
        {"day": 1, "start": "08:30", "end": "10:10", "person": "p6"},
        {"day": 1, "start": "09:00", "end": "12:00", "person": "p1"},
        {"day": 1, "start": "11:50", "end": "13:20", "person": "p2"},
        {"day": 2, "start": "08:00", "end": "09:00", "person": "p7"},
        {"day": 2, "start": "09:15", "end": "10:45", "person": "p3"},
        {"day": 2, "start": "10:00", "end": "11:00", "person": "p4"},
        {"day": 3, "start": "12:00", "end": "14:00", "person": "p5"},
        {"day": 3, "start": "13:30", "end": "15:00", "person": "p6"},
        {"day": 4, "start": "08:00", "end": "11:30", "person": "p1"},
        {"day": 4, "start": "10:00", "end": "12:00", "person": "p2"},
        {"day": 4, "start": "11:20", "end": "13:30", "person": "p3"},
    ]
    return render_template('view3.html', people=people, events=events)

@app.route('/view4', methods=['GET'])
def view4():
    return render_template('view4.html')

@app.route('/me', methods=['GET', 'POST'])
@login_required
def me():
    if request.method == "POST":
        '''
        Takes the form, if the section (and class) are not already stored, makes them. Then adds user
        '''
        asu_section_id = int(request.form.get("asu_section_id", "").strip())

        if not asu_section_id:
            flash("Section ID is required.", "danger")
            return redirect("/me")

        section = models.CourseSection.query.filter_by(
            asu_section_id=asu_section_id
        ).first()

        if not section:
            # Fetch section data from API
            class_raw = fetch_class_by_section(asu_section_id)
            if not class_raw:
                flash("Section does not exist.", "danger")
                return redirect("/me")
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

        return redirect("/me")
    # GET
    user_sections = (
        models.UserCourse.query
        .filter_by(user_id=current_user.id)
        .join(models.CourseSection)
        .all()
    )
    sections = [uc.section for uc in user_sections]
    return render_template(
        "me.html",
        sections=sections
    )


# ----- Routes: AUTH -----
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == "POST":
        first_name = request.form["first_name"]
        last_name = request.form["last_name"]
        password = request.form["password"]
        
        user = models.User.query.filter_by(
            first_name=first_name,
            last_name=last_name
        ).first()

        if not user or not user.check_password(password):
            flash("Invalid name or password.", "danger")
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
        password = request.form["password"]
        confirm = request.form["confirm_password"]

        if password != confirm:
            flash("Passwords do not match.", "danger")
            return render_template("register.html")

        user = models.User(first_name=first_name, last_name=last_name)
        user.set_password(password)

        db.session.add(user)
        db.session.commit()

        flash("Account created successfully.", "success")

        login_user(user)
        
        return redirect("/")
    
    return render_template('register.html')

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