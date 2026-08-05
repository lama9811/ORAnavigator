"""
Bounded breadth-first crawl of morgan.edu's Office of Research Administration
section, with a real browser.

Playwright rather than plain HTTP because 249 of the 382 knowledge base
documents are marked `playwright_verified` — the IRB and IACUC content lives
inside collapsed accordions that do not exist in the served HTML. Fetching those
pages without a browser returns a page that LOOKS like the content was deleted,
which would report a false change on roughly two-thirds of the KB.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Callable, Iterator, Optional

from extractors import kind_for_url
from fingerprint import (
    fingerprint,
    is_in_scope,
    looks_unreadable,
    normalize_text,
    normalize_url,
)

log = logging.getLogger(__name__)

SEED_URLS = [
    "https://www.morgan.edu/office-of-research-administration",
]

# The section is ~382 pages. The cap is a runaway guard for the case where a
# calendar or search page generates an unbounded URL space, not a real limit.
MAX_PAGES = 600
PAGE_TIMEOUT_MS = 25_000
POLITENESS_DELAY_S = 0.4

# Chrome/edge regions that appear on every page and would otherwise make a
# single nav edit look like all 382 pages changed at once.
_STRIP_SELECTORS = [
    "header", "footer", "nav", "noscript", "script", "style",
    "[role=banner]", "[role=navigation]", "[role=contentinfo]",
    ".breadcrumb", ".skip-link", "#sidebar", ".sidebar-nav",
]

# Where the real content lives, best first.
_CONTENT_SELECTORS = ["main", "article", "[role=main]", "#main-content", ".content", "body"]


@dataclass
class PageResult:
    url: str
    status: int = 0
    title: str = ""
    text: str = ""
    links: list[str] = field(default_factory=list)
    # Document and form URLs seen on this page. Kept separately from `links`
    # because is_in_scope() deliberately rejects /Documents/... — page crawling
    # must not follow them, but the file phase needs to know they exist. This is
    # the only way a file with no KB document is ever discovered.
    file_links: list[str] = field(default_factory=list)
    error: str = ""

    @property
    def unreadable(self) -> bool:
        return bool(self.error) or looks_unreadable(self.text, self.status)

    @property
    def digest(self) -> str:
        return fingerprint(self.text)


def _collect_file_links(raw_links, base: str) -> list[str]:
    """Absolute morgan.edu document and form URLs found on a page.

    These are exactly the links is_in_scope() throws away: ORA's files live
    under /Documents/ADMINISTRATION/OFFICES/ora/, outside the page prefix, so
    the crawl must not follow them — but discarding them at the point of
    discovery is why a newly posted PDF was invisible to the scrape.
    """
    out: list[str] = []
    for link in raw_links:
        url = normalize_url(link, base)
        if not url:
            continue
        kind = kind_for_url(url)
        if kind not in ("file", "form"):
            continue
        # Our own documents only. A funder's PDF linked from a page is not ours
        # to track, but a DocuSign form we route people to is.
        if kind == "file" and "morgan.edu" not in url.lower():
            continue
        if url not in out:
            out.append(url)
    return out


def _expand_accordions(page) -> None:
    """Open every collapsed region so its text is in the DOM before extraction.

    This is the step that made the original KB build reach for Playwright at
    all. Best-effort by design: a page with no accordions must not fail here.
    """
    try:
        page.eval_on_selector_all(
            "details:not([open])", "els => els.forEach(e => e.open = true)"
        )
    except Exception:
        pass

    for selector in ('[aria-expanded="false"]', ".accordion-button.collapsed", ".accordion-toggle"):
        try:
            for handle in page.query_selector_all(selector)[:60]:
                try:
                    handle.click(timeout=1200)
                except Exception:
                    continue
        except Exception:
            continue

    try:
        page.wait_for_timeout(350)
    except Exception:
        pass


def _extract(page) -> tuple[str, list[str]]:
    """Main-content text plus in-scope links, with site chrome removed."""
    try:
        page.eval_on_selector_all(
            ",".join(_STRIP_SELECTORS), "els => els.forEach(e => e.remove())"
        )
    except Exception:
        pass

    text = ""
    for selector in _CONTENT_SELECTORS:
        try:
            node = page.query_selector(selector)
            if node:
                text = node.inner_text() or ""
                if normalize_text(text):
                    break
        except Exception:
            continue

    links: list[str] = []
    try:
        links = page.eval_on_selector_all(
            "a[href]", "els => els.map(e => e.getAttribute('href'))"
        ) or []
    except Exception:
        pass

    return text, [l for l in links if l]


def fetch_page(page, url: str) -> PageResult:
    result = PageResult(url=url)
    try:
        response = page.goto(url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT_MS)
        result.status = response.status if response else 0
        try:
            page.wait_for_load_state("networkidle", timeout=6_000)
        except Exception:
            pass        # a page with polling widgets never goes idle; not fatal

        result.title = (page.title() or "").strip()
        _expand_accordions(page)
        text, raw_links = _extract(page)
        result.text = text
        result.links = [
            u for u in (normalize_url(l, url) for l in raw_links) if u and is_in_scope(u)
        ]
        result.file_links = _collect_file_links(raw_links, url)
    except Exception as e:
        result.error = str(e)[:400]
    return result


def crawl(
    seeds: Optional[list[str]] = None,
    max_pages: int = MAX_PAGES,
    on_page: Optional[Callable[[PageResult, int, int], None]] = None,
    should_stop: Optional[Callable[[], bool]] = None,
) -> Iterator[PageResult]:
    """Yield one PageResult per in-scope page, breadth-first.

    `on_page(result, done, found)` reports progress after each page — the job
    uses it to write the ScrapeRun row the UI polls. `should_stop()` is checked
    between pages so Cancel actually stops the crawl rather than just hiding it.
    """
    from playwright.sync_api import sync_playwright

    frontier = [normalize_url(u) for u in (seeds or SEED_URLS)]
    queued = {u for u in frontier if u}
    seen: set[str] = set()

    with sync_playwright() as pw:
        browser = pw.chromium.launch(args=["--no-sandbox", "--disable-dev-shm-usage"])
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (compatible; ORANavigatorKBSync/1.0; "
                "+https://ora.inavigator.ai) knowledge-base sync for morgan.edu"
            ),
            viewport={"width": 1280, "height": 2000},
        )
        page = context.new_page()
        # Images and fonts are pure cost here; we only ever read text.
        try:
            context.route(
                "**/*",
                lambda route: route.abort()
                if route.request.resource_type in ("image", "font", "media")
                else route.continue_(),
            )
        except Exception:
            pass

        try:
            while frontier and len(seen) < max_pages:
                if should_stop and should_stop():
                    log.info("Crawl cancelled after %d pages", len(seen))
                    break

                url = frontier.pop(0)
                if url in seen:
                    continue
                seen.add(url)

                result = fetch_page(page, url)

                for link in result.links:
                    if link not in seen and link not in queued:
                        queued.add(link)
                        frontier.append(link)

                if on_page:
                    on_page(result, len(seen), len(queued))
                yield result

                time.sleep(POLITENESS_DELAY_S)
        finally:
            try:
                context.close()
                browser.close()
            except Exception:
                pass
