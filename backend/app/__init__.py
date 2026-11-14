from flask import Flask, jsonify
from flask_cors import CORS
from .extensions import db, csrf


def upgrade_sqlite_schema():
    """Add Milestone 2 columns to an existing Milestone 1 SQLite database."""
    additions = {
        "student_profile": [("department", "VARCHAR(120)")],
        "course": [("description", "TEXT"), ("status", "VARCHAR(20) NOT NULL DEFAULT 'active'"),
                   ("updated_at", "DATETIME")],
        "examination": [("exam_type", "VARCHAR(30) NOT NULL DEFAULT 'Viva'"),
                        ("duration_minutes", "INTEGER NOT NULL DEFAULT 30"),
                        ("max_marks", "FLOAT NOT NULL DEFAULT 100"),
                        ("slot_creation_start", "DATETIME"), ("slot_creation_end", "DATETIME"),
                        ("booking_start", "DATETIME"), ("booking_end", "DATETIME"),
                        ("updated_at", "DATETIME")],
        "rubric_criterion": [("description", "TEXT"), ("weightage", "FLOAT NOT NULL DEFAULT 0")],
        "audit_log": [("metadata_json", "TEXT")],
        "examination_slot": [("meeting_link", "VARCHAR(500)"), ("reserved_count", "INTEGER NOT NULL DEFAULT 0")],
        "evaluation": [("submitted_at", "DATETIME")],
        "booking": [("examination_id", "INTEGER REFERENCES examination(id) ON DELETE CASCADE")],
    }
    inspector = db.inspect(db.engine)
    tables = set(inspector.get_table_names())
    with db.engine.begin() as connection:
        for table, columns in additions.items():
            if table not in tables:
                continue
            present = {column["name"] for column in db.inspect(db.engine).get_columns(table)}
            for name, definition in columns:
                if name not in present:
                    connection.exec_driver_sql(f'ALTER TABLE "{table}" ADD COLUMN "{name}" {definition}')
        if "examination" in tables:
            connection.exec_driver_sql("UPDATE examination SET status='booking_open' WHERE status='published'")
        if "examination_slot" in tables and "booking" in tables:
            connection.exec_driver_sql("UPDATE booking SET examination_id=(SELECT examination_id FROM examination_slot WHERE examination_slot.id=booking.slot_id) WHERE examination_id IS NULL")
            connection.exec_driver_sql("UPDATE examination_slot SET reserved_count=(SELECT COUNT(*) FROM booking WHERE booking.slot_id=examination_slot.id AND booking.status IN ('booked','completed'))")
            connection.exec_driver_sql("CREATE UNIQUE INDEX IF NOT EXISTS uq_active_student_exam ON booking(student_id, examination_id) WHERE status='booked'")


def create_app(config_object=None):
    app = Flask(__name__)
    app.config.from_object(config_object or "config.Config")
    db.init_app(app)
    csrf.init_app(app)
    CORS(app, origins=[app.config["FRONTEND_ORIGIN"]], supports_credentials=True, allow_headers=["Content-Type", "X-CSRFToken"], methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"])
    from . import models  # noqa: F401
    from .celery_app import init_celery
    init_celery(app)
    from .api import api
    app.register_blueprint(api)

    @app.before_request
    def protect_mutations():
        # Flask-WTF validates the CSRF header when a session exists. Auth endpoints
        # exempted because no authenticated session exists before login/register.
        from flask import request
        from flask_wtf.csrf import validate_csrf
        if app.config.get("WTF_CSRF_ENABLED", True) and request.method in {"POST", "PATCH", "PUT", "DELETE"} and request.endpoint not in {"api.login", "api.register", "api.csrf_token"}:
            token = request.headers.get("X-CSRFToken")
            if not token:
                return jsonify({"error": {"message": "CSRF token missing", "status": 400}}), 400
            try:
                validate_csrf(token)
            except Exception:
                return jsonify({"error": {"message": "CSRF token invalid", "status": 400}}), 400

    @app.errorhandler(404)
    def not_found(_):
        return jsonify({"error": {"message": "Resource not found", "status": 404}}), 404

    @app.errorhandler(500)
    def server_error(_):
        db.session.rollback()
        return jsonify({"error": {"message": "Internal server error", "status": 500}}), 500

    @app.errorhandler(413)
    def upload_too_large(_):
        return jsonify({"error": {"message": "Uploaded document must be 10 MB or smaller", "status": 413}}), 413

    @app.cli.command("init-db")
    def init_db_command():
        with app.app_context():
            upgrade_sqlite_schema()
            db.create_all()
            upgrade_sqlite_schema()
        print("Database tables created.")

    @app.cli.command("seed-admin")
    def seed_admin_command():
        from .services.auth import seed_admin
        with app.app_context():
            upgrade_sqlite_schema()
            db.create_all()
            upgrade_sqlite_schema()
            seed_admin(app.config["ADMIN_EMAIL"], app.config["ADMIN_PASSWORD"])
        print("Initial admin is present.")

    @app.cli.command("init-data")
    def init_data_command():
        from .services.auth import seed_admin
        with app.app_context():
            upgrade_sqlite_schema()
            db.create_all()
            upgrade_sqlite_schema()
            seed_admin(app.config["ADMIN_EMAIL"], app.config["ADMIN_PASSWORD"])
        print("Database initialized and initial admin ensured.")

    @app.cli.command("seed-demo")
    def seed_demo_command():
        """Add a small demo course/exam catalogue without changing accounts."""
        from .services.demo_data import seed_demo_data
        with app.app_context():
            upgrade_sqlite_schema()
            db.create_all()
            counts = seed_demo_data()
        print("Demo data added: " + ", ".join(f"{key}={value}" for key, value in counts.items()))

    @app.cli.command("reset-admin")
    def reset_admin_command():
        """Reset the first Admin account to the credentials in backend/.env."""
        import click
        from email_validator import validate_email, EmailNotValidError
        from sqlalchemy.exc import IntegrityError
        from .models import AuditLog, User
        from .services.auth import seed_admin

        try:
            email = validate_email(app.config["ADMIN_EMAIL"], check_deliverability=False).normalized
        except (EmailNotValidError, TypeError):
            raise click.ClickException("ADMIN_EMAIL must be a valid email address")
        password = app.config["ADMIN_PASSWORD"]
        if email == "admin@example.com" or password == "ChangeMe123!":
            raise click.ClickException("Set a private ADMIN_EMAIL and ADMIN_PASSWORD in backend/.env before resetting credentials")
        if not isinstance(password, str) or not 10 <= len(password) <= 128:
            raise click.ClickException("ADMIN_PASSWORD must be between 10 and 128 characters")

        with app.app_context():
            upgrade_sqlite_schema()
            db.create_all()
            upgrade_sqlite_schema()
            admin_user = User.query.filter_by(role="admin").order_by(User.id).first()
            if not admin_user:
                seed_admin(email, password)
            else:
                duplicate = User.query.filter(User.email == email, User.id != admin_user.id).first()
                if duplicate:
                    raise click.ClickException("ADMIN_EMAIL is already used by a non-target account")
                email_changed = admin_user.email != email
                admin_user.email = email
                admin_user.set_password(password)
                db.session.add(AuditLog(actor_id=None, action="admin.credentials_reset", entity_type="user",
                                        entity_id=admin_user.id,
                                        metadata_json='{"email_changed": %s, "password_changed": true}' % str(email_changed).lower()))
                try:
                    db.session.commit()
                except IntegrityError:
                    db.session.rollback()
                    raise click.ClickException("Could not reset credentials because the email address is already in use")
        print(f"Admin credentials reset for {email}.")

    return app
