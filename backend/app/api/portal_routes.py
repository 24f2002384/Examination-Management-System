"""Authenticated Examiner and Student workflows, with ownership enforced here."""
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4
from email_validator import validate_email, EmailNotValidError
from sqlalchemy import func, or_, text
from sqlalchemy.exc import IntegrityError
from flask import current_app, jsonify, request, send_file
from werkzeug.utils import secure_filename

from ..extensions import db
from ..services.cache import cached_response, invalidate_cache
from ..services.notifications import email_users, notify_users
from ..models import (AuditLog, Booking, Course, Evaluation, EvaluationCriterionScore,
                      Examination, ExaminationSlot, Notification, RubricCriterion,
                      StudentProfile, Submission, User, now_utc)
from . import api
from .routes import current_user, error, login_required, student_exam_updates
from .admin_routes import json_date, exam_json, slot_json, booking_json, exam_instructions


def iso(value):
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed
    except ValueError:
        return None


def as_utc(value):
    if value is None:
        return None
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def notify(user_id, message):
    db.session.add(Notification(user_id=user_id, message=message))


def portal_audit(action, entity, entity_id, metadata=None):
    import json
    db.session.add(AuditLog(actor_id=current_user().id, action=action, entity_type=entity,
                            entity_id=entity_id, metadata_json=json.dumps(metadata or {}, sort_keys=True)))


def rubric_data(exam):
    return [{"id": c.id, "name": c.name, "description": c.description,
             "max_score": c.max_score, "weightage": c.weightage} for c in exam.rubric_criteria]


def student_safe_exam(exam):
    return {"id": exam.id, "course_id": exam.course_id, "course_code": exam.course.code,
            "course_name": exam.course.name, "title": exam.title, "type": exam.exam_type,
            "description": exam_instructions(exam), "duration_minutes": exam.duration_minutes,
            "max_marks": exam.max_marks, "slot_creation_start": json_date(exam.slot_creation_start),
            "slot_creation_end": json_date(exam.slot_creation_end), "booking_start": json_date(exam.booking_start),
            "booking_end": json_date(exam.booking_end), "status": exam.status}


def slot_for_student(slot):
    active = slot.reserved_count
    return {"id": slot.id, "examination_id": slot.examination_id, "examiner": slot.examiner.full_name,
            "starts_at": json_date(slot.starts_at), "ends_at": json_date(slot.ends_at),
            "capacity": slot.capacity, "booked_seats": active, "available_seats": max(0, slot.capacity-active),
            "status": "full" if active >= slot.capacity else "available" if slot.status == "open" else slot.status,
            "meeting_link": slot.meeting_link}


def submission_label(exam_type):
    return {"Project Demo": "Project demo report", "Assessment": "Assessment answer document",
            "Viva": "Viva report", "Practical": "Practical report"}.get(exam_type, "Examination work document")


def submission_json(submission):
    if not submission:
        return None
    return {"id": submission.id, "filename": submission.original_filename,
            "size_bytes": submission.size_bytes, "submitted_at": json_date(submission.submitted_at),
            "download_url": f"/student/bookings/{submission.booking_id}/submission/download"}


def submission_download(submission):
    path = Path(current_app.config["SUBMISSION_DIR"]) / submission.stored_filename
    if not path.is_file():
        return error("Submitted document is no longer available", 404)
    return send_file(path, as_attachment=True, download_name=submission.original_filename,
                     mimetype="application/octet-stream", conditional=True)


def paged(query, serialize):
    page = request.args.get("page", 1, type=int)
    per_page = request.args.get("per_page", 20, type=int)
    if not page or not per_page or page < 1 or per_page < 1:
        return error("page and per_page must be positive integers", 400)
    p = query.paginate(page=page, per_page=min(per_page, 100), error_out=False)
    return jsonify({"items": [serialize(x) for x in p.items], "pagination": {"page": p.page,
        "per_page": p.per_page, "total": p.total, "pages": p.pages}})


@api.get("/examiner/dashboard")
@login_required("examiner")
def examiner_dashboard():
    user = current_user()
    assigned = Examination.query.filter_by(examiner_id=user.id)
    slots = ExaminationSlot.query.filter_by(examiner_id=user.id)
    bookings = Booking.query.join(ExaminationSlot).filter(ExaminationSlot.examiner_id == user.id, Booking.status == "booked")
    pending = Evaluation.query.join(Booking).join(ExaminationSlot, Booking.slot_id == ExaminationSlot.id).filter(
        ExaminationSlot.examiner_id == user.id, Evaluation.status == "draft").count()
    upcoming = slots.filter(ExaminationSlot.starts_at >= now_utc(), ExaminationSlot.status != "cancelled").order_by(ExaminationSlot.starts_at).limit(8).all()
    return jsonify({"role": "examiner", "stats": {"assigned_examinations": assigned.count(),
        "slots": slots.count(), "booked_students": bookings.count(), "pending_evaluations": pending},
        "upcoming_schedule": [{**slot_for_student(s), "examination": s.examination.title} for s in upcoming],
        "recent_activity": []})


