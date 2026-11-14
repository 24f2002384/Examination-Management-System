from functools import wraps
from datetime import datetime, timezone
from sqlalchemy import or_, desc
from flask import current_app, jsonify, request, session
from email_validator import validate_email, EmailNotValidError
from ..extensions import db
from ..models import User, Course, Examination, StudentProfile, ExaminerProfile, ExaminationSlot, Booking, Evaluation, Notification, AuditLog
from ..services.auth import register_student
from ..services.email import send_notice
from . import api


def error(message, status):
    return jsonify({"error": {"message": message, "status": status}}), status


def current_user():
    user_id = session.get("user_id")
    return db.session.get(User, user_id) if user_id else None


def login_required(role=None):
    def decorator(fn):
        @wraps(fn)
        def wrapped(*args, **kwargs):
            user = current_user()
            if not user or not user.is_active:
                return error("Authentication required", 401)
            if role and user.role not in ([role] if isinstance(role, str) else role):
                return error("You do not have permission to access this resource", 403)
            return fn(*args, **kwargs)
        return wrapped
    return decorator


@api.get("/health")
def health():
    db.session.execute(db.text("SELECT 1"))
    return jsonify({"status": "ok", "database": "ok"})


@api.post("/auth/register")
def register():
    data = request.get_json(silent=True) or {}
    name, email, password = data.get("full_name"), data.get("email"), data.get("password")
    if not isinstance(name, str) or not 2 <= len(name.strip()) <= 120:
        return error("full_name must be between 2 and 120 characters", 400)
    if not isinstance(email, str) or not isinstance(password, str) or len(password) < 10 or len(password) > 128:
        return error("A valid email and password of at least 10 characters are required", 400)
    try:
        email = validate_email(email, check_deliverability=False).normalized
    except EmailNotValidError:
        return error("A valid email address is required", 400)
    user = register_student(name, email, password)
    if not user:
        return error("An account with this email already exists", 409)
    login_url = current_app.config.get("FRONTEND_ORIGIN", "http://localhost:5173").rstrip("/") + "/login"
    if not send_notice(user.email, "Welcome to ExamWise",
                       f"Hello {user.full_name},\n\nYour ExamWise student account was created successfully. "
                       f"You can sign in here: {login_url}\n\nWelcome to ExamWise."):
        current_app.logger.warning("Welcome email was not delivered for newly registered student")
    session["user_id"] = user.id
    return jsonify({"user": user.to_dict()}), 201


@api.post("/auth/login")
def login():
    data = request.get_json(silent=True) or {}
    email, password = data.get("email"), data.get("password")
    role = data.get("role")
    if not isinstance(email, str) or not isinstance(password, str):
        return error("email and password are required", 400)
    user = User.query.filter_by(email=email.lower().strip()).first()
    if role is not None and (not isinstance(role, str) or role not in {"admin", "examiner", "student"}):
        return error("Choose Admin, Examiner, or Student", 400)
    if not user or not user.check_password(password) or not user.is_active or (role and user.role != role):
        return error("Invalid email or password", 401)
    session.clear()
    session["user_id"] = user.id
    return jsonify({"user": user.to_dict()})


@api.post("/auth/logout")
def logout():
    session.clear()
    response = jsonify({"message": "Logged out"})
    response.delete_cookie("session")
    response.delete_cookie("csrf_token")
    return response


@api.get("/auth/csrf")
def csrf_token():
    import secrets
    from flask_wtf.csrf import generate_csrf
    # Flask-WTF stores the signed token in the session; return it and persist the
    # session cookie before the browser sends the next mutating request.
    session.setdefault("csrf_initialized", True)
    session.setdefault("csrf_token", secrets.token_hex(32))
    return jsonify({"csrf_token": generate_csrf()})


@api.get("/auth/me")
@login_required()
def me():
    return jsonify({"user": current_user().to_dict()})


@api.route("/profile", methods=["GET", "PATCH"])
@login_required()
def profile():
    user = current_user()
    if request.method == "PATCH":
        data = request.get_json(silent=True) or {}
        allowed = {"full_name", "email", "current_password"}
        if not isinstance(data, dict) or set(data) - allowed:
            return error("Only full_name, email and current_password may be updated", 400)
        if "full_name" in data:
            value = data["full_name"]
            if not isinstance(value, str) or not 2 <= len(value.strip()) <= 120:
                return error("full_name must be between 2 and 120 characters", 400)
            user.full_name = value.strip()
        if "email" in data:
            try:
                email = validate_email(data["email"], check_deliverability=False).normalized
            except (EmailNotValidError, TypeError):
                return error("A valid email address is required", 400)
            if email != user.email:
                current_password = data.get("current_password")
                if not isinstance(current_password, str) or not user.check_password(current_password):
                    return error("Enter your current password to change your email address", 400)
            if User.query.filter(User.email == email, User.id != user.id).first():
                return error("An account with this email already exists", 409)
            user.email = email
        db.session.commit()
    return jsonify({"user": user.to_dict()})


