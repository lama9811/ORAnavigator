"""
Decide whether a changed page MATTERS, and draft the replacement content.

The fingerprint gate is deliberately dumb — it fires on a changed copyright
year as readily as on a changed F&A rate. This module is what makes the change
report worth reading.

Two rules borrowed from the rest of the codebase:

  * Evidence grounding (golden rule 2). A "material" verdict must quote the live
    page verbatim, and the quote is checked in code with the same
    whitespace-collapsing membership test as section_coach._quote_in. An
    unquotable verdict is demoted to needs_review, never applied.

  * Graceful fallback (golden rule 3). If Gemini is unavailable or returns
    nonsense, we do NOT guess. We report that the page changed and leave the
    document untouched for a human — which is exactly the behaviour for any
    change we cannot make confidently.
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass
from typing import Optional

log = logging.getLogger(__name__)

MODEL = os.getenv("SCRAPE_MODEL", "gemini-2.5-flash")
_MAX_PAGE_CHARS = 24_000


@dataclass
class Verdict:
    material: bool
    what_changed: str
    new_content: str
    quote: str
    confidence: str          # high | medium | low
    grounded: bool = False   # quote verified against the live page

    @property
    def applicable(self) -> bool:
        """Safe to write without a human looking first."""
        return self.material and self.grounded and self.confidence in ("high", "medium") and bool(self.new_content.strip())


def _quote_in(quote: str, source: str) -> bool:
    """Whitespace-collapsing membership test.

    Same reason as section_coach: the page text is hard-wrapped, the model's
    quote comes back with single spaces, and a raw substring match would reject
    a perfectly real quote.
    """
    if not quote or not source:
        return False
    q = " ".join(quote.split())
    s = " ".join(source.split())
    return len(q) >= 12 and q.lower() in s.lower()


_SYSTEM = """You maintain the Morgan State University Office of Research Administration knowledge base.

A page on morgan.edu changed. You are given the CURRENT page text and the STORED knowledge base document written from an earlier version of that page.

Decide whether the change is MATERIAL to someone asking the assistant a question.

MATERIAL — a fact a researcher would act on changed:
  rates and percentages, dollar amounts, deadlines and dates, eligibility rules,
  required forms or attachments, procedures and their order, staff names, titles,
  phone numbers or email addresses, policy requirements, URLs a user must visit.

NOT MATERIAL — the meaning to a researcher is unchanged:
  reworded sentences that say the same thing, reordered links or menus, layout,
  copyright years, "last updated" stamps, image captions, typo fixes, added
  pleasantries.

Return STRICT JSON, no markdown fence:
{
  "material": true|false,
  "what_changed": "one sentence, concrete and specific — name the old and new value",
  "quote": "a VERBATIM span copied from the CURRENT page text that shows the change. Copy exactly, do not paraphrase or fix anything.",
  "confidence": "high"|"medium"|"low",
  "new_content": "the full rewritten knowledge base document"
}

Rules for new_content:
  * Rewrite the STORED document so it reflects the CURRENT page. Keep its voice,
    structure, and level of detail — it is a concise reference, not a page dump.
  * Preserve every fact in the stored document that the current page still
    supports. You are editing, not starting over.
  * Never invent anything absent from the current page.
  * If material is false, return the stored document unchanged.
"""


def _parse(raw: str) -> Optional[dict]:
    if not raw:
        return None
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
        text = re.sub(r"\n?```$", "", text).strip()
    try:
        return json.loads(text)
    except Exception:
        pass
    match = re.search(r"\{[\s\S]*\}", text)
    if match:
        try:
            return json.loads(match.group(0))
        except Exception:
            return None
    return None


def adjudicate(page_text: str, stored_content: str, title: str = "") -> Verdict:
    """Judge one changed page. Never raises."""
    unknown = Verdict(
        material=True,
        what_changed="Page changed, but the change could not be assessed automatically.",
        new_content="",
        quote="",
        confidence="low",
        grounded=False,
    )

    page_text = (page_text or "")[:_MAX_PAGE_CHARS]
    if not page_text.strip():
        return unknown

    prompt = (
        f"PAGE TITLE: {title}\n\n"
        f"=== CURRENT PAGE TEXT ===\n{page_text}\n\n"
        f"=== STORED KNOWLEDGE BASE DOCUMENT ===\n{stored_content or '(none — this page is new)'}\n"
    )

    try:
        from google import genai
        from google.genai import types

        client = genai.Client(
            vertexai=True,
            project=os.getenv("GOOGLE_CLOUD_PROJECT"),
            location=os.getenv("GOOGLE_CLOUD_LOCATION", "us-central1"),
        )
        response = client.models.generate_content(
            model=MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(
                system_instruction=_SYSTEM,
                temperature=0.1,
                max_output_tokens=8192,
                # Same reasoning as opportunity_finder: thinking tokens eat the
                # output budget and truncate the JSON, which silently degrades
                # every verdict to "unknown".
                thinking_config=types.ThinkingConfig(thinking_budget=0),
            ),
        )
        data = _parse(getattr(response, "text", "") or "")
    except Exception as e:
        log.warning("Adjudication failed for %r: %s", title or "page", e)
        return unknown

    if not isinstance(data, dict):
        return unknown

    quote = str(data.get("quote") or "")
    verdict = Verdict(
        material=bool(data.get("material")),
        what_changed=str(data.get("what_changed") or "").strip()[:1000],
        new_content=str(data.get("new_content") or "").strip(),
        quote=quote.strip(),
        confidence=str(data.get("confidence") or "low").lower(),
        grounded=_quote_in(quote, page_text),
    )

    if verdict.material and not verdict.grounded:
        # It claims a change but cannot point at it on the page. Report, do not
        # apply — and drop the quote rather than showing an unverified one.
        log.info("Ungrounded verdict for %r; demoting to review", title or "page")
        verdict.quote = ""
        verdict.confidence = "low"

    if verdict.confidence not in ("high", "medium", "low"):
        verdict.confidence = "low"

    return verdict
