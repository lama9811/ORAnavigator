"""A percentage computed over pages we cannot confirm we read describes our
reading, not the draft. Same rule the AI-outage path already follows -- added
after an outage rendered a section as 100%, green, "No problems found"."""
from services import draft_review as dr
from services import solicitation_profile as sp

DRAFT = "Project Summary\nThis proposal provides an overview of the planned work."


def _profile():
    """Built through build_generic, the same path load_solicitation_profile
    uses in production -- a hand-written profile dict would not match what a
    real review actually receives and would prove nothing about it. Mirrors
    tests/test_draft_review_basics_only.py's fixture pattern."""
    rows = [
        {"id": "r1", "section": "project_summary", "section_label": "Project Summary",
         "label": "Include an overview", "kind": "semantic", "scored": True,
         "source": "Include an overview.", "why": "", "keywords": []},
    ]
    return sp.build_generic({}, rows, id="NSF 23-598", title="t")


def test_a_complete_ledger_leaves_the_score_alone():
    ledger = [{"page": 1, "section": "project_summary", "source": "model"},
              {"page": 2, "section": None, "source": "blank"}]
    out = dr.review_draft(DRAFT, profile=_profile(), use_ai=False, ledger=ledger)
    assert out["pages_unaccounted"] == []


def test_an_unaccounted_page_withholds_the_score():
    ledger = [{"page": 1, "section": "project_summary", "source": "model"},
              {"page": 2, "section": None, "source": "unassigned"}]
    out = dr.review_draft(DRAFT, profile=_profile(), use_ai=False, ledger=ledger)
    assert out["score"] is None
    assert out["pages_unaccounted"] == [2]
    assert "2" in (out["message"] or "")


def test_the_ledger_rides_on_the_result():
    ledger = [{"page": 1, "section": "project_summary", "source": "structure"}]
    out = dr.review_draft(DRAFT, profile=_profile(), use_ai=False, ledger=ledger)
    assert out["page_ledger"] == ledger


def test_no_ledger_changes_nothing():
    """Pasted text has no pages. This must not withhold a score."""
    out = dr.review_draft(DRAFT, profile=_profile(), use_ai=False)
    assert out["pages_unaccounted"] == []
    assert out["page_ledger"] is None


def test_a_toc_mismatch_rides_along_but_does_not_withhold():
    mismatch = [{"section": "biographical_sketch", "label": "Biographical Sketch",
                 "ledger_pages": 3, "toc_pages": 2}]
    ledger = [{"page": 1, "section": "project_summary", "source": "model"}]
    out = dr.review_draft(DRAFT, profile=_profile(), use_ai=False,
                          ledger=ledger, toc_mismatch=mismatch)
    assert out["toc_mismatch"] == mismatch
    assert out["pages_unaccounted"] == []


def test_a_page_gap_withholds_a_REAL_ai_score(monkeypatch):
    """The gate that matters, and the one every other test here misses: with
    `use_ai=False` in every test above, `score = score(...) if ai_used and
    pages_ok else None` is `None` for the trivial reason that `ai_used` is
    already `False` -- the withholding itself is never exercised, and
    `pages_ok` could be hard-coded `True` without a single assertion above
    catching it.

    This drives `review_draft` with `use_ai=True` and a stubbed model, so the
    AI half is genuinely on and genuinely finds the requirement: with a
    complete ledger the score is a real number, and with the SAME draft and
    profile but one `unassigned` page, the score is withheld anyway. That
    pairing is what actually pins the gate -- a working-looking answer with a
    silently corrupted denominator is exactly the incident this feature
    exists to prevent."""
    from services import gemini_client

    def stub(prompt, **kw):
        # Answers whichever call this module makes -- locate_sections reads
        # "sections", _review_batch reads "findings". One stub, because the
        # module doesn't distinguish its calls by anything this test needs to
        # key on.
        return {
            "sections": {"project_summary": "Project Summary"},
            "findings": [{
                "id": "r1", "status": "addressed",
                "note": "The overview is present.",
                "evidence": "This proposal provides an overview of the planned work.",
                "suggestion": "Name the specific research questions.",
            }],
        }

    monkeypatch.setattr(gemini_client, "generate_json", stub)

    complete = [{"page": 1, "section": "project_summary", "source": "model"}]
    ok = dr.review_draft(DRAFT, profile=_profile(), use_ai=True, ledger=complete)
    assert ok["ai"] is True, "the stub was never reached; this test proves nothing"
    assert ok["score"] is not None, "expected a real score with a complete ledger"
    assert isinstance(ok["score"]["percent"], (int, float))
    assert ok["score"]["percent"] == 100

    gap = [{"page": 1, "section": "project_summary", "source": "model"},
           {"page": 2, "section": None, "source": "unassigned"}]
    withheld = dr.review_draft(DRAFT, profile=_profile(), use_ai=True, ledger=gap)
    assert withheld["ai"] is True
    assert withheld["score"] is None, (
        "the AI produced a real answer and the page gap did not suppress it")
