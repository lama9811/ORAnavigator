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


def length_guidance(word_count: int, page_limit: Optional[float],
                    pages: Optional[int] = None) -> Optional[dict]:
    """How much of its allowance a section uses, or None when there is nothing
    worth saying.

    Returns None for a section with no stated page limit — most sections have
    none, and deriving an allowance for them would fabricate the limit. Returns
    None for an empty section too: nothing pasted is a different problem, already
    reported by the locate stage, and a "0% of your page" line would bury it.

    `pages` is the REAL count from an uploaded PDF, and where it exists this
    says nothing about length. The estimate divides by WORDS_PER_PAGE, so a
    dense one-page section measures 102% and the message called it "over the
    limit on most formatting" and told the author to "upload it to have this
    checked properly" — beside a deterministic row reading "1 page, within the
    1-page limit", on a draft that had just been uploaded. Both false, and
    contradicting the row next to them.

    Over the limit is suppressed for the same reason: `rb_page_limit` already
    reports the TRUE number ("2 pages, over the 1-page limit"), and an estimate
    beside it offers a second, different number for one fact.

    The MEASUREMENT survives either way — the word count is real and was moved
    into the score box deliberately. Only the estimated percentage and the
    verdict drawn from it go.
    """
    if not page_limit or page_limit <= 0 or not word_count or word_count <= 0:
        return None
    if isinstance(pages, int) and pages > 0:
        return {"words": word_count, "page_limit": page_limit, "pages": pages}
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


# Statuses that mean the rule is SATISFIED. A row in one of these has nothing
# for the author to fix, only — at most — something to strengthen.
_PASSING = {"addressed", "clear"}


def priorities_heading(findings: list[dict]) -> str:
    """What to call the list, given what is actually in it.

    "Do this first" over a section that met every rule is a to-do framing on a
    section with nothing to do, and it reads as failure. Authored HERE rather
    than in a modal, for the same reason `score()` authors its own `basis` and
    `verdict()` its own summary: a caption that lives in one modal is a caption
    the other one renders without.
    """
    for f in findings or []:
        # A CONDITIONAL is excluded from the framing as well as from the score.
        # An unmet "if you have unfunded postdocs, name them" on a proposal with
        # none would otherwise put "Do this first" beside a 100% -- the
        # self-contradiction this modal has had to unship repeatedly. It is
        # still LISTED; it just does not rename the list.
        if f.get("scored") is False:
            continue
        if f.get("status") in _ACTIONABLE and f.get("status") not in _PASSING:
            return "Do this first"
    return "Ways to strengthen this"


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
        # THE NOTE IS A FALLBACK FOR A FAILURE, NEVER FOR A PASS, and the
        # asymmetry is the whole fix. A failing row's note says what is MISSING,
        # which an author can act on even when it is phrased as a diagnosis. A
        # PASSING row's note says what was FOUND — a confirmation — and putting
        # that in a to-do list tells someone to do a thing they have done.
        #
        # Reported by a PI on a Project Summary that met all four of its rules:
        # the deterministic heading check has nothing to suggest (it is binary
        # and it passed), so it arrived with an empty `suggestion` and was
        # listed carrying "Found “Overview”, “Intellectual Merit”, “Broader
        # Impacts”, each on its own line." The docstring above already said
        # such a row is dropped; this fallback was quietly overriding it.
        text = (f.get("suggestion") or "").strip()
        if not text and status not in _PASSING:
            text = (f.get("note") or "").strip()
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
    # WHEN ANYTHING NEEDS FIXING, THE PLAN IS ONLY THE THINGS THAT NEED FIXING.
    #
    # Reported by a PI on a Facilities check: one `partial` and two `addressed`
    # rows arrived as a single list under "Do this first", so two of the three
    # things they were told to do first were rules the section already met. The
    # heading was right; the list was not. CLAUDE.md records the same error from
    # the other direction — "'Do this first' over four met rules is approval
    # rendered as failure" — which is why priorities_heading() exists.
    #
    # With nothing to fix, the met-but-improvable rows stand alone under "Ways
    # to strengthen this": that case is unchanged, and is why this filters
    # rather than simply dropping passes.
    fixes = [r for r in ranked
             if r[2]["status"] not in _PASSING and not r[2]["advisory"]]
    chosen = fixes or ranked
    return [r[2] for r in chosen[:limit]]
