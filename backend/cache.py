# -*- coding: utf-8 -*-
"""
Multi-Tier Cache Module for CS Navigator Chatbot
=================================================
Provides L1 (in-memory) + L2 (Redis) + Semantic (embedding similarity) caching.

Architecture:
    Request → L1 (In-Memory) → L2 (Redis) → Semantic (Embedding) → AI
               ~0.001ms         ~1-2ms        ~25-50ms              ~2-5s

L1: Fast, local to each server instance (cachetools TTLCache)
L2: Shared across servers, persistent (Redis Cloud)
Semantic: Matches similar queries via Google text-embedding-004 vectors.
          "prerequisites for data structures" matches "what do I need before COSC 220"
"""

import hashlib
import json
import os
import time
import logging
from typing import Optional
from cachetools import TTLCache
from threading import Lock

import numpy as np

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ============================================================================
# CACHE CONFIGURATION
# ============================================================================

# L1 (In-Memory) Settings
L1_CACHE_MAX_SIZE = 500  # Smaller since Redis is L2
L1_CACHE_TTL_SECONDS = 3600  # 1 hour for L1 (hot cache)

# L2 (Redis) Settings
L2_CACHE_TTL_SECONDS = 28800  # 8 hours for Redis

# Semantic Cache Settings
SEMANTIC_SIMILARITY_THRESHOLD = 0.95  # Cosine sim threshold (0.95 = near-exact match only, prevents cross-topic false positives)
SEMANTIC_MAX_ENTRIES = 100  # Max cached embeddings in memory
SEMANTIC_EMBEDDING_MODEL = 'text-embedding-004'
SEMANTIC_EMBEDDING_DIMS = 256  # Matryoshka truncation, 256 is fast + accurate enough

# Minimum query length to cache (avoid caching "hi", "hello", etc.)
MIN_QUERY_LENGTH = 15

# Queries containing these words should NOT be cached (personalized responses)
NO_CACHE_KEYWORDS = [
    "my advisor",
    "my gpa",
    "my credits",
    "my courses",
    "my schedule",
    "my degree",
    "my name",
    "my student",
    "i have",
    "i need",
    "i am",
    "i'm",
    "recommend me",
    "for me",
]

# Redis Configuration (from environment variables)
REDIS_URL = os.getenv("REDIS_URL", "")
REDIS_HOST = os.getenv("REDIS_HOST", "")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))
REDIS_PASSWORD = os.getenv("REDIS_PASSWORD", "")
REDIS_USERNAME = os.getenv("REDIS_USERNAME", "default")


# ============================================================================
# L1 CACHE (IN-MEMORY)
# ============================================================================

class L1Cache:
    """
    Level 1 Cache: In-memory LRU + TTL cache.
    Fastest access, local to each server instance.
    """

    def __init__(self, max_size: int = L1_CACHE_MAX_SIZE, ttl: int = L1_CACHE_TTL_SECONDS):
        self._cache = TTLCache(maxsize=max_size, ttl=ttl)
        self._lock = Lock()
        self._stats = {"hits": 0, "misses": 0}

    def get(self, key: str) -> Optional[str]:
        with self._lock:
            value = self._cache.get(key)
            if value is not None:
                self._stats["hits"] += 1
                return value
            self._stats["misses"] += 1
            return None

    def set(self, key: str, value: str) -> None:
        with self._lock:
            self._cache[key] = value

    def delete(self, key: str) -> bool:
        with self._lock:
            if key in self._cache:
                del self._cache[key]
                return True
            return False

    def clear(self) -> int:
        with self._lock:
            count = len(self._cache)
            self._cache.clear()
            self._stats = {"hits": 0, "misses": 0}
            return count

    def get_stats(self) -> dict:
        with self._lock:
            total = self._stats["hits"] + self._stats["misses"]
            hit_rate = (self._stats["hits"] / total * 100) if total > 0 else 0
            return {
                "hits": self._stats["hits"],
                "misses": self._stats["misses"],
                "hit_rate": f"{hit_rate:.1f}%",
                "size": len(self._cache),
                "max_size": self._cache.maxsize,
            }


# ============================================================================
# L2 CACHE (REDIS)
# ============================================================================

