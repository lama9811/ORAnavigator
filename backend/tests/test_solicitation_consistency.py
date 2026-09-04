"""The same solicitation, read twice, must give the same answer.

MEASURED FIRST, 2026-09-04, on NSF 23-598 pulled through the app's own URL
fetcher (54,065 chars) and fed to the real code path byte-identically:

  * the CONTRACT read, 10 runs -> `deadline` came back 2023-07-13 eight times
    and 2023-10-17 twice. Both dates are real and in the document; the
    solicitation states a Letter of Intent date AND a full proposal date, and
    the model was being asked to collapse them into one field. That field
    drives internal_routing_deadline, the ICS feed and the Deadline Watcher
    emails, so two PIs uploading the same PDF planned three months apart.
    `_EXTRACT_SYSTEM` rule 3 already says "the SINGLE EARLIEST upcoming date" —
    the rule was right and the model disobeyed it 2 times in 10.

  * the REQUIREMENT read, 5 runs -> 36 / 40 / 46 / 48 / 52 rows over 7-10
    sections. Compared by 5-gram overlap of the verbatim quotes (the method
    tests/test_solicitation_requirements_recall.py uses, and NOT by id — the
    ids churn, measured id-jaccard 0.04-0.12), the five runs between them found
    54 distinct requirements and only 32 of them — 59% — appeared in every run.
    Ten appeared in exactly one run of five, among them "Comply with Build
    America, Buy America Act requirements" and "Exclude established PIs from
    HBCU-EiR". The checklist is a STORED SNAPSHOT, so whichever run a PI drew
    was frozen into their checklist and their Draft Review denominator.

  * the same runs invented sections named `due_date`, `eligibility` and
    `preparation` — the solicitation's own headings, which `_SYSTEM` rule 9
    forbids and `_DOCUMENT_HEADINGS` exists to drop — and spelled the budget
    section three ways (`budget`, `budget_justification`,
    `budget_budget_justification`).

A FIXED SEED IS NOT THE FIX and was tested rather than assumed: 10 seeded runs
held the deadline but attachments still swung 5 -> 8, so the call does not
become deterministic. Temperature is already 0.0. Same conclusion CLAUDE.md
already records for the review path.
"""
import json

import pytest

from services import solicitation_extractor as sx
from services import solicitation_profile as sp
from services import solicitation_requirements as sr


# ---------------------------------------------------------------------------
# 1. The deadline is COMPUTED, not chosen by the model.
# ---------------------------------------------------------------------------

def _gemini_returning(payload: dict):
    return lambda prompt_text, system_instruction=None: json.dumps(payload)


def test_the_deadline_is_the_earliest_of_the_stated_dates(monkeypatch):
    """The model may nominate whichever date it likes; code takes the earliest.

    This is the exact shape that flipped in measurement: the model reports both
    dates correctly and then puts the LATER one in the single field."""
    monkeypatch.setattr(sx, "_call_gemini", _gemini_returning({
        "deadline": "2023-10-17",          # the model's pick -- the LATER date
        "deadlines": [
            {"label": "Full Proposal", "date": "2023-10-17"},
            {"label": "Letter of Intent", "date": "2023-07-13"},
        ],
        "source_quotes": {},
    }))
    out = sx.extract_from_text("a solicitation")
    assert out["deadline"] == "2023-07-13"


def test_a_single_stated_deadline_is_left_alone(monkeypatch):
    monkeypatch.setattr(sx, "_call_gemini", _gemini_returning({
        "deadline": "2026-06-12",
        "deadlines": [{"label": "Full Proposal", "date": "2026-06-12"}],
        "source_quotes": {},
    }))
    assert sx.extract_from_text("a solicitation")["deadline"] == "2026-06-12"


def test_an_unusable_date_list_leaves_the_models_deadline_alone(monkeypatch):
    """Fails SAFE. A list we cannot read must never blank a real date."""
    monkeypatch.setattr(sx, "_call_gemini", _gemini_returning({
        "deadline": "2026-06-12",
        "deadlines": [{"label": "Rolling", "date": "whenever you like"}],
        "source_quotes": {},
    }))
    assert sx.extract_from_text("a solicitation")["deadline"] == "2026-06-12"


def test_no_date_list_at_all_is_unchanged(monkeypatch):
    """Every solicitation read before this change came back without the list."""
    monkeypatch.setattr(sx, "_call_gemini", _gemini_returning({
        "deadline": "2026-06-12",
        "source_quotes": {},
    }))
    assert sx.extract_from_text("a solicitation")["deadline"] == "2026-06-12"


def test_a_time_of_day_still_sorts_earliest_first(monkeypatch):
    monkeypatch.setattr(sx, "_call_gemini", _gemini_returning({
        "deadline": "2026-08-01",
        "deadlines": [
            {"label": "Full Proposal", "date": "2026-08-01T17:00:00-05:00"},
            {"label": "Letter of Intent", "date": "2026-07-02T17:00:00-05:00"},
        ],
        "source_quotes": {},
    }))
    assert sx.extract_from_text("a solicitation")["deadline"].startswith("2026-07-02")


