"""One ceiling on concurrent model calls, and voting on BOTH review paths.

WHY THIS EXISTS. The pools in `draft_review` NEST, so their caps multiply and no
single number said how many threads could be talking to Vertex at once:
`review_draft` opens a 6-wide pool over sections, each worker reaches
`_voted_batch` which opens another `votes` wide, and `proofread` opens a third.
With `votes=1` the real ceiling was 6 and nobody noticed. Turning voting on for
the whole package -- which is the cheapest consistency win available, and was
simply switched off -- takes it to 18.

That is not hypothetical harm. `gemini_client.get_client()` had an unlocked lazy
init that a Section Check raced against ITSELF at three concurrent calls, and
the measured result was an uploaded Project Summary returning in 0.3s and
scoring 100% "No problems found". The race is fixed; the thundering herd it
exposed is not, because the backoffs are 1s then 2s with no jitter.

A GUARD THAT CANNOT FAIL IS NOT ONE, so both tests here were observed red first:
removing `_ask_model` from a call site, and dropping SEMANTIC_VOTES from the
`review_draft` job tuple.
"""
import threading

FIVE_LINE_SUMMARY = """Project Summary

We propose to study trustworthy cardiac AI using multimodal physiological
sensing. The work will develop new models and validate them on clinical data.
"""


class _ConcurrencySpy:
    """Counts how many calls are in flight at once, at the gemini boundary."""

    def __init__(self, real):
        self._real = real
        self._lock = threading.Lock()
        self.live = 0
        self.peak = 0
        self.calls = 0

    def __call__(self, *a, **kw):
        with self._lock:
            self.live += 1
            self.calls += 1
            self.peak = max(self.peak, self.live)
        try:
            return self._real(*a, **kw)
        finally:
            with self._lock:
                self.live -= 1


def _many_section_profile(n=12):
    """A profile wide enough that an unbounded fan-out would show."""
    from services import solicitation_profile as sp
    rows = [{
        "id": f"sol_r{i}", "section": f"section_{i}",
        "label": f"Describe item {i}", "kind": "semantic", "scored": True,
        "source": f"The proposal must describe item {i}.", "why": "",
        "keywords": [],
    } for i in range(n)]
    return sp.build_generic({}, rows, id="NSF 99-999", title="Wide solicitation")


def test_no_more_than_the_cap_of_model_calls_run_at_once(monkeypatch):
    from services import draft_review, gemini_client

    spy = _ConcurrencySpy(lambda *a, **kw: None)   # None = model unavailable
    monkeypatch.setattr(gemini_client, "generate_json", spy)

    text = "\n\n".join(f"Section {i}\nWe describe item {i} at length here."
                       for i in range(12))
    draft_review.review_draft(text, profile=_many_section_profile())

    assert spy.calls > 0, "the spy never saw a call; the test proves nothing"
    assert spy.peak <= draft_review._MODEL_SLOTS._initial_value, (
        f"{spy.peak} concurrent model calls against a cap of "
        f"{draft_review._MODEL_SLOTS._initial_value}")


def test_every_model_call_in_this_module_goes_through_the_cap():
    """A new call site that forgets `_ask_model` silently escapes the ceiling.

    Reading the source is the only check that covers a call added tomorrow; a
    behavioural test only covers the paths it happens to drive."""
    import inspect
    from services import draft_review

    src = inspect.getsource(draft_review)
    direct = [ln.strip() for ln in src.splitlines()
              if "gemini_client.generate_" in ln and "_ask_model" not in ln
              and not ln.strip().startswith("#")]
    # The only permitted mention is the one INSIDE _ask_model's own call.
    assert all(ln.startswith("gemini_client.generate_") for ln in direct), direct


def test_the_whole_package_review_votes_like_check_a_section(monkeypatch):
    """One engine, two entry points — they must not grade a section two ways."""
    from services import draft_review, gemini_client
    spy = _ConcurrencySpy(lambda *a, **kw: None)
    monkeypatch.setattr(gemini_client, "generate_json", spy)

    text = "Section 0\nWe describe item 0 at length in this paragraph here."
    draft_review.review_draft(text, profile=_many_section_profile(n=1),
                              use_ai=True)
    # locate + notes are single calls; the SECTION review is the voted one, so
    # the count must exceed what one vote per section could produce.
    assert spy.calls >= draft_review.SEMANTIC_VOTES, (
        f"{spy.calls} calls — the section review is still asking once")


# ── how a section is split, and why it is by batch COUNT ────────────────────

def test_a_small_section_gets_one_rule_per_call():
    """Where a flipped rule costs the most, isolate every rule.

    Measured on a real awarded Project Summary, ten uploads each:
    `pappg_ps_overview_methods` split not_found 7 / partial 3 at a 15-rule batch
    (scores 83% x7, 92% x3), and came back 92% ten times out of ten with nothing
    moving at one rule per call."""
    from services.draft_review import _batch_size
    for n in (1, 3, 5, 7):
        assert _batch_size(n) == 1, f"{n} rules should be one per call"


def test_a_large_section_is_bounded_by_the_NUMBER_of_calls():
    """And where isolating every rule is expensive, do not.

    Measured on a 45-rule section, the size of the real Budget section: 17.3s at
    a 15-rule batch, 16.0s at 5, and 51.4s at 1 — three times the wall clock on
    a tool ORA staff run in front of a PI. With N rules one flipped rule is
    worth 100/N of the score (14 points at N=7, 2 at N=45) while isolating every
    rule costs N round-trips, so isolation is worth most exactly where it is
    cheap."""
    import math
    from services import draft_review as dr
    # 45 is the real Budget section; 120 is past anything observed. Beyond that
    # the REVIEW_BATCH ceiling wins and the call count climbs again, which is
    # CORRECT — the token ceiling is the guarantee that must not bend, because
    # an overflowing batch loses rows silently. Asserted in its own test below.
    for n in (12, 18, 45, 120):
        calls = math.ceil(n / dr._batch_size(n))
        assert calls <= dr._MAX_BATCHES, f"{n} rules -> {calls} calls"


def test_a_batch_never_exceeds_the_output_token_ceiling():
    """REVIEW_BATCH is a DIFFERENT guarantee and must survive the adaptive size.

    It exists so a batch cannot overflow `max_output_tokens`: the reviewer OMITS
    rows rather than truncating visibly, an omitted row becomes `unclear`, and
    `unclear` leaves the score's denominator with nothing on screen to say so."""
    from services import draft_review as dr
    for n in (1, 45, 400, 5000):
        assert dr._batch_size(n) <= dr.REVIEW_BATCH, n
