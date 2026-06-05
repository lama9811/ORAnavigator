"""Full-app TestClient tests for the proposals 'Open form' enrichment and
the .ics calendar export. Drives the real route -> dependency -> response
cycle (the layer that catches request-model / wiring bugs)."""
import os
os.environ["TRUSTED_HOSTS"] = "testserver,localhost,127.0.0.1"

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from fastapi.testclient import TestClient

import main
import deps
from db import Base
from models import User, Submission, SubmissionTask
from security import hash_password
from services import ics_export


@pytest.fixture
def ctx():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    seed = TestingSession()
    u = User(email="pi@morgan.edu", password_hash=hash_password("password123"),
             role="user", name="Pat Investigator")
    seed.add(u)
    seed.commit()
    uid = u.id
    # A submission with a deadline + one task linked to a real form doc_id.
    from services.forms_catalog import list_forms
    real_doc_id = list_forms()[0]["doc_id"]
    sub = Submission(user_id=uid, title="NSF CAREER", sponsor="NSF",
                     deadline=datetime.now(timezone.utc) + timedelta(days=20),
                     status="active")
    seed.add(sub)
    seed.commit()
    seed.add(SubmissionTask(submission_id=sub.id, title="Internal routing form",
                            kb_doc_id=real_doc_id, status="pending", sort_order=0))
    seed.add(SubmissionTask(submission_id=sub.id, title="Biosketch",
                            kb_doc_id=None, status="pending", sort_order=1))
    seed.commit()
    sub_id = sub.id
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
    yield c, uid, sub_id, real_doc_id
    main.app.dependency_overrides.clear()


def test_task_dict_includes_resolved_form_url(ctx):
    c, uid, sub_id, real_doc_id = ctx
    r = c.get(f"/api/me/submissions/{sub_id}")
    assert r.status_code == 200
    tasks = r.json()["tasks"]
    linked = next(t for t in tasks if t["kb_doc_id"] == real_doc_id)
    assert linked["kb_doc_url"]            # truthy resolved URL
    assert linked["kb_doc_title"]
    unlinked = next(t for t in tasks if t["title"] == "Biosketch")
    assert unlinked["kb_doc_url"] is None


def test_deadlines_token_requires_auth(ctx):
    c, *_ = ctx
    main.app.dependency_overrides.pop(main.get_current_user, None)
    r = c.get("/api/me/deadlines-token")           # no Bearer
    assert r.status_code in (401, 403)


def test_deadlines_token_returns_urls(ctx):
    c, *_ = ctx
    r = c.get("/api/me/deadlines-token")
    assert r.status_code == 200
    body = r.json()
    assert "/api/me/deadlines.ics?token=" in body["ics_url"]
    assert body["webcal_url"].startswith("webcal://")


def test_ics_feed_valid_token_returns_calendar(ctx):
    c, uid, sub_id, _ = ctx
    tok = ics_export.mint_ics_token(uid)
    r = c.get(f"/api/me/deadlines.ics?token={tok}")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/calendar")
    assert "BEGIN:VCALENDAR" in r.text
    assert f"UID:submission-{sub_id}@ora.inavigator.ai" in r.text


def test_ics_feed_rejects_missing_token(ctx):
    c, *_ = ctx
    assert c.get("/api/me/deadlines.ics").status_code == 401


def test_ics_feed_rejects_bad_token(ctx):
    c, *_ = ctx
    assert c.get("/api/me/deadlines.ics?token=garbage").status_code == 401


def test_ics_token_cannot_be_replayed_as_bearer(ctx):
    c, uid, *_ = ctx
    main.app.dependency_overrides.pop(main.get_current_user, None)
    tok = ics_export.mint_ics_token(uid)
    r = c.get("/api/me/submissions", headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code in (401, 403)     # no email claim -> rejected
