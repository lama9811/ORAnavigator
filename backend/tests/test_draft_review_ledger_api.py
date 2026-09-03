"""The ledger must reach the browser, and `page_texts` must NOT.

`extract_upload` now returns the full per-page text, plus (when it split a
combined Research.gov package) a page ledger. Echoing the page texts in the
extraction report would put the PI's entire manuscript back on the wire -- the
same reason the solicitation TEXT is kept off the submission list. The ledger
itself is meant to reach the browser, but once, at the top level of `result`
-- never duplicated inside a per-file `extraction.files` entry.
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
    withsol = Submission(user_id=uid, title="From a solicitation", sponsor="NIH",
                         status="active", solicitation_json=json.dumps(PROFILE))
    seed.add(withsol)
    seed.commit()
    withsol_id = withsol.id
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
    yield c, withsol_id
    main.app.dependency_overrides.clear()


# ── the static-code guards (fast, no model, catch a regression at the call
# site rather than only through the slower end-to-end test below) ──────────

def test_review_draft_is_called_with_the_ledger():
    import pathlib
    src = pathlib.Path("main.py").read_text()
    assert "ledger=" in src and "toc_mismatch=" in src


def test_extraction_file_strip_covers_every_ledger_and_manuscript_key():
    """One constant, used at every `extraction.files` filter site in
    `main.py`, so the four call sites can't drift out of agreement the way the
    review found they already had (the section-check upload endpoint was
    still filtering `text` alone)."""
    import pathlib
    src = pathlib.Path("main.py").read_text()
    assert "_EXTRACTION_FILE_STRIP = (" in src
    idx = src.index("_EXTRACTION_FILE_STRIP = (")
    block = src[idx:idx + 400]
    for key in ("text", "section_spans", "page_texts",
               "page_ledger", "ledger_page_counts", "ledger_toc_mismatch"):
        assert key in block, f"{key} missing from _EXTRACTION_FILE_STRIP"
    # And it must actually be USED, not just defined -- a constant nobody
    # references is the same bug as never writing it.
    assert src.count("_EXTRACTION_FILE_STRIP") >= 4, (
        "expected the constant plus at least three usages "
        "(draft-review upload x2, section-check upload x2)")


def test_page_counts_is_not_overwritten_by_ledger_attribution():
    """`page_counts_from_ledger` is ATTRIBUTION (pages the ledger could assign
    to a section by name), not a section's real page REACH -- its own
    docstring says not to feed it to a page-limit rule. `page_counts` must
    keep reading `span["pages"]` and must never be `.update()`d with
    `ledger_page_counts`."""
    import pathlib
    src = pathlib.Path("main.py").read_text()
    assert "page_counts.update" not in src
    assert 'page_counts = {k: v["pages"] for k, v in file_spans.items()' in src


# ── the end-to-end behaviour ─────────────────────────────────────────────

_FAKE_LEDGER = [
    {"page": 1, "section": "research_strategy", "source": "structure", "verified": True, "chars": 40},
    {"page": 2, "section": None, "source": "unassigned", "verified": False, "chars": 0},
]
_FAKE_MISMATCH = [{"section": "research_strategy", "label": "Research Strategy",
                   "ledger_pages": 1, "toc_pages": 3}]


def _fake_extract_upload(filename, data, *, sections=None):
    return {
        "filename": filename,
        "text": "Research Strategy\nOur specific aims are to synthesize three polymers.",
        "pages": 2, "chars": 71, "truncated": False, "error": None,
        "page_texts": ["Research Strategy\nOur specific aims are to synthesize three polymers.",
                      "some second page text nobody should ever see again"],
        "page_ledger": _FAKE_LEDGER,
        "ledger_toc_mismatch": _FAKE_MISMATCH,
        "ledger_page_counts": {"research_strategy": 1},
        "spans_are_structural": False,
    }


def test_the_ledger_reaches_the_browser_and_page_texts_never_do(ctx, monkeypatch):
    from services import document_text as _dt
    monkeypatch.setattr(_dt, "extract_upload", _fake_extract_upload)

    c, withsol_id = ctx
    r = c.post(f"/api/me/submissions/{withsol_id}/draft-review/upload",
              files=[("files", ("draft.pdf", b"%PDF-1.4 fake", "application/pdf"))])
    assert r.status_code == 200, r.text
    body = r.json()

    result = body["result"]
    assert result["page_ledger"] == _FAKE_LEDGER
    assert result["pages_unaccounted"] == [2]
    assert result["toc_mismatch"] == _FAKE_MISMATCH
    # A page could not be accounted for, so the score is withheld -- Task 7's
    # rule, exercised here through the wiring rather than assumed.
    assert result["score"] is None

    # And the manuscript itself -- whole, per-page, or as a ledger quote --
    # must never ride back inside the per-file extraction report.
    files = body["extraction"]["files"]
    assert files
    for f in files:
        for key in ("text", "section_spans", "page_texts",
                   "page_ledger", "ledger_page_counts", "ledger_toc_mismatch"):
            assert key not in f, f
    dumped = json.dumps(body)
    assert "some second page text nobody should ever see again" not in dumped


def test_a_paste_review_carries_no_ledger_and_withholds_nothing_on_its_account(ctx):
    """A pasted review has no PDF pages to account for. `ledger` stays None,
    `pages_unaccounted` stays [], and nothing here suppresses the score."""
    c, withsol_id = ctx
    r = c.post(f"/api/me/submissions/{withsol_id}/draft-review",
              json={"draft_text": "Research Strategy\nOur specific aims are "
                                  "to synthesize three polymers.\n"})
    assert r.status_code == 200, r.text
    result = r.json()["result"]
    assert result["page_ledger"] is None
    assert result["pages_unaccounted"] == []
    assert result["toc_mismatch"] == []
