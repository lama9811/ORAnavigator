"""Deterministic checks that hold for ANY solicitation.

These are the rules whose input is a NUMBER the extracted contract already
carries — a page limit, a required attachment's name, the budget cap — so they
are decided by code for every funder (golden rule 1). This module is the WHOLE
deterministic layer; there is no per-funder check table. A solicitation-specific
rule nobody extracted a number for (a 30% equipment ceiling, a cost-sharing
prohibition) reaches the PI as a quoted SEMANTIC row instead, judged by the model
against its own solicitation quote.

SIGNATURE CONTRACT: fn(ctx, req) -> (status, detail, evidence)
  ctx  = {"text", "spans", "title", "budget", "profile"}
  req  = the requirement row; its parameters ride on req["check_args"]

STATUS VOCABULARY, and why these particular choices matter
----------------------------------------------------------
`clear` / `flagged` for the LIMIT checks, not `addressed` / `not_found`. Both
pairs score identically (draft_review._CREDIT: 1.0 / 0.0), so this is purely
about what the PI reads. An over-length Project Description reported as
"Not found" says *your Project Description is missing* — a confident, wrong
claim about the author's own work, which is the class of error this whole design
exists to prevent. A limit is a prohibition: you either respect it or you trip
it, and these rows carry `flag_if_present: True` so the UI groups them as flags.

`could_not_locate` / `not_checked` when the input needed was not supplied. Both
are excluded from the score denominator by draft_review, which is the honest
treatment of "we did not assess this" as opposed to "we assessed it and it is
missing".
"""

from __future__ import annotations

import re

from services.solicitation_profile import heading_regex, section_key

# ~550 words/page at typical federal formatting (11pt, single-spaced, 1" margins).
# Used ONLY to estimate a pasted section's length, and every message built from it
# says it is an estimate — a formatted PDF is the authority, not a word count.
WORDS_PER_PAGE = 550


def _norm_ws(s: str) -> str:
    return " ".join((s or "").split())


def contract_requirements(contract: dict) -> list[dict]:
    """Deterministic requirement rows from the contract's hard numbers.

    Shaped exactly like the semantic rows the extractor produces, so the engine
    cannot tell where a row came from. `source` prefers the contract's own
    verbatim `source_quotes` entry — the solicitation's actual sentence — and
    falls back to a stated derivation when the extractor had no quote for that
    field."""
    contract = contract or {}
    quotes = contract.get("source_quotes") or {}
    rows: list[dict] = []

    for section, limit in (contract.get("page_limits") or {}).items():
        try:
            limit = int(limit)
        except (TypeError, ValueError):
            continue                      # a non-numeric limit is not a check
        key = section_key(str(section))
        label = " ".join(w.capitalize() for w in key.split("_"))
        rows.append({
            "id": f"page_limit_{key}",
            "label": f"{label} within its {limit}-page limit",
            "section": key,
            "kind": "deterministic", "scored": True,
            "flag_if_present": True,
            "check": "page_limit",
            "check_args": {"section": key, "limit": limit},
            "source": _norm_ws(quotes.get("page_limits") or "")[:300]
                      or f"Extracted page limit: {label} — {limit} pages.",
            "why": "Most funders return an over-length section without review.",
            "keywords": [],
        })

    for name in (contract.get("required_attachments") or []):
        name = _norm_ws(str(name))
        if not name:
            continue
        key = section_key(name)
        rows.append({
            "id": f"attachment_{key}",
            "label": f"{name} included",
            # Deliberately whole-document: an attachment is not a section OF
            # another section, and scoping the search to one span would miss a
            # package pasted in a different order than the solicitation lists.
            "section": None,
            "kind": "deterministic", "scored": True,
            "check": "attachment_present",
            "check_args": {"name": name, "section_key": key},
            "source": _norm_ws(quotes.get("required_attachments") or "")[:300]
                      or f"The solicitation lists {name} as a required attachment.",
            "why": "A missing required attachment is a compliance rejection, not a weakness.",
            "keywords": [],
        })

    cap = contract.get("budget_cap")
    if cap:
        try:
            cap = int(cap)
        except (TypeError, ValueError):
            cap = None
    if cap:
        rows.append({
            "id": "budget_within_cap",
            "label": f"Budget within the ${cap:,} cap",
            "section": None,
            "kind": "deterministic", "scored": True,
            "flag_if_present": True,
            "check": "budget_cap",
            "check_args": {"cap": cap},
            "source": _norm_ws(quotes.get("budget_cap") or "")[:300]
                      or f"Extracted award maximum: ${cap:,}.",
            "why": "An over-cap budget is returned without review.",
            "keywords": [],
        })

    return rows


