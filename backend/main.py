import sys
# Force unbuffered output so we see logs immediately
sys.stdout.reconfigure(line_buffering=True)

print("[OK] main.py loaded successfully")

import os
import re
import json
import asyncio
# import time  # Commented: currently unused, kept for potential future use
import shutil #  NEW: For file operations
from typing import List, Dict, Any, Optional
from contextlib import asynccontextmanager
from datetime import datetime, timezone, timedelta


def iso_utc(dt):
    """Serialize a datetime as an unambiguous UTC ISO string (with a trailing
    'Z'). Timestamps are stored in UTC, but SQLite/MySQL return them as *naive*
    datetimes, so a plain .isoformat() has no timezone marker -- the browser
    then parses it as LOCAL time and shows the wrong hour. Stamping UTC here
    lets the client convert to the viewer's zone correctly. Returns None for a
    falsy input so callers can keep their `... if x else None` shape."""
    if not dt:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    # isoformat() renders +00:00; normalize to the shorter, universal 'Z'.
    return dt.isoformat().replace("+00:00", "Z")


#  FIXED IMPORTS: Use 'pypdf' which you installed, not 'PyPDF2'
import pypdf
import docx

from fastapi import FastAPI, HTTPException, Depends, status, File, UploadFile, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi.staticfiles import StaticFiles
from fastapi.responses import StreamingResponse, HTMLResponse
from pydantic import BaseModel, field_validator
from collections import Counter
import io
from dotenv import load_dotenv

# ==============================================================================
# 1. ENVIRONMENT LOADING (FIXED FOR ROOT FOLDER)
# ==============================================================================
# Get the absolute path of the backend folder
BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
# Get the project root (one level up)
PROJECT_ROOT = os.path.dirname(BACKEND_DIR)
# Path to .env file in the root
ENV_PATH = os.path.join(PROJECT_ROOT, ".env")

print(f"[INFO] Looking for .env at: {ENV_PATH}")

if os.path.exists(ENV_PATH):
    load_dotenv(ENV_PATH)
    print("[OK] .env file loaded!")
else:
    print("[ERROR] .env file NOT found at root. Checking backend folder...")
    load_dotenv(os.path.join(BACKEND_DIR, ".env"))

print(f"[KEY] JWT_SECRET Check: {'FOUND' if os.getenv('JWT_SECRET') else 'MISSING'}")

# SQLAlchemy Imports
from sqlalchemy.orm import Session
from sqlalchemy.exc import OperationalError, ProgrammingError
from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey, text

# Vertex AI Agent Engine (the RAG pipeline: ADK agent + VertexAiSearchTool)
from vertex_agent import query_agent, query_agent_stream, check_agent_health, reset_session, get_last_grounding

# Query caching for faster responses
from cache import query_cache, get_context_hash, log_cache_stats
from services.feature_suggester import suggest_feature
from kb_browser import try_browse, browse_citations

# Local Imports (Auth & DB) - These must run AFTER load_dotenv
from db import SessionLocal, engine, Base
from models import User, SupportTicket, FailedQuery, KBSuggestion, UserMemory, ChatHistory, Feedback
# Single source of truth for ProfileUpdateRequest -- main.py used to
# redefine it locally (only `name`), which silently masked the extended
# version in deps.py and broke profile saves once new fields were added.
# Import from deps.py instead so the schema and validator are shared.
from deps import ProfileUpdateRequest as _DepsProfileUpdateRequest
ProfileUpdateRequest = _DepsProfileUpdateRequest
from security import hash_password, verify_password, create_access_token
from jose import JWTError, jwt

# ==============================================================================
# 2. CONFIGURATION & CONSTANTS
# ==============================================================================
# Vertex AI Agent Engine config
USE_VERTEX_AGENT   = os.getenv("USE_VERTEX_AGENT", "true").lower() == "true"
ADK_BASE_URL       = os.getenv("ADK_BASE_URL", "http://127.0.0.1:8080")

# OpenAI config (used for text-to-speech only, not retrieval)
OPENAI_API_KEY     = os.getenv("OPENAI_API_KEY")
JWT_SECRET         = os.getenv("JWT_SECRET")
ALGORITHM          = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "4320"))  # 3 days default

# Upload configuration
UPLOAD_FOLDER = os.path.join(BACKEND_DIR, "uploads", "profile_pictures")
CHAT_FILES_FOLDER = os.path.join(BACKEND_DIR, "uploads", "chat_files") #  NEW: Chat files folder

ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'pdf', 'txt', 'docx', 'doc', 'mov', 'mp4'} #  NEW: Added Docs

# Create folders if not exist
for folder in [UPLOAD_FOLDER, CHAT_FILES_FOLDER]:
    if not os.path.exists(folder):
        os.makedirs(folder, exist_ok=True)
        print(f"[OK] Created folder: {folder}")

# Safety check for keys
if USE_VERTEX_AGENT:
    print(f"[INFO] Using Vertex AI Agent Engine at {ADK_BASE_URL}")
else:
    print("[WARN] USE_VERTEX_AGENT is disabled; the chat path requires the Vertex AI agent.")

# ==============================================================================
# 3. DATABASE MODELS
# ==============================================================================
# ChatHistory, Feedback, and all other models are now in models.py
# Imported above: ChatHistory, Feedback (via models import line)

def init_db():
    """Initializes the database tables and runs migrations."""
    # 1. Create tables if missing
    try:
        Base.metadata.create_all(bind=engine)
        print("[OK] Database tables checked/created.")
    except Exception as e:
        print(f"[WARN] DB Connection Error: {e}")

    # 2. Add session_id column if missing (For existing DBs)
    with engine.connect() as conn:
        try:
            # Check if column exists by selecting from it
            conn.execute(text("SELECT session_id FROM chat_history LIMIT 1"))
        except (OperationalError, ProgrammingError):
            print("[WARN] 'session_id' column missing. Adding it now...")
            try:
                conn.execute(text("ALTER TABLE chat_history ADD COLUMN session_id VARCHAR(255) DEFAULT 'default'"))
                conn.commit()
                print("[OK] Successfully added 'session_id' column!")
            except Exception as e:
                print(f"[ERROR] Failed to add column: {e}")

        # 3. Add profile_picture_data column if missing (For base64 storage)
        try:
            conn.execute(text("SELECT profile_picture_data FROM users LIMIT 1"))
        except (OperationalError, ProgrammingError):
            print("[WARN] 'profile_picture_data' column missing. Adding it now...")
            try:
                conn.execute(text("ALTER TABLE users ADD COLUMN profile_picture_data LONGTEXT"))
                conn.commit()
                print("[OK] Successfully added 'profile_picture_data' column!")
            except Exception as e:
                print(f"[ERROR] Failed to add profile_picture_data column: {e}")

        # 4. Add email auth columns if missing
        for col, col_type in [
            ("email_verified", "BOOLEAN DEFAULT TRUE"),
            ("verification_token", "VARCHAR(255)"),
            ("reset_token", "VARCHAR(255)"),
            ("reset_token_expires", "DATETIME"),
        ]:
            try:
                conn.execute(text(f"SELECT {col} FROM users LIMIT 1"))
            except (OperationalError, ProgrammingError):
                try:
                    conn.execute(text(f"ALTER TABLE users ADD COLUMN {col} {col_type}"))
                    conn.commit()
                    print(f"[OK] Added '{col}' column to users")
                except Exception:
                    pass

        # 5. Add chat_history.citations column if missing (Sources persistence).
        #    Self-heals the prod schema on startup so saving an answer's Sources
        #    never hits "Unknown column 'citations'".
        try:
            conn.execute(text("SELECT citations FROM chat_history LIMIT 1"))
        except (OperationalError, ProgrammingError):
            print("[WARN] 'citations' column missing. Adding it now...")
            try:
                conn.execute(text("ALTER TABLE chat_history ADD COLUMN citations MEDIUMTEXT NULL"))
                conn.commit()
                print("[OK] Successfully added 'citations' column!")
            except Exception as e:
                print(f"[ERROR] Failed to add citations column: {e}")

        # 5b. Add submissions.budget_json column if missing (Budget Helper).
        try:
            conn.execute(text("SELECT budget_json FROM submissions LIMIT 1"))
        except (OperationalError, ProgrammingError):
            print("[WARN] 'budget_json' column missing. Adding it now...")
            try:
                conn.execute(text("ALTER TABLE submissions ADD COLUMN budget_json MEDIUMTEXT NULL"))
                conn.commit()
                print("[OK] Successfully added 'budget_json' column!")
            except Exception as e:
                print(f"[ERROR] Failed to add budget_json column: {e}")

        # 5c. Add submissions.compliance_json column if missing (Compliance Sentinel).
        try:
            conn.execute(text("SELECT compliance_json FROM submissions LIMIT 1"))
        except (OperationalError, ProgrammingError):
            print("[WARN] 'compliance_json' column missing. Adding it now...")
            try:
                conn.execute(text("ALTER TABLE submissions ADD COLUMN compliance_json MEDIUMTEXT NULL"))
                conn.commit()
                print("[OK] Successfully added 'compliance_json' column!")
            except Exception as e:
                print(f"[ERROR] Failed to add compliance_json column: {e}")

        # 5d. Add submissions.sections_json column if missing (Section Drafting Coach).
        try:
            conn.execute(text("SELECT sections_json FROM submissions LIMIT 1"))
        except (OperationalError, ProgrammingError):
            print("[WARN] 'sections_json' column missing. Adding it now...")
            try:
                conn.execute(text("ALTER TABLE submissions ADD COLUMN sections_json MEDIUMTEXT NULL"))
                conn.commit()
                print("[OK] Successfully added 'sections_json' column!")
            except Exception as e:
                print(f"[ERROR] Failed to add sections_json column: {e}")

        # 6. Check if support_tickets table exists
        try:
            conn.execute(text("SELECT id FROM support_tickets LIMIT 1"))
            print("[OK] support_tickets table exists")
        except (OperationalError, ProgrammingError):
            print("[WARN] 'support_tickets' table missing. Creating it now...")
            try:
                conn.execute(text("""
                    CREATE TABLE support_tickets (
                        id INT AUTO_INCREMENT PRIMARY KEY,
                        user_id INT NOT NULL,
                        subject VARCHAR(255) NOT NULL,
                        category VARCHAR(50) NOT NULL,
                        description TEXT NOT NULL,
                        attachment_data LONGTEXT,
                        attachment_name VARCHAR(255),
                        status VARCHAR(50) DEFAULT 'open',
                        priority VARCHAR(20) DEFAULT 'normal',
                        admin_notes TEXT,
                        resolved_by INT,
                        resolved_at DATETIME,
                        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                        updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                        FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
                        FOREIGN KEY (resolved_by) REFERENCES users(id) ON DELETE SET NULL
                    )
                """))
                conn.commit()
                print("[OK] Successfully created 'support_tickets' table!")
            except Exception as e:
                print(f"[ERROR] Failed to create support_tickets table: {e}")

    # 8. Create/Update admin account
    try:
        db = SessionLocal()
        admin_email = os.getenv("ADMIN_EMAIL", "admin@morgan.edu")
        admin_password = os.getenv("ADMIN_PASSWORD")
        if not admin_password:
            print("[WARN] ADMIN_PASSWORD not set in env, skipping admin account creation")
            db.close()
            return

        existing_admin = db.query(User).filter(User.email == admin_email).first()

        if existing_admin:
            # Update existing user to admin
            if existing_admin.role != "admin":
                existing_admin.role = "admin"
                db.commit()
                print(f"[OK] Updated {admin_email} to admin role!")
            else:
                print(f"[OK] Admin account {admin_email} already exists with admin role.")
        else:
            # Create new admin account
            from security import hash_password
            hashed = hash_password(admin_password)
            admin_user = User(
                email=admin_email,
                password_hash=hashed,
                role="admin",
                name="Admin"
            )
            db.add(admin_user)
            db.commit()
            print(f"[OK] Created admin account: {admin_email}")

        db.close()
    except Exception as e:
        print(f"[ERROR] Failed to create/update admin account: {e}")

init_db()

# ==============================================================================
# 4. FASTAPI APP SETUP
# ==============================================================================
# AI System globals (initialized in lifespan)
def build_qa_chain():
    """Check the Vertex AI Agent's health on startup."""
    health = check_agent_health()
    print(f" Vertex AI Agent: {health['status']} - {health['message']}")
    if health["status"] != "connected":
        print("[WARN] ADK server not running. Start it with:")
        print("   cd google-ai-engine-research/adk_deploy && python -m google.adk.cli web . --port 8080")

@asynccontextmanager
async def lifespan(app):
    """Modern lifespan event handler for FastAPI"""
    # Startup
    build_qa_chain()
    yield
    # Shutdown (cleanup if needed)

app = FastAPI(title="ORA Navigator API", version="5.0.0", lifespan=lifespan)

ALLOWED_ORIGINS = os.getenv("CORS_ORIGINS", os.getenv("ALLOWED_ORIGINS", "http://localhost:3000,http://localhost:3001,http://localhost:5173,http://localhost:5174,http://localhost:5175,http://127.0.0.1:3000,http://127.0.0.1:3001,http://127.0.0.1:5173,http://127.0.0.1:5174,http://127.0.0.1:8000,https://inavigator.ai,https://ora.inavigator.ai,https://api-ora.inavigator.ai,https://oranavigator-frontend-ollhkgeova-uc.a.run.app,https://oranavigator-frontend-882573591705.us-central1.run.app")).split(",")
print(f"[CORS] Allowed origins: {ALLOWED_ORIGINS}")

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.add_middleware(
    TrustedHostMiddleware,
    allowed_hosts=os.getenv("TRUSTED_HOSTS", "localhost,127.0.0.1,inavigator.ai,ora.inavigator.ai,api-ora.inavigator.ai,oranavigator-backend-ollhkgeova-uc.a.run.app,oranavigator-frontend-ollhkgeova-uc.a.run.app,oranavigator-backend-882573591705.us-central1.run.app,oranavigator-frontend-882573591705.us-central1.run.app").split(",")
)

# Mount Static Files (Profile Pictures AND Chat Files)
UPLOADS_DIR = os.path.join(BACKEND_DIR, "uploads")
if os.path.exists(UPLOADS_DIR):
    try:
        app.mount("/uploads", StaticFiles(directory=UPLOADS_DIR), name="uploads")
        print(f"[OK] Static files mounted: /uploads -> {UPLOADS_DIR}")
    except Exception as e:
        print(f"[ERROR] Error mounting static files: {e}")
else:
    os.makedirs(UPLOADS_DIR, exist_ok=True)
    print(f"[OK] Created uploads directory: {UPLOADS_DIR}")

# ==============================================================================
# 4b. ROUTERS (modular endpoint files)
# ==============================================================================
from routers.auth import router as auth_router
app.include_router(auth_router)

# ==============================================================================
# 5. AUTHENTICATION HELPERS
# ==============================================================================
security = HTTPBearer()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: Session = Depends(get_db)
) -> Dict[str,Any]:
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
        }
    except JWTError as e:
        print(f"JWT decode error: {e}")
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid token")

def allowed_file(filename: str) -> bool:
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

# ==============================================================================
# 6. PYDANTIC SCHEMAS
# ==============================================================================
class RegisterRequest(BaseModel):
    email: str
    password: str
    name: Optional[str] = None

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
    model: str = ""              # "inav-1.0" (fast) or "inav-1.1" (pro)

    @field_validator("model", mode="before")
    @classmethod
    def validate_model(cls, v):
        if v not in VALID_MODELS:
            return ""
        return v

class GuestQueryRequest(BaseModel):
    query: str
    guestProfile: Optional[dict] = None

# ==============================================================================
# GUEST RATE LIMITING (Simple In-Memory)
# ==============================================================================
from collections import defaultdict
import time as time_module

guest_rate_limits = defaultdict(list)  # IP -> list of timestamps
# Requests per minute per IP. Override via env (e.g. GUEST_RATE_LIMIT=100000)
# to un-throttle the faithfulness eval harness; production keeps the default 15.
GUEST_RATE_LIMIT = int(os.getenv("GUEST_RATE_LIMIT", "15"))  # requests per minute
GUEST_RATE_WINDOW = 60  # seconds
_guest_rate_last_cleanup = time_module.time()

def check_guest_rate_limit(ip: str) -> bool:
    """Check if IP is within rate limit. Returns True if allowed, False if blocked."""
    global _guest_rate_last_cleanup
    current_time = time_module.time()

    # Periodic cleanup: purge stale IPs every 10 minutes to prevent memory leak
    if current_time - _guest_rate_last_cleanup > 600:
        stale_ips = [k for k, v in guest_rate_limits.items() if not v or current_time - v[-1] > GUEST_RATE_WINDOW]
        for k in stale_ips:
            del guest_rate_limits[k]
        _guest_rate_last_cleanup = current_time

    # Clean old entries for this IP
    guest_rate_limits[ip] = [t for t in guest_rate_limits[ip] if current_time - t < GUEST_RATE_WINDOW]
    # Check limit
    if len(guest_rate_limits[ip]) >= GUEST_RATE_LIMIT:
        return False
    # Add new request
    guest_rate_limits[ip].append(current_time)
    return True

# Forgot-password rate limiting: {email: [timestamp, ...]}
_forgot_pw_timestamps: dict[str, list] = {}
_forgot_pw_last_cleanup = time_module.time()
FORGOT_PW_RATE_LIMIT = 5   # max requests per window
FORGOT_PW_RATE_WINDOW = 900  # 15 minutes

# ProfileUpdateRequest is imported from deps.py (see top-of-file note).
# Local redefinition removed to fix the dead-import shadowing that
# silently dropped department / title / primary_role / interests fields
# on PUT /api/profile.

class PasswordChangeRequest(BaseModel):
    currentPassword: str
    newPassword: str

class TTSRequest(BaseModel):
    text: str
    voice: str = "alloy"  # Options: alloy, echo, fable, onyx, nova, shimmer

# ==============================================================================
# 7. STATIC DATA & RESOURCES
# ==============================================================================

def load_json_documents(paths: List[str]) -> List[Dict[str,Any]]:
    docs: List[Dict[str,Any]] = []
    for p in paths:
        try:
            data = json.load(open(p, encoding="utf-8"))
            if isinstance(data, dict):
                for k, v in data.items():
                    if isinstance(v, dict):
                        parts = [f"{subk}: {subv}" for subk, subv in v.items()]
                        docs.append({"text": f"{k} – " + "; ".join(parts), "source": p})
                    else:
                        docs.append({"text": f"{k}: {v}", "source": p})
            elif isinstance(data, list):
                for obj in data:
                    text = "\n".join(f"{kk}: {vv}" for kk, vv in obj.items())
                    docs.append({"text": text, "source": p})
        except Exception:
            pass
    return docs

