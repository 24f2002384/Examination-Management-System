"""Shared in-app and email notifications for examination workflow events."""
from ..extensions import db
from ..models import Booking, ExaminationSlot, Notification, User


def active_students(examination_id=None, booked_only=False):
    query = User.query.filter_by(role="student", is_active=True)
    if booked_only and examination_id is not None:
        query = query.join(Booking, Booking.student_id == User.id).join(
            ExaminationSlot, ExaminationSlot.id == Booking.slot_id
        ).filter(ExaminationSlot.examination_id == examination_id, Booking.status == "booked").distinct()
    return query.order_by(User.id).all()


def notify_users(users, message):
    unique = {user.id: user for user in users if user and user.is_active}
    for user in unique.values():
        db.session.add(Notification(user_id=user.id, message=message[:500]))
    return list(unique.values())


def email_users(users, subject, body):
    from .email import send_notice

    for user in {user.id: user for user in users if user and user.is_active}.values():
        send_notice(user.email, subject, body)
