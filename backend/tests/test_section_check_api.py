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


def test_a_paste_is_checked_without_a_solicitation(ctx, monkeypatch):
    """The rules are NSF's, so this must NOT 409 the way draft-review does."""
    c, sub_id, _, _ = ctx
    # The AI layer is off process-wide (conftest), and a section check whose
    # semantic half never ran now withholds its score rather than reporting the
    # deterministic remainder as a percentage. Stub the reviewer so the rows are
    # genuinely ASSESSED -- which is what makes the `by_source` split below mean
    # anything, rather than being read off a number the outage produced.
    from services import draft_review as _dr

    def _assessed(section_key, span, reqs, sections, solicitation_id, votes=1):
        return [_dr._finding(rq, "addressed", "stubbed", "", source="ai")
                for rq in reqs]

    monkeypatch.setattr(_dr, "_review_section", _assessed)

    r = c.post(f"/api/me/submissions/{sub_id}/section-check",
               json={"section": "project_summary", "text": FIVE_LINE,
                     "rulebook": "the PAPPG"})
    assert r.status_code == 200
    body = r.json()["result"]
    # scored since 2026-08-20; with no solicitation the split names the PAPPG only
    assert body["score"] is not None
    assert list(body["score"]["by_source"]) == ["the PAPPG"]
    assert any(f["id"] == "pappg_ps_headings" and f["status"] == "not_found"
               for f in body["findings"])


def test_a_paste_is_not_scored_when_the_reviewer_cannot_be_reached(ctx):
    """The same request with the AI layer down: 200, no score, and a reason.

    Not a 409 and not an error -- the deterministic rules really did run and
    their findings are worth showing. Only the NUMBER is withheld.
    """
    c, sub_id, _, _ = ctx
    r = c.post(f"/api/me/submissions/{sub_id}/section-check",
               json={"section": "project_summary", "text": FIVE_LINE,
                     "rulebook": "the PAPPG"})
    assert r.status_code == 200
    body = r.json()["result"]
    assert body["score"] is None
    assert body["message"]
    assert body["findings"], "the rule-based checks still ran and still count"


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


# ---------------------------------------------------------------------------
# THE PICKER IS DRIVEN BY THE PROPOSAL, NOT BY THE RULEBOOK ALONE (2026-08-26).
#
# `GET /api/me/section-check/sections` is auth-free and takes no submission, so
# it could only ever answer for the rulebook. Every proposal was therefore
# offered the PAPPG's seven sections whatever its own solicitation asked for,
# and `_section_check_inputs` 400'd anything the PAPPG had no rules for -- so a
# solicitation-only section was unreachable twice over.
#
# Measured on a real NSF 23-598 proposal: 8 scored rules on the Letter of
# Intent, which for that program is the first thing NSF requires, with no way
# for a PI to check it.
# ---------------------------------------------------------------------------

_LOI_PROFILE = {
    "id": "NSF 23-598",
    "title": "HBCU-EiR",
    "contract": {},
    "requirements": [
        {"id": "sol_loi_title", "section": "letter_intent",
         "section_label": "Letter of Intent",
         "label": "Include required title format in Letter of Intent",
         "kind": "semantic", "scored": True,
         "source": "The Letter of Intent title must begin 'Excellence in Research:'.",
         "why": "", "keywords": []},
        {"id": "sol_loi_pi", "section": "letter_intent",
         "section_label": "Letter of Intent",
         "label": "Include PI and Co-PI contact information in Letter of Intent",
         "kind": "semantic", "scored": True,
         "source": "The Letter of Intent must list PI and Co-PI contact information.",
         "why": "", "keywords": []},
    ],
}


@pytest.fixture
def ctx_with_solicitation(ctx):
    """The same harness, with a solicitation attached to the PI's submission."""
    c, sub_id, theirs_id, Session = ctx
    s = Session()
    sub = s.get(Submission, sub_id)
    sub.solicitation_json = json.dumps(_LOI_PROFILE)
    s.commit()
    s.close()
    return c, sub_id, theirs_id, Session


