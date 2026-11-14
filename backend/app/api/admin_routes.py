"""Admin management API. All mutations are validated and audited server-side."""
import json
import secrets
import string
from pathlib import Path
from datetime import datetime, timezone
from functools import wraps

from email_validator import validate_email, EmailNotValidError
from flask import current_app, jsonify, request
from sqlalchemy import func, or_
from sqlalchemy.exc import IntegrityError

from ..extensions import db
from ..services.cache import cached_response, invalidate_cache
from ..services.notifications import active_students, email_users, notify_users
from ..models import (AuditLog, Booking, Course, Evaluation, EvaluationCriterionScore, ExportJob,
                      Examination, ExaminationSlot, ExaminerProfile, RubricCriterion,
                      ReminderLog, StudentProfile, Submission, User, Notification, now_utc)
from . import api
from .routes import current_user, error, login_required

EXAM_TYPES = {"Viva", "Practical", "Project Demo", "Assessment"}
EXAM_STATUSES = {"draft", "slot_creation", "booking_open", "closed", "completed"}
BOOKING_STATUSES = {"booked", "cancelled", "completed"}


def audit(action, entity, entity_id, metadata=None):
    db.session.add(AuditLog(actor_id=current_user().id, action=action, entity_type=entity,
                            entity_id=entity_id, metadata_json=json.dumps(metadata or {}, sort_keys=True)))


def admin(fn):
    return login_required("admin")(fn)


def notify_exam_update(exam, newly_available=False):
    message = (f"A new examination is available: {exam.title}. Sign in and open Find Examinations to review the schedule and book a slot."
               if newly_available else f"The schedule or details for {exam.title} have been updated. Sign in to review your examination details.")
    recipients = active_students() if newly_available or exam.status == "booking_open" else active_students(exam.id, booked_only=True)
    if exam.examiner and exam.examiner.is_active:
        recipients.append(exam.examiner)
    recipients = notify_users(recipients, message)
    return recipients, message


def body():
    data = request.get_json(silent=True)
    return data if isinstance(data, dict) else None


def parse_date(value, field, required=False):
    if value in (None, ""):
        if required:
            raise ValueError(f"{field} is required")
        return None
    if not isinstance(value, str):
        raise ValueError(f"{field} must be an ISO date/time")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field} must be an ISO date/time") from exc
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)


def json_date(value):
    if value is None:
        return None
    # SQLite drops tzinfo on DateTime columns. All portal datetimes are stored
    # as UTC instants, so mark naive values as UTC before returning them. This
    # prevents browsers from interpreting UTC wall time as local time.
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def exam_instructions(exam):
    return exam.description or current_app.config.get("DEFAULT_EXAM_INSTRUCTIONS", "")


def pagination(query):
    page = request.args.get("page", 1, type=int)
    per_page = request.args.get("per_page", 20, type=int)
    if page is None or per_page is None or page < 1 or per_page < 1:
        raise ValueError("page and per_page must be positive integers")
    per_page = min(per_page, 100)
    p = query.paginate(page=page, per_page=per_page, error_out=False)
    return p


def paged(query, serializer):
    p = pagination(query)
    return jsonify({"items": [serializer(x) for x in p.items], "pagination": {
        "page": p.page, "per_page": p.per_page, "total": p.total, "pages": p.pages}})


def course_json(x):
    return {"id": x.id, "code": x.code, "name": x.name, "description": x.description,
            "status": x.status, "examination_count": len(x.examinations),
            "created_at": json_date(x.created_at), "updated_at": json_date(x.updated_at)}


def exam_json(x):
    return {"id": x.id, "course_id": x.course_id, "course_code": x.course.code,
            "course_name": x.course.name, "title": x.title, "type": x.exam_type,
            "description": exam_instructions(x), "duration_minutes": x.duration_minutes,
            "max_marks": x.max_marks, "slot_creation_start": json_date(x.slot_creation_start),
            "slot_creation_end": json_date(x.slot_creation_end), "booking_start": json_date(x.booking_start),
            "booking_end": json_date(x.booking_end), "status": x.status,
            "examiner_id": x.examiner_id, "examiner_name": x.examiner.full_name if x.examiner else None,
            "created_at": json_date(x.created_at), "updated_at": json_date(x.updated_at)}


def user_json(x):
    return {**x.to_dict(), "is_active": x.is_active,
            "department": x.examiner_profile.department if x.examiner_profile else None,
            "program": x.student_profile.program if x.student_profile else None,
            "student_number": x.student_profile.student_number if x.student_profile else None,
            "created_at": json_date(x.created_at)}


def slot_json(x):
    occupied = x.reserved_count
    if occupied == 0:
        # Preserve counts for legacy rows or records created before the reservation counter existed.
        occupied = sum(b.status in {"booked", "completed"} for b in x.bookings)
    status = "cancelled" if x.status == "cancelled" else "completed" if x.status == "completed" else "full" if occupied >= x.capacity else "available"
    return {"id": x.id, "examination_id": x.examination_id, "examination": x.examination.title,
            "examiner_id": x.examiner_id, "examiner": x.examiner.full_name,
            "starts_at": json_date(x.starts_at), "ends_at": json_date(x.ends_at),
            "capacity": x.capacity, "booked_seats": occupied, "available_seats": max(0, x.capacity-occupied), "status": status,
            "meeting_link": x.meeting_link}


def booking_json(x):
    return {"id": x.id, "student_id": x.student_id, "student": x.student.full_name,
            "student_email": x.student.email, "course": x.slot.examination.course.code,
            "examination_id": x.slot.examination_id, "examination": x.slot.examination.title,
            "slot_id": x.slot_id, "starts_at": json_date(x.slot.starts_at),
            "examiner_id": x.slot.examiner_id, "examiner": x.slot.examiner.full_name,
            "status": x.status, "created_at": json_date(x.created_at)}