@api.get("/examiner/examinations")
@login_required("examiner")
@cached_response("examinations")
def examiner_examinations():
    q = Examination.query.filter_by(examiner_id=current_user().id).join(Course)
    search = request.args.get("search", "").strip()
    status = request.args.get("status")
    if search:
        q = q.filter(or_(Examination.title.ilike(f"%{search}%"), Course.code.ilike(f"%{search}%"), Course.name.ilike(f"%{search}%")))
    if status:
        q = q.filter_by(status=status)
    return paged(q.order_by(Examination.booking_start), exam_json)


@api.get("/examiner/examinations/<int:exam_id>")
@login_required("examiner")
def examiner_examination_detail(exam_id):
    exam = db.session.get(Examination, exam_id)
    if not exam or exam.examiner_id != current_user().id:
        return error("Examination not found", 404)
    slots = ExaminationSlot.query.filter_by(examination_id=exam.id, examiner_id=current_user().id).order_by(ExaminationSlot.starts_at).all()
    bookings = Booking.query.join(ExaminationSlot).filter(ExaminationSlot.examination_id == exam.id,
        ExaminationSlot.examiner_id == current_user().id).all()
    return jsonify({"item": exam_json(exam), "instructions": exam.description,
        "timeline": {"slot_creation_start": json_date(exam.slot_creation_start), "slot_creation_end": json_date(exam.slot_creation_end),
                     "booking_start": json_date(exam.booking_start), "booking_end": json_date(exam.booking_end)},
        "rubric": rubric_data(exam), "slots": [slot_for_student(s) for s in slots],
        "booked_students": [{"booking_id": b.id, "student": b.student.full_name,
                             "student_number": b.student.student_profile.student_number if b.student.student_profile else None,
                             "slot_id": b.slot_id, "starts_at": json_date(b.slot.starts_at),
                             "booking_status": b.status, "evaluation_status": b.evaluation.status if b.evaluation else "pending"} for b in bookings]})


@api.get("/examiner/slots")
@login_required("examiner")
@cached_response("schedule")
def examiner_slots():
    q = ExaminationSlot.query.filter_by(examiner_id=current_user().id).join(Examination)
    exam_id = request.args.get("examination_id", type=int)
    if exam_id:
        q = q.filter_by(examination_id=exam_id)
    if request.args.get("status") == "cancelled":
        q = q.filter_by(status="cancelled")
    return paged(q.order_by(ExaminationSlot.starts_at), lambda s: {**slot_for_student(s), "examination": s.examination.title})


@api.post("/examiner/slots")
@login_required("examiner")
def create_examiner_slot():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return error("A JSON object is required", 400)
    exam = db.session.get(Examination, data.get("examination_id"))
    if not exam or exam.examiner_id != current_user().id:
        return error("Examination is not assigned to this examiner", 403)
    now = now_utc()
    start_window, end_window = as_utc(exam.slot_creation_start), as_utc(exam.slot_creation_end)
    if exam.status != "slot_creation" or not start_window or not end_window or not start_window <= now <= end_window:
        return error("Slots can only be created during the configured slot-creation window", 409)
    starts, ends = iso(data.get("starts_at")), iso(data.get("ends_at"))
    capacity = data.get("capacity")
    if not starts or not ends or starts >= ends:
        return error("Valid starts_at and ends_at are required; start must precede end", 400)
    if starts < now:
        return error("Slot time must be in the future", 400)
    if not isinstance(capacity, int) or isinstance(capacity, bool) or not 1 <= capacity <= 100:
        return error("capacity must be an integer between 1 and 100", 400)
    if (ends-starts).total_seconds() > exam.duration_minutes * 60:
        return error("Slot duration cannot exceed examination duration", 400)
    conflict = ExaminationSlot.query.filter(ExaminationSlot.examiner_id == current_user().id,
        ExaminationSlot.status != "cancelled", ExaminationSlot.starts_at < ends, ExaminationSlot.ends_at > starts).first()
    if conflict:
        return error("This time overlaps another slot assigned to you", 409)
    slot = ExaminationSlot(examination_id=exam.id, examiner_id=current_user().id,
        starts_at=starts, ends_at=ends, capacity=capacity,
        meeting_link=(data.get("meeting_link") or "").strip() or None)
    db.session.add(slot)
    db.session.flush()
    portal_audit("slot.created", "examination_slot", slot.id, {"examination_id": exam.id})
    db.session.commit()
    invalidate_cache("schedule", "examinations")
    return jsonify({"item": slot_for_student(slot)}), 201


