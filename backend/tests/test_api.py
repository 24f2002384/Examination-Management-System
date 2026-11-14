from app.extensions import db
from app.models import User, Course, Examination
from app.services.auth import seed_admin


def csrf(client):
    return client.get("/api/v1/auth/csrf").json["csrf_token"]


def test_database_and_admin_seed_are_idempotent(app):
    with app.app_context():
        seed_admin("admin@example.com", "StrongPassword123!")
        seed_admin("admin@example.com", "StrongPassword123!")
        assert User.query.filter_by(role="admin").count() == 1
        assert User.query.first().check_password("StrongPassword123!")


def test_registration_login_and_duplicate_email(client, monkeypatch):
    sent = []
    monkeypatch.setattr("app.api.routes.send_notice", lambda *args: sent.append(args) or True)
    response = client.post("/api/v1/auth/register", json={"full_name": "A Student", "email": "student@example.com", "password": "StrongPassword123!", "role": "admin"})
    assert response.status_code == 201
    assert response.json["user"]["role"] == "student"
    assert len(sent) == 1 and sent[0][0] == "student@example.com"
    assert client.post("/api/v1/auth/logout", headers={"X-CSRFToken": csrf(client)}).status_code == 200
    assert client.post("/api/v1/auth/login", json={"email": "student@example.com", "password": "wrong-password"}).status_code == 401
    assert client.post("/api/v1/auth/login", json={"email": "student@example.com", "password": "StrongPassword123!"}).status_code == 200
    assert client.post("/api/v1/auth/login", json={"email": "student@example.com", "password": "StrongPassword123!", "role": "admin"}).status_code == 401
    assert client.post("/api/v1/auth/register", json={"full_name": "Other Student", "email": "student@example.com", "password": "StrongPassword123!"}).status_code == 409
    assert len(sent) == 1


def test_registration_succeeds_when_welcome_email_fails(client, monkeypatch):
    monkeypatch.setattr("app.api.routes.send_notice", lambda *args: False)
    response = client.post("/api/v1/auth/register", json={"full_name": "Mail Failure", "email": "mail-failure@example.com", "password": "StrongPassword123!"})
    assert response.status_code == 201
    assert client.post("/api/v1/auth/login", json={"email": "mail-failure@example.com", "password": "StrongPassword123!"}).status_code == 200


def test_demo_catalogue_is_additive_and_idempotent(app):
    from app.services.demo_data import seed_demo_data
    with app.app_context():
        first = seed_demo_data()
        second = seed_demo_data()
        assert first == {"courses": 3, "examinations": 4, "rubric_criteria": 12}
        assert second == {"courses": 0, "examinations": 0, "rubric_criteria": 0}
        assert User.query.filter_by(role="examiner").count() == 0


def test_protected_endpoints_roles_and_student_dashboard(app, client):
    assert client.get("/api/v1/dashboard").status_code == 401
    with app.app_context():
        seed_admin("admin@example.com", "StrongPassword123!")
    client.post("/api/v1/auth/login", json={"email": "admin@example.com", "password": "StrongPassword123!"})
    assert client.get("/api/v1/admin/overview").status_code == 200
    assert client.get("/api/v1/examiners").status_code == 200
    assert client.get("/api/v1/dashboard").json["stats"]["students"] == 0
    client.post("/api/v1/auth/logout", headers={"X-CSRFToken": csrf(client)})
    client.post("/api/v1/auth/register", json={"full_name": "A Student", "email": "student@example.com", "password": "StrongPassword123!"})
    assert client.get("/api/v1/admin/overview").status_code == 403
    dash = client.get("/api/v1/dashboard")
    assert dash.status_code == 200 and dash.json["role"] == "student"


def test_health_and_currently_bookable_exam_count(app, client):
    assert client.get("/api/v1/health").json == {"status": "ok", "database": "ok"}
    client.post("/api/v1/auth/register", json={"full_name": "A Student", "email": "student@example.com", "password": "StrongPassword123!"})
    with app.app_context():
        course = Course(code="CS101", name="Computer Science")
        db.session.add(course)
        db.session.flush()
        from datetime import datetime, timedelta, timezone
        now = datetime.now(timezone.utc)
        db.session.add(Examination(course_id=course.id, title="Test Exam", status="booking_open",
            booking_start=now-timedelta(hours=1), booking_end=now+timedelta(hours=1)))
        db.session.commit()
    assert client.get("/api/v1/dashboard").json["stats"]["available_examinations"] == 1


def test_frontend_cors_allows_portal_mutation_methods(client):
    for path, method in [
        ("/api/v1/examiner/bookings/1/evaluation", "PUT"),
        ("/api/v1/examiner/slots/1", "DELETE"),
    ]:
        response = client.options(path, headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": method,
            "Access-Control-Request-Headers": "content-type,x-csrftoken",
        })
        assert response.status_code == 200
        assert method in response.headers["Access-Control-Allow-Methods"]