def apply_exam_data(exam, data, creating=False):
    allowed = {"course_id", "title", "type", "description", "duration_minutes", "max_marks",
               "slot_creation_start", "slot_creation_end", "booking_start", "booking_end", "status", "examiner_id"}
    unknown = set(data) - allowed
    if unknown:
        raise ValueError(f"Unknown fields: {', '.join(sorted(unknown))}")
    for key in ("course_id", "title", "type", "duration_minutes", "max_marks"):
        if creating and key not in data:
            raise ValueError(f"{key} is required")
    course_id = data.get("course_id", exam.course_id)
    course = db.session.get(Course, course_id)
    if not course or course.status != "active":
        raise ValueError("course_id must identify an active course")
    exam.course_id = course_id
    if "title" in data:
        if not isinstance(data["title"], str) or not 2 <= len(data["title"].strip()) <= 180:
            raise ValueError("title must be between 2 and 180 characters")
        exam.title = data["title"].strip()
    if "type" in data:
        if data["type"] not in EXAM_TYPES:
            raise ValueError("type must be Viva, Practical, Project Demo, or Assessment")
        exam.exam_type = data["type"]
    if "description" in data:
        if data["description"] is not None and not isinstance(data["description"], str):
            raise ValueError("description must be text")
        exam.description = data["description"].strip() if data["description"] and data["description"].strip() else current_app.config.get("DEFAULT_EXAM_INSTRUCTIONS", "")
    elif creating:
        exam.description = current_app.config.get("DEFAULT_EXAM_INSTRUCTIONS", "")
    if "duration_minutes" in data:
        if not isinstance(data["duration_minutes"], int) or not 1 <= data["duration_minutes"] <= 1440:
            raise ValueError("duration_minutes must be between 1 and 1440")
        exam.duration_minutes = data["duration_minutes"]
    if "max_marks" in data:
        if not isinstance(data["max_marks"], (int, float)) or isinstance(data["max_marks"], bool) or not 0 < data["max_marks"] <= 10000:
            raise ValueError("max_marks must be greater than 0 and at most 10000")
        exam.max_marks = float(data["max_marks"])
    for key in ("slot_creation_start", "slot_creation_end", "booking_start", "booking_end"):
        if key in data:
            setattr(exam, key, parse_date(data[key], key))
    if "examiner_id" in data:
        examiner_id = data["examiner_id"]
        examiner = db.session.get(User, examiner_id) if examiner_id else None
        if examiner_id and (not examiner or examiner.role != "examiner" or not examiner.is_active):
            raise ValueError("examiner_id must identify an active examiner")
        exam.examiner_id = examiner_id
    if "status" in data:
        target = data["status"]
        if target not in EXAM_STATUSES:
            raise ValueError("Invalid examination status")
        transitions = {"draft": {"draft", "slot_creation"}, "slot_creation": {"slot_creation", "booking_open", "closed"},
                       "booking_open": {"booking_open", "closed"}, "closed": {"closed", "completed"}, "completed": {"completed"}}
        if target not in transitions.get(exam.status, set()):
            raise ValueError(f"Cannot transition examination from {exam.status} to {target}")
        if target in {"slot_creation", "booking_open"}:
            for k in ("slot_creation_start", "slot_creation_end", "booking_start", "booking_end"):
                if not getattr(exam, k):
                    raise ValueError(f"{k} must be configured before opening this examination")
            if not (exam.slot_creation_start < exam.slot_creation_end <= exam.booking_start < exam.booking_end):
                raise ValueError("Dates must follow slot creation start < end <= booking start < end")
        if target == "booking_open" and exam.status == "draft":
            raise ValueError("Open slot creation before booking")
        exam.status = target
    if exam.slot_creation_start and exam.slot_creation_end and exam.slot_creation_start >= exam.slot_creation_end:
        raise ValueError("slot_creation_start must be before slot_creation_end")
    if exam.booking_start and exam.booking_end and exam.booking_start >= exam.booking_end:
        raise ValueError("booking_start must be before booking_end")
    if exam.rubric_criteria and sum(c.max_score for c in exam.rubric_criteria) > exam.max_marks + .0001:
        raise ValueError("Rubric maximum scores cannot exceed examination maximum marks")


@api.get("/admin/courses")
@admin
def list_courses():
    q = Course.query
    search = request.args.get("search", "").strip()
    status = request.args.get("status")
    if search:
        q = q.filter(or_(Course.code.ilike(f"%{search}%"), Course.name.ilike(f"%{search}%")))
    if status and status not in {"active", "archived"}:
        return error("Invalid course status filter", 400)
    if status in {"active", "archived"}:
        q = q.filter_by(status=status)
    return paged(q.order_by(Course.code), course_json)


@api.post("/admin/courses")
@admin
def create_course():
    data = body()
    if data is None:
        return error("A JSON object is required", 400)
    code, name = data.get("code"), data.get("name")
    if not isinstance(code, str) or not 2 <= len(code.strip()) <= 30 or not isinstance(name, str) or not 2 <= len(name.strip()) <= 160:
        return error("code and name are required and must be valid", 400)
    course = Course(code=code.strip().upper(), name=name.strip(), description=data.get("description"))
    db.session.add(course)
    try:
        db.session.flush()
        audit("course.created", "course", course.id, {"code": course.code})
        db.session.commit()
        invalidate_cache("examinations")
    except IntegrityError:
        db.session.rollback()
        return error("Course code already exists", 409)
    return jsonify({"item": course_json(course)}), 201


