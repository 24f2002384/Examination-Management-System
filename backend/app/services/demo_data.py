"""Add a small, clearly labeled sample catalogue without touching user accounts."""
from datetime import timedelta

from ..extensions import db
from ..models import Course, Examination, RubricCriterion, now_utc


SAMPLES = [
    ("DEMO-CS101", "Introduction to Computing", "Foundational computing concepts and problem solving."),
    ("DEMO-DS201", "Applied Data Science", "Data preparation, analysis, and interpretation."),
    ("DEMO-UX301", "Human-Centred Design", "Research and prototype evaluation for digital products."),
]
EXAMS = [
    ("DEMO-CS101", "Demo: Computing Viva", "Viva", 20, 50),
    ("DEMO-CS101", "Demo: Programming Practical", "Practical", 45, 100),
    ("DEMO-DS201", "Demo: Data Project Review", "Project Demo", 30, 100),
    ("DEMO-UX301", "Demo: Design Assessment", "Assessment", 30, 80),
]


def seed_demo_data():
    created_courses = 0
    for code, name, description in SAMPLES:
        if not Course.query.filter_by(code=code).first():
            db.session.add(Course(code=code, name=name, description=description, status="active"))
            created_courses += 1
    db.session.flush()

    now = now_utc()
    slot_start, slot_end = now - timedelta(days=1), now + timedelta(days=3)
    booking_start, booking_end = slot_end, now + timedelta(days=10)
    created_exams = created_rubrics = 0
    for course_code, title, exam_type, duration, marks in EXAMS:
        course = Course.query.filter_by(code=course_code).one()
        exam = Examination.query.filter_by(course_id=course.id, title=title).first()
        if not exam:
            exam = Examination(course_id=course.id,
                title=title, exam_type=exam_type,
                description="Sample examination record. Review and adjust dates before real use.",
                duration_minutes=duration, max_marks=marks,
                slot_creation_start=slot_start, slot_creation_end=slot_end,
                booking_start=booking_start, booking_end=booking_end,
                status="draft")
            db.session.add(exam)
            db.session.flush()
            created_exams += 1
        if not exam.rubric_criteria:
            criteria = [
                ("Subject knowledge", "Understanding of the core subject matter", exam.max_marks * 0.4, 40),
                ("Communication", "Clarity and organization of the response", exam.max_marks * 0.3, 30),
                ("Reasoning", "Application and explanation of ideas", exam.max_marks * 0.3, 30),
            ]
            for name, description, maximum, weight in criteria:
                db.session.add(RubricCriterion(examination_id=exam.id, name=name,
                    description=description, max_score=maximum, weightage=weight))
                created_rubrics += 1
    db.session.commit()
    return {"courses": created_courses, "examinations": created_exams, "rubric_criteria": created_rubrics}
