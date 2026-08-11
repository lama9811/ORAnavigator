"""Tests for the KB scraper's pure logic — URL scoping, fingerprinting, the
readable/unreadable distinction, and the AI adjudicator's grounding rule.

The crawler and the Cloud Run Job are not covered here (they need a browser and
GCP); everything that decides whether a document gets OVERWRITTEN is.
"""

import importlib.util
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent.parent
_SCRAPER = _ROOT / "kb_scraper"
sys.path.insert(0, str(_SCRAPER))


def _load(name):
    spec = importlib.util.spec_from_file_location(name, _SCRAPER / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


fp = _load("fingerprint")
adj = _load("adjudicator")
run = _load("run")


# ---------------------------------------------------------------------------
# URL normalization — wrong here means pages counted twice or missed entirely
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    ("https://www.morgan.edu/ora/", "https://www.morgan.edu/ora"),
    ("https://WWW.Morgan.EDU/ORA", "https://www.morgan.edu/ORA"),
    ("https://www.morgan.edu/ora#main", "https://www.morgan.edu/ora"),
    ("https://www.morgan.edu/ora?utm_source=x&utm_medium=y", "https://www.morgan.edu/ora"),
    ("https://www.morgan.edu/ora?page=2", "https://www.morgan.edu/ora?page=2"),
])
def test_url_normalization(raw, expected):
    assert fp.normalize_url(raw) == expected


def test_relative_urls_resolve_against_the_page():
    assert fp.normalize_url(
        "/office-of-research-administration/pre-award",
        "https://www.morgan.edu/office-of-research-administration",
    ) == "https://www.morgan.edu/office-of-research-administration/pre-award"


def test_trailing_slash_is_not_a_separate_page():
    """Otherwise every page reports its twin as new, forever."""
    a = fp.normalize_url("https://www.morgan.edu/office-of-research-administration/pre-award")
    b = fp.normalize_url("https://www.morgan.edu/office-of-research-administration/pre-award/")
    assert a == b


# ---------------------------------------------------------------------------
# Scope — ORA section only
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("url", [
    "https://www.morgan.edu/office-of-research-administration",
    "https://www.morgan.edu/office-of-research-administration/pre-award/budget-development",
    "https://www.morgan.edu/ora",
])
def test_ora_pages_are_in_scope(url):
    assert fp.is_in_scope(url)


@pytest.mark.parametrize("url", [
    "https://www.morgan.edu/admissions",                      # different section
    "https://www.google.com/office-of-research-administration",  # different host
    "https://www.morgan.edu/office-of-research-administration/logo.png",
    "https://www.morgan.edu/office-of-research-administration/style.css",
    "mailto:ask.ora@morgan.edu",
    "",
])
def test_out_of_scope_urls_are_rejected(url):
    assert not fp.is_in_scope(url)


@pytest.mark.parametrize("url", [
    "https://www.morgan.edu/spark",
    "https://www.morgan.edu/spark/",
    "https://www.morgan.edu/SPARK",
])
def test_ora_vanity_urls_are_in_scope(url):
    """SPARK is ORA's flagship training program and the Trainings page links it
    ONLY as `/spark`. A bare prefix test never reaches it, so the page was
    never crawled and had no KB document at all — invisible rather than
    broken."""
    assert fp.is_in_scope(url)


@pytest.mark.parametrize("url", [
    "https://www.morgan.edu/sparkle",
    "https://www.morgan.edu/sparkling-water",
    "https://www.morgan.edu/orange",
])
def test_alias_matching_does_not_swallow_similarly_named_sections(url):
    """The alias must match a path SEGMENT, not a prefix — otherwise `/spark`
    drags in every page whose path merely starts with those letters."""
    assert not fp.is_in_scope(url)


# ---------------------------------------------------------------------------
# Fingerprinting
# ---------------------------------------------------------------------------

def test_whitespace_differences_are_not_a_change():
    """A reflowed paragraph is the same page. Same principle as the evidence
    check in services/text_match.quote_in."""
    assert fp.fingerprint("The F&A rate\n\nis 54%.") == fp.fingerprint("The F&A rate is 54%.")


def test_a_changed_rate_is_a_change():
    assert fp.fingerprint("The F&A rate is 54%.") != fp.fingerprint("The F&A rate is 55%.")


def test_fingerprint_is_stable_across_calls():
    text = "Morgan State University Office of Research Administration"
    assert fp.fingerprint(text) == fp.fingerprint(text)


# ---------------------------------------------------------------------------
# Unreadable vs changed — the core safety distinction
# ---------------------------------------------------------------------------

def test_non_200_is_unreadable_not_changed():
    assert fp.looks_unreadable("some text " * 50, status=404)


