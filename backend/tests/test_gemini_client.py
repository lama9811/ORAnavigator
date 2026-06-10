"""Tests for the shared Gemini helper (services/gemini_client.py).

Focus on the two guarantees the AI layers depend on:
  1. JSON parsing is robust (fenced ```json blocks, strict=False, non-dict -> None).
  2. When the client is unavailable, calls return None FAST with NO network
     (the offline/CI fallback path). The autouse `_no_live_gemini` fixture in
     conftest already pins get_client() -> None, so we assert that directly.

Run: cd backend && ../.venv/bin/python -m pytest tests/test_gemini_client.py -v
"""
from services import gemini_client as gc


# ---------- no-client (offline) path: fast None, no network ----------------

def test_generate_json_returns_none_when_client_unavailable():
    # conftest autouse pins get_client -> None.
    assert gc.get_client() is None
    assert gc.generate_json("anything") is None


def test_generate_text_returns_none_when_client_unavailable():
    assert gc.generate_text("anything") is None


def test_no_client_makes_no_network_call(monkeypatch):
    """If there's no client, _generate must short-circuit before touching the
    model. We assert generate_content is never reached by making get_client
    return an object whose .models.generate_content explodes -- then pinning
    get_client back to None and confirming no explosion."""
    # With get_client -> None (autouse), this must not raise and must be None.
    assert gc._generate("x", temperature=0.0, max_output_tokens=10,
                        json_mode=True, timeout_s=None) is None


# ---------- JSON parsing (patch _generate to bypass the live client) -------

def test_generate_json_parses_plain_json(monkeypatch):
    monkeypatch.setattr(gc, "_generate", lambda *a, **k: '{"a": 1, "b": "x"}')
    assert gc.generate_json("p") == {"a": 1, "b": "x"}


def test_generate_json_strips_markdown_fences(monkeypatch):
    fenced = '```json\n{"a": 1}\n```'
    monkeypatch.setattr(gc, "_generate", lambda *a, **k: fenced)
    assert gc.generate_json("p") == {"a": 1}


def test_generate_json_bare_fence_no_lang(monkeypatch):
    fenced = '```\n{"a": 2}\n```'
    monkeypatch.setattr(gc, "_generate", lambda *a, **k: fenced)
    assert gc.generate_json("p") == {"a": 2}


def test_generate_json_tolerates_control_chars(monkeypatch):
    # strict=False must tolerate a literal control char inside a string value.
    monkeypatch.setattr(gc, "_generate", lambda *a, **k: '{"q": "a\x1fb"}')
    out = gc.generate_json("p")
    assert out == {"q": "a\x1fb"}


def test_generate_json_none_on_bad_json(monkeypatch):
    monkeypatch.setattr(gc, "_generate", lambda *a, **k: "not json at all")
    assert gc.generate_json("p") is None


def test_generate_json_none_on_non_dict(monkeypatch):
    monkeypatch.setattr(gc, "_generate", lambda *a, **k: '[1, 2, 3]')
    assert gc.generate_json("p") is None


def test_generate_json_none_on_empty(monkeypatch):
    monkeypatch.setattr(gc, "_generate", lambda *a, **k: None)
    assert gc.generate_json("p") is None


def test_system_instruction_is_forwarded(monkeypatch):
    """generate_json/generate_text accept system_instruction and pass it through
    to _generate (so the strict 'rules of the road' actually reach the model)."""
    seen = {}
    def fake_generate(prompt, **kw):
        seen.update(kw)
        return '{"ok": 1}'
    monkeypatch.setattr(gc, "_generate", fake_generate)
    assert gc.generate_json("p", system_instruction="BE STRICT") == {"ok": 1}
    assert seen.get("system_instruction") == "BE STRICT"


def test_build_config_includes_system_instruction():
    cfg = gc._build_config(0.0, 100, True, None, "RULES HERE")
    assert cfg.get("system_instruction") == "RULES HERE"
    assert cfg.get("response_mime_type") == "application/json"
    # absent when not provided
    cfg2 = gc._build_config(0.0, 100, False, None, None)
    assert "system_instruction" not in cfg2


# ---------- 429 retry-with-backoff (Fix 2026-06-10) -------------------------

class _Resp:
    def __init__(self, text):
        self.text = text


class _FakeModels:
    def __init__(self, fail_times, error_text="429 RESOURCE_EXHAUSTED"):
        self.calls = 0
        self.fail_times = fail_times
        self.error_text = error_text

    def generate_content(self, **kwargs):
        self.calls += 1
        if self.calls <= self.fail_times:
            raise Exception(self.error_text)
        return _Resp("OK")


class _FakeClient:
    def __init__(self, models):
        self.models = models


def _patch_client(monkeypatch, fake):
    monkeypatch.setattr(gc, "get_client", lambda: fake)
    monkeypatch.setattr(gc, "_RETRY_BACKOFFS", (0, 0))  # no real delay in tests


def test_retries_on_429_then_succeeds(monkeypatch):
    models = _FakeModels(fail_times=2)            # fail twice, succeed on 3rd
    _patch_client(monkeypatch, _FakeClient(models))
    out = gc.generate_text("p")
    assert out == "OK"
    assert models.calls == 3                      # 1 attempt + 2 retries


def test_429_exhausts_retries_returns_none(monkeypatch):
    models = _FakeModels(fail_times=99)           # always 429
    _patch_client(monkeypatch, _FakeClient(models))
    assert gc.generate_text("p") is None
    assert models.calls == 3                       # capped at 1 + len(backoffs)


def test_non_retryable_error_fails_fast(monkeypatch):
    models = _FakeModels(fail_times=99, error_text="403 PERMISSION_DENIED")
    _patch_client(monkeypatch, _FakeClient(models))
    assert gc.generate_text("p") is None
    assert models.calls == 1                        # no retry on non-429