# ---------------------------------------------------------------------------
# 2. The solicitation's own headings are not parts of a proposal.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("heading", [
    "Eligibility", "Eligibility Info", "Due Dates", "Due Date",
    "Preparation", "Proposal Preparation",
])
def test_a_solicitation_heading_is_not_a_proposal_section(heading):
    """Observed live in the 5-run measurement: due_date, eligibility and
    preparation were each filed as a section. A section naming no real part of
    a proposal can never be located in a draft, so every requirement under it
    reports "Not located" and drops out of the score's denominator -- recovered
    and then never checked, which is worse than not having it."""
    assert sr.canon_section(heading) is None


@pytest.mark.parametrize("real", [
    "Project Description", "Project Summary", "Background", "Introduction",
    "Results from Prior NSF Support", "Budget Justification",
    "Data Management Plan",
])
def test_a_real_proposal_section_is_not_dropped(real):
    """The MIRROR guard, and the dangerous direction. A PI's own Project
    Description routinely opens with Background or Introduction; dropping one
    of those to None would file its requirements at whole-document scope."""
    assert sr.canon_section(real) is not None


def test_budget_and_budget_justification_are_one_section():
    """A row filed under a section key that lost a merge can never be located.
    Measured: one run called it `budget`, another `budget_justification`."""
    sections = {"budget_justification": {"label": "Budget Justification",
                                         "aliases": ["budget justification"]}}
    assert sp.resolve_section_key(sections, "Budget") == "budget_justification"


def test_a_narrower_section_is_still_not_swallowed():
    """The safety property this must not break: set EQUALITY, never
    containment. Containment would fold Project Description Supplementary
    Documents into Project Description and lose a real section."""
    sections = {"project_description": {"label": "Project Description",
                                        "aliases": ["project description"]}}
    assert sp.resolve_section_key(
        sections, "Project Description Supplementary Documents") is None


# ---------------------------------------------------------------------------
# 3. Reading the document more than once, merged by QUOTE.
# ---------------------------------------------------------------------------

DOC = ("The proposal must include a Data Management Plan. "
       "No more than 30% of the budget can be allocated for equipment. "
       "Inclusion of voluntary committed cost sharing is prohibited.")


def _rows(*specs):
    return [{"label": lb, "section": "project_description", "source": src,
             "scored": True, "why": "", "keywords": []} for lb, src in specs]


def _passes_returning(sequence):
    """One canned answer per model call, in order; empty once exhausted."""
    calls = {"n": 0}

    def fake_ask(prompt, system=sr._SYSTEM, key="requirements"):
        i = calls["n"]
        calls["n"] += 1
        return sequence[i] if i < len(sequence) else []
    return fake_ask


def test_one_pass_is_the_default(monkeypatch):
    """Every existing caller and test must behave exactly as before."""
    monkeypatch.setattr(sr, "_ask", _passes_returning([
        _rows(("Submit a Data Management Plan",
               "The proposal must include a Data Management Plan.")),
    ]))
    out = sr.extract_requirements(DOC, max_rounds=0, targeted=False)
    assert out["passes"] == 1
    assert len(out["requirements"]) == 1


def test_two_passes_merge_rows_quoting_the_same_sentence(monkeypatch):
    """The ids churn, so the merge is on the QUOTE. Two readers naming one
    requirement differently must not become two requirements."""
    monkeypatch.setattr(sr, "_ask", _passes_returning([
        _rows(("Submit a Data Management Plan",
               "The proposal must include a Data Management Plan.")),
        _rows(("Include the Data Management Plan attachment",
               "The proposal must include a Data Management Plan.")),
    ]))
    out = sr.extract_requirements(DOC, max_rounds=0, targeted=False, passes=2)
    assert out["passes"] == 2
    assert len(out["requirements"]) == 1


def test_a_second_pass_recovers_what_the_first_missed(monkeypatch):
    """The whole point: 36-52 rows per run over a real 54-row document."""
    monkeypatch.setattr(sr, "_ask", _passes_returning([
        _rows(("Submit a Data Management Plan",
               "The proposal must include a Data Management Plan.")),
        _rows(("Cap equipment at 30%",
               "No more than 30% of the budget can be allocated for equipment.")),
    ]))
    out = sr.extract_requirements(DOC, max_rounds=0, targeted=False, passes=2)
    assert len(out["requirements"]) == 2
    assert {r["label"] for r in out["requirements"]} == {
        "Submit a Data Management Plan", "Cap equipment at 30%"}


def test_a_merged_row_still_carries_a_verifiable_quote(monkeypatch):
    """Golden rule 2 survives the merge -- a union must not become a way in
    for a row whose quote is not in the document."""
    monkeypatch.setattr(sr, "_ask", _passes_returning([
        _rows(("Real", "The proposal must include a Data Management Plan.")),
        _rows(("Invented", "The proposal must be printed on vellum.")),
    ]))
    out = sr.extract_requirements(DOC, max_rounds=0, targeted=False, passes=2)
    assert [r["label"] for r in out["requirements"]] == ["Real"]


