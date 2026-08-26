"""A full NSF package is longer than one prompt, and the tail must not vanish.

WHY THIS FILE EXISTS
--------------------
`MAX_DRAFT_CHARS` caps what reaches the model. It was 120,000, and a real
awarded NSF 23-598 package (56 pages, Morgan State, 2026) extracts to **145,023
characters** — so the last 25,023 were never offered to the locate stage at all.
Measured against that document: at 120,000 the locate stage found **8** sections;
given the whole text it found **10**, and the two it gained were exactly the ones
past the cut — Special Information/Supplementary Documents (heading at 131,720)
and the Letters of Collaboration (136,314). Every requirement filed under them
reported "Not located" and left the score's denominator.

Nothing anywhere said so. That is the same failure this repo already records for
`solicitation_extractor.extract_from_text` ("a silent stop reads as 'we found
everything' when it means 'we stopped looking'"), which is why it reports
`truncated` / `input_chars`. A review does not, so the two halves of the same
product treat the same problem differently.

Both halves are needed and neither substitutes for the other: a bigger cap is
still a cap, and a cap that is never reported is invisible however large it is.
"""

import json

from services import draft_review
from services import solicitation_profile as sp


TAIL_HEADING = "Letters of Collaboration"


def _long_package(total_chars: int) -> str:
    """A package whose LAST section heading sits beyond `MAX_DRAFT_CHARS`.

    The filler is prose rather than one repeated character so the tail heading
    is reached by length the way a real package reaches it, and so the locate
    prompt is not trivially compressible.
    """
    head = ("Project Summary\n\nOverview\nWe study estuarine salinity sensing.\n\n"
            "Project Description\n\n")
    filler = ("The proposed work develops zwitterionic networks and validates "
              "them in the field over twelve months of deployment.\n")
    body = filler * (max(0, total_chars - len(head)) // len(filler) + 1)
    return head + body[:max(0, total_chars - len(head))] + f"\n\n{TAIL_HEADING}\n\nDr. Kirsch confirms intent to collaborate.\n"


def _profile():
    extracted = [
        {"id": "sol_pd", "section": "project_description",
         "label": "Describe the work", "kind": "semantic", "scored": True,
         "source": "The Project Description must describe the work.",
         "why": "", "keywords": []},
        {"id": "sol_letters", "section": "collaboration_letter",
         "label": "Include letters of collaboration", "kind": "semantic",
         "scored": True,
         "source": "Letters of Collaboration must be included.",
         "why": "", "keywords": []},
    ]
    return sp.build_generic({}, extracted, id="NSF 23-598", title="t")


def test_the_locate_stage_is_offered_a_heading_past_the_old_cap():
    """The tail heading has to REACH the model, not merely exist in the file.

    Asserted at the boundary where the prompt is handed to Gemini, because that
    is the only place the truncation happens — `_locate_fallback` scans the full
    text either way, so a `use_ai=False` test passes no matter how small the cap
    is and would have watched this bug ship.
    """
    text = _long_package(130_000)
    assert len(text) > 120_000, "fixture no longer exceeds the old cap"

    seen = {}

    def spy(prompt, **kw):
        seen["prompt"] = prompt
        return None                      # fall through to the deterministic scan

    orig = draft_review.gemini_client.generate_json
    draft_review.gemini_client.generate_json = spy
    try:
        draft_review.locate_sections(text, _profile()["sections"], use_ai=True)
    finally:
        draft_review.gemini_client.generate_json = orig

    assert TAIL_HEADING in seen.get("prompt", ""), (
        f"the locate prompt was cut before the tail heading: it carried "
        f"{len(seen.get('prompt', ''))} chars of a {len(text)}-char package")


def test_a_paste_longer_than_the_cap_is_REPORTED_not_silently_cut():
    """A bigger cap is still a cap. When it bites, the review must say so.

    Same contract as `solicitation_extractor`'s `truncated` / `input_chars`:
    both numbers, so a PI can see how much of their package was read rather than
    being handed a completeness score computed over part of it.
    """
    over = draft_review.MAX_DRAFT_CHARS + 5_000
    result = draft_review.review_draft(_long_package(over), profile=_profile(),
                                       use_ai=False)

    assert result.get("truncated"), "an over-long paste was cut with no report"
    assert result["truncated"]["chars"] >= over
    assert result["truncated"]["read"] == draft_review.MAX_DRAFT_CHARS


def test_a_normal_package_reports_no_truncation():
    """The report must be ABSENT on a package that fitted, not a zeroed row —
    a warning that renders on every review stops being read, which is the same
    lesson as cutting the delegation caveat from four places to one."""
    result = draft_review.review_draft(_long_package(5_000), profile=_profile(),
                                       use_ai=False)
    assert result.get("truncated") is None