def test_empty_render_is_unreadable():
    """A failed JS render returns almost nothing; treating that as 'the content
    was deleted' would wipe a good document."""
    assert fp.looks_unreadable("", status=200)
    assert fp.looks_unreadable("Loading...", status=200)


def test_404_body_served_with_200_is_still_unreadable():
    body = "Page Not Found. The page you requested could not be found. " * 10
    assert fp.looks_unreadable(body, status=200)


def test_a_real_page_is_readable():
    body = (
        "Pre-Award services support faculty in developing proposals. The facilities "
        "and administrative rate for on-campus research is 54 percent of modified "
        "total direct costs. Contact the Office of Research Administration for help "
        "preparing your budget and routing your internal form before the deadline."
    )
    assert not fp.looks_unreadable(body, status=200)


# ---------------------------------------------------------------------------
# Adjudicator grounding — an AI verdict must be quotable or it does not apply
# ---------------------------------------------------------------------------

PAGE = (
    "Facilities and Administrative Cost Rates. The off-campus rate is 27% of "
    "modified total direct costs, effective July 1 2026."
)


def _verdict(**kw):
    base = dict(material=True, what_changed="rate changed", new_content="new body",
                quote="", confidence="high", grounded=False)
    base.update(kw)
    return adj.Verdict(**base)


def test_quote_matching_collapses_whitespace():
    assert adj._quote_in("the off-campus rate is 27%", "the  off-campus\n\nrate is 27%")


def test_quote_matching_rejects_text_not_on_the_page():
    assert not adj._quote_in("the off-campus rate is 99%", PAGE)


def test_trivially_short_quotes_are_rejected():
    """A two-word 'quote' matches almost any page and proves nothing."""
    assert not adj._quote_in("rate", PAGE)


def test_grounded_material_high_confidence_applies():
    assert _verdict(grounded=True, confidence="high").applicable


def test_ungrounded_verdict_never_applies():
    assert not _verdict(grounded=False, confidence="high").applicable


def test_low_confidence_never_applies():
    assert not _verdict(grounded=True, confidence="low").applicable


def test_cosmetic_change_never_applies():
    assert not _verdict(material=False, grounded=True, confidence="high").applicable


def test_empty_draft_never_applies():
    """Nothing to write is not a reason to write nothing over a real document."""
    assert not _verdict(grounded=True, confidence="high", new_content="   ").applicable


def test_broken_ai_response_falls_back_to_review_not_to_writing(monkeypatch):
    """Golden rule 3: when the model is unavailable we report, we don't guess."""
    monkeypatch.setattr(adj, "_parse", lambda raw: None)
    verdict = adj.adjudicate(PAGE, "stored content", "F&A Cost Rates")
    assert not verdict.applicable
    assert verdict.confidence == "low"


def test_empty_page_text_is_never_adjudicated():
    verdict = adj.adjudicate("", "stored content", "F&A")
    assert not verdict.applicable


# ---------------------------------------------------------------------------
# JSON parsing tolerance
# ---------------------------------------------------------------------------

def test_parses_a_fenced_json_block():
    assert adj._parse('```json\n{"material": true}\n```') == {"material": True}


def test_parses_json_embedded_in_prose():
    assert adj._parse('Here you go: {"material": false} — hope that helps') == {"material": False}


def test_unparseable_response_returns_none():
    assert adj._parse("I could not complete that request.") is None


def test_diff_summary_reports_direction_of_change():
    summary = fp.summarize_diff("The rate is 54%.", "The rate is 55%.")
    assert "54" in summary and "55" in summary


# ---------------------------------------------------------------------------
# Fingerprints are engine-specific — a hash of Gemini's markdown extraction and
# a hash of Playwright's inner_text() differ for the SAME unchanged page, so a
# row must record who wrote it or the first run after a switch reports every
# page as changed.
# ---------------------------------------------------------------------------

