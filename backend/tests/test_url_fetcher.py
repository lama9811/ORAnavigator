"""Unit tests for services/url_fetcher.py -- the SSRF guard, HTML->text, and the
PDF/HTML content branch. All network + DNS is mocked, so these run offline."""
import socket

import pytest

from services import url_fetcher as uf


# ---------------------------------------------------------------------------
# Helpers / fakes
# ---------------------------------------------------------------------------

def _addrinfo(ip):
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 0))]


def _map_dns(monkeypatch, mapping, default="93.184.216.34"):
    """Patch socket.getaddrinfo so each hostname resolves to a chosen IP."""
    def _fake(host, port, *a, **k):
        return _addrinfo(mapping.get(host, default))
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


def _patch_session(monkeypatch, responses):
    """Patch requests.Session so successive .get() calls return `responses`."""
    seq = list(responses)

    class _FakeSession:
        def get(self, url, **kw):
            return seq.pop(0)

    monkeypatch.setattr(uf.requests, "Session", lambda: _FakeSession())


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
    _patch_session(monkeypatch, [
        _FakeResp(302, headers={"Location": "http://internal.example/x"}),
    ])
    with pytest.raises(uf.FetchError) as ei:
        uf.fetch_solicitation_text("http://good.example/solicitation")
    assert ei.value.status == 400


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
    _patch_session(monkeypatch, [
        _FakeResp(200, headers={"Content-Type": "text/html"}, chunks=[body]),
    ])
    text = uf.fetch_solicitation_text("http://funder.example/grant")
    assert "Eligibility: US institutions only." in text


# ---------------------------------------------------------------------------
# PDF branch
# ---------------------------------------------------------------------------

def test_pdf_link_routes_to_pdf_extractor(monkeypatch):
    _map_dns(monkeypatch, {"nsf.example": "93.184.216.34"})
    _patch_session(monkeypatch, [
        _FakeResp(200, headers={"Content-Type": "application/pdf"},
                  chunks=[b"%PDF-1.7 ...binary..."]),
    ])
    from services import solicitation_extractor as sx
    monkeypatch.setattr(sx, "extract_text_from_pdf", lambda b: "EXTRACTED PDF TEXT")
    text = uf.fetch_solicitation_text("http://nsf.example/pubs/nsf24001.pdf")
    assert text == "EXTRACTED PDF TEXT"


def test_pdf_detected_by_magic_bytes_even_without_content_type(monkeypatch):
    _map_dns(monkeypatch, {"nsf.example": "93.184.216.34"})
    _patch_session(monkeypatch, [
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
    _patch_session(monkeypatch, [
        _FakeResp(200, headers={"Content-Type": "text/html"},
                  chunks=[b"x" * 1000, b"x" * 1000]),
    ])
    with pytest.raises(uf.FetchError) as ei:
        uf.fetch_solicitation_text("http://big.example/page", max_bytes=1500)
    assert ei.value.status == 413


def test_http_error_status_is_surfaced(monkeypatch):
    _map_dns(monkeypatch, {"funder.example": "93.184.216.34"})
    _patch_session(monkeypatch, [
        _FakeResp(404, headers={"Content-Type": "text/html"}, chunks=[b"nope"]),
    ])
    with pytest.raises(uf.FetchError) as ei:
        uf.fetch_solicitation_text("http://funder.example/missing")
    assert ei.value.status == 502


def test_empty_text_page_is_422(monkeypatch):
    _map_dns(monkeypatch, {"funder.example": "93.184.216.34"})
    _patch_session(monkeypatch, [
        _FakeResp(200, headers={"Content-Type": "text/html"},
                  chunks=[b"<html><body></body></html>"]),
    ])
    with pytest.raises(uf.FetchError) as ei:
        uf.fetch_solicitation_text("http://funder.example/blank")
    assert ei.value.status == 422
