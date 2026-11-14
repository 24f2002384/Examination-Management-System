from app.extensions import db
from app.models import User, Course, Examination, RubricCriterion, ExaminerProfile, ExaminationSlot, Booking, AuditLog
from app.services.auth import seed_admin

PASSWORD = "StrongPassword123!"


def login_admin(app, client):
    with app.app_context():
        seed_admin("admin@example.com", PASSWORD)
    client.post("/api/v1/auth/login", json={"email": "admin@example.com", "password": PASSWORD})
    return {"X-CSRFToken": client.get("/api/v1/auth/csrf").json["csrf_token"]}


def test_course_crud_permissions_uniqueness_and_audit(app, client):
    assert client.get("/api/v1/admin/courses").status_code == 401
    headers = login_admin(app, client)
    created = client.post("/api/v1/admin/courses", json={"code": "CS101", "name": "Computing"}, headers=headers)
    assert created.status_code == 201
    assert client.post("/api/v1/admin/courses", json={"code": "CS101", "name": "Duplicate"}, headers=headers).status_code == 409
    course_id = created.json["item"]["id"]
    assert client.patch(f"/api/v1/admin/courses/{course_id}", json={"name": "Computer Science"}, headers=headers).status_code == 200
    assert client.get("/api/v1/admin/courses?search=Computer", headers=headers).json["pagination"]["total"] == 1
    with app.app_context():
        assert AuditLog.query.filter_by(entity_type="course").count() >= 2


def test_admin_can_change_email_and_password_after_current_password_check(app, client):
    headers = login_admin(app, client)
    invalid = client.patch("/api/v1/admin/settings", json={
        "email": "admin-new@example.com", "current_password": "incorrect-password",
        "new_password": "NewStrongPassword456!", "confirm_password": "NewStrongPassword456!",
    }, headers=headers)
    assert invalid.status_code == 400

    changed = client.patch("/api/v1/admin/settings", json={
        "email": "admin-new@example.com", "current_password": PASSWORD,
        "new_password": "NewStrongPassword456!", "confirm_password": "NewStrongPassword456!",
    }, headers=headers)
    assert changed.status_code == 200
    assert changed.json["profile"]["email"] == "admin-new@example.com"
    assert client.post("/api/v1/auth/login", json={"email": "admin-new@example.com", "password": PASSWORD}).status_code == 401
    assert client.post("/api/v1/auth/login", json={"email": "admin-new@example.com", "password": "NewStrongPassword456!"}).status_code == 200
    with app.app_context():
        entry = AuditLog.query.filter_by(action="settings.updated").order_by(AuditLog.id.desc()).first()
        assert entry
        assert "new_password" not in entry.metadata_json
        assert "current_password" not in entry.metadata_json


def test_profile_email_change_requires_current_password(app, client):
    headers = login_admin(app, client)
    denied = client.patch("/api/v1/profile", json={
        "full_name": "System Administrator", "email": "another-admin@example.com",
    }, headers=headers)
    assert denied.status_code == 400
    assert "current password" in denied.json["error"]["message"].lower()

    allowed = client.patch("/api/v1/profile", json={
        "full_name": "System Administrator", "email": "another-admin@example.com",
        "current_password": PASSWORD,
    }, headers=headers)
    assert allowed.status_code == 200
    assert allowed.json["user"]["email"] == "another-admin@example.com"


def test_reset_admin_cli_rotates_credentials_from_configuration(app):
    with app.app_context():
        original_admin = seed_admin("admin@example.com", PASSWORD)
        examiner = User(full_name="Unchanged Examiner", email="examiner-unchanged@example.com", role="examiner")
        examiner.set_password(PASSWORD)
        db.session.add(examiner)
        db.session.commit()
        admin_id, examiner_id = original_admin.id, examiner.id
        examiner_hash = examiner.password_hash
    app.config["ADMIN_EMAIL"] = "recovered-admin@example.com"
    app.config["ADMIN_PASSWORD"] = "RecoveredPassword789!"

    result = app.test_cli_runner().invoke(args=["reset-admin"])
    assert result.exit_code == 0, result.output
    with app.app_context():
        admin = db.session.get(User, admin_id)
        assert admin and admin.role == "admin"
        assert admin.email == "recovered-admin@example.com"
        assert admin.check_password("RecoveredPassword789!")
        assert not admin.check_password(PASSWORD)
        unchanged_examiner = db.session.get(User, examiner_id)
        assert unchanged_examiner and unchanged_examiner.email == "examiner-unchanged@example.com"
        assert unchanged_examiner.password_hash == examiner_hash

    login_response = app.test_client().post("/api/v1/auth/login", json={
        "email": app.config["ADMIN_EMAIL"], "password": app.config["ADMIN_PASSWORD"],
    })
    assert login_response.status_code == 200
    assert login_response.json["user"]["role"] == "admin"


