"""The per-section check endpoints.

Route/auth level only — the rules themselves are tested in
test_rulebook_checks.py. Mirrors the single-`ctx`-fixture harness of
tests/test_proposals_api_e2e.py: one in-memory SQLite engine per test, both
get_db dependencies overridden, get_current_user stubbed. Read that file
before changing anything here.
"""
import os
os.environ["TRUSTED_HOSTS"] = "testserver,localhost,127.0.0.1"

import json

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

FIVE_LINE = ("We propose to study trustworthy cardiac AI using multimodal "
             "physiological sensing. The work will develop new models.")


@pytest.fixture
def ctx():
    """Yields (client, submission_id, other_users_submission_id, SessionMaker).

    The submission has NO solicitation attached — which is the point: this
    endpoint must work without one, unlike draft-review."""
    engine = create_engine("sqlite://",
                           connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine)
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    seed = TestingSession()
    u = User(email="pi@morgan.edu", password_hash=hash_password("password123"),
             role="user", name="Pat Investigator")
    other = User(email="other@morgan.edu", password_hash=hash_password("x"),
                 role="user", name="Someone Else")
    seed.add_all([u, other])
    seed.commit()
    uid = u.id

    sub = Submission(user_id=uid, title="REU Site: Cardiac AI",
                     sponsor="National Science Foundation", status="active")
    theirs = Submission(user_id=other.id, title="Someone else's",
                        sponsor="NSF", status="active")
    seed.add_all([sub, theirs])
    seed.commit()
    sub_id, theirs_id = sub.id, theirs.id
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
    yield c, sub_id, theirs_id, TestingSession
    main.app.dependency_overrides.clear()


def test_the_section_list_is_offered(ctx):
    c, _, _, _ = ctx
    r = c.get("/api/me/section-check/sections")
    assert r.status_code == 200
    keys = [s["key"] for s in r.json()["sections"]]
    assert "project_summary" in keys


def test_a_paste_is_checked_without_a_solicitation(ctx):
    """The rules are NSF's, so this must NOT 409 the way draft-review does."""
    c, sub_id, _, _ = ctx
    r = c.post(f"/api/me/submissions/{sub_id}/section-check",
               json={"section": "project_summary", "text": FIVE_LINE,
                     "rulebook": "the PAPPG"})
    assert r.status_code == 200
    body = r.json()["result"]
    assert body["score"] is None
    assert any(f["id"] == "pappg_ps_headings" and f["status"] == "not_found"
               for f in body["findings"])


def test_another_users_submission_is_404(ctx):
    c, _, theirs_id, _ = ctx
    r = c.post(f"/api/me/submissions/{theirs_id}/section-check",
               json={"section": "project_summary", "text": FIVE_LINE,
                     "rulebook": "the PAPPG"})
    assert r.status_code == 404


def test_an_unknown_rulebook_is_400(ctx):
    c, sub_id, _, _ = ctx
    r = c.post(f"/api/me/submissions/{sub_id}/section-check",
               json={"section": "project_summary", "text": FIVE_LINE,
                     "rulebook": "the Hitchhiker's Guide"})
    assert r.status_code == 400


def test_an_unknown_section_is_400(ctx):
    c, sub_id, _, _ = ctx
    r = c.post(f"/api/me/submissions/{sub_id}/section-check",
               json={"section": "cover_letter", "text": FIVE_LINE,
                     "rulebook": "the PAPPG"})
    assert r.status_code == 400


def test_the_paste_is_never_persisted(ctx):
    """It is the PI's unpublished manuscript. Same rule as Draft Review."""
    c, sub_id, _, TestingSession = ctx
    c.post(f"/api/me/submissions/{sub_id}/section-check",
           json={"section": "project_summary", "text": FIVE_LINE,
                 "rulebook": "the PAPPG"})
    db = TestingSession()
    sub = db.query(Submission).filter(Submission.id == sub_id).one()
    blob = " ".join(str(getattr(sub, f, "") or "") for f in
                    ("notes", "sections_json", "draft_review_json",
                     "solicitation_json", "budget_json", "compliance_json"))
    db.close()
    assert FIVE_LINE not in blob


# ── the upload endpoint (POST .../section-check/upload) ────────────────────

_CORRUPT_PDF = b"%PDF-1.4 not really a pdf"


def test_upload_another_users_submission_is_404(ctx):
    c, _, theirs_id, _ = ctx
    r = c.post(f"/api/me/submissions/{theirs_id}/section-check/upload",
               data={"section": "project_summary", "rulebook": "the PAPPG"},
               files={"file": ("x.pdf", _CORRUPT_PDF, "application/pdf")})
    assert r.status_code == 404


def test_upload_corrupt_file_returns_a_structured_error_not_an_empty_review(ctx):
    """A file that can't be read must come back with `result: None` and a
    real `error`, not a review that goes on to claim the section is empty —
    that would tell the PI their Project Summary has no headings when the
    truth is we never read a word of it."""
    c, sub_id, _, _ = ctx
    r = c.post(f"/api/me/submissions/{sub_id}/section-check/upload",
               data={"section": "project_summary", "rulebook": "the PAPPG"},
               files={"file": ("x.pdf", _CORRUPT_PDF, "application/pdf")})
    assert r.status_code == 200
    body = r.json()
    assert body["result"] is None
    assert body["error"]
    assert isinstance(body["extraction"], dict)
    assert "text" not in body["extraction"]


def test_upload_never_echoes_the_extracted_text_back(ctx):
    """Even on a SUCCESSFUL read the extracted text must not ride back in the
    response — same rule as the paste endpoint: it's the PI's unpublished
    manuscript."""
    c, sub_id, _, _ = ctx
    r = c.post(f"/api/me/submissions/{sub_id}/section-check/upload",
               data={"section": "project_summary", "rulebook": "the PAPPG"},
               files={"file": ("x.txt", FIVE_LINE.encode(), "text/plain")})
    assert r.status_code == 200
    body = r.json()
    assert body["result"] is not None
    assert "text" not in body["extraction"]
    assert FIVE_LINE not in json.dumps(body)


def test_upload_unknown_section_is_400(ctx):
    c, sub_id, _, _ = ctx
    r = c.post(f"/api/me/submissions/{sub_id}/section-check/upload",
               data={"section": "cover_letter", "rulebook": "the PAPPG"},
               files={"file": ("x.pdf", _CORRUPT_PDF, "application/pdf")})
    assert r.status_code == 400


def test_upload_unknown_rulebook_is_400(ctx):
    c, sub_id, _, _ = ctx
    r = c.post(f"/api/me/submissions/{sub_id}/section-check/upload",
               data={"section": "project_summary",
                     "rulebook": "the Hitchhiker's Guide"},
               files={"file": ("x.pdf", _CORRUPT_PDF, "application/pdf")})
    assert r.status_code == 400