@api.route("/admin/courses/<int:course_id>", methods=["GET", "PATCH", "DELETE"])
@admin
def course_detail(course_id):
    course = db.session.get(Course, course_id)
    if not course:
        return error("Course not found", 404)
    if request.method == "GET":
        return jsonify({"item": course_json(course)})
    if request.method == "DELETE":
        if course.examinations:
            return error("Course has examinations; archive it instead of deleting", 409)
        db.session.delete(course)
        audit("course.deleted", "course", course_id)
        db.session.commit()
        invalidate_cache("examinations")
        return "", 204
    data = body()
    if data is None:
        return error("A JSON object is required", 400)
    unknown = set(data) - {"code", "name", "description", "status"}
    if unknown:
        return error("Unknown course fields", 400)
    if "code" in data:
        if not isinstance(data["code"], str) or not 2 <= len(data["code"].strip()) <= 30:
            return error("Invalid course code", 400)
        course.code = data["code"].strip().upper()
    if "name" in data:
        if not isinstance(data["name"], str) or not 2 <= len(data["name"].strip()) <= 160:
            return error("Invalid course name", 400)
        course.name = data["name"].strip()
    if "description" in data:
        course.description = data["description"]
    if "status" in data:
        if data["status"] not in {"active", "archived"}:
            return error("status must be active or archived", 400)
        course.status = data["status"]
    try:
        audit("course.updated", "course", course.id, {"fields": sorted(data)})
        db.session.commit()
        invalidate_cache("examinations")
    except IntegrityError:
        db.session.rollback()
        return error("Course code already exists", 409)
    return jsonify({"item": course_json(course)})


@api.get("/admin/examinations")
@admin
@cached_response("examinations")
def list_examinations():
    q = Examination.query.join(Course)
    search = request.args.get("search", "").strip()
    status, exam_type, course_id = request.args.get("status"), request.args.get("type"), request.args.get("course_id", type=int)
    if search:
        q = q.filter(or_(Examination.title.ilike(f"%{search}%"), Course.code.ilike(f"%{search}%")))
    if status:
        if status not in EXAM_STATUSES:
            return error("Invalid status filter", 400)
        q = q.filter_by(status=status)
    if exam_type:
        if exam_type not in EXAM_TYPES:
            return error("Invalid type filter", 400)
        q = q.filter_by(exam_type=exam_type)
    if course_id:
        q = q.filter_by(course_id=course_id)
    return paged(q.order_by(Examination.created_at.desc()), exam_json)


@api.post("/admin/examinations")
@admin
def create_examination():
    data = body()
    if data is None:
        return error("A JSON object is required", 400)
    exam = Examination(title="Untitled", course_id=data.get("course_id") or 0, status="draft")
    try:
        apply_exam_data(exam, data, True)
        db.session.add(exam)
        db.session.flush()
        audit("examination.created", "examination", exam.id, {"title": exam.title})
        recipients, message = notify_exam_update(exam, newly_available=True) if exam.status == "booking_open" else ([], "")
        db.session.commit()
        if recipients:
            email_users(recipients, f"Examination available: {exam.title}", message)
        invalidate_cache("examinations", "schedule", "rubrics")
        return jsonify({"item": exam_json(exam)}), 201
    except ValueError as exc:
        db.session.rollback()
        return error(str(exc), 400)


@api.route("/admin/examinations/<int:exam_id>", methods=["GET", "PATCH"])
@admin
def examination_detail(exam_id):
    exam = db.session.get(Examination, exam_id)
    if not exam:
        return error("Examination not found", 404)
    if request.method == "GET":
        return jsonify({"item": exam_json(exam), "rubric": [rubric_json(r) for r in exam.rubric_criteria],
                        "slots": [slot_json(s) for s in exam.slots],
                        "bookings": [booking_json(b) for s in exam.slots for b in s.bookings]})
    data = body()
    if data is None:
        return error("A JSON object is required", 400)
    try:
        was_available = exam.status == "booking_open"
        apply_exam_data(exam, data)
        audit("examination.updated", "examination", exam.id, {"fields": sorted(data)})
        newly_available = not was_available and exam.status == "booking_open"
        recipients, message = notify_exam_update(exam, newly_available=newly_available) if newly_available or was_available else ([], "")
        db.session.commit()
        if recipients:
            email_users(recipients, f"Examination {'available' if newly_available else 'updated'}: {exam.title}", message)
        invalidate_cache("examinations", "schedule", "rubrics")
        return jsonify({"item": exam_json(exam)})
    except ValueError as exc:
        db.session.rollback()
        return error(str(exc), 400)


@api.post("/admin/examinations/<int:exam_id>/lifecycle")
@admin
def examination_lifecycle(exam_id):
    exam = db.session.get(Examination, exam_id)
    data = body()
    if not exam:
        return error("Examination not found", 404)
    if not data or set(data) != {"status"}:
        return error("A status is required", 400)
    try:
        was_available = exam.status == "booking_open"
        apply_exam_data(exam, {"status": data["status"]})
        audit("examination.status_changed", "examination", exam.id, {"status": exam.status})
        newly_available = not was_available and exam.status == "booking_open"
        status_changed = exam.status != ("booking_open" if was_available else None)
        recipients, message = notify_exam_update(exam, newly_available=newly_available) if newly_available or (was_available and status_changed) else ([], "")
        db.session.commit()
        if recipients:
            email_users(recipients, f"Examination {'available' if newly_available else 'updated'}: {exam.title}", message)
        invalidate_cache("examinations", "schedule", "rubrics")
    except ValueError as exc:
        db.session.rollback()
        return error(str(exc), 400)
    return jsonify({"item": exam_json(exam)})


def rubric_json(c):
    return {"id": c.id, "examination_id": c.examination_id, "name": c.name,
            "description": c.description, "max_score": c.max_score, "weightage": c.weightage}


@api.get("/admin/examinations/<int:exam_id>/rubric")
@admin
@cached_response("rubrics")
def list_rubric(exam_id):
    exam = db.session.get(Examination, exam_id)
    if not exam:
        return error("Examination not found", 404)
    items = [rubric_json(c) for c in exam.rubric_criteria]
    return jsonify({"items": items, "total_max_score": sum(c.max_score for c in exam.rubric_criteria),
                    "total_weightage": sum(c.weightage for c in exam.rubric_criteria), "exam_max_marks": exam.max_marks})