def test_examination_validation_lifecycle_and_rubric_rules(app, client):
    headers = login_admin(app, client)
    course = client.post("/api/v1/admin/courses", json={"code": "BIO1", "name": "Biology"}, headers=headers).json["item"]
    bad = {"course_id": course["id"], "title": "Oral", "type": "Viva", "duration_minutes": 30, "max_marks": 50}
    assert client.post("/api/v1/admin/examinations", json={**bad, "duration_minutes": 0}, headers=headers).status_code == 400
    good = {**bad, "slot_creation_start": "2030-01-01T09:00:00Z", "slot_creation_end": "2030-01-02T09:00:00Z", "booking_start": "2030-01-02T09:00:00Z", "booking_end": "2030-01-03T09:00:00Z"}
    exam = client.post("/api/v1/admin/examinations", json=good, headers=headers)
    assert exam.status_code == 201
    assert exam.json["item"]["description"] == app.config["DEFAULT_EXAM_INSTRUCTIONS"]
    exam_id = exam.json["item"]["id"]
    assert client.post(f"/api/v1/admin/examinations/{exam_id}/lifecycle", json={"status": "booking_open"}, headers=headers).status_code == 400
    assert client.post(f"/api/v1/admin/examinations/{exam_id}/lifecycle", json={"status": "slot_creation"}, headers=headers).status_code == 200
    assert client.post(f"/api/v1/admin/examinations/{exam_id}/lifecycle", json={"status": "booking_open"}, headers=headers).status_code == 200
    edited = client.patch(f"/api/v1/admin/examinations/{exam_id}", json={"title": "Updated Oral", "description": "Bring the required project materials."}, headers=headers)
    assert edited.status_code == 200
    assert edited.json["item"]["title"] == "Updated Oral"
    assert edited.json["item"]["status"] == "booking_open"
    assert edited.json["item"]["description"] == "Bring the required project materials."
    rubric = client.post(f"/api/v1/admin/examinations/{exam_id}/rubric", json={"name": "Knowledge", "max_score": 40, "weightage": 80}, headers=headers)
    assert rubric.status_code == 201
    assert client.post(f"/api/v1/admin/examinations/{exam_id}/rubric", json={"name": "Overflow", "max_score": 20, "weightage": 20}, headers=headers).status_code == 400


def test_examiner_student_permissions_and_report_aggregates(app, client):
    headers = login_admin(app, client)
    examiner = client.post("/api/v1/admin/examiners", json={"full_name": "Examiner One", "email": "examiner@example.com", "department": "Science"}, headers=headers)
    assert examiner.status_code == 201
    examiner_id = examiner.json["item"]["id"]
    assert examiner.json["initial_password"]
    assert client.patch(f"/api/v1/admin/examiners/{examiner_id}", json={"is_active": False}, headers=headers).status_code == 200
    assert client.post("/api/v1/auth/login", json={"email": "examiner@example.com", "password": examiner.json["initial_password"]}).status_code == 401
    client.post("/api/v1/auth/register", json={"full_name": "Student One", "email": "student@example.com", "password": PASSWORD})
    client.post("/api/v1/auth/logout", headers={"X-CSRFToken": client.get("/api/v1/auth/csrf").json["csrf_token"]})
    headers = login_admin(app, client)
    students = client.get("/api/v1/admin/students?search=Student", headers=headers)
    assert students.status_code == 200 and students.json["pagination"]["total"] == 1
    assert client.get("/api/v1/admin/reports/summary", headers=headers).json["totals"]["students"] == 1
    assert client.get("/api/v1/admin/audit", headers=headers).status_code == 200


def test_slot_capacity_reassignment_and_duplicate_booking_conflict(app, client):
    headers = login_admin(app, client)
    with app.app_context():
        seed_admin("admin@example.com", PASSWORD)
        course = Course(code="MAT", name="Math")
        e1 = User(full_name="E One", email="e1@example.com", role="examiner"); e1.set_password(PASSWORD); e1.examiner_profile=ExaminerProfile(department="Math")
        e2 = User(full_name="E Two", email="e2@example.com", role="examiner"); e2.set_password(PASSWORD); e2.examiner_profile=ExaminerProfile(department="Math")
        student = User(full_name="Student", email="student@example.com", role="student"); student.set_password(PASSWORD)
        db.session.add_all([course,e1,e2,student]); db.session.flush()
        exam=Examination(course_id=course.id,title="Test",examiner_id=e1.id,status="booking_open")
        db.session.add(exam); db.session.flush()
        slot=ExaminationSlot(examination_id=exam.id,examiner_id=e1.id,starts_at=__import__('datetime').datetime(2030,1,1,9),ends_at=__import__('datetime').datetime(2030,1,1,10),capacity=1)
        db.session.add(slot); db.session.flush()
        booking=Booking(slot_id=slot.id,student_id=student.id,status="booked")
        db.session.add(booking); db.session.commit()
        slot_id, examiner_id = slot.id, e2.id
    assert client.patch(f"/api/v1/admin/slots/{slot_id}", json={"capacity": 0}, headers=headers).status_code == 409
    assert client.post(f"/api/v1/admin/slots/{slot_id}/reassign", json={"examiner_id": examiner_id}, headers=headers).status_code == 200
    assert client.get("/api/v1/admin/slots?per_page=10", headers=headers).json["items"][0]["booked_seats"] == 1
