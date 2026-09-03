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

from fastapi import FastAPI, HTTPException, Depends, status, Body, File, Form, UploadFile, Request
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

        # 5e. Add submissions.solicitation_json if missing (Draft Review — the
        # solicitation each proposal is reviewed against).
        try:
            conn.execute(text("SELECT solicitation_json FROM submissions LIMIT 1"))
        except (OperationalError, ProgrammingError):
            print("[WARN] 'solicitation_json' column missing. Adding it now...")
            try:
                conn.execute(text("ALTER TABLE submissions ADD COLUMN solicitation_json MEDIUMTEXT NULL"))
                conn.commit()
                print("[OK] Successfully added 'solicitation_json' column!")
            except Exception as e:
                print(f"[ERROR] Failed to add solicitation_json column: {e}")

        # 5e-bis. Add submissions.draft_review_json if missing (the last saved
        # Draft Review, written only when the PI presses Save).
        try:
            conn.execute(text("SELECT draft_review_json FROM submissions LIMIT 1"))
        except (OperationalError, ProgrammingError):
            print("[WARN] 'draft_review_json' column missing. Adding it now...")
            try:
                conn.execute(text("ALTER TABLE submissions ADD COLUMN draft_review_json MEDIUMTEXT NULL"))
                conn.commit()
                print("[OK] Successfully added 'draft_review_json' column!")
            except Exception as e:
                print(f"[ERROR] Failed to add draft_review_json column: {e}")

        # 5f. Create solicitation_sources if missing (the stored solicitation
        # TEXT, so a PI is never asked for the same document twice).
        try:
            conn.execute(text("SELECT id FROM solicitation_sources LIMIT 1"))
            print("[OK] solicitation_sources table exists")
        except (OperationalError, ProgrammingError):
            print("[WARN] 'solicitation_sources' table missing. Creating it now...")
            try:
                # NOTE: this normally never runs. Base.metadata.create_all()
                # executes FIRST and creates the table from models.py, so this is
                # the fallback for a database where create_all could not (e.g.
                # restricted DDL grants). It is kept in sync with the model on
                # purpose — the MEDIUMTEXT and the foreign keys below both matter,
                # and a divergent fallback would be worse than none.
                conn.execute(text("""
                    CREATE TABLE IF NOT EXISTS solicitation_sources (
                        id INT AUTO_INCREMENT PRIMARY KEY,
                        user_id INT NOT NULL,
                        submission_id INT NULL,
                        `text` MEDIUMTEXT NOT NULL,
                        chars INT NOT NULL DEFAULT 0,
                        source_kind VARCHAR(16) NOT NULL DEFAULT 'pdf',
                        filename VARCHAR(255) NULL,
                        url TEXT NULL,
                        sha256 VARCHAR(64) NULL,
                        created_at DATETIME NOT NULL,
                        INDEX idx_ss_user (user_id),
                        INDEX idx_ss_submission (submission_id),
                        INDEX idx_ss_sha (sha256),
                        CONSTRAINT fk_ss_user FOREIGN KEY (user_id)
                            REFERENCES users (id),
                        CONSTRAINT fk_ss_submission FOREIGN KEY (submission_id)
                            REFERENCES submissions (id) ON DELETE CASCADE
                    )
                """))
                conn.commit()
                print("[OK] Successfully created 'solicitation_sources' table!")
            except Exception as e:
                # SQLite (local dev) rejects the MySQL-specific DDL above;
                # Base.metadata.create_all already made the table there.
                print(f"[INFO] solicitation_sources DDL not applied ({e}); "
                      "relying on metadata create_all.")

        # 5g. Add submission_tasks provenance columns if missing. The checklist
        # mixes solicitation-derived tasks with Morgan/ORA process tasks, and
        # showing them identically is what let a hardcoded page limit read as
        # something the funder said. NULL means "predates this" — grouped with
        # process tasks, never claimed as the solicitation's.
        for _col, _ddl in (
            ("source", "VARCHAR(32) NULL"),
            ("source_ref", "VARCHAR(128) NULL"),
            ("source_quote", "TEXT NULL"),
        ):
            try:
                conn.execute(text(f"SELECT {_col} FROM submission_tasks LIMIT 1"))
            except (OperationalError, ProgrammingError):
                print(f"[WARN] 'submission_tasks.{_col}' column missing. Adding it now...")
                try:
                    conn.execute(text(
                        f"ALTER TABLE submission_tasks ADD COLUMN {_col} {_ddl}"))
                    conn.commit()
                    print(f"[OK] Successfully added '{_col}' column!")
                except Exception as e:
                    print(f"[ERROR] Failed to add {_col} column: {e}")

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

        # 7. Add kb_page_fingerprints.engine column if missing.
        # A fingerprint hashes the extracted page TEXT, and the scrape engines
        # extract the same unchanged page differently. Without recording who
        # wrote each row, the first run after an engine switch compares browser
        # hashes against LLM hashes and reports every page as changed.
        try:
            conn.execute(text("SELECT engine FROM kb_page_fingerprints LIMIT 1"))
        except (OperationalError, ProgrammingError):
            print("[WARN] 'engine' column missing on kb_page_fingerprints. Adding it now...")
            try:
                conn.execute(text("ALTER TABLE kb_page_fingerprints ADD COLUMN engine VARCHAR(20)"))
                conn.commit()
                print("[OK] Successfully added 'engine' column!")
            except Exception as e:
                print(f"[ERROR] Failed to add engine column: {e}")

        # A file_new draft proposes where its document belongs in the tree.
        # Without the column, approving one would have nowhere to file it and
        # every new document would land in Unfiled.
        try:
            conn.execute(text("SELECT kb_path FROM scrape_changes LIMIT 1"))
        except (OperationalError, ProgrammingError):
            print("[WARN] 'kb_path' column missing on scrape_changes. Adding it now...")
            try:
                conn.execute(text("ALTER TABLE scrape_changes ADD COLUMN kb_path VARCHAR(255)"))
                conn.commit()
                print("[OK] Successfully added 'kb_path' column!")
            except Exception as e:
                print(f"[ERROR] Failed to add kb_path column: {e}")

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
            _cached_atts = query_cache.get_attachments(user_q, _cache_ctx)
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
                "attachments": _cached_atts or [],
                "images": [],
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
    _chat_attachments = get_last_grounding().get("attachments", [])
    _chat_images = get_last_grounding().get("images", [])
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
            if _chat_attachments:
                query_cache.set_attachments(user_q, _chat_attachments, _cache_ctx)
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
        cached_attachments = query_cache.get_attachments(user_q, context_hash)

        async def generate_cached_sse():
            yield f"data: {json.dumps({'type': 'status', 'content': 'Retrieved from cache'})}\n\n"
            if cached_citations:
                yield f"data: {json.dumps({'type': 'citations', 'content': cached_citations})}\n\n"
            if cached_attachments:
                yield f"data: {json.dumps({'type': 'attachments', 'content': cached_attachments})}\n\n"
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
                    # Download links are resolved at the DELIVER step, so they
                    # are only available once the turn is complete.
                    _atts = get_last_grounding().get("attachments", []) or []
                    if _atts:
                        yield f"data: {json.dumps({'type': 'attachments', 'content': _atts})}\n\n"
                    _imgs = get_last_grounding().get("images", []) or []
                    if _imgs:
                        yield f"data: {json.dumps({'type': 'images', 'content': _imgs})}\n\n"
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
                query_cache.set_attachments(
                    user_q, get_last_grounding().get("attachments", []) or [], context_hash)

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
        # Re-emit the Sources stored alongside the answer (cit: key) so a
        # repeated question keeps its citations instead of losing them.
        return {
            "response": cached_response,
            "cached": True,
            "citations": query_cache.get_citations(user_q, context_hash=""),
            "attachments": query_cache.get_attachments(user_q, context_hash=""),
        }

    # Use Vertex AI Agent for real questions
    guest_citations = []
    guest_attachments = []
    guest_images = []
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
            # Capture grounding immediately, before any later call can mutate
            # the module-global last-grounding state.
            guest_citations = get_last_grounding().get("citations", [])
            guest_attachments = get_last_grounding().get("attachments", [])
            guest_images = get_last_grounding().get("images", [])

            if answer and "error" not in answer.lower()[:50] and "I may not have complete information" not in answer and "don't have reliable information" not in answer:
                query_cache.set(user_q, answer, context_hash="")
                # Store citations under the parallel cit: key so a later cache
                # HIT re-emits the same Sources (see the hit branch above).
                if guest_citations:
                    query_cache.set_citations(user_q, guest_citations, context_hash="")
                if guest_attachments:
                    query_cache.set_attachments(user_q, guest_attachments, context_hash="")

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

    return {"response": answer, "citations": guest_citations,
            "attachments": guest_attachments, "images": guest_images}


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
    # ...and the listing cache, or the new document stays invisible to the flat
    # list AND the tree's Unfiled bucket for up to 60s.
    _cloud_kb_cache["docs"] = None
    _cloud_kb_cache["ts"] = 0
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
    _cloud_kb_cache["docs"] = None
    _cloud_kb_cache["ts"] = 0
    result["cache_cleared"] = cleared
    return result

