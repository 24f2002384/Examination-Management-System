from celery import Celery
from celery.schedules import crontab

celery = Celery("examination_portal")


def init_celery(app):
    celery.flask_app = app
    celery.conf.update(
        broker_url=app.config["CELERY_BROKER_URL"],
        result_backend=app.config["CELERY_RESULT_BACKEND"],
        task_default_retry_delay=app.config["CELERY_TASK_DEFAULT_RETRY_DELAY"],
        task_soft_time_limit=app.config["CELERY_TASK_SOFT_TIME_LIMIT"],
        task_time_limit=app.config["CELERY_TASK_TIME_LIMIT"],
        task_track_started=True,
        task_serializer="json", result_serializer="json", accept_content=["json"],
        timezone=app.config["CELERY_BEAT_TIMEZONE"], enable_utc=True,
        task_always_eager=app.config.get("CELERY_TASK_ALWAYS_EAGER", False),
        task_eager_propagates=app.config.get("CELERY_TASK_EAGER_PROPAGATES", False),
        beat_schedule={
            "daily-examination-reminders": {
                "task": "app.tasks.send_daily_reminders",
                "schedule": crontab(hour=app.config["REMINDER_HOUR"], minute=0),
            },
            "monthly-examination-report": {
                "task": "app.tasks.generate_monthly_report",
                "schedule": crontab(day_of_month=1, hour=app.config["MONTHLY_REPORT_HOUR"], minute=0),
            },
            "clean-expired-artifacts": {
                "task": "app.tasks.cleanup_expired_artifacts",
                "schedule": crontab(hour=2, minute=0),
            },
        },
    )

    if not getattr(celery, "_flask_context_task_installed", False):
        class FlaskContextTask(celery.Task):
            abstract = True

            def __call__(self, *args, **kwargs):
                with self.app.flask_app.app_context():
                    return self.run(*args, **kwargs)

        celery.Task = FlaskContextTask
        celery._flask_context_task_installed = True
    from . import tasks  # noqa: F401,E402
    app.extensions["celery"] = celery
    return celery
