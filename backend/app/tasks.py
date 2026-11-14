"""Idempotent scheduled and user-triggered work for the portal."""
import csv
import html
import io
import json
import logging
import os
import uuid
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from celery.utils.log import get_task_logger
from sqlalchemy import func, or_
from sqlalchemy.exc import IntegrityError

from .celery_app import celery
from .extensions import db
from .models import (Booking, Evaluation, Examination, ExaminationSlot, ExportJob,
                     MonthlyReport, Notification, ReminderLog, User)
from .services.email import delivery_succeeded, send_email as _send_email
from .services.email_templates import export_ready_email

logger = get_task_logger(__name__)
MAX_RETRIES = int(os.getenv("CELERY_TASK_MAX_RETRIES", "3"))


def _timezone():
    from flask import current_app
    try:
        return ZoneInfo(current_app.config["CELERY_BEAT_TIMEZONE"])
    except Exception:
        return ZoneInfo("UTC")


def _period_for_previous_month(today=None):
    today = today or datetime.now(_timezone()).date()
    first_this_month = today.replace(day=1)
    last_previous = first_this_month - timedelta(days=1)
    return last_previous.replace(day=1), last_previous


def _send_daily_reminders(today=None):
    from flask import current_app
    local_today = today or datetime.now(_timezone()).date()
    target_date = local_today + timedelta(days=1)
    bookings = Booking.query.filter_by(status="booked").all()
    delivered = 0
    failed = 0
    for booking in bookings:
        start = booking.slot.starts_at
        start = start.replace(tzinfo=timezone.utc) if start.tzinfo is None else start
        if start.astimezone(_timezone()).date() != target_date:
            continue
        if booking.slot.examination.status == "completed" or not booking.student or not booking.student.is_active or not booking.student.email:
            continue
        existing_exam_reminder = ReminderLog.query.join(Booking, ReminderLog.booking_id == Booking.id).join(
            ExaminationSlot, Booking.slot_id == ExaminationSlot.id
        ).filter(
            Booking.student_id == booking.student_id,
            ExaminationSlot.examination_id == booking.slot.examination_id,
            ReminderLog.reminder_date == local_today,
            ReminderLog.status == "delivered",
        ).first()
        if existing_exam_reminder:
            continue
        log_row = ReminderLog.query.filter_by(booking_id=booking.id, reminder_date=local_today).first()
        if log_row and log_row.status == "delivered":
            continue
        notification_already_created = bool(log_row and log_row.status == "not_configured")
        if not log_row:
            log_row = ReminderLog(booking_id=booking.id, reminder_date=local_today, status="pending")
            db.session.add(log_row)
            try:
                db.session.flush()
            except IntegrityError:
                db.session.rollback()
                continue
        exam = booking.slot.examination
        when = start.astimezone(_timezone()).strftime("%b %d, %Y %I:%M %p %Z")
        message = (f"Reminder: {exam.title} is scheduled for {when}. "
                   f"Examiner: {booking.slot.examiner.full_name}. "
                   f"Instructions: {exam.description or current_app.config.get('DEFAULT_EXAM_INSTRUCTIONS', 'See your examination details in the portal.')} "
                   f"Booking reference: {booking.id}.")
        adapter = current_app.config.get("REMINDER_DELIVERY", "email").lower()
        try:
            if not notification_already_created:
                db.session.add(Notification(user_id=booking.student_id, message=message))
            if adapter == "notification":
                pass
            elif adapter == "email":
                if not delivery_succeeded(_send_email(booking.student.email, f"Examination reminder: {exam.title}", message)):
                    log_row.status, log_row.safe_error = "not_configured", "Email delivery is not configured"
                    db.session.commit()
                    continue
            else:
                raise ValueError("Unsupported reminder delivery adapter")
            log_row.status, log_row.delivered_at, log_row.safe_error = "delivered", datetime.now(timezone.utc), None
            db.session.commit()
            delivered += 1
            logger.info("Reminder delivered for booking %s using %s", booking.id, adapter)
        except Exception as exc:
            db.session.rollback()
            logger.exception("Reminder delivery failed for booking %s", booking.id)
            row = ReminderLog.query.filter_by(booking_id=booking.id, reminder_date=local_today).first()
            if row:
                row.status, row.safe_error = "failed", f"Delivery failed ({type(exc).__name__})"
                db.session.commit()
            failed += 1
    if failed:
        raise RuntimeError(f"{failed} examination reminders failed and will be retried")
    return delivered


