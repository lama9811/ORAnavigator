from services import solicitation_profile as sp


def test_aliases_include_the_label_and_its_denumbered_form():
    aliases = sp.aliases_for("II. Project Description")
    assert "ii. project description" in aliases
    assert "project description" in aliases


def test_sections_are_built_from_the_requirement_rows_own_section_values():
    reqs = [
        {"id": "a", "label": "Research goals", "section": "project_description",
         "kind": "semantic", "scored": True, "source": "x", "why": "", "keywords": []},
        {"id": "b", "label": "Summary asks", "section": "project_summary",
         "kind": "semantic", "scored": True, "source": "y", "why": "", "keywords": []},
    ]
    sections = sp.sections_from(reqs)
    assert set(sections) == {"project_description", "project_summary"}
    assert sections["project_description"]["label"] == "Project Description"
    assert "project description" in sections["project_description"]["aliases"]


def test_sections_also_come_from_page_limits_and_attachments():
    sections = sp.sections_from([], page_limits={"data_management_plan": 2},
                                attachments=["Biosketch"])
    assert "data_management_plan" in sections
    assert "biosketch" in sections


def test_nothing_in_the_profile_module_knows_about_any_named_solicitation():
    # The point of the whole change: one path, no funder-specific branch. A
    # regression here would be someone re-adding a from_nsf()/from_nih() helper.
    import inspect
    import re
    src = inspect.getsource(sp).lower()
    # \b on "eir" is load-bearing: a bare substring test matches "their".
    for token in (r"23-598", r"\beir\b", r"\bhbcu\b", r"excellence in research"):
        assert not re.search(token, src), f"funder-specific token in profile module: {token}"


def test_requirements_for_none_returns_the_whole_document_rows():
    reqs = [
        {"id": "a", "label": "A", "section": None, "kind": "semantic",
         "scored": True, "source": "x", "why": "", "keywords": []},
        {"id": "b", "label": "B", "section": "project_summary", "kind": "semantic",
         "scored": True, "source": "y", "why": "", "keywords": []},
    ]
    profile = sp.make_profile(id="X", title="X", sections={}, requirements=reqs)
    assert [r["id"] for r in sp.requirements_for(profile, None)] == ["a"]


# ── one part of a proposal, named two ways ──────────────────────────────────
#
# The section universe is assembled from three sources that use DIFFERENT
# vocabulary for the same thing: requirement rows carry a canonicalised key
# (filler words stripped), while required-attachment names arrive verbatim from
# the solicitation. So one Budget Justification became two sections —
# `budget_justification` and `budget_and_budget_justification` — and one Letter
# of Intent became `letter_intent` and `letter_of_intent`.
#
# Not cosmetic. Measured on a real proposal: the attachment check looks its
# section up by the verbatim key, never finds a span (the PI wrote the short
# heading), and its fallbacks search for the LONG name as a whole line — so a
# Budget Justification sitting in the draft was reported "No Budget and Budget
# Justification found", i.e. a required attachment declared MISSING when it is
# present. That is the error class this tool exists to prevent.

def test_one_section_named_two_ways_is_merged():
    sections = sp.sections_from(
        [{"section": "budget_justification"}],
        attachments=["Budget and Budget Justification"])
    assert len(sections) == 1, f"expected one section, got {list(sections)}"
    assert "budget_justification" in sections


def test_the_merged_section_keeps_BOTH_headings_as_aliases():
    """The point of merging rather than dropping: a PI may type either name, and
    the locate stage matches whole heading lines against these aliases."""
    sections = sp.sections_from(
        [{"section": "budget_justification"}],
        attachments=["Budget and Budget Justification"])
    aliases = sections["budget_justification"]["aliases"]
    assert "budget justification" in aliases
    assert "budget and budget justification" in aliases


def test_merging_fixes_a_section_no_PI_could_ever_match():
    """`letter_intent` is what canonicalisation produces, and its only alias was
    "letter intent" — which nobody writes. The solicitation's own "Letter of
    Intent" was a SEPARATE section, so the seven LOI requirements sat under a
    heading that could not be located."""
    sections = sp.sections_from(
        [{"section": "letter_intent"}], attachments=["Letter of Intent"])
    assert len(sections) == 1
    assert "letter of intent" in sections["letter_intent"]["aliases"]


def test_sections_that_merely_share_words_are_NOT_merged():
    """The guard. Containment would fold a genuinely distinct section into its
    parent; only the same SET of content words counts as the same section."""
    sections = sp.sections_from(
        [{"section": "project_description"}],
        attachments=["Project Description Supplementary Documents"])
    assert len(sections) == 2


def test_resolve_finds_the_merged_section_by_either_name():
    sections = sp.sections_from(
        [{"section": "budget_justification"}],
        attachments=["Budget and Budget Justification"])
    assert sp.resolve_section_key(sections, "Budget and Budget Justification") \
        == "budget_justification"
    assert sp.resolve_section_key(sections, "Budget Justification") \
        == "budget_justification"
    assert sp.resolve_section_key(sections, "Data Management Plan") is None


def test_the_two_filler_sets_have_not_drifted():
    """`_SECTION_FILLER` is duplicated in solicitation_requirements on purpose —
    this module is data-only and importing that one would drag gemini_client in.
    Duplication is fine; SILENT divergence is not, because the two sets decide
    whether one part of a proposal lands on one section key or two."""
    from services import solicitation_requirements as sr
    assert sp._SECTION_FILLER == sr._SECTION_FILLER