@pytest.fixture()
def db_session():
    """In-memory SQLite carrying the real model definitions."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from models import Base

    eng = create_engine("sqlite://")
    Base.metadata.create_all(bind=eng)
    s = sessionmaker(bind=eng)()
    try:
        yield s
    finally:
        s.close()


def test_fingerprint_rows_record_their_engine(db_session):
    from models import KbPageFingerprint

    db_session.add(KbPageFingerprint(
        url="https://www.morgan.edu/ora", fingerprint="a" * 64, engine="playwright"
    ))
    db_session.commit()

    row = db_session.query(KbPageFingerprint).one()
    assert row.engine == "playwright"


def test_engine_is_nullable_for_rows_written_before_the_migration(db_session):
    from models import KbPageFingerprint

    db_session.add(KbPageFingerprint(url="https://www.morgan.edu/ora", fingerprint="b" * 64))
    db_session.commit()

    assert db_session.query(KbPageFingerprint).one().engine is None


# ---------------------------------------------------------------------------
# Engine selection. Playwright is the default because the Gemini engine cannot
# read 26 of 59 ORA URLs (RECITATION blocks the entire compliance core).
# ---------------------------------------------------------------------------

def test_playwright_is_the_default_engine(monkeypatch):
    monkeypatch.delenv("SCRAPE_ENGINE", raising=False)
    args = run.build_parser().parse_args([])
    assert args.engine == "playwright"


def test_scrape_engine_env_var_overrides_the_default(monkeypatch):
    monkeypatch.setenv("SCRAPE_ENGINE", "gemini")
    args = run.build_parser().parse_args([])
    assert args.engine == "gemini"


def test_engine_flag_beats_the_env_var(monkeypatch):
    monkeypatch.setenv("SCRAPE_ENGINE", "gemini")
    args = run.build_parser().parse_args(["--engine=playwright"])
    assert args.engine == "playwright"


# --- the forced-audit rule -------------------------------------------------

def test_gemini_forces_audit_because_its_text_is_not_byte_stable():
    assert run.resolve_audit("gemini", False) is True


def test_playwright_does_not_force_audit():
    assert run.resolve_audit("playwright", False) is False


def test_explicit_audit_flag_is_honoured_on_playwright():
    assert run.resolve_audit("playwright", True) is True


# ---------------------------------------------------------------------------
# Baseline reads and fingerprint writes are scoped to the engine that ran, so
# switching engines re-baselines silently instead of reporting every page as
# changed — and instead of reporting every old URL as removed from the site.
# ---------------------------------------------------------------------------

def _fp(session, url, digest, engine):
    from models import KbPageFingerprint
    session.add(KbPageFingerprint(url=url, fingerprint=digest, engine=engine))
    session.commit()


def test_baseline_ignores_rows_written_by_another_engine(db_session):
    _fp(db_session, "https://www.morgan.edu/ora", "g" * 64, "gemini")

    assert run.load_baseline(db_session, "playwright") == {}


def test_baseline_returns_rows_written_by_this_engine(db_session):
    _fp(db_session, "https://www.morgan.edu/ora", "p" * 64, "playwright")

    assert run.load_baseline(db_session, "playwright") == {
        "https://www.morgan.edu/ora": "p" * 64
    }


def test_baseline_ignores_pre_migration_rows_with_no_engine(db_session):
    _fp(db_session, "https://www.morgan.edu/ora", "n" * 64, None)

    assert run.load_baseline(db_session, "playwright") == {}


def test_a_gemini_era_url_is_not_reported_as_removed_from_the_site(db_session):
    """The removed-page sweep is `prior` minus the URLs seen this run. Scoping
    `prior` by engine is what stops a switch from proposing that every page was
    deleted."""
    _fp(db_session, "https://www.morgan.edu/ora", "g" * 64, "gemini")

    prior = run.load_baseline(db_session, "playwright")
    seen = set()  # nothing crawled yet
    assert [u for u in prior if u not in seen] == []


def test_upsert_updates_a_row_written_by_the_other_engine(db_session):
    """url is unique=True, so a bare INSERT would raise IntegrityError here."""
    from models import KbPageFingerprint

    _fp(db_session, "https://www.morgan.edu/ora", "g" * 64, "gemini")

    run.upsert_fingerprint(
        db_session, url="https://www.morgan.edu/ora", digest="p" * 64,
        engine="playwright", title="ORA", doc_ids=["about_ora"],
        char_count=2128, changed=False,
    )
    db_session.commit()

    rows = db_session.query(KbPageFingerprint).all()
    assert len(rows) == 1
    assert rows[0].fingerprint == "p" * 64
    assert rows[0].engine == "playwright"
    assert rows[0].char_count == 2128


def test_upsert_inserts_when_the_page_is_new(db_session):
    from models import KbPageFingerprint

    run.upsert_fingerprint(
        db_session, url="https://www.morgan.edu/ora/new", digest="p" * 64,
        engine="playwright", title="New", doc_ids=[], char_count=10, changed=False,
    )
    db_session.commit()

    assert db_session.query(KbPageFingerprint).count() == 1


def test_upsert_only_stamps_last_changed_when_the_page_changed(db_session):
    from models import KbPageFingerprint

    run.upsert_fingerprint(
        db_session, url="https://www.morgan.edu/ora", digest="p" * 64,
        engine="playwright", title="ORA", doc_ids=[], char_count=1, changed=False,
    )
    db_session.commit()
    assert db_session.query(KbPageFingerprint).one().last_changed_at is None

    run.upsert_fingerprint(
        db_session, url="https://www.morgan.edu/ora", digest="q" * 64,
        engine="playwright", title="ORA", doc_ids=[], char_count=1, changed=True,
    )
    db_session.commit()
    assert db_session.query(KbPageFingerprint).one().last_changed_at is not None


# ---------------------------------------------------------------------------
# Document links — is_in_scope rejects /Documents/..., so these were being
# discarded at the moment they were found. Collecting them is the only way a
# file with no KB document is ever discovered.
# ---------------------------------------------------------------------------

def test_page_result_defaults_to_no_file_links():
    crawler = _load("crawler")
    r = crawler.PageResult(url="https://www.morgan.edu/ora")
    assert r.file_links == []


def test_collect_file_links_keeps_our_documents_and_forms_only():
    crawler = _load("crawler")
    raw = [
        "/Documents/ADMINISTRATION/OFFICES/ora/PI/Handbook5.pdf",
        "/Documents/ADMINISTRATION/OFFICES/ora/Templates/x.docx",
        "https://na2.docusign.net/Member/PowerFormSigning.aspx?PowerFormId=abc",
        "/office-of-research-administration/pre-award",
        "https://example.com/other.pdf",
        "/Images/Shared/logo.png",
    ]
    out = crawler._collect_file_links(raw, "https://www.morgan.edu/ora")
    assert out == [
        "https://www.morgan.edu/Documents/ADMINISTRATION/OFFICES/ora/PI/Handbook5.pdf",
        "https://www.morgan.edu/Documents/ADMINISTRATION/OFFICES/ora/Templates/x.docx",
        "https://na2.docusign.net/Member/PowerFormSigning.aspx?PowerFormId=abc",
    ]


def test_collect_file_links_deduplicates():
    crawler = _load("crawler")
    raw = ["/Documents/ora/a.pdf", "/Documents/ora/a.pdf"]
    assert len(crawler._collect_file_links(raw, "https://www.morgan.edu/ora")) == 1


# ---------------------------------------------------------------------------
# The file phase's classifiers. First sighting must baseline, not report, or
# every known file shows up as changed on the first run.
# ---------------------------------------------------------------------------

def test_classify_file_picks_the_right_change_type():
    assert run._classify_file(known=None, doc_ids=[]) == "file_new"
    assert run._classify_file(known="abc", doc_ids=["a"]) == "file_changed"
    assert run._classify_file(known="abc", doc_ids=["a", "b"]) == "file_changed"


def test_first_sighting_of_a_file_with_documents_is_a_baseline_not_a_change():
    assert run._is_file_baseline(known=None, doc_ids=["a"]) is True
    assert run._is_file_baseline(known="abc", doc_ids=["a"]) is False


def test_a_never_seen_file_with_no_documents_is_not_a_baseline():
    # It is genuinely new information, so it drafts on the first run.
    assert run._is_file_baseline(known=None, doc_ids=[]) is False


# ---------------------------------------------------------------------------
# Bare-relative document links. morgan.edu writes every PDF/DOCX link this way
# and resolves it with <base href="https://www.morgan.edu/">. These used to
# normalize to "" and be dropped, which disabled the only mechanism that
# discovers a file with no KB document.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("raw", [
    "Documents/ADMINISTRATION/OFFICES/ora/PI/Handbook5.pdf",
    "./Documents/ADMINISTRATION/OFFICES/ora/PI/Handbook5.pdf",
])
def test_bare_relative_document_link_resolves_against_the_site_root(raw):
    page = "https://www.morgan.edu/office-of-research-administration/resources/templates"
    assert fp.normalize_url(raw, page) == (
        "https://www.morgan.edu/Documents/ADMINISTRATION/OFFICES/ora/PI/Handbook5.pdf"
    )


def test_bare_relative_is_not_resolved_against_the_containing_page():
    """The <base> tag points at the root, so page-relative joining is wrong.

    urljoin against the page URL yields a plausible path that 404s -- a worse
    outcome than dropping the link, because it looks like a real dead file.
    """
    page = "https://www.morgan.edu/office-of-research-administration/resources/templates"
    got = fp.normalize_url("Documents/ora/x.docx", page)
    assert "/resources/Documents/" not in got
    assert got == "https://www.morgan.edu/Documents/ora/x.docx"


def test_collect_file_links_now_sees_bare_relative_pdfs():
    crawler = _load("crawler")
    raw = [
        "Documents/ADMINISTRATION/OFFICES/ora/PI/Handbook1.pdf",
        "Documents/ADMINISTRATION/OFFICES/ora/Templates/budget.docx",
    ]
    out = crawler._collect_file_links(
        raw, "https://www.morgan.edu/office-of-research-administration/resources"
    )
    assert out == [
        "https://www.morgan.edu/Documents/ADMINISTRATION/OFFICES/ora/PI/Handbook1.pdf",
        "https://www.morgan.edu/Documents/ADMINISTRATION/OFFICES/ora/Templates/budget.docx",
    ]


def test_fragment_only_href_is_still_dropped():
    """The accordion toggles are <a href="#"> -- they must not become URLs."""
    assert fp.normalize_url("#", "https://www.morgan.edu/ora") == ""
