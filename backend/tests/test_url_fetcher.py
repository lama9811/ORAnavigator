"""Unit tests for services/url_fetcher.py -- the SSRF guard (incl. DNS-rebinding
defense via IP pinning), HTML->text, and the PDF/HTML content branch. All network
+ DNS is mocked, so these run offline."""
import socket

import pytest
import requests

from services import url_fetcher as uf


# ---------------------------------------------------------------------------
# Helpers / fakes
# ---------------------------------------------------------------------------

def _addrinfo(*ips):
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 0)) for ip in ips]


def _map_dns(monkeypatch, mapping, default="93.184.216.34"):
    """Patch socket.getaddrinfo so each hostname resolves to chosen IP(s).
    A mapping value may be a single IP string or a list of IP strings."""
    def _fake(host, port, *a, **k):
        val = mapping.get(host, default)
        ips = val if isinstance(val, (list, tuple)) else [val]
        return _addrinfo(*ips)
    monkeypatch.setattr(uf.socket, "getaddrinfo", _fake)


class _FakeResp:
    def __init__(self, status_code=200, headers=None, chunks=None):
        self.status_code = status_code
        self.headers = headers or {}
        self._chunks = chunks if chunks is not None else [b""]

    @property
    def is_redirect(self):
        return self.status_code in (301, 302, 303, 307, 308)

    def iter_content(self, chunk_size=65536):
        for c in self._chunks:
            yield c

    def close(self):
        pass


def _patch_open(monkeypatch, responses):
    """Patch uf._open so successive calls return `responses` in order. Records
    the (url, dest_ip) each call was made with for pinning assertions."""
    seq = list(responses)
    calls = []

    def _fake_open(url, dest_ip, headers):
        calls.append((url, dest_ip))
        return seq.pop(0)

    monkeypatch.setattr(uf, "_open", _fake_open)
    return calls


# ---------------------------------------------------------------------------
# SSRF guard
# ---------------------------------------------------------------------------

def test_rejects_non_http_scheme():
    with pytest.raises(uf.FetchError):
        uf.fetch_solicitation_text("file:///etc/passwd")


def test_rejects_empty_url():
    with pytest.raises(uf.FetchError):
        uf.fetch_solicitation_text("   ")


@pytest.mark.parametrize("ip", ["127.0.0.1", "10.0.0.5", "192.168.1.1",
                                "169.254.169.254", "::1"])
def test_rejects_internal_addresses(monkeypatch, ip):
    _map_dns(monkeypatch, {"evil.example": ip})
    with pytest.raises(uf.FetchError) as ei:
        uf.fetch_solicitation_text("http://evil.example/solicitation")
    assert ei.value.status == 400


def test_rejects_mixed_public_and_private_dns(monkeypatch):
    # An attacker returning [public, internal] must be rejected outright.
    _map_dns(monkeypatch, {"sneaky.example": ["93.184.216.34", "169.254.169.254"]})
    with pytest.raises(uf.FetchError) as ei:
        uf.fetch_solicitation_text("http://sneaky.example/x")
    assert ei.value.status == 400


def test_dns_failure_is_clean_error(monkeypatch):
    def _boom(host, port, *a, **k):
        raise socket.gaierror("nope")
    monkeypatch.setattr(uf.socket, "getaddrinfo", _boom)
    with pytest.raises(uf.FetchError) as ei:
        uf.fetch_solicitation_text("http://does-not-resolve.example")
    assert ei.value.status == 502


def test_redirect_into_private_host_is_blocked(monkeypatch):
    _map_dns(monkeypatch, {"good.example": "93.184.216.34",
                           "internal.example": "10.0.0.9"})
    _patch_open(monkeypatch, [
        _FakeResp(302, headers={"Location": "http://internal.example/x"}),
    ])
    with pytest.raises(uf.FetchError) as ei:
        uf.fetch_solicitation_text("http://good.example/solicitation")
    assert ei.value.status == 400


def test_fetch_connects_to_validated_ip(monkeypatch):
    # The pinned IP handed to _open must be the one we resolved + validated.
    _map_dns(monkeypatch, {"funder.example": "93.184.216.34"})
    calls = _patch_open(monkeypatch, [
        _FakeResp(200, headers={"Content-Type": "text/html"},
                  chunks=[b"<p>Eligibility: all.</p>"]),
    ])
    uf.fetch_solicitation_text("http://funder.example/grant")
    assert calls[0][1] == "93.184.216.34"


# ---------------------------------------------------------------------------
# IP-pinning adapter (DNS-rebinding / TOCTOU defense)
# ---------------------------------------------------------------------------