@api.post("/admin/examinations/<int:exam_id>/rubric")
@admin
def create_rubric(exam_id):
    exam = db.session.get(Examination, exam_id)
    data = body()
    if not exam:
        return error("Examination not found", 404)
    if not data:
        return error("A JSON object is required", 400)
    try:
        name = data.get("name")
        maximum, weight = data.get("max_score"), data.get("weightage")
        if not isinstance(name, str) or not name.strip() or not isinstance(maximum, (int, float)) or maximum <= 0:
            raise ValueError("name and positive max_score are required")
        if not isinstance(weight, (int, float)) or not 0 <= weight <= 100:
            raise ValueError("weightage must be between 0 and 100")
        if Evaluation.query.join(Booking).join(ExaminationSlot).filter(ExaminationSlot.examination_id == exam_id).count():
            raise ValueError("Rubric is locked after evaluations exist to preserve historical scores")
        if sum(c.max_score for c in exam.rubric_criteria) + maximum > exam.max_marks + .0001:
            raise ValueError("Rubric maximum scores cannot exceed examination maximum marks")
        if sum(c.weightage for c in exam.rubric_criteria) + weight > 100.0001:
            raise ValueError("Total rubric weightage cannot exceed 100")
        criterion = RubricCriterion(examination_id=exam_id, name=name.strip(), description=data.get("description"), max_score=float(maximum), weightage=float(weight))
        db.session.add(criterion)
        db.session.flush()
        audit("rubric.created", "rubric_criterion", criterion.id, {"examination_id": exam_id})
        db.session.commit()
        invalidate_cache("rubrics")
        return jsonify({"item": rubric_json(criterion)}), 201
    except ValueError as exc:
        db.session.rollback()
        return error(str(exc), 400)


@api.route("/admin/rubric/<int:criterion_id>", methods=["PATCH", "DELETE"])
@admin
def edit_rubric(criterion_id):
    c = db.session.get(RubricCriterion, criterion_id)
    if not c:
        return error("Rubric criterion not found", 404)
    has_eval = EvaluationCriterionScore.query.filter_by(criterion_id=c.id).count() > 0
    if has_eval:
        return error("Rubric is locked after evaluation scores exist to preserve history", 409)
    if request.method == "DELETE":
        db.session.delete(c)
        audit("rubric.deleted", "rubric_criterion", criterion_id)
        db.session.commit()
        invalidate_cache("rubrics")
        return "", 204
    data = body()
    if not data:
        return error("A JSON object is required", 400)
    name = data.get("name", c.name)
    maximum = data.get("max_score", c.max_score)
    weight = data.get("weightage", c.weightage)
    if not isinstance(name, str) or not name.strip() or not isinstance(maximum, (int, float)) or maximum <= 0 or not isinstance(weight, (int, float)) or not 0 <= weight <= 100:
        return error("Invalid rubric values", 400)
    siblings = [r for r in c.examination.rubric_criteria if r.id != c.id]
    if sum(r.max_score for r in siblings) + maximum > c.examination.max_marks + .0001 or sum(r.weightage for r in siblings) + weight > 100.0001:
        return error("Rubric totals exceed examination limits", 400)
    c.name, c.description, c.max_score, c.weightage = name.strip(), data.get("description", c.description), float(maximum), float(weight)
    audit("rubric.updated", "rubric_criterion", c.id, {"fields": sorted(data)})
    db.session.commit()
    invalidate_cache("rubrics")
    return jsonify({"item": rubric_json(c)})


@api.get("/admin/examiners")
@admin
def list_examiners():
    q = User.query.filter_by(role="examiner")
    search = request.args.get("search", "").strip()
    status = request.args.get("status")
    if search:
        q = q.filter(or_(User.full_name.ilike(f"%{search}%"), User.email.ilike(f"%{search}%"), ExaminerProfile.department.ilike(f"%{search}%"))).outerjoin(ExaminerProfile)
    if status and status not in {"active", "inactive"}:
        return error("Invalid account status filter", 400)
    if status in {"active", "inactive"}:
        q = q.filter_by(is_active=(status == "active"))
    return paged(q.order_by(User.full_name), user_json)


@api.post("/admin/examiners")
@admin
def create_examiner():
    data = body()
    if not data:
        return error("A JSON object is required", 400)
    name, email = data.get("full_name"), data.get("email")
    try:
        if not isinstance(name, str) or not 2 <= len(name.strip()) <= 120:
            raise ValueError("full_name must be between 2 and 120 characters")
        email = validate_email(email, check_deliverability=False).normalized
        password = data.get("password") or "".join(secrets.choice(string.ascii_letters + string.digits + "!@#$%") for _ in range(20))
        if not isinstance(password, str) or len(password) < 12 or len(password) > 128:
            raise ValueError("password must be 12–128 characters")
        user = User(full_name=name.strip(), email=email, role="examiner")
        user.set_password(password)
        user.examiner_profile = ExaminerProfile(department=data.get("department"))
        db.session.add(user)
        db.session.flush()
        audit("examiner.created", "user", user.id, {"email": email})
        db.session.commit()
        # Returned once to the authenticated Admin; never persisted in audit metadata.
        return jsonify({"item": user_json(user), "initial_password": password}), 201
    except EmailNotValidError:
        db.session.rollback()
        return error("A valid email address is required", 400)
    except ValueError as exc:
        db.session.rollback()
        return error(str(exc), 400)
    except IntegrityError:
        db.session.rollback()
        return error("An account with this email already exists", 409)


