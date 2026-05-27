"""Tests for the Sponsor Fit-Finder service.

The ranker is fully deterministic given the user's profile, so these
tests pin both the profile builder (DB-driven) and the scoring (pure)
without ever hitting the real KB or Gemini. A handful of integration
tests use a *minimal hand-crafted* funding-sources list so the suite
doesn't depend on the live `kb_structured/funding_sources/` tree.

Run from the backend/ directory:
    cd backend && ../.venv/bin/python -m pytest tests/test_sponsor_fit_finder.py -v
"""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from db import Base
from models import Submission, User, UserMemory
from services import sponsor_fit_finder as sff


# ---------- fixture --------------------------------------------------------

@pytest.fixture
def db():
    """Fresh SQLite in-memory DB per test."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    user = User(
        email="pi@morgan.edu", password_hash="x", role="user",
        name="Pat Investigator",
    )
    session.add(user)
    session.commit()
    session.user_id = user.id
    try:
        yield session
    finally:
        session.close()


def _mem(db, mt: str, content: str) -> None:
    """Helper: insert one UserMemory row."""
    db.add(UserMemory(user_id=db.user_id, memory_type=mt, content=content))
    db.commit()


# ---------- build_user_profile --------------------------------------------

def test_profile_empty_user_returns_safe_defaults(db):
    """A brand-new user with no memories or submissions still gets a
    valid profile -- just mostly-empty fields. The ranker handles this
    case by leaning on the HBCU/MSI default boost."""
    p = sff.build_user_profile(db, db.user_id)
    assert p["department"] is None
    assert p["role"] is None
    assert p["interests"] == []
    assert p["sponsors_seen"] == set()
    assert p["is_hbcu"] is True


def test_profile_collects_department_role_interests_sponsors(db):
    _mem(db, "department", "Computer Science")
    _mem(db, "role", "PI")
    _mem(db, "interest", "machine learning, cybersecurity")
    _mem(db, "interest", "trustworthy AI")
    _mem(db, "sponsor", "NSF")
    _mem(db, "active_grant", "NIH: R15 mechanism for AI-driven cancer screening")

    p = sff.build_user_profile(db, db.user_id)
    assert p["department"] == "Computer Science"
    assert p["role"] == "PI"
    assert len(p["interests"]) == 2
    # sponsors_seen combines `sponsor` and the prefix of `active_grant`
    assert "NSF" in p["sponsors_seen"]
    assert "NIH" in p["sponsors_seen"]


def test_profile_includes_submission_sponsor_history(db):
    """Real Submission rows are more authoritative than memory -- the
    profile should pick up their sponsor even if there's no matching
    UserMemory."""
    from datetime import datetime, timezone, timedelta
    db.add(Submission(
        user_id=db.user_id,
        title="DoD STTR Phase I",
        sponsor="DoD",
        deadline=datetime.now(timezone.utc) + timedelta(days=60),
        status="active",
    ))
    db.commit()
    p = sff.build_user_profile(db, db.user_id)
    assert "DOD" in p["sponsors_seen"]


def test_profile_skips_paused_memories(db):
    """A paused memory must NOT contribute to the profile. The user has
    asked us to ignore that fact -- ignoring it everywhere is the
    correct semantics."""
    _mem(db, "department", "Computer Science")
    # Pause that memory row directly
    db.query(UserMemory).filter(
        UserMemory.user_id == db.user_id,
        UserMemory.memory_type == "department",
    ).update({"paused": True})
    db.commit()

    p = sff.build_user_profile(db, db.user_id)
    assert p["department"] is None


def test_profile_ignores_unrelated_memory_types(db):
    """Memory types we don't care about for matching (preference, goal,
    context, etc.) should not pollute the profile dict."""
    _mem(db, "preference", "responses in plain text")
    _mem(db, "goal", "tenure by 2028")
    p = sff.build_user_profile(db, db.user_id)
    assert p["department"] is None
    assert p["role"] is None


# ---------- score_source --------------------------------------------------

def _src(doc_id, title, content, subcategory=""):
    return {
        "doc_id": doc_id,
        "title": title,
        "content": content,
        "subcategory": subcategory,
        "source_url": "https://example.org",
    }


def test_score_hbcu_msi_program_gets_hbcu_boost():
    """The Morgan-PI baseline is the HBCU/MSI boost. Any program
    targeting HBCUs or MSIs should rise to the top, even with no other
    profile signals."""
    profile = {"is_hbcu": True, "department": None, "role": None,
               "interests": [], "sponsors_seen": set()}
    src = _src("hbcu_a", "NSF HBCU-UP", "Funding for HBCU/MSI undergraduate programs.")
    r = sff.score_source(profile, src)
    assert r["score"] >= 30 + 10  # base 10 + HBCU bump 30
    assert any("HBCU" in s for s in r["matched_signals"])


def test_score_discipline_match_boosts_engineering():
    """A user in Engineering should out-score the baseline on a Defense
    Engineering source via the discipline-keyword bump."""
    profile = {"is_hbcu": False, "department": "Mechanical Engineering",
               "role": None, "interests": [], "sponsors_seen": set()}
    src = _src("defense", "DARPA",
               "Defense Advanced Research Projects Agency. Materials, aerospace, "
               "engineering. Funds basic and applied research.")
    r = sff.score_source(profile, src)
    assert r["score"] > 10  # baseline
    assert any("Engineering" in s or "engineering" in s.lower()
               for s in r["matched_signals"])


def test_score_sponsor_history_match_lifts_existing_partner_agencies():
    """A PI with NSF history should rank NSF sources above generic ones
    (all else equal)."""
    profile = {"is_hbcu": False, "department": None, "role": None,
               "interests": [], "sponsors_seen": {"NSF"}}
    nsf_src = _src("nsf_x", "NSF Engineering Directorate",
                   "NSF supports basic research across disciplines.")
    nih_src = _src("nih_x", "NIH NIGMS SuRE",
                   "Supports research at institutions with limited NIH funding.")
    a = sff.score_source(profile, nsf_src)
    b = sff.score_source(profile, nih_src)
    assert a["score"] > b["score"]
    assert any("Sponsor history" in s for s in a["matched_signals"])


def test_score_interest_match_increases_relevance():
    """Explicit interest content should boost a source whose text
    overlaps."""
    profile = {"is_hbcu": False, "department": None, "role": None,
               "interests": ["climate change adaptation"],
               "sponsors_seen": set()}
    src = _src(
        "noaa", "NOAA",
        "Funds research in climate, atmospheric, and oceanographic sciences."
    )
    r = sff.score_source(profile, src)
    assert any("research interests" in s for s in r["matched_signals"])
    assert r["score"] > 10


def test_score_student_program_dings_pi_role():
    """A student-internship program should de-rank for a PI so they see
    real research grants first. Not a hard exclusion -- just a nudge."""
    profile_pi = {"is_hbcu": False, "department": None, "role": "PI",
                  "interests": [], "sponsors_seen": set()}
    profile_student = {"is_hbcu": False, "department": None,
                       "role": "graduate student",
                       "interests": [], "sponsors_seen": set()}
    src = _src("internship", "NSF Undergraduate Internship",
               "10-week summer internship for undergraduate students.")
    r_pi = sff.score_source(profile_pi, src)
    r_st = sff.score_source(profile_student, src)
    assert r_st["score"] > r_pi["score"]


def test_score_never_returns_negative():
    """Defensive: even if every de-rank applies, the score floor is 0
    so the UI never has to handle negative numbers."""
    # PI on a heavily student-flavored program with nothing else
    profile = {"is_hbcu": False, "department": None, "role": "PI",
               "interests": [], "sponsors_seen": set()}
    src = _src("intern", "Undergrad Internship",
               "undergraduate undergraduate undergraduate internship internship")
    r = sff.score_source(profile, src)
    assert r["score"] >= 0


# ---------- rank_matches (ordering) --------------------------------------

def test_rank_orders_highest_score_first():
    profile = {"is_hbcu": True, "department": "Computer Science",
               "role": "PI", "interests": ["AI", "cybersecurity"],
               "sponsors_seen": {"NSF"}}
    sources = [
        _src("a_low",  "State of Maryland Arts Council",
             "Local arts grants for community organizations."),
        _src("b_high", "NSF HBCU-UP Computer Science Track",
             "NSF program for HBCU faculty in computing and cybersecurity. "
             "Materials, AI, machine learning, software."),
        _src("c_med",  "NSF Engineering Directorate",
             "NSF engineering research."),
    ]
    out = sff.rank_matches(profile, sources, limit=10)
    ids = [r["doc_id"] for r in out]
    # Highest should be the HBCU-UP CS program (HBCU + dept + sponsor + interest)
    assert ids[0] == "b_high"
    # The arts council should be last among the three
    assert ids[-1] == "a_low"


def test_rank_tie_break_is_deterministic_by_doc_id():
    """Identical scores must resolve to a deterministic order so tests
    and UIs don't flicker between runs."""
    profile = {"is_hbcu": False, "department": None, "role": None,
               "interests": [], "sponsors_seen": set()}
    sources = [
        _src("zzz_id", "Generic A", "no signals here at all"),
        _src("aaa_id", "Generic B", "also nothing"),
        _src("mmm_id", "Generic C", "still nothing"),
    ]
    out = sff.rank_matches(profile, sources, limit=10)
    # All score == baseline 10; ties resolved by doc_id ascending.
    ids = [r["doc_id"] for r in out]
    assert ids == ["aaa_id", "mmm_id", "zzz_id"]