# ==============================================================================
# 7b. ROOT DASHBOARD - Show endpoints & recent logs
# ==============================================================================
import logging
from collections import deque

# In-memory log buffer (last 200 log lines)
_log_buffer = deque(maxlen=200)

class BufferHandler(logging.Handler):
    def emit(self, record):
        _log_buffer.append(self.format(record))

_buf_handler = BufferHandler()
_buf_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
logging.getLogger().addHandler(_buf_handler)
logging.getLogger("uvicorn.access").addHandler(_buf_handler)
logging.getLogger("uvicorn.error").addHandler(_buf_handler)

def get_optional_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(HTTPBearer(auto_error=False)),
    db: Session = Depends(get_db)
) -> Optional[Dict[str, Any]]:
    """Like get_current_user but returns None instead of 401/403 when unauthenticated."""
    if not credentials:
        return None
    try:
        payload = jwt.decode(credentials.credentials, JWT_SECRET, algorithms=[ALGORITHM])
        user_email = payload.get("email")
        if not user_email:
            return None
        user = db.query(User).filter(User.email == user_email).first()
        if not user:
            return None
        return {"user_id": user.id, "email": user.email, "role": user.role}
    except JWTError:
        return None

@app.get("/", response_class=HTMLResponse)
def root_dashboard(request: Request, user: Optional[dict] = Depends(get_optional_user)):
    """Dashboard showing all endpoints and recent logs. Admin only, dev/staging only."""
    if not user or user.get("role") != "admin":
        from starlette.responses import RedirectResponse
        return RedirectResponse(url="/docs")
    # Hide logs in production unless explicitly enabled
    show_logs = os.getenv("SHOW_DASHBOARD_LOGS", "true").lower() == "true"
    routes = []
    for route in request.app.routes:
        if hasattr(route, "methods"):
            for method in sorted(route.methods):
                if method == "HEAD":
                    continue
                routes.append({"method": method, "path": route.path})
    routes.sort(key=lambda r: (r["path"], r["method"]))

    import html as _html
    logs_html = "\n".join(
        f"<div class='log'>{_html.escape(line)}</div>" for line in reversed(_log_buffer)
    ) or "<div class='log dim'>No logs captured yet.</div>"

    rows = "\n".join(
        f"<tr><td class='method {r['method'].lower()}'>{r['method']}</td><td>{r['path']}</td></tr>"
        for r in routes
    )

    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>ORANavigator API</title>