@api.get("/admin/examiners/<int:user_id>")
@admin
def examiner_detail(user_id):
    user = db.session.get(User, user_id)
    if not user or user.role != "examiner":
        return error("Examiner not found", 404)
    assignments = Examination.query.filter_by(examiner_id=user.id).count()
    slots = ExaminationSlot.query.filter_by(examiner_id=user.id).count()
    bookings = Booking.query.join(ExaminationSlot).filter(ExaminationSlot.examiner_id == user.id).count()
    evaluations = Evaluation.query.filter_by(examiner_id=user.id).count()
    return jsonify({"item": user_json(user), "workload": {"examinations": assignments, "slots": slots, "bookings": bookings, "evaluations": evaluations}})


@api.patch("/admin/examiners/<int:user_id>")
@admin
def update_examiner(user_id):
    user = db.session.get(User, user_id)
    data = body()
    if not user or user.role != "examiner":
        return error("Examiner not found", 404)
    if not data:
        return error("A JSON object is required", 400)
    if "full_name" in data:
        if not isinstance(data["full_name"], str) or not data["full_name"].strip():
            return error("Invalid full_name", 400)
        user.full_name = data["full_name"].strip()
    if "department" in data:
        user.examiner_profile.department = data["department"]
    if "is_active" in data:
        if not isinstance(data["is_active"], bool):
            return error("is_active must be a boolean", 400)
        user.is_active = data["is_active"]
    audit("examiner.updated", "user", user.id, {"fields": sorted(data)})
    db.session.commit()
    return jsonify({"item": user_json(user)})


@api.get("/admin/students")
@admin
def list_students():
    q = User.query.filter_by(role="student")
    search, status, department = request.args.get("search", "").strip(), request.args.get("status"), request.args.get("department")
    if search:
        q = q.filter(or_(User.full_name.ilike(f"%{search}%"), User.email.ilike(f"%{search}%"), StudentProfile.student_number.ilike(f"%{search}%"))).outerjoin(StudentProfile)
    if status and status not in {"active", "inactive"}:
        return error("Invalid account status filter", 400)
    if status in {"active", "inactive"}:
        q = q.filter_by(is_active=(status == "active"))
    if department:
        q = q.join(StudentProfile).filter(StudentProfile.department == department)
    return paged(q.order_by(User.full_name), user_json)


@api.get("/admin/students/<int:user_id>")
@admin
def student_detail(user_id):
    user = db.session.get(User, user_id)
    if not user or user.role != "student":
        return error("Student not found", 404)
    bookings = Booking.query.filter_by(student_id=user.id).order_by(Booking.created_at.desc()).all()
    return jsonify({"item": user_json(user), "bookings": [booking_json(b) for b in bookings],
                    "evaluations": [{"booking_id": b.id, "status": b.evaluation.status, "score": b.evaluation.total_score}
                                    for b in bookings if b.evaluation],
                    "published_results": [{"booking_id": b.id, "score": b.evaluation.total_score}
                                           for b in bookings if b.evaluation and b.evaluation.status == "published"]})


@api.patch("/admin/students/<int:user_id>")
@admin
def update_student(user_id):
    user = db.session.get(User, user_id)
    data = body()
    if not user or user.role != "student":
        return error("Student not found", 404)
    if not data or not isinstance(data.get("is_active"), bool):
        return error("is_active boolean is required", 400)
    user.is_active = data["is_active"]
    audit("student.status_changed", "user", user.id, {"is_active": user.is_active})
    db.session.commit()
    return jsonify({"item": user_json(user)})


@api.delete("/admin/students/<int:user_id>")
@admin
def delete_student(user_id):
    user = db.session.get(User, user_id)
    if not user or user.role != "student":
        return error("Student not found", 404)

    bookings = Booking.query.filter_by(student_id=user.id).all()
    booking_ids = [booking.id for booking in bookings]
    released_seats = 0
    for booking in bookings:
        if booking.status in {"booked", "completed"}:
            booking.slot.reserved_count = max(0, booking.slot.reserved_count - 1)
            released_seats += 1

    submissions = Submission.query.filter_by(student_id=user.id).all()
    submission_files = [submission.stored_filename for submission in submissions]
    exports = ExportJob.query.filter_by(user_id=user.id).all()
    export_files = [job.artifact_name for job in exports if job.artifact_name]
    export_count = len(exports)
    submission_count = len(submissions)
    booking_count = len(bookings)

    try:
        if booking_ids:
            evaluation_ids = [row[0] for row in db.session.query(Evaluation.id).filter(Evaluation.booking_id.in_(booking_ids)).all()]
            if evaluation_ids:
                EvaluationCriterionScore.query.filter(EvaluationCriterionScore.evaluation_id.in_(evaluation_ids)).delete(synchronize_session=False)
            Evaluation.query.filter(Evaluation.booking_id.in_(booking_ids)).delete(synchronize_session=False)
            ReminderLog.query.filter(ReminderLog.booking_id.in_(booking_ids)).delete(synchronize_session=False)
            Submission.query.filter(Submission.booking_id.in_(booking_ids)).delete(synchronize_session=False)
            Booking.query.filter(Booking.id.in_(booking_ids)).delete(synchronize_session=False)
        Submission.query.filter_by(student_id=user.id).delete(synchronize_session=False)
        ExportJob.query.filter_by(user_id=user.id).delete(synchronize_session=False)
        Notification.query.filter_by(user_id=user.id).delete(synchronize_session=False)
        StudentProfile.query.filter_by(user_id=user.id).delete(synchronize_session=False)
        AuditLog.query.filter_by(actor_id=user.id).update({AuditLog.actor_id: None}, synchronize_session=False)
        audit("student.deleted", "user", user.id, {
            "bookings_deleted": booking_count,
            "submissions_deleted": submission_count,
            "exports_deleted": export_count,
            "reserved_seats_released": released_seats,
        })
        db.session.delete(user)
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return error("Could not delete this student because related records are still in use", 409)

    def remove_private_files(directory, names):
        root = Path(directory).resolve()
        removed = 0
        for name in names:
            candidate = (root / Path(name).name).resolve()
            if candidate.parent == root and candidate.is_file():
                try:
                    candidate.unlink()
                    removed += 1
                except OSError as exc:
                    current_app.logger.warning("Student private file cleanup failed (%s)", type(exc).__name__)
        return removed

    remove_private_files(current_app.config["SUBMISSION_DIR"], submission_files)
    remove_private_files(current_app.config["EXPORT_DIR"], export_files)
    invalidate_cache("schedule", "examinations")
    return jsonify({"message": "Student and related records deleted", "bookings_deleted": booking_count,
        "submissions_deleted": submission_count, "exports_deleted": export_count,
        "reserved_seats_released": released_seats})


