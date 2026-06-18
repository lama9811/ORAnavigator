# -*- coding: utf-8 -*-
"""
URL fetcher for the Solicitation Ingestion feature.
===================================================
Fetches a user-supplied solicitation URL and returns plain text ready for
``solicitation_extractor.extract_from_text``. Handles two cases:

    * a direct link to a PDF  -> text via the existing pdfplumber extractor
    * a server-rendered HTML page -> visible text via lxml

This module is security-critical: it performs server-side requests to a URL the
*user* controls, so it includes an SSRF guard.

DNS-rebinding / TOCTOU defense: we resolve each host (and every redirect hop)
ourselves, reject any private / loopback / link-local / reserved address, and
then **pin the connection to that exact validated IP** via a custom transport
adapter -- so requests does NOT perform a second, unchecked DNS lookup that an
attacker could point at an internal address. The original hostname is preserved
for the Host header, TLS SNI, and certificate verification. This blocks
localhost, internal ranges, and the cloud metadata endpoint 169.254.169.254.

JavaScript-only pages are out of scope: a simple fetch sees no rendered content
and the extractor will return nothing, surfaced as a clear FetchError.
"""

import ipaddress
import os
import re
import socket
import time
from typing import Optional
from urllib.parse import urlparse, urljoin, urlunparse

import requests
from requests.adapters import HTTPAdapter

# 25 MB, matching the PDF upload cap in main.py.
_DEFAULT_MAX_BYTES = 25 * 1024 * 1024
_MAX_REDIRECTS = 5
# (connect timeout, read timeout)
_TIMEOUT = (5, 20)
_USER_AGENT = (
    "Mozilla/5.0 (compatible; ORANavigatorBot/1.0; +https://ora.inavigator.ai) "
    "solicitation-importer"
)
# A realistic browser identity + headers. Many CDNs serve a normal page to a
# browser-looking client but 404/403 an obvious bot. Used for both the direct
# fetch and the reader request.
_BROWSER_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,application/pdf,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Upgrade-Insecure-Requests": "1",
}
# Some funder sites (e.g. NSF / Akamai) return 403/404 to requests from cloud
# datacenter IPs even though the page is public in a browser. When a direct
# fetch is blocked, we retry through a read-only reader service that fetches the
# page from its own IP and returns clean text. Only PUBLIC user URLs reach it
# (the SSRF guard runs first), and the reader host is a fixed constant.
# Firecrawl: a hosted scraper (real browser, non-blocked IPs, handles JS) that
# returns clean markdown. Preferred when FIRECRAWL_API_KEY is set. v2 scrape API.
_FIRECRAWL_URL = "https://api.firecrawl.dev/v2/scrape"
_READER_PREFIX = "https://r.jina.ai/"
# Secondary, no-key proxy that returns the raw page (we convert it ourselves).
_PROXY_PREFIX = "https://api.allorigins.win/raw?url="
# Per-attempt sleeps for the reader (len() + 1 = number of attempts). The free
# reader is IP rate-limited, so a couple of backed-off retries matter. Setting a
# JINA_API_KEY env var (free tier at jina.ai) lifts the limit and makes it solid.
_READER_BACKOFFS = (1.5, 4.0)
# A real solicitation page is large; anything tiny is a throttle/placeholder.
_READER_MIN_CHARS = 400
# HTTP statuses that look like a block / soft failure worth a reader retry.
_BLOCKED_STATUSES = (403, 404, 406, 410, 429, 451, 500, 502, 503, 504)


