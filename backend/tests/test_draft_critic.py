"""Tests for the Draft Critic mechanical check service.

Draft Critic is the first Tier-1 AI-agent feature. It does NOT call an
LLM; every check is a deterministic transformation of the PDF text +
the solicitation context already known to the Submission. These tests
pin the individual check functions on hand-crafted text fixtures, so a
real PDF is not required.

The actual pdfplumber PDF->text step is covered by manual smoke tests
during deploy, not here -- it depends on real files.

Run from the backend/ directory:
    cd backend && ../.venv/bin/python -m pytest tests/test_draft_critic.py -v
"""
import pytest

from services import draft_critic as dc


# ---------- _section_present ------------------------------------------------

def test_section_present_case_insensitive():
    text = "Project Description\n\nThis proposal investigates..."
    assert dc._section_present(text, "project description") is True
    assert dc._section_present(text, "PROJECT DESCRIPTION") is True
    assert dc._section_present(text, "Project Description") is True


def test_section_present_returns_false_when_missing():
    text = "Project Summary\n\nWe propose...\n\nReferences\n..."
    assert dc._section_present(text, "Data Management Plan") is False


def test_section_present_handles_empty_inputs():
    assert dc._section_present("", "anything") is False
    assert dc._section_present("anything", "") is False
    assert dc._section_present(None, "x") is False  # type: ignore[arg-type]


# ---------- check_page_count ------------------------------------------------

def test_page_count_under_limit_is_ok():
    out = dc.check_page_count(
        actual_pages=12,
        page_limits={"project_description": 15},
    )
    assert out["status"] == "ok"
    assert out["value"] == "12 / 15"


def test_page_count_at_limit_is_ok():
    """Equal to the limit is still OK -- the cap is inclusive."""
    out = dc.check_page_count(
        actual_pages=15,
        page_limits={"project_description": 15},
    )
    assert out["status"] == "ok"


def test_page_count_over_limit_is_fail():
    out = dc.check_page_count(
        actual_pages=18,
        page_limits={"project_description": 15},
    )
    assert out["status"] == "fail"
    assert out["value"] == "18 / 15"


def test_page_count_skipped_when_no_limit():
    """A submission created manually (or one whose solicitation didn't
    state a page limit) skips the page check instead of failing."""
    out = dc.check_page_count(actual_pages=12, page_limits=None)
    assert out["status"] == "skipped"
    out2 = dc.check_page_count(actual_pages=12, page_limits={})
    assert out2["status"] == "skipped"


def test_page_count_prefers_project_description_over_other_limits():
    """When the solicitation lists multiple per-section limits, the
    Project Description / Research Strategy limit dominates total
    document length -- that's the one to check against."""
    out = dc.check_page_count(
        actual_pages=16,
        page_limits={"data_management_plan": 2, "project_description": 15},
    )
    # 16 > 15 -> fail (used the project_description limit, not DMP)
    assert out["status"] == "fail"
    assert "15" in out["value"]


def test_page_count_falls_back_to_smallest_limit_when_no_known_key():
    """If neither Project Description nor Research Strategy is listed,
    take the smallest stated limit (most binding)."""
    out = dc.check_page_count(
        actual_pages=12,
        page_limits={"appendix": 100, "letter_of_support": 5},
    )
    # 12 > 5 -> fail
    assert out["status"] == "fail"


# ---------- check_required_attachments --------------------------------------

def test_required_attachments_all_present_is_ok():
    text = """
        Biographical Sketch
        ...
        Current and Pending Support
        ...
        Data Management Plan
        ...
    """
    out = dc.check_required_attachments(
        text,
        required=["Biographical Sketch", "Current and Pending Support",
                  "Data Management Plan"],
    )
    assert out["status"] == "ok"
    assert out["missing"] == []
    assert set(out["found"]) == {"Biographical Sketch",
                                  "Current and Pending Support",
                                  "Data Management Plan"}