def test_pinned_adapter_rewrites_url_to_ip_and_keeps_host(monkeypatch):
    captured = {}

    def _fake_super_send(self, request, **kw):
        captured["url"] = request.url
        captured["host"] = request.headers.get("Host")
        return "SENT"

    monkeypatch.setattr(uf.HTTPAdapter, "send", _fake_super_send)
    adapter = uf._PinnedIPAdapter("93.184.216.34")
    req = requests.models.PreparedRequest()
    req.prepare(method="GET", url="https://funder.example/grant", headers={})

    result = adapter.send(req)
    assert result == "SENT"
    # Connects to the IP, not the hostname...
    assert "93.184.216.34" in captured["url"]
    assert "funder.example" not in captured["url"]
    # ...but the Host header (and thus SNI/cert target) stays the real hostname.
    assert captured["host"] == "funder.example"


# ---------------------------------------------------------------------------
# HTML -> text
# ---------------------------------------------------------------------------

def test_html_to_text_strips_scripts_and_styles():
    html = (b"<html><head><style>.x{color:red}</style>"
            b"<script>var bad='SECRET'</script></head>"
            b"<body><h1>NSF CAREER</h1><p>Deadline July 1.</p></body></html>")
    text = uf._html_to_text(html)
    assert "NSF CAREER" in text
    assert "Deadline July 1." in text
    assert "SECRET" not in text
    assert "color:red" not in text


def test_fetch_html_page_returns_visible_text(monkeypatch):
    _map_dns(monkeypatch, {"funder.example": "93.184.216.34"})
    body = b"<html><body><p>Eligibility: US institutions only.</p></body></html>"
    _patch_open(monkeypatch, [
        _FakeResp(200, headers={"Content-Type": "text/html"}, chunks=[body]),
    ])
    text = uf.fetch_solicitation_text("http://funder.example/grant")
    assert "Eligibility: US institutions only." in text


# ---------------------------------------------------------------------------
# PDF branch
# ---------------------------------------------------------------------------

def test_pdf_link_routes_to_pdf_extractor(monkeypatch):
    _map_dns(monkeypatch, {"nsf.example": "93.184.216.34"})
    _patch_open(monkeypatch, [
        _FakeResp(200, headers={"Content-Type": "application/pdf"},
                  chunks=[b"%PDF-1.7 ...binary..."]),
    ])
    from services import solicitation_extractor as sx
    monkeypatch.setattr(sx, "extract_text_from_pdf", lambda b: "EXTRACTED PDF TEXT")
    text = uf.fetch_solicitation_text("http://nsf.example/pubs/nsf24001.pdf")
    assert text == "EXTRACTED PDF TEXT"


def test_pdf_detected_by_magic_bytes_even_without_content_type(monkeypatch):
    _map_dns(monkeypatch, {"nsf.example": "93.184.216.34"})
    _patch_open(monkeypatch, [
        _FakeResp(200, headers={"Content-Type": "application/octet-stream"},
                  chunks=[b"%PDF-1.5 stuff"]),
    ])
    from services import solicitation_extractor as sx
    monkeypatch.setattr(sx, "extract_text_from_pdf", lambda b: "PDF VIA MAGIC")
    text = uf.fetch_solicitation_text("http://nsf.example/download")
    assert text == "PDF VIA MAGIC"


# ---------------------------------------------------------------------------
# Size cap / empties / errors
# ---------------------------------------------------------------------------

def test_size_cap_aborts(monkeypatch):
    _map_dns(monkeypatch, {"big.example": "93.184.216.34"})
    _patch_open(monkeypatch, [
        _FakeResp(200, headers={"Content-Type": "text/html"},
                  chunks=[b"x" * 1000, b"x" * 1000]),
    ])
    with pytest.raises(uf.FetchError) as ei:
        uf.fetch_solicitation_text("http://big.example/page", max_bytes=1500)
    assert ei.value.status == 413


def test_http_error_status_is_surfaced(monkeypatch):
    _map_dns(monkeypatch, {"funder.example": "93.184.216.34"})
    _patch_open(monkeypatch, [
        _FakeResp(404, headers={"Content-Type": "text/html"}, chunks=[b"nope"]),
    ])
    with pytest.raises(uf.FetchError) as ei:
        uf.fetch_solicitation_text("http://funder.example/missing")
    assert ei.value.status == 502


def test_empty_text_page_is_422(monkeypatch):
    _map_dns(monkeypatch, {"funder.example": "93.184.216.34"})
    _patch_open(monkeypatch, [
        _FakeResp(200, headers={"Content-Type": "text/html"},
                  chunks=[b"<html><body></body></html>"]),
    ])
    with pytest.raises(uf.FetchError) as ei:
        uf.fetch_solicitation_text("http://funder.example/blank")
    assert ei.value.status == 422
