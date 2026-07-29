"""Gemini-powered fetch: gemini-3.6-flash reads each page via the URL Context tool.

Drop-in alternative to crawler.py. It yields the same PageResult objects, so
run.py's loop, the fingerprint gate and the adjudicator all work unchanged —
only the thing holding the page open is different. Selected with
`--engine=gemini` or SCRAPE_ENGINE=gemini.

Two differences from the Playwright engine that callers must understand:

  * NO LINK DISCOVERY. The model reads the URLs it is given and does not walk
    the site. The URL list therefore comes from the KB's own snapshot index
    (the 59 morgan.edu pages the documents were built from), not from crawling.
    A page added to morgan.edu that no document references is invisible to this
    engine — the Playwright engine is the one that finds new pages.

  * TEXT IS A RENDERING, NOT A TRANSCRIPT. The model will not reproduce a page
    verbatim (see RECITATION below), so `text` is its markdown extraction of the
    page. It is close to the page but not byte-identical to it, and it varies
    slightly between runs even when the page has not moved. Consequences:
      - fingerprints are noisier, so unchanged pages can still register as moved
      - the adjudicator's quote check verifies against this extraction rather
        than against the page itself, which is a weaker guarantee than the
        Playwright engine gives (golden rule 2)

RECITATION is the failure mode to know about. Asking Gemini for a page's content
can trip its safety filter for reproducing source material, which returns an
EMPTY response with finishReason=RECITATION. Measured on live ORA pages: 4 of 5
extract fine; the IACUC SOPs page (a list of 50 items) is blocked every time.

A blocked page is reported UNREADABLE, never as an empty or deleted page. That
distinction is the whole ballgame: `looks_unreadable()` already stops an empty
read from being treated as "the content was removed", and a RECITATION block is
exactly that case wearing a different hat.
"""

from __future__ import annotations

import json
import logging
import os
import time
import urllib.error
import urllib.request
from typing import Callable, Iterator, Optional

from crawler import MAX_PAGES, PageResult, normalize_url

log = logging.getLogger(__name__)

MODEL = os.getenv("SCRAPE_MODEL", "gemini-3.6-flash")
LOCATION = os.getenv("SCRAPE_MODEL_LOCATION", os.getenv("GOOGLE_CLOUD_LOCATION", "us-central1"))

# Deliberately asks for an EXTRACTION, not a reproduction. "Reproduce the full
# text verbatim" trips RECITATION on far more pages than this phrasing does.
_PROMPT = (
    "Extract the content of {url} into clean structured markdown for a knowledge "
    "base: headings, every list item, contact details, dates, phone numbers and "
    "email addresses. Include items inside collapsible or accordion sections. "
    "Omit site navigation, header, footer and cookie notices. If the page cannot "
    "be read, reply with exactly: UNREADABLE"
)

_TIMEOUT_S = 90
_POLITENESS_DELAY_S = 0.2


def _endpoint() -> str:
    host = (
        "https://aiplatform.googleapis.com"
        if LOCATION == "global"
        else f"https://{LOCATION}-aiplatform.googleapis.com"
    )
    project = os.getenv("GOOGLE_CLOUD_PROJECT")
    return (
        f"{host}/v1beta1/projects/{project}/locations/{LOCATION}"
        f"/publishers/google/models/{MODEL}:generateContent"
    )


