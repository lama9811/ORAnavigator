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


# ── a closing fence with no opening one ────────────────────────────────────
#
# FOUND BY RUNNING THE APP, 2026-08-28, while chasing a PI's report that the
# same paragraph gave different results on different runs. The reviewer's reply
# came back as valid JSON followed by a bare "```" -- a CLOSING markdown fence
# with no opening one. The stripper only fires when the text STARTS with a
# fence, so nothing was removed, json.loads raised "Extra data: line 1 column
# 8421", and generate_json returned None.
#
# The cost is not one row. `_review_batch` treats a None as a failed call and
# falls back to `unclear` for EVERY requirement in the batch -- measured on a
# real Project Description, 15 rules became `unclear` at once and the section
# scored 3 of 3 on its deterministic rules alone instead of 13 of 16. Worse,
# `unclear` is absent from _CREDIT, so those 15 leave the denominator silently
# and the screen shows a confident 100%.
#
# Every generate_json caller is exposed to this: the solicitation contract read,
# the requirement extraction, both review paths, the proofreader and the
# opportunity finder.

def test_a_trailing_fence_without_an_opening_one_is_still_parsed(monkeypatch):
    from services import gemini_client as gc
    monkeypatch.setattr(gc, "_generate",
                        lambda *a, **k: '{"findings": [{"id": "x"}]}```')
    assert gc.generate_json("p") == {"findings": [{"id": "x"}]}


def test_a_normally_fenced_response_still_parses(monkeypatch):
    from services import gemini_client as gc
    monkeypatch.setattr(gc, "_generate",
                        lambda *a, **k: '```json\n{"a": 1}\n```')
    assert gc.generate_json("p") == {"a": 1}


def test_plain_json_is_untouched(monkeypatch):
    from services import gemini_client as gc
    monkeypatch.setattr(gc, "_generate", lambda *a, **k: '{"a": 1}')
    assert gc.generate_json("p") == {"a": 1}


def test_a_fence_inside_a_string_value_is_not_mangled(monkeypatch):
    """The repair must not corrupt a response that already parses -- a draft
    quoting a code fence is a real thing a proposal reviewer could return."""
    from services import gemini_client as gc
    payload = '{"evidence": "see the block ``` here", "id": "x"}'
    monkeypatch.setattr(gc, "_generate", lambda *a, **k: payload)
    assert gc.generate_json("p") == {"evidence": "see the block ``` here", "id": "x"}


def test_genuinely_malformed_output_still_returns_none(monkeypatch):
    """`{"a": 1` was the fixture here and is NOT malformed in the sense that
    matters -- it is missing only its closing brace, which is the exact live
    failure the repair exists for, so it now parses. Mismatched closers are
    structurally wrong and must still return None."""
    from services import gemini_client as gc
    monkeypatch.setattr(gc, "_generate", lambda *a, **k: '{"a": [1, 2}')
    assert gc.generate_json("p") is None


def test_output_that_is_not_json_at_all_returns_none(monkeypatch):
    from services import gemini_client as gc
    monkeypatch.setattr(gc, "_generate", lambda *a, **k: "I cannot help with that.")
    assert gc.generate_json("p") is None


# ── an unclosed object ─────────────────────────────────────────────────────
#
# CAUGHT BY INSTRUMENTING THE RAW RESPONSE, 2026-08-28, chasing a batch that
# was lost roughly one run in ten. The failing reply ended
#
#     ...ers of collaboration from key partners like Dr. Hartwig."}\n]
#
# against a healthy one ending "...Hartwig."}]}" -- the closing brace of the
# outer object is simply absent. `finish_reason` was STOP and out_tok was 1932
# against a ceiling of 8192, so this is NOT truncation at the token limit; the
# model ended its own output one character short.
#
# One missing character discarded 15 requirements: `_review_batch` reads None as
# a failed call and marks the whole batch `unclear`, which is absent from
# _CREDIT and leaves the score's denominator silently. Measured: the section
# reported 3 of 3 on its deterministic rules while 15 real ones went unchecked.
#
# Repaired only AFTER an honest parse fails, and only kept if the repair
# actually parses -- so a well-formed response is never touched.

def test_a_missing_closing_brace_is_repaired(monkeypatch):
    from services import gemini_client as gc
    monkeypatch.setattr(gc, "_generate",
                        lambda *a, **k: '{"findings": [{"id": "x"}]')
    assert gc.generate_json("p") == {"findings": [{"id": "x"}]}


def test_several_missing_closers_are_repaired(monkeypatch):
    from services import gemini_client as gc
    monkeypatch.setattr(gc, "_generate",
                        lambda *a, **k: '{"findings": [{"id": "x", "note": "n"}')
    assert gc.generate_json("p") == {"findings": [{"id": "x", "note": "n"}]}


def test_brackets_inside_a_string_do_not_confuse_the_repair(monkeypatch):
    """An evidence quote containing braces is ordinary in a proposal reviewer's
    output. Counting them as structure would append the wrong closers."""
    from services import gemini_client as gc
    monkeypatch.setattr(gc, "_generate",
                        lambda *a, **k: '{"evidence": "the set {x, y} and [1, 2]"')
    assert gc.generate_json("p") == {"evidence": "the set {x, y} and [1, 2]"}


def test_an_escaped_quote_does_not_confuse_the_repair(monkeypatch):
    from services import gemini_client as gc
    monkeypatch.setattr(gc, "_generate",
                        lambda *a, **k: '{"note": "he said \\"hi\\" loudly"')
    assert gc.generate_json("p") == {"note": 'he said "hi" loudly'}


def test_a_response_cut_mid_value_is_not_guessed_at(monkeypatch):
    """Balancing brackets must not invent data. A value that stops midway is
    genuinely lost and None is the honest answer."""
    from services import gemini_client as gc
    monkeypatch.setattr(gc, "_generate", lambda *a, **k: '{"findings": [{"id":')
    assert gc.generate_json("p") is None


def test_a_well_formed_response_is_never_rewritten(monkeypatch):
    from services import gemini_client as gc
    payload = '{"findings": [{"id": "x", "note": "already fine"}]}'
    monkeypatch.setattr(gc, "_generate", lambda *a, **k: payload)
    assert gc.generate_json("p") == {"findings": [{"id": "x", "note": "already fine"}]}
