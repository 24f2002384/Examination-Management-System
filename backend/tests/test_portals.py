from datetime import datetime, timedelta, timezone

from app.extensions import db
from app.models import Booking, Course, Examination, ExaminationSlot, User
from app.services.auth import seed_admin

PASSWORD = "StrongPassword123!"


def csrf(client):
    response=client.get("/api/v1/auth/csrf")
    return {"X-CSRFToken": response.json["csrf_token"]}


def sign_in(client, email, password):
    response = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200
    return csrf(client)


def prepare_exam(app, client):
    with app.app_context():
        seed_admin("admin@example.com", PASSWORD)
    headers = sign_in(client, "admin@example.com", PASSWORD)
    course = client.post("/api/v1/admin/courses", json={"code": "EMP201", "name": "Applied Systems"}, headers=headers).json["item"]
    examiner = client.post("/api/v1/admin/examiners", json={"full_name": "Dr Examiner", "email": "examiner@example.com"}, headers=headers).json
    now = datetime.now(timezone.utc)
    start = now - timedelta(minutes=2)
    slot_end = now + timedelta(hours=1)
    booking_end = now + timedelta(days=2)
    payload = {"course_id": course["id"], "title": "Systems Viva", "type": "Viva", "duration_minutes": 30,
        "max_marks": 20, "examiner_id": examiner["item"]["id"], "description": "Explain the design.",
        "slot_creation_start": start.isoformat(), "slot_creation_end": slot_end.isoformat(),
        "booking_start": slot_end.isoformat(), "booking_end": booking_end.isoformat()}
    exam = client.post("/api/v1/admin/examinations", json=payload, headers=headers).json["item"]
    exam_id = exam["id"]
    assert client.post(f"/api/v1/admin/examinations/{exam_id}/lifecycle", json={"status": "slot_creation"}, headers=headers).status_code == 200
    rubric = client.post(f"/api/v1/admin/examinations/{exam_id}/rubric", json={"name": "Reasoning", "description": "Clarity and logic", "max_score": 20, "weightage": 100}, headers=headers)
    assert rubric.status_code == 201
    return headers, examiner, exam_id


def create_slot(client, examiner, exam_id):
    headers=sign_in(client, "examiner@example.com", examiner["initial_password"])
    now = datetime.now(timezone.utc)
    response = client.post("/api/v1/examiner/slots", json={"examination_id": exam_id,
        "starts_at": (now+timedelta(hours=3)).isoformat(), "ends_at": (now+timedelta(hours=3, minutes=30)).isoformat(),
        "capacity": 1, "meeting_link": "https://meet.example.test/room"}, headers=headers)
    assert response.status_code == 201, response.json
    return response.json["item"]["id"]


def activate_booking_window(app, exam_id):
    # Simulate the clock moving beyond the configured slot-creation window.
    with app.app_context():
        exam = db.session.get(Examination, exam_id)
        exam.booking_start = datetime.now(timezone.utc)-timedelta(minutes=2)
        exam.booking_end = datetime.now(timezone.utc)+timedelta(days=2)
        exam.status = "booking_open"
        db.session.commit()


def test_examiner_ownership_slots_and_student_booking_consistency(app, monkeypatch):
    sent_emails = []
    monkeypatch.setattr("app.services.email.send_notice", lambda to, subject, body: sent_emails.append((to, subject)))
    admin_client = app.test_client()
    admin_headers, examiner, exam_id = prepare_exam(app, admin_client)
    examiner_client = app.test_client()
    wrong_window = sign_in(examiner_client, "examiner@example.com", examiner["initial_password"])
    now = datetime.now(timezone.utc)
    invalid_slot = examiner_client.post("/api/v1/examiner/slots", json={"examination_id": exam_id,
        "starts_at": (now+timedelta(hours=3)).isoformat(), "ends_at": (now+timedelta(hours=3,minutes=30)).isoformat(), "capacity": 0}, headers=wrong_window)
    assert invalid_slot.status_code == 400
    examiner_client.post("/api/v1/auth/logout", headers=csrf(examiner_client))
    slot_id = create_slot(examiner_client, examiner, exam_id)
    assert examiner_client.get(f"/api/v1/examiner/examinations/{exam_id}").status_code == 200
    assert examiner_client.get("/api/v1/examiner/slots").json["items"][0]["available_seats"] == 1

    # Another examiner cannot see or create slots against this examination.
    with app.app_context():
        other = User(full_name="Other Examiner", email="other@example.com", role="examiner")
        other.set_password(PASSWORD)
        from app.models import ExaminerProfile
        other.examiner_profile = ExaminerProfile(department="Other")
        db.session.add(other); db.session.commit()
    other_client=app.test_client(); sign_in(other_client,"other@example.com",PASSWORD)
    assert other_client.get(f"/api/v1/examiner/examinations/{exam_id}").status_code == 404

    student=app.test_client()
    assert student.post("/api/v1/auth/register",json={"full_name":"Student One","email":"student@example.com","password":PASSWORD}).status_code==201
    early=student.post("/api/v1/student/bookings",json={"slot_id":slot_id},headers=csrf(student))
    assert early.status_code==409
    activate_booking_window(app, exam_id)
    browse = student.get("/api/v1/student/examinations")
    assert browse.status_code == 200 and exam_id in {item["id"] for item in browse.json["items"]}
    details = student.get(f"/api/v1/student/examinations/{exam_id}")
    assert details.status_code == 200 and any(slot["id"] == slot_id for slot in details.json["slots"])
    booking=student.post("/api/v1/student/bookings",json={"slot_id":slot_id},headers=csrf(student))
    assert booking.status_code==201
    assert {recipient for recipient, _ in sent_emails} == {"student@example.com", "examiner@example.com"}
    assert student.post("/api/v1/student/bookings",json={"slot_id":slot_id},headers=csrf(student)).status_code==409
    assert examiner_client.get("/api/v1/examiner/booked-students").json["pagination"]["total"]==1
    admin_bookings=admin_client.get("/api/v1/admin/bookings",headers=admin_headers)
    assert admin_bookings.status_code==200 and admin_bookings.json["pagination"]["total"]==1, admin_bookings.json
    second=app.test_client(); second.post("/api/v1/auth/register",json={"full_name":"Student Two","email":"student2@example.com","password":PASSWORD})
    assert second.post("/api/v1/student/bookings",json={"slot_id":slot_id},headers=csrf(second)).status_code==409
    assert student.get("/api/v1/student/examinations").status_code==200
    with app.app_context():
        slot=db.session.get(ExaminationSlot,slot_id)
        assert slot.reserved_count==1


