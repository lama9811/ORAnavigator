"""
URL normalization and page fingerprinting.

Pure functions, no I/O and no Playwright, so the parts most likely to be wrong
are the parts that are cheapest to test.

The fingerprint is deliberately SENSITIVE: it collapses whitespace and nothing
else. It is a cheap gate, not a judgement — deciding whether a change matters is
the AI's job (see adjudicator.py). Trying to be clever here, by stripping dates
or copyright years, would silently hide the exact edits we care about most.
"""

from __future__ import annotations

import hashlib
import re
from urllib.parse import urlsplit, urlunsplit

ORA_PREFIX = "/office-of-research-administration"
ALLOWED_HOSTS = {"www.morgan.edu", "morgan.edu"}

# Query params that change the URL but never the page.
_JUNK_PARAMS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "fbclid", "gclid", "mc_cid", "mc_eid", "_ga",
}

# Assets and endpoints a page crawl should never enqueue.
_SKIP_SUFFIXES = (
    ".jpg", ".jpeg", ".png", ".gif", ".svg", ".webp", ".ico",
    ".css", ".js", ".zip", ".mp4", ".mp3", ".xlsx", ".xls",
    ".doc", ".docx", ".ppt", ".pptx",
)

_WS = re.compile(r"\s+")
_ZERO_WIDTH = re.compile(r"[​-‍﻿\xa0]")


def normalize_url(url: str, base: str = "") -> str:
    """Canonical form used for dedup and as the fingerprint key.

    Drops the fragment and tracking params, lowercases the host, and removes a
    trailing slash so `/pre-award` and `/pre-award/` are one page rather than
    two entries that each report the other as new.
    """
    if not url:
        return ""
    url = url.strip()
    if url.startswith("//"):
        url = "https:" + url
    elif url.startswith("/") and base:
        parts = urlsplit(base)
        url = urlunsplit((parts.scheme, parts.netloc, url, "", ""))
    elif url.startswith("/"):
        url = "https://www.morgan.edu" + url

    parts = urlsplit(url)
    if parts.scheme not in ("http", "https"):
        return ""

    query = "&".join(
        q for q in parts.query.split("&")
        if q and q.split("=")[0].lower() not in _JUNK_PARAMS
    )
    path = parts.path.rstrip("/") or "/"
    return urlunsplit((parts.scheme, parts.netloc.lower(), path, query, ""))


def is_in_scope(url: str) -> bool:
    """ORA section only — morgan.edu at large is tens of thousands of pages with
    no corresponding documents to compare against."""
    if not url:
        return False
    parts = urlsplit(url)
    if parts.netloc.lower() not in ALLOWED_HOSTS:
        return False
    path = parts.path.lower()
    if path.endswith(_SKIP_SUFFIXES):
        return False
    return path.startswith(ORA_PREFIX) or path == "/ora" or path.startswith("/ora/")


def normalize_text(text: str) -> str:
    """Whitespace-collapse only. Same principle as the evidence check in
    section_coach: a hard-wrapped copy and a single-line copy are the same
    text."""
    if not text:
        return ""
    return _WS.sub(" ", _ZERO_WIDTH.sub(" ", text)).strip()


def fingerprint(text: str) -> str:
    return hashlib.sha256(normalize_text(text).encode("utf-8")).hexdigest()


def looks_unreadable(text: str, status: int = 200, min_chars: int = 200) -> bool:
    """Did we fail to READ the page, as opposed to the page having changed?

    This distinction is the whole safety model. A 404 template, a failed JS
    render, or a site mid-deploy all produce a wildly different fingerprint —
    and treating that as "the page changed" would overwrite a good document with
    an error page. An unreadable page is not a change; we leave the document
    alone and report it separately.
    """
    if status != 200:
        return True
    stripped = normalize_text(text)
    if len(stripped) < min_chars:
        return True
    low = stripped.lower()
    return any(
        marker in low[:400]
        for marker in (
            "page not found",
            "404 error",
            "the page you requested could not be found",
            "access denied",
            "service unavailable",
        )
    )


def summarize_diff(old: str, new: str, width: int = 400) -> str:
    """A short human-readable hint about what moved, for the change report.

    Not a real diff — the UI renders that from previous_content/new_content.
    This is the one-line 'why am I looking at this'.
    """
    old_n, new_n = normalize_text(old), normalize_text(new)
    if old_n == new_n:
        return "no textual change"

    # Find the first divergence and show a window around it.
    i = 0
    limit = min(len(old_n), len(new_n))
    while i < limit and old_n[i] == new_n[i]:
        i += 1
    start = max(0, i - width // 4)
    delta = len(new_n) - len(old_n)
    sign = "+" if delta >= 0 else ""
    return (
        f"{sign}{delta} chars. From …{old_n[start:start + width]}… "
        f"to …{new_n[start:start + width]}…"
    )