def test_the_picker_for_a_proposal_names_its_own_sections(ctx_with_solicitation):
    c, sub_id, _, _ = ctx_with_solicitation
    r = c.get(f"/api/me/submissions/{sub_id}/section-check/sections")
    assert r.status_code == 200, r.text
    sections = r.json()["sections"]
    keys = [s["key"] for s in sections]
    # Letter of Intent is WITHHELD from this picker since 2026-08-27 -- it is a
    # separate submission with an earlier deadline, not a part of the proposal.
    # Its rules stay on the profile and a full Draft Review still checks them.
    assert "letter_intent" not in keys, keys
    # and the NSF baseline is still there, silence in the solicitation
    # notwithstanding. References Cited rather than Senior/Key Personnel:
    # Check a Section takes the rulebook's BASIC rows only, and Senior/Key
    # holds none of them (see test_section_check_basics_only.py).
    assert "references_cited" in keys, keys
    assert "project_summary" in keys, keys


def test_a_solicitation_only_section_is_no_longer_rejected(ctx_with_solicitation):
    """The 400 gate asked the RULEBOOK whether the section existed.

    So Letter of Intent was refused even once the picker offered it -- the
    second of the two places that made this rulebook-only.
    """
    c, sub_id, _, _ = ctx_with_solicitation
    r = c.post(f"/api/me/submissions/{sub_id}/section-check",
               json={"section": "letter_intent",
                     "text": "Letter of Intent\n\nExcellence in Research: Sensing\n"
                             "PI: Dr. A. Rivera, arivera@morgan.edu\n",
                     "rulebook": "the PAPPG"})
    assert r.status_code == 200, r.text
    ids = {f["id"] for f in r.json()["result"]["findings"]}
    assert {"sol_loi_title", "sol_loi_pi"} <= ids, sorted(ids)


def test_a_section_neither_source_knows_is_still_400(ctx_with_solicitation):
    """Widening the gate must not open it. `cover_letter` is in neither."""
    c, sub_id, _, _ = ctx_with_solicitation
    r = c.post(f"/api/me/submissions/{sub_id}/section-check",
               json={"section": "cover_letter", "text": FIVE_LINE,
                     "rulebook": "the PAPPG"})
    assert r.status_code == 400


def test_the_picker_404s_on_another_users_proposal(ctx_with_solicitation):
    """It reads that proposal's solicitation, so it carries the same rule as
    every other per-submission route."""
    c, _, theirs_id, _ = ctx_with_solicitation
    r = c.get(f"/api/me/submissions/{theirs_id}/section-check/sections")
    assert r.status_code == 404


def test_the_auth_free_picker_offers_the_sections_the_basics_cover(ctx_with_solicitation):
    """A proposal with no solicitation, and the modal's first paint, both still
    need an answer that needs no submission.

    FOUR, not the rulebook's nine: Check a Section reviews the rulebook's BASIC
    rows plus the solicitation, and only these four sections hold any basics.
    A picker offering a section the review would then find empty is how a PI
    selects one and is told there is nothing on file for it.
    """
    c, _, _, _ = ctx_with_solicitation
    r = c.get("/api/me/section-check/sections")
    assert r.status_code == 200
    assert {s["key"] for s in r.json()["sections"]} == {
        "project_summary", "project_description", "references_cited",
        "facilities_equipment_and_other_resources"}


def test_upload_accepts_a_solicitation_only_section(ctx_with_solicitation):
    """The upload path carries its own copy of the gate, and copies drift.

    Fixing only the JSON route would leave a PI who uploads their Letter of
    Intent as a file with the 400 the paste path no longer gives them -- one
    feature answering two ways depending on how the text arrived.
    """
    c, sub_id, _, _ = ctx_with_solicitation
    body = (b"Letter of Intent\n\nExcellence in Research: Sensing\n"
            b"PI: Dr. A. Rivera, arivera@morgan.edu\n")
    r = c.post(f"/api/me/submissions/{sub_id}/section-check/upload",
               data={"section": "letter_intent", "rulebook": "the PAPPG"},
               files={"file": ("loi.txt", body, "text/plain")})
    assert r.status_code == 200, r.text
    ids = {f["id"] for f in r.json()["result"]["findings"]}
    assert {"sol_loi_title", "sol_loi_pi"} <= ids, sorted(ids)