def test_required_attachments_some_missing_is_fail():
    text = "Biographical Sketch ...\nData Management Plan ...\n"
    out = dc.check_required_attachments(
        text,
        required=["Biographical Sketch", "Current and Pending Support",
                  "Data Management Plan"],
    )
    assert out["status"] == "fail"
    assert out["missing"] == ["Current and Pending Support"]
    assert "Current and Pending Support" in out["detail"]


def test_required_attachments_empty_list_is_skipped():
    """No required attachments listed (solicitation was silent or the
    submission was manual) -> skip rather than report 0-of-0 OK so the
    user understands there was nothing to check."""
    out = dc.check_required_attachments("some text", required=[])
    assert out["status"] == "skipped"


def test_required_attachments_ignores_blank_entries():
    """Defensive: blank strings in the required list shouldn't get
    counted as 'present' or 'missing'."""
    out = dc.check_required_attachments(
        "Biographical Sketch",
        required=["Biographical Sketch", "", "   "],
    )
    assert out["status"] == "ok"
    # Only the real attachment was checked.
    assert out["found"] == ["Biographical Sketch"]
    assert out["missing"] == []


# ---------- check_sponsor_default_sections ---------------------------------

def test_sponsor_default_sections_nsf_all_present():
    """An NSF draft that includes every standard section returns ok."""
    text = "\n".join([
        "Project Summary",
        "Project Description",
        "References Cited",
        "Biographical Sketch",
        "Budget Justification",
        "Current and Pending Support",
        "Facilities, Equipment and Other Resources",
        "Data Management Plan",
    ])
    out = dc.check_sponsor_default_sections(text, sponsor="NSF")
    assert out["status"] == "ok"
    assert out["missing"] == []


def test_sponsor_default_sections_nsf_some_missing_is_warn():
    """Missing a standard section is a warn (not fail) -- the
    solicitation may have relaxed the requirement and we don't want to
    block submission on a heuristic."""
    text = "Project Summary\nProject Description\nBiographical Sketch\n"
    out = dc.check_sponsor_default_sections(text, sponsor="NSF")
    assert out["status"] == "warn"
    assert "Data Management Plan" in out["missing"]


def test_sponsor_default_sections_nih_uses_nih_set():
    text = "Specific Aims\nResearch Strategy\nBiographical Sketch\n"
    out = dc.check_sponsor_default_sections(text, sponsor="NIH")
    # NIH-specific: Specific Aims is in the list (NSF doesn't have it)
    assert "Specific Aims" in out["found"]


def test_sponsor_default_sections_generic_for_unknown_sponsor():
    """An obscure / Internal sponsor falls back to the generic skeleton."""
    text = "Project Summary\nProject Description\nBudget\nBudget Justification\n"
    out = dc.check_sponsor_default_sections(text, sponsor="Internal")
    assert out["status"] == "ok"


# ---------- _largest_dollar_amount ------------------------------------------

def test_largest_dollar_simple_amounts():
    text = "Personnel: $120,000. Equipment: $45,000. Total: $498,500."
    assert dc._largest_dollar_amount(text) == 498_500


def test_largest_dollar_handles_K_M_suffixes():
    """Sponsors and PIs both write '$1.2M' for a $1.2 million budget."""
    text = "Year 1 total: $1.2M. Reserve: $500K."
    assert dc._largest_dollar_amount(text) == 1_200_000


def test_largest_dollar_returns_none_when_no_amount_found():
    """Plain numbers (no $) must NOT count -- table indices / page
    numbers / years would dominate the result otherwise."""
    text = "Section 3.2 references 15 papers across 2024 and 2025."
    assert dc._largest_dollar_amount(text) is None


def test_largest_dollar_handles_empty_input():
    assert dc._largest_dollar_amount("") is None
    assert dc._largest_dollar_amount(None) is None  # type: ignore[arg-type]


# ---------- check_budget_cap ------------------------------------------------