class L2Cache:
    """
    Level 2 Cache: Redis distributed cache.
    Shared across all server instances, persistent.
    """

    def __init__(self, ttl: int = L2_CACHE_TTL_SECONDS):
        self.ttl = ttl
        self._client = None
        self._connected = False
        self._stats = {"hits": 0, "misses": 0, "errors": 0}
        self._connect()

    def _connect(self):
        """Initialize Redis connection."""
        try:
            import redis

            if REDIS_URL:
                self._client = redis.from_url(REDIS_URL, decode_responses=True)
            else:
                self._client = redis.Redis(
                    host=REDIS_HOST,
                    port=REDIS_PORT,
                    password=REDIS_PASSWORD,
                    username=REDIS_USERNAME,
                    decode_responses=True,
                    socket_timeout=5,
                    socket_connect_timeout=5,
                    retry_on_timeout=True,
                )

            # Test connection
            self._client.ping()
            self._connected = True
            logger.info(f"[REDIS] Connected to {REDIS_HOST}:{REDIS_PORT}")

        except Exception as e:
            self._connected = False
            logger.warning(f"[REDIS] Connection failed: {e}. Running without L2 cache.")

    def is_connected(self) -> bool:
        return self._connected

    def get(self, key: str) -> Optional[str]:
        if not self._connected:
            return None

        try:
            value = self._client.get(f"csnavigator:{key}")
            if value is not None:
                self._stats["hits"] += 1
                return value
            self._stats["misses"] += 1
            return None
        except Exception as e:
            self._stats["errors"] += 1
            logger.warning(f"[REDIS] Get error: {e}")
            return None

    def set(self, key: str, value: str) -> bool:
        if not self._connected:
            return False

        try:
            self._client.setex(f"csnavigator:{key}", self.ttl, value)
            return True
        except Exception as e:
            self._stats["errors"] += 1
            logger.warning(f"[REDIS] Set error: {e}")
            return False

    def delete(self, key: str) -> bool:
        if not self._connected:
            return False

        try:
            return self._client.delete(f"csnavigator:{key}") > 0
        except Exception as e:
            self._stats["errors"] += 1
            logger.warning(f"[REDIS] Delete error: {e}")
            return False

    def clear(self) -> int:
        """Clear all csnavigator keys from Redis."""
        if not self._connected:
            return 0

        try:
            keys = self._client.keys("csnavigator:*")
            if keys:
                count = self._client.delete(*keys)
                self._stats = {"hits": 0, "misses": 0, "errors": 0}
                return count
            return 0
        except Exception as e:
            self._stats["errors"] += 1
            logger.warning(f"[REDIS] Clear error: {e}")
            return 0

    def get_stats(self) -> dict:
        total = self._stats["hits"] + self._stats["misses"]
        hit_rate = (self._stats["hits"] / total * 100) if total > 0 else 0

        info = {"connected": self._connected}
        if self._connected:
            try:
                db_size = len(self._client.keys("csnavigator:*"))
                info["size"] = db_size
            except:
                info["size"] = "unknown"

        return {
            **info,
            "hits": self._stats["hits"],
            "misses": self._stats["misses"],
            "errors": self._stats["errors"],
            "hit_rate": f"{hit_rate:.1f}%",
        }


# ============================================================================
# SEMANTIC CACHE (EMBEDDING SIMILARITY)
# ============================================================================