def test_rank_respects_limit():
    profile = {"is_hbcu": False, "department": None, "role": None,
               "interests": [], "sponsors_seen": set()}
    sources = [_src(f"src_{i}", f"Src {i}", "x") for i in range(20)]
    out = sff.rank_matches(profile, sources, limit=5)
    assert len(out) == 5


# ---------- explanation (template + LLM fallback) ------------------------

def test_explanation_template_when_no_signals():
    """If nothing matched, the template still produces something
    coherent rather than an empty string."""
    profile = {"is_hbcu": True, "department": None, "role": None,
               "interests": [], "sponsors_seen": set()}
    ranked = {"doc_id": "x", "score": 10, "matched_signals": [],
              "source": {"title": "Some Program",
                         "content": "...", "source_url": "..."}}
    text = sff.explain_match(profile, ranked, use_llm=False)
    assert "Some Program" in text
    assert len(text) > 0


def test_explanation_template_uses_signals_when_present():
    profile = {"is_hbcu": True, "department": "Computer Science", "role": "PI",
               "interests": [], "sponsors_seen": {"NSF"}}
    ranked = {
        "doc_id": "x", "score": 50,
        "matched_signals": [
            "HBCU/MSI eligibility (Morgan is an HBCU)",
            "Sponsor history match: NSF",
        ],
        "source": {"title": "NSF HBCU-UP", "content": "...", "source_url": ""},
    }
    text = sff.explain_match(profile, ranked, use_llm=False)
    assert "HBCU" in text or "Sponsor" in text.lower() or "fits because" in text.lower()


