"""Reading a whole solicitation without losing requirements.

Every test here guards against a SILENT loss — a requirement that disappears
with no error, which is the failure mode this module exists to remove. The model
is faked throughout; live-model recall is measured separately in
test_solicitation_requirements_recall.py.
"""
import re

import pytest

from services import solicitation_requirements as sr

SOLICITATION = (
    "The Project Description must include a sustainability plan describing how the "
    "work leads to a future core-program submission. " * 40
    + "\nProposals must include a Data Management Plan of no more than two pages.\n"
)


def _fake(rows, key="requirements"):
    """A stand-in for gemini_client.generate_json returning fixed rows."""
    return lambda prompt, **kw: {key: rows}


# ── chunking: every character is read ───────────────────────────────────────

def test_chunking_covers_the_whole_document():
    text = "".join(f"line {i}\n" for i in range(20_000))
    chunks = sr.chunk_text(text, size=10_000, overlap=500)
    assert len(chunks) > 1
    assert text[:200] in chunks[0]
    assert text[-200:] in chunks[-1]
    assert len("".join(chunks)) >= len(text)


def test_a_short_document_is_one_chunk_and_an_empty_one_is_none():
    assert sr.chunk_text("short") == ["short"]
    assert sr.chunk_text("") == []


def test_a_requirement_split_across_a_chunk_boundary_is_still_quotable():
    """The reason the windows overlap: a sentence cut in half is quotable in
    neither half, so the verifier would drop a real requirement."""
    sentence = "Proposals must include a postdoctoral mentoring plan of two pages."
    text = ("x" * 9_960) + sentence + ("y" * 5_000)
    chunks = sr.chunk_text(text, size=10_000, overlap=500)
    assert any(sentence in c for c in chunks), "overlap failed to keep a boundary sentence whole"


# ── grounding ───────────────────────────────────────────────────────────────

def test_a_row_whose_quote_is_not_in_the_document_is_dropped(monkeypatch):
    monkeypatch.setattr(sr.gemini_client, "generate_json", _fake([
        {"label": "Sustainability plan", "section": "project_description",
         "source": "must include a sustainability plan", "why": "", "keywords": [],
         "scored": True},
        {"label": "Invented ask", "section": "project_description",
         "source": "the proposal must include a haiku about polymers",
         "why": "", "keywords": [], "scored": True},
    ]))
    out = sr.extract_requirements(SOLICITATION, max_rounds=0)
    labels = {r["label"] for r in out["requirements"]}
    assert "Sustainability plan" in labels
    assert "Invented ask" not in labels
    assert out["dropped_unverified"] == 1


def test_a_bulleted_requirement_quote_survives_verification(monkeypatch):
    """pdfplumber leaves bullet glyphs and (cid:N) artifacts in list items, and a
    bulleted list is the commonest shape of a real requirement. Without noise-
    tolerant matching these rows vanish silently — systematically, and precisely
    where the requirements are densest."""
    doc = "Required elements:\n• (cid:127) Include a Data Management Plan of two pages.\n"
    monkeypatch.setattr(sr.gemini_client, "generate_json", _fake([
        {"label": "Data Management Plan", "section": "data_management_plan",
         "source": "Include a Data Management Plan of two pages.",
         "why": "", "keywords": [], "scored": True},
    ]))
    out = sr.extract_requirements(doc, max_rounds=0)
    assert [r["label"] for r in out["requirements"]] == ["Data Management Plan"]
    assert out["dropped_unverified"] == 0


def test_a_row_with_no_quote_or_no_label_is_dropped(monkeypatch):
    monkeypatch.setattr(sr.gemini_client, "generate_json", _fake([
        {"label": "No quote", "section": None, "source": "", "keywords": []},
        {"label": "", "section": None, "source": "must include a sustainability plan"},
        "not even a dict",
    ]))
    out = sr.extract_requirements(SOLICITATION, max_rounds=0)
    assert out["requirements"] == []


# ── the sweep ───────────────────────────────────────────────────────────────