<style>
  * {{ margin:0; padding:0; box-sizing:border-box; }}
  body {{ font-family: 'SF Mono', 'Fira Code', monospace; background:#0d1117; color:#c9d1d9; padding:2rem; }}
  h1 {{ color:#58a6ff; margin-bottom:.5rem; font-size:1.4rem; }}
  h2 {{ color:#8b949e; margin:1.5rem 0 .5rem; font-size:1rem; text-transform:uppercase; letter-spacing:.1em; }}
  .info {{ color:#8b949e; font-size:.85rem; margin-bottom:1rem; }}
  table {{ border-collapse:collapse; width:100%; max-width:700px; }}
  td {{ padding:4px 12px; border-bottom:1px solid #21262d; font-size:.85rem; }}
  .method {{ font-weight:bold; width:60px; }}
  .get {{ color:#3fb950; }}  .post {{ color:#d29922; }}  .put {{ color:#58a6ff; }}  .delete {{ color:#f85149; }}
  #logs {{ background:#161b22; border:1px solid #30363d; border-radius:6px; padding:1rem; max-height:500px; overflow-y:auto; margin-top:.5rem; }}
  .log {{ font-size:.78rem; padding:2px 0; border-bottom:1px solid #21262d; white-space:pre-wrap; word-break:break-all; }}
  .dim {{ color:#484f58; }}
  .refresh {{ color:#58a6ff; text-decoration:none; font-size:.85rem; }}
</style></head><body>
  <h1>ORANavigator API v2.1.0</h1>
  <div class="info">Backend is running. {len(routes)} endpoints registered.</div>

  <h2>Endpoints</h2>
  <table>{rows}</table>

  {'<h2>Recent Logs <a class="refresh" href="/">refresh</a></h2><div id="logs">' + logs_html + '</div>' if show_logs else '<p class="dim">Logs hidden in production. Set SHOW_DASHBOARD_LOGS=true to enable.</p>'}
</body></html>"""

# ==============================================================================
# 8. API ENDPOINTS
# ==============================================================================

# --- Auth: register, verify-email, resend-verification, login live in routers/auth.py ---


@app.post("/api/forgot-password")
async def forgot_password(request: Request, db: Session = Depends(get_db)):
    from email_service import generate_token, send_password_reset_email
    body = await request.json()
    email = body.get("email", "").strip().lower()
    if not email:
        raise HTTPException(status_code=400, detail="Email required")

    # Rate limit: max 5 forgot-password requests per 15 minutes per email
    global _forgot_pw_last_cleanup
    now_ts = time_module.time()

    # Periodic cleanup: purge stale emails every 15 minutes
    if now_ts - _forgot_pw_last_cleanup > FORGOT_PW_RATE_WINDOW:
        stale = [k for k, v in _forgot_pw_timestamps.items() if not v or now_ts - v[-1] > FORGOT_PW_RATE_WINDOW]
        for k in stale:
            del _forgot_pw_timestamps[k]
        _forgot_pw_last_cleanup = now_ts

    timestamps = _forgot_pw_timestamps.get(email, [])
    timestamps = [t for t in timestamps if now_ts - t < FORGOT_PW_RATE_WINDOW]
    if len(timestamps) >= FORGOT_PW_RATE_LIMIT:
        return {"message": "If an account exists with that email, a password reset link has been sent."}
    timestamps.append(now_ts)
    _forgot_pw_timestamps[email] = timestamps

    user = db.query(User).filter(User.email == email).first()
    if user:
        token = generate_token()
        user.reset_token = token
        user.reset_token_expires = datetime.now(timezone.utc) + timedelta(hours=1)
        db.commit()
        send_password_reset_email(email, token)

    return {"message": "If an account exists with that email, a password reset link has been sent."}


@app.post("/api/reset-password")
async def reset_password(request: Request, db: Session = Depends(get_db)):
    body = await request.json()
    token = body.get("token", "")
    new_password = body.get("password", "")
    if not token or not new_password:
        raise HTTPException(status_code=400, detail="Token and new password required")
    if len(new_password) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters")

    user = db.query(User).filter(User.reset_token == token).first()
    if not user:
        raise HTTPException(status_code=400, detail="Invalid or expired reset link")
    if user.reset_token_expires:
        expires = user.reset_token_expires if user.reset_token_expires.tzinfo else user.reset_token_expires.replace(tzinfo=timezone.utc)
        if expires < datetime.now(timezone.utc):
            raise HTTPException(status_code=400, detail="Reset link has expired. Request a new one.")

    user.password_hash = hash_password(new_password)
    user.reset_token = None
    user.reset_token_expires = None
    user.email_verified = True
    db.commit()
    return {"message": "Password reset successfully. You can now log in."}


# --- Profile Management ---
@app.get("/api/profile")
async def get_profile(user: dict = Depends(get_current_user), db: Session = Depends(get_db)):
    db_user = db.query(User).filter(User.id == user["user_id"]).first()
    if not db_user:
        raise HTTPException(status_code=404, detail="User not found")

    # Prefer base64 data (persistent) over file URL
    profile_pic = getattr(db_user, 'profile_picture_data', None)
    if not profile_pic:
        profile_pic = getattr(db_user, 'profile_picture', None)

    # Interests live in user_memories (multi-value). Read them here so the
    # profile form can render the user's current list as a comma-separated
    # string. Ordered by id ASC so a re-save preserves the user's typing order.
    from models import UserMemory as _UserMemory
    interest_rows = (
        db.query(_UserMemory)
        .filter(
            _UserMemory.user_id == db_user.id,
            _UserMemory.memory_type == "interest",
        )
        .order_by(_UserMemory.id.asc())
        .all()
    )
    interests_str = ", ".join((r.content or "").strip() for r in interest_rows if (r.content or "").strip())

    return {
        "email": db_user.email,
        "name": getattr(db_user, 'name', None),
        "profilePicture": profile_pic,
        "role": getattr(db_user, 'role', "user"),
        "department": getattr(db_user, 'department', None),
        "title": getattr(db_user, 'title', None),
        "primary_role": getattr(db_user, 'primary_role', None),
        "interests": interests_str,
    }

@app.put("/api/profile")
async def update_profile(req: ProfileUpdateRequest, user: dict = Depends(get_current_user), db: Session = Depends(get_db)):
    db_user = db.query(User).filter(User.id == user["user_id"]).first()
    if not db_user: raise HTTPException(404, "User not found")

    if req.name is not None and hasattr(db_user, 'name'): db_user.name = req.name
    if req.department is not None and hasattr(db_user, 'department'):
        db_user.department = req.department or None
    if req.title is not None and hasattr(db_user, 'title'):
        db_user.title = req.title or None
    if req.primary_role is not None and hasattr(db_user, 'primary_role'):
        # request validator already constrained primary_role to the enum or None
        db_user.primary_role = req.primary_role

    db.commit()

    # Mirror the structured profile fields into user_memories so the agent's
    # memory_context sees them automatically.
    # Best-effort: a mirror failure must not roll back the profile save -- but it
    # must NOT be hidden either. If it fails we still save the profile, and we
    # tell the caller so the UI can warn the user instead of silently claiming
    # full success (a silent mirror failure is exactly why the chatbot once knew
    # nothing about a user whose profile was clearly filled in).
    mirror_ok = True
    try:
        from services.memory_service import mirror_profile_to_memories
        mirror_profile_to_memories(
            db,
            user_id=db_user.id,
            department=req.department,
            primary_role=req.primary_role,
            interests=req.interests,
        )
        db.commit()
    except Exception as e:
        print(f"[PROFILE] memory mirror failed for user {db_user.id}: {e}")
        db.rollback()
        mirror_ok = False

    resp = {"message": "Profile updated"}
    if not mirror_ok:
        resp["warning"] = (
            "Your profile was saved, but syncing it to the assistant's memory "
            "failed, so the chatbot may not recall these details yet. Please try "
            "saving again, or contact support if it keeps happening."
        )
    return resp

@app.post("/api/change-password")
async def change_password(req: PasswordChangeRequest, user: dict = Depends(get_current_user), db: Session = Depends(get_db)):
    db_user = db.query(User).filter(User.id == user["user_id"]).first()
    if not db_user: raise HTTPException(404, "User not found")
    
    if not verify_password(req.currentPassword, db_user.password_hash):
        raise HTTPException(401, "Current password incorrect")

    if verify_password(req.newPassword, db_user.password_hash):
        raise HTTPException(400, "New password must be different from your current password")

    db_user.password_hash = hash_password(req.newPassword)
    db.commit()
    return {"message": "Password changed"}

@app.post("/api/upload-profile-picture")
async def upload_profile_picture(profilePicture: UploadFile = File(...), user: dict = Depends(get_current_user), db: Session = Depends(get_db)):
    if not allowed_file(profilePicture.filename):
        raise HTTPException(400, "Invalid file type")

    # Read file content
    file_content = await profilePicture.read()

    # Get file extension and mime type
    ext = profilePicture.filename.rsplit('.', 1)[1].lower()
    mime_types = {
        'jpg': 'image/jpeg',
        'jpeg': 'image/jpeg',
        'png': 'image/png',
        'gif': 'image/gif'
    }
    mime_type = mime_types.get(ext, 'image/jpeg')

    # Convert to base64 data URL
    import base64
    base64_data = base64.b64encode(file_content).decode('utf-8')
    data_url = f"data:{mime_type};base64,{base64_data}"

    # Also save to filesystem as backup
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    filename = f"user_{user['user_id']}_{timestamp}.{ext}"
    filepath = os.path.join(UPLOAD_FOLDER, filename)

    with open(filepath, "wb") as f:
        f.write(file_content)

    file_url = f"/uploads/profile_pictures/{filename}"

    # Save base64 to database (persistent) and file URL as fallback
    db_user = db.query(User).filter(User.id == user["user_id"]).first()
    if db_user:
        db_user.profile_picture = file_url  # File path as fallback
        if hasattr(db_user, 'profile_picture_data'):
            db_user.profile_picture_data = data_url  # Base64 for persistence
        db.commit()

    # Return base64 data URL for immediate display
    return {"url": data_url}

#  NEW: Chat File Upload Endpoint
MAX_UPLOAD_SIZE = 10 * 1024 * 1024  # 10MB

@app.post("/api/upload-file")
async def upload_chat_file(file: UploadFile = File(...), user: dict = Depends(get_current_user)):
    # 1. Validate File Type
    if not allowed_file(file.filename):
        raise HTTPException(400, "File type not allowed")

    # 2. Create Unique Filename
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    clean_name = re.sub(r'[^a-zA-Z0-9_.-]', '_', file.filename)
    filename = f"chat_{user['user_id']}_{timestamp}_{clean_name}"
    filepath = os.path.join(CHAT_FILES_FOLDER, filename)

    # 3. Stream to disk with size enforcement (never holds full file in memory)
    try:
        bytes_written = 0
        with open(filepath, "wb") as buffer:
            while chunk := await file.read(64 * 1024):  # 64KB chunks
                bytes_written += len(chunk)
                if bytes_written > MAX_UPLOAD_SIZE:
                    buffer.close()
                    os.remove(filepath)
                    raise HTTPException(413, f"File too large. Maximum size is {MAX_UPLOAD_SIZE // (1024*1024)}MB")
                buffer.write(chunk)
    except HTTPException:
        raise  # Preserve 413 for oversized files
    except Exception as e:
        print(f"[ERROR] File Save Error: {e}")
        raise HTTPException(500, "Could not save file")

    # 4. Return the public URL
    url = f"/uploads/chat_files/{filename}"
    return {"url": url, "filename": file.filename}

def extract_file_content(filepath: str) -> str:
    """Reads text from PDF, DOCX, or TXT files."""
    ext = filepath.split('.')[-1].lower()
    text = ""
    try:
        if ext == 'pdf':
            #  UPDATED: Uses pypdf instead of PyPDF2
            reader = pypdf.PdfReader(filepath)
            for page in reader.pages:
                text += page.extract_text() + "\n"
        elif ext in ['docx', 'doc']:
            doc = docx.Document(filepath)
            for para in doc.paragraphs:
                text += para.text + "\n"
        elif ext == 'txt':
            with open(filepath, 'r', encoding='utf-8') as f:
                text = f.read()
        else:
            return "[Image or unsupported file type - Text extraction skipped]"
    except Exception as e:
        print(f"Error reading file: {e}")
        return f"[Error reading file content: {e}]"
    
    # Limit content to ~15k chars to fit context window
    return text[:15000]

# ==============================================================================
# CHAT HELPERS
# ==============================================================================

# Tier 1: Query rewriting for follow-up resolution
from services.query_rewriter import rewrite_query, is_likely_followup

# Tier 2: Long-term user memory + Phase 1/2/3/4 helpers
from services.memory_service import (
    fetch_user_memories_sync,
    build_memory_context,
    fetch_latest_session_summary_sync,
    run_session_summary,
    retrieve_relevant_memories,
    retrieve_relevant_turns,
    embed_and_store_turn,
    consolidate_user_memories_single,
    consolidate_idle_users,
    touch_user_last_chat_at,
)


def _fetch_history_sync(user_id: int, session_id: str, limit: int = 10) -> tuple:
    """Fetch chat history + latest rolling summary in one DB session.

    Returns (turns_list, session_summary). turns_list keeps the previous
    [{user_query, bot_response}] shape so query_rewriter / is_likely_followup
    callers stay unchanged. session_summary is None until Phase 1 fires.
    """
    db = SessionLocal()
    try:
        history = db.query(ChatHistory)\
            .filter(ChatHistory.user_id == user_id, ChatHistory.session_id == session_id)\
            .order_by(ChatHistory.timestamp.desc())\
            .limit(limit)\
            .all()
        turns = [
            {"user_query": h.user_query, "bot_response": h.bot_response}
            for h in reversed(history)
        ]
        summary_row = (
            db.query(ChatHistory.session_summary)
            .filter(
                ChatHistory.user_id == user_id,
                ChatHistory.session_id == session_id,
                ChatHistory.session_summary.isnot(None),
            )
            .order_by(ChatHistory.id.desc())
            .first()
        )
        summary = summary_row[0] if summary_row else None
        return turns, summary
    finally:
        db.close()


def _build_conversation_context(history_dicts: list, session_summary: Optional[str] = None) -> str:
    """Format prior turns + optional rolling summary for the agent's context.

    Phase 1: when session_summary is present (set after ~8+ turns), inject it
    BEFORE the raw last-5-turn window so older context is preserved.
    """
    parts: list = []
    if session_summary:
        parts.append(f"EARLIER IN THIS SESSION:\n{session_summary.strip()}\n")
    if history_dicts:
        lines = ["PRIOR CONVERSATION:"]
        for h in history_dicts[-5:]:
            u = (h.get("user_query") or "").strip()
            b = (h.get("bot_response") or "").strip()
            if u:
                lines.append(f"User: {u}")
            if b:
                lines.append(f"Assistant: {b[:500]}")
        parts.append("\n".join(lines))
    return ("\n".join(parts) + "\n") if parts else ""


def _schedule_session_summary(user_id: int, session_id: str) -> None:
    """Fire-and-forget background summarization after a chat commit.

    Gated by ENABLE_SESSION_SUMMARY (default true). The task self-gates on
    turn count, so calling this on every commit is safe.
    """
    if os.getenv("ENABLE_SESSION_SUMMARY", "true").lower() not in ("1", "true", "yes"):
        return
    try:
        asyncio.create_task(asyncio.to_thread(run_session_summary, user_id, session_id))
    except RuntimeError:
        # No running event loop (sync test context). Silently skip.
        pass


def _schedule_embed_turn(chat_history_id: int) -> None:
    """Fire-and-forget Phase 4 embedding for a freshly-committed turn.

    Embeds the Q+A so future cross-session semantic recall can find it. Runs
    after the response is already sent → zero added latency.
    """
    if os.getenv("ENABLE_VERBATIM_RECALL", "true").lower() not in ("1", "true", "yes"):
        return
    try:
        asyncio.create_task(asyncio.to_thread(embed_and_store_turn, chat_history_id))
    except RuntimeError:
        pass


# ----------------------------------------------------------------------------
# Phase 3 — Real-time memory extraction (kill the 24h lag)
# ----------------------------------------------------------------------------
# Trigger extraction every 6 turns post-commit, gated by a per-user
# asyncio.Lock so a flurry of rapid turns doesn't fire concurrent extractions
# for the same user. Cross-replica safety is already handled by
# _merge_memories' substring dedup, so we don't need a distributed lock.

_realtime_extraction_locks: dict[int, asyncio.Lock] = {}


def _get_user_realtime_lock(user_id: int) -> asyncio.Lock:
    lock = _realtime_extraction_locks.get(user_id)
    if lock is None:
        lock = asyncio.Lock()
        _realtime_extraction_locks[user_id] = lock
    return lock


async def _run_extraction_locked(user_id: int) -> None:
    """Acquire the per-user lock then run extraction in a thread."""
    lock = _get_user_realtime_lock(user_id)
    if lock.locked():
        # Another coroutine is already extracting for this user — skip; their
        # run will pick up our new turn too (its hours_back=2 window covers it).
        return
    async with lock:
        await asyncio.to_thread(consolidate_user_memories_single, user_id, 2)


def _schedule_realtime_extraction(user_id: int, session_id: str) -> None:
    """Fire extraction every 6 turns for this user+session.

    Counts session turns post-commit with one cheap aggregate query. Skips
    if the per-user lock is held or the flag is off.
    """
    if os.getenv("ENABLE_REALTIME_MEMORY", "true").lower() not in ("1", "true", "yes"):
        return

    try:
        with SessionLocal() as _db:
            turn_count = (
                _db.query(ChatHistory)
                .filter(
                    ChatHistory.user_id == user_id,
                    ChatHistory.session_id == session_id,
                )
                .count()
            )
    except Exception as e:
        print(f"[MEMORY] turn-count query failed user={user_id}: {e}")
        return

    if turn_count <= 0 or turn_count % 6 != 0:
        return

    try:
        asyncio.create_task(_run_extraction_locked(user_id))
    except RuntimeError:
        pass


def _schedule_touch_last_chat(user_id: int) -> None:
    """Update users.last_chat_at = now() in the background.

    Powers the idle-sweep cron — fully best-effort, swallowed if migrate
    hasn't added the column yet.
    """
    try:
        asyncio.create_task(asyncio.to_thread(touch_user_last_chat_at, user_id))
    except RuntimeError:
        pass


def _schedule_regenerate_suggestions(user_id: int) -> None:
    """No-op: home-screen suggestions are now a single GLOBAL "Top 10 most-asked"
    list (same for every user), computed from ChatHistory by services/
    popular_questions.py and refreshed by the daily cron. Per-user AI
    personalization was removed, so we no longer burn a Gemini call per chat turn
    regenerating it. Kept as a stub so the post-commit task list is untouched."""
    return


def _schedule_post_commit_memory_tasks(
    user_id: int,
    session_id: str,
    chat_id: int,
) -> None:
    """Fire all Phase 1+3+4 background tasks after a chat turn commits.

    Runs *after* the response has been sent → zero added latency. Each
    sub-task is independently feature-flagged and self-gates on triggers.
    """
    _schedule_session_summary(user_id, session_id)
    _schedule_touch_last_chat(user_id)
    _schedule_embed_turn(chat_id)
    _schedule_realtime_extraction(user_id, session_id)
    _schedule_regenerate_suggestions(user_id)


# --- CHAT ROUTES (KB-only, with conversation memory) ---
@app.post("/chat")
async def chat_with_bot(req: QueryRequest, user=Depends(get_current_user), db: Session = Depends(get_db)):
    if not user:
        raise HTTPException(401, "Unauthorized")

    user_q = req.query.strip()
    original_q = user_q  # Preserve original for chat history (before rewrite)
    session_id = req.session_id or "default"

    # Detect file upload early
    file_match = re.search(r'uploads/chat_files/([^\)]+)', user_q)

    # Embed the query ONCE and share the vector across both semantic-recall
    # functions. Previously each (retrieve_relevant_memories + retrieve_relevant_turns)
    # embedded the same query independently -> two Vertex embed calls per turn.
    from services.embedding_util import embed_text as _embed_text
    q_vec = await asyncio.to_thread(_embed_text, user_q)

    # Parallel fetch: history (for rewriting) + long-term memory
    #   + Phase 2 semantic-fact recall + Phase 4 verbatim-turn recall.
    # The two recall tasks reuse the pre-computed q_vec, so no embedding happens
    # inside the gather (one shared embed call above instead of two).
    fetch_tasks = [
        asyncio.to_thread(_fetch_history_sync, user["user_id"], session_id, 5),
        asyncio.to_thread(fetch_user_memories_sync, user["user_id"], 10),
        asyncio.to_thread(retrieve_relevant_memories, user["user_id"], user_q, 5, 0.55, q_vec),
        asyncio.to_thread(retrieve_relevant_turns, user["user_id"], user_q, 3, 0.62, session_id, query_vec=q_vec),
    ]
    results = await asyncio.gather(*fetch_tasks, return_exceptions=True)
    if isinstance(results[0], Exception):
        history_dicts, session_summary = [], None
    else:
        history_dicts, session_summary = results[0]
    memory_dicts = results[1] if not isinstance(results[1], Exception) else []
    relevant_memories = (
        results[2] if len(results) > 2 and not isinstance(results[2], Exception) else []
    )
    relevant_turns = (
        results[3] if len(results) > 3 and not isinstance(results[3], Exception) else []
    )

    # Tier 1: Rewrite follow-up queries to be self-contained
    if USE_VERTEX_AGENT and history_dicts and is_likely_followup(user_q):
        user_q = await asyncio.to_thread(rewrite_query, user_q, history_dicts)

    # Cache check (mirrors /chat/stream). Skip for file uploads (unique content).
    # query_cache.get() internally refuses to serve personal-recall queries
    # ("what's my deadline?") from the shared cache, so this is leak-safe.
    if USE_VERTEX_AGENT and not file_match and not getattr(req, "skip_cache", False):
        _cache_ctx = get_context_hash(user["user_id"], model=req.model)
        _cached = query_cache.get(user_q, _cache_ctx)
        if _cached:
            print(f"[CACHE] HIT (/chat) for query: {user_q[:50]}...")
            _cached_cites = query_cache.get_citations(user_q, _cache_ctx)
            try:
                new_chat = ChatHistory(
                    user_id=user["user_id"],
                    session_id=session_id,
                    user_query=original_q,
                    bot_response=_cached,
                    citations=json.dumps(_cached_cites) if _cached_cites else None,
                )
                db.add(new_chat)
                db.commit()
                _schedule_post_commit_memory_tasks(user["user_id"], session_id, new_chat.id)
            except Exception as e:
                print(f"[ERROR] Failed to save cached chat history: {e}")
            return {
                "response": _cached,
                "citations": _cached_cites or [],
                "feature": suggest_feature(original_q),
            }

    memory_context = build_memory_context(memory_dicts, relevant_memories, relevant_turns)
    conversation_context = _build_conversation_context(history_dicts, session_summary)
    # Phase 1: session summary + recent-turn context rides on memory_context so
    # the ADK agent receives it via state_delta["memory"]. Falls back to no-op
    # when both are empty.
    if conversation_context:
        memory_context = conversation_context + (memory_context or "")

    # Always inject the user's SAVED PROFILE (department / title / role) as
    # authoritative context, independent of memory selection. The profile is
    # the source of truth for "what department am I in?"-type questions; relying
    # only on the mirrored memory failed when the department row got crowded out
    # of the top-N memory fetch, so the bot wrongly claimed it had no access.
    try:
        _pu = db.query(User).filter(User.id == user["user_id"]).first()
        _pbits = []
        if _pu is not None:
            if getattr(_pu, "name", None):
                _pbits.append(f"name: {_pu.name}")
            if getattr(_pu, "department", None):
                _pbits.append(f"department: {_pu.department}")
            if getattr(_pu, "title", None):
                _pbits.append(f"title: {_pu.title}")
            if getattr(_pu, "primary_role", None):
                _pbits.append(f"role: {_pu.primary_role}")
        if _pbits:
            profile_block = (
                "\nUSER PROFILE (authoritative facts the user saved about themselves; "
                "use these to answer questions like 'what department am I in?' or "
                "'what is my role?' -- never claim you don't have access to them):\n"
                + "\n".join(f"  {b}" for b in _pbits) + "\n"
            )
            memory_context = profile_block + (memory_context or "")
    except Exception as _e:
        print(f"[MEMORY] profile injection skipped: {_e}")

    print(
        f"[MEMORY] user={user['user_id']} session={session_id} "
        f"facts={len(memory_dicts)} relevant_facts={len(relevant_memories)} "
        f"relevant_turns={len(relevant_turns)} summary={'Y' if session_summary else 'N'}"
    )

    # Inject basic profile info so agent knows who they're talking to
    profile_parts = []
    if user.get("name"): profile_parts.append(f"Name: {user['name']}")
    if user.get("email"): profile_parts.append(f"Email: {user['email']}")
    if user.get("department"): profile_parts.append(f"Department: {user['department']}")
    if user.get("title"): profile_parts.append(f"Title: {user['title']}")
    if user.get("primary_role"): profile_parts.append(f"Role: {user['primary_role']}")
    profile_ctx = ""
    if profile_parts:
        profile_ctx = "USER PROFILE (from account):\n" + "\n".join(profile_parts) + "\n"

    if file_match and USE_VERTEX_AGENT:
        # File uploaded -> include file content as context for the agent
        filename = file_match.group(1)
        filepath = os.path.join(CHAT_FILES_FOLDER, filename)

        if os.path.exists(filepath):
            file_content = extract_file_content(filepath)
            clean_query = re.sub(r'\[.*?\]\(.*?\)', '', user_q).strip()
            if not clean_query:
                clean_query = "Summarize this file."

            file_context = f"{profile_ctx}{conversation_context}File Content:\n{file_content}\n"
            answer = query_agent(
                query=clean_query,
                user_id=str(user["user_id"]),
                context=file_context,
                model=req.model,
                memory_context=memory_context,
            )
        else:
            answer = "I received the file link, but I cannot find the file on the server to read it."

    elif USE_VERTEX_AGENT:
        # Vertex AI Agent Engine path
        try:
            agent_context = profile_ctx

            print(f" Vertex AI query: '{user_q[:50]}...' (user={user['user_id']}, context={len(agent_context)} chars, memory={len(memory_context)} chars, model={req.model})")
            answer = query_agent(
                query=user_q,
                user_id=str(user["user_id"]),
                context=agent_context,
                model=req.model,
                memory_context=memory_context,
            )
        except Exception as e:
            print(f"   Vertex AI Chat Error: {e}")
            answer = "I'm having trouble processing your request. Please try again."
    else:
        answer = "AI system is initializing. Please try again in a moment."

    # Store the answer in the cache for future identical questions. The
    # _should_cache() gate inside set() refuses personal-recall queries and
    # error/outage text, so this is leak-safe and won't poison on failures.
    _chat_citations = get_last_grounding().get("citations", [])
    _looks_err = (
        not answer
        or "trouble" in answer.lower()[:40]
        or "error" in answer.lower()[:50]
        or "initializing" in answer.lower()[:40]
    )
    if USE_VERTEX_AGENT and not file_match and not _looks_err:
        try:
            _cache_ctx = get_context_hash(user["user_id"], model=req.model)
            query_cache.set(user_q, answer, _cache_ctx)
            if _chat_citations:
                query_cache.set_citations(user_q, _chat_citations, _cache_ctx)
        except Exception as e:
            print(f"[CACHE] store skipped: {e}")

    # Persist user-specific chat record
    try:
        new_chat = ChatHistory(
            user_id=user["user_id"],
            session_id=session_id,
            user_query=original_q,
            bot_response=answer,
            citations=json.dumps(_chat_citations) if _chat_citations else None,
        )
        db.add(new_chat)
        db.commit()
        # Phases 1/3/4: schedule all background memory tasks (summary, embed,
        # realtime extraction, last_chat_at touch).
        _schedule_post_commit_memory_tasks(user["user_id"], session_id, new_chat.id)
    except Exception as e:
        print(f"[ERROR] Failed to save chat history: {e}")

    # Track failed queries for auto-research agent
    if answer and "error" not in answer.lower()[:50]:
        try:
            from research_agent import detect_and_log_failed_query
            detect_and_log_failed_query(original_q, answer, user["user_id"])
        except Exception:
            pass

    return {
        "response": answer,
        "citations": get_last_grounding().get("citations", []),
        "feature": suggest_feature(original_q),
    }


# ==============================================================================
# STREAMING CHAT ENDPOINT (Server-Sent Events)
# ==============================================================================
@app.post("/chat/stream")
async def chat_stream(req: QueryRequest, user=Depends(get_current_user), db: Session = Depends(get_db)):
    """Streaming chat endpoint using Server-Sent Events (SSE)."""
    if not user:
        raise HTTPException(401, "Unauthorized")

    user_q = req.query.strip()
    original_q = user_q  # Keep original for cache key + chat history
    session_id = req.session_id or "default"
    user_id = user["user_id"]

    # Embed the query ONCE and share the vector across both recall functions
    # (was two independent embed calls per turn -- one for memories, one for turns).
    from services.embedding_util import embed_text as _embed_text
    q_vec = await asyncio.to_thread(_embed_text, user_q)

    # Parallel fetch: history + memory + Phase 2 semantic facts + Phase 4 verbatim turns
    fetch_tasks = [
        asyncio.to_thread(_fetch_history_sync, user_id, session_id, 5),
        asyncio.to_thread(fetch_user_memories_sync, user_id, 10),
        asyncio.to_thread(retrieve_relevant_memories, user_id, user_q, 5, 0.55, q_vec),
        asyncio.to_thread(retrieve_relevant_turns, user_id, user_q, 3, 0.62, session_id, query_vec=q_vec),
    ]
    results = await asyncio.gather(*fetch_tasks, return_exceptions=True)
    if isinstance(results[0], Exception):
        history_dicts, session_summary = [], None
    else:
        history_dicts, session_summary = results[0]
    memory_dicts = results[1] if not isinstance(results[1], Exception) else []
    relevant_memories = (
        results[2] if len(results) > 2 and not isinstance(results[2], Exception) else []
    )
    relevant_turns = (
        results[3] if len(results) > 3 and not isinstance(results[3], Exception) else []
    )

    # Tier 1: Rewrite follow-up queries
    if history_dicts and is_likely_followup(user_q):
        user_q = await asyncio.to_thread(rewrite_query, user_q, history_dicts)

    memory_context = build_memory_context(memory_dicts, relevant_memories, relevant_turns)
    # Phase 1: prepend session summary + recent turns onto memory_context so the
    # ADK agent receives it via state_delta["memory"].
    _session_context_prefix = _build_conversation_context(history_dicts, session_summary)
    if _session_context_prefix:
        memory_context = _session_context_prefix + (memory_context or "")
    print(
        f"[MEMORY] (stream) user={user_id} session={session_id} "
        f"facts={len(memory_dicts)} relevant_facts={len(relevant_memories)} "
        f"relevant_turns={len(relevant_turns)} summary={'Y' if session_summary else 'N'}"
    )

    profile_parts = []
    if user.get("name"): profile_parts.append(f"Name: {user['name']}")
    if user.get("email"): profile_parts.append(f"Email: {user['email']}")
    if user.get("department"): profile_parts.append(f"Department: {user['department']}")
    if user.get("title"): profile_parts.append(f"Title: {user['title']}")
    if user.get("primary_role"): profile_parts.append(f"Role: {user['primary_role']}")
    agent_context = ""
    if profile_parts:
        agent_context = "USER PROFILE (from account):\n" + "\n".join(profile_parts) + "\n"

    # =========================================================================
    # KB BROWSER - Enumeration queries answered deterministically (no LLM call)
    # =========================================================================
    browse_response = try_browse(user_q, has_history=bool(history_dicts))
    if browse_response and not req.skip_cache:
        print(f"[KB_BROWSE] for query: {user_q[:50]}...")
        browse_citations_list = browse_citations(user_q, has_history=bool(history_dicts))

        async def generate_browse_sse():
            yield f"data: {json.dumps({'type': 'status', 'content': 'Browsing knowledge base...'})}\n\n"
            if browse_citations_list:
                yield f"data: {json.dumps({'type': 'citations', 'content': browse_citations_list})}\n\n"
            yield f"data: {json.dumps({'type': 'done', 'content': browse_response})}\n\n"
            try:
                with SessionLocal() as save_db:
                    new_chat = ChatHistory(
                        user_id=user_id,
                        session_id=session_id,
                        user_query=original_q,
                        bot_response=browse_response,
                        citations=json.dumps(browse_citations_list) if browse_citations_list else None,
                    )
                    save_db.add(new_chat)
                    save_db.commit()
                    new_chat_id = new_chat.id
                _schedule_post_commit_memory_tasks(user_id, session_id, new_chat_id)
            except Exception as e:
                print(f"   Chat-history save error (browse): {e}")

        return StreamingResponse(generate_browse_sse(), media_type="text/event-stream")

    # =========================================================================
    # CACHE CHECK
    # =========================================================================
    context_hash = get_context_hash(user_id, model=req.model)

    if req.skip_cache:
        print(f"[CACHE] SKIP (regenerate) for query: {user_q[:50]}...")
        cached_response = None
        import time as _time
        context_hash = f"regen_{int(_time.time())}"
        reset_session(str(user_id))
    else:
        cached_response = query_cache.get(user_q, context_hash)

    if cached_response:
        print(f"[CACHE] HIT for query: {user_q[:50]}...")
        cached_citations = query_cache.get_citations(user_q, context_hash)

        async def generate_cached_sse():
            yield f"data: {json.dumps({'type': 'status', 'content': 'Retrieved from cache'})}\n\n"
            if cached_citations:
                yield f"data: {json.dumps({'type': 'citations', 'content': cached_citations})}\n\n"
            _feat = suggest_feature(original_q)
            if _feat:
                yield f"data: {json.dumps({'type': 'feature', 'content': _feat})}\n\n"
            yield f"data: {json.dumps({'type': 'done', 'content': cached_response})}\n\n"

            try:
                with SessionLocal() as save_db:
                    new_chat = ChatHistory(
                        user_id=user_id,
                        session_id=session_id,
                        user_query=original_q,
                        bot_response=cached_response,
                        citations=json.dumps(cached_citations) if cached_citations else None,
                    )
                    save_db.add(new_chat)
                    save_db.commit()
                    new_chat_id = new_chat.id
                _schedule_post_commit_memory_tasks(user_id, session_id, new_chat_id)
            except Exception as e:
                print(f"[ERROR] Failed to save cached chat history: {e}")

        return StreamingResponse(
            generate_cached_sse(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no"
            }
        )

    # =========================================================================
    # CACHE MISS - Stream from AI agent
    # =========================================================================
    print(f"[CACHE] MISS for query: {user_q[:50]}...")
    stream_had_error = False

    async def generate_sse():
        nonlocal stream_had_error
        full_response = ""
        full_citations = []
        # Deterministic in-app feature callout (from the question, not the
        # answer). Emitted early so it's attached to the message regardless of
        # how the stream finishes; the UI only renders it once streaming ends.
        _feat = suggest_feature(original_q)
        if _feat:
            yield f"data: {json.dumps({'type': 'feature', 'content': _feat})}\n\n"
        try:
            for event in query_agent_stream(
                query=user_q,
                user_id=str(user_id),
                context=agent_context,
                model=req.model,
                memory_context=memory_context,
            ):
                event_type = event.get("type", "")
                content = event.get("content", "")

                if event_type == "status":
                    yield f"data: {json.dumps({'type': 'status', 'content': content})}\n\n"
                elif event_type == "chunk":
                    full_response += content
                    yield f"data: {json.dumps({'type': 'chunk', 'content': content})}\n\n"
                elif event_type == "citations":
                    full_citations = content or []
                    yield f"data: {json.dumps({'type': 'citations', 'content': content})}\n\n"
                elif event_type == "done":
                    full_response = content or full_response
                    yield f"data: {json.dumps({'type': 'done', 'content': full_response})}\n\n"
                elif event_type == "error":
                    stream_had_error = True
                    yield f"data: {json.dumps({'type': 'error', 'content': content})}\n\n"
                    if not full_response:
                        full_response = content
                    break

        except Exception as e:
            stream_had_error = True
            print(f"[ERROR] Streaming error: {e}")
            yield f"data: {json.dumps({'type': 'error', 'content': 'An error occurred during streaming.'})}\n\n"
            if not full_response:
                full_response = "An error occurred during streaming."

        # Fallback: if the citations event wasn't captured in the loop (e.g.
        # it arrived fused with done), recover the just-completed answer's
        # Sources from the grounding state so they still get persisted/cached.
        if not full_citations:
            full_citations = get_last_grounding().get("citations", []) or []

        # Cache the successful response
        if full_response and "error" not in full_response.lower()[:50] and "I may not have complete information" not in full_response and "don't have reliable information" not in full_response:
            if query_cache.set(user_q, full_response, context_hash):
                print(f"[CACHE] Stored response for: {user_q[:50]}...")
                query_cache.set_citations(user_q, full_citations, context_hash)

        # Save to chat history after stream completes (save original query)
        try:
            with SessionLocal() as save_db:
                new_chat = ChatHistory(
                    user_id=user_id,
                    session_id=session_id,
                    user_query=original_q,
                    bot_response=full_response,
                    citations=json.dumps(full_citations) if full_citations else None,
                )
                save_db.add(new_chat)
                save_db.commit()
                new_chat_id = new_chat.id
            _schedule_post_commit_memory_tasks(user_id, session_id, new_chat_id)
        except Exception as e:
            print(f"[ERROR] Failed to save streamed chat history: {e}")

        # Track failed queries
        if full_response and not stream_had_error and "error" not in full_response.lower()[:50]:
            try:
                from research_agent import detect_and_log_failed_query
                detect_and_log_failed_query(original_q, full_response, user_id)
            except Exception:
                pass

    return StreamingResponse(
        generate_sse(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no"
        }
    )


# ==============================================================================
# GUEST CHAT ENDPOINT (No Authentication Required)
# ==============================================================================
@app.post("/chat/guest")
async def chat_guest(req: GuestQueryRequest, request: Request):
    """Guest chat endpoint - NO authentication required, rate limited per IP."""
    client_ip = request.client.host if request.client else "unknown"

    if not check_guest_rate_limit(client_ip):
        raise HTTPException(
            status_code=429,
            detail="Rate limit exceeded. Please try again in a minute or sign up for unlimited access!"
        )

    user_q = req.query.strip()
    if not user_q:
        return {"response": "Please enter a question."}

    # Limit query length
    if len(user_q) > 500:
        user_q = user_q[:500]

    # Small talk override - greetings, acknowledgments, non-questions
    lower_q = user_q.lower().strip()
    norm = re.sub(r'[\s\W]+', '', lower_q)
    word_count = len(lower_q.split())

    greeting_patterns = ['hi', 'hey', 'heyt', 'hii', 'heyy', 'hello', 'helo', 'howdy', 'sup', 'yo', 'hola', 'greetings']
    if word_count <= 2 and (norm in greeting_patterns or re.match(r'^(hi+|hey+t?|hello+)$', norm)):
        return {"response": "Hello! I'm ORA Navigator, the assistant for Morgan State's Office of Research Administration. What can I help you with today?"}

    elif norm in ['whatsup', 'wassup', 'wazzup', 'whatsgood', 'howareyou', 'howru', 'howreyou', 'howyoudoing']:
        return {"response": "I'm doing great, thanks for asking! How can I help you with grants, compliance, or other ORA topics today?"}

    elif word_count <= 3 and re.match(r'^(bye|goodbye|see you|later|cya|peace|gotta go|gtg)', lower_q):
        return {"response": "Goodbye! Sign up for a free account to save your chat history."}

    elif re.search(r'\b(thank|thanks|thanx|thx|ty|appreciate)\b', lower_q):
        return {"response": "You're welcome! Feel free to ask more questions about ORA services."}

    elif norm in ['lol', 'lmao', 'rofl', 'haha', 'hahaha', 'hehe', 'lolol', 'xd', 'test', 'testing', 'testtest', 'asdf', 'aaa', 'zzz', 'idk', 'idc', 'nvm', 'nevermind', 'bruh', 'bro', 'dude', 'wow', 'omg', 'wtf', 'wth']:
        return {"response": "I'm here whenever you're ready! Ask me anything about Morgan State's Office of Research Administration - grants, compliance, forms, or staff contacts."}

    elif norm in ['ok', 'okay', 'okk', 'okok', 'k', 'kk', 'sure', 'alright', 'aight', 'cool', 'nice', 'great', 'good', 'gotit', 'understood', 'isee', 'ah', 'oh', 'ohh', 'hmm', 'hm', 'mhm', 'yep', 'yup', 'yes', 'yeah', 'ya', 'no', 'nope', 'nah', 'fine', 'bet', 'word', 'facts', 'true', 'right', 'correct']:
        return {"response": "Got it! Ask me anything about ORA services - grants, IRB, IACUC, COI, pre-award, post-award, forms, or staff."}

    elif len(norm) <= 2 or not any(c.isalpha() for c in user_q):
        return {"response": "I'm here to help with research administration questions at Morgan State. Ask me about grants, compliance, pre/post-award, forms, or staff contacts."}

    # =========================================================================
    # KB BROWSER - Enumeration queries answered deterministically from manifest
    # Bypasses Gemini entirely (~5ms). Falls through to agent if not a list query.
    # =========================================================================
    browse_response = try_browse(user_q, has_history=False)
    if browse_response:
        print(f"[KB_BROWSE] (guest) for: {user_q[:50]}...")
        return {"response": browse_response, "source": "kb_browser"}

    # =========================================================================
    # CACHE CHECK - Return cached response instantly for guest queries
    # =========================================================================
    cached_response = query_cache.get(user_q, context_hash="")
    if cached_response:
        print(f"[CACHE] HIT (guest) for: {user_q[:50]}...")
        return {"response": cached_response, "cached": True}

    # Use Vertex AI Agent for real questions
    if USE_VERTEX_AGENT:
        try:
            import uuid
            guest_user_id = f"guest_{uuid.uuid4().hex[:12]}"
            print(f"[CACHE] MISS (guest) for: '{user_q[:50]}...'")
            answer = query_agent(
                query=user_q,
                user_id=guest_user_id,
                context="",
            )

            if answer and "error" not in answer.lower()[:50] and "I may not have complete information" not in answer and "don't have reliable information" not in answer:
                query_cache.set(user_q, answer, context_hash="")

        except Exception as e:
            print(f"   Guest Vertex AI Error: {e}")
            answer = "I'm having trouble processing your request. Please try again."
    else:
        answer = "AI system is initializing. Please try again in a moment."

    # Track failed queries
    if answer and "error" not in answer.lower()[:50]:
        try:
            from research_agent import detect_and_log_failed_query
            detect_and_log_failed_query(user_q, answer)
        except Exception:
            pass

    return {"response": answer, "citations": get_last_grounding().get("citations", [])}


@app.get("/api/forms")
async def get_forms_catalog(
    category: str = "",
    sponsor: str = "",
    role: str = "",
    user=Depends(get_current_user),
):
    """Browseable catalog of ORA forms, templates, checklists, and memos.
    Read-only view over the bundled KB -- no LLM call. Filters intersect:
    passing two narrows further; empty filters mean "any". Unknown values
    yield an empty list rather than an error."""
    from services.forms_catalog import list_forms
    forms = list_forms(
        category=category or None,
        sponsor=sponsor or None,
        role=role or None,
    )
    return {"forms": forms, "count": len(forms)}


@app.get("/api/sample-proposals")
async def get_sample_proposals(category: str = ""):
    """Curated shelf of real, public funded proposals a PI can read for
    reference. Every entry is a hand-vetted direct link to an actual proposal
    document from an official funder or a reputable university research office.
    No LLM, no auth (the content is entirely public links). Optional ?category=
    narrows to one filter bucket; an empty or unknown value returns the full
    list. (The live Open Grants community merge was removed by product decision —
    only curated, authoritative samples are shown.)"""
    from services.sample_proposals import list_samples, categories
    proposals = list_samples(category or None)
    return {
        "proposals": proposals,
        "categories": categories(),
        "count": len(proposals),
    }


class SampleSearchRequest(BaseModel):
    query: str = ""


@app.post("/api/sample-proposals/search")
async def search_sample_proposals(
    req: SampleSearchRequest,
    user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Rank the curated sample shelf against a PI's free-text interest, enriched
    with their saved research interests. Pure deterministic keyword overlap (no
    LLM) -- returns the SAME entries reordered best-first, each matched one
    carrying a `match` {score, terms}. Auth'd so we can fold in saved interests;
    the page already sits behind RequireAuth."""
    from services.sample_proposals import list_samples, categories, rank_samples
    query = (req.query or "").strip()

    # Saved interests enrich the query (same source as the Opportunity Finder).
    interest_rows = (
        db.query(UserMemory)
        .filter(UserMemory.user_id == user["user_id"], UserMemory.memory_type == "interest")
        .order_by(UserMemory.id.asc())
        .all()
    )
    interests = ", ".join((r.content or "").strip() for r in interest_rows if (r.content or "").strip())

    items = list_samples(None)  # rank the whole shelf; the UI filters by chip on top
    ranked = rank_samples(items, f"{query} {interests}".strip())
    return {
        "proposals": ranked,
        "categories": categories(),
        "count": len(ranked),
        "matched": bool(query or interests),
    }


@app.get("/api/sample-proposals/{sample_id}/download")
async def download_sample_proposal(sample_id: str):
    """Stream the hosted PDF for an authored ("pdf"-type) sample proposal as a
    download. 404 if the id is unknown, the entry is a link (not a hosted PDF),
    or the file is missing. No auth -- the content is our own public sample."""
    from fastapi.responses import FileResponse
    from services.sample_proposals import get_sample, pdf_path
    path = pdf_path(sample_id)
    if not path:
        raise HTTPException(status_code=404, detail="Sample PDF not found")
    sample = get_sample(sample_id) or {}
    # A clean, human filename for the browser's Save dialog.
    download_name = f"{sample.get('id', 'sample-proposal')}.pdf"
    return FileResponse(path, media_type="application/pdf", filename=download_name)


class OpportunitySearchRequest(BaseModel):
    description: str


@app.post("/api/opportunities/search")
async def search_opportunities(
    req: OpportunitySearchRequest,
    user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Opportunity Finder: a PI's free-text research description -> ranked list of
    live, OPEN federal opportunities (Grants.gov), each with a grounded fit
    explanation, a deterministic institution-eligibility verdict, a PI-level
    eligibility advisory, and a mechanism note. The PI's saved interests enrich
    the query. Returns [] (not an error) when the federal API is unreachable, so
    the UI degrades gracefully."""
    description = (req.description or "").strip()
    if not description:
        raise HTTPException(status_code=422, detail="A research description is required.")

    # Enrich the query with the user's saved interests (multi-value, in memories).
    interest_rows = (
        db.query(UserMemory)
        .filter(UserMemory.user_id == user["user_id"], UserMemory.memory_type == "interest")
        .order_by(UserMemory.id.asc())
        .all()
    )
    interests = ", ".join((r.content or "").strip() for r in interest_rows if (r.content or "").strip())

    from services.opportunity_finder import find_opportunities
    results = find_opportunities(description, profile={"interests": interests})
    return {"opportunities": results, "count": len(results)}


@app.get("/chat-history")
async def get_chat_history(user=Depends(get_current_user), db: Session = Depends(get_db)):
    """Fetch chat history for the logged-in user."""
    chats = db.query(ChatHistory)\
              .filter(ChatHistory.user_id == user["user_id"])\
              .order_by(ChatHistory.timestamp.asc())\
              .all()
    history = []
    for c in chats:
        try:
            cites = json.loads(c.citations) if c.citations else []
        except (ValueError, TypeError):
            cites = []
        history.append({
            "session_id": c.session_id or "default",
            "user": c.user_query,
            "bot": c.bot_response,
            "citations": cites,
            "time": iso_utc(c.timestamp)
        })
    return {"history": history}


@app.post("/reset-history")
async def reset_chat_history(user=Depends(get_current_user), db: Session = Depends(get_db)):
    """Delete history only for this user"""
    db.query(ChatHistory).filter(ChatHistory.user_id == user["user_id"]).delete()
    db.commit()
    return {"message": "Chat history reset."}


@app.delete("/api/sessions/{session_id}")
async def delete_session(session_id: str, user=Depends(get_current_user), db: Session = Depends(get_db)):
    """Delete a single chat session for the logged-in user."""
    deleted = db.query(ChatHistory).filter(
        ChatHistory.user_id == user["user_id"],
        ChatHistory.session_id == session_id,
    ).delete()
    db.commit()
    if deleted == 0:
        raise HTTPException(404, "Session not found")
    return {"message": "Session deleted", "deleted_messages": deleted}


# --- Voice Mode Endpoints ---
@app.post("/api/tts")
async def text_to_speech(req: TTSRequest, _user=Depends(get_current_user)):
    """Convert text to speech using OpenAI TTS API"""
    if not OPENAI_API_KEY:
        raise HTTPException(500, "OpenAI API key not configured")

    try:
        from openai import OpenAI
        client = OpenAI(api_key=OPENAI_API_KEY)

        # Use TTS-1 for speed (tts-1-hd for quality but slower)
        response = client.audio.speech.create(
            model="tts-1",
            voice=req.voice,
            input=req.text[:4096],  # Limit to 4096 chars
            response_format="mp3"
        )

        # Stream the audio response
        audio_data = io.BytesIO(response.content)
        return StreamingResponse(
            audio_data,
            media_type="audio/mpeg",
            headers={"Content-Disposition": "inline; filename=response.mp3"}
        )
    except Exception as e:
        print(f"TTS Error: {e}")
        raise HTTPException(500, f"TTS generation failed: {str(e)}")

# ==============================================================================
# HOME-SCREEN SUGGESTION POOL (shared by guest endpoint + cold-start path)
# ==============================================================================
# Single source of truth for the default ORA-themed question set. Sampled by:
#   - GET /api/popular-questions             (guests / unauthenticated)
#   - GET /api/me/suggested-questions        (cold-start: <3 turns / <2 facts)
#   - services/suggestion_generator.py       (filler when LLM output fails validation)
DEFAULT_QUESTION_POOL = [
    # Pre-award
    "How do I find funding opportunities for my research?",
    "What is the process for submitting a grant proposal?",
    "Who reviews and approves proposals before submission?",
    "What are the deadlines for upcoming NSF and NIH submissions?",
    "How do I prepare a budget for a federal grant?",
    "What is Morgan State's federal F&A (indirect cost) rate?",
    "What fringe benefit rate should I use for faculty and staff?",
    "Where do I find Morgan State's UEI, EIN, FWA, and other institutional IDs?",
    "How do I get an Advance Account before my award is fully set up?",
    # Post-award
    "How do I set up a new grant account after an award is made?",
    "What are the rules for spending grant funds on travel or equipment?",
    "How do I request a no-cost extension on an active award?",
    "How do I close out a grant at the end of the project period?",
    "When are effort reports due and how do I certify mine?",
    "How do I add a subaward to an existing grant?",
    # Compliance (IRB, IACUC, COI)
    "How do I submit an IRB application for human subjects research?",
    "When do I need IACUC approval for animal research?",
    "What is required for a Conflict of Interest disclosure?",
    "Where can I find training requirements for research compliance (CITI)?",
    "How long does IRB approval typically take and when does the IRB meet?",
    "Which IACUC SOPs apply to my animal study?",
    "What do I need to know about NSPM-33 and research security?",
    "How do I report a research-related incident or protocol deviation?",
    # Forms & process
    "Where can I find the internal routing form for proposal submission?",
    "What forms do I need to add a co-investigator after an award?",
    "Where are the standard ORA proposal-prep templates and checklists?",
    # Staff & contacts
    "Who is the contact for pre-award support in my department?",
    "How do I reach the Office of Research Administration leadership?",
    "Who handles subaward and subcontract questions?",
    "Who do I contact about IRB or IACUC submissions?",
    # Trainings & resources
    "What does the monthly D-RED seminar cover and when is it held?",
    "Where can I find the New Faculty Development Seminar schedule?",
    "Where is the PI Handbook and what's the latest version?",
    # General
    "What services does the Office of Research Administration provide?",
    "How do I get started as a new PI at Morgan State?",
    "Where can I find current research policies and procedures?",
]


@app.get("/api/popular-questions")
async def get_popular_questions(db: Session = Depends(get_db)):
    """Global Top-10 most-asked ORA questions: the curated pool ranked by how
    many DISTINCT users have asked about each (services/popular_questions.py).
    The SAME list for everyone (guests and authenticated users). Cached + daily
    cron; degrades to the curated pool order when history is thin."""
    from services.popular_questions import get_top_questions
    try:
        questions = get_top_questions(db, DEFAULT_QUESTION_POOL, 10)
    except Exception as e:
        print(f"[POPULAR] get_popular_questions failed: {e}")
        questions = DEFAULT_QUESTION_POOL[:10]
    return {"questions": questions, "source": "popular"}

@app.get("/health")
def health():
    if USE_VERTEX_AGENT:
        try:
            result = check_agent_health()
            ai_status = result.get("status", "offline") if isinstance(result, dict) else "offline"
        except Exception:
            ai_status = "offline"
        return {"status": "ok", "db": "connected", "ai": "ready" if ai_status == "connected" else "offline"}
    return {"status": "ok", "db": "connected", "ai": "offline"}

# ==============================================================================
# ADMIN DASHBOARD ENDPOINTS
# ==============================================================================

# --- Admin: User Management ---
@app.get("/api/admin/users")
async def get_all_users(
    search: Optional[str] = None,
    role: Optional[str] = None,
    user: dict = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Get all users (admin only)"""
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")

    query = db.query(User).order_by(User.created_at.desc())

    if search:
        search_term = f"%{search}%"
        query = query.filter(
            (User.email.ilike(search_term)) |
            (User.name.ilike(search_term))
        )

    if role and role != "all":
        query = query.filter(User.role == role)

    users = query.all()

    return {
        "users": [
            {
                "id": u.id,
                "email": u.email,
                "name": u.name,
                "role": u.role,
                "created_at": iso_utc(u.created_at)
            }
            for u in users
        ],
        "total": len(users)
    }

@app.get("/api/admin/users/stats")
async def get_user_stats(user: dict = Depends(get_current_user), db: Session = Depends(get_db)):
    """Get user statistics (admin only)"""
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")

    from datetime import timedelta
    now = datetime.now(timezone.utc)
    week_ago = now - timedelta(days=7)
    month_ago = now - timedelta(days=30)

    total_users = db.query(User).count()
    total_regular = db.query(User).filter(User.role == "user").count()
    total_admins = db.query(User).filter(User.role == "admin").count()
    new_this_week = db.query(User).filter(User.created_at >= week_ago).count()
    new_this_month = db.query(User).filter(User.created_at >= month_ago).count()

    return {
        "total": total_users,
        "users": total_regular,
        "admins": total_admins,
        "new_this_week": new_this_week,
        "new_this_month": new_this_month
    }

@app.put("/api/admin/users/{user_id}/role")
async def update_user_role(
    user_id: int,
    new_role: str,
    user: dict = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Update user role (admin only)"""
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")

    if new_role not in ["user", "admin"]:
        raise HTTPException(status_code=400, detail="Role must be 'user' or 'admin'")

    target_user = db.query(User).filter(User.id == user_id).first()
    if not target_user:
        raise HTTPException(status_code=404, detail="User not found")

    target_user.role = new_role
    db.commit()

    return {"message": f"User {target_user.email} role updated to {new_role}"}

# --- Admin: System Health ---
@app.get("/api/admin/health")
async def get_system_health(user: dict = Depends(get_current_user), db: Session = Depends(get_db)):
    """Get detailed system health (admin only)"""
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")

    health_status = {
        "database": {"status": "unknown", "message": ""},
        "vertex_agent": {"status": "unknown", "message": ""},
        "openai_tts": {"status": "unknown", "message": ""},
        "mode": "vertex_ai" if USE_VERTEX_AGENT else "legacy_rag",
        "last_check": iso_utc(datetime.now(timezone.utc))
    }

    # Check Database
    try:
        db.execute(text("SELECT 1"))
        health_status["database"] = {"status": "connected", "message": "Database connection OK"}
    except Exception as e:
        health_status["database"] = {"status": "error", "message": str(e)[:100]}

    # Check Vertex AI Agent
    if USE_VERTEX_AGENT:
        health_status["vertex_agent"] = check_agent_health()
    else:
        health_status["vertex_agent"] = {"status": "not_configured", "message": "USE_VERTEX_AGENT disabled"}

    # Check OpenAI TTS
    try:
        if OPENAI_API_KEY:
            health_status["openai_tts"] = {"status": "configured", "message": "TTS API key present"}
        else:
            health_status["openai_tts"] = {"status": "not_configured", "message": "TTS unavailable (no OpenAI key)"}
    except Exception as e:
        health_status["openai_tts"] = {"status": "error", "message": str(e)[:100]}

    return health_status

# --- Admin: Knowledge Base Management ---
DATA_SOURCES_DIR = os.path.join(BACKEND_DIR, "data_sources")

@app.get("/api/admin/knowledge-base/files")
async def list_kb_files(user: dict = Depends(get_current_user)):
    """List all knowledge base JSON files (admin only)"""
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")

    files = []
    if os.path.exists(DATA_SOURCES_DIR):
        for f in os.listdir(DATA_SOURCES_DIR):
            if f.endswith(".json"):
                filepath = os.path.join(DATA_SOURCES_DIR, f)
                size = os.path.getsize(filepath)
                modified = datetime.fromtimestamp(os.path.getmtime(filepath))
                files.append({
                    "filename": f,
                    "size": size,
                    "modified": iso_utc(modified)
                })

    return {"files": sorted(files, key=lambda x: x["filename"])}

@app.get("/api/admin/knowledge-base/search")
async def search_kb_files(q: str, user: dict = Depends(get_current_user)):
    """Search across all knowledge base files (admin only)"""
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")

    if not q or len(q) < 2:
        return {"results": []}

    results = []
    search_term = q.lower()

    if os.path.exists(DATA_SOURCES_DIR):
        for filename in os.listdir(DATA_SOURCES_DIR):
            if not filename.endswith(".json"):
                continue

            filepath = os.path.join(DATA_SOURCES_DIR, filename)
            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    content = f.read()

                content_lower = content.lower()

                # Find ALL matches in this file
                idx = 0
                match_count = 0
                while True:
                    idx = content_lower.find(search_term, idx)
                    if idx == -1:
                        break

                    match_count += 1

                    # Get context around match (80 chars before and after)
                    start = max(0, idx - 80)
                    end = min(len(content), idx + len(q) + 80)
                    context = content[start:end]

                    # Clean up context (remove newlines for display)
                    context = context.replace('\n', ' ').replace('\r', '')

                    # Find the match in context and highlight it
                    match_start_in_context = idx - start
                    actual_match = content[idx:idx+len(q)]

                    # Build highlighted context
                    highlighted = (
                        context[:match_start_in_context] +
                        f"<mark>{actual_match}</mark>" +
                        context[match_start_in_context + len(q):]
                    )

                    results.append({
                        "filename": filename,
                        "context": "..." + highlighted.strip() + "...",
                        "position": idx,
                        "match_number": match_count
                    })

                    idx += len(q)

                    # Limit matches per file to 10
                    if match_count >= 10:
                        break

            except Exception:
                continue

    # Sort by filename, then position
    results.sort(key=lambda x: (x["filename"], x.get("position", 0)))

    return {"results": results[:50], "total_matches": len(results)}

@app.get("/api/admin/knowledge-base/{filename}")
async def get_kb_file(filename: str, user: dict = Depends(get_current_user)):
    """Get content of a knowledge base file (admin only)"""
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")

    if not filename.endswith(".json"):
        raise HTTPException(status_code=400, detail="Only JSON files allowed")

    # Prevent path traversal: strip directory components
    safe_filename = os.path.basename(filename)
    filepath = os.path.join(DATA_SOURCES_DIR, safe_filename)
    if not os.path.realpath(filepath).startswith(os.path.realpath(DATA_SOURCES_DIR)):
        raise HTTPException(status_code=400, detail="Invalid filename")
    if not os.path.exists(filepath):
        raise HTTPException(status_code=404, detail="File not found")

    try:
        with open(filepath, "r", encoding="utf-8") as f:
            content = json.load(f)
        return {"filename": safe_filename, "content": content}
    except json.JSONDecodeError as e:
        raise HTTPException(status_code=500, detail=f"Invalid JSON: {str(e)}")

@app.put("/api/admin/knowledge-base/{filename}")
async def update_kb_file(filename: str, content: dict, user: dict = Depends(get_current_user)):
    """Update a knowledge base file (admin only)"""
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")

    if not filename.endswith(".json"):
        raise HTTPException(status_code=400, detail="Only JSON files allowed")

    # Prevent path traversal
    safe_filename = os.path.basename(filename)
    filepath = os.path.join(DATA_SOURCES_DIR, safe_filename)
    if not os.path.realpath(filepath).startswith(os.path.realpath(DATA_SOURCES_DIR)):
        raise HTTPException(status_code=400, detail="Invalid filename")

    # Create backup
    if os.path.exists(filepath):
        backup_path = filepath + ".backup"
        shutil.copy(filepath, backup_path)

    try:
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(content, f, indent=2, ensure_ascii=False)
        return {"message": f"File {filename} updated successfully"}
    except Exception as e:
        # Restore backup on failure
        if os.path.exists(filepath + ".backup"):
            shutil.copy(filepath + ".backup", filepath)
        raise HTTPException(status_code=500, detail=f"Failed to save: {str(e)}")

@app.post("/api/admin/knowledge-base/ingest")
async def trigger_ingestion(user: dict = Depends(get_current_user)):
    """Trigger knowledge base re-ingestion (admin only)"""
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")

    try:
        # Legacy Pinecone ingestion removed. Using Vertex AI structured datastore now.
        return {"message": "Ingestion not needed. Using Vertex AI structured datastore (instant updates via admin dashboard)."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Ingestion failed: {str(e)}")


@app.post("/api/admin/knowledge-base/sync-all")
async def sync_all_kb(user: dict = Depends(get_current_user)):
    """One-click: clear all answer caches so KB edits surface immediately.
    Retrieval is agent-first (Vertex AI Search datastore), so there is no
    separate search index to re-ingest -- KB doc edits are live at once."""
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")

    results = {"cache": None}

    # Clear all caches (L1 + L2 + semantic) so updated KB docs surface right away
    try:
        cleared = query_cache.clear()
        results["cache"] = {"status": "ok", "cleared": cleared}
    except Exception as e:
        results["cache"] = {"status": "error", "reason": str(e)[:200]}

    return {
        "success": True,
        "message": "Cache cleared. KB edits are live via the Vertex AI datastore.",
        "details": results,
    }

# --- Admin: Cloud Knowledge Base (Vertex AI Datastore) ---
from datastore_manager import (
    list_datastore_documents,
    get_document_content,
    upload_document,
    delete_document,
    update_document,
    sync_datastore,
    search_documents as search_cloud_kb,
)

_cloud_kb_cache = {"docs": None, "ts": 0}

@app.get("/api/admin/cloud-kb/documents")
async def list_cloud_kb_docs(user: dict = Depends(get_current_user), refresh: bool = False):
    """List all documents in the Vertex AI Search datastore. Cached for 60s."""
    import time as _t
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    try:
        # Use cached result if fresh (60s TTL) unless forced refresh
        if not refresh and _cloud_kb_cache["docs"] and _t.time() - _cloud_kb_cache["ts"] < 60:
            docs = _cloud_kb_cache["docs"]
            print(f"[CACHE] Cloud KB docs from cache ({len(docs)} docs)")
        else:
            docs = await asyncio.to_thread(list_datastore_documents)
            _cloud_kb_cache["docs"] = docs
            _cloud_kb_cache["ts"] = _t.time()
        return {"documents": docs, "total": len(docs)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to list documents: {e}")

@app.get("/api/admin/cloud-kb/documents/{doc_id}/content")
async def read_cloud_kb_doc(doc_id: str, uri: str = "", user: dict = Depends(get_current_user)):
    """Read content of a document from the structured datastore"""
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    try:
        content = get_document_content(doc_id)
        return {"content": content, "doc_id": doc_id}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to read document: {e}")

@app.post("/api/admin/cloud-kb/upload")
async def upload_cloud_kb_doc(
    file: UploadFile = File(...),
    user: dict = Depends(get_current_user)
):
    """Upload a new document to the cloud KB"""
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")

    allowed_exts = {'txt', 'pdf', 'html', 'csv', 'json'}
    ext = file.filename.rsplit('.', 1)[-1].lower() if '.' in file.filename else ''
    if ext not in allowed_exts:
        raise HTTPException(status_code=400, detail=f"Allowed types: {', '.join(allowed_exts)}")

    content = await file.read()
    content_type = file.content_type or "text/plain"

    result = upload_document(file.filename, content, content_type)
    if not result["success"]:
        raise HTTPException(status_code=500, detail=result["message"])
    # Auto-clear cache so chatbot uses fresh data
    cleared = query_cache.clear()
    result["cache_cleared"] = cleared
    return result

@app.put("/api/admin/cloud-kb/documents/{doc_id}")
async def update_cloud_kb_doc(
    doc_id: str,
    request: Request,
    user: dict = Depends(get_current_user)
):
    """Update content of an existing document in the cloud KB"""
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")

    body = await request.json()
    content = body.get("content", "")
    if not content:
        raise HTTPException(status_code=400, detail="Content required")

    result = update_document(doc_id, content.encode("utf-8"))
    if not result["success"]:
        raise HTTPException(status_code=500, detail=result["message"])
    # Clear ALL caches + reset ALL ADK sessions so chatbot uses fresh data
    cleared = query_cache.clear()
    # Reset all ADK sessions so no agent reuses stale context
    try:
        from vertex_agent import _session_cache
        session_count = len(_session_cache)
        _session_cache.clear()
    except Exception:
        session_count = 0
    result["cache_cleared"] = cleared
    result["sessions_reset"] = session_count
    return result

@app.delete("/api/admin/cloud-kb/documents/{doc_id}")
async def delete_cloud_kb_doc(doc_id: str, uri: str = "", user: dict = Depends(get_current_user)):
    """Delete a document from the cloud KB"""
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")

    result = delete_document(doc_id, uri)
    if not result["success"]:
        raise HTTPException(status_code=500, detail=result["message"])
    # Auto-clear cache so chatbot uses fresh data
    cleared = query_cache.clear()
    result["cache_cleared"] = cleared
    return result

@app.post("/api/admin/cloud-kb/sync")
async def sync_cloud_kb(user: dict = Depends(get_current_user)):
    """Re-sync all GCS documents into the datastore"""
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")

    result = sync_datastore()
    if not result["success"]:
        raise HTTPException(status_code=500, detail=result["message"])
    # Auto-clear cache so chatbot uses fresh data
    cleared = query_cache.clear()
    result["cache_cleared"] = cleared
    return result


# ==============================================================================
# CACHE MANAGEMENT ENDPOINTS
# ==============================================================================

@app.get("/api/cache/stats")
async def get_cache_stats_public():
    """Get cache statistics (public, read-only)."""
    stats = query_cache.get_stats()
    return {
        "success": True,
        "cache_stats": stats,
        "cache_type": "multi-tier (L1: in-memory, L2: Redis)"
    }

@app.get("/api/admin/cache/stats")
async def get_cache_stats_admin(user: dict = Depends(get_current_user)):
    """Get cache statistics - admin version with more details."""
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")

    stats = query_cache.get_stats()
    return {
        "success": True,
        "cache_stats": stats
    }

@app.post("/api/admin/cache/clear")
async def clear_cache(user: dict = Depends(get_current_user)):
    """Clear all cached responses"""
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")

    cleared_count = query_cache.clear()
    return {
        "success": True,
        "message": f"Cleared {cleared_count} cached items"
    }

@app.get("/api/admin/cloud-kb/search")
async def search_cloud_kb_docs(q: str, user: dict = Depends(get_current_user)):
    """Search across all cloud KB documents"""
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    if not q or len(q) < 2:
        return {"results": []}
    try:
        results = search_cloud_kb(q)
        return {"results": results, "query": q, "total": len(results)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Search failed: {e}")

# --- Admin: Analytics ---
@app.get("/api/admin/analytics")
async def get_analytics(user: dict = Depends(get_current_user), db: Session = Depends(get_db)):
    """Get usage analytics (admin only)"""
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")

    from datetime import timedelta
    now = datetime.now(timezone.utc)

    # User signups by day (last 7 days)
    signups_by_day = []
    for i in range(6, -1, -1):
        day = now - timedelta(days=i)
        day_start = day.replace(hour=0, minute=0, second=0, microsecond=0)
        day_end = day_start + timedelta(days=1)
        count = db.query(User).filter(
            User.created_at >= day_start,
            User.created_at < day_end
        ).count()
        signups_by_day.append({
            "date": day_start.strftime("%Y-%m-%d"),
            "day": day_start.strftime("%a"),
            "count": count
        })

    # Ticket stats
    total_tickets = db.query(SupportTicket).count()
    open_tickets = db.query(SupportTicket).filter(SupportTicket.status == "open").count()

    return {
        "signups_by_day": signups_by_day,
        "total_users": db.query(User).count(),
        "total_tickets": total_tickets,
        "open_tickets": open_tickets,
        "timestamp": iso_utc(now)
    }

# ==============================================================================
# SUPPORT TICKET ENDPOINTS
# ==============================================================================

@app.get("/api/tickets")
async def list_tickets(status: str = None, user: dict = Depends(get_current_user), db: Session = Depends(get_db)):
    """List tickets - admins see all, users see their own"""
    query = db.query(SupportTicket)
    if user.get("role") != "admin":
        query = query.filter(SupportTicket.user_id == user["user_id"])
    if status and status != "all":
        query = query.filter(SupportTicket.status == status)
    tickets = query.order_by(SupportTicket.created_at.desc()).all()
    return {
        "tickets": [
            {
                "id": t.id,
                "subject": t.subject,
                "category": t.category,
                "description": t.description,
                "status": t.status,
                "priority": t.priority,
                "user_email": db.query(User).filter(User.id == t.user_id).first().email if t.user_id else "Unknown",
                "attachment_name": t.attachment_name,
                "attachment_data": t.attachment_data if t.attachment_data else None,
                "admin_notes": t.admin_notes,
                "created_at": iso_utc(t.created_at),
                "updated_at": iso_utc(t.updated_at),
            }
            for t in tickets
        ]
    }

@app.get("/api/tickets/stats/summary")
async def get_ticket_stats(user: dict = Depends(get_current_user), db: Session = Depends(get_db)):
    """Get ticket statistics"""
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    total = db.query(SupportTicket).count()
    open_count = db.query(SupportTicket).filter(SupportTicket.status == "open").count()
    in_progress = db.query(SupportTicket).filter(SupportTicket.status == "in_progress").count()
    resolved = db.query(SupportTicket).filter(SupportTicket.status == "resolved").count()
    return {"total": total, "open": open_count, "in_progress": in_progress, "resolved": resolved}

@app.post("/api/tickets")
async def create_ticket(request: Request, user: dict = Depends(get_current_user), db: Session = Depends(get_db)):
    """Create a new support ticket"""
    body = await request.json()
    subject = (body.get("subject", "") or "")[:200]
    description = (body.get("description", "") or "")[:5000]
    category = body.get("category", "other") or "other"
    priority = body.get("priority", "normal") or "normal"
    attachment_data = body.get("attachment_data")
    # Cap base64 attachment at ~7.5MB (10MB file base64-encoded)
    if attachment_data and len(attachment_data) > 10_000_000:
        raise HTTPException(413, "Attachment too large")
    ticket = SupportTicket(
        user_id=user["user_id"],
        subject=subject,
        category=category,
        description=description,
        priority=priority,
        attachment_data=attachment_data,
        attachment_name=(body.get("attachment_name", "") or "")[:255],
    )
    db.add(ticket)
    db.commit()
    db.refresh(ticket)
    return {"success": True, "ticket_id": ticket.id}

@app.put("/api/tickets/{ticket_id}")
async def update_ticket(ticket_id: int, request: Request, user: dict = Depends(get_current_user), db: Session = Depends(get_db)):
    """Update ticket status/notes"""
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    ticket = db.query(SupportTicket).filter(SupportTicket.id == ticket_id).first()
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")
    body = await request.json()
    if "status" in body:
        ticket.status = body["status"]
        if body["status"] == "resolved":
            ticket.resolved_by = user["user_id"]
            ticket.resolved_at = datetime.now(timezone.utc)
    if "admin_notes" in body:
        ticket.admin_notes = body["admin_notes"]
    db.commit()
    return {"success": True}


# ==============================================================================
# FEEDBACK ENDPOINTS
# ==============================================================================

@app.post("/api/feedback")
async def submit_feedback(request: Request, user: dict = Depends(get_current_user)):
    """Submit feedback on a bot response (helpful/not_helpful/report)."""
    body = await request.json()
    message_text = body.get("message_text", "")
    feedback_type = body.get("feedback_type", "")
    report_details = body.get("report_details", "")
    session_id = body.get("session_id", "default")

    if feedback_type not in ("helpful", "not_helpful", "report"):
        raise HTTPException(status_code=400, detail="Invalid feedback type")

    with SessionLocal() as db:
        fb = Feedback(
            user_id=user.get("user_id"),
            session_id=session_id,
            message_text=message_text[:2000],
            feedback_type=feedback_type,
            report_details=report_details[:1000] if report_details else None,
        )
        db.add(fb)
        db.commit()

    # If "report" (explicit bug report), log as failed query for research.
    # "not_helpful" alone is NOT logged - users thumb-down for many reasons
    # (too verbose, wrong tone, etc.) that don't indicate a KB miss.
    # Only "report" means "this answer is factually wrong or missing info".
    if feedback_type == "report" and message_text:
        try:
            from models import FailedQuery
            with SessionLocal() as db:
                chat = db.query(ChatHistory).filter(
                    ChatHistory.user_id == user.get("user_id"),
                    ChatHistory.bot_response.contains(message_text[:100])
                ).order_by(ChatHistory.timestamp.desc()).first()
                if chat:
                    # Don't duplicate: check if this query was already logged
                    existing = db.query(FailedQuery).filter(
                        FailedQuery.user_query == chat.user_query.strip(),
                        FailedQuery.user_id == user.get("user_id"),
                    ).first()
                    if not existing:
                        entry = FailedQuery(
                            user_query=chat.user_query.strip(),
                            bot_response=chat.bot_response[:1000],
                            user_id=user.get("user_id"),
                            status="new",
                        )
                        db.add(entry)
                        db.commit()
        except Exception:
            pass

    return {"success": True}

@app.get("/api/feedback/stats")
async def get_feedback_stats(user: dict = Depends(get_current_user), db: Session = Depends(get_db)):
    """Get feedback statistics"""
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    total = db.query(Feedback).count()
    helpful = db.query(Feedback).filter(Feedback.feedback_type == "helpful").count()
    not_helpful = db.query(Feedback).filter(Feedback.feedback_type == "not_helpful").count()
    reports = db.query(Feedback).filter(Feedback.feedback_type == "report").count()
    satisfaction_rate = round((helpful / total * 100) if total > 0 else 0, 1)

    # Recent reports
    recent_reports = db.query(Feedback).filter(
        Feedback.feedback_type == "report"
    ).order_by(Feedback.timestamp.desc()).limit(10).all()

    return {
        "total": total,
        "helpful": helpful,
        "not_helpful": not_helpful,
        "reports": reports,
        "satisfaction_rate": satisfaction_rate,
        "recent_reports": [
            {
                "id": r.id,
                "message_preview": (r.message_text[:150] + "...") if r.message_text and len(r.message_text) > 150 else r.message_text,
                "message_text": r.message_text,  # full reported response, for the detail view
                "details": r.report_details,
                "timestamp": iso_utc(r.timestamp),
            }
            for r in recent_reports
        ]
    }

@app.get("/api/feedback/all")
async def get_all_feedback(type: str = None, user: dict = Depends(get_current_user), db: Session = Depends(get_db)):
    """Get all feedback entries"""
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    query = db.query(Feedback)
    if type and type != "all":
        query = query.filter(Feedback.feedback_type == type)
    else:
        # "All" means all *actionable* feedback: not-helpful + reports. Plain
        # "helpful" ratings carry no user comment, so they're only a count
        # (shown in the stats cards), not part of this list.
        query = query.filter(Feedback.feedback_type.in_(["not_helpful", "report"]))

    # A not-helpful rating with no comment is just a count (reflected in the
    # stats card). Only surface not-helpful entries that actually carry a
    # comment, so the list stays actionable. Reports always show.
    query = query.filter(
        (Feedback.feedback_type != "not_helpful")
        | ((Feedback.report_details.isnot(None)) & (Feedback.report_details != ""))
    )
    items = query.order_by(Feedback.timestamp.desc()).limit(100).all()
    return {
        "feedback": [
            {
                "id": f.id,
                "user_id": f.user_id,
                "session_id": f.session_id,
                "message_text": f.message_text,
                "feedback_type": f.feedback_type,
                "report_details": f.report_details,
                "timestamp": iso_utc(f.timestamp),
            }
            for f in items
        ]
    }


# ==============================================================================
# AUTO-RESEARCH AGENT ENDPOINTS
# ==============================================================================

from research_agent import run_research_batch, get_research_stats
from models import FailedQuery, KBSuggestion

@app.post("/api/admin/research/run")
async def trigger_research(user: dict = Depends(get_current_user)):
    """Manually trigger a research batch (admin only)."""
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    result = await asyncio.to_thread(run_research_batch)
    return result

@app.get("/api/admin/research/stats")
async def research_stats_endpoint(user: dict = Depends(get_current_user)):
    """Get research agent stats for dashboard."""
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    return get_research_stats()

@app.get("/api/admin/research/suggestions")
async def list_suggestions(status: str = "pending", user: dict = Depends(get_current_user)):
    """List KB suggestions from the research agent."""
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    with SessionLocal() as db:
        query = db.query(KBSuggestion)
        if status != "all":
            query = query.filter(KBSuggestion.status == status)
        suggestions = query.order_by(KBSuggestion.created_at.desc()).limit(100).all()
        return {"suggestions": [{
            "id": s.id, "cluster_id": s.cluster_id, "topic": s.topic,
            "representative_query": s.representative_query, "query_count": s.query_count,
            "researched_answer": s.researched_answer,
            "sources": json.loads(s.sources) if s.sources else [],
            "confidence": s.confidence, "suggested_doc_id": s.suggested_doc_id,
            "suggested_content": s.suggested_content, "status": s.status,
            "admin_notes": s.admin_notes,
            "created_at": iso_utc(s.created_at) or "",
        } for s in suggestions]}

@app.put("/api/admin/research/suggestions/{suggestion_id}")
async def review_suggestion(suggestion_id: int, request: Request, user: dict = Depends(get_current_user)):
    """Approve, reject, or edit a KB suggestion."""
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    body = await request.json()
    action = body.get("action")

    with SessionLocal() as db:
        suggestion = db.query(KBSuggestion).filter(KBSuggestion.id == suggestion_id).first()
        if not suggestion:
            raise HTTPException(status_code=404, detail="Suggestion not found")

        if action == "approve":
            suggestion.status = "approved"
            suggestion.reviewed_by = user.get("user_id")
            suggestion.reviewed_at = datetime.now(timezone.utc)
        elif action == "reject":
            suggestion.status = "rejected"
            suggestion.admin_notes = body.get("notes", "")
            suggestion.reviewed_by = user.get("user_id")
            suggestion.reviewed_at = datetime.now(timezone.utc)
        elif action == "edit":
            if "content" in body:
                suggestion.suggested_content = body["content"]
            if "doc_id" in body:
                suggestion.suggested_doc_id = body["doc_id"]
            if "notes" in body:
                suggestion.admin_notes = body["notes"]

        db.commit()
    return {"success": True}

@app.post("/api/admin/research/suggestions/{suggestion_id}/push")
async def push_suggestion(suggestion_id: int, user: dict = Depends(get_current_user)):
    """Push an approved suggestion to the live KB datastore."""
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")

    with SessionLocal() as db:
        suggestion = db.query(KBSuggestion).filter(
            KBSuggestion.id == suggestion_id,
            KBSuggestion.status == "approved"
        ).first()
        if not suggestion:
            raise HTTPException(status_code=404, detail="Approved suggestion not found")

        doc_id = suggestion.suggested_doc_id
        content = suggestion.suggested_content
        if not doc_id or not content:
            raise HTTPException(status_code=400, detail="Missing doc_id or content")

        # Check if doc exists -> append; otherwise -> create
        existing = get_document_content(doc_id)
        if existing and not existing.startswith("Error"):
            merged = existing.rstrip() + "\n\n" + content
            result = update_document(doc_id, merged.encode("utf-8"))
        else:
            result = upload_document(f"{doc_id}.txt", content.encode("utf-8"))

        if result["success"]:
            suggestion.status = "pushed"
            db.commit()
            query_cache.clear()
            try:
                from vertex_agent import _session_cache
                _session_cache.clear()
            except Exception:
                pass
            return {"success": True, "message": f"Pushed to KB as {doc_id}"}
        else:
            raise HTTPException(status_code=500, detail=result["message"])

@app.get("/api/admin/research/failed-queries")
async def list_failed_queries(status: str = "all", user: dict = Depends(get_current_user)):
    """List raw failed queries for transparency."""
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    with SessionLocal() as db:
        query = db.query(FailedQuery)
        if status != "all":
            query = query.filter(FailedQuery.status == status)
        queries = query.order_by(FailedQuery.created_at.desc()).limit(200).all()
        return {"queries": [{
            "id": q.id, "user_query": q.user_query, "bot_response": q.bot_response[:200],
            "cluster_id": q.cluster_id, "status": q.status,
            "created_at": iso_utc(q.created_at) or "",
        } for q in queries]}

@app.post("/api/internal/research/run")
async def internal_research_trigger(request: Request):
    """Triggered by Cloud Scheduler daily at 2am. Auth via shared secret."""
    secret = request.headers.get("X-Research-Secret", "")
    expected = os.getenv("RESEARCH_SECRET", "")
    if not expected or secret != expected:
        raise HTTPException(status_code=403, detail="Invalid research secret")
    result = await asyncio.to_thread(run_research_batch)
    return result


@app.post("/api/internal/memory/consolidate")
async def internal_memory_consolidate(request: Request):
    """Triggered by Cloud Scheduler daily at 3am. Consolidates conversations into long-term user memories."""
    secret = request.headers.get("X-Research-Secret", "")
    expected = os.getenv("RESEARCH_SECRET", "")
    if not expected or secret != expected:
        raise HTTPException(status_code=403, detail="Invalid research secret")
    from services.memory_service import consolidate_user_memories
    result = await asyncio.to_thread(consolidate_user_memories, 24)
    return result


@app.post("/api/internal/memory/backfill-profiles")
async def internal_memory_backfill_profiles(request: Request, db: Session = Depends(get_db)):
    """One-time backfill: mirror every existing user's saved profile
    (department / role) into their UserMemory notebook so the chatbot can recall
    it without each user re-saving. Idempotent (the mirror upserts). Same
    X-Research-Secret auth as the other internal endpoints."""
    secret = request.headers.get("X-Research-Secret", "")
    expected = os.getenv("RESEARCH_SECRET", "")
    if not expected or secret != expected:
        raise HTTPException(status_code=403, detail="Invalid research secret")
    from services.memory_service import backfill_profile_memories
    result = await asyncio.to_thread(backfill_profile_memories, db)
    return result


@app.post("/api/internal/memory/idle-sweep")
async def internal_memory_idle_sweep(request: Request):
    """Phase 3 idle-sweep cron — runs every 5 min.

    Picks up users who've been idle 5-10 minutes and runs realtime memory
    extraction. Complements the per-turn trigger (every 6 turns) so users
    who stop chatting mid-session still get their facts captured before the
    3am cron. Auth via X-Research-Secret (same as consolidate endpoint).
    """
    secret = request.headers.get("X-Research-Secret", "")
    expected = os.getenv("RESEARCH_SECRET", "")
    if not expected or secret != expected:
        raise HTTPException(status_code=403, detail="Invalid research secret")
    result = await asyncio.to_thread(consolidate_idle_users, 5, 10)
    return result


@app.post("/api/internal/deadlines/check")
async def internal_deadline_check(request: Request):
    """Deadline Watcher cron — fires every morning.

    Scans active Submissions, finds the ones sitting on a reminder
    bucket (14 / 7 / 3 / 1 / 0 days from deadline), emails the owner
    once per (submission, bucket) pair. Idempotent: a DeadlineReminderLog
    row is written after each successful send so repeat runs (manual
    retries, Cloud Scheduler retries) never double-email.

    Auth via X-Research-Secret (same shared secret as the memory crons)."""
    secret = request.headers.get("X-Research-Secret", "")
    expected = os.getenv("RESEARCH_SECRET", "")
    if not expected or secret != expected:
        raise HTTPException(status_code=403, detail="Invalid research secret")

    from services import deadline_watcher as _dw

    def _run():
        with SessionLocal() as db:
            return _dw.send_due_reminders(db)

    result = await asyncio.to_thread(_run)
    return result


@app.post("/api/internal/popular-questions/recompute")
async def internal_recompute_popular_questions(request: Request):
    """Recompute the global "Top 10 most-asked" landing-page questions from
    ChatHistory and refresh the cache. Meant for a daily Cloud Scheduler cron so
    the serving endpoints never run the scan inline. Idempotent.

    Auth via X-Research-Secret (same shared secret as the other internal crons)."""
    secret = request.headers.get("X-Research-Secret", "")
    expected = os.getenv("RESEARCH_SECRET", "")
    if not expected or secret != expected:
        raise HTTPException(status_code=403, detail="Invalid research secret")

    from services.popular_questions import recompute

    def _run():
        with SessionLocal() as db:
            return recompute(db, DEFAULT_QUESTION_POOL, 10)

    questions = await asyncio.to_thread(_run)
    return {"status": "ok", "count": len(questions), "questions": questions}


# ==============================================================================
# Phase 5 — Per-User Memory Management API (Memory tab in ProfilePage)
# ==============================================================================
# Endpoints let a user see + edit + delete + pause what the bot remembers
# about them. All authenticated via get_current_user. Path params are
# validated against current_user.id (defense in depth — don't trust path).


def _user_memory_to_dict(m) -> dict:
    """Serialize a UserMemory row for the API (no embedding payload)."""
    return {
        "id": m.id,
        "type": m.memory_type,
        "content": m.content,
        "created_at": iso_utc(m.created_at),
        "updated_at": iso_utc(m.updated_at),
        "paused": bool(m.paused),
    }


def _chat_row_to_dict(c) -> dict:
    """Serialize a ChatHistory row for the Memory tab's 'Past conversations'."""
    return {
        "id": c.id,
        "session_id": c.session_id,
        "timestamp": iso_utc(c.timestamp),
        "user_query": (c.user_query or "")[:500],
        "bot_response": (c.bot_response or "")[:1000],
        "topic_label": c.topic_label,
        "has_embedding": c.embedding is not None,
    }


@app.get("/api/me/suggested-questions")
async def me_get_suggested_questions(
    user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Same GLOBAL Top-10 most-asked questions as /api/popular-questions -- every
    user now sees the identical list (per-user personalization was removed by
    product decision). Kept as a separate route so the authenticated frontend
    path is unchanged. Pure read; degrades to the curated pool on any error."""
    if not user:
        raise HTTPException(401, "Unauthorized")
    from services.popular_questions import get_top_questions
    try:
        questions = get_top_questions(db, DEFAULT_QUESTION_POOL, 10)
    except Exception as e:
        print(f"[POPULAR] me_get_suggested_questions failed for user={user.get('user_id')}: {e}")
        questions = DEFAULT_QUESTION_POOL[:10]
    return {"questions": questions, "generated_at": None, "source": "popular"}


@app.get("/api/me/memories")
async def me_get_memories(
    user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Return everything the bot remembers about the current user.

    Returns: facts (UserMemory rows) and recent_conversations (last 50
    embedded turns).
    """
    if not user:
        raise HTTPException(401, "Unauthorized")

    uid = user["user_id"]
    facts = db.query(UserMemory).filter(UserMemory.user_id == uid)\
        .order_by(UserMemory.updated_at.desc()).all()
    convos = db.query(ChatHistory).filter(ChatHistory.user_id == uid)\
        .order_by(ChatHistory.timestamp.desc()).limit(50).all()
    embedded_turn_count = db.query(ChatHistory).filter(
        ChatHistory.user_id == uid, ChatHistory.embedding.isnot(None),
    ).count()

    return {
        "facts": [_user_memory_to_dict(m) for m in facts],
        "recent_conversations": [_chat_row_to_dict(c) for c in convos],
        "stats": {
            "fact_count": len(facts),
            "embedded_turns": embedded_turn_count,
        },
    }


@app.delete("/api/me/memories/{memory_id}", status_code=204)
async def me_delete_memory(
    memory_id: int,
    user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Delete a single UserMemory row owned by the current user."""
    if not user:
        raise HTTPException(401, "Unauthorized")
    row = db.query(UserMemory).filter(
        UserMemory.id == memory_id,
        UserMemory.user_id == user["user_id"],  # defense in depth
    ).first()
    if not row:
        raise HTTPException(404, "Memory not found")
    db.delete(row)
    db.commit()
    return


@app.patch("/api/me/memories/{memory_id}")
async def me_patch_memory(
    memory_id: int,
    body: dict,
    user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Edit a memory's content or pause flag. If content changes, the
    embedding is recomputed so semantic recall stays accurate."""
    if not user:
        raise HTTPException(401, "Unauthorized")
    row = db.query(UserMemory).filter(
        UserMemory.id == memory_id,
        UserMemory.user_id == user["user_id"],
    ).first()
    if not row:
        raise HTTPException(404, "Memory not found")

    content_changed = False
    if "content" in body and isinstance(body["content"], str):
        new_content = body["content"].strip()
        if new_content and new_content != row.content:
            row.content = new_content
            content_changed = True
    if "paused" in body:
        row.paused = bool(body["paused"])

    if content_changed:
        # Recompute embedding so semantic recall reflects the new text.
        from services.embedding_util import embed_text
        from services.memory_service import _serialize_embedding, EMBEDDING_MODEL_VERSION
        vec = embed_text(row.content)
        if vec:
            row.embedding = _serialize_embedding(vec)
            row.embedding_model = EMBEDDING_MODEL_VERSION
        else:
            # Couldn't embed — null the column so retrieval skips this row
            # rather than using stale embedding for new content.
            row.embedding = None
            row.embedding_model = None

    row.updated_at = datetime.utcnow()
    db.commit()
    return _user_memory_to_dict(row)


@app.delete("/api/me/conversations/{chat_id}")
async def me_delete_conversation(
    chat_id: int,
    hard: bool = False,
    user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Remove a past turn from semantic-recall results.

    Default: soft-zero (clears the embedding so retrieve_relevant_turns
    can't surface it; text remains for audit).
    ?hard=true: full row delete.
    """
    if not user:
        raise HTTPException(401, "Unauthorized")
    row = db.query(ChatHistory).filter(
        ChatHistory.id == chat_id,
        ChatHistory.user_id == user["user_id"],
    ).first()
    if not row:
        raise HTTPException(404, "Conversation not found")
    if hard:
        db.delete(row)
    else:
        row.embedding = None
        row.embedding_model = None
    db.commit()
    return {"deleted": True, "hard": hard}


@app.delete("/api/me/memories")
async def me_delete_all_memories(
    user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Right-to-erasure: hard-delete all UserMemory rows AND zero out every
    chat_history.embedding for the current user. Text rows in chat_history
    are kept so the user's own chat-history page still shows what they
    asked — only the semantic index is wiped.
    """
    if not user:
        raise HTTPException(401, "Unauthorized")
    uid = user["user_id"]
    fact_count = db.query(UserMemory).filter(UserMemory.user_id == uid).delete()
    turn_count = (
        db.query(ChatHistory)
        .filter(ChatHistory.user_id == uid, ChatHistory.embedding.isnot(None))
        .update({
            ChatHistory.embedding: None,
            ChatHistory.embedding_model: None,
        }, synchronize_session=False)
    )
    db.commit()
    return {
        "deleted_facts": fact_count,
        "cleared_embeddings": turn_count,
    }


# ----------------------------------------------------------------------------
# Phase 6 — Admin debug view (read-only)
# ============================================================================
# PROPOSALS TRACKER -- in-flight grant submissions with task checklists
# ============================================================================
from services import proposals_service as _proposals_service
from services.proposal_templates import available_templates as _available_templates


def _submission_to_dict(s, include_tasks: bool = True) -> dict:
    """Serialize a Submission ORM row for the API. Hard-deletes mean the
    user never sees ghost rows; tasks ride along by default."""
    out = {
        "id": s.id,
        "title": s.title,
        "sponsor": s.sponsor,
        "deadline": s.deadline.isoformat() if s.deadline else None,
        # Morgan's internal routing deadline: 5 business days before the sponsor
        # date, so a first-timer plans backward from the real institutional cutoff.
        "internal_deadline": (_proposals_service.internal_routing_deadline(s.deadline).isoformat()
                              if s.deadline else None),
        "status": s.status,
        "notes": s.notes,
        # Budget Helper: parsed saved inputs (None if no budget saved). Whether a
        # budget exists is cheap to expose on the list view too (drives the badge).
        "has_budget": bool(getattr(s, "budget_json", None)),
        # Compliance Sentinel: whether a compliance check has been saved (badge).
        "has_compliance": bool(getattr(s, "compliance_json", None)),
        # Drafting Coach: whether a section draft has been saved (badge / next-step).
        "has_sections": bool(getattr(s, "sections_json", None)),
        "created_at": iso_utc(s.created_at),
        "updated_at": iso_utc(s.updated_at),
    }
    if include_tasks:
        out["tasks"] = [_submission_task_to_dict(t) for t in s.tasks]
        raw = getattr(s, "budget_json", None)
        if raw:
            try:
                out["budget"] = json.loads(raw)
            except (ValueError, TypeError):
                out["budget"] = None
        else:
            out["budget"] = None
    return out


def _submission_task_to_dict(t) -> dict:
    from services.forms_catalog import get_form
    from services.task_guidance import guidance_for
    form = get_form(t.kb_doc_id)
    return {
        "id": t.id,
        "title": t.title,
        "description": t.description,
        "kb_doc_id": t.kb_doc_id,
        # Resolved form link (None when the task has no linked form, e.g.
        # biosketch / DMP / Specific Aims -- intentionally unlinked).
        "kb_doc_url": form["url"] if form else None,
        "kb_doc_title": form["title"] if form else None,
        "due_offset_days": t.due_offset_days,
        "status": t.status,
        "notes": t.notes,
        "sort_order": t.sort_order,
        # Phase 4: short how-to + sample for known tasks (None if no match).
        "guidance": guidance_for(t.title),
    }


def _parse_deadline(raw):
    """Accept ISO datetime, plain date (YYYY-MM-DD), or None."""
    if not raw:
        return None
    if isinstance(raw, str):
        try:
            return datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            try:
                return datetime.strptime(raw, "%Y-%m-%d")
            except ValueError:
                raise HTTPException(400, f"Invalid deadline format: {raw!r}")
    raise HTTPException(400, f"Invalid deadline type: {type(raw).__name__}")


@app.get("/api/me/submissions")
async def list_my_submissions(
    user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """All of the current user's proposal submissions, newest first."""
    if not user:
        raise HTTPException(401, "Unauthorized")
    subs = _proposals_service.list_submissions(db, user_id=user["user_id"])
    return {
        "submissions": [_submission_to_dict(s, include_tasks=False) for s in subs],
        "count": len(subs),
    }


@app.get("/api/me/deadlines-token")
async def my_deadlines_token(
    request: Request,
    user: dict = Depends(get_current_user),
):
    """Mint the per-user calendar URLs (download + webcal subscribe).
    The token is scoped to 'ics' and carries no email claim, so it can't be
    used as a normal auth bearer."""
    from services.ics_export import mint_ics_token
    tok = mint_ics_token(user["user_id"])
    base = str(request.base_url).rstrip("/")          # e.g. https://host
    ics_url = f"{base}/api/me/deadlines.ics?token={tok}"
    host = request.url.hostname or ""
    if request.url.port and request.url.port not in (80, 443):
        host = f"{host}:{request.url.port}"
    webcal_url = f"webcal://{host}/api/me/deadlines.ics?token={tok}"
    return {"ics_url": ics_url, "webcal_url": webcal_url}


@app.get("/api/me/deadlines.ics")
async def my_deadlines_ics(
    token: str = "",
    db: Session = Depends(get_db),
):
    """Token-authed (no Bearer) calendar feed of the user's proposal
    deadlines. Calendar apps fetch this URL directly."""
    from fastapi import Response
    from services.ics_export import decode_ics_token, build_calendar
    user_id = decode_ics_token(token)
    if user_id is None:
        raise HTTPException(status_code=401, detail="Invalid calendar token")
    subs = _proposals_service.list_submissions(db, user_id=user_id)
    body = build_calendar(subs)
    return Response(
        content=body,
        media_type="text/calendar",
        headers={"Content-Disposition": 'attachment; filename="ora-deadlines.ics"'},
    )


@app.post("/api/me/submissions")
async def create_my_submission(
    payload: dict,
    user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Create a new submission and seed its task list from the sponsor's
    template. Body: {title, sponsor, deadline?, notes?}."""
    if not user:
        raise HTTPException(401, "Unauthorized")
    title = (payload.get("title") or "").strip()
    if not title:
        raise HTTPException(400, "title is required")
    sponsor = (payload.get("sponsor") or "Internal").strip() or "Internal"
    deadline = _parse_deadline(payload.get("deadline"))
    notes = payload.get("notes")
    sub = _proposals_service.create_submission(
        db, user_id=user["user_id"], title=title,
        sponsor=sponsor, deadline=deadline, notes=notes,
    )
    return _submission_to_dict(sub, include_tasks=True)


@app.get("/api/me/submissions/{submission_id}")
async def get_my_submission(
    submission_id: int,
    user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not user:
        raise HTTPException(401, "Unauthorized")
    sub = _proposals_service.get_submission(
        db, submission_id=submission_id, user_id=user["user_id"])
    if sub is None:
        raise HTTPException(404, "Submission not found")
    return _submission_to_dict(sub, include_tasks=True)


# ──────────────────────────────────────────────────────────────────────────
# Budget Helper — deterministic grant-budget math + AI-drafted justification.
# Numbers come ONLY from services/budget_helper.compute_budget (never the LLM).
# ──────────────────────────────────────────────────────────────────────────

@app.get("/api/budget/rates")
async def budget_rate_options(user: dict = Depends(get_current_user)):
    """F&A + fringe rate tables that populate the Budget Helper selectors."""
    if not user:
        raise HTTPException(401, "Unauthorized")
    from services.budget_helper import rate_options
    return rate_options()


@app.post("/api/budget/compute")
async def budget_compute(payload: dict, user: dict = Depends(get_current_user)):
    """Stateless: compute a full budget breakdown from line-item inputs. Drives
    the Budget Helper's live summary. Every figure is deterministic."""
    if not user:
        raise HTTPException(401, "Unauthorized")
    from services.budget_helper import compute_budget
    return compute_budget(payload or {})


@app.post("/api/budget/justification")
async def budget_justification(payload: dict, user: dict = Depends(get_current_user)):
    """Draft the budget-justification narrative. AI-polished when available, with
    a HARD fallback to the deterministic template. The figures come from the
    deterministic compute -- the AI is told to never change a number."""
    if not user:
        raise HTTPException(401, "Unauthorized")
    from services.budget_helper import (
        compute_budget, draft_justification, per_line_justifications, _fmt,
    )
    inputs = payload.get("inputs", payload) or {}
    budget = compute_budget(inputs)
    template = draft_justification(budget)
    per_line = per_line_justifications(budget)   # deterministic, additive
    if not payload.get("use_ai", True):
        return {"justification": template, "ai": False, "per_line": per_line}
    try:
        from services import gemini_client
        prompt = (
            "You are a grants budget specialist at Morgan State University. Rewrite the "
            "budget justification below into clear, professional, sponsor-ready prose. "
            "RULES: Do NOT change, add, or remove ANY dollar figure, percentage, name, or "
            "rate -- reproduce them EXACTLY. Do not invent line items. Keep it concise.\n\n"
            f"{template}"
        )
        text_out = (gemini_client.generate_text(prompt, temperature=0.2, max_output_tokens=900) or "").strip()
        # Completeness guard: Gemini can return a TRUNCATED fragment (e.g. it
        # stops mid-sentence under load). A non-empty fragment would otherwise
        # be shown in place of the full justification. A complete justification
        # always states the total project cost, so require that figure to be
        # present; otherwise fall back to the complete deterministic template.
        total_fmt = _fmt(budget.get("total") or 0)
        if text_out and total_fmt in text_out:
            return {"justification": text_out, "ai": True, "template": template, "per_line": per_line}
        if text_out:
            print(f"[BUDGET] AI justification truncated (missing {total_fmt}) -- using template")
    except Exception as e:
        print(f"[BUDGET] AI justification failed, using deterministic template: {e}")
    return {"justification": template, "ai": False, "per_line": per_line}


@app.get("/api/me/submissions/{submission_id}/budget")
async def get_submission_budget(
    submission_id: int,
    user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Load a submission's saved budget inputs + a fresh deterministic compute."""
    if not user:
        raise HTTPException(401, "Unauthorized")
    sub = _proposals_service.get_submission(db, submission_id=submission_id, user_id=user["user_id"])
    if sub is None:
        raise HTTPException(404, "Submission not found")
    from services.budget_helper import compute_budget
    raw = getattr(sub, "budget_json", None)
    inputs = {}
    if raw:
        try:
            inputs = json.loads(raw)
        except (ValueError, TypeError):
            inputs = {}
    return {"inputs": inputs, "computed": compute_budget(inputs)}


@app.put("/api/me/submissions/{submission_id}/budget")
async def save_submission_budget(
    submission_id: int,
    payload: dict,
    user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Save budget inputs onto the submission (recomputed deterministically on load)."""
    if not user:
        raise HTTPException(401, "Unauthorized")
    sub = _proposals_service.get_submission(db, submission_id=submission_id, user_id=user["user_id"])
    if sub is None:
        raise HTTPException(404, "Submission not found")
    from services.budget_helper import compute_budget
    inputs = payload.get("inputs", payload) or {}
    computed = compute_budget(inputs)          # validate it computes cleanly
    sub.budget_json = json.dumps(inputs)
    db.commit()
    db.refresh(sub)
    return {"inputs": inputs, "computed": computed}


@app.get("/api/me/submissions/{submission_id}/budget.csv")
async def export_submission_budget_csv(
    submission_id: int,
    user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Download the saved budget as CSV (opens in Excel / Sheets)."""
    if not user:
        raise HTTPException(401, "Unauthorized")
    sub = _proposals_service.get_submission(db, submission_id=submission_id, user_id=user["user_id"])
    if sub is None:
        raise HTTPException(404, "Submission not found")
    from fastapi.responses import Response
    from services.budget_helper import compute_budget, budget_to_csv
    raw = getattr(sub, "budget_json", None)
    inputs = {}
    if raw:
        try:
            inputs = json.loads(raw)
        except (ValueError, TypeError):
            inputs = {}
    csv_text = budget_to_csv(compute_budget(inputs))
    return Response(
        content=csv_text,
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="budget-{submission_id}.csv"'},
    )


# ──────────────────────────────────────────────────────────────────────────
# Compliance Sentinel — deterministic "which approvals do I need?" checklist.
# WHICH approvals are required is decided ONLY by code rules in
# services/compliance_sentinel (never the LLM). No AI in this feature.
# ──────────────────────────────────────────────────────────────────────────

@app.get("/api/compliance/questions")
async def compliance_questions(user: dict = Depends(get_current_user)):
    """The yes/no questionnaire + the sponsor-derived-triggers note."""
    if not user:
        raise HTTPException(401, "Unauthorized")
    from services.compliance_sentinel import questionnaire
    return questionnaire()


@app.post("/api/compliance/assess")
async def compliance_assess(payload: dict, user: dict = Depends(get_current_user)):
    """Stateless: assess a checklist from {answers, sponsor}. Drives the live
    Sentinel panel. Every status is deterministic."""
    if not user:
        raise HTTPException(401, "Unauthorized")
    from services.compliance_sentinel import assess_compliance
    payload = payload or {}
    return assess_compliance(payload.get("answers") or {}, sponsor=payload.get("sponsor"))


@app.get("/api/me/submissions/{submission_id}/compliance")
async def get_submission_compliance(
    submission_id: int,
    user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Load a submission's saved answers + a fresh deterministic assessment
    (using the submission's own sponsor)."""
    if not user:
        raise HTTPException(401, "Unauthorized")
    sub = _proposals_service.get_submission(db, submission_id=submission_id, user_id=user["user_id"])
    if sub is None:
        raise HTTPException(404, "Submission not found")
    from services.compliance_sentinel import assess_compliance
    raw = getattr(sub, "compliance_json", None)
    answers = {}
    if raw:
        try:
            answers = (json.loads(raw) or {}).get("answers", {})
        except (ValueError, TypeError):
            answers = {}
    return {"answers": answers, "result": assess_compliance(answers, sponsor=sub.sponsor)}


@app.put("/api/me/submissions/{submission_id}/compliance")
async def save_submission_compliance(
    submission_id: int,
    payload: dict,
    user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Save questionnaire answers onto the submission (re-assessed on load)."""
    if not user:
        raise HTTPException(401, "Unauthorized")
    sub = _proposals_service.get_submission(db, submission_id=submission_id, user_id=user["user_id"])
    if sub is None:
        raise HTTPException(404, "Submission not found")
    from services.compliance_sentinel import assess_compliance
    answers = (payload or {}).get("answers", payload) or {}
    result = assess_compliance(answers, sponsor=sub.sponsor)   # validate it computes
    sub.compliance_json = json.dumps({"answers": answers})
    db.commit()
    db.refresh(sub)
    return {"answers": answers, "result": result}


@app.post("/api/me/submissions/{submission_id}/compliance/tasks")
async def add_compliance_tasks(
    submission_id: int,
    payload: dict,
    user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Create SubmissionTasks for the REQUIRED compliance items. Idempotent:
    skips any task whose title already exists on the submission."""
    if not user:
        raise HTTPException(401, "Unauthorized")
    sub = _proposals_service.get_submission(db, submission_id=submission_id, user_id=user["user_id"])
    if sub is None:
        raise HTTPException(404, "Submission not found")
    from services.compliance_sentinel import assess_compliance, suggested_tasks
    # Use saved answers unless the caller passes a fresh set.
    answers = (payload or {}).get("answers")
    answers_from_payload = answers is not None
    if answers is None:
        raw = getattr(sub, "compliance_json", None)
        try:
            answers = (json.loads(raw) or {}).get("answers", {}) if raw else {}
        except (ValueError, TypeError):
            answers = {}
    result = assess_compliance(answers or {}, sponsor=sub.sponsor)
    # Persist the answers if the caller supplied them, so the compliance check
    # shows as saved (has_compliance) without needing a separate Save click.
    if answers_from_payload:
        sub.compliance_json = json.dumps({"answers": answers or {}})
    existing = {(t.title or "").strip().lower() for t in (sub.tasks or [])}
    created = []
    for t in suggested_tasks(result):
        if t["title"].strip().lower() in existing:
            continue
        task = _proposals_service.add_task(
            db, submission_id=submission_id, user_id=user["user_id"],
            title=t["title"], description=t["description"], kb_doc_id=t.get("kb_doc_id"),
        )
        if task is not None:
            created.append(_submission_task_to_dict(task))
            existing.add(t["title"].strip().lower())
    db.commit()
    return {"created": created, "result": result}


@app.patch("/api/me/submissions/{submission_id}")
async def update_my_submission(
    submission_id: int,
    payload: dict,
    user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not user:
        raise HTTPException(401, "Unauthorized")
    deadline = payload.get("deadline")
    deadline_parsed = _parse_deadline(deadline) if deadline else None
    sub = _proposals_service.update_submission(
        db, submission_id=submission_id, user_id=user["user_id"],
        title=payload.get("title"),
        sponsor=payload.get("sponsor"),
        deadline=deadline_parsed if deadline else None,
        status=payload.get("status"),
        notes=payload.get("notes"),
    )
    if sub is None:
        raise HTTPException(404, "Submission not found")
    return _submission_to_dict(sub, include_tasks=True)


@app.delete("/api/me/submissions/{submission_id}", status_code=204)
async def delete_my_submission(
    submission_id: int,
    user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not user:
        raise HTTPException(401, "Unauthorized")
    ok = _proposals_service.delete_submission(
        db, submission_id=submission_id, user_id=user["user_id"])
    if not ok:
        raise HTTPException(404, "Submission not found")
    return None


@app.post("/api/me/submissions/{submission_id}/tasks")
async def create_my_submission_task(
    submission_id: int,
    payload: dict,
    user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Append a custom task to a submission."""
    if not user:
        raise HTTPException(401, "Unauthorized")
    title = (payload.get("title") or "").strip()
    if not title:
        raise HTTPException(400, "title is required")
    task = _proposals_service.add_task(
        db, submission_id=submission_id, user_id=user["user_id"],
        title=title,
        description=payload.get("description"),
        due_offset_days=payload.get("due_offset_days"),
    )
    if task is None:
        raise HTTPException(404, "Submission not found")
    return _submission_task_to_dict(task)


@app.patch("/api/me/submissions/{submission_id}/tasks/{task_id}")
async def update_my_submission_task(
    submission_id: int,
    task_id: int,
    payload: dict,
    user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Toggle status (pending/done), edit title, etc."""
    if not user:
        raise HTTPException(401, "Unauthorized")
    task = _proposals_service.update_task(
        db, submission_id=submission_id, task_id=task_id,
        user_id=user["user_id"],
        title=payload.get("title"),
        description=payload.get("description"),
        status=payload.get("status"),
        notes=payload.get("notes"),
    )
    if task is None:
        raise HTTPException(404, "Task not found")
    return _submission_task_to_dict(task)


@app.delete("/api/me/submissions/{submission_id}/tasks/{task_id}", status_code=204)
async def delete_my_submission_task(
    submission_id: int,
    task_id: int,
    user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not user:
        raise HTTPException(401, "Unauthorized")
    ok = _proposals_service.delete_task(
        db, submission_id=submission_id, task_id=task_id,
        user_id=user["user_id"])
    if not ok:
        raise HTTPException(404, "Task not found")
    return None


@app.get("/api/me/submissions/templates/list")
async def list_proposal_templates(user: dict = Depends(get_current_user)):
    """The set of sponsor templates the user can pick from when creating
    a submission. Drives the template dropdown in the create modal."""
    if not user:
        raise HTTPException(401, "Unauthorized")
    return {"templates": _available_templates()}


# ----------------------------------------------------------------------------
# Solicitation ingestion: PDF -> structured fields -> seeded Submission.
# Two-step flow:
#   1) POST /from-solicitation (file upload) -> returns extracted dict
#   2) POST /from-solicitation/confirm (JSON body) -> creates Submission
# This keeps "extract" cheap+idempotent and "commit" explicit so the user
# always reviews the AI-extracted fields before they become a real proposal.

_MAX_SOLICITATION_PDF_BYTES = 25 * 1024 * 1024  # 25 MB


@app.post("/api/me/submissions/from-solicitation")
async def extract_solicitation(
    file: UploadFile = File(...),
    user: dict = Depends(get_current_user),
):
    """Step 1: parse a sponsor PDF and return the extracted JSON. Does
    NOT create a Submission -- the user reviews/edits, then calls the
    confirm endpoint."""
    if not user:
        raise HTTPException(401, "Unauthorized")
    filename = (file.filename or "").lower()
    ctype = (file.content_type or "").lower()
    if not (filename.endswith(".pdf") or "pdf" in ctype):
        raise HTTPException(400, "Only PDF uploads are supported.")

    pdf_bytes = await file.read()
    if not pdf_bytes:
        raise HTTPException(400, "Uploaded file is empty.")
    if len(pdf_bytes) > _MAX_SOLICITATION_PDF_BYTES:
        raise HTTPException(413, "PDF is larger than 25 MB.")

    from services import solicitation_extractor as _sx
    extracted = _sx.extract_from_pdf_bytes(pdf_bytes)
    if extracted is None:
        raise HTTPException(
            422,
            "Couldn't read this PDF -- the file may be scanned or "
            "image-only. Try a text-based PDF, or create the proposal "
            "manually.",
        )
    return {"extracted": extracted}


class SolicitationUrlRequest(BaseModel):
    url: str


@app.post("/api/me/submissions/from-solicitation/url")
async def extract_solicitation_from_url(
    payload: SolicitationUrlRequest,
    user: dict = Depends(get_current_user),
):
    """Step 1 (URL variant): fetch a sponsor solicitation URL (an HTML page or a
    linked PDF), extract the same structured JSON the PDF flow returns. Does NOT
    create a Submission -- the user reviews/edits, then calls the confirm
    endpoint. Same response shape as /from-solicitation so the UI is shared."""
    if not user:
        raise HTTPException(401, "Unauthorized")

    from services import url_fetcher, solicitation_extractor as _sx
    try:
        text = url_fetcher.fetch_solicitation_text(payload.url)
    except url_fetcher.FetchError as e:
        raise HTTPException(e.status, e.message)

    extracted = _sx.extract_from_text(text)
    if extracted is None:
        raise HTTPException(
            422,
            "Couldn't pull a solicitation out of that page -- it may not be a "
            "solicitation, or the content is image-only. Try the PDF upload, or "
            "create the proposal manually.",
        )
    return {"extracted": extracted}


@app.post("/api/me/submissions/from-solicitation/confirm")
async def confirm_solicitation_submission(
    payload: dict,
    user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Step 2: commit a user-reviewed extracted dict as a real Submission.
    Body shape:
        { "extracted": {<contract dict>}, "title_override": "optional" }"""
    if not user:
        raise HTTPException(401, "Unauthorized")
    extracted = payload.get("extracted")
    if not isinstance(extracted, dict):
        raise HTTPException(400, "Missing 'extracted' dict in body.")
    title_override = payload.get("title_override")

    sub = _proposals_service.create_submission_from_solicitation(
        db, user_id=user["user_id"], extracted=extracted,
        title_override=title_override,
    )
    return _submission_to_dict(sub, include_tasks=True)


# ----------------------------------------------------------------------------
# Draft Critic: upload a draft PDF, get a mechanical pre-submission check
# against the solicitation already attached to this Submission. No LLM call,
# so no hallucination risk -- every check is deterministic from the PDF text.

_MAX_DRAFT_PDF_BYTES = 25 * 1024 * 1024  # 25 MB, same as solicitation upload


@app.post("/api/me/submissions/{submission_id}/critique")
async def critique_draft(
    submission_id: int,
    file: UploadFile = File(...),
    user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Run Draft Critic on an uploaded draft PDF. The submission's
    existing solicitation context (page limits, required attachments,
    budget cap) is reconstructed from notes + tasks and passed in.
    Submissions created manually still get a useful critique against
    sponsor-default sections."""
    if not user:
        raise HTTPException(401, "Unauthorized")

    sub = _proposals_service.get_submission(
        db, submission_id=submission_id, user_id=user["user_id"],
    )
    if sub is None:
        raise HTTPException(404, "Submission not found")

    filename = (file.filename or "").lower()
    ctype = (file.content_type or "").lower()
    if not (filename.endswith(".pdf") or "pdf" in ctype):
        raise HTTPException(400, "Only PDF uploads are supported.")

    pdf_bytes = await file.read()
    if not pdf_bytes:
        raise HTTPException(400, "Uploaded file is empty.")
    if len(pdf_bytes) > _MAX_DRAFT_PDF_BYTES:
        raise HTTPException(413, "PDF is larger than 25 MB.")

    from services import draft_critic as _dc
    solicitation = _proposals_service.reconstruct_solicitation_context(sub)
    critique = _dc.critique_pdf(
        pdf_bytes=pdf_bytes,
        sponsor=sub.sponsor,
        solicitation=solicitation,
    )
    if critique is None:
        raise HTTPException(
            422,
            "Couldn't read this PDF -- the file may be scanned or "
            "image-only. Try a text-based PDF.",
        )

    return {
        "submission_id": submission_id,
        "submission_title": sub.title,
        "sponsor": sub.sponsor,
        "solicitation_context": solicitation,
        "critique": critique,
    }


# ----------------------------------------------------------------------------
# Section Drafting Coach (Phase 2): outline a proposal section, or give advisory
# feedback on the PI's own draft of it. Coaching only -- never writes the prose.

@app.get("/api/me/submissions/{submission_id}/sections")
async def list_coach_sections(
    submission_id: int,
    user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """The proposal sections the coach can help with, for this submission's sponsor."""
    if not user:
        raise HTTPException(401, "Unauthorized")
    sub = _proposals_service.get_submission(db, submission_id=submission_id, user_id=user["user_id"])
    if sub is None:
        raise HTTPException(404, "Submission not found")
    from services import section_coach as _sc
    return {"sponsor": sub.sponsor, "sections": _sc.available_sections(sub.sponsor)}


class SectionCoachRequest(BaseModel):
    section_key: str
    mode: str = "outline"          # "outline" | "review"
    topic: str = ""                # optional: tailors the outline tips
    draft_text: str = ""           # required for "review"


@app.post("/api/me/submissions/{submission_id}/section-coach")
async def section_coach(
    submission_id: int,
    payload: SectionCoachRequest,
    user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Outline a section or review the PI's draft of it. Uses the submission's
    sponsor + reconstructed solicitation context. Advisory; never authoritative."""
    if not user:
        raise HTTPException(401, "Unauthorized")
    sub = _proposals_service.get_submission(db, submission_id=submission_id, user_id=user["user_id"])
    if sub is None:
        raise HTTPException(404, "Submission not found")

    from services import section_coach as _sc
    context = _proposals_service.reconstruct_solicitation_context(sub)
    context["eligibility"] = _eligibility_text_from_notes(sub.notes)   # 'match THIS solicitation'
    if payload.mode == "review":
        result = _sc.review_section(sub.sponsor, payload.section_key, payload.draft_text, context)
    else:
        result = _sc.outline_section(sub.sponsor, payload.section_key, payload.topic, context)
    if result is None:
        raise HTTPException(400, "Unknown proposal section.")
    return {"submission_id": submission_id, "sponsor": sub.sponsor, "result": result}


@app.post("/api/me/submissions/{submission_id}/coherence")
async def section_coherence(
    submission_id: int,
    user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Advisory cross-section coherence check: do the PI's SAVED sections agree
    with each other (and with eligibility + budget)? Reads existing
    sections_json/budget_json/notes -- no new state. Advisory; never authoritative."""
    if not user:
        raise HTTPException(401, "Unauthorized")
    sub = _proposals_service.get_submission(db, submission_id=submission_id, user_id=user["user_id"])
    if sub is None:
        raise HTTPException(404, "Submission not found")

    from services import section_coach as _sc
    from services.budget_helper import compute_budget

    raw = getattr(sub, "sections_json", None)
    drafts = {}
    if raw:
        try:
            drafts = json.loads(raw)
        except (ValueError, TypeError):
            drafts = {}
    if not isinstance(drafts, dict):
        drafts = {}

    budget = None
    raw_b = getattr(sub, "budget_json", None)
    if raw_b:
        try:
            budget = compute_budget(json.loads(raw_b))
        except (ValueError, TypeError):
            budget = None

    context = _proposals_service.reconstruct_solicitation_context(sub)
    context["eligibility"] = _eligibility_text_from_notes(sub.notes)
    result = _sc.coherence_check(sub.sponsor, drafts, context, budget)
    return {"submission_id": submission_id, "sponsor": sub.sponsor, "result": result}


@app.post("/api/me/submissions/{submission_id}/responsiveness")
async def section_responsiveness(
    submission_id: int,
    user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Advisory responsiveness matrix: does each ASK in the solicitation (required
    elements/attachments, named sections, review criteria, eligibility) appear in
    the PI's SAVED drafts? Reads existing sections_json/notes -- no new state.
    Grounded coverage only; never a score or verdict."""
    if not user:
        raise HTTPException(401, "Unauthorized")
    sub = _proposals_service.get_submission(db, submission_id=submission_id, user_id=user["user_id"])
    if sub is None:
        raise HTTPException(404, "Submission not found")

    from services import section_coach as _sc

    raw = getattr(sub, "sections_json", None)
    drafts = {}
    if raw:
        try:
            drafts = json.loads(raw)
        except (ValueError, TypeError):
            drafts = {}
    if not isinstance(drafts, dict):
        drafts = {}

    context = _proposals_service.reconstruct_solicitation_context(sub)
    context["eligibility"] = _eligibility_text_from_notes(sub.notes)
    result = _sc.responsiveness_matrix(sub.sponsor, drafts, context)
    return {"submission_id": submission_id, "sponsor": sub.sponsor, "result": result}


@app.get("/api/me/submissions/{submission_id}/sections/drafts")
async def get_section_drafts(
    submission_id: int,
    user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """The PI's saved per-section draft text for this submission."""
    if not user:
        raise HTTPException(401, "Unauthorized")
    sub = _proposals_service.get_submission(db, submission_id=submission_id, user_id=user["user_id"])
    if sub is None:
        raise HTTPException(404, "Submission not found")
    raw = getattr(sub, "sections_json", None)
    drafts = {}
    if raw:
        try:
            drafts = json.loads(raw)
        except (ValueError, TypeError):
            drafts = {}
    return {"drafts": drafts if isinstance(drafts, dict) else {}}


class SectionDraftRequest(BaseModel):
    section_key: str
    text: str = ""


@app.put("/api/me/submissions/{submission_id}/sections/drafts")
async def save_section_draft(
    submission_id: int,
    payload: SectionDraftRequest,
    user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Save (or clear) the PI's draft text for one section. Coaching only -- this
    is the PI's own writing; we store it so they can come back to it."""
    if not user:
        raise HTTPException(401, "Unauthorized")
    sub = _proposals_service.get_submission(db, submission_id=submission_id, user_id=user["user_id"])
    if sub is None:
        raise HTTPException(404, "Submission not found")
    raw = getattr(sub, "sections_json", None)
    drafts = {}
    if raw:
        try:
            drafts = json.loads(raw)
        except (ValueError, TypeError):
            drafts = {}
    if not isinstance(drafts, dict):
        drafts = {}
    text = (payload.text or "").strip()
    if text:
        drafts[payload.section_key] = text
    else:
        drafts.pop(payload.section_key, None)   # empty -> clear
    sub.sections_json = json.dumps(drafts)
    db.commit()
    return {"drafts": drafts}


# ----------------------------------------------------------------------------
# Eligibility-text helper. Pulls the "Eligibility: ..." line out of a
# submission's solicitation notes; used by the section-coach ("match THIS
# solicitation"). The interactive Fundability / Eligibility self-check feature
# was removed, but the extracted eligibility TEXT is still part of ingestion.

def _eligibility_text_from_notes(notes: Optional[str]) -> Optional[str]:
    """Pull the 'Eligibility: ...' line out of a submission's solicitation notes."""
    if not notes:
        return None
    import re as _re
    m = _re.search(r"^Eligibility:\s*(.+)$", notes, _re.MULTILINE)
    return m.group(1).strip() if m else None


# ----------------------------------------------------------------------------
# Admins can see (but NOT edit) any user's memory state. Per GDPR, only the
# user themselves can modify or delete their memories. This endpoint exists
# for support and debugging.

@app.get("/api/admin/memories/{target_user_id}")
async def admin_get_user_memories(
    target_user_id: int,
    user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Admin read-only view of a user's memory state."""
    if not user or user.get("role") != "admin":
        raise HTTPException(403, "Admin access required")

    target = db.query(User).filter(User.id == target_user_id).first()
    if not target:
        raise HTTPException(404, "User not found")

    facts = db.query(UserMemory).filter(UserMemory.user_id == target_user_id)\
        .order_by(UserMemory.updated_at.desc()).all()
    embedded_turn_count = db.query(ChatHistory).filter(
        ChatHistory.user_id == target_user_id, ChatHistory.embedding.isnot(None),
    ).count()
    total_turn_count = db.query(ChatHistory).filter(
        ChatHistory.user_id == target_user_id,
    ).count()

    return {
        "user": {
            "id": target.id,
            "email": target.email,
            "name": target.name,
            "last_chat_at": iso_utc(getattr(target, "last_chat_at", None)),
        },
        "facts": [_user_memory_to_dict(m) for m in facts],
        "stats": {
            "fact_count": len(facts),
            "embedded_turns": embedded_turn_count,
            "total_turns": total_turn_count,
            "coverage_pct": round(100 * embedded_turn_count / total_turn_count, 1) if total_turn_count else 0,
        },
    }


# ==============================================================================
# CLOUD KB STATS ENDPOINT
# ==============================================================================

@app.get("/api/admin/cloud-kb/stats")
async def get_cloud_kb_stats(user: dict = Depends(get_current_user)):
    """Get cloud KB statistics - doc count, total size, last modified"""
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    try:
        docs = list_datastore_documents()
        total_size = sum(d.get("size", 0) for d in docs)
        last_modified = max((d.get("modified", "") for d in docs), default="") if docs else ""
        return {
            "total_documents": len(docs),
            "total_size": total_size,
            "last_modified": last_modified,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get stats: {e}")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=5000, reload=True)