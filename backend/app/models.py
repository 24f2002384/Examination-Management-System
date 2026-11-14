from datetime import datetime, timezone
from werkzeug.security import generate_password_hash, check_password_hash
from .extensions import db


def now_utc():
    return datetime.now(timezone.utc)


class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(254), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), nullable=False, index=True)
    full_name = db.Column(db.String(120), nullable=False)
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=now_utc)
    student_profile = db.relationship("StudentProfile", back_populates="user", uselist=False, cascade="all, delete-orphan")
    examiner_profile = db.relationship("ExaminerProfile", back_populates="user", uselist=False, cascade="all, delete-orphan")
    notifications = db.relationship("Notification", back_populates="user", cascade="all, delete-orphan")
    audit_logs = db.relationship("AuditLog", back_populates="actor")

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    def to_dict(self):
        return {"id": self.id, "email": self.email, "role": self.role, "full_name": self.full_name}


class StudentProfile(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id", ondelete="CASCADE"), unique=True, nullable=False)
    student_number = db.Column(db.String(40), unique=True)
    program = db.Column(db.String(120))
    department = db.Column(db.String(120))
    user = db.relationship("User", back_populates="student_profile")


class ExaminerProfile(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id", ondelete="CASCADE"), unique=True, nullable=False)
    department = db.Column(db.String(120))
    user = db.relationship("User", back_populates="examiner_profile")


class Course(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(30), unique=True, nullable=False)
    name = db.Column(db.String(160), nullable=False)
    description = db.Column(db.Text)
    status = db.Column(db.String(20), nullable=False, default="active", index=True)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=now_utc)
    updated_at = db.Column(db.DateTime(timezone=True), nullable=False, default=now_utc, onupdate=now_utc)
    examinations = db.relationship("Examination", back_populates="course")


class Examination(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    course_id = db.Column(db.Integer, db.ForeignKey("course.id", ondelete="RESTRICT"), nullable=False, index=True)
    examiner_id = db.Column(db.Integer, db.ForeignKey("user.id", ondelete="SET NULL"), index=True)
    title = db.Column(db.String(180), nullable=False)
    description = db.Column(db.Text)
    exam_type = db.Column(db.String(30), nullable=False, default="Viva")
    duration_minutes = db.Column(db.Integer, nullable=False, default=30)
    max_marks = db.Column(db.Float, nullable=False, default=100)
    slot_creation_start = db.Column(db.DateTime(timezone=True))
    slot_creation_end = db.Column(db.DateTime(timezone=True))
    booking_start = db.Column(db.DateTime(timezone=True))
    booking_end = db.Column(db.DateTime(timezone=True))
    status = db.Column(db.String(20), nullable=False, default="draft", index=True)
    starts_at = db.Column(db.DateTime(timezone=True))
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=now_utc)
    updated_at = db.Column(db.DateTime(timezone=True), nullable=False, default=now_utc, onupdate=now_utc)
    course = db.relationship("Course", back_populates="examinations")
    examiner = db.relationship("User", foreign_keys=[examiner_id])
    rubric_criteria = db.relationship("RubricCriterion", back_populates="examination", cascade="all, delete-orphan")
    slots = db.relationship("ExaminationSlot", back_populates="examination", cascade="all, delete-orphan")


class RubricCriterion(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    examination_id = db.Column(db.Integer, db.ForeignKey("examination.id", ondelete="CASCADE"), nullable=False, index=True)
    name = db.Column(db.String(160), nullable=False)
    description = db.Column(db.Text)
    max_score = db.Column(db.Float, nullable=False)
    weightage = db.Column(db.Float, nullable=False, default=0)
    examination = db.relationship("Examination", back_populates="rubric_criteria")


class ExaminationSlot(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    examination_id = db.Column(db.Integer, db.ForeignKey("examination.id", ondelete="CASCADE"), nullable=False, index=True)
    examiner_id = db.Column(db.Integer, db.ForeignKey("user.id", ondelete="CASCADE"), nullable=False, index=True)
    starts_at = db.Column(db.DateTime(timezone=True), nullable=False)
    ends_at = db.Column(db.DateTime(timezone=True), nullable=False)
    capacity = db.Column(db.Integer, nullable=False, default=1)
    status = db.Column(db.String(20), nullable=False, default="open")
    meeting_link = db.Column(db.String(500))
    reserved_count = db.Column(db.Integer, nullable=False, default=0)
    examination = db.relationship("Examination", back_populates="slots")
    examiner = db.relationship("User", foreign_keys=[examiner_id])
    bookings = db.relationship("Booking", back_populates="slot")


class Booking(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    slot_id = db.Column(db.Integer, db.ForeignKey("examination_slot.id", ondelete="CASCADE"), nullable=False, index=True)
    student_id = db.Column(db.Integer, db.ForeignKey("user.id", ondelete="CASCADE"), nullable=False, index=True)
    examination_id = db.Column(db.Integer, db.ForeignKey("examination.id", ondelete="CASCADE"), nullable=True, index=True)
    status = db.Column(db.String(20), nullable=False, default="booked", index=True)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=now_utc)
    __table_args__ = (
        db.UniqueConstraint("slot_id", "student_id", name="uq_booking_slot_student"),
        db.Index("uq_active_student_exam", "student_id", "examination_id", unique=True,
                 sqlite_where=db.text("status = 'booked'")),
    )
    slot = db.relationship("ExaminationSlot", back_populates="bookings")
    student = db.relationship("User", foreign_keys=[student_id])
    evaluation = db.relationship("Evaluation", back_populates="booking", uselist=False)
    submission = db.relationship("Submission", back_populates="booking", uselist=False, cascade="all, delete-orphan")


class Submission(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    booking_id = db.Column(db.Integer, db.ForeignKey("booking.id", ondelete="CASCADE"), unique=True, nullable=False, index=True)
    student_id = db.Column(db.Integer, db.ForeignKey("user.id", ondelete="CASCADE"), nullable=False, index=True)
    original_filename = db.Column(db.String(255), nullable=False)
    stored_filename = db.Column(db.String(80), nullable=False, unique=True)
    size_bytes = db.Column(db.Integer, nullable=False)
    submitted_at = db.Column(db.DateTime(timezone=True), nullable=False, default=now_utc)
    updated_at = db.Column(db.DateTime(timezone=True), nullable=False, default=now_utc, onupdate=now_utc)
    booking = db.relationship("Booking", back_populates="submission")
    student = db.relationship("User", foreign_keys=[student_id])


class Evaluation(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    booking_id = db.Column(db.Integer, db.ForeignKey("booking.id", ondelete="CASCADE"), unique=True, nullable=False)
    examiner_id = db.Column(db.Integer, db.ForeignKey("user.id", ondelete="SET NULL"))
    status = db.Column(db.String(20), nullable=False, default="draft")
    total_score = db.Column(db.Float)
    submitted_at = db.Column(db.DateTime(timezone=True))
    published_at = db.Column(db.DateTime(timezone=True))
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=now_utc)
    booking = db.relationship("Booking", back_populates="evaluation")
    examiner = db.relationship("User", foreign_keys=[examiner_id])
    criterion_scores = db.relationship("EvaluationCriterionScore", back_populates="evaluation", cascade="all, delete-orphan")


class EvaluationCriterionScore(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    evaluation_id = db.Column(db.Integer, db.ForeignKey("evaluation.id", ondelete="CASCADE"), nullable=False)
    criterion_id = db.Column(db.Integer, db.ForeignKey("rubric_criterion.id", ondelete="RESTRICT"), nullable=False)
    score = db.Column(db.Float, nullable=False)
    feedback = db.Column(db.Text)
    __table_args__ = (db.UniqueConstraint("evaluation_id", "criterion_id", name="uq_eval_criterion"),)
    evaluation = db.relationship("Evaluation", back_populates="criterion_scores")
    criterion = db.relationship("RubricCriterion")


class Notification(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id", ondelete="CASCADE"), nullable=False, index=True)
    message = db.Column(db.String(500), nullable=False)
    read_at = db.Column(db.DateTime(timezone=True))
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=now_utc)
    user = db.relationship("User", back_populates="notifications")


class GmailConnection(db.Model):
    """Single server-side Gmail sender connection; credentials are Fernet-encrypted."""
    id = db.Column(db.Integer, primary_key=True)
    account_email = db.Column(db.String(254))
    encrypted_credentials = db.Column(db.Text)
    status = db.Column(db.String(20), nullable=False, default="disconnected")
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=now_utc)
    updated_at = db.Column(db.DateTime(timezone=True), nullable=False, default=now_utc, onupdate=now_utc)


class MonthlyReport(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    period_start = db.Column(db.Date, nullable=False, unique=True, index=True)
    period_end = db.Column(db.Date, nullable=False)
    status = db.Column(db.String(20), nullable=False, default="queued", index=True)
    artifact_name = db.Column(db.String(255))
    summary_json = db.Column(db.Text)
    delivery_status = db.Column(db.String(30), nullable=False, default="not_configured")
    safe_error = db.Column(db.String(500))
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=now_utc)
    generated_at = db.Column(db.DateTime(timezone=True))


class ExportJob(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    job_id = db.Column(db.String(36), nullable=False, unique=True, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id", ondelete="CASCADE"), nullable=False, index=True)
    status = db.Column(db.String(20), nullable=False, default="requested", index=True)
    artifact_name = db.Column(db.String(255))
    safe_error = db.Column(db.String(500))
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=now_utc)
    started_at = db.Column(db.DateTime(timezone=True))
    completed_at = db.Column(db.DateTime(timezone=True))
    user = db.relationship("User")


class ReminderLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    booking_id = db.Column(db.Integer, db.ForeignKey("booking.id", ondelete="CASCADE"), nullable=False)
    reminder_date = db.Column(db.Date, nullable=False)
    status = db.Column(db.String(20), nullable=False, default="pending")
    safe_error = db.Column(db.String(500))
    delivered_at = db.Column(db.DateTime(timezone=True))
    __table_args__ = (db.UniqueConstraint("booking_id", "reminder_date", name="uq_booking_reminder_period"),)


class AuditLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    actor_id = db.Column(db.Integer, db.ForeignKey("user.id", ondelete="SET NULL"), index=True)
    action = db.Column(db.String(120), nullable=False)
    entity_type = db.Column(db.String(80))
    entity_id = db.Column(db.Integer)
    metadata_json = db.Column(db.Text)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=now_utc)
    actor = db.relationship("User", back_populates="audit_logs")


@db.event.listens_for(Booking, "before_insert")
def populate_booking_examination(_mapper, connection, booking):
    if booking.examination_id is None and booking.slot_id is not None:
        booking.examination_id = connection.execute(
            db.text("SELECT examination_id FROM examination_slot WHERE id=:slot_id"),
            {"slot_id": booking.slot_id},
        ).scalar_one_or_none()
