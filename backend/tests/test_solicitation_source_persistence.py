"""The solicitation document must be KEPT the first time it is read.

The PI hands us the document once, at the contract-extraction step. Everything
after that — the deep requirement read, a re-read when the extraction prompt
improves, Draft Review — should come off the stored copy. Asking for the same
PDF a second time is the bug these tests exist to prevent.

Before this, the text was stored only by the separate /solicitation-requirements
read, which takes 60-150s and deliberately does not block Create. A PI who
clicked Create before it finished — or whose read failed — ended up with a
proposal carrying the funder's numbers and no document at all, and was asked to
upload the very same file again later.

Gemini and the network are mocked throughout; these drive the real routes.
"""
import os

os.environ["TRUSTED_HOSTS"] = "testserver,localhost,127.0.0.1"
os.environ.setdefault("JWT_SECRET", "test-secret-for-solicitation-source")

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import deps
import main
from db import Base
from models import SolicitationSource, Submission, User
from security import hash_password

PDF_TEXT = "NSF 24-001 CAREER. Program Description. Budget cap $600,000."

CONTRACT = {
    "sponsor": "NSF", "program_id": "NSF 24-001", "program_name": "CAREER",
    "deadline": "2026-07-01", "budget_cap": 600000, "page_limits": {},
    "required_attachments": [], "eligibility": "US institutions",
    "submission_portal": "Research.gov", "source_quotes": {},
    "unverified_fields": [],
}

READ = {"text": PDF_TEXT, "pages": 12, "pages_without_text": 0,
        "chars": len(PDF_TEXT), "engine": "pdfplumber", "error": None}


@pytest.fixture
def ctx(monkeypatch):
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
    yield c, TestingSession, uid
    main.app.dependency_overrides.clear()


def _sources(Session):
    s = Session()
    try:
        return s.query(SolicitationSource).all()
    finally:
        s.close()


# ── the contract read keeps the document ────────────────────────────────────

def test_pdf_contract_read_stores_the_document_and_returns_its_id(ctx, monkeypatch):
    """Step 1 of the create flow already has the whole text in hand. Keeping it
    there is what makes every later read free."""
    c, Session, uid = ctx
    from services import solicitation_extractor as sx
    monkeypatch.setattr(sx, "read_pdf", lambda b: READ)
    monkeypatch.setattr(sx, "extract_from_text", lambda t: CONTRACT)

    r = c.post("/api/me/submissions/from-solicitation",
               files={"file": ("nsf24001.pdf", b"%PDF-1.4 fake", "application/pdf")})

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["extracted"]["program_id"] == "NSF 24-001"
    assert body["source_id"], "the contract read must return the stored document's id"

    rows = _sources(Session)
    assert len(rows) == 1
    assert rows[0].text == PDF_TEXT
    assert rows[0].source_kind == "pdf"
    assert rows[0].filename == "nsf24001.pdf"
    assert rows[0].submission_id is None  # unbound until confirm


def test_url_contract_read_stores_the_document_and_returns_its_id(ctx, monkeypatch):
    c, Session, uid = ctx
    from services import url_fetcher, solicitation_extractor as sx
    monkeypatch.setattr(url_fetcher, "fetch_solicitation_text", lambda url: PDF_TEXT)
    monkeypatch.setattr(sx, "extract_from_text", lambda t: CONTRACT)

    r = c.post("/api/me/submissions/from-solicitation/url",
               json={"url": "https://nsf.gov/pubs/nsf24001/nsf24001.htm"})

    assert r.status_code == 200, r.text
    assert r.json()["source_id"]
    rows = _sources(Session)
    assert len(rows) == 1
    assert rows[0].text == PDF_TEXT
    assert rows[0].source_kind == "url"
    assert rows[0].url == "https://nsf.gov/pubs/nsf24001/nsf24001.htm"


def test_an_unreadable_pdf_still_422s_and_stores_nothing(ctx, monkeypatch):
    """A scan has no text to keep. The 422 must survive the new save path."""
    c, Session, uid = ctx
    from services import solicitation_extractor as sx
    monkeypatch.setattr(sx, "read_pdf", lambda b: {**READ, "text": "", "chars": 0})
    monkeypatch.setattr(sx, "extract_from_text", lambda t: None)

    r = c.post("/api/me/submissions/from-solicitation",
               files={"file": ("scan.pdf", b"%PDF-1.4 fake", "application/pdf")})

    assert r.status_code == 422
    assert _sources(Session) == []


# ── the deep read reuses it instead of asking again ─────────────────────────