def test_every_row_keeps_a_unique_id_after_a_merge(monkeypatch):
    """Ids are the checklist's source_ref. Two rows sharing one would make a
    ticked task ambiguous."""
    monkeypatch.setattr(sr, "_ask", _passes_returning([
        _rows(("Submit a Data Management Plan",
               "The proposal must include a Data Management Plan.")),
        _rows(("Cap equipment at 30%",
               "No more than 30% of the budget can be allocated for equipment.")),
    ]))
    out = sr.extract_requirements(DOC, max_rounds=0, targeted=False, passes=2)
    ids = [r["id"] for r in out["requirements"]]
    assert len(ids) == len(set(ids))


def test_a_compound_sentence_keeps_its_separate_rows(monkeypatch):
    """THE TRAP a plain quote-dedupe falls into.

    `_SYSTEM` rule 4 deliberately SPLITS "must describe how A, B and C" into
    one row per ask, so several legitimate requirements quote ONE sentence.
    Collapsing a quote-cluster to a single row would delete real requirements
    -- the exact opposite of what reading twice is for. The pass that split the
    sentence must win over the pass that did not."""
    doc = ("The Project Description must describe the sustainability plan, "
           "the success metrics, and the institutional research capacity.")
    sentence = ("The Project Description must describe the sustainability plan, "
                "the success metrics, and the institutional research capacity.")
    monkeypatch.setattr(sr, "_ask", _passes_returning([
        # pass 1 -- lumped it into one row
        _rows(("Describe the plan, metrics and capacity", sentence)),
        # pass 2 -- split it correctly, three rows off the same sentence
        _rows(("Describe the sustainability plan", sentence),
              ("Describe the success metrics", sentence),
              ("Describe the institutional research capacity", sentence)),
    ]))
    out = sr.extract_requirements(doc, max_rounds=0, targeted=False, passes=2)
    labels = {r["label"] for r in out["requirements"]}
    assert labels == {"Describe the sustainability plan",
                      "Describe the success metrics",
                      "Describe the institutional research capacity"}


def test_reading_in_parallel_cannot_stampede_the_model(monkeypatch):
    """The pools NEST and their caps MULTIPLY.

    `passes` fans out at the top; inside each pass `MAX_WORKERS` fans out again
    per chunk. Six passes over a multi-chunk solicitation is 6x4 = 24 calls in
    flight, and gemini_client's backoff is a fixed 1s/2s with no jitter -- a
    thundering herd, not a fix. Same failure class CLAUDE.md records for
    draft_review, which had to grow `_MODEL_SLOTS` for exactly this.

    Asserts the CEILING, not the arrangement: the module may schedule work
    however it likes so long as it never has more than `_MODEL_SLOTS` calls
    in flight at once."""
    import threading

    live = {"now": 0, "peak": 0}
    lock = threading.Lock()
    gate = threading.Event()

    # Patched at the WIRE boundary, not at `_ask` -- replacing `_ask` would
    # replace the semaphore along with it and the test could never fail.
    def slow_call(prompt, **kwargs):
        with lock:
            live["now"] += 1
            live["peak"] = max(live["peak"], live["now"])
        gate.wait(0.05)
        with lock:
            live["now"] -= 1
        return {"requirements": []}

    monkeypatch.setattr(sr.gemini_client, "generate_json", slow_call)
    # many chunks x many passes -- the shape that multiplies
    big = (DOC + " ") * 20000
    assert len(big) > sr.CHUNK_CHARS, "fixture must actually chunk"
    sr.extract_requirements(big, max_rounds=1, targeted=False, passes=6)
    assert live["peak"] <= sr._MODEL_SLOTS_N, (
        f"{live['peak']} concurrent model calls, ceiling is {sr._MODEL_SLOTS_N}")


def test_one_failed_pass_does_not_lose_the_others(monkeypatch):
    """GOLDEN RULE 3, applied to the fan-out this change introduced.

    Reading six times means six chances to raise. Losing a whole requirement
    read -- 30s of work and the PI's place in the upload flow -- because one of
    six threads threw is a worse outcome than the single-pass read it
    replaced."""
    calls = {"n": 0}

    def flaky(prompt, system=sr._SYSTEM, key="requirements"):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("transient")
        return _rows(("Submit a Data Management Plan",
                      "The proposal must include a Data Management Plan."))

    monkeypatch.setattr(sr, "_ask", flaky)
    out = sr.extract_requirements(DOC, max_rounds=0, targeted=False, passes=3)
    assert len(out["requirements"]) == 1
    assert out["failed_passes"] == 1


def test_every_pass_failing_is_raised_not_swallowed(monkeypatch):
    """A real bug must not hide behind the fallback. `_extract_once` already
    returns an EMPTY result when the model is merely unavailable, so an
    exception reaching here means something is actually broken."""
    def always(prompt, system=sr._SYSTEM, key="requirements"):
        raise RuntimeError("boom")

    monkeypatch.setattr(sr, "_ask", always)
    with pytest.raises(RuntimeError):
        sr.extract_requirements(DOC, max_rounds=0, targeted=False, passes=3)
