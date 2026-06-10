"""Full-app TestClient tests for the Compliance Sentinel endpoints — the
route -> dependency -> response cycle (questions, assess, save/load, add-tasks).

Self-contained env so it runs standalone (importing `main` connects to the DB
on startup; SQLite keeps that fast and offline)."""
import os
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
os.environ.setdefault("TRUSTED_HOSTS", "testserver,localhost,127.0.0.1")

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
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    seed = TestingSession()
    u = User(email="pi@morgan.edu", password_hash=hash_password("password123"),
             role="user", name="Pat Investigator")
    seed.add(u)
    seed.commit()
    uid = u.id
    sub = Submission(user_id=uid, title="NIH R01", sponsor="NIH", status="active")
    seed.add(sub)
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
    yield c, sub_id
    main.app.dependency_overrides.clear()


def test_questions_endpoint_lists_questionnaire(ctx):
    c, _ = ctx
    r = c.get("/api/compliance/questions")
    assert r.status_code == 200
    keys = {q["key"] for q in r.json()["questions"]}
    assert "human_subjects" in keys and "export_controlled" in keys


def test_assess_is_stateless(ctx):
    c, _ = ctx
    r = c.post("/api/compliance/assess",
               json={"answers": {"human_subjects": "yes"}, "sponsor": "Internal"})
    assert r.status_code == 200
    items = {i["id"]: i for i in r.json()["items"]}
    assert items["irb"]["status"] == "required"


def test_per_submission_uses_submission_sponsor(ctx):
    # The seeded submission is NIH -> RCR + COI required even with empty answers.
    c, sub_id = ctx
    r = c.get(f"/api/me/submissions/{sub_id}/compliance")
    assert r.status_code == 200
    items = {i["id"]: i for i in r.json()["result"]["items"]}
    assert items["rcr"]["status"] == "required"
    assert items["coi"]["status"] == "required"


def test_save_then_load_roundtrip(ctx):
    c, sub_id = ctx
    save = c.put(f"/api/me/submissions/{sub_id}/compliance",
                 json={"answers": {"human_subjects": "yes", "animals": "no"}})
    assert save.status_code == 200
    load = c.get(f"/api/me/submissions/{sub_id}/compliance")
    assert load.json()["answers"]["human_subjects"] == "yes"
    items = {i["id"]: i for i in load.json()["result"]["items"]}
    assert items["irb"]["status"] == "required"
    assert items["iacuc"]["status"] == "not_required"


def test_add_tasks_creates_required_only_and_is_idempotent(ctx):
    c, sub_id = ctx
    c.put(f"/api/me/submissions/{sub_id}/compliance",
          json={"answers": {"human_subjects": "yes", "animals": "no",
                            "export_controlled": "no", "foreign_collaboration": "yes"}})
    first = c.post(f"/api/me/submissions/{sub_id}/compliance/tasks", json={})
    assert first.status_code == 200
    created = first.json()["created"]
    titles = " ".join(t["title"].lower() for t in created)
    # IRB (human subjects yes), RCR + COI (NIH sponsor) become tasks...
    assert "irb" in titles
    # ...export_security is a REVIEW item (foreign-only) -> NOT a task
    assert all("export" not in t["title"].lower() for t in created)
    # every created task resolves an Open-form link (KB-index fallback)
    assert all(t["kb_doc_url"] for t in created)
    # re-running creates nothing new (idempotent on title)
    again = c.post(f"/api/me/submissions/{sub_id}/compliance/tasks", json={})
    assert again.json()["created"] == []


def test_other_users_submission_is_404(ctx):
    c, _ = ctx
    r = c.get("/api/me/submissions/99999/compliance")
    assert r.status_code == 404


def test_has_compliance_flag_on_listing(ctx):
    c, sub_id = ctx
    before = c.get("/api/me/submissions").json()["submissions"]
    assert before[0]["has_compliance"] is False
    c.put(f"/api/me/submissions/{sub_id}/compliance",
          json={"answers": {"human_subjects": "no"}})
    after = c.get("/api/me/submissions").json()["submissions"]
    assert after[0]["has_compliance"] is True
