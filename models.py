from werkzeug.security import generate_password_hash, check_password_hash
from extras.extensions import db
from flask_login import UserMixin
from sqlalchemy import UniqueConstraint
from datetime import datetime, timedelta


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
    asu_section_id = db.Column(db.Integer, nullable=False, unique=True)
    term = db.Column(db.String(20), nullable=False)
    location = db.Column(db.String(25), nullable=False)
    days_of_week = db.Column(db.String(10), nullable=True)
    start_time = db.Column(db.Time, nullable=True)
    end_time = db.Column(db.Time, nullable=True)
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
    events = db.relationship("Event", back_populates="user_course", cascade="all, delete-orphan")

    def create_events(self, session):
        """Create event rows based on this UserCourse connection.

        This will create one Event per meeting date derived from the linked
        CourseSection and add them to the provided SQLAlchemy session (or
        `db.session` if none is provided).
        """
        user = self.user
        section = self.section
        start_date = self.section.start_date
        end_date = self.section.end_date
        start_time = self.section.start_time
        end_time = self.section.end_time

        # Map days to values (e.g. M = 1)
        days = section.days_of_week.split()
        mapping = {"M": 0, "T": 1, "W": 2, "Th": 3, "F": 4}
        dayValues = set(mapping[day] for day in days if day in mapping)

        # Idempotency: delete previously generated events for this user+section
        session.query(Event).filter_by(user_course_id=self.id).delete(synchronize_session=False)

        # For each day, find first occurance and then increment by 7
        for target_day in dayValues:
            delta_days = (target_day - start_date.weekday() + 7) % 7
            occurence_date = start_date + timedelta(days=delta_days)
            while occurence_date < end_date:
                start_dt = datetime.combine(occurence_date, start_time)
                end_dt = datetime.combine(occurence_date, end_time)
                ev = Event(
                    user_id = self.user_id,
                    title = section.course.title,
                    start = start_dt,
                    end = end_dt,
                    user_course_id = self.id
                )
                session.add(ev)
                occurence_date += timedelta(days=7)
        return


class Event(db.Model):
    """Events as they appear on the calendar"""
    __tablename__ = "events"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)

    title = db.Column(db.String(255), nullable=False)
    start = db.Column(db.DateTime, nullable=False)
    end = db.Column(db.DateTime, nullable=False)

    user_course_id = db.Column(
        db.Integer,
        db.ForeignKey("user_courses.id", ondelete="CASCADE"),
        nullable=True,
    )
    user_course = db.relationship("UserCourse", back_populates="events")

    user = db.relationship("User", backref="events")

    def __repr__(self):
        return f"<Event {self.title} {self.start.isoformat()} -> {self.end.isoformat()}>"