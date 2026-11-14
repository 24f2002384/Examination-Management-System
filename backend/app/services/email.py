"""SMTP delivery shared by transactional notices and scheduled jobs."""
import smtplib
from email.message import EmailMessage

from flask import current_app


def delivery_succeeded(result):
    return isinstance(result, dict) and result.get("delivered") is True


def send_email(to, subject, body, html_body=None):
    cfg = current_app.config
    if cfg.get("EMAIL_PROVIDER", "smtp").strip().lower() == "gmail_api":
        from .gmail_email import send_gmail_email
        return send_gmail_email(to, subject, body, html_body=html_body)
    if not (cfg.get("SMTP_HOST") and cfg.get("SMTP_FROM") and to):
        return {"delivered": False, "provider": "smtp", "reason": "SMTP is not configured or recipient is missing"}
    message = EmailMessage()
    message["From"], message["To"], message["Subject"] = cfg["SMTP_FROM"], to, subject
    message.set_content(body)
    if html_body:
        message.add_alternative(html_body, subtype="html")
    if cfg["SMTP_PORT"] == 465:
        with smtplib.SMTP_SSL(cfg["SMTP_HOST"], cfg["SMTP_PORT"], timeout=15) as smtp:
            if cfg.get("SMTP_USERNAME"):
                smtp.login(cfg["SMTP_USERNAME"], cfg.get("SMTP_PASSWORD") or "")
            smtp.send_message(message)
    else:
        with smtplib.SMTP(cfg["SMTP_HOST"], cfg["SMTP_PORT"], timeout=15) as smtp:
            smtp.starttls()
            if cfg.get("SMTP_USERNAME"):
                smtp.login(cfg["SMTP_USERNAME"], cfg.get("SMTP_PASSWORD") or "")
            smtp.send_message(message)
    return {"delivered": True, "provider": "smtp"}


def send_notice(to, subject, body):
    """Send optional email without failing the committed portal action."""
    try:
        return send_email(to, subject, body)
    except Exception as exc:
        current_app.logger.exception("Email notification failed (%s)", type(exc).__name__)
        return {"delivered": False, "provider": current_app.config.get("EMAIL_PROVIDER", "smtp"),
                "reason": f"{type(exc).__name__}: {str(exc)[:240]}"}
