"""A real page count beats a word-count estimate, and silences it.

THE BUG, seen on a real screen (2026-08-27)
-------------------------------------------
A PI uploaded a one-page Project Summary. The panel showed, at the same time:

    [clear]  Project Summary fits on one page
             "1 page, within the 1-page limit."

    length   563 words   102% of your one page
             "...about 102% of the space -- over the limit on most formatting.
              The page count comes from your PDF, so upload it to have this
              checked properly."

Both clauses of the second are false. They are not over -- pdfplumber counted
the PDF and it is one page -- and they had just uploaded the PDF it tells them
to upload.

WHY IT HAPPENED. `rb_page_limit` receives `ctx["pages"]`, the real count from
the upload path. `length_guidance` received only a word count and a page limit,
so it divided by WORDS_PER_PAGE and reported the estimate as a verdict. The real
count was in scope at the call site and was simply not passed.

THE RULE: where a real page count exists it is authoritative and the estimate
says nothing. Where there is none -- every paste, and all of `review_draft` --
the estimate is the only signal there is and is unchanged. That asymmetry is the
whole fix, and both halves have tests here, because silencing the paste case
would remove the only length signal a pasting PI ever gets.

Same family as the failures CLAUDE.md already records: an estimate rendered as a
verdict, and two parts of one screen contradicting each other.
"""

from services import draft_review
from services import section_guidance as sg
from services import solicitation_profile as sp


# ── the unit: what the estimate does when a real count exists ───────────────

def test_a_real_page_count_within_the_limit_silences_the_estimate():
    g = sg.length_guidance(563, page_limit=1, pages=1)
    assert g is not None, "the measurement itself must survive"
    assert not g.get("message"), g
    assert g.get("pct") is None, g
    assert g["pages"] == 1
    assert g["words"] == 563


def test_a_real_page_count_over_the_limit_also_silences_the_estimate():
    """`rb_page_limit` already reports "2 pages, over the 1-page limit" with the
    TRUE number. An estimate beside it can only offer a second, different number
    for one fact."""
    g = sg.length_guidance(1400, page_limit=1, pages=2)
    assert not g.get("message"), g
    assert g.get("pct") is None, g
    assert g["pages"] == 2


def test_without_a_real_page_count_the_estimate_is_unchanged():
    """The paste case, and the reason this is a third argument rather than a
    deletion. Here the estimate is the only signal there is."""
    g = sg.length_guidance(563, page_limit=1)
    assert g["pct"] == 102, g
    assert "over the limit on most formatting" in g["message"]
    assert "upload it" in g["message"]
    assert g.get("pages") is None


def test_a_short_section_without_a_page_count_still_gets_its_measurement():
    g = sg.length_guidance(76, page_limit=1)
    assert g["pct"] == 14, g
    assert "no minimum" in g["message"]


def test_no_page_limit_is_still_nothing_to_say():
    assert sg.length_guidance(563, page_limit=None, pages=1) is None
    assert sg.length_guidance(0, page_limit=1, pages=1) is None


# ── the wiring: what a PI actually gets ─────────────────────────────────────

TEXT = ("Overview\n" + "word " * 560 + "\n\n"
        "Intellectual Merit\nThis advances the field.\n\n"
        "Broader Impacts\nStudents are trained.\n")


def _profile():
    row = {"id": "sol_ps", "section": "project_summary",
           "label": "Include the LOI number", "kind": "semantic", "scored": True,
           "source": ("The Project Summary must include the LOI number in addition "
                      "to all the requirements outlined in the PAPPG."),
           "why": "", "keywords": []}
    return sp.build_generic({}, [row], id="NSF 23-598", title="t")


def test_an_uploaded_pdf_does_not_get_told_to_upload_a_pdf():
    result = draft_review.review_section(
        TEXT, section="project_summary", rulebook="the PAPPG",
        profile=_profile(), pages=1, use_ai=False)
    length = (result.get("guidance") or {}).get("length") or {}
    assert not length.get("message"), length
    assert length.get("pages") == 1, length


def test_a_paste_still_gets_the_full_length_message():
    result = draft_review.review_section(
        TEXT, section="project_summary", rulebook="the PAPPG",
        profile=_profile(), use_ai=False)
    length = (result.get("guidance") or {}).get("length") or {}
    assert "over the limit on most formatting" in (length.get("message") or ""), length


def test_the_page_rule_and_the_length_line_cannot_contradict_each_other():
    """The actual regression. A section whose deterministic page rule came back
    `clear` must not carry a line calling it over the limit."""
    result = draft_review.review_section(
        TEXT, section="project_summary", rulebook="the PAPPG",
        profile=_profile(), pages=1, use_ai=False)
    page_rows = [f for f in result["findings"]
                 if "page" in f["label"].lower() and f["status"] in {"clear", "flagged"}]
    assert page_rows, "fixture lost the deterministic page rule"
    assert page_rows[0]["status"] == "clear", page_rows[0]
    message = ((result.get("guidance") or {}).get("length") or {}).get("message") or ""
    assert "over the limit" not in message, message