def test_the_sweep_keeps_going_until_a_round_adds_nothing(monkeypatch):
    calls = {"n": 0}

    def fake(prompt, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            return {"requirements": [
                {"label": "Sustainability plan", "section": "project_description",
                 "source": "must include a sustainability plan", "why": "",
                 "keywords": [], "scored": True}]}
        if calls["n"] == 2:
            return {"requirements": [
                {"label": "Data Management Plan", "section": "data_management_plan",
                 "source": "Data Management Plan of no more than two pages",
                 "why": "", "keywords": [], "scored": True}]}
        return {"requirements": []}

    monkeypatch.setattr(sr.gemini_client, "generate_json", fake)
    out = sr.extract_requirements(SOLICITATION, max_rounds=5)
    assert len(out["requirements"]) == 2
    assert out["hit_round_cap"] is False
    assert calls["n"] >= 3          # first pass + a productive sweep + a dry one


def test_a_sweep_that_never_runs_dry_reports_the_round_cap(monkeypatch):
    """Bounded, and it SAYS so. A silent cap reads as 'we found everything' when
    it means 'we stopped looking'."""
    calls = {"n": 0}

    def fake(prompt, **kw):
        calls["n"] += 1
        return {"requirements": [
            {"label": f"Requirement {calls['n']}", "section": None,
             "source": "must include a sustainability plan", "why": "",
             "keywords": [], "scored": True}]}

    monkeypatch.setattr(sr.gemini_client, "generate_json", fake)
    out = sr.extract_requirements(SOLICITATION, max_rounds=2)
    assert out["hit_round_cap"] is True
    assert out["rounds"] == 2


def test_the_time_budget_stops_the_sweep_and_says_so(monkeypatch):
    monkeypatch.setattr(sr.gemini_client, "generate_json", _fake([
        {"label": "Sustainability plan", "section": "project_description",
         "source": "must include a sustainability plan", "why": "",
         "keywords": [], "scored": True}]))
    out = sr.extract_requirements(SOLICITATION, max_rounds=5, budget_s=0)
    assert out["hit_time_cap"] is True
    assert out["requirements"], "the first pass must still be kept when time runs out"


# ── dedup and section vocabulary ────────────────────────────────────────────

def test_the_same_requirement_from_two_chunks_becomes_one_row(monkeypatch):
    monkeypatch.setattr(sr.gemini_client, "generate_json", _fake([
        {"label": "Sustainability plan", "section": "project_description",
         "source": "must include a sustainability plan", "why": "",
         "keywords": [], "scored": True}]))
    text = SOLICITATION * 3          # long enough to chunk
    out = sr.extract_requirements(text, max_rounds=0)
    assert len(out["requirements"]) == 1
    assert out["chunks"] >= 1


def test_two_chunks_naming_one_section_differently_produce_one_section():
    """Each distinct section costs the reviewer its own Gemini call, so an
    unmerged vocabulary is a latency and rate-limit problem, not just untidy."""
    assert sr.canon_section("Project Description") == "project_description"
    assert sr.canon_section("the project descriptions") == "project_description"
    assert sr.canon_section("PROJECT_DESCRIPTION") == "project_description"
    assert sr.canon_section(None) is None
    assert sr.canon_section("   ") is None


def test_singularization_does_not_mangle_words_ending_in_s():
    assert sr.canon_section("data analysis") == "data_analysis"
    assert sr.canon_section("letters of collaboration") == "letter_collaboration"


def test_ids_are_stable_across_equivalent_rows():
    a = sr.make_id({"label": "Sustainability plan", "section": "project_description"})
    b = sr.make_id({"label": "Sustainability  plan", "section": "the project description"})
    assert a == b


# ── offline ─────────────────────────────────────────────────────────────────

def test_offline_returns_no_requirements_and_says_so():
    """Golden rule 3: never raise, never fabricate. An empty list plus ai=False
    is the honest answer, and the caller turns it into a visible warning."""
    out = sr.extract_requirements(SOLICITATION, use_ai=False)
    assert out["requirements"] == []
    assert out["ai"] is False


def test_an_empty_document_is_not_an_error():
    out = sr.extract_requirements("")
    assert out["requirements"] == [] and out["chars"] == 0


def test_a_model_outage_mid_run_yields_no_rows_rather_than_an_exception(monkeypatch):
    monkeypatch.setattr(sr.gemini_client, "generate_json", lambda *a, **k: None)
    out = sr.extract_requirements(SOLICITATION, max_rounds=1)
    assert out["requirements"] == []


# ── merit criteria ──────────────────────────────────────────────────────────

def test_merit_criteria_are_quote_verified(monkeypatch):
    doc = "Reviewers will assess Intellectual Merit and Broader Impacts."
    monkeypatch.setattr(sr.gemini_client, "generate_json", _fake([
        {"criterion": "Intellectual Merit", "asks": "Advances knowledge.",
         "source": "Reviewers will assess Intellectual Merit"},
        {"criterion": "Fabricated", "asks": "Nothing.",
         "source": "reviewers will assess the applicant's astrological sign"},
    ], key="criteria"))
    out = sr.extract_merit_criteria(doc)
    assert [c["criterion"] for c in out] == ["Intellectual Merit"]


def test_merit_criteria_offline_is_empty_not_an_error():
    assert sr.extract_merit_criteria("anything", use_ai=False) == []


# ── the guarantee behind the whole change ───────────────────────────────────

def test_this_module_names_no_funder():
    import inspect
    src = inspect.getsource(sr).lower()
    for token in (r"23-598", r"\beir\b", r"\bhbcu\b"):
        assert not re.search(token, src), f"funder-specific token: {token}"