class FetchError(Exception):
    """A user-facing fetch failure. ``status`` is the HTTP status the API
    endpoint should return; ``message`` is safe to show the user."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.message = message
        self.status = status


def _ip_is_public(ip_str: str) -> bool:
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return False
    return not (ip.is_private or ip.is_loopback or ip.is_link_local
                or ip.is_reserved or ip.is_multicast or ip.is_unspecified)


def _resolve_public_ip(hostname: str) -> str:
    """Resolve ``hostname`` and return ONE validated public IP. Raises
    FetchError if resolution fails or ANY resolved address is non-public
    (an attacker returning a mix of public+internal IPs is rejected outright)."""
    if not hostname:
        raise FetchError("That URL has no host.", 400)
    try:
        infos = socket.getaddrinfo(hostname, None)
    except socket.gaierror:
        raise FetchError("Couldn't resolve that website's address.", 502)
    ips = [info[4][0] for info in infos]
    if not ips:
        raise FetchError("Couldn't resolve that website's address.", 502)
    for ip_str in ips:
        if not _ip_is_public(ip_str):
            raise FetchError(
                "That URL points to a non-public address and can't be fetched.",
                400,
            )
    return ips[0]


def _parse_http_url(url: str):
    """Scheme/host sanity check. Returns the parsed URL (does NOT resolve DNS)."""
    url = (url or "").strip()
    if not url:
        raise FetchError("Please enter a URL.", 400)
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise FetchError("Only http and https URLs are supported.", 400)
    if not parsed.hostname:
        raise FetchError("That doesn't look like a valid URL.", 400)
    return parsed


class _PinnedIPAdapter(HTTPAdapter):
    """Transport adapter that connects to a pre-validated IP instead of letting
    urllib3 re-resolve the hostname (closing the DNS-rebinding TOCTOU window),
    while keeping the real hostname for the Host header, TLS SNI, and cert
    verification."""

    def __init__(self, dest_ip: str, **kwargs):
        self._dest_ip = dest_ip
        super().__init__(**kwargs)

    def send(self, request, **kwargs):
        parsed = urlparse(request.url)
        hostname = parsed.hostname
        port = parsed.port
        if parsed.scheme == "https":
            # Verify the cert against the real hostname and send correct SNI,
            # even though we connect to the pinned IP.
            self.poolmanager.connection_pool_kw["server_hostname"] = hostname
            self.poolmanager.connection_pool_kw["assert_hostname"] = hostname
        ip_host = f"[{self._dest_ip}]" if ":" in self._dest_ip else self._dest_ip
        new_netloc = f"{ip_host}:{port}" if port else ip_host
        request.url = urlunparse(parsed._replace(netloc=new_netloc))
        request.headers["Host"] = f"{hostname}:{port}" if port else hostname
        return super().send(request, **kwargs)


def _open(url: str, dest_ip: str, headers: dict) -> requests.Response:
    """One pinned GET (no auto-redirects, streamed). Factored out so tests can
    stub the network while the SSRF/redirect logic above stays real."""
    session = requests.Session()
    adapter = _PinnedIPAdapter(dest_ip)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    return session.get(url, headers=headers, timeout=_TIMEOUT,
                       allow_redirects=False, stream=True)


def _read_capped(resp: requests.Response, max_bytes: int) -> bytes:
    """Stream the body, aborting if it exceeds ``max_bytes``."""
    chunks = []
    total = 0
    for chunk in resp.iter_content(chunk_size=64 * 1024):
        if not chunk:
            continue
        total += len(chunk)
        if total > max_bytes:
            raise FetchError(
                f"That page is larger than {max_bytes // (1024 * 1024)} MB.", 413
            )
        chunks.append(chunk)
    return b"".join(chunks)


def _html_to_text(html_bytes: bytes) -> str:
    """Visible text from an HTML page via lxml: drop script/style/noscript, then
    collapse whitespace."""
    try:
        import lxml.html  # explicit dep in requirements (was transitive)
    except ImportError:  # pragma: no cover - lxml is always installed
        raise FetchError("Can't read web pages right now (missing parser).", 500)
    try:
        doc = lxml.html.fromstring(html_bytes)
    except Exception:
        raise FetchError("Couldn't parse that web page.", 422)
    for el in doc.xpath("//script | //style | //noscript | //template"):
        el.drop_tree()
    text = doc.text_content() or ""
    # Collapse runs of blank lines / spaces so the extractor gets clean prose.
    text = re.sub(r"[ \t\xa0]+", " ", text)
    text = re.sub(r"\n\s*\n\s*\n+", "\n\n", text)
    return text.strip()


def _looks_like_pdf(url: str, content_type: str, body: bytes) -> bool:
    if "application/pdf" in (content_type or "").lower():
        return True
    if body[:5] == b"%PDF-":
        return True
    path = urlparse(url).path.lower()
    return path.endswith(".pdf")


def _fetch_direct(url: str, max_bytes: int = _DEFAULT_MAX_BYTES) -> str:
    """Direct fetch: follow redirects (re-resolving + re-validating + re-pinning
    each hop) and return plain text. Raises FetchError on failure; the upstream
    HTTP status is preserved on the error so the caller can decide to retry via
    the reader fallback."""
    current = url
    headers = dict(_BROWSER_HEADERS)
    resp = None
    try:
        for _ in range(_MAX_REDIRECTS + 1):
            parsed = _parse_http_url(current)
            dest_ip = _resolve_public_ip(parsed.hostname)  # validate + pin target
            resp = _open(current, dest_ip, headers)
            if resp.is_redirect or resp.status_code in (301, 302, 303, 307, 308):
                location = resp.headers.get("Location")
                resp.close()
                resp = None
                if not location:
                    raise FetchError("That page redirected without a target.", 502)
                current = urljoin(current, location)  # re-validated next loop
                continue
            break
        else:
            raise FetchError("That URL redirected too many times.", 502)

        if resp.status_code >= 400:
            raise FetchError(
                f"The site returned an error ({resp.status_code}) for that URL.",
                resp.status_code,
            )

        content_type = resp.headers.get("Content-Type", "")
        body = _read_capped(resp, max_bytes)
    except FetchError:
        raise
    except requests.exceptions.Timeout:
        raise FetchError("That site took too long to respond. Try again.", 504)
    except requests.exceptions.RequestException:
        raise FetchError("Couldn't reach that URL.", 502)
    finally:
        if resp is not None:
            resp.close()

    if not body:
        raise FetchError("That URL returned an empty page.", 422)

    if _looks_like_pdf(current, content_type, body):
        from services import solicitation_extractor as _sx
        text = _sx.extract_text_from_pdf(body)
    else:
        text = _html_to_text(body)

    if not text or not text.strip():
        raise FetchError(
            "Couldn't pull any readable text from that URL. If it's a "
            "JavaScript-heavy or login-only page, download the PDF and upload it "
            "instead.",
            422,
        )
    return text


def _fetch_via_firecrawl(url: str, max_bytes: int) -> Optional[str]:
    """Preferred scraper when FIRECRAWL_API_KEY is set: Firecrawl runs a real
    browser from non-blocked IPs (handles JS, bot walls, PDFs) and returns clean
    markdown. The user URL has ALREADY passed the SSRF guard; Firecrawl's host is
    a fixed constant. Returns markdown text, or None (no key / failure / thin)."""
    key = (os.getenv("FIRECRAWL_API_KEY") or "").strip()
    # "unset" is the placeholder the deploy creates when no real key exists yet
    # (see cloudbuild.yaml self-heal) -- treat it as no key so we skip the call.
    if not key or key.lower() == "unset":
        return None
    try:
        resp = requests.post(
            _FIRECRAWL_URL,
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json={"url": url, "formats": ["markdown"], "onlyMainContent": True},
            timeout=(5, 45),   # Firecrawl renders the page, so allow longer
        )
    except requests.exceptions.RequestException:
        return None
    if resp.status_code >= 400:
        print(f"   [URL-FIRECRAWL] status {resp.status_code}")
        return None
    try:
        data = resp.json()
    except ValueError:
        return None
    md = (((data or {}).get("data") or {}).get("markdown") or "").strip()
    if len(md) >= _READER_MIN_CHARS:
        print(f"   [URL-FIRECRAWL] returned {len(md)} chars")
        return md[:max_bytes]
    return None


def _fetch_via_reader(url: str, max_bytes: int) -> Optional[str]:
    """Fallback: fetch the page through a read-only reader proxy (fetches from
    its own IP and returns clean text/markdown, including for PDF links and
    JS-rendered pages). The user URL has ALREADY passed the SSRF guard; the
    reader host is a fixed constant.

    Retries with backoff on rate-limit / thin responses (the free tier is IP
    rate-limited). If JINA_API_KEY is set, it's sent as a Bearer token, which
    raises the limit and makes this reliable from a server. Returns usable text,
    or None if every attempt failed."""
    headers = dict(_BROWSER_HEADERS)
    headers["Accept"] = "text/plain, text/markdown, */*"
    headers["X-Return-Format"] = "text"
    key = (os.getenv("JINA_API_KEY") or "").strip()
    if key:
        headers["Authorization"] = f"Bearer {key}"

    for attempt in range(len(_READER_BACKOFFS) + 1):
        if attempt:
            time.sleep(_READER_BACKOFFS[attempt - 1])
        try:
            resp = requests.get(_READER_PREFIX + url, headers=headers,
                                timeout=_TIMEOUT, stream=True)
        except requests.exceptions.RequestException:
            continue
        try:
            status = resp.status_code
            if status == 429 or status >= 500:
                continue                      # transient -> back off and retry
            if status >= 400:
                return None                   # hard failure, won't improve
            try:
                body = _read_capped(resp, max_bytes)
            except FetchError:
                return None
        finally:
            resp.close()
        text = body.decode("utf-8", "replace")
        text = re.sub(r"\n\s*\n\s*\n+", "\n\n", text).strip()
        if len(text) >= _READER_MIN_CHARS:    # got real content
            print(f"   [URL-READER] reader returned {len(text)} chars (attempt {attempt + 1})")
            return text
        # too thin (throttle/placeholder) -> retry
    print("   [URL-READER] reader produced no usable content after retries")
    return None


def _fetch_via_proxy(url: str, max_bytes: int) -> Optional[str]:
    """Secondary no-key proxy (allorigins) that returns the RAW page; we convert
    HTML->text or PDF->text ourselves. Backup for when the reader is throttled."""
    from urllib.parse import quote
    try:
        resp = requests.get(_PROXY_PREFIX + quote(url, safe=""),
                            headers=_BROWSER_HEADERS, timeout=_TIMEOUT, stream=True)
    except requests.exceptions.RequestException:
        return None
    try:
        if resp.status_code >= 400:
            return None
        content_type = resp.headers.get("Content-Type", "")
        try:
            body = _read_capped(resp, max_bytes)
        except FetchError:
            return None
    finally:
        resp.close()
    if not body:
        return None
    try:
        if _looks_like_pdf(url, content_type, body):
            from services import solicitation_extractor as _sx
            text = _sx.extract_text_from_pdf(body)
        else:
            text = _html_to_text(body)
    except FetchError:
        return None
    if text and len(text.strip()) >= _READER_MIN_CHARS:
        print(f"   [URL-READER] proxy returned {len(text)} chars")
        return text.strip()
    return None


def fetch_solicitation_text(url: str, max_bytes: int = _DEFAULT_MAX_BYTES) -> str:
    """Fetch a solicitation URL and return plain text for the extractor. Tries a
    direct (IP-pinned, SSRF-guarded) fetch first; if the site blocks our server
    (403/404/etc.) or returns no readable text, retries once through a read-only
    reader proxy. Raises FetchError if both fail."""
    # SSRF gate on the ORIGINAL user URL up front: only public http(s) hosts may
    # be fetched, whether directly OR handed to the reader proxy.
    parsed = _parse_http_url(url)
    _resolve_public_ip(parsed.hostname)
    try:
        return _fetch_direct(url, max_bytes)
    except FetchError as e:
        retryable = e.status in _BLOCKED_STATUSES or "readable text" in e.message or "empty" in e.message
        if not retryable:
            raise
        # Site blocks our server IP -> try scrapers that fetch from their own
        # IPs: Firecrawl first (best, when a key is set), then the Jina reader,
        # then allorigins as a no-key backup.
        for strategy in (_fetch_via_firecrawl, _fetch_via_reader, _fetch_via_proxy):
            text = strategy(url, max_bytes)
            if text:
                return text
        raise
