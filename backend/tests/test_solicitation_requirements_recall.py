"""Does the GENERIC extractor find what a human found?

tests/fixtures/nsf_23_598.py holds ~25 requirements a person read out of one
solicitation and verified by hand. No service imports it. It is the only
human-verified requirement list in this repo, and therefore the only yardstick
for an extractor that now does that job for every funder.

OPT-IN: needs a real document and live Gemini, so it never runs in CI.

    # from the published page (no file needed)
    cd backend && SOLICITATION_URL="$(python3 -c 'from tests.fixtures import nsf_23_598 as f; print(f.SOLICITATION_URL)')" \\
      python3 -m pytest tests/test_solicitation_requirements_recall.py -v -s

    # or from a downloaded PDF
    cd backend && SOLICITATION_PDF=/tmp/nsf23-598.pdf \\
      python3 -m pytest tests/test_solicitation_requirements_recall.py -v -s

HOW THE MATCH WORKS, and why it is not a keyword test. Each curated row carries
the VERBATIM solicitation sentence it came from, and so does each extracted row.
Both quote the SAME document, so overlapping their 5-grams asks the only
question worth asking — "did the extractor find this sentence?" — rather than
whether two summaries happen to share vocabulary. An earlier draft of this test
matched on keywords like "train", which almost any proposal text satisfies; it
would have passed while recovering nothing.

What is ASSERTED is a floor: 80% recall, and 100% grounding. What is PRINTED and
deliberately not asserted: precision (a real solicitation has far more
requirements than a human wrote down, so extra rows are expected and mostly
legitimate) and run-to-run stability (gemini-3.6-flash is not deterministic;
the number belongs in a commit message, not in a gate that would flake).
"""
import os
import re

import pytest

PDF = os.getenv("SOLICITATION_PDF") or os.getenv("EIR_SOLICITATION_PDF")
URL = os.getenv("SOLICITATION_URL")

pytestmark = pytest.mark.skipif(
    not (PDF or URL),
    reason="set SOLICITATION_PDF=<path> or SOLICITATION_URL=<url> to run",
)

# A curated row counts as found when an extracted row's quote shares this much
# of its 5-gram shingles — a third of one sentence, verbatim, from the same
# document. High enough that unrelated rows do not match, low enough to survive
# the extractor quoting a longer or shorter span of the same sentence.
_OVERLAP_FLOOR = 0.34
_RECALL_FLOOR = 0.80


@pytest.fixture(autouse=True)
def _live_gemini(monkeypatch):
    """conftest pins the AI layer OFF for every test. This one needs it live."""
    monkeypatch.undo()


def _shingles(text: str, n: int = 5) -> set:
    words = re.sub(r"[^a-z0-9 ]+", " ", (text or "").lower()).split()
    if len(words) < n:
        return {" ".join(words)} if words else set()
    return {" ".join(words[i:i + n]) for i in range(len(words) - n + 1)}


def _best_match(curated: dict, extracted: list) -> tuple:
    """(row, overlap) for the extracted row whose quote best covers this
    curated row's solicitation sentence."""
    target = _shingles(curated["source"])
    if not target:
        return None, 0.0
    best, score = None, 0.0
    for row in extracted:
        overlap = len(target & _shingles(row["source"])) / len(target)
        if overlap > score:
            best, score = row, overlap
    return best, score


def _read_document() -> str:
    if PDF:
        from services import solicitation_extractor as sx
        with open(PDF, "rb") as fh:
            read = sx.read_pdf(fh.read())
        assert read["chars"] > 20_000, f"the PDF read looks truncated: {read}"
        assert not read["pages_without_text"], (
            f"{read['pages_without_text']} pages had no text layer — this "
            "document is partly a scan, so recall would be measured against an "
            "incomplete read")
        return read["text"]
    from services import url_fetcher
    text = url_fetcher.fetch_solicitation_text(URL)
    assert len(text) > 20_000, f"fetched only {len(text)} chars from {URL}"
    return text


def test_generic_extraction_recovers_the_curated_requirements():
    from services import solicitation_requirements as sr
    from services.text_match import quote_in
    from tests.fixtures import nsf_23_598 as fx

    text = _read_document()
    out = sr.extract_requirements(text)
    extracted = out["requirements"]

    print(f"\n--- extraction ---")
    print(f"  {len(extracted)} requirements from {out['chars']:,} chars "
          f"in {out['chunks']} chunk(s), {out['rounds']} sweep round(s), "
          f"{out['elapsed_s']}s")
    print(f"  dropped unquotable: {out['dropped_unverified']}   "
          f"time cap: {out['hit_time_cap']}   round cap: {out['hit_round_cap']}")

    curated = [r for r in fx.EIR_REQUIREMENTS if r.get("source")]
    found, missed = [], []
    print(f"\n--- alignment against {len(curated)} human-verified rows ---")
    for row in curated:
        match, overlap = _best_match(row, extracted)
        if overlap >= _OVERLAP_FLOOR:
            found.append(row)
            print(f"  OK   {row['label'][:52]:54} <- {match['label'][:40]} ({overlap:.2f})")
        else:
            missed.append(row)
            print(f"  MISS {row['label'][:52]:54} (best {overlap:.2f})")

    recall = len(found) / len(curated)
    matched_ids = {id(_best_match(r, extracted)[0]) for r in found}
    extra = [r for r in extracted if id(r) not in matched_ids]
    print(f"\n--- summary ---")
    print(f"  recall    {len(found)}/{len(curated)} = {recall:.0%}")
    print(f"  extra     {len(extra)} rows with no curated counterpart "
          f"(expected: a real solicitation states more than a human wrote down)")

    # GROUNDING IS ABSOLUTE, not a floor. Every row must be quotable from the
    # document — that is the guarantee the whole feature rests on, and it is
    # enforced in code, so a failure here means the enforcement broke.
    ungrounded = [r for r in extracted
                  if not quote_in(text, r["source"], drop_list_noise=True)]
    assert not ungrounded, f"{len(ungrounded)} rows are not quotable: {ungrounded[:3]}"

    assert recall >= _RECALL_FLOOR, (
        f"recall {recall:.0%} is below the {_RECALL_FLOOR:.0%} floor. "
        f"Missed: {[r['label'] for r in missed]}")


def test_two_reads_of_one_document_are_reported_not_asserted():
    """gemini-3.6-flash is not deterministic. This PRINTS how much two reads of
    the same document differ and never fails on it: the number is real
    information for a commit message, and a gate on it would flake forever.
    Extraction runs once per solicitation and is stored, so the variance costs a
    PI nothing — but it is exactly why the requirement list is shown to them."""
    from services import solicitation_requirements as sr

    text = _read_document()
    a = sr.extract_requirements(text)["requirements"]
    b = sr.extract_requirements(text)["requirements"]
    ids_a, ids_b = {r["id"] for r in a}, {r["id"] for r in b}
    jaccard = len(ids_a & ids_b) / max(1, len(ids_a | ids_b))
    print(f"\n--- stability ---")
    print(f"  run A: {len(a)} rows   run B: {len(b)} rows   "
          f"jaccard {jaccard:.2f}")
    print(f"  only in A: {sorted(ids_a - ids_b)[:5]}")
    print(f"  only in B: {sorted(ids_b - ids_a)[:5]}")
