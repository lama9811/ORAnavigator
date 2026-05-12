# backend/seed_admin.py

from backend.db import SessionLocal
from backend.models import User
from backend.security import hash_password

def seed_admin():
    db = SessionLocal()
    email = "admin@example.com"
    raw_password = "Secret123"

    # Avoid duplicate
    if db.query(User).filter_by(email=email).first():
        print(f"✅ Admin '{email}' already exists.")
        return

    user = User(
        email=email,
        password_hash=hash_password(raw_password),
        role="admin"
    )
    db.add(user)
    db.commit()
    print(f"✅ Created admin user: {email} / {raw_password}")

if __name__ == "__main__":
    seed_admin()
