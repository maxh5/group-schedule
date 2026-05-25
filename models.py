from extras.extensions import db
from flask_login import UserMixin
from sqlalchemy import UniqueConstraint
from datetime import datetime, timedelta


class User(db.Model, UserMixin):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    first_name = db.Column(db.String(25), nullable=False)
    last_name = db.Column(db.String(50), nullable=False)

    # Public @handle for friend/group discovery (set after Google sign-in onboarding)
    username = db.Column(db.String(30), unique=True, nullable=True)
    profile_image = db.Column(db.String(255), nullable=False, default='default.jpg')

    google_sub = db.Column(db.String(255), unique=True, nullable=False, index=True)
    email = db.Column(db.String(255), unique=True, nullable=False, index=True)
    # OAuth offline access for Google Calendar API (nullable until consent grants refresh_token)
    google_refresh_token = db.Column(db.Text, nullable=True)

    # Relationships:
    saved_sections = db.relationship(
        "UserCourse", back_populates="user", cascade="all, delete-orphan"
    )
    linked_calendars = db.relationship(
        "UserLinkedCalendar",
        back_populates="user",
        cascade="all, delete-orphan",
    )

    def __repr__(self):
        return f"<{self.full_name} (id={self.id})>"

    @property
    def full_name(self) -> str:
        return f"{self.first_name} {self.last_name}".strip() or (self.email or "User")


class Friendship(db.Model):
    """Tracks friend requests and accepted friendships"""
    __tablename__ = "friendships"

    id = db.Column(db.Integer, primary_key=True)
    requester_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    receiver_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    status = db.Column(db.String(20), default="pending", nullable=False)  # pending, accepted
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    requester = db.relationship("User", foreign_keys=[requester_id], backref="sent_requests")
    receiver = db.relationship("User", foreign_keys=[receiver_id], backref="received_requests")

    __table_args__ = (UniqueConstraint("requester_id", "receiver_id", name="uq_friendship"),)


class Group(db.Model):
    """User groups for sharing schedules"""
    __tablename__ = "groups"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    description = db.Column(db.String(255), nullable=True)
    profile_image = db.Column(db.String(255), nullable=False, default='default_group.jpg')
    created_by = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    members = db.relationship("GroupMember", back_populates="group", cascade="all, delete-orphan")


class GroupMember(db.Model):
    """Members of a group"""
    __tablename__ = "group_members"

    id = db.Column(db.Integer, primary_key=True)
    group_id = db.Column(db.Integer, db.ForeignKey("groups.id"), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    role = db.Column(db.String(20), default="member")  # admin, member
    status = db.Column(db.String(20), default="pending")  # pending, accepted
    joined_at = db.Column(db.DateTime, default=datetime.utcnow)
    display_order = db.Column(db.Integer, default=0)

    group = db.relationship("Group", back_populates="members")
    user = db.relationship("User", backref="groups")

    __table_args__ = (UniqueConstraint("group_id", "user_id", name="uq_group_member"),)


class UserLinkedCalendar(db.Model):
    """Per-user calendar source (Google calendarList id) and main-view visibility."""

    __tablename__ = "user_linked_calendars"
    __table_args__ = (UniqueConstraint("user_id", "provider", "external_id", name="uq_user_provider_cal"),)

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    provider = db.Column(db.String(32), nullable=False, default="google")
    external_id = db.Column(db.String(512), nullable=False)
    summary = db.Column(db.String(512), nullable=False, default="")
    background_color = db.Column(db.String(32), nullable=True)
    included_in_main_view = db.Column(db.Boolean, nullable=False, default=True)

    user = db.relationship("User", back_populates="linked_calendars")


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
        """Create event rows based on this UserCourse connection."""
        section = self.section
        start_date = self.section.start_date
        end_date = self.section.end_date
        start_time = self.section.start_time
        end_time = self.section.end_time

        days = section.days_of_week.split()
        mapping = {"M": 0, "T": 1, "W": 2, "Th": 3, "F": 4}
        dayValues = set(mapping[day] for day in days if day in mapping)

        session.query(Event).filter_by(user_course_id=self.id).delete(synchronize_session=False)

        for target_day in dayValues:
            delta_days = (target_day - start_date.weekday() + 7) % 7
            occurence_date = start_date + timedelta(days=delta_days)
            while occurence_date < end_date:
                start_dt = datetime.combine(occurence_date, start_time)
                end_dt = datetime.combine(occurence_date, end_time)
                ev = Event(
                    user_id=self.user_id,
                    title=section.course.title,
                    start=start_dt,
                    end=end_dt,
                    user_course_id=self.id
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