def test_budget_cap_under_is_ok():
    text = "Total budget: $498,500"
    out = dc.check_budget_cap(text, budget_cap=500_000)
    assert out["status"] == "ok"
    assert "$498,500" in out["value"]


def test_budget_cap_over_is_fail():
    text = "Total budget: $612,000"
    out = dc.check_budget_cap(text, budget_cap=500_000)
    assert out["status"] == "fail"


def test_budget_cap_skipped_when_no_cap():
    out = dc.check_budget_cap("Total: $400,000", budget_cap=None)
    assert out["status"] == "skipped"


def test_budget_cap_warn_when_no_dollar_amount_found():
    """No $ amount in the draft = budget section is probably missing or
    PDF is image-only. Surface as warn so the user investigates."""
    out = dc.check_budget_cap("just prose, no money figures", budget_cap=500_000)
    assert out["status"] == "warn"


# ---------- critique_pdf (top-level integration on the text layer) ----------

def test_critique_pdf_returns_none_when_pdf_unparseable(monkeypatch):
    """When pdfplumber can't open the bytes, the public API returns
    None so the endpoint can surface a friendly 422 to the user."""
    monkeypatch.setattr(dc, "_extract_pdf", lambda b: ("", 0, []))
    assert dc.critique_pdf(b"\x00\x01\x02", sponsor="NSF") is None


def test_critique_pdf_rolls_up_counts_and_issues(monkeypatch):
    """End-to-end on the orchestration layer (skipping pdfplumber).
    Validates the structure the frontend modal will consume."""
    fake_text = "\n".join([
        "Project Summary",
        "Project Description",
        "Biographical Sketch",
        "Budget Justification",
        "Data Management Plan",
        "References Cited",
        "Current and Pending Support",
        "Facilities, Equipment and Other Resources",
        "Total request: $498,500",
    ])
    monkeypatch.setattr(dc, "_extract_pdf", lambda b: (fake_text, 12, [fake_text]))

    out = dc.critique_pdf(
        pdf_bytes=b"not really a pdf",
        sponsor="NSF",
        solicitation={
            "budget_cap": 500_000,
            "page_limits": {"project_description": 15},
            "required_attachments": ["Biographical Sketch", "Data Management Plan"],
        },
    )
    assert out is not None
    assert out["pages"] == 12
    assert out["sponsor"] == "NSF"
    # V2: at least page count, attachments, sections, budget (Project
    # Summary length check may add a 5th when a summary header is found).
    assert len(out["checks"]) >= 4
    # The four core checks should each pass on this clean fixture.
    core_names = {"Page count", "Required attachments",
                  "Standard NSF sections", "Budget vs cap"}
    core = [c for c in out["checks"] if c["name"] in core_names]
    assert all(c["status"] == "ok" for c in core)


def test_critique_pdf_flags_over_budget(monkeypatch):
    fake_text = "Biographical Sketch\nData Management Plan\nTotal request: $612,000"
    monkeypatch.setattr(dc, "_extract_pdf", lambda b: (fake_text, 10, [fake_text]))

    out = dc.critique_pdf(
        pdf_bytes=b"x",
        sponsor="NSF",
        solicitation={
            "budget_cap": 500_000,
            "page_limits": {"project_description": 15},
            "required_attachments": ["Biographical Sketch", "Data Management Plan"],
        },
    )
    assert out is not None
    assert out["issues"] >= 1
    # The budget check specifically should be the failure
    budget_check = next(c for c in out["checks"] if c["name"] == "Budget vs cap")
    assert budget_check["status"] == "fail"