class SemanticCache:
    """
    Semantic similarity cache using Google text-embedding-004.
    Matches queries with similar meaning even when worded differently.

    Example matches (above 0.92 cosine similarity):
      "prerequisites for data structures" ~ "what do I need before taking COSC 220"
      "AI courses at Morgan State" ~ "what classes cover artificial intelligence"

    Entries are stored in-memory for fast search and persisted to Redis
    for durability across server restarts.
    """

    def __init__(self, l2_cache: L2Cache):
        # Each entry: (embedding_ndarray, query_text, response_text)
        self._entries: list[tuple[np.ndarray, str, str]] = []
        self._lock = Lock()
        self._l2 = l2_cache
        self._genai_client = None
        self._available = False
        self._stats = {"hits": 0, "misses": 0, "errors": 0, "embed_time_ms": 0}
        self._init_client()

    def _init_client(self):
        """Initialize Google embedding client."""
        try:
            from google import genai
            self._genai_client = genai.Client(vertexai=True)
            self._available = True
            logger.info(f"[SEMANTIC] Embedding client ready (model={SEMANTIC_EMBEDDING_MODEL}, dims={SEMANTIC_EMBEDDING_DIMS})")
        except Exception as e:
            logger.warning(f"[SEMANTIC] Embedding client unavailable: {e}. Semantic caching disabled.")
            return

        # Load persisted entries from Redis
        self._load_from_redis()

    def _embed(self, text: str) -> Optional[np.ndarray]:
        """Embed text into a 256-dim vector via Google's embedding API."""
        if not self._available:
            return None
        try:
            from google import genai
            start = time.time()
            result = self._genai_client.models.embed_content(
                model=SEMANTIC_EMBEDDING_MODEL,
                contents=text,
                config=genai.types.EmbedContentConfig(
                    output_dimensionality=SEMANTIC_EMBEDDING_DIMS,
                ),
            )
            elapsed = (time.time() - start) * 1000
            self._stats["embed_time_ms"] += elapsed
            return np.array(result.embeddings[0].values, dtype=np.float32)
        except Exception as e:
            self._stats["errors"] += 1
            logger.warning(f"[SEMANTIC] Embedding error: {e}")
            return None

    @staticmethod
    def _cosine_sim(a: np.ndarray, b: np.ndarray) -> float:
        """Fast cosine similarity between two vectors."""
        dot = np.dot(a, b)
        norm = np.linalg.norm(a) * np.linalg.norm(b)
        return float(dot / norm) if norm > 0 else 0.0

    def get(self, query: str) -> Optional[str]:
        """Find a semantically similar cached response."""
        if not self._available:
            self._stats["misses"] += 1
            return None

        with self._lock:
            if not self._entries:
                self._stats["misses"] += 1
                return None

        q_emb = self._embed(query)
        if q_emb is None:
            self._stats["misses"] += 1
            return None

        best_sim, best_idx = 0.0, -1
        with self._lock:
            for i, (emb, _, _) in enumerate(self._entries):
                sim = self._cosine_sim(q_emb, emb)
                if sim > best_sim:
                    best_sim, best_idx = sim, i

            if best_sim >= SEMANTIC_SIMILARITY_THRESHOLD and best_idx >= 0:
                _, matched_q, response = self._entries[best_idx]
                self._stats["hits"] += 1
                logger.info(
                    f"[SEMANTIC] HIT ({best_sim:.3f}): "
                    f"'{query[:40]}' ~ '{matched_q[:40]}'"
                )
                return response

        self._stats["misses"] += 1
        return None

    def set(self, query: str, response: str) -> bool:
        """Store a query-response pair with its embedding."""
        if not self._available:
            return False

        q_emb = self._embed(query)
        if q_emb is None:
            return False

        with self._lock:
            # Deduplicate: if a near-identical query exists, update it
            for i, (emb, _, _) in enumerate(self._entries):
                if self._cosine_sim(q_emb, emb) > 0.98:
                    self._entries[i] = (q_emb, query, response)
                    self._persist_entry(query, q_emb, response)
                    return True

            # Evict oldest if at capacity
            if len(self._entries) >= SEMANTIC_MAX_ENTRIES:
                self._entries.pop(0)

            self._entries.append((q_emb, query, response))

        self._persist_entry(query, q_emb, response)
        logger.info(f"[SEMANTIC] Stored: '{query[:50]}' ({len(self._entries)} entries)")
        return True

    def _persist_entry(self, query: str, embedding: np.ndarray, response: str):
        """Persist entry to Redis for durability across restarts."""
        if not self._l2 or not self._l2.is_connected():
            return
        key = hashlib.md5(query.lower().strip().encode()).hexdigest()
        try:
            data = json.dumps({
                "q": query,
                "e": embedding.tolist(),
                "r": response,
            })
            self._l2._client.setex(
                f"csnavigator:sem:{key}",
                L2_CACHE_TTL_SECONDS,
                data,
            )
        except Exception as e:
            logger.warning(f"[SEMANTIC] Redis persist error: {e}")

    def _load_from_redis(self):
        """Load persisted semantic entries from Redis on startup."""
        if not self._l2 or not self._l2.is_connected():
            return
        try:
            keys = self._l2._client.keys("csnavigator:sem:*")
            loaded = 0
            for key in keys[:SEMANTIC_MAX_ENTRIES]:
                raw = self._l2._client.get(key)
                if raw:
                    data = json.loads(raw)
                    emb = np.array(data["e"], dtype=np.float32)
                    self._entries.append((emb, data["q"], data["r"]))
                    loaded += 1
            if loaded:
                logger.info(f"[SEMANTIC] Loaded {loaded} entries from Redis")
        except Exception as e:
            logger.warning(f"[SEMANTIC] Failed to load from Redis: {e}")

    def clear(self) -> int:
        """Clear all semantic cache entries."""
        with self._lock:
            count = len(self._entries)
            self._entries.clear()
        if self._l2 and self._l2.is_connected():
            try:
                keys = self._l2._client.keys("csnavigator:sem:*")
                if keys:
                    self._l2._client.delete(*keys)
            except:
                pass
        self._stats = {"hits": 0, "misses": 0, "errors": 0, "embed_time_ms": 0}
        return count

    def get_stats(self) -> dict:
        total = self._stats["hits"] + self._stats["misses"]
        hit_rate = (self._stats["hits"] / total * 100) if total > 0 else 0
        return {
            "available": self._available,
            "hits": self._stats["hits"],
            "misses": self._stats["misses"],
            "errors": self._stats["errors"],
            "hit_rate": f"{hit_rate:.1f}%",
            "index_size": len(self._entries),
            "max_entries": SEMANTIC_MAX_ENTRIES,
            "threshold": SEMANTIC_SIMILARITY_THRESHOLD,
            "total_embed_time_ms": round(self._stats["embed_time_ms"], 1),
        }


