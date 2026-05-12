# backend/seed_admin.py

import os
import sys

from backend.db import SessionLocal
from backend.models import User
from backend.security import hash_password

def seed_admin():
    email = os.getenv("ADMIN_EMAIL")
    raw_password = os.getenv("ADMIN_PASSWORD")

    if not email or not raw_password:
        print("ERROR: Set ADMIN_EMAIL and ADMIN_PASSWORD environment variables.")
        print("Usage: ADMIN_EMAIL=x ADMIN_PASSWORD=y python seed_admin.py")
        sys.exit(1)

    db = SessionLocal()

    # Avoid duplicate
    if db.query(User).filter_by(email=email).first():
        print(f"Admin '{email}' already exists.")
        return

    user = User(
        email=email,
        password_hash=hash_password(raw_password),
        role="admin"
    )
    db.add(user)
    db.commit()
    print(f"Created admin user: {email}")

if __name__ == "__main__":
    seed_admin()
