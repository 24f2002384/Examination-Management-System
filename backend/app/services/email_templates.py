"""Reusable ExamWise transactional email templates."""
import html
from datetime import datetime, timezone


def export_ready_email(name, filename, requested_at=None, dashboard_url=None):
    display_name = html.escape(name or "Student")
    safe_filename = html.escape(filename or "examination-history.csv")
    requested = requested_at or datetime.now(timezone.utc).strftime("%d %b %Y %H:%M UTC")
    safe_requested = html.escape(str(requested))
    url = html.escape(dashboard_url or "", quote=True)
    action = (f'<p><a href="{url}" style="display:inline-block;padding:12px 20px;background:#245a8d;color:#fff;text-decoration:none;border-radius:6px;font-weight:700">Open ExamWise Dashboard</a></p>'
              if url else "")
    text = (f"Hello {name or 'Student'},\n\nYour ExamWise examination history export is ready.\n"
            f"Filename: {filename or 'examination-history.csv'}\nRequested: {requested}\n\n"
            "Sign in to your ExamWise dashboard to download the CSV.\n"
            "For your security, the export is available only after authentication.\n\nExamWise")
    html_body = f"""<!doctype html><html><body style="margin:0;background:#f3f6fa;font-family:Arial,sans-serif;color:#243b53">
<div style="max-width:620px;margin:24px auto;background:#fff;border:1px solid #dfe7ef;border-radius:10px;overflow:hidden">
<header style="background:#163a63;color:#fff;padding:24px 28px"><strong style="font-size:22px">ExamWise</strong><div style="margin-top:6px;color:#cfe0f0">Examination Management Portal</div></header>
<main style="padding:28px"><h1 style="font-size:24px;margin:0 0 16px;color:#163a63">Your Exam History Export Is Ready</h1>
<p>Hello {display_name},</p><p>Your requested examination history CSV has been generated and is ready in your ExamWise dashboard.</p>{action}
<section style="background:#f3f6fa;border-radius:7px;padding:14px 16px;margin:20px 0"><strong>Export details</strong><p style="margin:8px 0 0">Filename: {safe_filename}<br>Requested: {safe_requested}</p></section>
<p style="font-size:13px;color:#52677e">For your privacy, exports are protected by your ExamWise login and are not exposed through a public link.</p></main>
<footer style="padding:18px 28px;border-top:1px solid #e5ebf1;font-size:12px;color:#718096">ExamWise · Examination Management Portal</footer></div></body></html>"""
    return text, html_body
