from werkzeug.security import generate_password_hash, check_password_hash
from extras.extensions import db
from flask_login import UserMixin
from sqlalchemy import UniqueConstraint


class User(db.Model, UserMixin):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    first_name = db.Column(db.String(25), nullable=False)
    last_name = db.Column(db.String(50), nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)

    # Relationships:
    saved_sections = db.relationship(
        "UserCourse", back_populates="user", cascade="all, delete-orphan"
    )

    def __repr__(self):
        return f"<{self.full_name} (id={self.id})>"

    # Helpers:
    def set_password(self, password: str):
        """Hash & store password."""
        self.password_hash = generate_password_hash(password)

    def check_password(self, password: str) -> bool:
        """Return True if password matches stored hash."""
        return check_password_hash(self.password_hash, password)
    
    @property
    def full_name(self) -> str:
        return f"{self.first_name} {self.last_name}"


class Course(db.Model):
    """Course itself"""
    __tablename__ = "courses"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    asu_course_id = db.Column(db.String(7), nullable=False)
    title = db.Column(db.String(100), nullable=False)
    sections = db.relationship("CourseSection", back_populates="course", cascade="all, delete-orphan")


class CourseSection(db.Model):
    """Specific course sections/offerings"""
    __tablename__ = "course_sections"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    course_id = db.Column(db.Integer, db.ForeignKey("courses.id", ondelete="CASCADE"), nullable=False)
    asu_section_id = db.Column(db.Integer, nullable=False, unique=True)  # unique section identifier
    term = db.Column(db.String(20), nullable=False)        # e.g. "Fall 2026"
    days_of_week = db.Column(db.String(21), nullable=False)  # e.g. "Mon,Wed,Fri"
    start_time = db.Column(db.Time, nullable=False)
    end_time = db.Column(db.Time, nullable=False)
    start_date = db.Column(db.Date, nullable=False)
    end_date = db.Column(db.Date, nullable=False)

    # Relationships
    course = db.relationship("Course", back_populates="sections")
    enrolled_users = db.relationship("UserCourse", back_populates="section", cascade="all, delete-orphan")


class UserCourse(db.Model):
    """Association table: Which users saved/enrolled in which course section"""
    __tablename__ = "user_courses"
    __table_args__ = (UniqueConstraint("user_id", "section_id", name="uq_user_section"),)

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    section_id = db.Column(db.Integer, db.ForeignKey("course_sections.id", ondelete="CASCADE"), nullable=False)

    user = db.relationship("User", back_populates="saved_sections")
    section = db.relationship("CourseSection", back_populates="enrolled_users")