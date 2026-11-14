import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

from flask import current_app, jsonify, request, send_file
from sqlalchemy import func

from ..extensions import db
from ..models import ExportJob, MonthlyReport, Notification
from ..tasks import export_student_history, generate_monthly_report
from . import api
from .routes import current_user, error, login_required


def _date(value):
    return value.isoformat() if value else None


def _export_json(job):
    return {"job_id": job.job_id, "status": job.status, "created_at": _date(job.created_at),
            "started_at": _date(job.started_at), "completed_at": _date(job.completed_at),
            "safe_error": job.safe_error, "download_url": f"/student/exports/{job.job_id}/download" if job.status == "completed" else None}


def _report_json(report):
    return {"id": report.id, "period_start": _date(report.period_start), "period_end": _date(report.period_end),
            "status": report.status, "delivery_status": report.delivery_status,
            "created_at": _date(report.created_at), "generated_at": _date(report.generated_at),
            "summary": json.loads(report.summary_json) if report.summary_json else None,
            "safe_error": report.safe_error, "download_url": f"/admin/monthly-reports/{report.id}/download" if report.artifact_name else None}


@api.route("/student/exports", methods=["GET", "POST"])
@login_required("student")
def student_exports():
    if request.method == "GET":
        rows = ExportJob.query.filter_by(user_id=current_user().id).order_by(ExportJob.created_at.desc()).limit(50).all()
        return jsonify({"items": [_export_json(row) for row in rows]})
    job = ExportJob(job_id=str(uuid.uuid4()), user_id=current_user().id, status="requested")
    db.session.add(job)
    db.session.commit()
    try:
        export_student_history.delay(job.job_id)
    except Exception as exc:
        job = ExportJob.query.filter_by(job_id=job.job_id).first()
        job.status, job.safe_error, job.completed_at = "failed", "The export could not be queued because the background worker is unavailable.", datetime.now(timezone.utc)
        db.session.add(Notification(user_id=job.user_id, message="Your examination history export could not be queued. Please try again."))
        db.session.commit()
        current_app.logger.exception("Could not queue student export (%s)", type(exc).__name__)
    db.session.refresh(job)
    return jsonify({"item": _export_json(job)}), 202


@api.get("/student/exports/<string:job_id>/download")
@login_required("student")
def download_student_export(job_id):
    job = ExportJob.query.filter_by(job_id=job_id, user_id=current_user().id).first()
    if not job:
        return error("Export job not found", 404)
    if job.status != "completed" or not job.artifact_name:
        return error("Export is not ready to download", 409)
    root = Path(current_app.config["EXPORT_DIR"]).resolve()
    path = (root / Path(job.artifact_name).name).resolve()
    if path.parent != root or not path.is_file():
        return error("Export file is unavailable", 404)
    response = send_file(path, mimetype="text/csv", as_attachment=True, download_name="examination-history.csv", max_age=0)
    response.headers["Cache-Control"] = "private, no-store"
    return response


@api.get("/admin/monthly-reports")
@login_required("admin")
def monthly_reports():
    rows = MonthlyReport.query.order_by(MonthlyReport.period_start.desc()).limit(100).all()
    return jsonify({"items": [_report_json(row) for row in rows]})


@api.post("/admin/monthly-reports")
@login_required("admin")
def request_monthly_report():
    # A manual run follows the same reporting period as the first-of-month schedule.
    from ..tasks import _period_for_previous_month
    start, end = _period_for_previous_month()
    report = MonthlyReport.query.filter_by(period_start=start).first()
    if report and report.status in {"queued", "processing", "completed"}:
        return jsonify({"item": _report_json(report)}), 200
    if not report:
        report = MonthlyReport(period_start=start, period_end=end, status="queued")
        db.session.add(report)
        db.session.commit()
    else:
        report.status, report.safe_error = "queued", None
        db.session.commit()
    try:
        generate_monthly_report.delay(report.id)
    except Exception as exc:
        db.session.rollback()
        report = db.session.get(MonthlyReport, report.id)
        report.status, report.safe_error = "failed", "The report could not be queued because the background worker is unavailable."
        db.session.commit()
        current_app.logger.exception("Could not queue monthly report (%s)", type(exc).__name__)
    db.session.refresh(report)
    return jsonify({"item": _report_json(report)}), 202


@api.get("/admin/monthly-reports/<int:report_id>")
@login_required("admin")
def monthly_report_detail(report_id):
    report = db.session.get(MonthlyReport, report_id)
    if not report:
        return error("Monthly report not found", 404)
    return jsonify({"item": _report_json(report)})


@api.get("/admin/monthly-reports/<int:report_id>/download")
@login_required("admin")
def download_monthly_report(report_id):
    report = db.session.get(MonthlyReport, report_id)
    if not report or not report.artifact_name:
        return error("Monthly report artifact not found", 404)
    root = Path(current_app.config["REPORT_DIR"]).resolve()
    path = (root / Path(report.artifact_name).name).resolve()
    if path.parent != root or not path.is_file():
        return error("Monthly report artifact not found", 404)
    response = send_file(path, mimetype="text/html", as_attachment=True,
                         download_name=f"examination-report-{report.period_start:%Y-%m}.html", max_age=0)
    response.headers["Cache-Control"] = "private, no-store"
    return response


@api.get("/admin/jobs/summary")
@login_required("admin")
def background_job_summary():
    exports = {status: count for status, count in db.session.query(ExportJob.status, func.count(ExportJob.id)).group_by(ExportJob.status).all()}
    reports = {status: count for status, count in db.session.query(MonthlyReport.status, func.count(MonthlyReport.id)).group_by(MonthlyReport.status).all()}
    return jsonify({"exports": exports, "reports": reports,
                    "latest_reports": [_report_json(row) for row in MonthlyReport.query.order_by(MonthlyReport.created_at.desc()).limit(5).all()]})
