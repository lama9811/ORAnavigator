"""Draft a KB document from a file, or update one whose file changed.

Two prompts, one grounding rule. Every draft must quote the file verbatim, and
the quote is verified in code against the extracted text with the same
whitespace-collapsing test the page adjudicator and section_coach use (golden
rule 2). A draft that cannot prove where it came from is dropped and the caller
reports a pointer instead — better nothing than a confident invention.

draft_update is deliberately NOT "rewrite this document from the file". The
stored content is LLM-summarised prose plus hand-authored material a scrape
cannot regenerate: key_facts in 51 documents, leadership_history,
irb_voting_members, staff phone/office. A naive rewrite would silently delete
all of it, so the prompt receives both sides and is told to change only what the
file contradicts.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Callable, Optional

from adjudicator import _parse, _quote_in     # one grounding test, one parser

log = logging.getLogger(__name__)

MODEL = os.getenv("SCRAPE_MODEL", "gemini-2.5-flash")
_MAX_FILE_CHARS = 24_000


@dataclass
class Draft:
    title: str = ""
    content: str = ""
    category: str = ""
    subcategory: str = ""
    what_changed: str = ""
    quote: str = ""
    confidence: str = "low"
    grounded: bool = False

    @property
    def applicable(self) -> bool:
        """Safe to offer as a one-click approval."""
        return (
            self.grounded
            and self.confidence in ("high", "medium")
            and bool(self.content.strip())
        )


_NEW_SYSTEM = """You maintain the Morgan State University Office of Research Administration knowledge base.

You are given the text of a document published on morgan.edu that the knowledge base does not yet cover. Write the knowledge base entry for it.

Return STRICT JSON, no markdown fence:
{
  "title": "a short human title, e.g. 'PI Handbook 5 - Grant-Related Processes'",
  "content": "the knowledge base entry",
  "category": "pre_award|post_award|research_compliance|trainings|resources|about|policies_and_guidelines|funding_sources",
  "subcategory": "a short slug",
  "quote": "a VERBATIM span copied from the document text. Copy exactly; do not paraphrase or fix anything.",
  "confidence": "high"|"medium"|"low"
}

Rules for content:
  * A concise reference a research administrator can act on — the facts, rules,
    deadlines, rates, contacts and required steps. Not a page dump, and not a
    description of what the document is "about".
  * Every statement must be supported by the document text. Invent nothing.
  * If the text is too fragmentary to write a useful entry, set confidence "low".
"""

_UPDATE_SYSTEM = """You maintain the Morgan State University Office of Research Administration knowledge base.

A source document on morgan.edu changed. You are given the CURRENT document text and the EXISTING knowledge base entry, which was written from an earlier version.

Update the entry so it matches the current document.

Return STRICT JSON, no markdown fence:
{
  "content": "the full updated knowledge base entry",
  "what_changed": "one sentence, concrete — name the old and the new value",
  "quote": "a VERBATIM span copied from the CURRENT document text showing the change",
  "confidence": "high"|"medium"|"low"
}

Rules — read these carefully:
  * You are EDITING, not rewriting. Change only what the current document
    contradicts.
  * PRESERVE every detail the current document does not address. The existing
    entry contains hand-written material that does not appear in the document at
    all — committee member lists, staff phone numbers and offices, historical
    notes. Deleting any of it is a failure, even if the document never mentions it.
  * Invent nothing absent from the current document.
"""


def _default_generate(prompt: str, system: str) -> str:
    from google import genai
    from google.genai import types

    client = genai.Client(
        vertexai=True,
        project=os.getenv("GOOGLE_CLOUD_PROJECT"),
        # gemini-3.6-flash 404s in us-central1 and answers only on `global`.
        # Getting this wrong makes every call fail, and since a failure degrades
        # to "no draft", the run would look clean while proposing nothing.
        location=os.getenv(
            "SCRAPE_MODEL_LOCATION", os.getenv("GOOGLE_CLOUD_LOCATION", "us-central1")
        ),
    )
    response = client.models.generate_content(
        model=MODEL,
        contents=prompt,
        config=types.GenerateContentConfig(
            system_instruction=system,
            temperature=0.1,
            max_output_tokens=8192,
            # Thinking tokens eat the output budget and truncate the JSON, which
            # silently degrades every draft to "could not be produced".
            thinking_config=types.ThinkingConfig(thinking_budget=0),
        ),
    )
    return getattr(response, "text", "") or ""


def _run(prompt: str, system: str, generate: Optional[Callable]) -> Optional[dict]:
    generate = generate or _default_generate
    try:
        return _parse(generate(prompt, system))
    except Exception as e:
        log.warning("File adjudication failed: %s", e)
        return None


def _finish(draft: Draft, text: str, label: str) -> Draft:
    """Apply the grounding rule and normalise confidence."""
    draft.grounded = _quote_in(draft.quote, text)
    if not draft.grounded:
        log.info("Ungrounded draft for %s; reporting without one", label)
        draft.quote = ""
        draft.confidence = "low"
    if draft.confidence not in ("high", "medium", "low"):
        draft.confidence = "low"
    return draft


def draft_new(file_text: str, url: str, title_hint: str = "",
              generate: Optional[Callable] = None) -> Draft:
    """Write a KB entry for a document the KB does not cover. Never raises."""
    text = (file_text or "")[:_MAX_FILE_CHARS]
    if not text.strip():
        # No text layer (scanned PDF) or an unsupported format. Report it; never
        # draft from an empty read.
        return Draft(what_changed="The file has no readable text — review it by hand.")

    prompt = (
        f"DOCUMENT URL: {url}\n"
        f"SUGGESTED TITLE: {title_hint or url.rsplit('/', 1)[-1]}\n\n"
        f"=== DOCUMENT TEXT ===\n{text}\n"
    )
    data = _run(prompt, _NEW_SYSTEM, generate)
    if not isinstance(data, dict):
        return Draft(what_changed="A draft could not be produced for this file.")

    draft = Draft(
        title=str(data.get("title") or "").strip()[:300],
        content=str(data.get("content") or "").strip(),
        category=str(data.get("category") or "").strip(),
        subcategory=str(data.get("subcategory") or "").strip(),
        quote=str(data.get("quote") or "").strip(),
        confidence=str(data.get("confidence") or "low").lower(),
    )
    return _finish(draft, text, url)


def draft_update(file_text: str, stored_content: str, doc_title: str = "",
                 generate: Optional[Callable] = None) -> Draft:
    """Update ONE existing entry from its changed source file. Never raises.

    Called once per derived document. The file is never re-split across the
    documents that came from it — each call sees this file plus that document's
    own content, so a bad draft damages one document rather than 22.
    """
    text = (file_text or "")[:_MAX_FILE_CHARS]
    if not text.strip() or not (stored_content or "").strip():
        return Draft(
            what_changed="The file changed but could not be compared automatically."
        )

    prompt = (
        f"ENTRY TITLE: {doc_title}\n\n"
        f"=== CURRENT DOCUMENT TEXT ===\n{text}\n\n"
        f"=== EXISTING KNOWLEDGE BASE ENTRY ===\n{stored_content}\n"
    )
    data = _run(prompt, _UPDATE_SYSTEM, generate)
    if not isinstance(data, dict):
        return Draft(
            what_changed="The file changed. No draft was produced — review it by hand."
        )

    draft = Draft(
        content=str(data.get("content") or "").strip(),
        what_changed=str(data.get("what_changed") or "").strip()[:1000],
        quote=str(data.get("quote") or "").strip(),
        confidence=str(data.get("confidence") or "low").lower(),
    )
    return _finish(draft, text, doc_title or "entry")
