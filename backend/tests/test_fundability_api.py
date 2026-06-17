"""TestClient coverage for the Phase 3 endpoints (eligibility + fundability).
Gemini is OFF (conftest), so the reviewer pass returns its deterministic fallback."""
import os

os.environ.setdefault("TRUSTED_HOSTS", "testserver,localhost,127.0.0.1")
os.environ.setdefault("JWT_SECRET", "test-secret-for-fundability")

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
    TS = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    seed = TS()
    u = User(email="pi@morgan.edu", password_hash=hash_password("password123"),
             role="user", name="Pat")
    seed.add(u); seed.commit(); uid = u.id
    sub = Submission(user_id=uid, title="NSF CAREER", sponsor="NSF", status="active",
                     notes="Program ID: NSF 24-001\nEligibility: Only untenured tenure-track faculty may apply.")
    seed.add(sub); seed.commit(); sub_id = sub.id
    seed.close()

    def _override_db():
        db = TS()
        try:
            yield db
        finally:
            db.close()

    main.app.dependency_overrides[main.get_db] = _override_db
    main.app.dependency_overrides[deps.get_db] = _override_db
    main.app.dependency_overrides[main.get_current_user] = lambda: {
        "user_id": uid, "email": "pi@morgan.edu", "role": "user"}
    c = TestClient(main.app)
    yield c, sub_id
    main.app.dependency_overrides.clear()


def test_criteria_includes_nsf_and_eligibility_text(ctx):
    c, sub_id = ctx
    r = c.get(f"/api/me/submissions/{sub_id}/fundability/criteria")
    assert r.status_code == 200, r.text
    data = r.json()
    assert any(x["key"] == "broader_impacts" for x in data["criteria"])
    assert "tenure-track" in (data["eligibility_text"] or "")
    assert data["eligibility_questions"]


def test_eligibility_stop(ctx):
    c, sub_id = ctx
    r = c.post(f"/api/me/submissions/{sub_id}/eligibility",
               json={"answers": {"appointment_ok": "no", "org_eligible": "yes",
                                 "within_limits": "yes", "limited_submission": "no"}})
    assert r.status_code == 200
    assert r.json()["result"]["overall"] == "stop"


def test_fundability_review_fallback(ctx):
    c, sub_id = ctx
    r = c.post(f"/api/me/submissions/{sub_id}/fundability",
               json={"draft_text": "Our project advances knowledge and trains students."})
    assert r.status_code == 200
    result = r.json()["result"]
    assert result["criteria"] and any(x["key"] == "intellectual_merit" for x in result["criteria"])