def test_critique_pdf_with_no_solicitation_context_still_runs(monkeypatch):
    """Submissions created manually (not from a solicitation) have an
    empty context dict. Draft Critic should still produce a useful
    critique using sponsor-default sections."""
    fake_text = "Project Summary\nProject Description\nBudget Justification\n"
    monkeypatch.setattr(dc, "_extract_pdf", lambda b: (fake_text, 5, [fake_text]))

    out = dc.critique_pdf(pdf_bytes=b"x", sponsor="NSF", solicitation=None)
    assert out is not None
    # Page count, attachments, and budget should all be skipped (no context)
    # but sections check should still run.
    skipped = [c for c in out["checks"] if c["status"] == "skipped"]
    assert len(skipped) >= 3
    section_check = next(c for c in out["checks"]
                         if c["name"].startswith("Standard"))
    assert section_check["status"] in ("ok", "warn")


# ---------- v2 upgrades: verdict, grammar, dedupe, solicitation detect -----

def test_verdict_ready_when_no_warn_or_fail():
    v = dc._overall_verdict({"ok": 4, "warn": 0, "fail": 0, "skipped": 0})
    assert v["level"] == "ready"


def test_verdict_critical_when_any_fail():
    v = dc._overall_verdict({"ok": 2, "warn": 1, "fail": 1, "skipped": 0})
    assert v["level"] == "critical"
    assert "1 check" in v["message"]   # singular


def test_verdict_needs_review_for_multiple_warns():
    v = dc._overall_verdict({"ok": 1, "warn": 3, "fail": 0, "skipped": 0})
    assert v["level"] == "needs_review"


def test_verdict_minor_for_single_warn():
    v = dc._overall_verdict({"ok": 3, "warn": 1, "fail": 0, "skipped": 0})
    assert v["level"] == "minor"


def test_page_count_singular_grammar():
    """Defensive against the original '1 pages' bug. A 1-page draft
    must render '1 page' (no 's')."""
    out = dc.check_page_count(actual_pages=1,
                              page_limits={"project_description": 15})
    assert "1 page" in out["detail"]
    assert "1 pages" not in out["detail"]


def test_section_present_requires_header_not_body_match():
    """V2 detection is header-aware: a phrase buried in body text
    should NOT be flagged as a section header."""
    body = ("In this proposal we include a biographical sketch for "
            "each investigator, as required.")
    assert dc._section_present(body, "Biographical Sketch") is False


def test_section_present_matches_real_header():
    """A real header line (the name at the start of a line) IS matched."""
    text = (
        "Project Description\n"
        "We propose to study...\n"
        "\n"
        "Biographical Sketch\n"
        "Dr. Smith earned her PhD at..."
    )
    assert dc._section_present(text, "Biographical Sketch") is True


def test_required_attachments_dedupes_against_standard_sections():
    """If Required Attachments already flagged 'Data Management Plan'
    as missing, the Standard Sections check must NOT flag it again --
    the user sees one row, not two."""
    text = "Project Description\nReferences\n"  # neither has DMP
    req_check = dc.check_required_attachments(
        text, required=["Data Management Plan"]
    )
    assert "Data Management Plan" in req_check["missing"]
    suppress = set(req_check["missing"])
    sec_check = dc.check_sponsor_default_sections(
        text, sponsor="NSF", suppress=suppress
    )
    assert "Data Management Plan" not in (sec_check["missing"] or [])


def test_check_looks_like_solicitation_detects_sponsor_pdf():
    """A PDF full of 'Program Description', 'Important Dates', etc. --
    aka the user uploaded the funding announcement, not their draft --
    must surface a sanity warning."""
    text = (
        "Program Description\nThis program supports research...\n\n"
        "Important Dates\nDeadline: August 14, 2026\n\n"
        "Award Information\nUp to $1,200,000...\n\n"
        "Page Limitations\nProject Description: 15 pages\n\n"
        "Required Attachments\n(1) Project Summary..."
    )
    out = dc.check_looks_like_solicitation(text)
    assert out is not None
    assert out["status"] == "warn"
    assert "solicitation" in out["detail"].lower()


