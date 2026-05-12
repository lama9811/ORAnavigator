# backend/db.py
import os
from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from dotenv import load_dotenv

# Load environment variables from parent directory
BASE_DIR = os.path.dirname(__file__)
PROJECT_ROOT = os.path.dirname(BASE_DIR)  # Go up one level to find .env
load_dotenv(os.path.join(PROJECT_ROOT, ".env"))

DATABASE_URL = os.getenv("DATABASE_URL")

print(f"[DIR] BASE_DIR: {BASE_DIR}")
print(f"[DIR] PROJECT_ROOT: {PROJECT_ROOT}")
print(f" CONNECTING TO DATABASE: {'***' if DATABASE_URL else 'NOT SET'}")

if not DATABASE_URL:
    # Fallback to SQLite if DATABASE_URL is not set
    DATABASE_URL = "sqlite:///./cs_chatbot.db"
    print(f"[ERROR] ERROR: DATABASE_URL is missing. Using SQLite fallback: {DATABASE_URL}")
else:
    print(f"[OK] DATABASE_URL loaded successfully!")

def _make_engine(url: str):
    if "sqlite" in url:
        return create_engine(
            url,
            connect_args={"check_same_thread": False},
            pool_pre_ping=True,
            pool_recycle=3600,
        )
    if "mysql" not in url:
        return create_engine(url, pool_pre_ping=True, pool_recycle=3600)

    from urllib.parse import urlparse, parse_qs, unquote
    parsed = urlparse(url.replace("mysql+pymysql://", "mysql://"))
    user = unquote(parsed.username or "")
    password = unquote(parsed.password or "")
    database = (parsed.path or "/").lstrip("/")
    qs = parse_qs(parsed.query)
    socket_path = qs.get("unix_socket", [None])[0]

    if socket_path:
        import pymysql
        print(f"[DB] Using unix socket: {socket_path}")
        def _connect():
            return pymysql.connect(
                user=user, password=password, database=database,
                unix_socket=socket_path,
            )
        return create_engine(
            "mysql+pymysql://", creator=_connect,
            pool_pre_ping=True, pool_recycle=3600,
        )

    import ssl
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    print(f"[DB] Using TCP with SSL: {parsed.hostname}:{parsed.port}")
    return create_engine(
        url, connect_args={"ssl": ctx},
        pool_pre_ping=True, pool_recycle=3600,
    )

engine = _make_engine(DATABASE_URL)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()
