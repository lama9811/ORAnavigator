"""Every rule handed to a review comes back with a row. No rule may vanish.

WHY THIS EXISTS. "Make sure it checks every rule, not just guess it." The engine
already refuses to INFER: when the model omits a row despite `_review_system`
rule 2, `_review_batch` returns `unclear` with a note saying so rather than
reading the model's silence as absence from the draft. But nothing asserted the
stronger property across BOTH entry points — that the set of rows out is exactly
the set of rules in.

That property is easy to break and silent when broken. A rule has been lost
three separate ways already: a section key that lost a merge (45 PAPPG rules
pointed at a key `sections_from` had renamed), a picker/profile key mismatch
(6 scored solicitation rows), and two names sharing no word-set (2 more). Every
time the screen looked normal, because the surviving rules filled the page.

MEASURED on the awarded package, four offered sections, no solicitation
attached: **19 rules, 19 judged, 0 unclear**. This file is what keeps that true.
"""
import collections

# A status meaning a judgement was actually made ABOUT THE DRAFT. The rest are
# honest refusals -- `not_checked` (not a property of text), `could_not_locate`
# (we never found the section), `delegated` (not ours), `not_in_draft` (no
# document can contain it), `unclear` (nobody looked). Each is out of the
# score's denominator, and each must still appear as a ROW.
JUDGED = {"addressed", "partial", "not_found", "clear", "flagged"}


def _profile(n, section="project_description"):
    from services import solicitation_profile as sp
    return sp.build_generic({}, [{
        "id": f"sol_r{i}", "section": section,
        "label": f"Describe item {i}", "kind": "semantic", "scored": True,
        "source": f"The proposal must describe item {i}.", "why": "",
        "keywords": [],
    } for i in range(n)], id="NSF 99-999", title="A generic solicitation")


DRAFT = ("Project Description\n"
         "We describe the work, its aims, and the plan for students.\n")


def test_every_rule_comes_back_from_the_whole_package_review():
    from services import draft_review
    profile = _profile(30)
    ids_in = {r["id"] for r in profile["requirements"]}
    out = draft_review.review_draft(DRAFT, profile=profile)
    ids_out = [f["id"] for f in out["findings"]]

    assert ids_in <= set(ids_out), f"vanished: {sorted(ids_in - set(ids_out))}"
    dupes = [i for i, c in collections.Counter(ids_out).items() if c > 1]
    assert not dupes, f"reported twice: {dupes}"


def test_every_rule_comes_back_from_a_section_check():
    from services import draft_review
    profile = _profile(30)
    ids_in = {r["id"] for r in profile["requirements"]}
    out = draft_review.review_section(DRAFT, section="project_description",
                                      rulebook="the PAPPG", profile=profile)
    ids_out = [f["id"] for f in out["findings"]]

    assert ids_in <= set(ids_out), f"vanished: {sorted(ids_in - set(ids_out))}"
    dupes = [i for i, c in collections.Counter(ids_out).items() if c > 1]
    assert not dupes, f"reported twice: {dupes}"


def test_a_rule_the_model_skipped_is_reported_unassessed_not_absent():
    """The model's silence is a fact about the MODEL, never about the draft.

    Inferring `not_found` from an omitted row would tell a PI they had not
    written something they had written -- the failure this whole module spends
    its guards preventing."""
    from unittest import mock
    from services import draft_review
    reqs = [{"id": "a", "label": "Item A", "section": "project_description",
             "kind": "semantic", "scored": True, "source": "Describe A.",
             "why": "", "keywords": []},
            {"id": "b", "label": "Item B", "section": "project_description",
             "kind": "semantic", "scored": True, "source": "Describe B.",
             "why": "", "keywords": []}]
    span = {"text": DRAFT, "marker": "Project Description", "start": 0}
    sections = {"project_description": {"label": "Project Description",
                                        "aliases": []}}

    # The model answers about "a" and silently drops "b".
    with mock.patch.object(draft_review.gemini_client, "generate_json",
                           return_value={"findings": [
                               {"id": "a", "status": "addressed",
                                "evidence": "We describe the work",
                                "note": "n", "suggestion": "s"}]}):
        out = draft_review._review_batch("project_description", span, reqs,
                                         sections, "NSF 99-999")

    by_id = {f["id"]: f for f in out}
    assert set(by_id) == {"a", "b"}, "a skipped rule vanished entirely"
    assert by_id["b"]["status"] == "unclear", by_id["b"]["status"]
    assert by_id["b"]["status"] not in JUDGED
    assert "did not return a result" in by_id["b"]["note"]