def test_check_looks_like_solicitation_skips_normal_proposals():
    """A regular proposal draft (no funding-announcement boilerplate)
    should NOT trip the sanity warning."""
    text = (
        "Project Summary\nWe propose to study...\n\n"
        "Project Description\nOur approach builds on...\n\n"
        "Budget Justification\nPersonnel costs are based on..."
    )
    assert dc.check_looks_like_solicitation(text) is None


def test_check_per_section_page_limits_returns_dmp_check():
    """When the solicitation caps DMP at 2 pages and the draft has a
    DMP section, we should get a check row for it."""
    pages = [
        "Project Description\nOur work...",
        "Project Description continues...",
        "Data Management Plan\nWe will deposit all data...",
        "References\n...",
    ]
    out = dc.check_per_section_page_limits(
        pages, page_limits={"data_management_plan": 2,
                            "project_description": 15}
    )
    names = [c["name"] for c in out]
    assert any("Data Management Plan" in n for n in names)


def test_critique_pdf_surfaces_verdict_in_payload(monkeypatch):
    """The end-to-end orchestrator must now include a `verdict` field
    on the response for the UI to render the big banner."""
    fake_text = (
        "Project Summary\nWe propose...\n"
        "Project Description\nOur approach...\n"
        "Biographical Sketch\nDr X is...\n"
        "Budget Justification\n$498,500 total\n"
        "Data Management Plan\nWe will share...\n"
        "References Cited\n[1] ...\n"
        "Current and Pending Support\nNone.\n"
        "Facilities, Equipment and Other Resources\nMorgan lab.\n"
    )
    pages = [fake_text]
    monkeypatch.setattr(dc, "_extract_pdf", lambda b: (fake_text, 12, pages))

    out = dc.critique_pdf(
        pdf_bytes=b"x",
        sponsor="NSF",
        solicitation={
            "budget_cap": 500_000,
            "page_limits": {"project_description": 15},
            "required_attachments": ["Biographical Sketch", "Data Management Plan"],
        },
    )
    assert out is not None
    assert "verdict" in out
    assert out["verdict"]["level"] in ("ready", "minor", "needs_review", "critical")
    assert out["verdict"]["label"]
    assert out["verdict"]["message"]


# ---------- reconstruct_solicitation_context (helper on proposals_service) ---

def test_reconstruct_solicitation_context_from_notes_and_tasks():
    """The helper on proposals_service pulls structured solicitation
    data back out of a Submission row, by parsing notes + filtering
    tasks. Tested here because Draft Critic is the only consumer."""
    from services import proposals_service as ps

    class _FakeTask:
        def __init__(self, title):
            self.title = title

    class _FakeSubmission:
        notes = (
            "Program ID: NSF 23-573\n"
            "Eligibility: Early-career tenure-track faculty\n"
            "Budget cap: $600,000\n"
            "Submission portal: Research.gov\n"
            "Page limits: project_description: 15p, data_management_plan: 2p"
        )
        tasks = [
            _FakeTask("Submit internal routing form"),
            _FakeTask("Prepare required attachment: Biographical Sketch"),
            _FakeTask("Prepare required attachment: Data Management Plan"),
        ]

    ctx = ps.reconstruct_solicitation_context(_FakeSubmission())
    assert ctx["budget_cap"] == 600_000
    assert ctx["page_limits"] == {
        "project_description": 15,
        "data_management_plan": 2,
    }
    assert set(ctx["required_attachments"]) == {
        "Biographical Sketch",
        "Data Management Plan",
    }


def test_reconstruct_solicitation_context_with_blank_notes():
    """Manually-created submissions have no notes blob and no
    required-attachment tasks -- helper must return safe empties."""
    from services import proposals_service as ps

    class _FakeSubmission:
        notes = None
        tasks = []

    ctx = ps.reconstruct_solicitation_context(_FakeSubmission())
    assert ctx["budget_cap"] is None
    assert ctx["page_limits"] == {}
    assert ctx["required_attachments"] == []