def student_exam_updates(now):
    """Return preparation/opening states separately from exams students can book."""
    rows = Examination.query.join(Course).filter(Course.status == "active", or_(
        (Examination.status == "slot_creation") & (Examination.slot_creation_end >= now),
        (Examination.status == "booking_open") & Examination.booking_start.isnot(None) & (Examination.booking_end >= now),
    )).order_by(Examination.created_at.desc()).limit(8).all()

    def utc(value):
        return value.replace(tzinfo=timezone.utc) if value and value.tzinfo is None else value

    updates = []
    for exam in rows:
        start, end = utc(exam.booking_start), utc(exam.booking_end)
        phase = "preparing" if exam.status == "slot_creation" else "scheduled" if start and start > now else "open"
        updates.append({"id": exam.id, "title": exam.title, "course_code": exam.course.code,
                        "type": exam.exam_type, "phase": phase,
                        "booking_start": start.isoformat() if start else None,
                        "booking_end": end.isoformat() if end else None})
    return updates


@api.get("/dashboard")
@login_required()
def dashboard():
    user = current_user()
    if user.role == "admin":
        stats = {"courses": Course.query.count(), "examinations": Examination.query.count(),
                 "students": User.query.filter_by(role="student").count(), "examiners": User.query.filter_by(role="examiner").count(),
                 "slots": ExaminationSlot.query.count(), "bookings": Booking.query.count()}
        upcoming = Examination.query.filter(Examination.status.in_(["slot_creation", "booking_open"])).order_by(Examination.booking_start).limit(5).all()
        recent = AuditLog.query.order_by(AuditLog.created_at.desc()).limit(6).all()
        return jsonify({"role": user.role, "stats": stats, "upcoming_examinations": [exam.title for exam in upcoming],
                        "recent_activity": [{"id": item.id, "action": item.action.replace(".", " "), "created_at": item.created_at.isoformat()} for item in recent]})
    if user.role == "examiner":
        assigned = Examination.query.filter_by(examiner_id=user.id)
        slots = ExaminationSlot.query.filter_by(examiner_id=user.id)
        bookings = Booking.query.join(ExaminationSlot).filter(ExaminationSlot.examiner_id == user.id)
        pending = Booking.query.join(ExaminationSlot).outerjoin(Evaluation).filter(ExaminationSlot.examiner_id == user.id,
            Booking.status.in_(["booked", "completed"]), or_(Evaluation.id.is_(None), Evaluation.status == "draft")).count()
        upcoming = slots.filter(ExaminationSlot.starts_at >= datetime.now(timezone.utc), ExaminationSlot.status != "cancelled").order_by(ExaminationSlot.starts_at).limit(5).all()
        recent = Notification.query.filter_by(user_id=user.id).order_by(desc(Notification.created_at)).limit(5).all()
        return jsonify({"role": user.role, "stats": {"assigned_examinations": assigned.count(), "slots": slots.count(), "booked_students": bookings.filter(Booking.status == "booked").count(), "pending_evaluations": pending}, "upcoming_schedule": [{"examination": slot.examination.title, "starts_at": slot.starts_at.isoformat()} for slot in upcoming], "recent_activity": [{"id": n.id, "action": n.message, "created_at": n.created_at.isoformat()} for n in recent]})
    bookings = Booking.query.filter_by(student_id=user.id)
    now = datetime.now(timezone.utc)
    available = Examination.query.join(Course).filter(Course.status == "active", Examination.status == "booking_open",
        Examination.booking_start <= now, Examination.booking_end >= now).count()
    exam_updates = student_exam_updates(now)
    notifications = Notification.query.filter_by(user_id=user.id).order_by(desc(Notification.created_at)).limit(5).all()
    upcoming = bookings.join(ExaminationSlot).filter(Booking.status == "booked", ExaminationSlot.starts_at >= now).order_by(ExaminationSlot.starts_at).limit(5).all()
    return jsonify({"role": user.role, "stats": {"available_examinations": available, "upcoming_examinations": len(upcoming), "booked_slots": bookings.filter(Booking.status == "booked").count(), "published_results": Evaluation.query.join(Booking).filter(Booking.student_id == user.id, Evaluation.status == "published").count()}, "upcoming_schedule": [{"examination": b.slot.examination.title, "starts_at": b.slot.starts_at.isoformat()} for b in upcoming], "recent_activity": [{"id": n.id, "action": n.message, "created_at": n.created_at.isoformat()} for n in notifications], "examination_updates": exam_updates})


@api.get("/admin/overview")
@login_required("admin")
def admin_overview():
    return dashboard()


@api.get("/examiners")
@login_required("admin")
def examiners():
    page = max(request.args.get("page", 1, type=int), 1)
    per_page = min(max(request.args.get("per_page", 20, type=int), 1), 100)
    pagination = User.query.filter_by(role="examiner").order_by(User.id).paginate(page=page, per_page=per_page, error_out=False)
    return jsonify({"items": [user.to_dict() for user in pagination.items], "pagination": {"page": page, "per_page": per_page, "total": pagination.total, "pages": pagination.pages}})
