import csv
import fnmatch
import io
import shutil
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.extensions import db
from app.models import (Booking, Course, Evaluation, Examination, ExaminationSlot,
                        ExportJob, MonthlyReport, Notification, ReminderLog, User)
from app.celery_app import celery
from app.services import cache as cache_service
from app.tasks import (_export_student_history, _generate_monthly_report,
                       _period_for_previous_month, _send_daily_reminders,
                       cleanup_expired_artifacts, export_student_history)


class FakeRedis:
    def __init__(self):
        self.data = {}
        self.ttl = {}

    def get(self, key):
        return self.data.get(key)

    def setex(self, key, ttl, value):
        self.ttl[key] = ttl
        self.data[key] = value

    def scan_iter(self, match, count=200):
        return (key for key in list(self.data) if fnmatch.fnmatch(key, match))

    def delete(self, *keys):
        for key in keys:
            self.data.pop(key, None)


@pytest.fixture
def artifact_dir(app):
    path = Path(app.instance_path) / ("m4-test-" + uuid.uuid4().hex)
    path.mkdir(parents=True)
    yield path
    shutil.rmtree(path, ignore_errors=True)


def test_cache_hit_ttl_invalidation_and_database_fallback(app, client, monkeypatch):
    fake = FakeRedis()
    app.config.update(CACHE_ENABLED=True, CACHE_DEFAULT_TIMEOUT=123)
    monkeypatch.setitem(cache_service._clients, app.config["REDIS_URL"], fake)
    client.post("/api/v1/auth/register", json={"full_name": "Cache Student", "email": "cache@example.com", "password": "StrongPassword123!"})
    first = client.get("/api/v1/student/examinations")
    second = client.get("/api/v1/student/examinations")
    assert "X-Cache" not in first.headers and "X-Cache" not in second.headers
    assert not fake.data  # Student booking availability is deliberately read fresh.
    with app.app_context():
        from app.services.auth import seed_admin
        seed_admin("admin@example.com", "StrongPassword123!")
    client.post("/api/v1/auth/logout")
    client.post("/api/v1/auth/login", json={"email": "admin@example.com", "password": "StrongPassword123!"})
    assert client.get("/api/v1/admin/examinations?status=draft").headers["X-Cache"] == "MISS"
    assert client.get("/api/v1/admin/examinations?status=draft").headers["X-Cache"] == "HIT"
    assert client.get("/api/v1/admin/examinations?status=closed").headers["X-Cache"] == "MISS"
    assert client.post("/api/v1/admin/courses", json={"code": "CACH1", "name": "Cache Invalidation"}).status_code == 201
    assert client.get("/api/v1/admin/examinations?status=draft").headers["X-Cache"] == "MISS"
    monkeypatch.setattr(cache_service, "_client", lambda: (_ for _ in ()).throw(ConnectionError("Redis down")))
    assert cache_service.cache_get("examinations:x") is None
    assert cache_service.cache_set("examinations:x", {"safe": True}) is False


def _make_reminder_booking(app, slot_start):
    with app.app_context():
        student = User(full_name="Reminder Student", email="reminder@example.com", role="student")
        student.set_password("StrongPassword123!")
        examiner = User(full_name="Assigned Examiner", email="reminder-examiner@example.com", role="examiner")
        examiner.set_password("StrongPassword123!")
        course = Course(code="REM101", name="Reminder Course")
        db.session.add_all([student, examiner, course]); db.session.flush()
        exam = Examination(course_id=course.id, examiner_id=examiner.id, title="Tomorrow Viva",
                           description="Bring your project notes", status="booking_open")
        db.session.add(exam); db.session.flush()
        slot = ExaminationSlot(examination_id=exam.id, examiner_id=examiner.id, starts_at=slot_start,
                               ends_at=slot_start + timedelta(minutes=30), capacity=1, reserved_count=1)
        db.session.add(slot); db.session.flush()
        booking = Booking(slot_id=slot.id, examination_id=exam.id, student_id=student.id, status="booked")
        db.session.add(booking); db.session.commit()
        return booking.id


def test_reminder_eligibility_and_duplicate_prevention(app):
    from zoneinfo import ZoneInfo
    local_tz = ZoneInfo(app.config["CELERY_BEAT_TIMEZONE"])
    tomorrow = datetime.combine(datetime.now(local_tz).date() + timedelta(days=1), datetime.min.time(), local_tz) + timedelta(hours=11)
    booking_id = _make_reminder_booking(app, tomorrow.astimezone(timezone.utc).replace(tzinfo=None))
    with app.app_context():
        today = datetime.now(local_tz).date()
        app.config["REMINDER_DELIVERY"] = "notification"
        assert _send_daily_reminders(today) == 1
        assert _send_daily_reminders(today) == 0
        assert Notification.query.filter(Notification.message.contains("Tomorrow Viva")).count() == 1
        row = ReminderLog.query.filter_by(booking_id=booking_id, reminder_date=today).one()
        assert row.status == "delivered" and row.delivered_at is not None


