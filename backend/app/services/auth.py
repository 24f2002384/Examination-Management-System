from sqlalchemy.exc import IntegrityError
from ..extensions import db
from ..models import User, StudentProfile


def register_student(full_name, email, password):
    user = User(full_name=full_name.strip(), email=email.lower().strip(), role="student")
    user.set_password(password)
    user.student_profile = StudentProfile()
    db.session.add(user)
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return None
    return user


def seed_admin(email, password):
    email = email.lower().strip()
    admin = User.query.filter_by(role="admin").first()
    if admin:
        return admin
    existing = User.query.filter_by(email=email).first()
    if existing:
        raise ValueError("Configured admin email belongs to a non-admin account")
    admin = User(full_name="System Administrator", email=email, role="admin")
    admin.set_password(password)
    db.session.add(admin)
    db.session.commit()
    return admin