@api.get("/admin/slots")
@admin
@cached_response("schedule")
def list_slots():
    q = ExaminationSlot.query.join(Examination).join(User, ExaminationSlot.examiner_id == User.id)
    search = request.args.get("search", "").strip()
    if search:
        q = q.filter(or_(Examination.title.ilike(f"%{search}%"), User.full_name.ilike(f"%{search}%")))
    if request.args.get("examination_id", type=int):
        q = q.filter_by(examination_id=request.args.get("examination_id", type=int))
    if request.args.get("examiner_id", type=int):
        q = q.filter_by(examiner_id=request.args.get("examiner_id", type=int))
    if request.args.get("status"):
        q = q.filter_by(status=request.args.get("status"))
    for key, operator in (("date_from", "ge"), ("date_to", "le")):
        if request.args.get(key):
            try:
                value = parse_date(request.args[key], key, True)
            except ValueError as exc:
                return error(str(exc), 400)
            q = q.filter(ExaminationSlot.starts_at >= value if operator == "ge" else ExaminationSlot.starts_at <= value)
    return paged(q.order_by(ExaminationSlot.starts_at), slot_json)


@api.get("/admin/slots/<int:slot_id>")
@admin
def slot_detail(slot_id):
    slot = db.session.get(ExaminationSlot, slot_id)
    if not slot:
        return error("Slot not found", 404)
    return jsonify({"item": slot_json(slot), "bookings": [booking_json(b) for b in slot.bookings]})


@api.post("/admin/slots/<int:slot_id>/reschedule")
@admin
def reschedule_slot(slot_id):
    slot = db.session.get(ExaminationSlot, slot_id)
    data = body()
    if not slot:
        return error("Slot not found", 404)
    try:
        starts, ends = parse_date(data.get("starts_at"), "starts_at", True), parse_date(data.get("ends_at"), "ends_at", True)
        if starts >= ends:
            raise ValueError("starts_at must be before ends_at")
        old = {"starts_at": json_date(slot.starts_at), "ends_at": json_date(slot.ends_at)}
        slot.starts_at, slot.ends_at = starts, ends
        affected = []
        for booking in slot.bookings:
            if booking.status == "booked":
                affected.append(booking.student)
        notify_users(affected, f"Your {slot.examination.title} slot has been rescheduled to {starts.isoformat()} by an administrator.")
        audit("slot.rescheduled", "examination_slot", slot.id, {"before": old, "after": {"starts_at": json_date(starts), "ends_at": json_date(ends)}})
        db.session.commit()
        email_users(affected, f"Examination slot changed: {slot.examination.title}",
            f"An administrator rescheduled your {slot.examination.title} slot. New time: {starts.isoformat()} to {ends.isoformat()}.")
        invalidate_cache("schedule")
        return jsonify({"item": slot_json(slot)})
    except (ValueError, AttributeError) as exc:
        db.session.rollback()
        return error(str(exc), 400)


@api.post("/admin/slots/<int:slot_id>/reassign")
@admin
def reassign_slot(slot_id):
    slot = db.session.get(ExaminationSlot, slot_id)
    data = body()
    examiner = db.session.get(User, data.get("examiner_id")) if data else None
    if not slot:
        return error("Slot not found", 404)
    if not examiner or examiner.role != "examiner" or not examiner.is_active:
        return error("examiner_id must identify an active examiner", 400)
    before = slot.examiner_id
    old_examiner = slot.examiner
    slot.examiner_id = examiner.id
    affected = []
    for booking in slot.bookings:
        if booking.evaluation:
            booking.evaluation.examiner_id = examiner.id
        if booking.status == "booked":
            affected.append(booking.student)
    notify_users(affected, f"The examiner for {slot.examination.title} has changed to {examiner.full_name}.")
    audit("slot.reassigned", "examination_slot", slot.id, {"before_examiner_id": before, "after_examiner_id": examiner.id})
    db.session.commit()
    email_users(affected, f"Examiner updated: {slot.examination.title}",
        f"The examiner for your {slot.examination.title} slot has changed to {examiner.full_name}.")
    if old_examiner and old_examiner.id != examiner.id:
        email_users([old_examiner], f"Slot reassigned: {slot.examination.title}",
            f"An administrator reassigned a {slot.examination.title} slot to {examiner.full_name}.")
    email_users([examiner], f"New slot assignment: {slot.examination.title}",
        f"An administrator assigned you a {slot.examination.title} slot on {slot.starts_at.isoformat()}.")
    invalidate_cache("schedule")
    return jsonify({"item": slot_json(slot)})