class _Client:
    """Minimal REST caller for the URL Context tool.

    Deliberately NOT the google-genai SDK: this image pins google-genai==1.14.0
    to match backend/requirements.txt (the two share db.py, models.py and
    datastore_manager.py, and a client drift would mean the job and the API
    disagree about the same datastore). 1.14.0 has no url_context support at
    all — types.Tool has no such field — so using the SDK here would force an
    upgrade that the pin exists to prevent. The REST shape is stable and needs
    nothing outside the standard library.
    """

    def __init__(self):
        import google.auth
        import google.auth.transport.requests

        self._creds, _ = google.auth.default(
            scopes=["https://www.googleapis.com/auth/cloud-platform"]
        )
        self._request = google.auth.transport.requests.Request()

    def _token(self) -> str:
        if not self._creds.valid:
            self._creds.refresh(self._request)
        return self._creds.token

    def generate(self, prompt: str) -> dict:
        body = json.dumps({
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "tools": [{"urlContext": {}}],
            "generationConfig": {
                "temperature": 0,
                "maxOutputTokens": 8192,
                # Same reasoning as the adjudicator: thinking tokens eat the
                # output budget and truncate the extraction.
                "thinkingConfig": {"thinkingBudget": 0},
            },
        }).encode("utf-8")

        req = urllib.request.Request(
            _endpoint(),
            data=body,
            headers={
                "Authorization": f"Bearer {self._token()}",
                "Content-Type": "application/json",
            },
        )
        with urllib.request.urlopen(req, timeout=_TIMEOUT_S) as resp:
            return json.loads(resp.read().decode("utf-8"))


def _client():
    return _Client()


def fetch_page(client, url: str) -> PageResult:
    """One page, one model call. Never raises — failures become PageResult.error."""
    try:
        data = client.generate(_PROMPT.format(url=url))
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = json.loads(e.read().decode("utf-8"))["error"]["message"]
        except Exception:
            pass
        return PageResult(url=url, error=f"model call failed: HTTP {e.code} {detail}"[:300])
    except Exception as e:
        return PageResult(url=url, error=f"model call failed: {e}"[:300])

    if "error" in data:
        return PageResult(url=url, error=f"model error: {data['error'].get('message','')}"[:300])

    candidate = (data.get("candidates") or [{}])[0]
    text = "".join(
        p.get("text", "") for p in candidate.get("content", {}).get("parts", [])
    ).strip()
    finish = str(candidate.get("finishReason") or "")
    retrieval = ""
    try:
        meta = candidate.get("urlContextMetadata") or {}
        retrieval = str((meta.get("urlMetadata") or [{}])[0].get("urlRetrievalStatus") or "")
    except Exception:
        pass

    # An empty body is NOT an empty page. RECITATION means the model read the
    # page and refused to hand back the content; a retrieval failure means it
    # never got there. Both are unreadable, and neither is evidence that
    # anything changed.
    if not text or text == "UNREADABLE":
        reason = "RECITATION" if "RECITATION" in finish.upper() else (finish or "empty response")
        if retrieval and "SUCCESS" not in retrieval.upper():
            reason = f"{reason} / {retrieval}"
        return PageResult(url=url, error=f"gemini returned no content ({reason})")

    if retrieval and "SUCCESS" not in retrieval.upper():
        return PageResult(url=url, error=f"url retrieval failed ({retrieval})")

    # First markdown heading is the closest thing to a <title> the model gives us.
    title = ""
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("#"):
            title = line.lstrip("#").strip()
            break

    return PageResult(url=url, status=200, title=title[:300], text=text, links=[])


def crawl(
    seeds: Optional[list[str]] = None,
    max_pages: int = MAX_PAGES,
    on_page: Optional[Callable[[PageResult, int, int], None]] = None,
    should_stop: Optional[Callable[[], bool]] = None,
    urls: Optional[list[str]] = None,
) -> Iterator[PageResult]:
    """Yield one PageResult per URL. Same contract as crawler.crawl().

    `urls` is the work list. run.py passes the KB's known morgan.edu URLs,
    because this engine cannot discover pages on its own.
    """
    targets = [normalize_url(u) for u in (urls or seeds or [])]
    targets = [u for u in dict.fromkeys(targets) if u][:max_pages]
    total = len(targets)
    log.info("Gemini engine: %d URLs, model=%s location=%s", total, MODEL, LOCATION)

    client = _client()
    done = 0

    for url in targets:
        if should_stop and should_stop():
            log.info("Cancelled after %d pages", done)
            break

        result = fetch_page(client, url)
        done += 1

        if result.unreadable:
            log.warning("Unreadable: %s (%s)", url, result.error[:80])
        else:
            log.info("Read %d chars: %s", len(result.text), url)

        if on_page:
            on_page(result, done, total)
        yield result

        time.sleep(_POLITENESS_DELAY_S)
