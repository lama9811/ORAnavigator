"""Full-app TestClient tests for the solicitation-driven Draft Review.

The behavior that must never regress: a proposal with no solicitation is told to
attach one. It is NOT given a review of nothing — the engine would happily score
a draft against zero requirements and return a confident percentage meaning
nothing at all.
"""
import json
import os

os.environ["TRUSTED_HOSTS"] = "testserver,localhost,127.0.0.1"

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

PROFILE = {
    "version": 1,
    "id": "PAR-24-118",
    "title": "NIH Research Project Grant",
    "contract": {"budget_cap": 500000, "page_limits": {}, "required_attachments": []},
    "requirements": [
        {"id": "research_strategy_specific_aim", "label": "Specific aims",
         "section": "research_strategy", "kind": "semantic", "scored": True,
         "source": "State the specific aims.", "why": "", "keywords": ["aims"]},
    ],
    "merit_criteria": [],
    "eligibility_notes": [],
    "read_report": {"pages": 30, "pages_without_text": 0, "chars": 90000},
    "extraction": {"rounds": 1, "dropped_unverified": 0},
}

DRAFT = "Research Strategy\nOur specific aims are to synthesize three polymers.\n"


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
    uid = u.id
    bare = Submission(user_id=uid, title="Hand-made proposal", sponsor="NSF", status="active")
    withsol = Submission(user_id=uid, title="From a solicitation", sponsor="NIH",
                         status="active", solicitation_json=json.dumps(PROFILE))
    seed.add_all([bare, withsol])
    seed.commit()
    bare_id, withsol_id = bare.id, withsol.id
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
    yield c, bare_id, withsol_id
    main.app.dependency_overrides.clear()


# ── the review ──────────────────────────────────────────────────────────────

def test_a_proposal_without_a_solicitation_is_told_to_attach_one(ctx):
    c, bare_id, _ = ctx
    r = c.post(f"/api/me/submissions/{bare_id}/draft-review", json={"draft_text": DRAFT})
    assert r.status_code == 409
    assert "solicitation" in r.json()["detail"].lower()


def test_the_review_runs_against_the_stored_solicitation(ctx):
    c, _, withsol_id = ctx
    r = c.post(f"/api/me/submissions/{withsol_id}/draft-review", json={"draft_text": DRAFT})
    assert r.status_code == 200
    result = r.json()["result"]
    assert result["solicitation"]["id"] == "PAR-24-118"
    ids = {f["id"] for f in result["findings"]}
    # The stored semantic row, plus the deterministic cap check derived from this
    # solicitation's own contract. Nothing else: the requirement universe is
    # fixed by what was stored, and the model can never widen it.
    assert ids == {"research_strategy_specific_aim", "budget_within_cap"}


def test_the_score_is_withheld_when_the_ai_layer_is_offline(ctx):
    # conftest pins the AI layer off, so this is the offline path: findings
    # still come back, the percentage does not (it would be a verdict on our
    # availability wearing the clothes of a verdict on the draft).
    c, _, withsol_id = ctx
    r = c.post(f"/api/me/submissions/{withsol_id}/draft-review", json={"draft_text": DRAFT})
    assert r.json()["result"]["score"] is None


def test_the_upload_path_also_refuses_without_a_solicitation(ctx):
    c, bare_id, _ = ctx
    r = c.post(f"/api/me/submissions/{bare_id}/draft-review/upload",
               files=[("files", ("draft.txt", DRAFT.encode(), "text/plain"))])
    assert r.status_code == 409


def test_an_unreadable_upload_is_reported_not_scored_as_missing(ctx):
    # Reporting a failed read as absent content is the one thing this must never
    # do — it would tell a PI their whole package is missing.
    c, _, withsol_id = ctx
    r = c.post(f"/api/me/submissions/{withsol_id}/draft-review/upload",
               files=[("files", ("scan.pdf", b"%PDF-1.4 garbage", "application/pdf"))])
    assert r.status_code == 200
    body = r.json()
    assert body["result"] is None
    assert "couldn't read" in (body.get("error") or "").lower()


def test_another_users_proposal_is_not_reviewable(ctx):
    c, _, _ = ctx
    r = c.post("/api/me/submissions/99999/draft-review", json={"draft_text": DRAFT})
    assert r.status_code == 404


def test_the_eir_routes_are_gone(ctx):
    # One path into the reviewer. A surviving alias would be a second, untested one.
    c, _, withsol_id = ctx
    r = c.post(f"/api/me/submissions/{withsol_id}/eir-review", json={"draft_text": DRAFT})
    assert r.status_code == 404


# ── reading a solicitation's requirements ───────────────────────────────────

