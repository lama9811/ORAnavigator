"""Storing the solicitation a proposal is reviewed against.

The load path is the one that matters: it decides whether a draft gets reviewed
at all, and returning something plausible-but-empty here would produce a
confident completeness percentage computed against nothing.
"""
import json

from services import proposals_service as ps

PAYLOAD = {
    "version": 1,
    "id": "PAR-24-118",
    "title": "NIH Research Project Grant",
    "url": "https://grants.nih.gov/par-24-118",
    "contract": {"budget_cap": 500000, "page_limits": {"research_strategy": 12},
                 "required_attachments": ["Data Management Plan"]},
    "requirements": [
        {"id": "research_strategy_specific_aim", "label": "Specific aims",
         "section": "research_strategy", "kind": "semantic", "scored": True,
         "source": "State the specific aims.", "why": "", "keywords": ["aims"]},
    ],
    "merit_criteria": [{"criterion": "Significance", "asks": "Does it matter?",
                        "source": "Reviewers will assess significance."}],
    "eligibility_notes": ["Domestic institutions of higher education."],
    "read_report": {"pages": 34, "pages_without_text": 0, "chars": 120000},
    "extraction": {"rounds": 2, "dropped_unverified": 3},
}


class _Sub:
    """Minimal stand-in for the ORM row — these helpers only read the column."""
    def __init__(self, payload=None):
        self.solicitation_json = json.dumps(payload) if payload is not None else None


def test_a_saved_profile_comes_back_ready_to_review_against():
    profile = ps.load_solicitation_profile(_Sub(PAYLOAD))
    assert profile["id"] == "PAR-24-118"
    assert profile["merit_criteria"][0]["criterion"] == "Significance"
    assert profile["eligibility_notes"] == ["Domestic institutions of higher education."]


def test_the_check_callables_are_reattached_on_load():
    # `checks` holds functions and cannot be serialized. If they were not put
    # back, every deterministic row would silently no-op and the PI would simply
    # never hear about their page limit or their budget cap.
    profile = ps.load_solicitation_profile(_Sub(PAYLOAD))
    assert callable(profile["checks"]["budget_cap"])
    assert callable(profile["checks"]["page_limit"])


def test_deterministic_rows_are_regenerated_from_the_contract_not_stored():
    # Regenerated, so editing the contract moves the check that enforces it.
    profile = ps.load_solicitation_profile(_Sub(PAYLOAD))
    checks = {r["check"] for r in profile["requirements"] if r["kind"] == "deterministic"}
    assert checks == {"budget_cap", "page_limit", "attachment_present"}


def test_sections_are_derived_on_load_so_they_cannot_drift():
    profile = ps.load_solicitation_profile(_Sub(PAYLOAD))
    assert "research_strategy" in profile["sections"]
    assert "data_management_plan" in profile["sections"]


def test_no_stored_solicitation_returns_none_not_an_empty_profile():
    # An empty profile would review a draft against zero requirements and hand
    # back a percentage. Reviewing against nothing has to be impossible.
    assert ps.load_solicitation_profile(_Sub()) is None


def test_a_payload_with_no_semantic_requirements_is_treated_as_absent():
    assert ps.load_solicitation_profile(_Sub({**PAYLOAD, "requirements": []})) is None


def test_malformed_json_is_treated_as_absent_never_raised():
    sub = _Sub()
    sub.solicitation_json = "{not json"
    assert ps.load_solicitation_profile(sub) is None


def test_a_non_dict_blob_is_treated_as_absent():
    sub = _Sub()
    sub.solicitation_json = json.dumps(["not", "a", "profile"])
    assert ps.load_solicitation_profile(sub) is None


def test_the_summary_carries_the_header_facts_without_the_rows():
    summary = ps.solicitation_summary(_Sub(PAYLOAD))
    assert summary["id"] == "PAR-24-118"
    assert summary["requirement_count"] == 1
    assert summary["read_report"]["pages"] == 34
    assert "requirements" not in summary


def test_the_summary_is_none_when_nothing_is_stored():
    assert ps.solicitation_summary(_Sub()) is None


def test_saving_drops_the_unserializable_and_the_derived(monkeypatch):
    class _DB:
        committed = False
        def commit(self): self.committed = True
        def refresh(self, _): pass

    sub = _Sub()
    ps.save_solicitation_profile(_DB(), sub, {**PAYLOAD,
                                              "checks": {"budget_cap": lambda *a: None},
                                              "sections": {"stale": {"label": "Stale"}}})
    stored = json.loads(sub.solicitation_json)
    assert "checks" not in stored          # callables do not serialize
    assert "sections" not in stored        # derived on load, so it cannot go stale
    assert stored["version"] == ps.SOLICITATION_PROFILE_VERSION


def test_the_notes_lines_helper_produces_what_draft_critic_parses_back():
    # The attach-later path must write the SAME lines the create path does, or
    # Draft Critic and the review disagree about one proposal's own solicitation.
    lines = ps.solicitation_notes_lines({
        "program_id": "PAR-24-118", "budget_cap": 500000,
        "page_limits": {"research_strategy": 12},
        "required_attachments": ["Data Management Plan", "Biosketch"],
    })
    joined = "\n".join(lines)
    assert "Program ID: PAR-24-118" in joined
    assert "Budget cap: $500,000" in joined
    assert "Page limits: research_strategy: 12p" in joined
    assert "Required attachments: Data Management Plan; Biosketch" in joined
