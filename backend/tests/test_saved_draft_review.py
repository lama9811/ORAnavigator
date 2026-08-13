"""Keeping a Draft Review, so the PI can reopen it instead of re-running.

Draft Review is stateless BY DESIGN: the paste is an unpublished manuscript and
storing a copy nobody asked for would be wrong. Saving is therefore an EXPLICIT
act, never automatic, and what it stores is the RESULT — which does carry
evidence quotes lifted from the draft. The draft text itself is still never
stored, and the PI can delete the saved copy without deleting the proposal.

One saved review per proposal: the most recent. A history would need its own
table, and nobody has asked to compare three runs.
"""
import os

os.environ["TRUSTED_HOSTS"] = "testserver,localhost,127.0.0.1"
os.environ.setdefault("JWT_SECRET", "test-secret-for-saved-review")

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import deps
import main
from db import Base
from models import Submission, User
from security import hash_password

RESULT = {
    "solicitation": {"id": "NSF 23-598", "title": "HBCU-EiR", "url": ""},
    "score": {"percent": 71, "band": "amber", "assessed": 24, "earned": 17.0,
              "counts": {"addressed": 12}, "basis": "71% of the 24 ..."},
    "findings": [{"id": "a", "label": "Data Management Plan included",
                  "section": None, "kind": "deterministic", "scored": True,
                  "prohibition": False, "status": "addressed", "note": "Found it.",
                  "evidence": "Data Management Plan", "solicitation_says": "A DMP is required.",
                  "why": "", "source": "check", "delegated_to": None}],
    "sections_located": [{"key": "project_summary", "label": "Project Summary",
                          "heading": "Project Summary", "word_count": 243}],
    "sections_missing": [], "reviewer_notes": [], "eligibility_notes": [],
    "mistakes": [], "delegated": [], "word_count": 1200, "ai": True, "message": None,
}


@pytest.fixture
def ctx():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine)
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    seed = TestingSession()
    u = User(email="pi@morgan.edu", password_hash=hash_password("password123"),
             role="user", name="Pat Investigator")
    seed.add(u)
    seed.commit()
    sub = Submission(user_id=u.id, title="Adaptive Polymers", sponsor="NSF",
                     status="active")
    seed.add(sub)
    seed.commit()
    uid, sid = u.id, sub.id
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
    yield c, TestingSession, uid, sid
    main.app.dependency_overrides.clear()


# ── save / load / delete ────────────────────────────────────────────────────

def test_a_saved_review_comes_back(ctx):
    c, Session, uid, sid = ctx
    r = c.post(f"/api/me/submissions/{sid}/draft-review/save", json={"result": RESULT})
    assert r.status_code == 200
    assert r.json()["saved_at"]

    got = c.get(f"/api/me/submissions/{sid}/draft-review/saved")
    assert got.status_code == 200
    body = got.json()
    assert body["result"]["score"]["percent"] == 71
    assert body["result"]["findings"][0]["label"] == "Data Management Plan included"
    assert body["saved_at"] == r.json()["saved_at"]


def test_nothing_saved_is_a_404_not_an_empty_review(ctx):
    """An empty review would render as a real one scored 0 — the same class of
    lie as a percentage computed with the AI layer down."""
    c, Session, uid, sid = ctx
    assert c.get(f"/api/me/submissions/{sid}/draft-review/saved").status_code == 404


def test_saving_again_replaces_the_previous_one(ctx):
    c, Session, uid, sid = ctx
    c.post(f"/api/me/submissions/{sid}/draft-review/save", json={"result": RESULT})
    newer = {**RESULT, "score": {**RESULT["score"], "percent": 88}}
    c.post(f"/api/me/submissions/{sid}/draft-review/save", json={"result": newer})
    assert c.get(f"/api/me/submissions/{sid}/draft-review/saved") \
        .json()["result"]["score"]["percent"] == 88


def test_the_PI_can_delete_what_they_saved(ctx):
    """They stored quotes from their own unpublished manuscript. Taking that
    back must not mean deleting the proposal."""
    c, Session, uid, sid = ctx
    c.post(f"/api/me/submissions/{sid}/draft-review/save", json={"result": RESULT})
    assert c.delete(f"/api/me/submissions/{sid}/draft-review/saved").status_code == 200
    assert c.get(f"/api/me/submissions/{sid}/draft-review/saved").status_code == 404

    s = Session()
    try:
        assert s.query(Submission).filter_by(id=sid).first() is not None
    finally:
        s.close()


def test_an_empty_body_is_refused(ctx):
    """Saving nothing would overwrite a good review with a blank one."""
    c, Session, uid, sid = ctx
    assert c.post(f"/api/me/submissions/{sid}/draft-review/save",
                  json={"result": {}}).status_code == 400


# ── the listing carries the timestamp, never the review ─────────────────────

def test_the_submission_list_reports_WHEN_but_not_the_review_itself(ctx):
    """`list_submissions` loads whole rows. Shipping a 40KB review with every
    proposal is the mistake that kept the solicitation TEXT off this table."""
    c, Session, uid, sid = ctx
    c.post(f"/api/me/submissions/{sid}/draft-review/save", json={"result": RESULT})

    rows = c.get("/api/me/submissions").json()
    row = next(r for r in (rows if isinstance(rows, list) else rows["submissions"])
               if r["id"] == sid)
    assert row["draft_review_saved_at"], "the PI should see there IS a saved review"
    assert "findings" not in str(row), "the review body must not ride on the list"


def test_no_saved_review_reports_no_timestamp(ctx):
    c, Session, uid, sid = ctx
    rows = c.get("/api/me/submissions").json()
    row = next(r for r in (rows if isinstance(rows, list) else rows["submissions"])
               if r["id"] == sid)
    assert row["draft_review_saved_at"] is None


# ── isolation ───────────────────────────────────────────────────────────────

def test_another_users_proposal_is_not_reachable(ctx):
    c, Session, uid, sid = ctx
    s = Session()
    try:
        other = User(email="other@morgan.edu", password_hash=hash_password("x"),
                     role="user", name="Other")
        s.add(other)
        s.commit()
        theirs = Submission(user_id=other.id, title="Theirs", sponsor="NIH",
                            status="active")
        s.add(theirs)
        s.commit()
        other_sid = theirs.id
    finally:
        s.close()

    assert c.post(f"/api/me/submissions/{other_sid}/draft-review/save",
                  json={"result": RESULT}).status_code == 404
    assert c.get(f"/api/me/submissions/{other_sid}/draft-review/saved").status_code == 404