def test_reading_requirements_saves_nothing_until_it_is_confirmed(ctx, monkeypatch):
    c, bare_id, _ = ctx
    from services import solicitation_requirements as sr
    monkeypatch.setattr(sr, "extract_requirements", lambda *a, **k: {
        "requirements": [{"id": "x_aims", "label": "Specific aims", "section": "x",
                          "kind": "semantic", "scored": True, "source": "State the aims.",
                          "why": "", "keywords": []}],
        "ai": True, "rounds": 1, "chunks": 1, "chars": 100,
        "dropped_unverified": 0, "hit_round_cap": False, "hit_time_cap": False,
        "elapsed_s": 1.0})
    monkeypatch.setattr(sr, "extract_merit_criteria", lambda *a, **k: [])
    monkeypatch.setattr("services.url_fetcher.fetch_solicitation_text",
                        lambda url: "Applicants must state the aims.")

    r = c.post("/api/me/solicitation-requirements", data={"url": "https://example.org/foa"})
    assert r.status_code == 200
    assert len(r.json()["requirements"]) == 1

    # Golden rule 4: nothing is stored until the PI confirms.
    detail = c.get(f"/api/me/submissions/{bare_id}").json()
    assert detail["has_solicitation_requirements"] is False


def test_a_scanned_pdf_is_reported_as_unreadable_not_as_a_short_list(ctx):
    c, _, _ = ctx
    r = c.post("/api/me/solicitation-requirements",
               files=[("file", ("scan.pdf", b"%PDF-1.4 no text layer", "application/pdf"))])
    assert r.status_code == 422
    assert "scanned" in r.json()["detail"].lower()


def test_a_partial_read_warns_rather_than_failing(ctx, monkeypatch):
    """30 of 34 readable pages are worth keeping. Turning a partial read into an
    error would throw away the part we DID read; a warning keeps it honest."""
    c, _, _ = ctx
    from services import solicitation_extractor as sx
    from services import solicitation_requirements as sr
    monkeypatch.setattr(sx, "read_pdf", lambda data: {
        "text": "Applicants must state the aims.", "pages": 34,
        "pages_without_text": 4, "chars": 31, "engine": "pdfplumber", "error": None})
    monkeypatch.setattr(sr, "extract_requirements", lambda *a, **k: {
        "requirements": [], "ai": True, "rounds": 1, "chunks": 1, "chars": 31,
        "dropped_unverified": 0, "hit_round_cap": False, "hit_time_cap": False,
        "elapsed_s": 1.0})
    monkeypatch.setattr(sr, "extract_merit_criteria", lambda *a, **k: [])

    r = c.post("/api/me/solicitation-requirements",
               files=[("file", ("part.pdf", b"%PDF-1.4", "application/pdf"))])
    assert r.status_code == 200
    warnings = " ".join(r.json()["warnings"])
    assert "4 of 34" in warnings and "scan" in warnings


def test_requirements_with_no_source_are_never_stored(ctx):
    c, bare_id, _ = ctx
    r = c.put(f"/api/me/submissions/{bare_id}/solicitation", json={
        "extracted": {"program_id": "PAR-24-118"},
        "requirements": [{"label": "Unquoted ask", "source": ""}],
    })
    # The client is not authoritative about what the solicitation says.
    assert r.status_code == 400


# ── attaching to a proposal that already exists ─────────────────────────────

def test_attaching_writes_the_profile_the_notes_and_the_tasks(ctx):
    c, bare_id, _ = ctx
    r = c.put(f"/api/me/submissions/{bare_id}/solicitation", json={
        "extracted": {"program_id": "PAR-24-118", "budget_cap": 500000,
                      "required_attachments": ["Data Management Plan"]},
        "requirements": [{"label": "Specific aims", "section": "research_strategy",
                          "source": "State the specific aims.", "scored": True}],
        "read_report": {"pages": 30, "pages_without_text": 0},
    })
    assert r.status_code == 200
    body = r.json()
    assert body["has_solicitation_requirements"] is True
    # The notes lines Draft Critic and the frontend read by regex — without them
    # two features stay blind to a solicitation this proposal demonstrably has.
    assert "Budget cap: $500,000" in body["notes"]
    assert any("Data Management Plan" in t["title"] for t in body["tasks"])
    assert body["solicitation_summary"]["requirement_count"] == 1


def test_attaching_never_overwrites_the_pis_own_notes(ctx):
    c, bare_id, _ = ctx
    c.patch(f"/api/me/submissions/{bare_id}", json={"notes": "Call Dana about the cost share."})
    r = c.put(f"/api/me/submissions/{bare_id}/solicitation", json={
        "extracted": {"program_id": "PAR-24-118", "budget_cap": 500000},
        "requirements": [{"label": "Specific aims", "source": "State the specific aims."}],
    })
    assert "Call Dana about the cost share." in r.json()["notes"]


def test_an_attached_proposal_can_then_be_reviewed(ctx):
    c, bare_id, _ = ctx
    c.put(f"/api/me/submissions/{bare_id}/solicitation", json={
        "extracted": {"program_id": "PAR-24-118"},
        "requirements": [{"label": "Specific aims", "section": "research_strategy",
                          "source": "State the specific aims."}],
    })
    r = c.post(f"/api/me/submissions/{bare_id}/draft-review", json={"draft_text": DRAFT})
    assert r.status_code == 200
    assert r.json()["result"]["solicitation"]["id"] == "PAR-24-118"


def test_detaching_clears_the_solicitation_and_the_review_refuses_again(ctx):
    c, _, withsol_id = ctx
    r = c.delete(f"/api/me/submissions/{withsol_id}/solicitation")
    assert r.status_code == 200
    assert r.json()["has_solicitation_requirements"] is False
    assert c.post(f"/api/me/submissions/{withsol_id}/draft-review",
                  json={"draft_text": DRAFT}).status_code == 409