def _check_page_limit(ctx: dict, req: dict) -> tuple:
    args = req.get("check_args") or {}
    span = (ctx.get("spans") or {}).get(args.get("section"))
    if not span:
        # NOT a failure. Claiming a section is over its limit when we never found
        # the section would be a confident, wrong statement about the draft.
        return "could_not_locate", (
            "That section was not found in what you pasted, so its page limit was "
            "not checked. If you did include it, give it a clear heading and re-run."
        ), ""
    limit = args.get("limit")
    words = len(span["text"].split())
    pages = words / WORDS_PER_PAGE
    if pages <= limit:
        return "clear", (
            f"About {words:,} words ≈ {pages:.1f} pages (estimated), within the "
            f"{limit}-page limit."
        ), ""
    return "flagged", (
        f"About {words:,} words ≈ {pages:.1f} pages (estimated), over the "
        f"{limit}-page limit. This is an estimate from word count — check the "
        "formatted PDF before you cut anything."
    ), ""


def _check_attachment_present(ctx: dict, req: dict) -> tuple:
    """Is the attachment actually IN the package, or merely mentioned?

    Three tiers, because a bare substring search is a guaranteed false positive:
    "as described in our Data Management Plan" inside the narrative would report
    a missing DMP as present, which is the one error that costs a submission.
      1. A verified section span under this name  -> addressed (authoritative:
         the locate stage already proved that heading exists in the paste)
      2. A heading LINE naming it                 -> addressed
      3. A mention anywhere in the prose          -> partial, and says why
    """
    args = req.get("check_args") or {}
    name = _norm_ws(args.get("name") or "")
    if not name:
        return "not_checked", "No attachment name to look for.", ""
    text = ctx.get("text") or ""

    key = args.get("section_key") or section_key(name)
    span = (ctx.get("spans") or {}).get(key)
    if span:
        return "addressed", f"Found a {name} section in what you pasted.", \
            _norm_ws(span.get("marker") or "")[:160]

    m = heading_regex(name).search(text)
    if m:
        return "addressed", f"Found a {name} heading.", _norm_ws(m.group(0))[:160]

    loose = re.search(r"\s+".join(re.escape(tok) for tok in name.split()), text, re.IGNORECASE)
    if loose:
        return "partial", (
            f"{name} is mentioned, but it does not appear as its own section. If it "
            "is a separate file, upload it with the rest of the package so it can "
            "be checked properly."
        ), _norm_ws(text[max(0, loose.start() - 40):loose.end() + 60])[:160]

    return "not_found", (
        f"No {name} found in what you pasted. If you have it as a separate file, "
        "upload it with the rest of the package."
    ), ""


def _check_budget_cap(ctx: dict, req: dict) -> tuple:
    """Requested total vs the sponsor cap, on the RIGHT total.

    budget["total"] is YEAR 1 on a multi-year budget (budget_helper says so
    explicitly). Comparing that against a whole-project cap tells a PI with a
    3-year budget they are comfortably under a cap they are three times over —
    a wrong number in the one place a wrong number is unrecoverable. When the
    saved budget already carries its own verdict for this same cap, that verdict
    wins: it is the Budget Helper's arithmetic, and two components disagreeing
    about one figure is worse than either answer alone.
    """
    cap = (req.get("check_args") or {}).get("cap")
    budget = ctx.get("budget")
    if not budget:
        return "not_checked", (
            "No budget saved for this proposal yet. Build one in the Budget Helper "
            "and this check runs against real numbers."
        ), ""

    my = budget.get("multi_year") or {}
    basis = "cumulative"
    if my:
        own_cap = my.get("cap")
        if own_cap and int(own_cap) == int(cap) and my.get("cap_status") in ("ok", "over"):
            years = my.get("project_years")
            if my.get("cap_basis") == "per_year":
                if my["cap_status"] == "ok":
                    return "clear", (
                        f"Every year of the {years}-year budget is within the "
                        f"${cap:,} per-year cap."
                    ), ""
                return "flagged", (
                    f"The worst year is ${my.get('cap_overage', 0):,.0f} over the "
                    f"${cap:,} per-year cap."
                ), ""
            total = float((my.get("cumulative") or {}).get("total") or 0.0)
            if my["cap_status"] == "ok":
                return "clear", (
                    f"${total:,.0f} requested across all {years} years, within the "
                    f"${cap:,} cap."
                ), ""
            return "flagged", (
                f"${total:,.0f} requested across all {years} years — "
                f"${my.get('cap_overage', 0):,.0f} over the ${cap:,} cap."
            ), ""
        total = float((my.get("cumulative") or {}).get("total") or 0.0)
        basis = f"across all {my.get('project_years')} years"
    else:
        total = float(budget.get("total") or 0.0)
        basis = "total"

    if total <= 0:
        return "not_checked", "The saved budget has no total to measure against.", ""
    if total <= cap:
        return "clear", f"${total:,.0f} requested ({basis}), within the ${cap:,} cap.", ""
    return "flagged", (
        f"${total:,.0f} requested ({basis}) — ${total - cap:,.0f} over the ${cap:,} cap."
    ), ""


CHECKS = {
    "page_limit": _check_page_limit,
    "attachment_present": _check_attachment_present,
    "budget_cap": _check_budget_cap,
}