def test_explanation_falls_back_when_llm_returns_empty(monkeypatch):
    """If Gemini returns empty text (rate-limit / content-filter / etc.)
    the template fallback fires so the UI never shows a blank line."""
    profile = {"is_hbcu": True, "department": None, "role": None,
               "interests": [], "sponsors_seen": set()}
    ranked = {"doc_id": "x", "score": 40,
              "matched_signals": ["HBCU/MSI eligibility (Morgan is an HBCU)"],
              "source": {"title": "Some Program", "content": "x"}}

    class _FakeClient:
        class _FakeModels:
            def generate_content(self, **kw):
                class _R: text = ""
                return _R()
        models = _FakeModels()

    monkeypatch.setattr(sff, "_get_gemini_client", lambda: _FakeClient())
    text = sff.explain_match(profile, ranked, use_llm=True)
    assert text  # non-empty
    assert "Some Program" in text


# ---------- find_matches (end-to-end orchestration) ----------------------

def test_find_matches_with_no_kb_dir_returns_empty(monkeypatch, db):
    """When the KB dir is missing, we degrade gracefully -- no crash,
    just an empty match list. (Production never hits this, but the
    test guarantees the error mode is friendly.)"""
    monkeypatch.setattr(sff, "load_funding_sources", lambda: [])
    out = sff.find_matches(db, db.user_id, limit=10, explain=False)
    assert out["matches"] == []
    assert out["total_sources_scanned"] == 0


def test_find_matches_returns_ui_payload(monkeypatch, db):
    """End-to-end shape check on what the API endpoint will hand back to
    the React UI. Uses a hand-crafted sources list + explain=False so no
    LLM hits."""
    _mem(db, "department", "Computer Science")
    _mem(db, "interest", "AI safety")
    sources = [
        _src("good", "NSF HBCU-UP Computer Science",
             "Funds HBCU computing research. NSF program for AI, "
             "machine learning, software, and cybersecurity."),
        _src("meh", "Maryland Arts Council Grant",
             "Local arts grants."),
    ]
    monkeypatch.setattr(sff, "load_funding_sources", lambda: sources)
    out = sff.find_matches(db, db.user_id, limit=5, explain=False)
    assert out["total_sources_scanned"] == 2
    assert len(out["matches"]) == 2
    assert out["matches"][0]["doc_id"] == "good"
    # All UI-required fields present
    first = out["matches"][0]
    for k in ("doc_id", "title", "source_url", "score",
              "matched_signals", "explanation", "content_excerpt"):
        assert k in first, f"missing UI key: {k}"
