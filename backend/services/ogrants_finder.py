"""
Open Grants (ogrants.org) live sample feed
==========================================
Augments the curated Sample Proposals Library with researcher-shared proposals
from the community-maintained Open Grants project (github.com/weecology/ogrants).

Like the Grants.gov Opportunity Finder, this is a LIVE source behind a hard
boundary:
  * Fixed, trusted host (api.github.com) -> no SSRF surface.
  * Graceful fallback: any failure returns [] (or the last good cache) -- it
    never fabricates and never breaks the Samples page.
  * Cached in-process (6h TTL) so a page load doesn't hit GitHub each time and
    we stay far under the unauthenticated 60-req/hr limit.

DATA MODEL (verified): each grant is a Jekyll collection file `_grants/*.md`
holding only YAML front-matter (title, author, year, institution, link, funder,
program, status) -- NO full proposal text. The `link` points OUT to externally
hosted material (GitHub repo, Google Doc, figshare, personal site). So every
entry is a `type: "link"` card -- we never rehost copyrighted proposals.
"""
from __future__ import annotations

import io
import re
import tarfile
from threading import Lock
from typing import Optional

import requests
from cachetools import TTLCache

# --- GitHub boundary --------------------------------------------------------
# Tarball of the default branch: one request returns the whole repo. requests
# follows the 302 to codeload.github.com (trusted GitHub infra), same "fixed
# trusted host" stance as opportunity_finder's Grants.gov calls.
_GH_TARBALL = "https://api.github.com/repos/weecology/ogrants/tarball/HEAD"
_TIMEOUT = 20
_CACHE_TTL = 21_600  # 6 hours
_HEADERS = {"Accept": "application/vnd.github+json", "User-Agent": "ora-navigator"}

_KEY = "index"
_cache: TTLCache = TTLCache(maxsize=1, ttl=_CACHE_TTL)
_lock = Lock()
_last_good: list = []  # served if a refresh fails after the TTL expires


# ===========================================================================
# Parsing / normalization (pure; no network)
# ===========================================================================

def _parse_front_matter(md: str) -> dict:
    """Parse a Jekyll `_grants/*.md` file's flat YAML front-matter into a dict.
    Tiny, dependency-free (no PyYAML): the files are flat `key: value` pairs.
    Strips the `---` fences and surrounding quotes. Lower-cases keys."""
    if not md:
        return {}
    text = md.strip()
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) >= 2:
            text = parts[1]
    fm: dict = {}
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or ":" not in line:
            continue
        key, _, val = line.partition(":")
        key = key.strip().lower()
        # Flat keys only; skip indented continuations / list items.
        if not key or " " in key or line[:1].isspace():
            continue
        fm[key] = val.strip().strip("'\"").strip()
    return fm


def _first_https(link: str) -> str:
    """First https:// URL in a (possibly comma/space-separated) link field."""
    for part in re.split(r"[,\s]+", link or ""):
        part = part.strip().rstrip(",")
        if part.startswith("https://"):
            return part
    return ""


def _categories_for(funder: str, program: str) -> list:
    """Map a grant's funder/program onto the existing Samples category chips
    (NSF / NIH / Foundations / Early-career). Substring match, defensive.
    Unknown funders return [] -- the entry still shows under 'All', just not in
    a chip."""
    f = (funder or "").lower()
    p = (program or "").lower()
    cats: list = []
    if "national science foundation" in f or re.search(r"\bnsf\b", f):
        cats.append("NSF")
    if ("national institutes of health" in f or re.search(r"\bnih\b", f)
            or any(t in f for t in ("niaid", "nci", "nimh", "nigms", "ninds", "nhlbi"))):
        cats.append("NIH")
    if any(t in f for t in ("foundation", "trust", "sloan", "moore", "wellcome",
                            "czi", "chan zuckerberg", "templeton", "navigation fund",
                            "simons", "kavli", "packard")):
        cats.append("Foundations")
    if any(t in p for t in ("career", "early career", "k99", "f31", "f32",
                            "fellowship", "postdoc")):
        cats.append("Early-career")
    return cats


def _normalize(fm: dict, slug: str) -> Optional[dict]:
    """Front-matter -> a Samples card dict matching the static SAMPLE_PROPOSALS
    shape, or None to skip (no title or no usable https link -> never fabricate)."""
    title = (fm.get("title") or "").strip()
    url = _first_https(fm.get("link"))
    if not title or not url:
        return None
    funder = (fm.get("funder") or fm.get("institution") or "Open Grants").strip()
    program = (fm.get("program") or "").strip()
    year = (fm.get("year") or "").strip()
    status = (fm.get("status") or "").strip().lower()
    kind = " · ".join([x for x in (program, year, status) if x]) or "Researcher-shared proposal"
    why = ("Researcher-shared via Open Grants (ogrants.org)"
           + (f" — a real {status} proposal." if status else "."))
    return {
        "id": f"ogrants-{slug}",
        "type": "link",          # always: we link out, never rehost
        "title": title,
        "source": funder,
        "url": url,
        "categories": _categories_for(funder, program),
        "kind": kind,
        "access": "free",
        "why": why,
        "community": True,       # drives the "Community" badge in the UI
    }


# ===========================================================================
# Live fetch (cached; graceful [])
# ===========================================================================

def _download_and_parse() -> list:
    """Download the repo tarball and normalize every `_grants/*.md` entry.
    One HTTP request. Raises on network error (caught by fetch_index)."""
    resp = requests.get(_GH_TARBALL, timeout=_TIMEOUT, headers=_HEADERS)
    resp.raise_for_status()
    out: list = []
    with tarfile.open(fileobj=io.BytesIO(resp.content), mode="r:gz") as tar:
        for member in tar.getmembers():
            name = member.name
            if not member.isfile() or "/_grants/" not in name or not name.endswith(".md"):
                continue
            f = tar.extractfile(member)
            if not f:
                continue
            try:
                md = f.read().decode("utf-8", "replace")
            except Exception:  # noqa: BLE001
                continue
            slug = name.rsplit("/", 1)[-1][:-3]  # filename stem (drop ".md")
            card = _normalize(_parse_front_matter(md), slug)
            if card:
                out.append(card)
    return out


def fetch_index() -> list:
    """Return the cached, normalized ogrants entries. Refreshes from GitHub when
    the cache is cold/expired; on any failure serves the last good result, or []
    if we've never succeeded. Never raises."""
    global _last_good
    with _lock:
        cached = _cache.get(_KEY)
        if cached is not None:
            return cached
        try:
            data = _download_and_parse()
        except Exception as e:  # noqa: BLE001 -- never break the Samples page
            print(f"   [OGRANTS] fetch failed: {e}")
            data = []
        if data:
            _cache[_KEY] = data
            _last_good = data
            return data
        return _last_good or []


def list_community_samples(category: Optional[str] = None) -> list:
    """Public entry: live ogrants entries, optionally filtered to one category
    chip. [] on any failure (never fabricates)."""
    items = fetch_index()
    if not category:
        return list(items)
    return [c for c in items if category in (c.get("categories") or [])]


def clear_cache() -> None:
    """Test/ops helper: drop the in-process cache and last-good fallback."""
    global _last_good
    with _lock:
        _cache.clear()
        _last_good = []
