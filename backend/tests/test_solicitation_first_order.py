"""The solicitation's own rules are listed BEFORE the rulebook's.

WHY (reported by a PI reading a real screen, 2026-08-27)
-------------------------------------------------------
Check a Section is BASICS + SOLICITATION, and the product rule is that the
solicitation LEADS while NSF's basics are the floor. On a Project Summary that
came out looking like the opposite: NSF 23-598 states exactly ONE Project
Summary rule -- "must include the LOI number in addition to all the
requirements outlined in the PAPPG" -- so the screen showed five PAPPG rows and
the PI's own single rule last. Their words: "no it's only from pappg i want it
to be with solicitation and then nsf basic rules".

The MIX was correct and is not changed here: that section really does have one
solicitation rule, verified against the stored profile, and the PAPPG rows are
the whole reason a five-line Project Summary with no Intellectual Merit
statement stopped coming back "Addressed". What was wrong was the ORDER. On the
same proposal's Project Description the split is 13 solicitation rows to 3, so
the PI's own asks dominate wherever the funder actually wrote them.

ORDER, NOT HEADINGS. CLAUDE.md records the three provenance headings ("From
your solicitation" / "...+ NSF baseline" / "NSF baseline") being removed within
hours on 2026-08-26, because every section is checked against BOTH and a PI read
them as "these are checked differently" -- "IF IT CHECKS BOTH THEN DON'T GROUP
IT." The same reasoning applies here, so this changes sequence only. Provenance
still reaches the reader through the per-row tag and `score.by_source`.

ONE ENGINE, TWO ENTRY POINTS. `review_draft` and `review_section` share the
sort, for the reason `review_section`'s own docstring gives: two engines that
disagree about the same section is the confusion this tool exists to remove.
"""

from services import draft_review
from services import rulebook_baseline as rb
from services import solicitation_profile as sp


PAPPG = "the PAPPG"


def _row(rid, section, label, *, section_label=None):
    row = {"id": rid, "section": section, "label": label, "kind": "semantic",
           "scored": True, "source": f"The solicitation requires: {label}.",
           "why": "", "keywords": []}
    if section_label:
        row["section_label"] = section_label
    return row


def _profile():
    """Two solicitation rules on a section the PAPPG also has basics for.

    The first row carries NSF 23-598's REAL sentence, and that matters: rulebook
    rows reach a stored profile only when the solicitation CITES the rulebook
    (`baseline_rows` -> `rulebooks_cited_by`). A fixture whose `source` never
    says "PAPPG" gets no baseline rows at all, so `review_draft` sees one source
    and the ordering assertion passes vacuously. CLAUDE.md records that exact
    fixture trap making an earlier filter look like it worked.
    """
    rows = [
        _row("sol_ps_loi", "project_summary",
             "Include the LOI number in the Project Summary"),
        _row("sol_ps_title", "project_summary",
             "Use the required title format in the Project Summary"),
    ]
    rows[0]["source"] = ("The Project Summary must include the LOI number in "
                         "addition to all the requirements outlined in the PAPPG.")
    return sp.build_generic({}, rows, id="NSF 23-598", title="t")


TEXT = ("Overview\nWe will build adaptive sensors. LOI number 12345.\n\n"
        "Intellectual Merit\nThis advances ion transport theory.\n\n"
        "Broader Impacts\nFour undergraduates are trained each year.\n")


def _sources(findings):
    """1 for a rulebook row, 0 for a solicitation row -- the sort key itself."""
    return [1 if f.get("rulebook") else 0 for f in findings]


def test_section_check_lists_the_solicitations_rules_first():
    result = draft_review.review_section(
        TEXT, section="project_summary", rulebook=PAPPG,
        profile=_profile(), use_ai=False)
    flags = _sources(result["findings"])
    assert 0 in flags and 1 in flags, "fixture needs both sources"
    assert flags == sorted(flags), [
        (f.get("rulebook") or "solicitation", f["id"]) for f in result["findings"]]


def test_draft_review_orders_them_the_same_way():
    """Both entry points share one engine; they must not disagree on screen."""
    result = draft_review.review_draft(TEXT, profile=_profile(), use_ai=False)
    ps = [f for f in result["findings"] if f.get("section") == "project_summary"]
    flags = _sources(ps)
    assert 0 in flags and 1 in flags, "fixture needs both sources"
    assert flags == sorted(flags), [
        (f.get("rulebook") or "solicitation", f["id"]) for f in ps]


def test_requirement_order_survives_inside_each_group():
    """A stable two-level sort: source first, then the order the requirements
    were stored in.

    The PAPPG group is what proves the SECOND key does work. Findings are built
    deterministic-pass-first, so the raw order puts `pappg_ps_one_page` (a code
    check) second, while the rulebook stores it LAST. Only the requirement-order
    key restores the authored sequence -- assert on the solicitation rows alone
    and a stable sort makes the key look load-bearing when it is not, which
    mutation-testing caught.
    """
    result = draft_review.review_section(
        TEXT, section="project_summary", rulebook=PAPPG,
        profile=_profile(), use_ai=False)
    sol = [f["id"] for f in result["findings"] if not f.get("rulebook")]
    assert sol.index("sol_ps_loi") < sol.index("sol_ps_title"), sol

    pappg = [f["id"] for f in result["findings"] if f.get("rulebook")]
    authored = [r["id"] for r in rb.rules_for(PAPPG, tier="basic")
                if r.get("section") == "project_summary"]
    assert pappg == [i for i in authored if i in pappg], (pappg, authored)


def test_ordering_does_not_change_which_rules_are_checked():
    """Sequence only. A sort that dropped or added a row would be a different
    change entirely, and the score would move with it."""
    result = draft_review.review_section(
        TEXT, section="project_summary", rulebook=PAPPG,
        profile=_profile(), use_ai=False)
    ids = [f["id"] for f in result["findings"]]
    assert len(ids) == len(set(ids)), "a row was duplicated"
    assert {"sol_ps_loi", "sol_ps_title"} <= set(ids), sorted(ids)
    basics = {r["id"] for r in rb.rules_for(PAPPG, tier="basic")
              if r.get("section") == "project_summary"}
    assert basics, "filter matched nothing -- the assertion below would be vacuous"
    assert basics <= set(ids), sorted(basics - set(ids))


def test_draft_review_normalises_order_even_if_the_profile_stored_it_wrongly():
    """The `review_draft` half of the sort is DEFENCE, and mutation-testing
    showed nothing exercised it: `build_generic` already appends baseline rows
    last, so reverting that site alone changed no result. This builds a profile
    with the rulebook rows stored FIRST -- the state the sort exists to correct
    -- so the guard can actually fail.

    Worth keeping rather than deleting: the two entry points must not drift, and
    a stored profile's row order is not something this function should have to
    trust. Same posture as the `apply_delegation` guard CLAUDE.md records as
    defence-in-depth that was not yet reachable.
    """
    good = _profile()
    rows = list(good["requirements"])
    flipped = ([r for r in rows if r.get("rulebook")]
               + [r for r in rows if not r.get("rulebook")])
    assert flipped != rows, "fixture did not actually reorder anything"
    profile = {**good, "requirements": flipped}

    result = draft_review.review_draft(TEXT, profile=profile, use_ai=False)
    ps = [f for f in result["findings"] if f.get("section") == "project_summary"]
    flags = _sources(ps)
    assert 0 in flags and 1 in flags, "fixture needs both sources"
    assert flags == sorted(flags), [
        (f.get("rulebook") or "solicitation", f["id"]) for f in ps]
