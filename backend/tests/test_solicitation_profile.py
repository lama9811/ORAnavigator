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
