"""Tests for the streaming chat path (Layer-3 fast path).

Two layers:
  * _run_verified_stream — orchestration (fake _do_agent_pass): streams chunks,
    finalizes, cautions on weak grounding, refuses/errors safely.
  * _do_agent_pass(stream=True) — the prefix guard that must stream a real answer
    token-by-token but NEVER stream a leaked 429 / KB error.
"""
import json

import vertex_agent as va


# ── _run_verified_stream (fake the ADK pass) ───────────────────────────────

def _result(text="", chunks=0, coverage=0.0, citations=None, grounded_corpus="",
            kb_fail=False, outage=False, error=None, streamed=False):
    return {"text": text, "chunks": chunks, "coverage": coverage,
            "citations": citations or [], "grounded_corpus": grounded_corpus,
            "kb_fail": kb_fail, "outage": outage, "error": error, "streamed": streamed}


def _fake_pass(chunks, result_data):
    def fake(message, user_id, session_id, context="", model="", memory_context="", stream=False):
        if stream:
            for c in chunks:
                yield {"type": "chunk", "content": c}
        yield {"type": "_result", "data": result_data}
    return fake


def _run(monkeypatch, chunks, result_data, message="What is the F&A rate?"):
    monkeypatch.setattr(va, "_do_agent_pass", _fake_pass(chunks, result_data))
    return list(va._run_verified_stream(message, "u", "s"))


def test_stream_emits_chunks_then_done(monkeypatch):
    evs = _run(monkeypatch, ["Pre-award ", "proposals route ", "through ORA."],
               _result("Pre-award proposals route through ORA.", chunks=2, coverage=0.5))
    types = [e["type"] for e in evs]
    assert types.count("chunk") == 3
    assert "error" not in types
    done = [e for e in evs if e["type"] == "done"]
    assert done and "ORA" in done[0]["content"]
    # grounded (2 chunks) -> no caution appended
    assert va._WEAK_NOTE not in done[0]["content"]


def test_stream_weak_answer_gets_caution_not_regeneration(monkeypatch):
    evs = _run(monkeypatch, ["Some plausible answer about the topic."],
               _result("Some plausible answer about the topic.", chunks=0, coverage=0.0))
    done = [e for e in evs if e["type"] == "done"][0]
    assert va._WEAK_NOTE in done["content"]            # cautioned, not hidden/regenerated
    assert "Some plausible answer" in done["content"]   # the streamed answer is kept


def test_stream_outage_never_streams(monkeypatch):
    evs = _run(monkeypatch, [], _result(outage=True))
    types = [e["type"] for e in evs]
    assert "chunk" not in types
    assert any(e["type"] == "error" for e in evs)


def test_stream_empty_answer_refuses(monkeypatch):
    evs = _run(monkeypatch, [], _result(text="", chunks=2))
    done = [e for e in evs if e["type"] == "done"][0]
    assert "rather not guess" in done["content"] or "reliable information" in done["content"].lower()


# ── _do_agent_pass(stream=True) prefix guard (fake the ADK SSE) ────────────

def _sse(obj):
    return ("data: " + json.dumps(obj)).encode("utf-8")


class _FakeResp:
    def __init__(self, lines):
        self._lines, self.status_code = lines, 200

    def raise_for_status(self):
        pass

    def iter_lines(self):
        for ln in self._lines:
            yield ln

    def close(self):
        pass


def test_do_agent_pass_streams_real_answer(monkeypatch):
    full = ("This is a sufficiently long, real KB answer that clears the 60-char "
            "prefix guard and then keeps going for a while.")
    steps = [full[:30], full[:75], full]   # cumulative, as the ADK sends it
    lines = [_sse({"content": {"role": "model", "parts": [{"text": s}]}}) for s in steps]
    monkeypatch.setattr(va.requests, "post", lambda *a, **k: _FakeResp(lines))
    evs = list(va._do_agent_pass("q", "u", "s", stream=True))
    chunks = [e for e in evs if e.get("type") == "chunk"]
    assert chunks
    assert "".join(c["content"] for c in chunks) == full      # deltas reassemble exactly
    data = [e for e in evs if e.get("type") == "_result"][0]["data"]
    assert data["text"] == full and data["streamed"] is True


def test_do_agent_pass_never_streams_429(monkeypatch):
    err = "Error: 429 RESOURCE_EXHAUSTED. Quota exceeded for this model -- please retry shortly."
    lines = [_sse({"content": {"role": "model", "parts": [{"text": err}]}})]
    monkeypatch.setattr(va.requests, "post", lambda *a, **k: _FakeResp(lines))
    monkeypatch.setattr(va, "_RATE_LIMIT_BACKOFFS", ())   # no retries/sleeps
    evs = list(va._do_agent_pass("q", "u", "s", stream=True))
    assert not any(e.get("type") == "chunk" for e in evs)   # the error was NEVER shown
    data = [e for e in evs if e.get("type") == "_result"][0]["data"]
    assert data["outage"] is True
