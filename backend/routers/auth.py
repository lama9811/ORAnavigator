# backend/routers/auth.py
# Auth endpoints: register, verify-email, resend-verification, login.
#
# Flow: verify-first. /api/register does NOT create a user row. It builds a
# signed JWT containing the email + hashed password + name and emails the
# user a verification link with that JWT. /api/verify-email decodes the JWT
# and only then creates the user row (with email_verified=True). If the user
# never clicks, no row ever exists.

import os
import re
import time as time_module
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException, Depends, Request, status
from sqlalchemy.orm import Session
from jose import jwt, JWTError

from deps import get_db, RegisterRequest, LoginRequest
from models import User
from security import (
    hash_password, verify_password, create_access_token,
    JWT_SECRET, ALGORITHM,
)

router = APIRouter(tags=["auth"])

# ---------------------------------------------------------------------------
# Constants & rate-limit state
# ---------------------------------------------------------------------------
ALLOWED_EMAIL_DOMAINS = ["morgan.edu"]
SIGNUP_TOKEN_TYPE = "signup_verify"
SIGNUP_TOKEN_EXPIRY_HOURS = 24
_register_timestamps: dict[str, list] = {}


def _create_signup_token(email: str, hashed_password: str, name: str | None) -> str:
    """Build a signed JWT that encodes everything we need to create the user
    when they click the verification link. No DB write until they click."""
    payload = {
        "type": SIGNUP_TOKEN_TYPE,
        "email": email,
        "pw_hash": hashed_password,
        "name": name,
        "exp": datetime.now(timezone.utc) + timedelta(hours=SIGNUP_TOKEN_EXPIRY_HOURS),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=ALGORITHM)


def _decode_signup_token(token: str) -> dict | None:
    """Validate a signup verification token. Returns the payload dict or None."""
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[ALGORITHM])
    except JWTError:
        return None
    if payload.get("type") != SIGNUP_TOKEN_TYPE:
        return None
    return payload


# ---------------------------------------------------------------------------
# POST /api/register   — does NOT create the user; sends verification email
# ---------------------------------------------------------------------------
@router.post("/api/register", status_code=status.HTTP_202_ACCEPTED)
def register(req: RegisterRequest, db: Session = Depends(get_db)):
    from email_service import send_verification_email

    email = req.email.strip().lower()

    if not re.match(r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$', email):
        raise HTTPException(status_code=400, detail="Invalid email format")

    # Rate limit per EMAIL (3 attempts per email per hour)
    now_ts = time_module.time()
    reg_ts = _register_timestamps.get(email, [])
    reg_ts = [t for t in reg_ts if now_ts - t < 3600]
    if len(reg_ts) >= 3:
        raise HTTPException(status_code=429, detail="Too many attempts for this email. Try again in an hour.")
    reg_ts.append(now_ts)
    _register_timestamps[email] = reg_ts

    # Domain check
    email_domain = email.split("@")[-1].lower()
    allow_test = os.getenv("ALLOW_TEST_EMAILS", "false").lower() == "true"
    if email_domain not in ALLOWED_EMAIL_DOMAINS and not (allow_test and email.endswith("@test.com")):
        raise HTTPException(status_code=400, detail="Only @morgan.edu email addresses are allowed.")

    if len(req.password) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters")

    # If the email is already registered & verified, reject. (Unverified rows
    # don't exist in the new flow — but kept as a guard.)
    existing = db.query(User).filter(User.email == email).first()
    if existing:
        raise HTTPException(status_code=400, detail="Email already registered. Please log in instead.")

    hashed = hash_password(req.password)
    name = req.name.strip() if req.name else None

    # Temporary bypass: when SKIP_EMAIL_VERIFICATION=true, create the (already
    # verified) user row directly and skip the email round-trip. Used while a
    # working no-reply sender isn't wired up yet. All other validation above
    # (domain, password length, rate limit, duplicate check) still applies.
    # Reversible: unset the flag to restore the normal verify-first flow.
    skip_verification = os.getenv("SKIP_EMAIL_VERIFICATION", "false").lower() == "true"
    if skip_verification:
        new_user = User(
            email=email,
            password_hash=hashed,
            role="user",
            email_verified=True,
            verification_token=None,
            name=name,
        )
        db.add(new_user)
        db.commit()
        return {
            "message": "Account created — you can log in now.",
            "verified": True,
        }

    token = _create_signup_token(email=email, hashed_password=hashed, name=name)
    send_verification_email(email, token)

    # NB: no DB write. The user row is created when /api/verify-email is called.
    return {
        "message": "Check your Morgan State email to verify and finish creating your account.",
    }


# ---------------------------------------------------------------------------
# GET /api/verify-email   — decodes the JWT, creates the user, redirects
# ---------------------------------------------------------------------------
@router.get("/api/verify-email")
def verify_email(token: str, db: Session = Depends(get_db)):
    from starlette.responses import RedirectResponse

    payload = _decode_signup_token(token)
    if not payload:
        raise HTTPException(status_code=400, detail="Invalid or expired verification link")

    email = payload["email"]
    hashed_password = payload["pw_hash"]
    name = payload.get("name")

    app_url = os.getenv("APP_URL", "https://ora.inavigator.ai")

    # If the row already exists (someone clicked an older link, or a duplicate
    # link), treat as success — they're verified, send them to login.
    existing = db.query(User).filter(User.email == email).first()
    if existing:
        if not existing.email_verified:
            existing.email_verified = True
            db.commit()
        return RedirectResponse(url=f"{app_url}/login?verified=true")

    # Create the user now — first time the link is being clicked.
    new_user = User(
        email=email,
        password_hash=hashed_password,
        role="user",
        email_verified=True,
        verification_token=None,
        name=name,
    )
    db.add(new_user)
    db.commit()

    return RedirectResponse(url=f"{app_url}/login?verified=true")


# ---------------------------------------------------------------------------
# POST /api/resend-verification
#
# In the verify-first flow we have no record of a pending signup (no DB row
# until the user clicks the link). So we can't re-issue a JWT here — we'd need
# the password again. Frontend should send the user back to the signup form
# instead. This endpoint stays for backwards compatibility and returns a
# friendly message.
# ---------------------------------------------------------------------------
@router.post("/api/resend-verification")
async def resend_verification(request: Request, db: Session = Depends(get_db)):
    body = await request.json()
    email = (body.get("email", "") or "").strip().lower()
    user = db.query(User).filter(User.email == email).first()
    if user and user.email_verified:
        return {"message": "Your email is already verified — please log in."}
    return {
        "message": (
            "If your email isn't verified yet, please submit the signup form "
            "again to receive a fresh verification email."
        ),
    }


# ---------------------------------------------------------------------------
# POST /api/login
# ---------------------------------------------------------------------------
@router.post("/api/login")
def login(req: LoginRequest, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == req.email).first()
    if not user or not verify_password(req.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid credentials")

    # In the new flow, only verified users exist in the DB. This check stays
    # as a defense against any legacy unverified rows.
    if not getattr(user, "email_verified", True) and user.role != "admin":
        raise HTTPException(
            status_code=403,
            detail="Please verify your email first. Check your inbox for the verification link.",
        )

    token = create_access_token(
        {"user_id": user.id, "role": user.role, "email": user.email}
    )
    return {"access_token": token, "token_type": "bearer"}