def _monthly_report_data(start, end):
    start_utc = datetime.combine(start, time.min, tzinfo=_timezone()).astimezone(timezone.utc)
    end_utc = datetime.combine(end + timedelta(days=1), time.min, tzinfo=_timezone()).astimezone(timezone.utc)
    completed_examinations = Examination.query.filter(Examination.status == "completed").filter(
        Examination.id.in_(db.session.query(ExaminationSlot.examination_id).filter(
            ExaminationSlot.starts_at >= start_utc.replace(tzinfo=None), ExaminationSlot.starts_at < end_utc.replace(tzinfo=None)))).count()
    evaluated = db.session.query(func.count(func.distinct(Booking.student_id))).join(
        Evaluation, Evaluation.booking_id == Booking.id).join(ExaminationSlot).filter(
        Evaluation.status.in_(["submitted", "published"]), Evaluation.submitted_at >= start_utc.replace(tzinfo=None),
        Evaluation.submitted_at < end_utc.replace(tzinfo=None)).scalar() or 0
    completed_bookings = Booking.query.join(ExaminationSlot).filter(Booking.status == "completed",
        ExaminationSlot.starts_at >= start_utc.replace(tzinfo=None), ExaminationSlot.starts_at < end_utc.replace(tzinfo=None)).count()
    pending = Booking.query.join(ExaminationSlot).outerjoin(Evaluation).filter(
        ExaminationSlot.starts_at >= start_utc.replace(tzinfo=None), ExaminationSlot.starts_at < end_utc.replace(tzinfo=None),
        Booking.status.in_(["booked", "completed"]), or_(Evaluation.id.is_(None), Evaluation.status == "draft")).count()
    workload = db.session.query(User.full_name, func.count(Evaluation.id).label("evaluations")).join(
        Evaluation, Evaluation.examiner_id == User.id).filter(User.role == "examiner",
        Evaluation.submitted_at >= start_utc.replace(tzinfo=None), Evaluation.submitted_at < end_utc.replace(tzinfo=None)
        ).group_by(User.id, User.full_name).order_by(func.count(Evaluation.id).desc()).limit(10).all()
    bookings_by_status = db.session.query(Booking.status, func.count(Booking.id)).join(ExaminationSlot).filter(
        ExaminationSlot.starts_at >= start_utc.replace(tzinfo=None), ExaminationSlot.starts_at < end_utc.replace(tzinfo=None)
        ).group_by(Booking.status).all()
    exams_by_status = db.session.query(Examination.status, func.count(Examination.id)).group_by(Examination.status).all()
    return {"period_start": start.isoformat(), "period_end": end.isoformat(),
            "examinations_conducted": completed_examinations, "students_evaluated": evaluated,
            "completed_bookings": completed_bookings, "pending_evaluations": pending,
            "most_active_examiners": [{"name": name, "evaluations": count} for name, count in workload],
            "booking_statistics": {status: count for status, count in bookings_by_status},
            "examination_statistics": {status: count for status, count in exams_by_status}}


def _report_html(summary):
    def rows(mapping):
        return "".join(f"<tr><th>{html.escape(str(k).replace('_', ' ').title())}</th><td>{int(v)}</td></tr>" for k, v in mapping.items()) or "<tr><td colspan='2'>No records</td></tr>"
    examiners = "".join(f"<tr><td>{html.escape(row['name'])}</td><td>{row['evaluations']}</td></tr>" for row in summary["most_active_examiners"]) or "<tr><td colspan='2'>No evaluations</td></tr>"
    return ("<!doctype html><html><head><meta charset='utf-8'><title>Monthly examination report</title>"
            "<style>body{font:16px Arial;max-width:900px;margin:2rem auto;color:#20344d}table{border-collapse:collapse;width:100%;margin:1rem 0}td,th{border:1px solid #d8e0ea;padding:.6rem;text-align:left}h1{color:#163a63}</style></head><body>"
            f"<h1>Monthly Examination Report</h1><p>Reporting period: {summary['period_start']} to {summary['period_end']}</p>"
            "<h2>Activity</h2><table>" + rows({k: summary[k] for k in ["examinations_conducted", "students_evaluated", "completed_bookings", "pending_evaluations"]}) +
            "</table><h2>Most active examiners</h2><table><tr><th>Examiner</th><th>Submitted evaluations</th></tr>" + examiners +
            "</table><h2>Bookings by status</h2><table>" + rows(summary["booking_statistics"])+
            "</table><h2>Examinations by status</h2><table>" + rows(summary["examination_statistics"])+"</table></body></html>")