@api.patch("/admin/slots/<int:slot_id>")
@admin
def update_slot(slot_id):
    slot = db.session.get(ExaminationSlot, slot_id)
    data = body()
    if not slot:
        return error("Slot not found", 404)
    if not data:
        return error("A JSON object is required", 400)
    if "capacity" in data:
        capacity = data["capacity"]
        booked = slot.reserved_count
        if not isinstance(capacity, int) or capacity < booked or capacity < 1:
            return error("capacity must be a positive integer at least equal to existing reservations", 409)
        slot.capacity = capacity
    if "status" in data:
        if data["status"] not in {"open", "closed", "cancelled", "completed"}:
            return error("Invalid slot status", 400)
        if data["status"] == "cancelled" and any(b.status == "booked" for b in slot.bookings):
            return error("Reschedule or cancel active bookings before cancelling this slot", 409)
        slot.status = data["status"]
    affected = [booking.student for booking in slot.bookings if booking.status == "booked"]
    if affected:
        notify_users(affected, f"Your {slot.examination.title} slot details have been updated by an administrator.")
    audit("slot.updated", "examination_slot", slot.id, {"fields": sorted(data)})
    db.session.commit()
    if affected:
        email_users(affected, f"Examination slot updated: {slot.examination.title}",
            f"An administrator updated the details for your {slot.examination.title} slot. Sign in to review the latest schedule.")
    invalidate_cache("schedule")
    return jsonify({"item": slot_json(slot)})


@api.get("/admin/bookings")
@admin
def list_bookings():
    q = Booking.query.join(User, Booking.student_id == User.id).join(ExaminationSlot, Booking.slot_id == ExaminationSlot.id).join(Examination, ExaminationSlot.examination_id == Examination.id)
    search = request.args.get("search", "").strip()
    if search:
        q = q.filter(or_(User.full_name.ilike(f"%{search}%"), User.email.ilike(f"%{search}%"), Examination.title.ilike(f"%{search}%")))
    if request.args.get("status"):
        if request.args["status"] not in BOOKING_STATUSES:
            return error("Invalid booking status", 400)
        q = q.filter_by(status=request.args["status"])
    if request.args.get("examination_id", type=int):
        q = q.filter(ExaminationSlot.examination_id == request.args.get("examination_id", type=int))
    if request.args.get("examiner_id", type=int):
        q = q.filter(ExaminationSlot.examiner_id == request.args.get("examiner_id", type=int))
    if request.args.get("student_id", type=int):
        q = q.filter(Booking.student_id == request.args.get("student_id", type=int))
    if request.args.get("slot_id", type=int):
        q = q.filter(Booking.slot_id == request.args.get("slot_id", type=int))
    return paged(q.order_by(Booking.created_at.desc()), booking_json)


@api.get("/admin/bookings/<int:booking_id>")
@admin
def booking_detail(booking_id):
    b = db.session.get(Booking, booking_id)
    if not b:
        return error("Booking not found", 404)
    return jsonify({"item": booking_json(b), "evaluation": {"status": b.evaluation.status, "score": b.evaluation.total_score} if b.evaluation else None})


@api.patch("/admin/bookings/<int:booking_id>")
@admin
def update_booking(booking_id):
    b = db.session.get(Booking, booking_id)
    data = body()
    if not b:
        return error("Booking not found", 404)
    if not data:
        return error("A JSON object is required", 400)
    original_slot = b.slot
    affected_examiners = [original_slot.examiner] if original_slot.examiner else []
    if "status" in data:
        old_status, new_status = b.status, data["status"]
        if new_status not in BOOKING_STATUSES:
            return error("Invalid booking status", 400)
        if old_status == "completed" and new_status != "completed":
            return error("Completed bookings cannot be reopened", 409)
        if old_status != "booked" and new_status == "booked":
            active = Booking.query.join(ExaminationSlot).filter(Booking.student_id == b.student_id,
                ExaminationSlot.examination_id == b.slot.examination_id, Booking.status == "booked", Booking.id != b.id).first()
            if active:
                return error("Student already has an active booking for this examination", 409)
            if b.slot.status != "open" or b.slot.reserved_count >= b.slot.capacity:
                return error("Slot has no available capacity", 409)
            b.slot.reserved_count += 1
        elif old_status == "booked" and new_status == "cancelled":
            b.slot.reserved_count = max(0, b.slot.reserved_count-1)
        b.status = new_status
    if "slot_id" in data:
        if b.status != "booked":
            return error("Only active bookings can be rescheduled", 409)
        slot = db.session.get(ExaminationSlot, data["slot_id"])
        if not slot or slot.examination_id != b.slot.examination_id or slot.status != "open":
            return error("Target must be an open slot for the same examination", 400)
        if sum(x.status == "booked" for x in slot.bookings) >= slot.capacity:
            return error("Target slot is full", 409)
        active = Booking.query.join(ExaminationSlot).filter(Booking.student_id == b.student_id,
                ExaminationSlot.examination_id == slot.examination_id, Booking.status == "booked", Booking.id != b.id).first()
        if active:
            return error("Student already has an active booking for this examination", 409)
        old_slot = b.slot
        if old_slot.examiner and old_slot.examiner not in affected_examiners:
            affected_examiners.append(old_slot.examiner)
        b.slot_id = slot.id
        if slot.examiner and slot.examiner not in affected_examiners:
            affected_examiners.append(slot.examiner)
        old_slot.reserved_count = max(0, old_slot.reserved_count-1)
        slot.reserved_count += 1
        db.session.add(Notification(user_id=b.student_id, message=f"Your {slot.examination.title} booking has been moved to {slot.starts_at.isoformat()} by an administrator."))
    audit("booking.updated", "booking", b.id, {"fields": sorted(data)})
    if "status" in data and b.status in {"cancelled", "completed"}:
        db.session.add(Notification(user_id=b.student_id, message=f"Your {b.slot.examination.title} booking status is now {b.status}."))
    booking_student = b.student
    booking_exam = b.slot.examination
    if "slot_id" in data or "status" in data:
        notify_users(affected_examiners, f"A booking for {booking_exam.title} was updated by an administrator.")
    db.session.commit()
    if "slot_id" in data or "status" in data:
        email_users([booking_student], f"Booking updated: {booking_exam.title}",
            f"An administrator updated your {booking_exam.title} booking. Current status: {b.status}. Current slot: {b.slot.starts_at.isoformat()}.")
        email_users(affected_examiners, f"Booking updated: {booking_exam.title}",
            f"An administrator updated a booking for {booking_exam.title}. Current booking status: {b.status}.")
    invalidate_cache("schedule")
    return jsonify({"item": booking_json(b)})