@api.route("/examiner/slots/<int:slot_id>", methods=["GET", "PATCH", "DELETE"])
@login_required("examiner")
def examiner_slot_detail(slot_id):
    slot = db.session.get(ExaminationSlot, slot_id)
    if not slot or slot.examiner_id != current_user().id:
        return error("Slot not found", 404)
    booked = Booking.query.filter_by(slot_id=slot.id, status="booked").count()
    if request.method == "GET":
        return jsonify({"item": {**slot_for_student(slot), "booked_students": [
            {"booking_id": b.id, "name": b.student.full_name, "email": b.student.email}
            for b in slot.bookings if b.status == "booked"]}})
    if request.method == "DELETE":
        if booked:
            return error("A slot with active bookings cannot be deleted. Cancel bookings first.", 409)
        if slot.status == "completed":
            return error("Completed slot history cannot be removed", 409)
        slot.status = "cancelled"
        portal_audit("slot.cancelled", "examination_slot", slot.id)
        db.session.commit()
        invalidate_cache("schedule")
        return jsonify({"item": slot_for_student(slot)})
    data = request.get_json(silent=True)
    if not isinstance(data, dict) or set(data) - {"capacity", "meeting_link"}:
        return error("Only capacity and meeting_link may be updated", 400)
    if "capacity" in data:
        cap = data["capacity"]
        if not isinstance(cap, int) or isinstance(cap, bool) or cap < max(1, booked) or cap > 100:
            return error("capacity must be between active bookings and 100", 409)
        slot.capacity = cap
    if "meeting_link" in data:
        slot.meeting_link = (data["meeting_link"] or "").strip() or None
    affected = [booking.student for booking in slot.bookings if booking.status == "booked"]
    if affected:
        notify_users(affected, f"Your {slot.examination.title} slot details were updated by the Examiner.")
    portal_audit("slot.updated", "examination_slot", slot.id, {"fields": sorted(data)})
    db.session.commit()
    if affected:
        email_users(affected, f"Examination slot updated: {slot.examination.title}",
            f"Your Examiner updated details for your {slot.examination.title} slot. Sign in to review the latest schedule.")
    invalidate_cache("schedule")
    return jsonify({"item": slot_for_student(slot)})


@api.get("/examiner/booked-students")
@login_required("examiner")
def examiner_booked_students():
    q = Booking.query.join(ExaminationSlot).join(Examination).filter(
        ExaminationSlot.examiner_id == current_user().id, Booking.status == "booked")
    return paged(q.order_by(Examination.title, ExaminationSlot.starts_at), lambda b: {
        **booking_json(b), "evaluation_status": b.evaluation.status if b.evaluation else "pending"})


