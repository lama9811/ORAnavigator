"""End-to-end TestClient tests for the auth-gated profile + register endpoints.

Unlike the other suites (which call services directly), these import the FULL
FastAPI app and drive the real request → dependency → response cycle. That is
exactly what would have caught the `ProfileUpdateRequest` shadow-class bug: a
stripped local stub once shadowed the real model and silently dropped
department/title/role on PUT, yet the unit tests (which imported the model
directly) still passed. A round-trip through the live route would have failed.

These also lock in the `/api/register` domain gate (the ALLOW_TEST_EMAILS
behavior) so a future change can't silently let non-@morgan.edu signups through.

NOTE: importing `main` runs startup init that may reach the network, so run
these in a normal local environment (network available), e.g.:
    cd backend && ../.venv/bin/python -m pytest tests/test_profile_api_e2e.py -v
"""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from fastapi.testclient import TestClient

import main
import deps
from db import Base
from models import User
from security import hash_password


@pytest.fixture
def client():
    """Full app wired to a fresh shared in-memory SQLite, with one seeded
    faculty user and the auth dependency overridden to that user."""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,  # one shared in-memory DB across sessions/threads
    )
    Base.metadata.create_all(engine)
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    seed = TestingSession()
    u = User(email="pi@morgan.edu", password_hash=hash_password("password123"),
             role="user", name="Pat Investigator")
    seed.add(u)
    seed.commit()
    uid = u.id
    seed.close()

    def _override_db():
        db = TestingSession()
        try:
            yield db
        finally:
            db.close()

    # Profile routes use main.py's get_db / get_current_user;
    # /api/register (routers/auth.py) uses deps.get_db. Override all three.
    main.app.dependency_overrides[main.get_db] = _override_db
    main.app.dependency_overrides[deps.get_db] = _override_db
    main.app.dependency_overrides[main.get_current_user] = lambda: {
        "user_id": uid, "email": "pi@morgan.edu", "role": "user",
    }

    c = TestClient(main.app)
    yield c
    main.app.dependency_overrides.clear()


# --------------------------------------------------------------------------
# GET /api/profile
# --------------------------------------------------------------------------
def test_get_profile_returns_extended_fields(client):
    r = client.get("/api/profile")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["email"] == "pi@morgan.edu"
    # The extended-profile contract: these keys must always be present.
    for k in ("department", "title", "primary_role", "interests"):
        assert k in body


# --------------------------------------------------------------------------
# PUT /api/profile — the shadow-class regression test
# --------------------------------------------------------------------------
def test_put_profile_roundtrips_all_fields(client):
    """The bug: a local ProfileUpdateRequest stub had only `name`, so PUT
    silently dropped department/title/role. This PUT-then-GET round-trip is
    what catches it — if the handler binds to a stripped model, these
    assertions fail (or the PUT 500s)."""
    payload = {
        "name": "Dr. Pat Investigator",
        "department": "Computer Science",
        "title": "Associate Professor",
        "primary_role": "PI",
        "interests": "cybersecurity, health disparities",
    }
    r = client.put("/api/profile", json=payload)
    assert r.status_code == 200, r.text

    got = client.get("/api/profile").json()
    assert got["department"] == "Computer Science"
    assert got["title"] == "Associate Professor"
    assert got["primary_role"] == "PI"
    # interests are mirrored into UserMemory rows and returned comma-joined
    assert "cybersecurity" in got["interests"]
    assert "health disparities" in got["interests"]


def test_put_profile_rejects_invalid_role(client):
    """primary_role is validated against PROFILE_ROLE_ENUM at the API layer."""
    r = client.put("/api/profile", json={"primary_role": "Wizard"})
    assert r.status_code == 422


def test_put_profile_allows_clearing_role(client):
    assert client.put("/api/profile", json={"primary_role": "PI"}).status_code == 200
    assert client.put("/api/profile", json={"primary_role": ""}).status_code == 200
    assert client.get("/api/profile").json()["primary_role"] is None


# --------------------------------------------------------------------------
# Auth is actually required
# --------------------------------------------------------------------------
def test_profile_requires_auth(client):
    # Drop the auth override so the real dependency runs with no token.
    main.app.dependency_overrides.pop(main.get_current_user, None)
    r = client.get("/api/profile")
    assert r.status_code in (401, 403)


# --------------------------------------------------------------------------
# /api/register domain gate — locks the ALLOW_TEST_EMAILS behavior
# --------------------------------------------------------------------------
def test_register_rejects_non_morgan(client, monkeypatch):
    monkeypatch.delenv("ALLOW_TEST_EMAILS", raising=False)
    r = client.post("/api/register", json={
        "email": "outsider@gmail.com", "password": "password123", "name": "X"})
    assert r.status_code == 400
    assert "morgan.edu" in r.text.lower()


def test_register_rejects_test_email_when_disabled(client, monkeypatch):
    monkeypatch.setenv("ALLOW_TEST_EMAILS", "false")
    r = client.post("/api/register", json={
        "email": "bot@test.com", "password": "password123", "name": "X"})
    assert r.status_code == 400


def test_register_allows_test_email_when_enabled(client, monkeypatch):
    """Documents the toggle: with ALLOW_TEST_EMAILS=true, @test.com passes the
    domain gate and proceeds to the verify-email step (202 Accepted)."""
    monkeypatch.setenv("ALLOW_TEST_EMAILS", "true")
    r = client.post("/api/register", json={
        "email": "bot2@test.com", "password": "password123", "name": "X"})
    assert r.status_code == 202