@api.get("/admin/reports/summary")
@admin
def report_summary():
    booking_query = Booking.query
    date_from, date_to = request.args.get("date_from"), request.args.get("date_to")
    try:
        if date_from:
            booking_query = booking_query.filter(Booking.created_at >= parse_date(date_from, "date_from", True))
        if date_to:
            booking_query = booking_query.filter(Booking.created_at <= parse_date(date_to, "date_to", True))
    except ValueError as exc:
        return error(str(exc), 400)
    completed_bookings = booking_query.filter_by(status="completed").count()
    completed_exams = Examination.query.filter_by(status="completed").count()
    students_evaluated = db.session.query(func.count(func.distinct(Booking.student_id))).join(Evaluation, Evaluation.booking_id == Booking.id).filter(Evaluation.status.in_(["submitted", "published"])).scalar() or 0
    pending = booking_query.filter(Booking.status == "completed").outerjoin(Evaluation).filter(or_(Evaluation.id.is_(None), Evaluation.status == "draft")).count()
    from sqlalchemy import extract
    trend = booking_query.with_entities(func.strftime("%Y-%m", Booking.created_at), func.count(Booking.id)).group_by(func.strftime("%Y-%m", Booking.created_at)).order_by(func.strftime("%Y-%m", Booking.created_at)).all()
    workload = db.session.query(User.id, User.full_name, func.count(ExaminationSlot.id)).join(ExaminationSlot, ExaminationSlot.examiner_id == User.id, isouter=True).filter(User.role == "examiner").group_by(User.id, User.full_name).order_by(User.full_name).all()
    exam_stats = db.session.query(Examination.status, func.count(Examination.id)).group_by(Examination.status).all()
    return jsonify({"totals": {"courses": Course.query.count(), "examinations": Examination.query.count(),
        "students": User.query.filter_by(role="student").count(), "examiners": User.query.filter_by(role="examiner").count(),
        "slots": ExaminationSlot.query.count(), "bookings": booking_query.count(), "completed_examinations": completed_exams,
        "students_evaluated": students_evaluated, "completed_bookings": completed_bookings, "pending_evaluations": pending},
        "booking_trend": [{"month": month, "bookings": count} for month, count in trend],
        "examiner_workload": [{"examiner_id": uid, "name": name, "slots": count} for uid, name, count in workload],
        "examination_completion": [{"status": status, "count": count} for status, count in exam_stats]})


@api.get("/admin/audit")
@admin
def audit_history():
    q = AuditLog.query
    entity = request.args.get("entity")
    if entity:
        q = q.filter_by(entity_type=entity)
    p = pagination(q.order_by(AuditLog.created_at.desc()))
    return jsonify({"items": [{"id": x.id, "actor": x.actor.full_name if x.actor else "System", "action": x.action,
        "entity_type": x.entity_type, "entity_id": x.entity_id, "metadata": json.loads(x.metadata_json or "{}"),
        "created_at": json_date(x.created_at)} for x in p.items],
        "pagination": {"page": p.page, "per_page": p.per_page, "total": p.total, "pages": p.pages}})


@api.get("/admin/settings")
@admin
def get_settings():
    user = current_user()
    return jsonify({"profile": user.to_dict(), "settings": {"application_name": "Examination Management Portal", "timezone": "Asia/Kolkata",
        "default_exam_instructions": current_app.config.get("DEFAULT_EXAM_INSTRUCTIONS", "")}})


@api.patch("/admin/settings")
@admin
def update_settings():
    user = current_user()
    data = body()
    if not data:
        return error("A JSON object is required", 400)
    allowed = {"full_name", "email", "current_password", "new_password", "confirm_password"}
    unknown = set(data) - allowed
    if unknown:
        return error(f"Unknown fields: {', '.join(sorted(unknown))}", 400)

    email = None
    if "email" in data:
        try:
            email = validate_email(data["email"], check_deliverability=False).normalized
        except (EmailNotValidError, TypeError):
            return error("A valid email address is required", 400)
        if User.query.filter(User.email == email, User.id != user.id).first():
            return error("An account with this email already exists", 409)

    has_password_input = bool(data.get("new_password") or data.get("confirm_password"))
    if "email" in data or has_password_input:
        current_password = data.get("current_password")
        if not isinstance(current_password, str) or not user.check_password(current_password):
            return error("Current password is incorrect", 400)
    if has_password_input:
        new_password = data.get("new_password")
        confirm_password = data.get("confirm_password")
        if not isinstance(new_password, str) or not 10 <= len(new_password) <= 128:
            return error("New password must be between 10 and 128 characters", 400)
        if not isinstance(confirm_password, str) or new_password != confirm_password:
            return error("New password and confirmation do not match", 400)

    name = data.get("full_name")
    if name is not None:
        if not isinstance(name, str) or not 2 <= len(name.strip()) <= 120:
            return error("Invalid full_name", 400)
        user.full_name = name.strip()
    if email is not None:
        user.email = email
    if has_password_input:
        user.set_password(new_password)
    audit("settings.updated", "user", user.id,
          {"fields": sorted(key for key in data if key not in {"current_password", "new_password", "confirm_password"}),
           "password_changed": has_password_input})
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return error("An account with this email already exists", 409)
    return jsonify({"profile": user.to_dict(), "settings": {"application_name": "Examination Management Portal", "timezone": "Asia/Kolkata"}})
