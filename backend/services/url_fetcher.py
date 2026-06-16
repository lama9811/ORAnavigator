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
import re
import socket
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


def fetch_solicitation_text(url: str, max_bytes: int = _DEFAULT_MAX_BYTES) -> str:
    """Fetch ``url`` (following redirects, re-resolving + re-validating + re-pinning
    on each hop) and return plain text. Raises FetchError on any failure."""
    current = url
    headers = {"User-Agent": _USER_AGENT, "Accept": "*/*"}
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
