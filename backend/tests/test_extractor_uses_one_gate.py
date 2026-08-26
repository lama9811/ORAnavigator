"""The requirement extractor must verify quotes the SAME way everything else does.

WHAT WENT WRONG
---------------
`solicitation_requirements` contains TWO definitions of "this quote is
verified". Line 677 calls `text_match.quote_in`. The main extraction path --
`absorb`, the one every chunk and every sweep round goes through -- reimplements
it as `normalize(row["source"]) not in haystack`, hoisting the normalised
document out of the loop.

That was a reasonable optimisation and it made the module the only place where
golden rule 2 has a second, private implementation. So when `quote_in` learned
to read dash-broken words (2026-08-26) and then to tolerate lost PDF ligatures,
the extractor got neither -- and it is the component that reads solicitations
out of typeset PDFs, which is precisely where both artifacts occur.

Measured on a real NSF solicitation the PI uploaded: "2 proposed requirements
were dropped because they could not be quoted from the document." Both were
genuine NSF rules whose quotes contained a word the PDF had mangled
(`justification` -> `justication`, the fi ligature lost). Fixing `quote_in`
changed nothing, because this path never calls it.

The fast path is KEPT -- re-normalising a 50,000-character document once per
row would be wasteful -- and `quote_in` is consulted only for the rows the fast
path would otherwise throw away, which is a handful.
"""

import pytest

from services import solicitation_requirements as sr


DOC = (
    "Program Description\n"
    "Funding requests outside the typical range should have corresponding\n"
    "detailed budget justications that demonstrate the relevance of the\n"
    "request to the project.\n"
    "Proposals must include a data management plan of no more than two pages.\n"
)


def _model_rows(*sources):
    return [{"label": f"Rule {i}", "section": "project_description",
             "kind": "semantic", "scored": True, "source": s,
             "why": "", "keywords": []} for i, s in enumerate(sources)]


def test_a_quote_the_pdf_mangled_is_no_longer_dropped(monkeypatch):
    """The exact row from the report. The model writes `justifications`; the
    document says `justications` because pdfplumber lost the ligature."""
    calls = {"n": 0}

    def fake_ask(prompt, system=None, key="requirements"):
        calls["n"] += 1
        if calls["n"] > 1:
            return []          # the sweep finds nothing new
        return _model_rows(
            "detailed budget justifications that demonstrate the relevance")

    monkeypatch.setattr(sr, "_ask", fake_ask)
    out = sr.extract_requirements(DOC)
    assert out["dropped_unverified"] == 0, out["dropped_unverified"]
    assert len(out["requirements"]) == 1, out["requirements"]


def test_a_genuinely_fabricated_quote_is_still_dropped(monkeypatch):
    """The gate must still bite. Widening it to cover a PDF artifact must not
    turn it into a rubber stamp."""
    calls = {"n": 0}

    def fake_ask(prompt, system=None, key="requirements"):
        calls["n"] += 1
        if calls["n"] > 1:
            return []
        return _model_rows("The proposal must include a letter from the Governor")

    monkeypatch.setattr(sr, "_ask", fake_ask)
    out = sr.extract_requirements(DOC)
    assert out["dropped_unverified"] == 1, out["dropped_unverified"]
    assert out["requirements"] == [], out["requirements"]


def test_an_exact_quote_still_takes_the_fast_path(monkeypatch):
    calls = {"n": 0}

    def fake_ask(prompt, system=None, key="requirements"):
        calls["n"] += 1
        if calls["n"] > 1:
            return []
        return _model_rows("a data management plan of no more than two pages")

    monkeypatch.setattr(sr, "_ask", fake_ask)
    out = sr.extract_requirements(DOC)
    assert out["dropped_unverified"] == 0
    assert len(out["requirements"]) == 1