def test_unconfigured_email_is_not_reported_as_delivered(app):
    from zoneinfo import ZoneInfo
    local_tz = ZoneInfo(app.config["CELERY_BEAT_TIMEZONE"])
    tomorrow = datetime.combine(datetime.now(local_tz).date() + timedelta(days=1), datetime.min.time(), local_tz) + timedelta(hours=9)
    booking_id = _make_reminder_booking(app, tomorrow.astimezone(timezone.utc).replace(tzinfo=None))
    with app.app_context():
        app.config["REMINDER_DELIVERY"] = "email"
        app.config["SMTP_HOST"] = None
        today = datetime.now(local_tz).date()
        assert _send_daily_reminders(today) == 0
        assert ReminderLog.query.filter_by(booking_id=booking_id, reminder_date=today).one().status == "not_configured"


def _make_export_data(app):
    with app.app_context():
        student = User(full_name="Export Student", email="export@example.com", role="student")
        student.set_password("StrongPassword123!")
        examiner = User(full_name="Export Examiner", email="export-examiner@example.com", role="examiner")
        examiner.set_password("StrongPassword123!")
        course = Course(code="EXP101", name="Export Course")
        db.session.add_all([student, examiner, course]); db.session.flush()
        ids = []
        for i, status in enumerate(("published", "submitted")):
            exam = Examination(course_id=course.id, examiner_id=examiner.id, title=f"Export Exam {i}", status="completed")
            db.session.add(exam); db.session.flush()
            slot = ExaminationSlot(examination_id=exam.id, examiner_id=examiner.id,
                starts_at=datetime.now(timezone.utc).replace(tzinfo=None), ends_at=datetime.now(timezone.utc).replace(tzinfo=None)+timedelta(minutes=30), capacity=1)
            db.session.add(slot); db.session.flush()
            booking = Booking(slot_id=slot.id, examination_id=exam.id, student_id=student.id, status="completed")
            db.session.add(booking); db.session.flush()
            db.session.add(Evaluation(booking_id=booking.id, examiner_id=examiner.id, status=status, total_score=91 if status == "published" else 99))
            ids.append(booking.id)
        db.session.commit()
        return student.id


def test_student_export_csv_publication_filter_and_owner_download(app, client, artifact_dir):
    user_id = _make_export_data(app)
    app.config["EXPORT_DIR"] = artifact_dir / "exports"
    with app.app_context():
        job = ExportJob(job_id="2ef6a114-3051-48e4-b635-a4b5f8cb7029", user_id=user_id)
        db.session.add(job); db.session.commit()
        assert _export_student_history(job.job_id) == "completed"
        db.session.refresh(job)
        content = (app.config["EXPORT_DIR"] / job.artifact_name).read_text(encoding="utf-8-sig")
        rows = list(csv.DictReader(io.StringIO(content)))
        assert len(rows) == 2
        assert rows[0]["Marks Obtained"] == "91.0"
        assert rows[1]["Marks Obtained"] == ""
    client.post("/api/v1/auth/login", json={"email": "export@example.com", "password": "StrongPassword123!"})
    assert client.get(f"/api/v1/student/exports/{job.job_id}/download").status_code == 200
    other = app.test_client()
    other.post("/api/v1/auth/register", json={"full_name": "Other Student", "email": "other-export@example.com", "password": "StrongPassword123!"})
    assert other.get(f"/api/v1/student/exports/{job.job_id}/download").status_code == 404


def test_monthly_report_period_generation_and_admin_endpoint(app, client, artifact_dir):
    assert _period_for_previous_month(date(2026, 1, 1)) == (date(2025, 12, 1), date(2025, 12, 31))
    app.config["REPORT_DIR"] = artifact_dir / "reports"
    with app.app_context():
        report_id = _generate_monthly_report(period_start="2025-12-01")
        report = db.session.get(MonthlyReport, report_id)
        assert report.status == "completed" and report.delivery_status == "not_configured"
        assert (app.config["REPORT_DIR"] / report.artifact_name).read_text(encoding="utf-8").startswith("<!doctype html>")
    client.post("/api/v1/auth/register", json={"full_name": "Report Student", "email": "report@example.com", "password": "StrongPassword123!"})
    assert client.get("/api/v1/admin/monthly-reports").status_code == 403