# ============================================================================
# MULTI-TIER CACHE (L1 + L2 + SEMANTIC)
# ============================================================================

class MultiTierCache:
    """
    Multi-tier cache combining L1 (in-memory), L2 (Redis), and Semantic (embedding).

    Flow:
    GET: L1 (exact) → L2 (exact) → Semantic (similar) → Miss
    SET: L1 + L2 (exact) + Semantic (embedding)

    Benefits:
    - L1 provides ultra-fast access for hot data
    - L2 provides persistence and cross-server sharing
    - Semantic catches differently-worded versions of the same question
    - Graceful degradation if Redis or embedding API is down
    """

    def __init__(self):
        self.l1 = L1Cache()
        self.l2 = L2Cache()
        self.semantic = SemanticCache(self.l2)
        self._skipped = 0

    def _normalize_query(self, query: str) -> str:
        """Normalize query for consistent cache keys."""
        return " ".join(query.lower().strip().split())

    def _generate_key(self, query: str, context_hash: str = "") -> str:
        """Generate cache key from query."""
        normalized = self._normalize_query(query)
        key_source = f"{normalized}:{context_hash}"
        return hashlib.md5(key_source.encode()).hexdigest()

    def _should_cache(self, query: str) -> bool:
        """Determine if query should be cached."""
        if len(query) < MIN_QUERY_LENGTH:
            return False

        query_lower = query.lower()
        for keyword in NO_CACHE_KEYWORDS:
            if keyword in query_lower:
                return False

        return True

    def get(self, query: str, context_hash: str = "") -> Optional[str]:
        """
        Get cached response using multi-tier lookup.
        L1 (exact) → L2 (exact) → Semantic (similar) → None
        """
        if not self._should_cache(query):
            self._skipped += 1
            return None

        key = self._generate_key(query, context_hash)

        # Try L1 first (fastest)
        response = self.l1.get(key)
        if response is not None:
            logger.info(f"[CACHE] L1 HIT for: {query[:50]}...")
            return response

        # Try L2 (Redis)
        response = self.l2.get(key)
        if response is not None:
            logger.info(f"[CACHE] L2 HIT for: {query[:50]}...")
            # Promote to L1 for faster future access
            self.l1.set(key, response)
            return response

        # L3: Semantic similarity (catches rephrased versions of the same question)
        # 0.95 threshold = safe, only near-identical matches. Saves a full Gemini call (~4s)
        if not context_hash:
            response = self.semantic.get(query)
            if response is not None:
                self.l1.set(key, response)
                return response

        logger.info(f"[CACHE] MISS for: {query[:50]}...")
        return None

    def set(self, query: str, response: str, context_hash: str = "") -> bool:
        """Store response in all cache tiers."""
        if not self._should_cache(query):
            return False

        # Don't cache error responses or outage messages
        if "error" in response.lower()[:50] or "unavailable" in response.lower()[:50]:
            return False
        if "trouble connecting" in response.lower() or "system issue" in response.lower():
            return False

        # Don't cache responses with grounding disclaimers (they indicate low confidence)
        if "I may not have complete information" in response or "Please verify with the CS department" in response:
            return False

        key = self._generate_key(query, context_hash)

        # Write to exact-match tiers
        self.l1.set(key, response)
        self.l2.set(key, response)

        # L3: Store embedding for semantic similarity matching
        if not context_hash:
            self.semantic.set(query, response)

        logger.info(f"[CACHE] Stored in L1+L2+SEM: {query[:50]}...")
        return True

    def invalidate(self, query: str, context_hash: str = "") -> bool:
        """Remove query from all cache tiers."""
        key = self._generate_key(query, context_hash)
        l1_deleted = self.l1.delete(key)
        l2_deleted = self.l2.delete(key)
        return l1_deleted or l2_deleted

    def clear(self) -> dict:
        """Clear all caches."""
        l1_count = self.l1.clear()
        l2_count = self.l2.clear()
        sem_count = self.semantic.clear()
        return {"l1_cleared": l1_count, "l2_cleared": l2_count, "semantic_cleared": sem_count}

    def get_stats(self) -> dict:
        """Get combined cache statistics."""
        l1_stats = self.l1.get_stats()
        l2_stats = self.l2.get_stats()
        sem_stats = self.semantic.get_stats()

        total_hits = l1_stats["hits"] + l2_stats["hits"] + sem_stats["hits"]
        total_misses = l1_stats["misses"]  # L1 misses = total queries that missed all tiers
        total = total_hits + total_misses
        overall_hit_rate = (total_hits / total * 100) if total > 0 else 0

        return {
            "overall": {
                "total_hits": total_hits,
                "total_misses": total_misses,
                "hit_rate": f"{overall_hit_rate:.1f}%",
                "skipped": self._skipped,
            },
            "l1_inmemory": l1_stats,
            "l2_redis": l2_stats,
            "semantic": sem_stats,
        }