def test_requirements_read_accepts_a_stored_source_id_and_adds_no_second_row(ctx, monkeypatch):
    """The modal reads the same document twice — once for the contract, once for
    the requirements. The second read should come off the stored copy."""
    c, Session, uid = ctx
    seed = Session()
    row = SolicitationSource(user_id=uid, submission_id=None, text=PDF_TEXT,
                             chars=len(PDF_TEXT), source_kind="pdf",
                             filename="nsf24001.pdf", url=None, sha256="x")
    seed.add(row)
    seed.commit()
    source_id = row.id
    seed.close()

    seen = {}

    def _fake_read(text, contract):
        seen["text"] = text
        return {"requirements": [], "merit_criteria": [], "eligibility_notes": [],
                "extraction": {"rounds": 1, "dropped_unverified": 0, "ai": True}}

    monkeypatch.setattr(main, "_read_solicitation_requirements", _fake_read)

    r = c.post("/api/me/solicitation-requirements", data={"source_id": str(source_id)})

    assert r.status_code == 200, r.text
    assert seen["text"] == PDF_TEXT, "it must read the STORED text"
    assert r.json()["source_id"] == source_id
    assert len(_sources(Session)) == 1, "reusing a document must not store it twice"


def test_a_source_id_belonging_to_someone_else_is_refused(ctx, monkeypatch):
    """A source id arrives from the client and is never trusted to name a row
    this user may not read — the stored text is the PI's unpublished document."""
    c, Session, uid = ctx
    seed = Session()
    other = User(email="someone@morgan.edu", password_hash=hash_password("password123"),
                 role="user", name="Other PI")
    seed.add(other)
    seed.commit()
    row = SolicitationSource(user_id=other.id, submission_id=None, text="not yours",
                             chars=9, source_kind="pdf", filename="x.pdf",
                             url=None, sha256="y")
    seed.add(row)
    seed.commit()
    source_id = row.id
    seed.close()

    r = c.post("/api/me/solicitation-requirements", data={"source_id": str(source_id)})

    assert r.status_code in (403, 404), r.text


# ── the end-to-end promise ──────────────────────────────────────────────────

def test_confirm_binds_the_document_even_when_no_requirements_came_back(ctx, monkeypatch):
    """The case that was broken in the client: the PI clicks Create before the
    60-150s requirement read finishes. The proposal must still own the document,
    so Draft Review can offer a re-read instead of another upload."""
    c, Session, uid = ctx
    from services import solicitation_extractor as sx
    monkeypatch.setattr(sx, "read_pdf", lambda b: READ)
    monkeypatch.setattr(sx, "extract_from_text", lambda t: CONTRACT)

    step1 = c.post("/api/me/submissions/from-solicitation",
                   files={"file": ("nsf24001.pdf", b"%PDF-1.4 fake", "application/pdf")})
    assert step1.status_code == 200, step1.text
    source_id = step1.json()["source_id"]

    # No requirements in the body — exactly what the modal sends when the deep
    # read has not finished.
    created = c.post("/api/me/submissions/from-solicitation/confirm",
                     json={"extracted": CONTRACT, "source_id": source_id})
    assert created.status_code == 200, created.text
    body = created.json()

    assert body["has_solicitation_requirements"] is False
    assert body["has_solicitation_source"] is True, (
        "the document must be on file even with no requirement list")

    detail = c.get(f"/api/me/submissions/{body['id']}")
    assert detail.status_code == 200, detail.text
    assert detail.json()["has_solicitation_source"] is True


def test_attaching_without_a_requirement_list_still_keeps_the_document(ctx, monkeypatch):
    """The modal's own button offers "Attach without the requirement list". The
    backend refused it outright — and threw the document away with it, so the
    next screen asked the PI for the same file. Keep what we were given: the
    funder's numbers in notes, the document on file, no requirement list."""
    c, Session, uid = ctx
    seed = Session()
    sub = Submission(user_id=uid, title="Existing proposal", sponsor="NSF",
                     status="active")
    seed.add(sub)
    seed.commit()
    sub_id = sub.id
    row = SolicitationSource(user_id=uid, submission_id=None, text=PDF_TEXT,
                             chars=len(PDF_TEXT), source_kind="pdf",
                             filename="nsf24001.pdf", url=None, sha256="z")
    seed.add(row)
    seed.commit()
    source_id = row.id
    seed.close()

    r = c.put(f"/api/me/submissions/{sub_id}/solicitation",
              json={"extracted": CONTRACT, "source_id": source_id})

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["has_solicitation_source"] is True
    assert body["has_solicitation_requirements"] is False
    assert "600,000" in (body["notes"] or "") or "600000" in (body["notes"] or "")


def test_attaching_with_neither_requirements_nor_a_document_is_still_refused(ctx):
    """Nothing to store and nothing to keep — that stays a 400. Otherwise the
    endpoint would silently accept a call that changes nothing meaningful."""
    c, Session, uid = ctx
    seed = Session()
    sub = Submission(user_id=uid, title="Existing proposal", sponsor="NSF",
                     status="active")
    seed.add(sub)
    seed.commit()
    sub_id = sub.id
    seed.close()

    r = c.put(f"/api/me/submissions/{sub_id}/solicitation",
              json={"extracted": CONTRACT})

    assert r.status_code == 400


# ── an ABANDONED read must be offered back, not thrown away ─────────────────
#
# The document is written unbound (the proposal may not exist yet) and bound at
# save. A PI who closes the modal at the review step — or during the 60-150s
# requirement read — leaves an orphan, and every later screen then asks them to
# upload the very file we are already holding. Observed in a dev database: three
# copies of one 50,508-char solicitation, all unbound, while every proposal still
# said "this proposal needs its solicitation read in full".
#
# We do NOT auto-bind: nothing in the data says which document belongs to which
# proposal. The PI is offered it and confirms.

