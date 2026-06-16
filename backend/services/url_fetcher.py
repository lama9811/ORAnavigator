# -*- coding: utf-8 -*-
"""
URL fetcher for the Solicitation Ingestion feature.
===================================================
Fetches a user-supplied solicitation URL and returns plain text ready for
``solicitation_extractor.extract_from_text``. Handles two cases:

    * a direct link to a PDF  -> text via the existing pdfplumber extractor
    * a server-rendered HTML page -> visible text via lxml

This module is security-critical: it performs server-side requests to a URL the
*user* controls, so it includes an SSRF guard. Every host (including each
redirect hop) is resolved and rejected if it maps to a private / loopback /
link-local / reserved IP -- this blocks localhost, internal ranges, and the
cloud metadata endpoint 169.254.169.254.

JavaScript-only pages are out of scope: a simple fetch sees no rendered content
and the extractor will return nothing, surfaced as a clear FetchError.
"""

import ipaddress
import re
import socket
from io import BytesIO
from urllib.parse import urlparse, urljoin

import requests

# 25 MB, matching the PDF upload cap in main.py.
_DEFAULT_MAX_BYTES = 25 * 1024 * 1024
_MAX_REDIRECTS = 5
# (connect timeout, read timeout)
_TIMEOUT = (5, 20)
_USER_AGENT = (
    "Mozilla/5.0 (compatible; ORANavigatorBot/1.0; +https://ora.inavigator.ai) "
    "solicitation-importer"
)


class FetchError(Exception):
    """A user-facing fetch failure. ``status`` is the HTTP status the API
    endpoint should return; ``message`` is safe to show the user."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.message = message
        self.status = status


def _assert_public_host(hostname: str) -> None:
    """Resolve ``hostname`` and raise FetchError if ANY resolved address is not
    a normal public IP. Conservative by design: if resolution fails or any
    address looks internal, we refuse."""
    if not hostname:
        raise FetchError("That URL has no host.", 400)
    try:
        infos = socket.getaddrinfo(hostname, None)
    except socket.gaierror:
        raise FetchError("Couldn't resolve that website's address.", 502)
    for info in infos:
        ip_str = info[4][0]
        try:
            ip = ipaddress.ip_address(ip_str)
        except ValueError:
            raise FetchError("That URL resolved to an invalid address.", 400)
        if (ip.is_private or ip.is_loopback or ip.is_link_local
                or ip.is_reserved or ip.is_multicast or ip.is_unspecified):
            raise FetchError(
                "That URL points to a non-public address and can't be fetched.",
                400,
            )


def _validate_url(url: str) -> str:
    """Scheme/host sanity check + SSRF guard. Returns the normalized URL."""
    url = (url or "").strip()
    if not url:
        raise FetchError("Please enter a URL.", 400)
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise FetchError("Only http and https URLs are supported.", 400)
    if not parsed.hostname:
        raise FetchError("That doesn't look like a valid URL.", 400)
    _assert_public_host(parsed.hostname)
    return url


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
        import lxml.html  # transitive dep via python-docx; pinned in requirements
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


def fetch_solicitation_text(url: str, max_bytes: int = _DEFAULT_MAX_BYTES) -> str:
    """Fetch ``url`` (following redirects, with the SSRF guard re-applied on each
    hop) and return plain text. Raises FetchError on any failure."""
    current = _validate_url(url)

    session = requests.Session()
    headers = {"User-Agent": _USER_AGENT, "Accept": "*/*"}
    resp = None
    try:
        for _ in range(_MAX_REDIRECTS + 1):
            resp = session.get(
                current,
                headers=headers,
                timeout=_TIMEOUT,
                allow_redirects=False,
                stream=True,
            )
            if resp.is_redirect or resp.status_code in (301, 302, 303, 307, 308):
                location = resp.headers.get("Location")
                resp.close()
                if not location:
                    raise FetchError("That page redirected without a target.", 502)
                # Resolve relative redirects, then re-validate the new host.
                current = urljoin(current, location)
                _validate_url(current)
                continue
            break
        else:
            raise FetchError("That URL redirected too many times.", 502)

        if resp.status_code >= 400:
            raise FetchError(
                f"The site returned an error ({resp.status_code}) for that URL.",
                502,
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
