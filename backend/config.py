import os
from pathlib import Path

from dotenv import load_dotenv

INSTANCE_DIR = Path(__file__).resolve().parent / "instance"
load_dotenv(Path(__file__).resolve().parent / ".env", override=False)


def _artifact_dir(value, default):
    path = Path(value) if value else Path(default)
    return (path if path.is_absolute() else INSTANCE_DIR / path).resolve()


class Config:
    SECRET_KEY = os.getenv("SECRET_KEY", "local-only-change-me")
    SQLALCHEMY_DATABASE_URI = os.getenv("DATABASE_URL", "sqlite:///emp.db")
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = os.getenv("COOKIE_SECURE", "false").lower() == "true"
    WTF_CSRF_ENABLED = True
    WTF_CSRF_CHECK_DEFAULT = False
    FRONTEND_ORIGIN = os.getenv("FRONTEND_ORIGIN", "http://localhost:5173")
    REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    CELERY_BROKER_URL = os.getenv("CELERY_BROKER_URL", REDIS_URL)
    CELERY_RESULT_BACKEND = os.getenv("CELERY_RESULT_BACKEND", REDIS_URL)
    CELERY_TASK_DEFAULT_RETRY_DELAY = int(os.getenv("CELERY_TASK_DEFAULT_RETRY_DELAY", "60"))
    CELERY_TASK_MAX_RETRIES = int(os.getenv("CELERY_TASK_MAX_RETRIES", "3"))
    CELERY_TASK_ALWAYS_EAGER = os.getenv("CELERY_TASK_ALWAYS_EAGER", "false").lower() == "true"
    CELERY_TASK_SOFT_TIME_LIMIT = int(os.getenv("CELERY_TASK_SOFT_TIME_LIMIT", "240"))
    CELERY_TASK_TIME_LIMIT = int(os.getenv("CELERY_TASK_TIME_LIMIT", "300"))
    CELERY_BEAT_TIMEZONE = os.getenv("APP_TIMEZONE", "Asia/Kolkata")
    REMINDER_HOUR = int(os.getenv("REMINDER_HOUR", "8"))
    MONTHLY_REPORT_HOUR = int(os.getenv("MONTHLY_REPORT_HOUR", "1"))
    REMINDER_DELIVERY = os.getenv("REMINDER_DELIVERY", "email")
    SMTP_HOST = os.getenv("SMTP_HOST")
    SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
    SMTP_USERNAME = os.getenv("SMTP_USERNAME")
    SMTP_PASSWORD = os.getenv("SMTP_PASSWORD")
    SMTP_FROM = os.getenv("SMTP_FROM")
    REPORT_EMAIL = os.getenv("REPORT_EMAIL")
    EMAIL_PROVIDER = os.getenv("EMAIL_PROVIDER", "smtp").strip().lower()
    GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID")
    GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET")
    GOOGLE_REDIRECT_URI = os.getenv("GOOGLE_REDIRECT_URI", "http://localhost:5000/api/v1/admin/google-mail/callback")
    GOOGLE_TOKEN_ENCRYPTION_KEY = os.getenv("GOOGLE_TOKEN_ENCRYPTION_KEY")
    SUBMISSION_DIR = Path(os.getenv("SUBMISSION_DIR") or str(INSTANCE_DIR / "submissions")).resolve()
    MAX_SUBMISSION_BYTES = 10 * 1024 * 1024
    MAX_CONTENT_LENGTH = MAX_SUBMISSION_BYTES + 64 * 1024
    DEFAULT_EXAM_INSTRUCTIONS = os.getenv("DEFAULT_EXAM_INSTRUCTIONS",
        "Please arrive 10 minutes before your slot, bring your institution ID, and follow the assigned Examiner's instructions.")
    CACHE_ENABLED = os.getenv("CACHE_ENABLED", "true").lower() == "true"
    CACHE_DEFAULT_TIMEOUT = int(os.getenv("CACHE_DEFAULT_TIMEOUT", "300"))
    EXPORT_DIR = _artifact_dir(os.getenv("EXPORT_DIR"), "exports")
    REPORT_DIR = _artifact_dir(os.getenv("REPORT_DIR"), "reports")
    EXPORT_RETENTION_DAYS = int(os.getenv("EXPORT_RETENTION_DAYS", "7"))
    REPORT_RETENTION_DAYS = int(os.getenv("REPORT_RETENTION_DAYS", "365"))
    ADMIN_EMAIL = os.getenv("ADMIN_EMAIL", "admin@example.com")
    ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "ChangeMe123!")


class TestingConfig(Config):
    TESTING = True
    WTF_CSRF_ENABLED = False
    SQLALCHEMY_DATABASE_URI = "sqlite://"
    SESSION_COOKIE_SECURE = False
    CACHE_ENABLED = False
    EMAIL_PROVIDER = "smtp"
    CELERY_TASK_ALWAYS_EAGER = True
    CELERY_TASK_EAGER_PROPAGATES = True