def _mk_source(Session, uid, **over):
    from datetime import datetime, timedelta
    s = Session()
    try:
        row = SolicitationSource(
            user_id=over.pop("user_id", uid),
            submission_id=over.pop("submission_id", None),
            text=over.pop("text", PDF_TEXT), chars=len(PDF_TEXT),
            source_kind="pdf", filename=over.pop("filename", "nsf24001.pdf"),
            url=None, sha256="x" * 64,
            created_at=over.pop("created_at", datetime.utcnow()),
        )
        s.add(row); s.commit(); s.refresh(row)
        return row.id
    finally:
        s.close()


def test_unbound_documents_are_offered_back(ctx):
    c, Session, uid = ctx
    sid = _mk_source(Session, uid, filename="abandoned.pdf")

    r = c.get("/api/me/solicitation-sources/unbound")
    assert r.status_code == 200, r.text
    rows = r.json()["sources"]
    assert [x["id"] for x in rows] == [sid]
    assert rows[0]["filename"] == "abandoned.pdf"
    assert rows[0]["chars"] == len(PDF_TEXT)


def test_the_listing_never_ships_the_document_text(ctx):
    """~300KB a row, and nothing on the picker renders it. Same reason the text
    lives in its own table instead of a column on Submission."""
    c, Session, uid = ctx
    _mk_source(Session, uid)
    rows = c.get("/api/me/solicitation-sources/unbound").json()["sources"]
    assert rows and "text" not in rows[0], rows[0]


def test_a_document_already_attached_to_a_proposal_is_not_offered(ctx):
    """It is not abandoned — it is in use, and re-offering it invites a PI to
    move one proposal's solicitation onto another."""
    c, Session, uid = ctx
    s = Session()
    sub = Submission(user_id=uid, title="P", sponsor="NSF", status="active")
    s.add(sub); s.commit(); sub_id = sub.id; s.close()
    _mk_source(Session, uid, submission_id=sub_id)

    assert c.get("/api/me/solicitation-sources/unbound").json()["sources"] == []


def test_another_users_document_is_never_offered(ctx):
    c, Session, uid = ctx
    s = Session()
    other = User(email="other@morgan.edu", password_hash=hash_password("password123"),
                 role="user", name="Other")
    s.add(other); s.commit(); other_id = other.id; s.close()
    _mk_source(Session, uid, user_id=other_id)

    assert c.get("/api/me/solicitation-sources/unbound").json()["sources"] == []


def test_a_document_past_the_reap_window_is_not_offered(ctx):
    """The reaper deletes unbound rows after a day, but only when a new one is
    written — so a stale orphan can outlive its welcome in the table. Offering
    it would resurrect a document from a session the PI has long forgotten."""
    from datetime import datetime, timedelta
    c, Session, uid = ctx
    _mk_source(Session, uid, created_at=datetime.utcnow() - timedelta(days=3))

    assert c.get("/api/me/solicitation-sources/unbound").json()["sources"] == []


def test_newest_first(ctx):
    from datetime import datetime, timedelta
    c, Session, uid = ctx
    old = _mk_source(Session, uid, created_at=datetime.utcnow() - timedelta(hours=5),
                     filename="older.pdf")
    new = _mk_source(Session, uid, filename="newer.pdf")
    rows = c.get("/api/me/solicitation-sources/unbound").json()["sources"]
    assert [x["id"] for x in rows] == [new, old]


# ── reusing one skips the upload, not the extraction ────────────────────────

def test_contract_read_accepts_a_stored_document_instead_of_a_file(ctx, monkeypatch):
    c, Session, uid = ctx
    from services import solicitation_extractor as sx
    seen = {}

    def _extract(t):
        seen["text"] = t
        return CONTRACT

    monkeypatch.setattr(sx, "extract_from_text", _extract)
    sid = _mk_source(Session, uid)

    r = c.post("/api/me/submissions/from-solicitation", data={"source_id": str(sid)})

    assert r.status_code == 200, r.text
    assert seen["text"] == PDF_TEXT, "must extract from the STORED text"
    assert r.json()["source_id"] == sid, "must echo the same row, not store a second copy"
    assert len(_sources(Session)) == 1, "reusing a document must not duplicate it"


def test_another_users_stored_document_cannot_be_reused(ctx, monkeypatch):
    c, Session, uid = ctx
    from services import solicitation_extractor as sx
    monkeypatch.setattr(sx, "extract_from_text", lambda t: CONTRACT)
    s = Session()
    other = User(email="other2@morgan.edu", password_hash=hash_password("password123"),
                 role="user", name="Other")
    s.add(other); s.commit(); other_id = other.id; s.close()
    sid = _mk_source(Session, uid, user_id=other_id)

    r = c.post("/api/me/submissions/from-solicitation", data={"source_id": str(sid)})
    assert r.status_code == 404, r.text
