"""How long a section is against what it is allowed, and what to do first.

DETERMINISTIC AND MODEL-FREE (golden rule 1), and that is the whole point.

THE PROBLEM THIS CLOSES, reported by a PI. A 76-word Project Summary came back
with six of eight rules "Addressed". Nothing was wrong with the checks: those
PAPPG rules are about PRESENCE, and the summary really does contain a sentence
naming objectives and methods. What was wrong is that the tool had no way to say
the obvious thing — a one-page Project Summary allows about 550 words and this
one uses 14% of it — and that "Addressed" reads to an author as "this is fine"
when it only ever meant "the rule is satisfied at the floor".

Two runs of that same paste disagreed about how many rows passed (mine: three
addressed, two partial; theirs: six addressed). So a model asked "is this thin?"
would be inconsistent on exactly the question the author already distrusts. A
word count and an ordering are arithmetic, and arithmetic goes in code.

A MEASUREMENT, NEVER A VERDICT. The PAPPG sets a MAXIMUM for a section and never
a minimum, so "your Project Summary is too short" would be inventing the rule
this module is careful not to state. "You are using 14% of your page" is a fact
the author can act on and argue with. Same line `rb_page_limit` already holds
when it reports a page ESTIMATE from a paste and refuses to call it pass or fail.
"""

from __future__ import annotations

from typing import Optional

from services.generic_checks import WORDS_PER_PAGE

# Below this share of the allowance, the gap is worth pointing at. A section at
# 480 of 550 words is fine and must not be commented on — a tool that remarks on
# every draft is one whose remarks stop being read, which is the same reason the
# delegation caveat had to be cut from four places on screen down to one.
_SHORT_PCT = 60
# And above this it is worth saying too, because the page limit is a real rule
# and an over-length section is returned without review.
_LONG_PCT = 100

# The plan is a PLAN. Twenty ordered instructions is the wall of findings this
# was supposed to replace.
_DEFAULT_LIMIT = 3

# Nobody can act on these. `not_checked` and `could_not_locate` mean nobody
# looked, `delegated` means it is not ours, `not_in_draft` means it happens at
# submission, `unclear` means the reviewer skipped it. A to-do list built from
# any of them is a list of things the author cannot do.
_ACTIONABLE = {"not_found": 0, "flagged": 0, "partial": 1, "addressed": 2, "clear": 2}


def length_guidance(word_count: int, page_limit: Optional[float]) -> Optional[dict]:
    """How much of its allowance a section uses, or None when there is nothing
    worth saying.

    Returns None for a section with no stated page limit — most sections have
    none, and deriving an allowance for them would fabricate the limit. Returns
    None for an empty section too: nothing pasted is a different problem, already
    reported by the locate stage, and a "0% of your page" line would bury it.
    """
    if not page_limit or page_limit <= 0 or not word_count or word_count <= 0:
        return None
    allowance = int(round(WORDS_PER_PAGE * page_limit))
    pct = int(round(100.0 * word_count / allowance))
    if _SHORT_PCT <= pct <= _LONG_PCT:
        return None

    pages = "one page" if page_limit == 1 else f"{page_limit:g} pages"
    if pct > _LONG_PCT:
        message = (
            f"{word_count:,} words. {pages.capitalize()} is roughly {allowance:,} words, "
            f"so this is about {pct}% of the space — over the limit on most formatting. "
            "The page count comes from your PDF, so upload it to have this checked properly."
        )
    else:
        message = (
            f"{word_count:,} words. {pages.capitalize()} allows roughly {allowance:,} words, "
            f"so this uses about {pct}% of the space you are given. There is no minimum "
            "length, but reviewers read this section first."
        )
    return {"words": word_count, "allowance": allowance, "pct": pct,
            "page_limit": page_limit, "message": message}


def priorities(findings: list[dict], limit: int = _DEFAULT_LIMIT) -> list[dict]:
    """The few things to do next, ordered, each carrying its own suggestion.

    Ordering, in full: real failures, then thin passes, then rules that are met
    but could be stronger — and within each, the order the findings arrived, so
    two runs of one paste never reorder the plan. The author already distrusts
    run-to-run variance; this half must not add to it.

    A row with nothing concrete to say is DROPPED rather than listed. A priority
    entry reading "improve this" is a line telling someone to do something
    unspecified, which is worse than one fewer line.
    """
    ranked = []
    for i, f in enumerate(findings or []):
        status = f.get("status")
        if status not in _ACTIONABLE:
            continue
        text = (f.get("suggestion") or "").strip() or (f.get("note") or "").strip()
        if not text:
            continue
        # An advisory row is a conditional the author may not even be subject to
        # ("if you request consultants, detail them"). Measured on a clean Budget
        # Justification: 10 of 14 not_found rows were conditionals that draft was
        # never subject to. They rank below everything real.
        advisory = f.get("scored") is False
        ranked.append((_ACTIONABLE[status] + (10 if advisory else 0), i, {
            "id": f.get("id"), "label": f.get("label"), "status": status,
            "text": text, "advisory": advisory,
        }))
    ranked.sort(key=lambda r: (r[0], r[1]))
    return [r[2] for r in ranked[:limit]]