def _generate_monthly_report(report_id=None, period_start=None):
    from flask import current_app
    if report_id:
        report = db.session.get(MonthlyReport, report_id)
        if not report:
            return None
        if report.status == "completed":
            return report.id
        start, end = report.period_start, report.period_end
        report.status, report.safe_error = "processing", None
        db.session.commit()
    else:
        start, end = _period_for_previous_month()
        if period_start:
            start = date.fromisoformat(period_start)
            end = (date(start.year + (start.month == 12), 1 if start.month == 12 else start.month + 1, 1) - timedelta(days=1))
        report = MonthlyReport.query.filter_by(period_start=start).first()
        if report and report.status == "completed":
            return report.id
        if not report:
            report = MonthlyReport(period_start=start, period_end=end, status="processing")
            db.session.add(report)
            db.session.commit()
        else:
            report.status = "processing"
            db.session.commit()
    artifact_path = None
    try:
        summary = _monthly_report_data(start, end)
        content = _report_html(summary)
        directory = Path(current_app.config["REPORT_DIR"]).resolve()
        directory.mkdir(parents=True, exist_ok=True)
        name = uuid.uuid4().hex + ".html"
        artifact_path = directory / name
        artifact_path.write_text(content, encoding="utf-8")
        report.summary_json, report.artifact_name = json.dumps(summary), name
        report.generated_at, report.status = datetime.now(timezone.utc), "completed"
        report.safe_error = None
        destination = current_app.config.get("REPORT_EMAIL")
        try:
            if destination and delivery_succeeded(_send_email(destination, f"Monthly examination report · {start:%Y-%m}",
                                           "The monthly examination report is attached below.", content)):
                report.delivery_status = "sent"
            else:
                report.delivery_status = "not_configured"
        except Exception as exc:
            report.delivery_status = "failed"
            report.safe_error = f"Report email delivery failed ({type(exc).__name__})"
            logger.exception("Monthly report email delivery failed")
        db.session.commit()
        logger.info("Monthly report %s generated for %s through %s", report.id, start, end)
        return report.id
    except Exception as exc:
        db.session.rollback()
        if artifact_path and artifact_path.exists():
            artifact_path.unlink()
        logger.exception("Monthly report generation failed")
        report = db.session.get(MonthlyReport, report_id) if report_id else MonthlyReport.query.filter_by(period_start=start).first()
        if report:
            report.status, report.safe_error = "failed", f"Report generation failed ({type(exc).__name__})"
            db.session.commit()
        raise


def _export_student_history(job_id):
    from flask import current_app
    job = ExportJob.query.filter_by(job_id=job_id).first()
    if not job or job.status == "completed":
        return job.status if job else "missing"
    job.status, job.started_at, job.safe_error = "processing", datetime.now(timezone.utc), None
    db.session.commit()
    target = None
    temporary = None
    try:
        rows = Booking.query.filter_by(student_id=job.user_id).order_by(Booking.created_at).all()
        output = io.StringIO(newline="")
        writer = csv.writer(output)
        writer.writerow(["Student ID", "Examination", "Course", "Examination Date", "Examiner", "Marks Obtained", "Booking Status"])
        for booking in rows:
            evaluation = booking.evaluation
            marks = evaluation.total_score if evaluation and evaluation.status == "published" else ""
            writer.writerow([job.user_id, booking.slot.examination.title, booking.slot.examination.course.code,
                booking.slot.starts_at.isoformat(), booking.slot.examiner.full_name, marks, booking.status])
        directory = Path(current_app.config["EXPORT_DIR"]).resolve()
        directory.mkdir(parents=True, exist_ok=True)
        name = uuid.uuid4().hex + ".csv"
        target = directory / name
        temporary = directory / (name + ".tmp")
        temporary.write_text(output.getvalue(), encoding="utf-8-sig", newline="")
        os.replace(temporary, target)
        job.artifact_name, job.status = name, "completed"
        job.completed_at = datetime.now(timezone.utc)
        db.session.add(Notification(user_id=job.user_id, message="Your examination history export is ready to download."))
        db.session.commit()
        try:
            text_body, html_body = export_ready_email(
                job.user.full_name, job.artifact_name, job.created_at,
                current_app.config.get("FRONTEND_ORIGIN", "http://localhost:5173") + "/dashboard",
            )
            delivered = _send_email(job.user.email, "Your Exam History Export Is Ready", text_body, html_body)
            if not delivery_succeeded(delivered):
                logger.warning("Student history export email was not delivered for job %s: %s", job.job_id, delivered.get("reason", "provider returned no delivery") if isinstance(delivered, dict) else "invalid provider result")
        except Exception as exc:
            logger.warning("Student history export email failed for job %s (%s): %s", job.job_id, type(exc).__name__, str(exc)[:240])
        logger.info("Student history export completed for job %s", job.job_id)
        return "completed"
    except Exception:
        db.session.rollback()
        for candidate in (temporary, target):
            if candidate and candidate.exists():
                candidate.unlink()
        logger.exception("Student history export failed for job %s", job_id)
        raise