# =============================================================================
# KB WEB SCRAPE — trigger, progress, change review
# The crawl runs in a Cloud Run Job; these endpoints only start it and read the
# rows it writes. See kb_scrape_service.py for why it cannot run in-process.
# =============================================================================

@app.post("/api/admin/kb-scrape/run")
async def start_kb_scrape(user: dict = Depends(get_current_user), db: Session = Depends(get_db)):
    """Kick off a scrape of morgan.edu/office-of-research-administration."""
    import kb_scrape_service as scrape

    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    try:
        run = await asyncio.to_thread(scrape.start_run, db, user.get("id"))
    except RuntimeError as e:
        # Already running — a conflict, not a server fault.
        raise HTTPException(status_code=409, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Could not start scrape: {e}")
    return scrape.run_to_dict(run)


@app.get("/api/admin/kb-scrape/status")
async def kb_scrape_status(user: dict = Depends(get_current_user), db: Session = Depends(get_db)):
    """Polled by the progress bar. Cheap by design — one indexed row read."""
    import kb_scrape_service as scrape

    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")

    run = scrape.active_run(db) or scrape.last_finished_run(db)
    return {
        "run": scrape.run_to_dict(run),
        "baseline_pages": scrape.baseline_size(db),
    }


@app.post("/api/admin/kb-scrape/cancel")
async def cancel_kb_scrape(user: dict = Depends(get_current_user), db: Session = Depends(get_db)):
    """Ask the running job to stop. It checks this flag between pages, so a
    cancel takes effect within a page or two rather than instantly."""
    import kb_scrape_service as scrape

    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")

    run = scrape.active_run(db)
    if not run:
        raise HTTPException(status_code=404, detail="No scrape is running")
    run.cancel_requested = True
    db.commit()
    return {"success": True, "message": "Cancelling after the current page"}


@app.get("/api/admin/kb-scrape/changes")
async def list_kb_scrape_changes(
    run_id: int = 0,
    status: str = "",
    user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """The change report: the latest run, plus everything still unreviewed.

    Asking for a specific run_id returns exactly that run. The default view does
    NOT, because scoping it to one run makes a scrape bury the previous scrape's
    unreviewed work: a run that finds nothing becomes "latest", the panel empties,
    and approvable drafts from earlier runs vanish from the queue while the tree's
    badges still count them. The panel and the tree then disagree about the same
    database, and the tree is the one telling the truth.
    """
    from sqlalchemy import or_

    import kb_scrape_service as scrape
    from models import ScrapeChange

    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")

    explicit_run = bool(run_id)
    if not run_id:
        latest = scrape.active_run(db) or scrape.last_finished_run(db)
        if not latest:
            return {"changes": [], "run_id": None, "counts": {}}
        run_id = latest.id

    if explicit_run:
        scope = ScrapeChange.run_id == run_id
    else:
        scope = or_(ScrapeChange.run_id == run_id, ScrapeChange.status == "pending")

    query = db.query(ScrapeChange).filter(scope)
    if status:
        query = query.filter(ScrapeChange.status == status)

    # Pending first — those are the ones waiting on a decision.
    order = {"pending": 0, "approved": 1, "rejected": 2, "skipped": 3, "cosmetic": 4}
    rows = query.order_by(ScrapeChange.id.desc()).limit(500).all()
    rows.sort(key=lambda c: order.get(c.status, 9))

    counts: dict = {}
    for c in db.query(ScrapeChange).filter(scope).all():
        counts[c.status] = counts.get(c.status, 0) + 1

    return {
        "run_id": run_id,
        "changes": [scrape.change_to_dict(c) for c in rows],
        "counts": counts,
    }


@app.get("/api/admin/kb-scrape/changes/{change_id}/diff")
async def kb_scrape_change_diff(
    change_id: int, user: dict = Depends(get_current_user), db: Session = Depends(get_db)
):
    from models import ScrapeChange

    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")

    change = db.query(ScrapeChange).filter(ScrapeChange.id == change_id).first()
    if not change:
        raise HTTPException(status_code=404, detail="Change not found")
    return {
        "id": change.id,
        "url": change.url,
        "doc_id": change.doc_id,
        "what_changed": change.what_changed or "",
        "evidence_quote": change.evidence_quote or "",
        "previous_content": change.previous_content or "",
        "new_content": change.new_content or "",
    }


@app.post("/api/admin/kb-scrape/changes/{change_id}/revert")
async def revert_kb_scrape_change(
    change_id: int, user: dict = Depends(get_current_user), db: Session = Depends(get_db)
):
    """Restore the content an auto-applied change replaced."""
    import kb_scrape_service as scrape

    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")

    result = await asyncio.to_thread(scrape.revert_change, db, change_id, user.get("id"))
    if not result["success"]:
        raise HTTPException(status_code=400, detail=result["message"])
    result["cache_cleared"] = query_cache.clear()
    _cloud_kb_cache["docs"] = None
    _cloud_kb_cache["ts"] = 0
    return result


@app.post("/api/admin/kb-scrape/changes/{change_id}/approve")
async def approve_kb_scrape_change(
    change_id: int, user: dict = Depends(get_current_user), db: Session = Depends(get_db)
):
    """Apply a proposed change. This is the ONLY path by which a scrape reaches a
    knowledge base document — until it runs, the crawl has written nothing."""
    import kb_scrape_service as scrape

    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")

    result = await asyncio.to_thread(scrape.approve_change, db, change_id, user.get("id"))
    if not result["success"]:
        raise HTTPException(status_code=400, detail=result["message"])

    # The document's content changed, so the chatbot's cached answers and any
    # live ADK sessions are now stale.
    result["cache_cleared"] = query_cache.clear()
    try:
        from vertex_agent import _session_cache
        _session_cache.clear()
    except Exception:
        pass
    _cloud_kb_cache["docs"] = None
    _cloud_kb_cache["ts"] = 0
    return result


@app.post("/api/admin/kb-scrape/changes/dismiss-reported")
async def dismiss_reported_changes(
    user: dict = Depends(get_current_user), db: Session = Depends(get_db)
):
    """Clear every pending proposal that has no draft to approve.

    Declared BEFORE the /{change_id}/... routes: FastAPI matches in order, and
    a path param would otherwise swallow "dismiss-reported" as a change_id.
    """
    import kb_scrape_service as scrape

    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    return await asyncio.to_thread(scrape.dismiss_reported, db, user.get("id"))


@app.post("/api/admin/kb-scrape/changes/{change_id}/reject")
async def reject_kb_scrape_change(
    change_id: int, user: dict = Depends(get_current_user), db: Session = Depends(get_db)
):
    """Dismiss a proposal. Nothing was ever written, so nothing is undone."""
    import kb_scrape_service as scrape

    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")

    result = await asyncio.to_thread(scrape.reject_change, db, change_id, user.get("id"))
    if not result["success"]:
        raise HTTPException(status_code=400, detail=result["message"])
    return result


@app.post("/api/internal/kb-scrape/run")
async def internal_kb_scrape(request: Request, db: Session = Depends(get_db)):
    """Same scrape on a schedule, for Cloud Scheduler. Same shared-secret auth
    as the other /api/internal endpoints."""
    import kb_scrape_service as scrape

    secret = request.headers.get("X-Research-Secret", "")
    expected = os.getenv("RESEARCH_SECRET", "")
    if not expected or secret != expected:
        raise HTTPException(status_code=403, detail="Invalid research secret")
    try:
        run = await asyncio.to_thread(scrape.start_run, db, None)
    except RuntimeError as e:
        return {"skipped": True, "reason": str(e)}
    return scrape.run_to_dict(run)


@app.post("/api/admin/cloud-kb/documents")
async def create_cloud_kb_doc(request: Request, user: dict = Depends(get_current_user)):
    """Author a new KB document straight into a tree node.

    Body: {title, content, kb_path?, source_url?, doc_id?}. doc_id is derived
    from the title when not supplied; creation fails rather than overwrites if
    it collides.
    """
    from kb_tree import node_paths, suggest_doc_id
    from datastore_manager import create_kb_document

    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")

    body = await request.json()
    title = (body.get("title") or "").strip()
    content = (body.get("content") or "").strip()
    kb_path = (body.get("kb_path") or "").strip().strip("/")
    source_url = (body.get("source_url") or "").strip()

    if not title:
        raise HTTPException(status_code=400, detail="Title is required")
    if not content:
        raise HTTPException(status_code=400, detail="Content is required")
    if kb_path and kb_path not in node_paths():
        raise HTTPException(status_code=400, detail=f"Unknown tree path: {kb_path}")

    doc_id = (body.get("doc_id") or "").strip() or suggest_doc_id(title, kb_path)
    if not doc_id:
        raise HTTPException(status_code=400, detail="Could not derive a document id from that title")

    result = await asyncio.to_thread(
        create_kb_document, doc_id, title, content, kb_path, source_url
    )
    if not result["success"]:
        # A colliding id is the caller's problem to fix, not a server fault.
        status = 409 if "already exists" in result["message"] else 500
        raise HTTPException(status_code=status, detail=result["message"])

    # A new document changes what the chatbot can answer, so drop the answer
    # cache as well as the listing cache.
    result["cache_cleared"] = query_cache.clear()
    _cloud_kb_cache["docs"] = None
    _cloud_kb_cache["ts"] = 0
    try:
        import vertex_agent
        vertex_agent._kb_url_map = None      # so the new source_url resolves in citations
        vertex_agent._session_cache.clear()
    except Exception:
        pass
    return result


@app.get("/api/admin/cloud-kb/doc-id")
async def preview_cloud_kb_doc_id(
    title: str = "", kb_path: str = "", user: dict = Depends(get_current_user)
):
    """Live preview of the doc_id a title will produce, plus a collision check."""
    from kb_tree import suggest_doc_id
    from datastore_manager import document_exists

    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    doc_id = suggest_doc_id(title, kb_path.strip().strip("/"))
    taken = bool(doc_id) and await asyncio.to_thread(document_exists, doc_id)
    return {"doc_id": doc_id, "taken": taken}


@app.get("/api/admin/cloud-kb/tree")
async def get_cloud_kb_tree(
    user: dict = Depends(get_current_user),
    refresh: bool = False,
    db: Session = Depends(get_db),
):
    """The KB as a browsable hierarchy mirroring morgan.edu/ora.

    Shape and titles come from the bundled manifest; placement comes from each
    document's own kb_path; counts are computed from the live datastore.
    Documents that are unplaced — or placed at a node that no longer exists —
    are returned in `unfiled` rather than being dropped.
    """
    import time as _t
    from kb_scrape_service import pending_by_doc
    from kb_tree import build_tree, flat_paths

    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    try:
        if not refresh and _cloud_kb_cache["docs"] and _t.time() - _cloud_kb_cache["ts"] < 60:
            docs = _cloud_kb_cache["docs"]
        else:
            docs = await asyncio.to_thread(list_datastore_documents)
            _cloud_kb_cache["docs"] = docs
            _cloud_kb_cache["ts"] = _t.time()
        # Badges come from this join, not from the documents — an unapproved
        # proposal must leave no mark on the document itself. Never cached with
        # the doc list: approving a change has to clear the badge immediately.
        result = build_tree(docs, pending=pending_by_doc(db))
        result["paths"] = flat_paths()
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to build KB tree: {e}")


@app.put("/api/admin/cloud-kb/documents/{doc_id}/placement")
async def set_cloud_kb_placement(
    doc_id: str,
    request: Request,
    user: dict = Depends(get_current_user),
):
    """File a document into a tree node by setting its kb_path.

    Only paths that exist in the manifest are accepted — you can file a document
    anywhere in the tree, but you cannot invent a node. That is what keeps the
    tree mirroring morgan.edu. An empty kb_path unfiles the document.
    """
    from kb_tree import node_paths
    from datastore_manager import update_placement

    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")

    body = await request.json()
    kb_path = (body.get("kb_path") or "").strip().strip("/")
    if kb_path and kb_path not in node_paths():
        raise HTTPException(status_code=400, detail=f"Unknown tree path: {kb_path}")

    result = await asyncio.to_thread(update_placement, doc_id, kb_path)
    if not result["success"]:
        raise HTTPException(status_code=500, detail=result["message"])

    # The listing cache backs both the flat list and the tree; a stale entry
    # would show the document in its old node for up to 60s.
    _cloud_kb_cache["docs"] = None
    _cloud_kb_cache["ts"] = 0
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
        # Draft Review: whether this proposal has a solicitation to be reviewed
        # against. Drives the tool's badge and its attach-first empty state.
        "has_solicitation_requirements": bool(getattr(s, "solicitation_json", None)),
        "draft_review_saved_at": _saved_review_at(s),
        "created_at": iso_utc(s.created_at),
        "updated_at": iso_utc(s.updated_at),
    }
    if include_tasks:
        out["tasks"] = [_submission_task_to_dict(t) for t in s.tasks]
        # Detail view only: enough for the review modal's header to name the
        # solicitation and show how well it could be read, without shipping
        # every requirement row on the list view.
        out["solicitation_summary"] = _proposals_service.solicitation_summary(s)
        # Whether the solicitation DOCUMENT is on file, so the UI can offer
        # "re-read" instead of asking for the upload again. Detail view only:
        # on the list view this would be one extra query per proposal.
        from sqlalchemy.orm import object_session as _object_session
        _sess = _object_session(s)
        out["has_solicitation_source"] = bool(
            _sess and _proposals_service.has_solicitation_source(_sess, s.id))
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
        # Provenance. The UI groups on this so a PI can see which tasks were
        # read out of their solicitation (and check the quote) and which are
        # Morgan/ORA process that no funder states. NULL for tasks predating
        # the column and for the PI's own additions.
        "source": getattr(t, "source", None),
        "source_quote": getattr(t, "source_quote", None),
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
    file: Optional[UploadFile] = File(None),
    source_id: Optional[str] = Form(None),
    user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Step 1: parse a sponsor PDF and return the extracted JSON. Does
    NOT create a Submission -- the user reviews/edits, then calls the
    confirm endpoint.

    IT DOES, HOWEVER, KEEP THE DOCUMENT. This is the first and often the only
    moment the PI hands us the file, and the text is already in hand here. The
    deep requirement read is a separate 60-150s request that deliberately does
    not block Create, so storing the text there alone left a real hole: a PI who
    clicked Create early, or whose read failed, got a proposal carrying the
    funder's numbers and no document — and was asked to upload the same file
    again when they opened Draft Review. Saved unbound; /confirm binds it.

    Reads via read_pdf() rather than extract_from_pdf_bytes() for one reason:
    the one-shot form throws the text away, and the text is the point.

    `source_id` REUSES a document already stored for this user, taken in
    preference to a file — the same precedence /api/me/solicitation-requirements
    already applies, and for the same reason: one document should be read once
    and leave one row. It is what the "you uploaded this recently" picker sends
    when a PI comes back to an abandoned read, so they are never asked to find
    the same file twice. Ownership is a filter inside the load, so another
    user's id is indistinguishable from a missing one."""
    if not user:
        raise HTTPException(401, "Unauthorized")

    from services import solicitation_extractor as _sx

    if source_id:
        stored = _proposals_service.load_solicitation_source_by_id(
            db, source_id=source_id, user_id=user["user_id"])
        if stored is None:
            raise HTTPException(404, "That stored solicitation is no longer available.")
        text = stored.get("text") or ""
        extracted = _sx.extract_from_text(text) if text.strip() else None
        if extracted is None:
            raise HTTPException(422, "Couldn't read the stored solicitation.")
        # Echo the SAME id — reusing a document must not store a second copy.
        return {"extracted": extracted, "source_id": stored["id"]}

    if file is None:
        raise HTTPException(400, "Upload a PDF or choose a stored solicitation.")
    filename = (file.filename or "").lower()
    ctype = (file.content_type or "").lower()
    if not (filename.endswith(".pdf") or "pdf" in ctype):
        raise HTTPException(400, "Only PDF uploads are supported.")

    pdf_bytes = await file.read()
    if not pdf_bytes:
        raise HTTPException(400, "Uploaded file is empty.")
    if len(pdf_bytes) > _MAX_SOLICITATION_PDF_BYTES:
        raise HTTPException(413, "PDF is larger than 25 MB.")

    read = _sx.read_pdf(pdf_bytes)
    text = read.get("text") or ""
    # Stored BEFORE the model runs. A Gemini failure must not also cost the
    # document — that is exactly when you least want to ask the PI for it again.
    source_id = _proposals_service.save_solicitation_source(
        db, user_id=user["user_id"], text=text, source_kind="pdf",
        filename=file.filename, url=None)

    extracted = _sx.extract_from_text(text) if text.strip() else None
    if extracted is None:
        raise HTTPException(
            422,
            "Couldn't read this PDF -- the file may be scanned or "
            "image-only. Try a text-based PDF, or create the proposal "
            "manually.",
        )
    # The client passes this back at confirm, which binds the stored document
    # to the new proposal.
    return {"extracted": extracted, "source_id": source_id}


@app.get("/api/me/solicitation-sources/unbound")
async def list_unbound_solicitation_sources(
    user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Documents this user read but never attached to a proposal.

    Feeds the "you uploaded this recently" picker. Metadata only — never the
    text (~300KB a row, and nothing on the picker renders it). Purely an
    affordance: if this call fails the UI simply does not show the picker and
    uploading still works, so it must never be a gate."""
    if not user:
        raise HTTPException(401, "Unauthorized")
    return {"sources": _proposals_service.list_unbound_solicitation_sources(
        db, user["user_id"])}


class SolicitationUrlRequest(BaseModel):
    url: str


@app.post("/api/me/submissions/from-solicitation/url")
async def extract_solicitation_from_url(
    payload: SolicitationUrlRequest,
    user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Step 1 (URL variant): fetch a sponsor solicitation URL (an HTML page or a
    linked PDF), extract the same structured JSON the PDF flow returns. Does NOT
    create a Submission -- the user reviews/edits, then calls the confirm
    endpoint. Same response shape as /from-solicitation so the UI is shared.

    Keeps the fetched text for the same reason the PDF path does, and with more
    urgency: some funder sites block cloud-datacenter IPs, so a URL that reads
    once may not read again."""
    if not user:
        raise HTTPException(401, "Unauthorized")

    from services import url_fetcher, solicitation_extractor as _sx
    try:
        text = url_fetcher.fetch_solicitation_text(payload.url)
    except url_fetcher.FetchError as e:
        raise HTTPException(e.status, e.message)

    source_id = _proposals_service.save_solicitation_source(
        db, user_id=user["user_id"], text=text or "", source_kind="url",
        filename=None, url=payload.url)

    extracted = _sx.extract_from_text(text)
    if extracted is None:
        raise HTTPException(
            422,
            "Couldn't pull a solicitation out of that page -- it may not be a "
            "solicitation, or the content is image-only. Try the PDF upload, or "
            "create the proposal manually.",
        )
    return {"extracted": extracted, "source_id": source_id}


@app.post("/api/me/submissions/from-solicitation/confirm")
async def confirm_solicitation_submission(
    payload: dict,
    user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Step 2: commit a user-reviewed extracted dict as a real Submission.

    Body shape:
        { "extracted": {<contract dict>}, "title_override": "optional",
          "requirements": [...], "merit_criteria": [...],
          "eligibility_notes": [...], "read_report": {...},
          "extraction": {...}, "source": {...} }

    Everything after `title_override` is optional and comes from the separate
    /api/me/solicitation-requirements read. When present it is stored as this
    proposal's solicitation — THE save point, and the only place the create flow
    writes it. When absent the proposal is still created; the PI can attach the
    requirements later rather than being blocked on a slow read."""
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

    stored = _solicitation_payload(payload, extracted)
    if stored is not None:
        _proposals_service.save_solicitation_profile(db, sub, stored)
        _proposals_service.sync_solicitation_requirement_tasks(db, sub, stored)
    # Bind the document itself, even when no requirements came back: a failed
    # read should still leave the text on the proposal so it can be re-read
    # without asking the PI for the file again.
    if payload.get("source_id"):
        _proposals_service.bind_solicitation_source(
            db, source_id=payload["source_id"], user_id=user["user_id"],
            submission_id=sub.id)
    db.refresh(sub)
    return _submission_to_dict(sub, include_tasks=True)


# ----------------------------------------------------------------------------
# REMOVED 2026-08-11 — Draft Critic (POST .../critique) and services/draft_critic.py.
# Product decision. Draft Review replaces it and does strictly more: the same
# mechanical checks (page limits, required attachments, budget vs cap) now run
# as deterministic rows inside services/generic_checks.py, driven by the stored
# solicitation rather than by sponsor defaults, plus the requirement coverage
# Draft Critic never had. Do not re-add.
#
# proposals_service.reconstruct_solicitation_context SURVIVES it, unused by any
# feature today: it is the only parser of the `notes` solicitation lines, which
# two write paths still produce, and its tests document that round-trip.


class EirReviewRequest(BaseModel):
    draft_text: str = ""


class SectionCheckRequest(BaseModel):
    section: str
    text: str = ""
    rulebook: str = "the PAPPG"


_NO_SOLICITATION_DETAIL = (
    "Attach this proposal's solicitation first — the review is run against its "
    "requirements."
)


def _require_profile_and_budget(sub):
    """The two inputs every draft review needs, or a 409 explaining what to do.

    409 rather than a review of nothing: with no stored solicitation the engine
    would score a draft against zero requirements and hand back a confident
    percentage that means nothing at all. The frontend keys off this status to
    show the attach panel."""
    profile = _proposals_service.load_solicitation_profile(sub)
    if profile is None:
        raise HTTPException(409, _NO_SOLICITATION_DETAIL)
    from services.budget_helper import compute_budget
    budget = None
    raw_b = getattr(sub, "budget_json", None)
    if raw_b:
        try:
            budget = compute_budget(json.loads(raw_b))
        except (ValueError, TypeError):
            budget = None
    return profile, budget


def _saved_review_at(sub) -> Optional[str]:
    """When the PI last saved a review of this proposal, or None.

    Only the TIMESTAMP rides on the submission list — the saved review itself is
    tens of KB and `list_submissions` loads whole rows, so shipping it with every
    proposal in the list would be the same mistake that kept the solicitation
    TEXT off this table."""
    raw = getattr(sub, "draft_review_json", None)
    if not raw:
        return None
    try:
        return (json.loads(raw) or {}).get("saved_at")
    except (ValueError, TypeError):
        return None


@app.post("/api/me/submissions/{submission_id}/draft-review/save")
async def save_draft_review(
    submission_id: int,
    payload: dict = Body(...),
    user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Keep the LAST review, so the PI can reopen it instead of re-running.

    Explicit action only. The review is stateless by design — the paste is an
    unpublished manuscript — and this stores the RESULT, which carries evidence
    quotes from that draft. The draft text itself is still never stored.
    Overwrites: one saved review per proposal, the most recent one."""
    if not user:
        raise HTTPException(401, "Unauthorized")
    sub = _proposals_service.get_submission(db, submission_id=submission_id, user_id=user["user_id"])
    if sub is None:
        raise HTTPException(404, "Submission not found")
    result = (payload or {}).get("result")
    if not isinstance(result, dict) or not result:
        raise HTTPException(400, "No review to save.")
    saved_at = datetime.utcnow().isoformat() + "Z"
    sub.draft_review_json = json.dumps({
        "version": 1,
        "result": result,
        "extraction": (payload or {}).get("extraction"),
        "saved_at": saved_at,
    })
    db.commit()
    return {"saved_at": saved_at}


@app.get("/api/me/submissions/{submission_id}/draft-review/saved")
async def get_saved_draft_review(
    submission_id: int,
    user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """The saved review, or 404 when there is none."""
    if not user:
        raise HTTPException(401, "Unauthorized")
    sub = _proposals_service.get_submission(db, submission_id=submission_id, user_id=user["user_id"])
    if sub is None:
        raise HTTPException(404, "Submission not found")
    raw = getattr(sub, "draft_review_json", None)
    if not raw:
        raise HTTPException(404, "No saved review")
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        raise HTTPException(404, "No saved review")


@app.delete("/api/me/submissions/{submission_id}/draft-review/saved")
async def delete_saved_draft_review(
    submission_id: int,
    user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Discard it. The PI stored quotes from their own manuscript; they get to
    take that back without deleting the proposal."""
    if not user:
        raise HTTPException(401, "Unauthorized")
    sub = _proposals_service.get_submission(db, submission_id=submission_id, user_id=user["user_id"])
    if sub is None:
        raise HTTPException(404, "Submission not found")
    sub.draft_review_json = None
    db.commit()
    return {"deleted": True}


@app.post("/api/me/submissions/{submission_id}/draft-review")
async def draft_review_endpoint(
    submission_id: int,
    payload: EirReviewRequest,
    user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Completeness review of a pasted draft against THIS proposal's solicitation.

    Stateless — the paste is NOT persisted: it is the PI's unpublished
    manuscript, and storing it would create a copy nobody asked for. The
    submission is read only for its title and its saved budget.

    The returned `score` is completeness against the stored solicitation,
    computed in code from coverage counts (golden rule 1). It is not a funding
    prediction, and is withheld entirely when the AI layer is unavailable."""
    if not user:
        raise HTTPException(401, "Unauthorized")
    sub = _proposals_service.get_submission(db, submission_id=submission_id, user_id=user["user_id"])
    if sub is None:
        raise HTTPException(404, "Submission not found")

    from services import draft_review as _dr
    profile, budget = _require_profile_and_budget(sub)
    result = _dr.review_draft(payload.draft_text, profile=profile,
                              title=sub.title, budget=budget)
    return {"submission_id": submission_id, "sponsor": sub.sponsor, "result": result}


# Per-file and total upload ceilings. A real proposal package IS several PDFs;
# these are generous enough for that and small enough that a mis-drop (a video,
# a dataset) is rejected before it is read into memory.
_DRAFT_MAX_FILE_BYTES = 25 * 1024 * 1024      # 25 MB per file
_DRAFT_MAX_TOTAL_BYTES = 60 * 1024 * 1024     # 60 MB per request
_DRAFT_MAX_FILES = 12

# Keys `document_text.extract_upload` may attach to a per-file dict that must
# NEVER ride back to the browser: `text`/`section_spans`/`page_texts` are the
# PI's unpublished manuscript (whole, sectioned, or page-by-page), and
# `page_ledger`/`ledger_page_counts`/`ledger_toc_mismatch` are the ledger's own
# internal keys -- the ledger and its mismatch ARE returned, but once, at the
# top level of `result` (see `services.draft_review.review_draft`'s return),
# never duplicated inside a per-file extraction entry.
_EXTRACTION_FILE_STRIP = ("text", "section_spans", "page_texts",
                          "page_ledger", "ledger_page_counts", "ledger_toc_mismatch")


@app.post("/api/me/submissions/{submission_id}/draft-review/upload")
async def draft_review_upload(
    submission_id: int,
    files: list[UploadFile] = File(...),
    user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """The same review, but from uploaded files instead of pasted text.

    Accepts several files at once because a real proposal package IS several
    files (narrative, letters, budget justification, the required attachments).
    Each is extracted independently and the results are concatenated with the
    filename as a heading, which also gives the locate stage a marker for
    sections that are otherwise easy to miss.

    A file that cannot be read does NOT fail the request — it comes back in
    `extraction.files` with an `error`, and the review runs on whatever was
    readable. Reporting an unreadable file as missing content is the one thing
    this must never do. Uploads are parsed in memory and never written to disk
    or persisted."""
    if not user:
        raise HTTPException(401, "Unauthorized")
    sub = _proposals_service.get_submission(db, submission_id=submission_id, user_id=user["user_id"])
    if sub is None:
        raise HTTPException(404, "Submission not found")
    if not files:
        raise HTTPException(400, "No files uploaded.")
    if len(files) > _DRAFT_MAX_FILES:
        raise HTTPException(400, f"Too many files (max {_DRAFT_MAX_FILES}).")

    from services import document_text as _dt
    from services import draft_review as _dr

    # Before reading a single byte: no solicitation, no review.
    profile, budget = _require_profile_and_budget(sub)

    extracted, total_bytes = [], 0
    for upload in files:
        data = await upload.read()
        total_bytes += len(data)
        if len(data) > _DRAFT_MAX_FILE_BYTES:
            extracted.append({"filename": upload.filename, "text": "", "pages": 0,
                              "chars": 0, "truncated": False,
                              "error": f"Larger than {_DRAFT_MAX_FILE_BYTES // (1024 * 1024)} MB."})
            continue
        if total_bytes > _DRAFT_MAX_TOTAL_BYTES:
            raise HTTPException(400, "Those files are too large in total (max 60 MB).")
        # `sections` opts this file into STRUCTURAL splitting: a single
        # combined Research.gov PDF carries its own sub-document
        # boundaries, and reading them is what lets a PI upload the file
        # Research.gov handed them instead of eleven separate ones.
        extracted.append(_dt.extract_upload(
            upload.filename or "file", data,
            sections=(profile or {}).get("sections")))

    # `document_text.extract_upload` builds a `page_ledger` for EVERY PDF with
    # extractable text whenever `sections` is non-empty -- not only the one
    # that turned out to be a structurally-split combined package. This loop
    # passes the same `sections` to every file, so on the "one PDF per
    # section" pattern this repo otherwise expects, EVERY file gets its own
    # ledger. Picking "the first one with a ledger" would silently pick
    # whichever file happens to sort first, describe only ITS pages as "the
    # upload's" pages, and -- if that file's true content maps to no key in
    # this profile and every row comes back `unassigned` -- withhold the
    # score for the WHOLE review over a file that isn't even the real
    # package. So the selection is keyed on how many files were uploaded:
    #
    #   ONE file  -> use its ledger unconditionally. Its pages ARE the
    #                upload's pages, whether or not `pdf_sections.split()`
    #                found real structure -- and a single combined PDF whose
    #                split BAILS is exactly the case that needs the page
    #                accounting most.
    #   2+ files  -> use a ledger ONLY from a file whose `spans_are_structural`
    #                is True -- the flag that distinguishes a real
    #                object-graph split from "the model's page-ledger WALK
    #                also produced something for this ordinary file". If no
    #                file has one, there is no meaningful single page
    #                numbering across files: set no ledger, and the panel
    #                simply does not render rather than reporting a lie.
    _ledger, _toc_mismatch = None, []
    if len(extracted) == 1:
        _only = extracted[0]
        if _only.get("page_ledger"):
            _ledger = _only["page_ledger"]
            _toc_mismatch = _only.get("ledger_toc_mismatch") or []
    else:
        for f in extracted:
            if f.get("spans_are_structural") and f.get("page_ledger"):
                _ledger = f["page_ledger"]
                _toc_mismatch = f.get("ledger_toc_mismatch") or []
                break

    # ONE FILE IS ONE SECTION, when the filename says so. This replaces
    # `_dt.combine`: it produces the same document, and additionally hands back
    # which file IS which section and each one's REAL page count -- both of which
    # the old path computed and threw away, leaving the reviewer to re-guess the
    # seams with a model call. See services/document_text.map_files_to_sections.
    draft_text, file_spans, _leftover, file_map = _dt.map_files_to_sections(
        extracted, (profile or {}).get("sections") or {})
    # Section key -> real page count, the shape run_deterministic wants. Without
    # it every page rule runs on a word-count estimate even though we hold the
    # exact count from the PDF. Deliberately NOT `ledger_page_counts`: that is
    # ATTRIBUTION (pages the ledger could assign to a section by name), not a
    # section's real page REACH -- see `page_ledger.page_counts_from_ledger`'s
    # own docstring, which says in so many words not to feed it to a page-limit
    # rule. `span["pages"]` (from `map_files_to_sections`/`spans_from_ledger`)
    # already carries the real reach, absorbed interior pages included.
    page_counts = {k: v["pages"] for k, v in file_spans.items() if v.get("pages")}
    if not draft_text.strip():
        # Nothing readable. Return the per-file errors rather than a review that
        # would report every requirement as missing.
        return {
            "submission_id": submission_id, "sponsor": sub.sponsor, "result": None,
            "extraction": {"files": [{k: v for k, v in f.items()
                                      if k not in _EXTRACTION_FILE_STRIP}
                                     for f in extracted], "words": 0},
            "error": "Couldn't read any text from those files.",
        }

    # Did the PDF's OWN structure name these sections? If so the model is not
    # asked to name whatever is left, so the score's denominator is fixed rather
    # than depending on a guess that lands on some runs and not others.
    #
    # MUST read `spans_are_structural`, never `bool(section_spans)`.
    # `section_spans` is now also set when the page-ledger WALK named a
    # section `pdf_sections.split()` could not -- a model call, not a
    # deterministic read. Reading presence alone would silently disable the
    # AI locate stage the moment `split()` bails (the common case for
    # anything but a Research.gov-assembled combined PDF), leaving any
    # section neither a filename nor the walk named permanently
    # `could_not_locate` with nothing on screen saying why.
    structural = any(f.get("spans_are_structural") for f in extracted)
    result = _dr.review_draft(draft_text, profile=profile, title=sub.title,
                              budget=budget, pages=page_counts or None,
                              file_spans=file_spans or None,
                              structural=structural,
                              ledger=_ledger, toc_mismatch=_toc_mismatch)
    return {
        "submission_id": submission_id,
        "sponsor": sub.sponsor,
        "result": result,
        # Per-file report so the UI can show what was read and what wasn't. The
        # extracted TEXT is deliberately not echoed back -- nor is the ledger's
        # per-page attribution (it rides once, at the top level, inside
        # `result`, not per file).
        "extraction": {
            "files": [{k: v for k, v in f.items()
                       if k not in _EXTRACTION_FILE_STRIP} for f in extracted],
            "words": len(draft_text.split()),
            # Which file was read as which section, so a mis-map is visible on
            # screen rather than silently shaping the score.
            "sections": file_map,
        },
    }


@app.get("/api/me/section-check/sections")
async def section_check_sections(rulebook: str = "the PAPPG"):
    """Which sections a PI can check one at a time, in Research.gov's order.

    Auth-free: it is a static list of section names, and the picker needs it
    before the modal has anything to check."""
    from services import rulebook_baseline as _rb
    return {"rulebook": rulebook, "sections": _rb.sections_offered(rulebook)}


@app.get("/api/me/submissions/{submission_id}/section-check/sections")
async def section_check_sections_for_submission(
    submission_id: int,
    rulebook: str = "the PAPPG",
    user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """The sections THIS proposal can have checked, one at a time.

    The auth-free route above answers for the rulebook and cannot do better --
    it never sees a submission. So every proposal was offered the PAPPG's seven
    sections whatever its own solicitation asked for, and the solicitation's own
    deliverables were unreachable: measured on a live NSF 23-598 proposal, 8
    scored Letter of Intent rules a PI had no way to check.

    Both lists are returned, because neither contains the other -- offering only
    what the solicitation names would drop the 48 PAPPG rules on References
    Cited, Facilities and Senior/Key Personnel, which NSF enforces whether or
    not a given solicitation restates them. Each entry carries its own
    `solicitation_rules` / `rulebook_rules` counts so the picker can group
    without recomputing the split.

    Auth'd and ownership-checked, unlike the auth-free route, because it reads
    that proposal's solicitation."""
    if not user:
        raise HTTPException(401, "Unauthorized")
    sub = _proposals_service.get_submission(
        db, submission_id=submission_id, user_id=user["user_id"])
    if sub is None:
        raise HTTPException(404, "Submission not found")
    from services import solicitation_profile as _sp
    profile = _proposals_service.load_solicitation_profile(sub)
    return {"rulebook": rulebook,
            "sections": _sp.sections_offered_for(profile, rulebook)}


def _section_check_inputs(payload_section: str, rulebook: str,
                          profile: Optional[dict] = None):
    """Validate the pair, or 400 naming what is wrong.

    THE PROFILE IS PART OF THE ANSWER, not a refinement of it. This asked the
    rulebook alone until 2026-08-26, so a section only the SOLICITATION names --
    NSF 23-598's Letter of Intent, 8 scored rules and the first thing that
    program requires -- was refused here even once the picker offered it. That
    made this the second of the two places enforcing rulebook-only.

    Still a real gate: a section neither source knows is still 400, so widening
    it did not open it."""
    from services import rulebook_baseline as _rb
    from services import solicitation_profile as _sp
    if not _rb.rules_for(rulebook):
        raise HTTPException(400, f"No rules are on file for {rulebook}.")
    if _rb.rules_for(rulebook, payload_section):
        return
    if profile:
        key = _sp.resolve_section_key(profile.get("sections") or {},
                                      payload_section)
        if key and _sp.requirements_for(profile, key):
            return
    raise HTTPException(
        400, f"Neither {rulebook} nor this solicitation has rules on file "
             f"for that section.")


@app.post("/api/me/submissions/{submission_id}/section-check")
async def section_check_endpoint(
    submission_id: int,
    payload: SectionCheckRequest,
    user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Check ONE section against its rulebook, while the PI is still writing it.

    Stateless — the paste is NOT persisted. It is the PI's unpublished
    manuscript, the same rule Draft Review follows.

    Deliberately does NOT 409 without a solicitation, unlike draft-review: these
    rules are NSF's, not the solicitation's, and no completeness percentage is
    returned, so the guard that 409 exists to enforce has nothing to protect
    here. When a solicitation IS attached its own rows for this section are
    checked alongside."""
    if not user:
        raise HTTPException(401, "Unauthorized")
    sub = _proposals_service.get_submission(
        db, submission_id=submission_id, user_id=user["user_id"])
    if sub is None:
        raise HTTPException(404, "Submission not found")
    # The profile is loaded BEFORE the gate because the gate now consults it —
    # a section only this solicitation names must not be refused.
    profile = _proposals_service.load_solicitation_profile(sub)
    _section_check_inputs(payload.section, payload.rulebook, profile)

    from services import draft_review as _dr
    budget = None
    raw_b = getattr(sub, "budget_json", None)
    if raw_b:
        try:
            from services.budget_helper import compute_budget
            budget = compute_budget(json.loads(raw_b))
        except (ValueError, TypeError):
            budget = None

    result = _dr.review_section(payload.text, section=payload.section,
                                rulebook=payload.rulebook, profile=profile,
                                budget=budget)
    return {"submission_id": submission_id, "result": result}


@app.post("/api/me/submissions/{submission_id}/section-check/upload")
async def section_check_upload(
    submission_id: int,
    section: str = Form(...),
    rulebook: str = Form("the PAPPG"),
    file: UploadFile = File(...),
    user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """The same check from an uploaded PDF.

    ONE file, because one file IS one section here — which is what makes the
    page count exact rather than a word-count estimate. That is the only thing
    this path can do that a paste cannot, and it is the whole reason it exists."""
    if not user:
        raise HTTPException(401, "Unauthorized")
    sub = _proposals_service.get_submission(
        db, submission_id=submission_id, user_id=user["user_id"])
    if sub is None:
        raise HTTPException(404, "Submission not found")
    # Same ordering as the paste path above, and for the same reason: the gate
    # consults the solicitation, so the profile has to exist before it runs.
    profile = _proposals_service.load_solicitation_profile(sub)
    _section_check_inputs(section, rulebook, profile)

    data = await file.read()
    if len(data) > _DRAFT_MAX_FILE_BYTES:
        raise HTTPException(
            400, f"That file is larger than {_DRAFT_MAX_FILE_BYTES // (1024 * 1024)} MB.")

    from services import document_text as _dt
    from services import draft_review as _dr
    read = _dt.extract_upload(file.filename or "file", data)
    if not (read.get("text") or "").strip():
        return {"submission_id": submission_id, "result": None,
                "extraction": {k: v for k, v in read.items() if k not in _EXTRACTION_FILE_STRIP},
                "error": read.get("error") or "Couldn't read any text from that file."}

    result = _dr.review_section(read["text"], section=section, rulebook=rulebook,
                                profile=profile, pages=read.get("pages") or None)
    return {
        "submission_id": submission_id,
        "result": result,
        # The extracted TEXT is deliberately not echoed back -- and neither is
        # `page_texts`, which `extract_upload` sets unconditionally on every
        # PDF read (this endpoint passes no `sections=`, so the ledger keys
        # never populate here, but `page_texts` still would without this).
        "extraction": {k: v for k, v in read.items() if k not in _EXTRACTION_FILE_STRIP},
    }


# ----------------------------------------------------------------------------
# Reading a solicitation's REQUIREMENTS — the deep read behind Draft Review.
#
# Deliberately its own request, not folded into /from-solicitation or
# /confirm. The read is 10-18 Gemini calls (chunk -> sweep -> verify), 60-150s,
# against a 300s Cloud Run request cap on a single-worker backend. Folding it
# into the contract call would risk a 504 that loses the contract too; folding
# it into confirm would risk one that leaves the PI unsure whether their
# proposal exists. Fired from the review step of the upload modal instead, it
# overlaps the time the PI already spends checking the extracted fields.
#
# It SAVES NOTHING. The PI confirms the list, and /confirm (new proposal) or
# PUT .../solicitation (existing one) is what writes it (golden rule 4).

def _solicitation_warnings(read_report: dict, contract: Optional[dict],
                           extraction: dict) -> list[str]:
    """Plain sentences the UI renders verbatim. Every one of them exists so a
    partial read can never be mistaken for a complete one."""
    out: list[str] = []
    blank = int((read_report or {}).get("pages_without_text") or 0)
    pages = int((read_report or {}).get("pages") or 0)
    if blank:
        out.append(
            f"{blank} of {pages} pages had no extractable text — this looks like a "
            "scan, so any requirements on those pages were not read."
        )
    if (contract or {}).get("truncated"):
        out.append(
            "This solicitation is longer than the metadata extractor reads in one "
            "pass, so the deadline and cap above may be incomplete. The requirement "
            "list below did read the whole document."
        )
    if extraction.get("hit_time_cap"):
        out.append("Reading ran out of time, so the requirement list may be incomplete.")
    if extraction.get("hit_round_cap"):
        out.append(
            "Reading stopped at its round limit while still finding new "
            "requirements, so the list may be incomplete."
        )
    dropped = int(extraction.get("dropped_unverified") or 0)
    if dropped:
        out.append(
            f"{dropped} proposed requirement(s) were dropped because they could not "
            "be quoted from the document."
        )
    if extraction.get("ai") is False:
        out.append(
            "The AI reader is unavailable, so no requirements could be read. You can "
            "still create the proposal and attach them later."
        )
    return out


def _read_solicitation_requirements(text: str, contract: Optional[dict]) -> dict:
    """Shared body: text -> requirements + merit criteria + warnings."""
    from services import solicitation_requirements as _sr
    out = _sr.extract_requirements(text)
    merit = _sr.extract_merit_criteria(text) if out.get("ai") else []
    extraction = {k: v for k, v in out.items() if k != "requirements"}
    return {
        "requirements": out["requirements"],
        "merit_criteria": merit,
        "eligibility_notes": ([contract["eligibility"]]
                              if (contract or {}).get("eligibility") else []),
        "extraction": extraction,
    }


@app.post("/api/me/solicitation-requirements")
async def read_solicitation_requirements(
    file: Optional[UploadFile] = File(None),
    url: Optional[str] = Form(None),
    source_id: Optional[str] = Form(None),
    user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Read a solicitation end to end and return its requirements FOR REVIEW.

    Not under a submission id: on the create flow the proposal does not exist
    yet. Saves no requirements — the PI confirms them (golden rule 4) — though
    it does keep the document when it is the one reading it.

    Takes a `source_id` in preference to a file or a url: the contract step has
    already read and stored this exact document, so re-uploading it would cost
    the PI a second upload and leave two rows for one solicitation.

    422 ONLY when nothing at all was readable. A partial read comes back 200
    with a warning — reporting "we could not read 4 of 34 pages" is the whole
    point, and turning that into an error would throw away the 30 pages we did
    read."""
    if not user:
        raise HTTPException(401, "Unauthorized")

    from services import solicitation_extractor as _sx

    read_report: dict = {}
    contract = None
    stored = None
    if source_id:
        stored = _proposals_service.load_solicitation_source_by_id(
            db, source_id=source_id, user_id=user["user_id"])
        if stored is None:
            raise HTTPException(404, "That stored solicitation could not be found.")
        text = stored["text"]
        read_report = {"pages": None, "pages_without_text": 0,
                       "chars": stored["chars"], "engine": "stored", "error": None}
    elif file is not None:
        data = await file.read()
        if not data:
            raise HTTPException(400, "Uploaded file is empty.")
        if len(data) > _MAX_SOLICITATION_PDF_BYTES:
            raise HTTPException(413, "PDF is larger than 25 MB.")
        read = _sx.read_pdf(data)
        text, read_report = read["text"], {k: v for k, v in read.items() if k != "text"}
    elif url:
        from services import url_fetcher
        try:
            text = url_fetcher.fetch_solicitation_text(url)
        except url_fetcher.FetchError as e:
            raise HTTPException(e.status, e.message)
        read_report = {"pages": None, "pages_without_text": 0, "chars": len(text or ""),
                       "engine": "url", "error": None}
    else:
        raise HTTPException(400, "Provide a stored source_id, a PDF file or a url.")

    if not (text or "").strip():
        raise HTTPException(
            422,
            "Couldn't read any text from that solicitation — it may be scanned or "
            "image-only. Try a text-based PDF, or create the proposal and attach "
            "the requirements later.",
        )

    # KEEP THE DOCUMENT. Written before the model runs, so a read that times out
    # or comes back empty still leaves the text stored — the whole point is that
    # the PI is never asked for the same solicitation twice, and "the extraction
    # failed" is exactly when you least want to ask them again. Skipped when the
    # text CAME from storage: that would leave two rows for one document, and
    # only the newest would ever be bound.
    if stored is None:
        source_id = _proposals_service.save_solicitation_source(
            db, user_id=user["user_id"], text=text,
            source_kind="pdf" if file is not None else "url",
            filename=(file.filename if file is not None else None), url=url)
    else:
        source_id = stored["id"]

    payload = _read_solicitation_requirements(text, contract)
    payload["read_report"] = read_report
    payload["warnings"] = _solicitation_warnings(read_report, contract,
                                                 payload["extraction"])
    # The client passes this back at confirm / attach, which binds the stored
    # document to the proposal.
    payload["source_id"] = source_id
    return payload


def _clean_requirement_rows(rows) -> list[dict]:
    """Server-side validation of a requirement list arriving from the browser.

    The client is never authoritative about what a solicitation says: a row
    without a verbatim quote has nothing behind it, and ids are recomputed here
    rather than trusted."""
    from services import solicitation_requirements as _sr
    out = []
    for raw in (rows or []):
        if not isinstance(raw, dict):
            continue
        label = " ".join(str(raw.get("label") or "").split())[:120]
        source = " ".join(str(raw.get("source") or "").split())[:300]
        if not label or not source:
            continue
        row = {
            "label": label,
            "section": raw.get("section"),
            "kind": "semantic",
            "scored": bool(raw.get("scored", True)),
            "source": source,
            "why": " ".join(str(raw.get("why") or "").split())[:300],
            "keywords": [str(k).strip().lower() for k in (raw.get("keywords") or [])
                         if str(k).strip()][:8],
        }
        row["id"] = _sr.make_id(row)
        out.append(row)
    return out


def _solicitation_payload(body: dict, extracted: dict) -> Optional[dict]:
    """Assemble what gets stored, or None when there is nothing worth storing."""
    rows = _clean_requirement_rows(body.get("requirements"))
    if not rows:
        return None
    merit = [c for c in (body.get("merit_criteria") or [])
             if isinstance(c, dict) and c.get("criterion") and c.get("asks")]
    return {
        "version": _proposals_service.SOLICITATION_PROFILE_VERSION,
        "id": extracted.get("program_id") or extracted.get("program_name") or "this solicitation",
        "title": extracted.get("program_name") or "",
        "url": body.get("url") or (body.get("source") or {}).get("url"),
        "source": body.get("source") or {},
        "contract": extracted,
        "requirements": rows,
        "merit_criteria": merit,
        "eligibility_notes": [str(n) for n in (body.get("eligibility_notes") or []) if n],
        "read_report": body.get("read_report") or {},
        "extraction": body.get("extraction") or {},
        "extracted_at": datetime.now(timezone.utc).isoformat(),
        "model": os.getenv("SOLICITATION_REQUIREMENTS_MODEL", "gemini-3.6-flash"),
    }


@app.put("/api/me/submissions/{submission_id}/solicitation")
async def attach_solicitation(
    submission_id: int,
    payload: dict,
    user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Attach a reviewed solicitation to a proposal that already exists.

    The single save path for an existing proposal (the create flow saves through
    /from-solicitation/confirm). It writes three things in one commit, and the
    second is easy to forget: the profile itself; the notes lines Draft Critic
    and the frontend's hasSolicitation() read, appended only where absent so a
    PI's own notes are never overwritten; and any missing required-attachment
    tasks."""
    if not user:
        raise HTTPException(401, "Unauthorized")
    sub = _proposals_service.get_submission(db, submission_id=submission_id, user_id=user["user_id"])
    if sub is None:
        raise HTTPException(404, "Submission not found")
    extracted = payload.get("extracted")
    if not isinstance(extracted, dict):
        raise HTTPException(400, "Missing 'extracted' dict in body.")

    stored = _solicitation_payload(payload, extracted)
    # No requirement list is not, on its own, nothing to attach. The modal's own
    # button offers "Attach without the requirement list" — for a read that
    # failed, or a PI who did not want to wait 60-150s — and refusing it here
    # discarded the DOCUMENT too, so the very next screen asked them to upload
    # the same file again. Keep whatever we were actually given; refuse only
    # when that is nothing at all.
    if stored is None and not payload.get("source_id"):
        raise HTTPException(400, "No usable requirements to attach — every row needs "
                                 "a label and a verbatim quote from the solicitation.")

    # Notes: append only what is missing, never overwrite. Two features read
    # these lines by regex, and a proposal with requirements but no notes lines
    # would leave them blind to a solicitation it demonstrably has.
    existing_notes = sub.notes or ""
    missing = [ln for ln in _proposals_service.solicitation_notes_lines(extracted)
               if ln not in existing_notes]
    if missing:
        sub.notes = ("\n".join([existing_notes.rstrip(), *missing]).strip()
                     if existing_notes.strip() else "\n".join(missing))

    if stored is not None:
        _proposals_service.save_solicitation_profile(db, sub, stored)
        # The checklist's solicitation half, one quoted task per requirement.
        # This also retires the sponsor-name guesses ("NSF requires a 2-page
        # Data Management Plan") that the real document now supersedes.
        _proposals_service.sync_solicitation_requirement_tasks(db, sub, stored)
    else:
        db.commit()          # the notes lines above, which nothing else commits
    # ALWAYS, even with a full requirement list: `required_attachments` is a
    # separate contract field, and a required attachment the requirement read
    # happened to miss is the single likeliest reason a submission is rejected
    # outright. Deduped against the tasks that now exist, so an attachment the
    # requirements DID cover does not appear twice.
    _proposals_service.sync_required_attachment_tasks(db, sub, extracted)
    if payload.get("source_id"):
        _proposals_service.bind_solicitation_source(
            db, source_id=payload["source_id"], user_id=user["user_id"],
            submission_id=sub.id)
    db.refresh(sub)
    return _submission_to_dict(sub, include_tasks=True)


@app.post("/api/me/submissions/{submission_id}/solicitation/reread")
async def reread_solicitation_requirements(
    submission_id: int,
    user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Re-read the requirements from the solicitation ALREADY STORED for this
    proposal. No upload, no URL, nothing asked of the PI.

    This is what storing the document buys. The extraction prompt improved twice
    in a single day — once taking one solicitation from 20 requirements to 43 —
    and without the stored text every existing proposal would have needed another
    upload to benefit. 409 when nothing was kept, which is every proposal whose
    solicitation was attached before the text was stored.

    Saves nothing on its own: the new list comes back for review and the PI
    confirms it through PUT .../solicitation, exactly like a fresh read
    (golden rule 4)."""
    if not user:
        raise HTTPException(401, "Unauthorized")
    sub = _proposals_service.get_submission(db, submission_id=submission_id,
                                            user_id=user["user_id"])
    if sub is None:
        raise HTTPException(404, "Submission not found")

    source = _proposals_service.load_solicitation_source(db, submission_id)
    if source is None:
        raise HTTPException(409, "No stored solicitation for this proposal — "
                                 "upload it once and it will be kept.")

    contract = None
    raw = getattr(sub, "solicitation_json", None)
    if raw:
        try:
            contract = (json.loads(raw) or {}).get("contract")
        except (ValueError, TypeError):
            contract = None

    payload = _read_solicitation_requirements(source["text"], contract)
    read_report = {"pages": None, "pages_without_text": 0,
                   "chars": source["chars"], "engine": "stored", "error": None}
    payload["read_report"] = read_report
    payload["warnings"] = _solicitation_warnings(read_report, contract,
                                                 payload["extraction"])
    payload["source_id"] = source["id"]
    payload["contract"] = contract or {}
    return payload


@app.delete("/api/me/submissions/{submission_id}/solicitation")
async def detach_solicitation(
    submission_id: int,
    user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Detach the stored solicitation. Clears the profile column only.

    The notes lines and the seeded tasks are the PI's now, and silently removing
    work they may have done against them would be worse than leaving them. The
    stored DOCUMENT is kept too, so re-attaching never costs another upload."""
    if not user:
        raise HTTPException(401, "Unauthorized")
    sub = _proposals_service.get_submission(db, submission_id=submission_id, user_id=user["user_id"])
    if sub is None:
        raise HTTPException(404, "Submission not found")
    sub.solicitation_json = None
    db.commit()
    db.refresh(sub)
    return _submission_to_dict(sub, include_tasks=True)


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