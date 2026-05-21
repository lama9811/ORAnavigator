"""Unit tests for the promptfoo provider scripts."""
import sys
from pathlib import Path

import pytest

EVAL_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(EVAL_DIR))

import adk_provider          # noqa: E402
import backend_provider      # noqa: E402


# --- adk_provider: pure SSE-parsing function -------------------------------

def test_extract_text_picks_longest_text_part():
    lines = [
        'data: {"content": {"parts": [{"text": "short"}]}}',
        'data: {"content": {"parts": [{"text": "this is the longest answer"}]}}',
        'data: [DONE]',
    ]
    assert adk_provider.extract_text(lines) == "this is the longest answer"


def test_extract_text_ignores_non_data_lines_and_bad_json():
    lines = ["", "event: ping", "data: not-json", 'data: {"content": {"parts": []}}']
    assert adk_provider.extract_text(lines) == ""


# --- backend_provider: response parsing + retry ----------------------------

class _FakeResp:
    def __init__(self, status_code, payload=None):
        self.status_code = status_code
        self._payload = payload or {}

    def json(self):
        return self._payload


def test_backend_provider_returns_response_field(monkeypatch):
    monkeypatch.setattr(
        backend_provider.requests, "post",
        lambda *a, **k: _FakeResp(200, {"response": "hello from ORA"}),
    )
    out = backend_provider.call_api("hi", {"config": {}}, {})
    assert out["output"] == "hello from ORA"


def test_backend_provider_retries_on_429_then_succeeds(monkeypatch):
    calls = {"n": 0}

    def fake_post(*a, **k):
        calls["n"] += 1
        if calls["n"] == 1:
            return _FakeResp(429)
        return _FakeResp(200, {"response": "recovered"})

    monkeypatch.setattr(backend_provider.requests, "post", fake_post)
    monkeypatch.setattr(backend_provider.time, "sleep", lambda *_: None)
    out = backend_provider.call_api("hi", {"config": {}}, {})
    assert out["output"] == "recovered"
    assert calls["n"] == 2


def test_backend_provider_uses_base_url_from_config(monkeypatch):
    seen = {}

    def fake_post(url, *a, **k):
        seen["url"] = url
        return _FakeResp(200, {"response": "ok"})

    monkeypatch.setattr(backend_provider.requests, "post", fake_post)
    backend_provider.call_api("hi", {"config": {"base_url": "http://host:9999"}}, {})
    assert seen["url"] == "http://host:9999/chat/guest"


# --- backend rate-limit env override ---------------------------------------

def test_guest_rate_limit_reads_env(monkeypatch):
    """GUEST_RATE_LIMIT must be overridable via env var for eval runs."""
    monkeypatch.setenv("GUEST_RATE_LIMIT", "100000")
    backend_dir = EVAL_DIR.parent.parent.parent / "backend"
    src = (backend_dir / "main.py").read_text()
    # The constant must be derived from os.getenv, not a bare literal.
    assert 'os.getenv("GUEST_RATE_LIMIT"' in src or \
           "os.getenv('GUEST_RATE_LIMIT'" in src
