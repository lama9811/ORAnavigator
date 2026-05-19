"""
Shared embedding utility for ORA Navigator's persistent-memory features.

Single source of truth for:
  - Lazy Vertex AI genai client init
  - text-embedding-004 @ 256-dim Matryoshka calls
  - 3-attempt exponential retry on transient failures
  - Cosine similarity for stored embeddings (list[float] or ndarray)

Used by:
  - cache.py (semantic L3 response cache)
  - services/memory_service.py (Phase 2 — fact retrieval, Phase 4 — turn retrieval)

Design choices:
  - embed_text() returns list[float] so callers can json.dumps() into the DB
    without conversion. cache.py wraps the result in np.array() for its
    in-memory entries; that's cheap.
  - cosine_sim() accepts list or ndarray; converts internally. Memory-recall
    paths load embeddings from JSON columns (list) and compare against a
    just-embedded query (list), so this avoids forcing every caller to
    pre-convert.
  - Failures are logged and return None — never raise. Callers store NULL
    embeddings, and retrieval paths skip NULL rows. No correctness loss,
    just degraded recall until the next successful embed.
"""

from __future__ import annotations

import logging
import os
import time
from threading import Lock
from typing import Optional, Sequence, Union

import numpy as np

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "text-embedding-004"
DEFAULT_DIMS = 256

# Retry policy for transient Vertex failures (429, brief 5xx, network blips).
_RETRY_DELAYS_SEC = (0.2, 1.0, 5.0)

# Module-level lazy singleton — matches cache.py:287-296 pattern.
_genai_client = None
_client_lock = Lock()
_client_unavailable_logged = False


def _get_client():
    """Lazily initialize the Vertex genai client. Returns None if unavailable."""
    global _genai_client, _client_unavailable_logged

    if _genai_client is not None:
        return _genai_client

    with _client_lock:
        if _genai_client is not None:
            return _genai_client
        try:
            from google import genai

            _genai_client = genai.Client(vertexai=True)
            logger.info(
                "[EMBED] Vertex embedding client ready (model=%s, dims=%d)",
                DEFAULT_MODEL,
                DEFAULT_DIMS,
            )
            return _genai_client
        except Exception as exc:
            if not _client_unavailable_logged:
                logger.warning(
                    "[EMBED] Vertex embedding client unavailable: %s. "
                    "Memory/cache embedding will be disabled.",
                    exc,
                )
                _client_unavailable_logged = True
            return None


def embed_text(
    text: str,
    *,
    model: str = DEFAULT_MODEL,
    dims: int = DEFAULT_DIMS,
) -> Optional[list[float]]:
    """Embed a string into a float vector via Vertex AI.

    Returns a Python list[float] (JSON-serializable for direct DB storage).
    Returns None on persistent failure after retries; callers should handle
    by skipping or storing NULL.

    Empty/whitespace-only input short-circuits to None.
    """
    if not text or not text.strip():
        return None

    client = _get_client()
    if client is None:
        return None

    from google import genai  # local import keeps module import cheap when genai missing

    last_err: Optional[Exception] = None
    for attempt, delay in enumerate((0.0, *_RETRY_DELAYS_SEC)):
        if delay:
            time.sleep(delay)
        try:
            result = client.models.embed_content(
                model=model,
                contents=text,
                config=genai.types.EmbedContentConfig(output_dimensionality=dims),
            )
            values = result.embeddings[0].values
            return list(values)
        except Exception as exc:
            last_err = exc
            # Log once at warn (first attempt) and again at error if all retries fail.
            if attempt == 0:
                logger.warning("[EMBED] Embed attempt 1 failed: %s. Retrying.", exc)

    logger.error(
        "[EMBED] All %d attempts exhausted: %s. Returning None.",
        len(_RETRY_DELAYS_SEC) + 1,
        last_err,
    )
    return None


_VectorLike = Union[Sequence[float], np.ndarray]


def cosine_sim(a: _VectorLike, b: _VectorLike) -> float:
    """Fast cosine similarity. Accepts list[float] or np.ndarray for either arg.

    Returns 0.0 if either vector is empty, None, or has zero norm.
    Mirrors cache.py SemanticCache._cosine_sim semantics.
    """
    if a is None or b is None:
        return 0.0

    arr_a = a if isinstance(a, np.ndarray) else np.asarray(a, dtype=np.float32)
    arr_b = b if isinstance(b, np.ndarray) else np.asarray(b, dtype=np.float32)

    if arr_a.size == 0 or arr_b.size == 0:
        return 0.0

    dot = float(np.dot(arr_a, arr_b))
    norm = float(np.linalg.norm(arr_a) * np.linalg.norm(arr_b))
    return dot / norm if norm > 0 else 0.0


# ----------------------------------------------------------------------------
# Optional rate-limit guard (Phase 6 hardening)
# ----------------------------------------------------------------------------
# Vertex AI text-embedding-004 default quota is ~60 RPM per project. We
# throttle to a configurable ceiling so backfill scripts can't starve the
# live chat path. Set EMBEDDING_MAX_RPM=0 to skip embedding entirely
# (kill-switch for cost spikes).

try:
    _EMBED_MAX_RPM = int(os.getenv("EMBEDDING_MAX_RPM", "50"))
except ValueError:
    _EMBED_MAX_RPM = 50

_rate_lock = Lock()
_rate_window_start = 0.0
_rate_count = 0


def embed_text_throttled(
    text: str,
    *,
    model: str = DEFAULT_MODEL,
    dims: int = DEFAULT_DIMS,
) -> Optional[list[float]]:
    """Same as embed_text() but enforces EMBEDDING_MAX_RPM.

    Use this from backfill scripts or batch jobs. The hot chat path can
    call embed_text() directly — at 1 call/turn it never approaches the
    quota.

    If EMBEDDING_MAX_RPM=0, returns None immediately (kill switch).
    """
    if _EMBED_MAX_RPM <= 0:
        return None

    global _rate_window_start, _rate_count

    while True:
        with _rate_lock:
            now = time.time()
            if now - _rate_window_start >= 60.0:
                _rate_window_start = now
                _rate_count = 0

            if _rate_count < _EMBED_MAX_RPM:
                _rate_count += 1
                break

            wait_for = 60.0 - (now - _rate_window_start)
        time.sleep(max(0.05, wait_for))

    return embed_text(text, model=model, dims=dims)
