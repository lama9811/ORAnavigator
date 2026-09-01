""""Each reference includes a year of publication" is decided by code.

WHY. References Cited had one scored rule -- "Citations follow accepted
scholarly practice" -- so the section's whole percentage was one model verdict
on the vaguest sentence in the rulebook. A denominator of 1 can only ever print
100, 50, 0 or nothing, which reads as a measurement and is not one.

NSF's sentence is far more concrete than the one being scored:

    "Each reference must include the names of all authors (in the same sequence
    in which they appear in the publication), the article and journal title,
    book title, volume number, page numbers, and year of publication."

Of those elements the YEAR is the one that is unambiguously checkable: it is a
four-digit token, it is either present in an entry or it is not, and no ground
truth about the cited work is needed to tell. Author sequence, by contrast, is
checkable by nothing we have -- verifying it needs the publication itself -- so
it stays off rather than being handed to a model that would have to guess.

MEASURED before choosing the shape, on the References Cited section of the real
NSF proposal in backend/sample_proposals (nsf-ej-idss-planning-proposal.pdf) and
on reference lists in APA and IEEE style:

    APA (3 entries)   3/3 carry a year
    IEEE (3 entries)  3/3 carry a year
    real NSF sample   2/4 carry a year -- [1] ends "Current edition." and
                      [3] ends "Technical documentation."

The real sample is the point: those two entries genuinely lack a year of
publication, which is exactly the incompleteness NSF's sentence names. The rule
finds a true defect in a real awarded proposal on its first run.

WHAT THIS DELIBERATELY DOES NOT DO. It does not check "bibliographic citations
only". That was measured too, and no deterministic signal separated a real
bibliography from narrative prose: counting prose sentences flags every list
whose article titles are long (real NSF sample 3, APA 3 -- same as narrative),
and counting finite-verb function words misses nominal-style narrative entirely
("Types of data. Requirements registers, ..." scores 0). It is a judgement about
meaning, so it stays a semantic row for the model to read.
"""

from services import rulebook_baseline as rb
from services import rulebook_checks as rc


SECTION = "references_cited"

# Verbatim from backend/sample_proposals/nsf-ej-idss-planning-proposal.pdf,
# including the line wrapping pdfplumber produces, because entries wrapping
# mid-title is the shape the entry splitter has to survive.
REAL_NSF_SAMPLE = """[1] National Science Foundation. Proposal & Award Policies & Procedures Guide (PAPPG). Current
edition.
[2] National Academies of Sciences, Engineering, and Medicine. Reproducibility and Replicability in
Science. The National Academies Press, 2019.
[3] U.S. EPA. EJScreen: Environmental Justice Screening and Mapping Tool. Technical
documentation.
[4] Wilkinson, M. et al. "The FAIR Guiding Principles for scientific data management and
stewardship." Scientific Data 3, 160018 (2016)."""

APA = """Smith, J. A., & Doe, R. B. (2020). Machine learning approaches for predicting protein structure in cellular environments. Journal of Computational Biology, 27(4), 512-530.
Chen, L., Kumar, P., & Osei, A. (2019). A longitudinal study of groundwater contamination in post-industrial urban watersheds. Environmental Science & Technology, 53(11), 6201-6215.
Nguyen, T. (2021). Rethinking equity in algorithmic decision systems for public health. Nature Human Behaviour, 5, 1123-1135."""

IEEE = """[1] A. Patel and M. Rivera, "Sparse coding for hyperspectral unmixing," IEEE Trans. Geosci. Remote Sens., vol. 58, no. 3, pp. 1702-1714, 2020.
[2] K. Yamada, "Robust control of networked systems," in Proc. IEEE CDC, 2018, pp. 44-51.
[3] R. Okonkwo, Foundations of Data Stewardship. MIT Press, 2022."""

NO_YEARS = """National Science Foundation. Proposal & Award Policies & Procedures Guide. Current edition.
U.S. EPA. EJScreen: Environmental Justice Screening and Mapping Tool. Technical documentation.
World Health Organization. Guidance on research ethics. In press."""


def _ctx(text):
    return {"spans": {SECTION: {"text": text, "marker": "References Cited",
                                "start": 0}}}


def _req():
    return next(r for r in rb.rules_for("the PAPPG", SECTION)
                if r["id"] == "pappg_rc_year")


def _run(text):
    return rc.rb_citation_year(_ctx(text), _req())


# ── the rule is wired as a deterministic, scored row ────────────────────────

def test_the_year_rule_is_deterministic_and_scored():
    """It is code, and it counts -- the whole point is a real denominator."""
    req = _req()
    assert req["kind"] == "deterministic", req
    assert req["check"] == "rb_citation_year", req
    assert req["scored"] is True, req


def test_the_year_rule_quotes_nsfs_own_sentence():
    assert "year of publication" in _req()["source"]


# ── entries that carry a year ───────────────────────────────────────────────

def test_apa_references_all_carry_a_year():
    status, note, _ = _run(APA)
    assert status == "addressed", (status, note)
    assert "3" in note


def test_ieee_references_all_carry_a_year():
    status, note, _ = _run(IEEE)
    assert status == "addressed", (status, note)


# ── entries that do not ─────────────────────────────────────────────────────

def test_the_real_nsf_sample_is_partial_with_two_of_four_missing():
    """The measurement this rule was built on. Entries [1] and [3] end
    "Current edition." and "Technical documentation." -- no year of
    publication, which is precisely what NSF's sentence asks for."""
    status, note, quote = _run(REAL_NSF_SAMPLE)
    assert status == "partial", (status, note)
    assert "2 of 4" in note, note
    assert quote, "a finding must quote the text it is about"


def test_no_years_at_all_is_not_found():
    status, note, _ = _run(NO_YEARS)
    assert status == "not_found", (status, note)


# ── the shapes that must not produce a false verdict ────────────────────────

def test_a_wrapped_entry_counts_once_not_twice():
    """pdfplumber wraps a long entry across lines. Counting lines instead of
    entries would report "1 of 2 missing" on a single complete reference."""
    wrapped = ('Wilkinson, M. et al. "The FAIR Guiding Principles for '
               'scientific data management and\nstewardship." Scientific Data '
               '3, 160018 (2016).')
    status, note, _ = _run(wrapped)
    assert status == "addressed", (status, note)
    assert "1" in note, note


def test_a_section_that_was_never_located_is_not_a_failure():
    """could_not_locate leaves the denominator -- it never reads as a defect."""
    status, _, _ = rc.rb_citation_year({"spans": {}}, _req())
    assert status == "could_not_locate", status


def test_an_empty_section_does_not_claim_every_reference_passed():
    status, _, _ = _run("   \n  \n")
    assert status != "addressed", "no entries must never read as all-complete"


def test_a_section_heading_is_not_counted_as_a_reference():
    """Caught by test_verdict_without_a_score.py on the first full run: the
    span can begin with the heading line, and counting it as an entry reported
    "1 of 2 references have no year" about a single complete reference. Lines
    before the first entry marker are preamble, not references."""
    text = ("References Cited\n\n"
            "[1] F. A. Berezin and V. N. Tolstoy. The Group with Grassmann "
            "Structure. Communications in Mathematical Physics, 1981.\n")
    status, note, _ = _run(text)
    assert status == "addressed", (status, note)
    assert "All 1 reference" in note, note