def test_a_failed_model_call_leaves_every_rule_accounted_for():
    """An outage must not shrink the list. Every rule still gets a row."""
    from unittest import mock
    from services import draft_review
    profile = _profile(12)
    ids_in = {r["id"] for r in profile["requirements"]}
    with mock.patch.object(draft_review.gemini_client, "generate_json",
                           side_effect=RuntimeError("Vertex down")):
        out = draft_review.review_section(DRAFT, section="project_description",
                                          rulebook="the PAPPG", profile=profile)
    ids_out = {f["id"] for f in out["findings"]}
    assert ids_in <= ids_out, f"vanished in an outage: {sorted(ids_in - ids_out)}"


# ── and the count is REPORTED, not left to be inferred ──────────────────────

def _f(rid, status, scored=True):
    return {"id": rid, "status": status, "scored": scored, "label": rid,
            "section": "project_description", "note": "", "evidence": ""}


def test_the_score_says_how_many_rules_nobody_judged():
    """"92% of the 6 requirements" never said whether 6 was the whole list."""
    from services.draft_review import score
    s = score([_f("a", "addressed"), _f("b", "partial"),
               _f("c", "unclear"), _f("d", "could_not_locate"),
               _f("e", "not_checked"), _f("f", "delegated"),
               _f("g", "not_in_draft")], solicitation_id="NSF 99-999")
    assert s["assessed"] == 2, s["assessed"]
    assert s["not_assessed"] == 5, s["not_assessed"]


def test_an_advisory_row_is_not_counted_as_unassessed():
    """A conditional the draft was never subject to WAS judged; it just does not
    score. Counting it as skipped would report work the reviewer did as work it
    refused."""
    from services.draft_review import score
    s = score([_f("a", "addressed"), _f("b", "not_found", scored=False)],
              solicitation_id="NSF 99-999")
    assert s["assessed"] == 1
    assert s["not_assessed"] == 0, s["not_assessed"]


def test_a_fully_checked_section_reports_nothing_unassessed():
    from services.draft_review import score
    s = score([_f("a", "addressed"), _f("b", "clear"), _f("c", "not_found")],
              solicitation_id="NSF 99-999")
    assert (s["assessed"], s["not_assessed"]) == (3, 0)


# ── a section that is not part of the package is not "missing from it" ──────

def test_a_section_scoped_out_of_the_package_is_not_shown_as_missing():
    """The Letter of Intent, reported by a PI reading the section map.

    NSF 23-598 requires one — as a SEPARATE submission with its own earlier
    deadline, filed by the AOR months before the proposal. `draft_scope` already
    gets the SCORE right (its rules come back `not_in_draft`, out of the
    denominator), but the screen still listed it among the sections "not found
    in what you pasted", which teaches a PI something false about what a
    proposal contains and invites them to go and add one."""
    from services.draft_review import _wholly_out_of_package
    findings = [{"section": "letter_intent", "status": "not_in_draft"},
                {"section": "letter_intent", "status": "not_in_draft"}]
    assert _wholly_out_of_package(findings, "letter_intent") is True


def test_a_section_with_one_real_rule_is_still_reported_missing():
    """The mirror. One assessable rule and the section stays on the map — a
    genuinely absent section must never be hidden."""
    from services.draft_review import _wholly_out_of_package
    findings = [{"section": "supplementary_document", "status": "not_in_draft"},
                {"section": "supplementary_document", "status": "could_not_locate"}]
    assert _wholly_out_of_package(findings, "supplementary_document") is False


def test_a_section_with_no_rules_of_its_own_is_left_alone():
    """A required attachment puts a section in the universe carrying no
    requirement rows. Hiding those would stop reporting a missing attachment —
    the compliance rejection this tool exists to prevent."""
    from services.draft_review import _wholly_out_of_package
    assert _wholly_out_of_package([], "letter_of_institutional_support") is False


# ── the prompt carries the completeness contract, not just the code ─────────

def test_the_reviewer_is_told_to_read_all_of_it_and_return_every_row():
    """Both halves of "don't skip anything", asserted on the prompt itself.

    The code already refuses to infer: an omitted row becomes `unclear` rather
    than being read as absence. But `unclear` is absent from `_CREDIT`, so the
    rule leaves the score's DENOMINATOR — a skipped row silently changes the
    percentage instead of showing up as a gap. With section location now
    deterministic, that is the last remaining way one unchanged draft can score
    two ways, so the instruction is worth pinning."""
    from services.draft_review import _review_system
    p = _review_system("NSF 99-999").lower()

    assert "read every line" in p, "nothing tells the reviewer to read to the end"
    assert "do not stop at the first match" in p
    assert "exactly one row per id" in p
    # The artifacts this repo has measured breaking a quote mid-sentence.
    assert "page 12 of 56" in p or "mid-sentence" in p


def test_the_reviewer_is_still_told_never_to_invent():
    """The counterweight. "Do not skip anything" pushes a model toward
    answering from the requirement's own wording when the draft is silent, and
    a fabricated pass is worse than an honest gap."""
    from services.draft_review import _review_system
    p = _review_system("NSF 99-999").lower()
    assert "never infer" in p
    assert "judge only the words in front of you" in p