def test_evaluation_submission_publication_and_student_isolation(app):
    admin_client=app.test_client(); admin_headers, examiner, exam_id=prepare_exam(app,admin_client)
    examiner_client=app.test_client(); slot_id=create_slot(examiner_client,examiner,exam_id)
    activate_booking_window(app,exam_id)
    student=app.test_client()
    student.post("/api/v1/auth/register",json={"full_name":"Student One","email":"student@example.com","password":PASSWORD})
    booking=student.post("/api/v1/student/bookings",json={"slot_id":slot_id},headers=csrf(student)).json["item"]
    with app.app_context():
        exam=db.session.get(Examination,exam_id); exam.booking_end=datetime.now(timezone.utc)-timedelta(minutes=1); db.session.commit()
    assert student.post(f"/api/v1/student/bookings/{booking['id']}/cancel",headers=csrf(student)).status_code==409
    with app.app_context():
        exam=db.session.get(Examination,exam_id); exam.booking_end=datetime.now(timezone.utc)+timedelta(days=1); db.session.commit()
    sign_in(examiner_client,"examiner@example.com",examiner["initial_password"])
    invalid=examiner_client.put(f"/api/v1/examiner/bookings/{booking['id']}/evaluation",json={"submit":True,"criteria":[{"criterion_id":1,"score":21,"feedback":"too high"}]},headers=csrf(examiner_client))
    assert invalid.status_code==400
    submitted=examiner_client.put(f"/api/v1/examiner/bookings/{booking['id']}/evaluation",json={"submit":True,"criteria":[{"criterion_id":1,"score":17,"feedback":"Clear explanation"}]},headers=csrf(examiner_client))
    assert submitted.status_code==200 and submitted.json["item"]["total_score"]==17
    assert student.get("/api/v1/student/results").json["pagination"]["total"]==0
    published=admin_client.post(f"/api/v1/admin/evaluations/{submitted.json['item']['id']}/publish",headers=admin_headers)
    assert published.status_code==200
    result=student.get("/api/v1/student/results").json["items"][0]
    assert result["total_marks"]==17 and result["criteria"][0]["feedback"]=="Clear explanation"
    assert examiner_client.put(f"/api/v1/examiner/bookings/{booking['id']}/evaluation",json={"submit":True,"criteria":[]},headers=csrf(examiner_client)).status_code==409
    other=app.test_client(); other.post("/api/v1/auth/register",json={"full_name":"Student Two","email":"student2@example.com","password":PASSWORD})
    assert other.get(f"/api/v1/student/results/{result['evaluation_id']}").status_code==404
    assert student.get("/api/v1/notifications").json["pagination"]["total"]>=2


def test_cancellation_restores_capacity_and_respects_deadline(app):
    admin_client=app.test_client(); _, examiner, exam_id=prepare_exam(app,admin_client)
    examiner_client=app.test_client(); slot_id=create_slot(examiner_client,examiner,exam_id)
    activate_booking_window(app,exam_id)
    student=app.test_client(); student.post("/api/v1/auth/register",json={"full_name":"Student One","email":"student@example.com","password":PASSWORD})
    booking=student.post("/api/v1/student/bookings",json={"slot_id":slot_id},headers=csrf(student)).json["item"]
    cancelled=student.post(f"/api/v1/student/bookings/{booking['id']}/cancel",headers=csrf(student))
    assert cancelled.status_code==200 and cancelled.json["slot"]["available_seats"]==1
    with app.app_context():
        assert db.session.get(ExaminationSlot,slot_id).reserved_count==0
    # A second student can use the restored seat; the old cancellation remains in history.
    second=app.test_client(); second.post("/api/v1/auth/register",json={"full_name":"Student Two","email":"student2@example.com","password":PASSWORD})
    assert second.post("/api/v1/student/bookings",json={"slot_id":slot_id},headers=csrf(second)).status_code==201
    assert student.get("/api/v1/student/bookings?status=cancelled").json["pagination"]["total"]==1
