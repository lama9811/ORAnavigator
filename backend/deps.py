# backend/deps.py
# Shared dependencies extracted from main.py for use by APIRouter modules.

import os
from typing import Dict, Any, Optional, List

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel, field_validator
from sqlalchemy.orm import Session
from jose import JWTError, jwt

from db import SessionLocal
from models import User
from security import hash_password, verify_password, create_access_token

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
JWT_SECRET = os.getenv("JWT_SECRET")
ALGORITHM = "HS256"

# ---------------------------------------------------------------------------
# FastAPI security scheme
# ---------------------------------------------------------------------------
security = HTTPBearer()

# ---------------------------------------------------------------------------
# Database dependency
# ---------------------------------------------------------------------------
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# ---------------------------------------------------------------------------
# Auth dependencies
# ---------------------------------------------------------------------------
def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    token = credentials.credentials
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[ALGORITHM])
        user_email = payload.get("email")
        if not user_email:
            raise HTTPException(status_code=403, detail="Invalid token")

        user = db.query(User).filter(User.email == user_email).first()
        if not user:
            raise HTTPException(status_code=403, detail="User not found")

        return {
            "user_id": user.id,
            "email": user.email,
            "role": user.role,
            "name": user.name,
            "student_id": user.student_id,
        }
    except JWTError as e:
        print(f"JWT decode error: {e}")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Invalid token"
        )

def get_optional_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(
        HTTPBearer(auto_error=False)
    ),
    db: Session = Depends(get_db),
) -> Optional[Dict[str, Any]]:
    """Like get_current_user but returns None instead of 401/403 when unauthenticated."""
    if not credentials:
        return None
    try:
        payload = jwt.decode(
            credentials.credentials, JWT_SECRET, algorithms=[ALGORITHM]
        )
        user_email = payload.get("email")
        if not user_email:
            return None
        user = db.query(User).filter(User.email == user_email).first()
        if not user:
            return None
        return {"user_id": user.id, "email": user.email, "role": user.role}
    except JWTError:
        return None

# ---------------------------------------------------------------------------
# Pydantic request/response models
# ---------------------------------------------------------------------------
class RegisterRequest(BaseModel):
    email: str
    password: str
    name: Optional[str] = None
    student_id: Optional[str] = None

    @staticmethod
    def validate_email_format(v):
        import re
        if not re.match(r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$', v):
            raise ValueError("Invalid email format")
        return v

    @staticmethod
    def validate_password_strength(v):
        if len(v) < 8:
            raise ValueError("Password must be at least 8 characters")
        return v

class LoginRequest(BaseModel):
    email: str
    password: str

VALID_MODELS = {"", "inav-1.0", "inav-1.1"}

class QueryRequest(BaseModel):
    query: str
    session_id: str = "default"
    skip_cache: bool = False
    model: str = ""

    @field_validator("model", mode="before")
    @classmethod
    def validate_model(cls, v):
        if v not in VALID_MODELS:
            return ""
        return v

class GuestQueryRequest(BaseModel):
    query: str
    guestProfile: Optional[dict] = None

class Course(BaseModel):
    course_code: str
    course_name: str
    credits: int
    prerequisites: List[str] = []
    offered: List[str] = []

class ProfileUpdateRequest(BaseModel):
    name: Optional[str] = None
    studentId: Optional[str] = None
    major: Optional[str] = None

class PasswordChangeRequest(BaseModel):
    currentPassword: str
    newPassword: str

class TTSRequest(BaseModel):
    text: str
    voice: str = "alloy"

class DegreeWorksRequest(BaseModel):
    student_name: Optional[str] = None
    student_id: Optional[str] = None
    degree_program: Optional[str] = None
    catalog_year: Optional[str] = None
    classification: Optional[str] = None
    advisor: Optional[str] = None
    overall_gpa: Optional[float] = None
    major_gpa: Optional[float] = None
    total_credits_earned: Optional[float] = None
    credits_required: Optional[float] = None
    credits_remaining: Optional[float] = None
    courses_completed: Optional[List[Dict[str, Any]]] = None
    courses_in_progress: Optional[List[Dict[str, Any]]] = None
    courses_remaining: Optional[List[Dict[str, Any]]] = None