# ============================================================================
# GLOBAL CACHE INSTANCE
# ============================================================================

# Single global multi-tier cache instance
query_cache = MultiTierCache()


# ============================================================================
# HELPER FUNCTIONS (Backwards Compatible)
# ============================================================================

def get_context_hash(user_id: int = None, has_degreeworks: bool = False, model: str = "", has_canvas: bool = False, dw_hash: str = "") -> str:
    """
    Generate a context hash for cache key differentiation.
    Includes model and data sources so different contexts get separate cache entries.
    """
    parts = []
    if (has_degreeworks or has_canvas) and user_id:
        parts.append(f"user:{user_id}")
    if has_degreeworks:
        parts.append("dw")
    if dw_hash:
        parts.append(f"dwh:{dw_hash}")
    if has_canvas:
        parts.append("canvas")
    if model:
        parts.append(f"m:{model}")
    if parts:
        return hashlib.md5(":".join(parts).encode()).hexdigest()[:8]
    return ""


def log_cache_stats():
    """Print cache statistics to console."""
    stats = query_cache.get_stats()
    print(f"\n{'='*60}")
    print("CACHE STATISTICS")
    print(f"{'='*60}")
    print(f"Overall Hit Rate: {stats['overall']['hit_rate']}")
    print(f"Total Hits: {stats['overall']['total_hits']}")
    print(f"Total Misses: {stats['overall']['total_misses']}")
    print(f"Skipped (personalized): {stats['overall']['skipped']}")
    print(f"\nL1 (In-Memory): {stats['l1_inmemory']['size']}/{stats['l1_inmemory']['max_size']} items")
    print(f"L2 (Redis): Connected={stats['l2_redis']['connected']}, Items={stats['l2_redis'].get('size', 'N/A')}")
    sem = stats['semantic']
    print(f"Semantic: Available={sem['available']}, Index={sem['index_size']}/{sem['max_entries']}, Hits={sem['hits']}")
    print(f"{'='*60}\n")