@api.get("/examiner/evaluations")
@login_required("examiner")
def examiner_evaluations():
    q = Booking.query.join(ExaminationSlot).filter(ExaminationSlot.examiner_id == current_user().id)
    status = request.args.get("status")
    bookings = q.order_by(Booking.created_at.desc()).all()
    result = []
    for b in bookings:
        evaluation = b.evaluation
        row = {"booking_id": b.id, "student": b.student.full_name, "examination": b.slot.examination.title,
               "course": b.slot.examination.course.code, "starts_at": json_date(b.slot.starts_at),
               "status": evaluation.status if evaluation else "pending"}
        if not status or row["status"] == status:
            result.append(row)
    page = request.args.get("page", 1, type=int) or 1
    per_page = min(request.args.get("per_page", 20, type=int) or 20, 100)
    start = (page-1)*per_page
    return jsonify({"items": result[start:start+per_page], "pagination": {"page": page, "per_page": per_page,
        "total": len(result), "pages": (len(result)+per_page-1)//per_page}})


def evaluation_json(evaluation):
    return {"id": evaluation.id, "booking_id": evaluation.booking_id, "status": evaluation.status,
            "total_score": evaluation.total_score, "submitted_at": json_date(evaluation.submitted_at),
            "criteria": [{"criterion_id": s.criterion_id, "name": s.criterion.name, "max_score": s.criterion.max_score,
                          "score": s.score, "feedback": s.feedback} for s in evaluation.criterion_scores]}


@api.get("/examiner/bookings/<int:booking_id>/evaluation")
@login_required("examiner")
def get_evaluation(booking_id):
    booking = db.session.get(Booking, booking_id)
    if not booking or booking.slot.examiner_id != current_user().id:
        return error("Booking not found", 404)
    if booking.status != "booked" and booking.status != "completed":
        return error("Cancelled bookings cannot be evaluated", 409)
    evaluation = booking.evaluation
    return jsonify({"booking": {"id": booking.id, "student": booking.student.full_name,
        "examination": booking.slot.examination.title, "course": booking.slot.examination.course.code,
        "max_marks": booking.slot.examination.max_marks,
        "submission": {**submission_json(booking.submission), "download_url": f"/examiner/bookings/{booking.id}/submission/download"} if booking.submission else None,
        "submission_label": submission_label(booking.slot.examination.exam_type)}, "rubric": rubric_data(booking.slot.examination),
        "evaluation": evaluation_json(evaluation) if evaluation else None})


@api.get("/examiner/bookings/<int:booking_id>/submission/download")
@login_required("examiner")
def download_examiner_submission(booking_id):
    booking = db.session.get(Booking, booking_id)
    if not booking or booking.slot.examiner_id != current_user().id or not booking.submission:
        return error("Submission not found", 404)
    return submission_download(booking.submission)


@api.put("/examiner/bookings/<int:booking_id>/evaluation")
@login_required("examiner")
def save_evaluation(booking_id):
    booking = db.session.get(Booking, booking_id)
    data = request.get_json(silent=True)
    if not booking or booking.slot.examiner_id != current_user().id:
        return error("Booking not found", 404)
    if booking.status not in {"booked", "completed"}:
        return error("Cancelled bookings cannot be evaluated", 409)
    if not isinstance(data, dict) or not isinstance(data.get("criteria"), list):
        return error("criteria must be an array", 400)
    evaluation = booking.evaluation
    if evaluation and evaluation.status in {"submitted", "published"}:
        return error("Submitted evaluations are locked to preserve history", 409)
    by_id = {c.id: c for c in booking.slot.examination.rubric_criteria}
    submitted = data.get("submit", False)
    incoming = {}
    for row in data["criteria"]:
        if not isinstance(row, dict) or row.get("criterion_id") not in by_id:
            return error("Each score must reference a criterion in this examination rubric", 400)
        score, feedback = row.get("score"), row.get("feedback", "")
        if score is None and not submitted:
            continue
        if not isinstance(score, (int, float)) or isinstance(score, bool) or score < 0 or score > by_id[row["criterion_id"]].max_score:
            return error(f"Score for {by_id[row['criterion_id']].name} must be between 0 and {by_id[row['criterion_id']].max_score}", 400)
        if not isinstance(feedback, str) or len(feedback) > 2000:
            return error("Criterion feedback must be text up to 2000 characters", 400)
        incoming[row["criterion_id"]] = (float(score), feedback)
    if submitted and set(incoming) != set(by_id):
        return error("Every rubric criterion needs a score before submission", 400)
    if not evaluation:
        evaluation = Evaluation(booking_id=booking.id, examiner_id=current_user().id, status="draft")
        db.session.add(evaluation)
        db.session.flush()
    existing = {s.criterion_id: s for s in evaluation.criterion_scores}
    for criterion_id, (score, feedback) in incoming.items():
        if criterion_id in existing:
            existing[criterion_id].score, existing[criterion_id].feedback = score, feedback
        else:
            db.session.add(EvaluationCriterionScore(evaluation_id=evaluation.id, criterion_id=criterion_id, score=score, feedback=feedback))
    db.session.flush()
    scores_by_id = {s.criterion_id: s.score for s in evaluation.criterion_scores}
    scores_by_id.update({criterion_id: score for criterion_id, (score, _feedback) in incoming.items()})
    evaluation.total_score = sum(scores_by_id.values())
    if submitted:
        evaluation.status = "submitted"
        evaluation.submitted_at = now_utc()
        booking.status = "completed"
        portal_audit("evaluation.submitted", "evaluation", evaluation.id, {"booking_id": booking.id})
        notify(booking.student_id, f"Your evaluation for {booking.slot.examination.title} has been completed. Results will appear after publication.")
    else:
        evaluation.status = "draft"
        portal_audit("evaluation.draft_saved", "evaluation", evaluation.id, {"booking_id": booking.id})
    db.session.commit()
    if submitted:
        email_users([booking.student], f"Evaluation completed: {booking.slot.examination.title}",
            f"Your evaluation for {booking.slot.examination.title} has been completed. Your result will appear in the Student portal after publication.")
    return jsonify({"item": evaluation_json(evaluation)})


@api.get("/student/dashboard")
@login_required("student")
def student_dashboard():
    user = current_user()
    now = now_utc()
    available = Examination.query.join(Course).filter(Course.status == "active", Examination.status == "booking_open",
        Examination.booking_start <= now, Examination.booking_end >= now).count()
    exam_updates = student_exam_updates(now)
    active_bookings = Booking.query.join(ExaminationSlot).filter(Booking.student_id == user.id, Booking.status == "booked")
    upcoming = active_bookings.filter(ExaminationSlot.starts_at >= now).count()
    published = Evaluation.query.join(Booking).filter(Booking.student_id == user.id, Evaluation.status == "published").count()
    notifications = Notification.query.filter_by(user_id=user.id).order_by(Notification.created_at.desc()).limit(5).all()
    return jsonify({"role": "student", "stats": {"available_examinations": available,
        "upcoming_examinations": upcoming, "booked_slots": active_bookings.count(), "published_results": published},
        "examination_updates": exam_updates,
        "recent_activity": [{"id": n.id, "action": n.message, "created_at": json_date(n.created_at)} for n in notifications]})


@api.get("/student/examinations")
@login_required("student")
def student_examinations():
    now = now_utc()
    q = Examination.query.join(Course).filter(Examination.status == "booking_open",
        Examination.booking_start <= now, Examination.booking_end >= now, Course.status == "active")
    search = request.args.get("search", "").strip()
    if search:
        q = q.filter(or_(Examination.title.ilike(f"%{search}%"), Course.name.ilike(f"%{search}%"), Course.code.ilike(f"%{search}%")))
    raw_course_id = request.args.get("course_id")
    if raw_course_id:
        try:
            course_id = int(raw_course_id)
            if course_id < 1:
                raise ValueError
        except ValueError:
            return error("course_id must be a positive integer", 400)
        q = q.filter(Examination.course_id == course_id)
    if request.args.get("type"):
        if request.args["type"] not in {"Viva", "Practical", "Project Demo", "Assessment"}:
            return error("Invalid examination type filter", 400)
        q = q.filter(Examination.exam_type == request.args["type"])
    return paged(q.order_by(Examination.booking_end), student_safe_exam)


@api.get("/student/examinations/<int:exam_id>")
@login_required("student")
def student_examination_detail(exam_id):
    now = now_utc()
    exam = Examination.query.join(Course).filter(Examination.id == exam_id,
        Examination.status == "booking_open", Course.status == "active").first()
    if not exam:
        return error("Examination not found", 404)
    if not exam.booking_start or not exam.booking_end or not as_utc(exam.booking_start) <= now <= as_utc(exam.booking_end):
        return error("Examination is not currently open for student browsing", 404)
    active = Booking.query.join(ExaminationSlot).filter(Booking.student_id == current_user().id,
        ExaminationSlot.examination_id == exam.id, Booking.status == "booked").first()
    slots = ExaminationSlot.query.filter_by(examination_id=exam.id, status="open").filter(
        ExaminationSlot.starts_at >= now, ExaminationSlot.reserved_count < ExaminationSlot.capacity).order_by(ExaminationSlot.starts_at).all()
    eligible = bool(exam.booking_start and exam.booking_end and as_utc(exam.booking_start) <= now <= as_utc(exam.booking_end) and active is None)
    return jsonify({"item": student_safe_exam(exam), "eligible_to_book": eligible,
        "eligibility_reason": "You already have an active booking for this examination" if active else None if eligible else "Booking is outside its configured window",
        "slots": [slot_for_student(s) for s in slots]})


@api.post("/student/bookings")
@login_required("student")
def book_slot():
    data = request.get_json(silent=True)
    if not isinstance(data, dict) or not isinstance(data.get("slot_id"), int):
        return error("slot_id is required", 400)
    slot = db.session.get(ExaminationSlot, data["slot_id"])
    if not slot or slot.status != "open":
        return error("Slot is unavailable", 404)
    exam = slot.examination
    now = now_utc()
    if exam.status != "booking_open" or not exam.booking_start or not exam.booking_end or not as_utc(exam.booking_start) <= now <= as_utc(exam.booking_end):
        return error("Booking is outside the configured booking window", 409)
    if Booking.query.filter_by(student_id=current_user().id, examination_id=exam.id, status="booked").first():
        return error("You already have an active booking for this examination", 409)
    try:
        # This conditional write is atomic in SQLite. Competing requests can
        # increment only while the stored reservation count remains below capacity.
        changed = db.session.execute(text("UPDATE examination_slot SET reserved_count=reserved_count+1 WHERE id=:id AND status='open' AND reserved_count<capacity"), {"id": slot.id}).rowcount
        if changed != 1:
            db.session.rollback()
            return error("This slot is full; choose another time", 409)
        slot.reserved_count += 1
        booking = Booking(slot_id=slot.id, student_id=current_user().id, examination_id=exam.id, status="booked")
        db.session.add(booking)
        db.session.flush()
        portal_audit("booking.created", "booking", booking.id, {"examination_id": exam.id, "slot_id": slot.id})
        notify(current_user().id, f"Your booking for {exam.title} is confirmed.")
        notify(slot.examiner_id, f"A student booked a seat for {exam.title}.")
        db.session.commit()
        from ..services.email import send_notice
        start_time = as_utc(slot.starts_at).isoformat()
        send_notice(current_user().email, f"Booking confirmed: {exam.title}",
                    f"Your examination booking is confirmed.\nExamination: {exam.title}\nCourse: {exam.course.code}\nStart time (UTC): {start_time}\nBooking reference: {booking.id}")
        if slot.examiner:
            send_notice(slot.examiner.email, f"New booking: {exam.title}",
                        f"A student booked a seat for {exam.title}.\nStart time (UTC): {start_time}\nBooking reference: {booking.id}")
        invalidate_cache("schedule")
        return jsonify({"item": booking_json(booking), "slot": slot_for_student(slot)}), 201
    except IntegrityError:
        db.session.rollback()
        return error("You already have an active booking for this examination or the slot is full", 409)


@api.get("/student/bookings")
@login_required("student")
def student_bookings():
    q = Booking.query.filter_by(student_id=current_user().id)
    status = request.args.get("status")
    if status:
        if status not in {"booked", "cancelled", "completed"}:
            return error("Invalid booking status", 400)
        q = q.filter_by(status=status)
    search = request.args.get("search", "").strip()
    if search:
        q = q.join(ExaminationSlot).join(Examination).join(Course).filter(or_(Examination.title.ilike(f"%{search}%"), Course.code.ilike(f"%{search}%")))
    return paged(q.order_by(Booking.created_at.desc()), lambda b: {**booking_json(b),
        "evaluation_status": b.evaluation.status if b.evaluation else "pending"})


@api.get("/student/bookings/<int:booking_id>/submission")
@login_required("student")
def student_submission(booking_id):
    booking = db.session.get(Booking, booking_id)
    if not booking or booking.student_id != current_user().id:
        return error("Booking not found", 404)
    return jsonify({"item": submission_json(booking.submission),
        "label": submission_label(booking.slot.examination.exam_type),
        "replace_allowed": booking.status in {"booked", "completed"} and
            not (booking.evaluation and booking.evaluation.status in {"submitted", "published"})})


@api.post("/student/bookings/<int:booking_id>/submission")
@login_required("student")
def upload_student_submission(booking_id):
    booking = db.session.get(Booking, booking_id)
    if not booking or booking.student_id != current_user().id:
        return error("Booking not found", 404)
    if booking.status not in {"booked", "completed"}:
        return error("Work can only be submitted for an active examination booking", 409)
    if booking.evaluation and booking.evaluation.status in {"submitted", "published"}:
        return error("Work can no longer be changed after evaluation is submitted", 409)
    uploaded = request.files.get("file")
    if not uploaded or not uploaded.filename:
        return error("Choose a document to upload", 400)
    original_name = secure_filename(uploaded.filename)
    suffix = Path(original_name).suffix.lower()
    if not original_name or suffix not in {".pdf", ".doc", ".docx", ".txt"}:
        return error("Upload a PDF, DOC, DOCX, or TXT document", 400)

    directory = Path(current_app.config["SUBMISSION_DIR"])
    directory.mkdir(parents=True, exist_ok=True)
    stored_name = f"{uuid4().hex}{suffix}"
    destination = directory / stored_name
    old_submission = booking.submission
    old_name = old_submission.stored_filename if old_submission else None
    try:
        uploaded.save(destination)
        size = destination.stat().st_size
        if size < 1 or size > current_app.config["MAX_SUBMISSION_BYTES"]:
            destination.unlink(missing_ok=True)
            return error("Document must be between 1 byte and 10 MB", 413 if size > current_app.config["MAX_SUBMISSION_BYTES"] else 400)
        if old_submission:
            old_submission.original_filename = original_name[:255]
            old_submission.stored_filename = stored_name
            old_submission.size_bytes = size
            old_submission.submitted_at = now_utc()
            submission = old_submission
        else:
            submission = Submission(booking_id=booking.id, student_id=current_user().id,
                original_filename=original_name[:255], stored_filename=stored_name, size_bytes=size)
            db.session.add(submission)
        message = f"Work submitted for {booking.slot.examination.title}."
        notify(current_user().id, message)
        notify(booking.slot.examiner_id, f"{current_user().full_name} submitted work for {booking.slot.examination.title}.")
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        destination.unlink(missing_ok=True)
        current_app.logger.warning("Student submission save failed (%s)", type(exc).__name__)
        return error("Could not save your document. Please try again.", 500)
    if old_name:
        (directory / old_name).unlink(missing_ok=True)
    from ..services.email import send_notice
    send_notice(current_user().email, f"Work received: {booking.slot.examination.title}",
        f"Your {submission_label(booking.slot.examination.exam_type).lower()} was received for {booking.slot.examination.title}. File: {submission.original_filename}.")
    if booking.slot.examiner:
        send_notice(booking.slot.examiner.email, f"Student work submitted: {booking.slot.examination.title}",
            f"{current_user().full_name} submitted a document for {booking.slot.examination.title}. Sign in to review it before evaluation.")
    return jsonify({"item": submission_json(submission), "message": message}), 201


@api.get("/student/bookings/<int:booking_id>/submission/download")
@login_required("student")
def download_student_submission(booking_id):
    booking = db.session.get(Booking, booking_id)
    if not booking or booking.student_id != current_user().id or not booking.submission:
        return error("Submission not found", 404)
    return submission_download(booking.submission)


@api.post("/student/bookings/<int:booking_id>/cancel")
@login_required("student")
def cancel_booking(booking_id):
    booking = db.session.get(Booking, booking_id)
    if not booking or booking.student_id != current_user().id:
        return error("Booking not found", 404)
    if booking.status != "booked":
        return error("Only active bookings can be cancelled", 409)
    deadline = booking.slot.examination.booking_end
    if not deadline or now_utc() >= as_utc(deadline):
        return error("The booking cancellation deadline has passed", 409)
    booking.status = "cancelled"
    slot = booking.slot
    slot.reserved_count = max(0, slot.reserved_count-1)
    portal_audit("booking.cancelled", "booking", booking.id, {"slot_id": slot.id})
    notify(booking.student_id, f"Your booking for {booking.slot.examination.title} was cancelled.")
    notify(slot.examiner_id, f"A booking for {booking.slot.examination.title} was cancelled.")
    db.session.commit()
    from ..services.email import send_notice
    send_notice(current_user().email, f"Booking cancelled: {booking.slot.examination.title}",
                f"Your booking for {booking.slot.examination.title} has been cancelled. Booking reference: {booking.id}.")
    if slot.examiner:
        send_notice(slot.examiner.email, f"Booking cancelled: {booking.slot.examination.title}",
                    f"A booking for {booking.slot.examination.title} has been cancelled. Booking reference: {booking.id}.")
    invalidate_cache("schedule")
    return jsonify({"item": booking_json(booking), "slot": slot_for_student(slot)})


@api.get("/student/results")
@login_required("student")
def student_results():
    q = Evaluation.query.join(Booking).filter(Booking.student_id == current_user().id, Evaluation.status == "published")
    def result_json(e):
        b, exam = e.booking, e.booking.slot.examination
        return {"evaluation_id": e.id, "booking_id": b.id, "examination": exam.title,
                "course": exam.course.code, "total_marks": e.total_score, "maximum_marks": exam.max_marks,
                "evaluation_date": json_date(e.submitted_at), "examiner_feedback": "; ".join(s.feedback for s in e.criterion_scores if s.feedback),
                "criteria": evaluation_scores(e)}
    return paged(q.order_by(Evaluation.published_at.desc()), result_json)


def evaluation_scores(evaluation):
    return [{"criterion": s.criterion.name, "score": s.score, "maximum": s.criterion.max_score,
             "feedback": s.feedback} for s in evaluation.criterion_scores]


@api.get("/student/results/<int:evaluation_id>")
@login_required("student")
def student_result_detail(evaluation_id):
    evaluation = Evaluation.query.join(Booking).filter(Evaluation.id == evaluation_id,
        Booking.student_id == current_user().id, Evaluation.status == "published").first()
    if not evaluation:
        return error("Published result not found", 404)
    b, exam = evaluation.booking, evaluation.booking.slot.examination
    return jsonify({"item": {"evaluation_id": evaluation.id, "examination": exam.title, "course": exam.course.code,
        "total_marks": evaluation.total_score, "maximum_marks": exam.max_marks,
        "evaluation_date": json_date(evaluation.submitted_at), "examiner_feedback": "; ".join(s.feedback for s in evaluation.criterion_scores if s.feedback),
        "criteria": evaluation_scores(evaluation)}})


@api.get("/notifications")
@login_required()
def notifications():
    q = Notification.query.filter_by(user_id=current_user().id).order_by(Notification.created_at.desc())
    return paged(q, lambda n: {"id": n.id, "message": n.message, "read_at": json_date(n.read_at), "created_at": json_date(n.created_at)})


@api.post("/notifications/<int:notification_id>/read")
@login_required()
def mark_notification_read(notification_id):
    n = db.session.get(Notification, notification_id)
    if not n or n.user_id != current_user().id:
        return error("Notification not found", 404)
    n.read_at = now_utc()
    db.session.commit()
    return jsonify({"item": {"id": n.id, "read_at": json_date(n.read_at)}})


@api.post("/admin/evaluations/<int:evaluation_id>/publish")
@login_required("admin")
def publish_result(evaluation_id):
    evaluation = db.session.get(Evaluation, evaluation_id)
    if not evaluation:
        return error("Evaluation not found", 404)
    if evaluation.status != "submitted":
        return error("Only submitted evaluations can be published", 409)
    evaluation.status = "published"
    evaluation.published_at = now_utc()
    notify(evaluation.booking.student_id, f"Your result for {evaluation.booking.slot.examination.title} has been published.")
    portal_audit("result.published", "evaluation", evaluation.id, {"booking_id": evaluation.booking_id})
    db.session.commit()
    from ..services.email import send_notice
    student = evaluation.booking.student
    exam = evaluation.booking.slot.examination
    send_notice(student.email, f"Result published: {exam.title}",
                f"Your result for {exam.title} ({exam.course.code}) is available in the Student portal.")
    return jsonify({"item": {"id": evaluation.id, "status": evaluation.status,
        "published_at": json_date(evaluation.published_at)}})


@api.get("/admin/evaluations")
@login_required("admin")
def admin_evaluations():
    q = Evaluation.query.join(Booking).join(ExaminationSlot)
    status = request.args.get("status")
    if status:
        q = q.filter(Evaluation.status == status)
    return paged(q.order_by(Evaluation.created_at.desc()), lambda e: {**evaluation_json(e),
        "student": e.booking.student.full_name, "examination": e.booking.slot.examination.title})


@api.patch("/examiner/profile")
@login_required("examiner")
def update_examiner_profile():
    user = current_user()
    data = request.get_json(silent=True)
    if not isinstance(data, dict) or set(data) - {"full_name", "department", "email"}:
        return error("Only full_name, email and department may be updated", 400)
    if "full_name" in data:
        if not isinstance(data["full_name"], str) or not 2 <= len(data["full_name"].strip()) <= 120:
            return error("Invalid full_name", 400)
        user.full_name = data["full_name"].strip()
    if "department" in data:
        if not isinstance(data["department"], str) or len(data["department"]) > 120:
            return error("Invalid department", 400)
        user.examiner_profile.department = data["department"].strip()
    if "email" in data:
        try:
            email = validate_email(data["email"], check_deliverability=False).normalized
        except (EmailNotValidError, TypeError):
            return error("A valid email address is required", 400)
        if User.query.filter(User.email == email, User.id != user.id).first():
            return error("An account with this email already exists", 409)
        user.email = email
    db.session.commit()
    return jsonify({"user": user.to_dict(), "department": user.examiner_profile.department})


@api.get("/examiner/profile")
@login_required("examiner")
def examiner_profile():
    user = current_user()
    return jsonify({"user": user.to_dict(), "department": user.examiner_profile.department,
        "workload": {"examinations": Examination.query.filter_by(examiner_id=user.id).count(),
                     "slots": ExaminationSlot.query.filter_by(examiner_id=user.id).count(),
                     "bookings": Booking.query.join(ExaminationSlot).filter(ExaminationSlot.examiner_id == user.id).count()}})


@api.patch("/student/profile")
@login_required("student")
def update_student_profile():
    user = current_user()
    data = request.get_json(silent=True)
    if not isinstance(data, dict) or set(data) - {"full_name", "email", "program", "department", "student_number"}:
        return error("Only personal and academic profile fields may be updated", 400)
    if "full_name" in data:
        if not isinstance(data["full_name"], str) or not 2 <= len(data["full_name"].strip()) <= 120:
            return error("Invalid full_name", 400)
        user.full_name = data["full_name"].strip()
    if "email" in data:
        try:
            email = validate_email(data["email"], check_deliverability=False).normalized
        except (EmailNotValidError, TypeError):
            return error("A valid email address is required", 400)
        if User.query.filter(User.email == email, User.id != user.id).first():
            return error("An account with this email already exists", 409)
        user.email = email
    profile = user.student_profile
    for field in ("program", "department"):
        if field in data:
            if not isinstance(data[field], str) or len(data[field]) > 120:
                return error(f"Invalid {field}", 400)
            setattr(profile, field, data[field].strip())
    if "student_number" in data:
        value = data["student_number"]
        if value and (not isinstance(value, str) or len(value) > 40):
            return error("Invalid student_number", 400)
        profile.student_number = value or None
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return error("student_number is already in use", 409)
    return jsonify({"user": user.to_dict(), "program": profile.program, "department": profile.department,
                    "student_number": profile.student_number})


@api.get("/student/profile")
@login_required("student")
def student_profile():
    user = current_user()
    p = user.student_profile
    return jsonify({"user": user.to_dict(), "program": p.program, "department": p.department,
                    "student_number": p.student_number})