@celery.task(name="app.tasks.send_daily_reminders", autoretry_for=(Exception,), retry_backoff=True,
             max_retries=MAX_RETRIES, soft_time_limit=120, time_limit=180)
def send_daily_reminders():
    return _send_daily_reminders()


@celery.task(name="app.tasks.generate_monthly_report", autoretry_for=(Exception,), retry_backoff=True,
             max_retries=MAX_RETRIES, soft_time_limit=240, time_limit=300)
def generate_monthly_report(report_id=None, period_start=None):
    return _generate_monthly_report(report_id, period_start)


@celery.task(name="app.tasks.export_student_history", bind=True, max_retries=MAX_RETRIES,
             soft_time_limit=120, time_limit=180)
def export_student_history(self, job_id):
    from flask import current_app
    try:
        return _export_student_history(job_id)
    except Exception as exc:
        job = ExportJob.query.filter_by(job_id=job_id).first()
        if not job:
            raise
        if self.request.retries < self.max_retries:
            job.status, job.safe_error = "requested", "Export is being retried after a temporary error."
            db.session.commit()
            raise self.retry(exc=exc, countdown=current_app.config["CELERY_TASK_DEFAULT_RETRY_DELAY"])
        job.status, job.safe_error = "failed", "The export could not be generated. Please request another export."
        job.completed_at = datetime.now(timezone.utc)
        db.session.add(Notification(user_id=job.user_id, message="Your examination history export failed. Please try again."))
        db.session.commit()
        raise


@celery.task(name="app.tasks.cleanup_expired_artifacts", autoretry_for=(OSError,), retry_backoff=True,
             max_retries=MAX_RETRIES, soft_time_limit=120, time_limit=180)
def cleanup_expired_artifacts():
    from flask import current_app
    now = datetime.now(timezone.utc)
    exports = Path(current_app.config["EXPORT_DIR"]).resolve()
    reports = Path(current_app.config["REPORT_DIR"]).resolve()
    export_cutoff = now - timedelta(days=current_app.config["EXPORT_RETENTION_DAYS"])
    report_cutoff = now - timedelta(days=current_app.config["REPORT_RETENTION_DAYS"])
    removed = 0
    for row in ExportJob.query.filter(ExportJob.completed_at < export_cutoff, ExportJob.status == "completed").all():
        if row.artifact_name:
            candidate = (exports / Path(row.artifact_name).name).resolve()
            if candidate.parent == exports and candidate.exists():
                candidate.unlink()
                removed += 1
        row.artifact_name, row.status = None, "expired"
    for row in MonthlyReport.query.filter(MonthlyReport.generated_at < report_cutoff, MonthlyReport.status == "completed").all():
        if row.artifact_name:
            candidate = (reports / Path(row.artifact_name).name).resolve()
            if candidate.parent == reports and candidate.exists():
                candidate.unlink()
                removed += 1
        row.artifact_name, row.status = None, "expired"
    db.session.commit()
    logger.info("Expired %s generated artifacts", removed)
    return removed
