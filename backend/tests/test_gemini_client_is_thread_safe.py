"""Two callers racing for the Gemini client must both get one.

MEASURED 2026-08-28. `_voted_batch` asks the model three times CONCURRENTLY,
so a Section Check is itself the first thing to race this lazy init. On a cold
process:

    thread A: _client is None, _init_attempted is False -> sets the flag True,
              then spends ~1s inside genai.Client(...)
    thread B: _client is still None, _init_attempted is already True
              -> returns None IMMEDIATELY

B's None is indistinguishable from "AI unavailable", so its vote falls back to
the deterministic path and is lost. Observed end to end: three uploads of the
same Project Summary returned in 0.3s with every semantic rule `unclear`,
against ~12s and a full assessment once the client was warm.

The flag exists so a GENUINE failure stays fast-None forever ("Fast None when
unavailable" is the module's documented contract, and tests/conftest.py rests
on it). It must not also make an in-progress success look like a failure.
"""
import threading

import pytest

from services import gemini_client

# Captured BEFORE conftest's autouse fixture pins get_client() -> None for the
# whole suite. That stub is what every other test needs; this file is the one
# that must exercise the real lazy init.
_REAL_GET_CLIENT = gemini_client.get_client


def _install_fake_genai(monkeypatch, client_cls):
    """Stand in for `from google import genai` inside get_client().

    The whole `google` package is replaced, because a plain setattr on the real
    one loses to the import machinery for a namespace-package submodule.
    """
    import sys, types
    fake_genai = types.ModuleType("google.genai")
    fake_genai.Client = client_cls
    fake_google = types.ModuleType("google")
    fake_google.__path__ = []          # a package, so `from google import ...` works
    fake_google.genai = fake_genai
    monkeypatch.setitem(sys.modules, "google", fake_google)
    monkeypatch.setitem(sys.modules, "google.genai", fake_genai)


@pytest.fixture
def fresh(monkeypatch):
    """A client module rewound to its cold-start state."""
    monkeypatch.setattr(gemini_client, "_client", None, raising=False)
    monkeypatch.setattr(gemini_client, "_init_attempted", False, raising=False)
    monkeypatch.setattr(gemini_client, "_alt_clients", {}, raising=False)
    monkeypatch.setattr(gemini_client, "get_client", _REAL_GET_CLIENT)
    return gemini_client


def test_concurrent_callers_all_get_the_client(fresh, monkeypatch):
    """A slow constructor must not hand every other thread a None."""
    started = threading.Event()

    class _Slow:
        def __init__(self, *a, **kw):
            started.set()
            # Long enough that every other thread reaches get_client() while
            # this one is still inside the constructor.
            threading.Event().wait(0.30)

    _install_fake_genai(monkeypatch, _Slow)

    results = []
    lock = threading.Lock()

    def call():
        c = fresh.get_client()
        with lock:
            results.append(c)

    threads = [threading.Thread(target=call) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    assert len(results) == 4
    assert all(r is not None for r in results), (
        f"{sum(r is None for r in results)} of 4 concurrent callers were told "
        "the AI layer is unavailable while it was still initialising")
    # And exactly one client, shared -- not one per thread.
    assert len({id(r) for r in results}) == 1


def test_a_real_init_failure_still_fast_nones_forever(fresh, monkeypatch):
    """The contract the flag exists for is unchanged."""
    calls = []

    class _Boom:
        def __init__(self, *a, **kw):
            calls.append(1)
            raise RuntimeError("no ADC")

    _install_fake_genai(monkeypatch, _Boom)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    assert fresh.get_client() is None
    assert fresh.get_client() is None
    assert fresh.get_client() is None
    assert len(calls) == 1, "a failed init must not be retried on every call"
