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
from extras.api import fetch_class_by_section
"""Models"""
import models


# ----- Functions -----
@login_manager.user_loader
def load_user(user_id):
    return models.User.query.get(int(user_id))


# ----- Routes -----
@app.route('/', methods=['GET'])
def index():
    section = request.args.get('section')
    result = None
    if section:
        result = fetch_class_by_section(section)
    return render_template('index.html', result=result, query=section)

@login_required
@app.route('/view', methods=['GET'])
def view():
    return render_template('view.html')

@app.route('/view2', methods=['GET'])
def view2():
    return render_template('view2.html')

@app.route('/view3', methods=['GET'])
def view3():
    return render_template('view3.html')

@app.route('/me', methods=['GET'])
def me():
    if request.method == "POST":
        asu_section_id = request.form.get("asu_section_id", "").strip()

        if not asu_section_id:
            flash("Section ID is required.", "danger")
            return redirect("/me")

        # Look up the section by ASU section ID
        section = models.CourseSection.query.filter_by(
            asu_section_id=asu_section_id
        ).first()

        if not section:
            flash("That section does not exist.", "warning")
            return redirect("/me")

        # Attempt to associate user with section
        user_section = models.UserCourse(
            user_id=current_user.id,
            section_id=section.id
        )

        try:
            db.session.add(user_section)
            db.session.commit()
            flash("Section added successfully.", "success")
        except IntegrityError:
            db.session.rollback()
            flash("You have already added this section.", "info")

        return redirect("/me")
    # --- GET ---
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