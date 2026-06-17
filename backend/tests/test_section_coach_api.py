"""TestClient coverage for the Section Drafting Coach endpoints.

Drives the real route -> dependency -> response cycle. Gemini is OFF (conftest),
so the coach returns its deterministic outline/keyword-review output."""
import os

os.environ.setdefault("TRUSTED_HOSTS", "testserver,localhost,127.0.0.1")
os.environ.setdefault("JWT_SECRET", "test-secret-for-section-coach")

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from fastapi.testclient import TestClient

import main
import deps
from db import Base
from models import User, Submission
from security import hash_password


@pytest.fixture
def ctx():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine)
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    seed = TestingSession()
    u = User(email="pi@morgan.edu", password_hash=hash_password("password123"),
             role="user", name="Pat Investigator")
    seed.add(u); seed.commit(); uid = u.id
    sub = Submission(user_id=uid, title="NSF CAREER", sponsor="NSF", status="active")
    seed.add(sub); seed.commit(); sub_id = sub.id
    seed.close()

    def _override_db():
        db = TestingSession()
        try:
            yield db
        finally:
            db.close()

    main.app.dependency_overrides[main.get_db] = _override_db
    main.app.dependency_overrides[deps.get_db] = _override_db
    main.app.dependency_overrides[main.get_current_user] = lambda: {
        "user_id": uid, "email": "pi@morgan.edu", "role": "user",
    }
    c = TestClient(main.app)
    yield c, sub_id
    main.app.dependency_overrides.clear()


def test_list_sections_for_nsf(ctx):
    c, sub_id = ctx
    r = c.get(f"/api/me/submissions/{sub_id}/sections")
    assert r.status_code == 200, r.text
    keys = [s["key"] for s in r.json()["sections"]]
    assert "project_summary" in keys and "broader_impacts" in keys


def test_outline_endpoint(ctx):
    c, sub_id = ctx
    r = c.post(f"/api/me/submissions/{sub_id}/section-coach",
               json={"section_key": "project_summary", "mode": "outline"})
    assert r.status_code == 200, r.text
    result = r.json()["result"]
    assert result["mode"] == "outline" and result["outline"]


def test_review_endpoint(ctx):
    c, sub_id = ctx
    r = c.post(f"/api/me/submissions/{sub_id}/section-coach",
               json={"section_key": "project_summary", "mode": "review",
                     "draft_text": "Overview of the work. Intellectual Merit: it matters."})
    assert r.status_code == 200, r.text
    result = r.json()["result"]
    assert result["mode"] == "review" and result["checklist"]


def test_unknown_section_is_400(ctx):
    c, sub_id = ctx
    r = c.post(f"/api/me/submissions/{sub_id}/section-coach",
               json={"section_key": "bogus", "mode": "outline"})
    assert r.status_code == 400


def test_other_users_submission_is_404(ctx):
    c, sub_id = ctx
    r = c.get(f"/api/me/submissions/{sub_id + 999}/sections")
    assert r.status_code == 404