def test_celery_registration_student_export_and_admin_report_queue(app, client, artifact_dir, monkeypatch):
    user_id = _make_export_data(app)
    app.config["EXPORT_DIR"] = artifact_dir / "exports"
    app.config["REPORT_DIR"] = artifact_dir / "reports"
    assert celery.conf.broker_url == app.config["CELERY_BROKER_URL"]
    assert {"daily-examination-reminders", "monthly-examination-report"}.issubset(celery.conf.beat_schedule)
    assert "app.tasks.export_student_history" in celery.tasks
    assert "app.tasks.generate_monthly_report" in celery.tasks
    queued_tasks = []
    monkeypatch.setattr("app.tasks.export_student_history.delay",
        lambda job_id: (queued_tasks.append(job_id), export_student_history.apply(args=[job_id]))[1])
    client.post("/api/v1/auth/login", json={"email": "export@example.com", "password": "StrongPassword123!"})
    queued = client.post("/api/v1/student/exports")
    assert queued.status_code == 202 and queued.json["item"]["status"] == "completed"
    job_id = queued.json["item"]["job_id"]
    assert queued_tasks == [job_id]
    assert client.get("/api/v1/student/exports").json["items"][0]["job_id"] == job_id
    assert client.get(f"/api/v1/student/exports/{job_id}/download").status_code == 200
    with app.app_context():
        from app.services.auth import seed_admin
        seed_admin("admin@example.com", "StrongPassword123!")
    client.post("/api/v1/auth/logout")
    client.post("/api/v1/auth/login", json={"email": "admin@example.com", "password": "StrongPassword123!"})
    report = client.post("/api/v1/admin/monthly-reports")
    assert report.status_code == 202 and report.json["item"]["status"] == "completed"
    report_id = report.json["item"]["id"]
    assert client.get(f"/api/v1/admin/monthly-reports/{report_id}/download").status_code == 200
    assert client.post("/api/v1/admin/monthly-reports").status_code == 200


def test_export_final_failure_is_safe_and_notifies_once(app, artifact_dir):
    user_id = _make_export_data(app)
    blocker = artifact_dir / "file-not-directory"
    blocker.write_text("blocked", encoding="utf-8")
    app.config["EXPORT_DIR"] = blocker / "exports"
    job_id = "b788ced7-15d1-44fc-8991-3e46c51ef22e"
    with app.app_context():
        db.session.add(ExportJob(job_id=job_id, user_id=user_id)); db.session.commit()
        result = export_student_history.apply(args=[job_id], throw=False, retries=export_student_history.max_retries)
        job = ExportJob.query.filter_by(job_id=job_id).one()
        assert result.failed() and job.status == "failed"
        assert job.safe_error and "blocked" not in job.safe_error
        assert Notification.query.filter_by(user_id=user_id).count() == 1


def test_artifact_cleanup_keeps_history_and_expires_files(app, artifact_dir):
    user_id = _make_export_data(app)
    exports, reports = artifact_dir / "exports", artifact_dir / "reports"
    exports.mkdir(); reports.mkdir()
    old_export, old_report = exports / "old.csv", reports / "old.html"
    old_export.write_text("csv", encoding="utf-8")
    old_report.write_text("html", encoding="utf-8")
    now = datetime.now(timezone.utc)
    with app.app_context():
        app.config.update(EXPORT_DIR=exports, REPORT_DIR=reports, EXPORT_RETENTION_DAYS=7, REPORT_RETENTION_DAYS=365)
        db.session.add(ExportJob(job_id="b87c984d-4ee8-438f-b5e3-a8050102f9c3", user_id=user_id,
            status="completed", artifact_name="old.csv", completed_at=now-timedelta(days=8)))
        db.session.add(MonthlyReport(period_start=date(2025, 1, 1), period_end=date(2025, 1, 31),
            status="completed", artifact_name="old.html", generated_at=now-timedelta(days=366)))
        db.session.commit()
        assert cleanup_expired_artifacts() == 2
        assert not old_export.exists() and not old_report.exists()
        assert ExportJob.query.one().status == "expired"
        assert MonthlyReport.query.one().status == "expired"


def test_relative_artifact_directories_are_anchored_to_instance(app):
    from config import INSTANCE_DIR
    assert app.config["EXPORT_DIR"] == (INSTANCE_DIR / "exports").resolve()
    assert app.config["REPORT_DIR"] == (INSTANCE_DIR / "reports").resolve()


def test_export_ready_email_has_html_and_plain_text_content():
    from app.services.email_templates import export_ready_email
    text, markup = export_ready_email("A Student", "history.csv", "2026-11-14", "http://localhost:5173/dashboard")
    assert "Your Exam History Export Is Ready" in markup
    assert "history.csv" in text and "A Student" in text
    assert "dashboard" in markup
    assert "protected" in markup.lower()
