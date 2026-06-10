"""Tests for the Compliance Sentinel deterministic core (2026-06-10).

The whole point of the Sentinel is that WHICH approvals you need is decided by
code rules, never an LLM. These tests pin the trigger logic that PIs get wrong:

  * IRB required iff human subjects
  * IACUC required iff animals
  * COI required for PHS/NIH sponsors (all investigators) OR a financial interest
  * RCR training required for NSF/NIH sponsors
  * Export Control / Research Security: TCP (required) vs NSPM-33 review

Every rule's KB form id must resolve to a live URL (guards against a KB rename
silently producing a dead "Open form" link).
"""

from services.compliance_sentinel import (
    assess_compliance,
    suggested_tasks,
    QUESTIONS,
    RULES,
)


def _item(result, rule_id):
    for it in result["items"]:
        if it["id"] == rule_id:
            return it
    raise AssertionError(f"no item {rule_id} in {[i['id'] for i in result['items']]}")


# ---------------------------------------------------------------------------
# IRB — human subjects
# ---------------------------------------------------------------------------

def test_irb_required_when_human_subjects_yes():
    r = assess_compliance({"human_subjects": "yes"}, sponsor="Internal")
    assert _item(r, "irb")["status"] == "required"


def test_irb_not_required_when_human_subjects_no():
    r = assess_compliance({"human_subjects": "no"}, sponsor="Internal")
    assert _item(r, "irb")["status"] == "not_required"


def test_irb_review_when_unanswered():
    r = assess_compliance({}, sponsor="Internal")
    assert _item(r, "irb")["status"] == "review"


# ---------------------------------------------------------------------------
# IACUC — animals
# ---------------------------------------------------------------------------

def test_iacuc_required_when_animals_yes():
    r = assess_compliance({"animals": "yes"}, sponsor="Internal")
    assert _item(r, "iacuc")["status"] == "required"


def test_iacuc_not_required_when_animals_no():
    r = assess_compliance({"animals": "no"}, sponsor="Internal")
    assert _item(r, "iacuc")["status"] == "not_required"


# ---------------------------------------------------------------------------
# COI — sponsor-derived (PHS/NIH) OR financial interest
# ---------------------------------------------------------------------------

def test_coi_required_for_nih_even_without_financial_interest():
    r = assess_compliance({"financial_interest": "no"}, sponsor="NIH")
    assert _item(r, "coi")["status"] == "required"


def test_coi_required_when_financial_interest_yes_nonfederal():
    r = assess_compliance({"financial_interest": "yes"}, sponsor="Internal")
    assert _item(r, "coi")["status"] == "required"


def test_coi_not_required_when_no_interest_and_nonfederal():
    r = assess_compliance({"financial_interest": "no"}, sponsor="Internal")
    assert _item(r, "coi")["status"] == "not_required"


# ---------------------------------------------------------------------------
# RCR — federal training mandate (NSF/NIH)
# ---------------------------------------------------------------------------

def test_rcr_required_for_nsf():
    r = assess_compliance({}, sponsor="NSF")
    assert _item(r, "rcr")["status"] == "required"


def test_rcr_required_for_nih():
    r = assess_compliance({}, sponsor="NIH")
    assert _item(r, "rcr")["status"] == "required"


def test_rcr_not_required_for_internal():
    r = assess_compliance({}, sponsor="Internal")
    assert _item(r, "rcr")["status"] == "not_required"


def test_sponsor_match_is_case_insensitive():
    r = assess_compliance({}, sponsor="nsf")
    assert _item(r, "rcr")["status"] == "required"


# ---------------------------------------------------------------------------
# Export Control / Research Security
# ---------------------------------------------------------------------------

def test_export_required_when_controlled_tech():
    r = assess_compliance({"export_controlled": "yes"}, sponsor="Internal")
    it = _item(r, "export_security")
    assert it["status"] == "required"
    assert "technology_control_plan" in it["kb_doc_id"]


def test_export_review_when_foreign_only():
    r = assess_compliance(
        {"export_controlled": "no", "foreign_collaboration": "yes"}, sponsor="Internal"
    )
    it = _item(r, "export_security")
    assert it["status"] == "review"
    assert "nspm_33" in it["kb_doc_id"]


def test_export_not_required_when_neither():
    r = assess_compliance(
        {"export_controlled": "no", "foreign_collaboration": "no"}, sponsor="Internal"
    )
    assert _item(r, "export_security")["status"] == "not_required"


def test_controlled_tech_takes_precedence_over_foreign():
    r = assess_compliance(
        {"export_controlled": "yes", "foreign_collaboration": "yes"}, sponsor="Internal"
    )
    assert _item(r, "export_security")["status"] == "required"


# ---------------------------------------------------------------------------
# Summary + tasks
# ---------------------------------------------------------------------------

def test_summary_counts_match_items():
    r = assess_compliance(
        {"human_subjects": "yes", "animals": "no", "financial_interest": "yes",
         "export_controlled": "no", "foreign_collaboration": "no"},
        sponsor="NIH",
    )
    s = r["summary"]
    statuses = [i["status"] for i in r["items"]]
    assert s["required"] == statuses.count("required")
    assert s["review"] == statuses.count("review")
    assert s["not_required"] == statuses.count("not_required")


def test_suggested_tasks_only_for_required_items():
    r = assess_compliance(
        {"human_subjects": "yes", "animals": "no", "export_controlled": "no",
         "foreign_collaboration": "yes"},  # foreign-only => review, NOT a task
        sponsor="Internal",
    )
    tasks = suggested_tasks(r)
    titles = " ".join(t["title"].lower() for t in tasks)
    assert "irb" in titles or "human subjects" in titles
    # review item (export_security) must NOT become a task
    assert all("export" not in t["title"].lower() and "security" not in t["title"].lower()
               for t in tasks)
    # every task carries the rule's kb_doc_id
    assert all(t.get("kb_doc_id") for t in tasks)


# ---------------------------------------------------------------------------
# Every rule links to a live KB doc (no dead "Open form" links)
# ---------------------------------------------------------------------------

def test_every_required_item_resolves_a_url():
    r = assess_compliance(
        {"human_subjects": "yes", "animals": "yes", "financial_interest": "yes",
         "export_controlled": "yes"},
        sponsor="NIH",
    )
    for it in r["items"]:
        if it["status"] in ("required", "review"):
            assert it["kb_doc_id"], f"{it['id']} missing doc id"
            assert it["kb_doc_url"], f"{it['id']} ({it['kb_doc_id']}) resolved no URL"
            assert it["kb_doc_url"].startswith("http")


def test_questions_cover_all_questionnaire_triggers():
    keys = {q["key"] for q in QUESTIONS}
    assert {"human_subjects", "animals", "financial_interest",
            "foreign_collaboration", "export_controlled"} <= keys


# ---------------------------------------------------------------------------
# Robustness — never crash on junk
# ---------------------------------------------------------------------------

def test_junk_answers_do_not_crash():
    r = assess_compliance({"human_subjects": "YES", "animals": 1, "export_controlled": None},
                          sponsor=None)
    assert "items" in r and len(r["items"]) == len(RULES)


def test_truthy_yes_variants_trigger():
    # "YES"/"true"/"y" should all count as yes
    for val in ("YES", "Yes", "y", "true", "True"):
        r = assess_compliance({"human_subjects": val}, sponsor="Internal")
        assert _item(r, "irb")["status"] == "required", val
