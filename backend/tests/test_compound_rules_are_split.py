"""One rule, one question.

WHY. A PI checked the same Facilities PDF twice and got 88% then 100%. Measured
across five runs on the old prompt: 100, 88, 63, 88, 63 -- and every point of
that spread came from ONE rule, "Names senior/key personnel and postdocs drawing
no funds". Their draft names its unfunded senior people and says nothing about
postdocs, so the rule is genuinely half-satisfied and the model has to round it.
It rounded differently run to run.

No prompt fixes that. A compound question has no clean yes or no, so the answer
is a coin-weighting rather than a reading. Three curated rules asked more than
one thing:

    The Overview describes the objectives AND the methods
    Covers internal AND external resources, physical AND personnel
    Names senior/key personnel AND postdocs drawing no funds

Each becomes single-question rules. Two gains, and the second matters more than
the stability: an author stops seeing a vague `partial` and starts seeing
exactly which half is missing.

WHAT THIS IS NOT. It is not new authority and not a stricter standard -- NSF's
sentence is unchanged and each half still quotes it. The rules are re-expressed,
not added to. A draft satisfying the old compound rule satisfies both halves of
its split, and a draft satisfying neither fails both; only the genuinely
half-met case changes, and it changes from a coin-flip to a specific answer.

The count moves 14 -> 17, so anything asserting 14 basics is asserting this
change did not happen.
"""

from services import rulebook_baseline as rb


def _ids(section=None, tier="basic"):
    rows = rb.rules_for("the PAPPG", section, tier=tier) if section else \
        rb.rules_for("the PAPPG", tier=tier)
    return [r["id"] for r in rows]


def _by_id(rid):
    return next((r for r in rb.rules_for("the PAPPG") if r["id"] == rid), None)


# ── the splits ─────────────────────────────────────────────────────────────

def test_the_overview_rule_asks_about_objectives_and_methods_separately():
    ids = _ids("project_summary")
    assert "pappg_ps_overview_objectives" in ids, ids
    assert "pappg_ps_overview_methods" in ids, ids
    assert "pappg_ps_overview" not in ids, "the compound rule is still there"


def test_the_unfunded_rule_asks_about_personnel_and_postdocs_separately():
    ids = _ids("facilities_equipment_and_other_resources")
    assert "pappg_fe_unfunded_personnel" in ids, ids
    assert "pappg_fe_unfunded_postdocs" in ids, ids
    assert "pappg_fe_unfunded" not in ids


def test_the_coverage_rule_asks_about_each_kind_of_resource_separately():
    ids = _ids("facilities_equipment_and_other_resources")
    assert "pappg_fe_coverage_internal_external" in ids, ids
    assert "pappg_fe_coverage_physical_personnel" in ids, ids
    assert "pappg_fe_coverage" not in ids


def test_the_three_known_compound_rules_are_gone():
    """Deliberately NOT a blanket ban on " and " in a label: that test is passed
    by rewording, which is worse than not having it -- a rule can ask two
    questions in any number of phrasings. The three that were measured wobbling
    are named instead, so re-adding one is what fails.
    """
    ids = set(_ids())
    for gone in ("pappg_ps_overview", "pappg_fe_coverage", "pappg_fe_unfunded"):
        assert gone not in ids, gone


def test_the_physical_personnel_axis_is_deliberately_not_split_further():
    """RECORDED, because it is a judgement someone may want to revisit.

    "internal and external resources (both physical and personnel)" has two
    independent axes, so a strict reading gives FOUR rules. It is split on
    internal/external only. A draft routinely describes a lab and the people in
    it in one breath, and four rules would give a 617-word section eight of them
    -- against three for the whole Project Description. Over-splitting turns a
    check into a nag, which is the failure this repo already has on record from
    shipping 142 rules at once.
    """
    rows = [r for r in rb.rules_for("the PAPPG", "facilities_equipment_and_other_resources",
                                    tier="basic")]
    coverage = [r for r in rows if r["id"].startswith("pappg_fe_coverage")]
    assert len(coverage) == 2, [r["id"] for r in coverage]


# ── what must not change ───────────────────────────────────────────────────

def test_every_half_still_quotes_nsfs_own_sentence():
    """No new authority. Each half carries the sentence the compound rule
    carried, so "why is this required?" still shows NSF's words."""
    for rid, cue in (("pappg_ps_overview_objectives", "objectives and methods"),
                     ("pappg_ps_overview_methods", "objectives and methods"),
                     ("pappg_fe_unfunded_personnel", "no funds are being requested"),
                     ("pappg_fe_unfunded_postdocs", "postdoctoral"),
                     ("pappg_fe_coverage_internal_external", "internal and"),
                     ("pappg_fe_coverage_physical_personnel", "physical and personnel")):
        row = _by_id(rid)
        assert row is not None, rid
        assert cue in row["source"], (rid, row["source"])
        assert row["rulebook"] == "the PAPPG"
        assert row["tier"] == "basic"


def test_the_splits_stay_in_their_own_sections():
    for rid in ("pappg_ps_overview_objectives", "pappg_ps_overview_methods"):
        assert _by_id(rid)["section"] == "project_summary"
    for rid in ("pappg_fe_unfunded_personnel", "pappg_fe_unfunded_postdocs",
                "pappg_fe_coverage_internal_external",
                "pappg_fe_coverage_physical_personnel"):
        assert _by_id(rid)["section"] == "facilities_equipment_and_other_resources"


def test_the_deterministic_checks_are_untouched():
    """Splitting judgement rules must not disturb the rules decided by code --
    those never wobbled and carry the sections' only hard verdicts."""
    checks = {r["id"]: r["check"] for r in rb.rules_for("the PAPPG", tier="basic")
              if r.get("check")}
    assert checks == {
        "pappg_ps_headings": "rb_headings",
        "pappg_ps_one_page": "rb_page_limit",
        "pappg_pd_impacts_header": "rb_headings",
        "pappg_pd_no_urls": "rb_no_urls",
        "pappg_pd_page_limit": "rb_page_limit",
        "pappg_rc_et_al": "rb_et_al",
        "pappg_fe_no_financials": "rb_no_financials",
        # Moved from model judgement to code on 2026-08-28 -- it was the last
        # unstable rule in Facilities. See test_narrative_check.py.
        "pappg_fe_narrative": "rb_narrative",
    }, checks


def test_the_basic_count_reflects_the_split():
    assert len(_ids()) == 17, _ids()
