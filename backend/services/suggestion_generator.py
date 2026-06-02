"""
Personalized home-screen suggestion generator
=============================================
Precomputes the 10 questions shown on the chatbot's empty-state welcome screen
for each authenticated user. Runs in the post-commit memory hook so the GET
endpoint can be a ~5ms read.

Pipeline (inside regenerate_for_user):
  1. Compute signature from MAX(chat_history.id) + MAX(user_memories.updated_at)
     for this user. If row exists and signature matches AND was generated
     <10 min ago, skip (throttle).
  2. Pull last 10 ChatHistory rows (cross-session), dedup near-identical
     user_query strings via cosine on existing embeddings (>0.92).
  3. Pull up to 8 un-paused UserMemory rows of high-signal types.
  4. Cold-start gate: <3 turns AND <2 facts -> write "default" row with a
     shuffled DEFAULT_QUESTION_POOL sample; return.
  5. Hybrid generation:
       - 6 LLM questions via gemini-2.5-flash (temp 0.7), prompt sees only
         user_query text + bulleted facts (NEVER bot_response — keeps the
         signal clean, blocks hallucination leakage).
       - 2-4 template questions filled deterministically from concrete facts
         (active_grant, irb_protocol, iacuc_protocol, sponsor).
  6. Validate LLM list: >=5 well-formed strings, each <=120 chars,
     case-insensitive dedup. Failure -> fall back to template + pool filler.
  7. Shuffle final, cap at 10, upsert into user_suggested_questions.

Reuses:
  - Gemini lazy-singleton pattern from services/query_rewriter.py:48-73
  - JSON-array parsing (fenced-codeblock stripping) from services/memory_service.py:246-254
  - cosine_sim from services/embedding_util.py
"""

import json
import os
import random
import re
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from db import SessionLocal
from models import ChatHistory, UserMemory, UserSuggestedQuestions

# ----------------------------------------------------------------------------
# Tunables
# ----------------------------------------------------------------------------
TARGET_COUNT = 10
LLM_COUNT = 6
HISTORY_LIMIT = 10
FACT_LIMIT = 8
COLD_START_TURN_THRESHOLD = 3
COLD_START_FACT_THRESHOLD = 2
DEDUP_COSINE_THRESHOLD = 0.92
THROTTLE_WINDOW = timedelta(minutes=10)
MAX_QUESTION_CHARS = 120

# Memory types that carry useful personalization signal for suggestions.
HIGH_SIGNAL_FACT_TYPES = {
    "interest", "active_grant", "sponsor", "irb_protocol",
    "iacuc_protocol", "goal", "role", "department",
}

# ----------------------------------------------------------------------------
# Lazy Gemini client (mirrors services/query_rewriter.py:48-73)
# ----------------------------------------------------------------------------
_gemini_client = None
_gemini_init_attempted = False


def _get_client():
    """Get or create the cached Gemini client. Returns None if unavailable."""
    global _gemini_client, _gemini_init_attempted
    if _gemini_client is not None:
        return _gemini_client
    if _gemini_init_attempted:
        return None
    _gemini_init_attempted = True

    try:
        from google import genai
        project = os.getenv("GOOGLE_CLOUD_PROJECT", "oranavigator-vertex-ai")
        try:
            _gemini_client = genai.Client(vertexai=True, project=project, location="us-central1")
            print("   [SUGGEST] Gemini client initialized (Vertex AI)")
        except Exception:
            api_key = os.getenv("GEMINI_API_KEY", "")
            if api_key:
                _gemini_client = genai.Client(api_key=api_key)
                print("   [SUGGEST] Gemini client initialized (API key)")
            else:
                print("   [SUGGEST] No Gemini client available")
    except Exception as e:
        print(f"   [SUGGEST] Client init failed: {e}")

    return _gemini_client


# ----------------------------------------------------------------------------
# Signature: cheap stale-detection key
# ----------------------------------------------------------------------------
def current_signature(user_id: int, db: Session) -> str:
    """Return "{max_chat_id}:{max_memory_updated_epoch}" for this user.
    Both component queries are indexed; the call is sub-millisecond."""
    max_chat_id = db.query(func.max(ChatHistory.id))\
        .filter(ChatHistory.user_id == user_id).scalar()
    max_mem_updated = db.query(func.max(UserMemory.updated_at))\
        .filter(UserMemory.user_id == user_id).scalar()

    chat_part = str(max_chat_id) if max_chat_id is not None else "0"
    mem_part = str(int(max_mem_updated.timestamp())) if max_mem_updated else "0"
    return f"{chat_part}:{mem_part}"


# ----------------------------------------------------------------------------
# History fetch + semantic dedup
# ----------------------------------------------------------------------------
def _fetch_recent_queries(user_id: int, db: Session) -> list[str]:
    """Return up to HISTORY_LIMIT recent user_query strings (cross-session),
    deduped on cosine similarity of stored embeddings where available."""
    rows = db.query(ChatHistory)\
        .filter(ChatHistory.user_id == user_id)\
        .order_by(ChatHistory.timestamp.desc())\
        .limit(HISTORY_LIMIT * 2).all()

    from services.embedding_util import cosine_sim

    kept: list[tuple[str, Optional[list[float]]]] = []
    for row in rows:
        q = (row.user_query or "").strip()
        if not q:
            continue
        emb = None
        if row.embedding:
            try:
                emb = json.loads(row.embedding)
            except Exception:
                emb = None
        # Near-duplicate against anything already kept?
        is_dup = False
        if emb is not None:
            for _, kemb in kept:
                if kemb is not None and cosine_sim(emb, kemb) > DEDUP_COSINE_THRESHOLD:
                    is_dup = True
                    break
        if not is_dup:
            kept.append((q, emb))
        if len(kept) >= HISTORY_LIMIT:
            break

    return [q for q, _ in kept]


# ----------------------------------------------------------------------------
# Memory fetch (un-paused, high-signal types only)
# ----------------------------------------------------------------------------
def _fetch_facts(user_id: int, db: Session) -> list[dict]:
    """Return up to FACT_LIMIT un-paused UserMemory rows of high-signal types,
    most-recently-updated first."""
    rows = db.query(UserMemory)\
        .filter(
            UserMemory.user_id == user_id,
            UserMemory.paused == False,  # noqa: E712 — SQLAlchemy needs ==
            UserMemory.memory_type.in_(list(HIGH_SIGNAL_FACT_TYPES)),
        )\
        .order_by(UserMemory.updated_at.desc())\
        .limit(FACT_LIMIT).all()
    return [{"type": r.memory_type, "content": (r.content or "").strip()} for r in rows if r.content]


# ----------------------------------------------------------------------------
# Template-filled questions (deterministic, zero-hallucination)
# ----------------------------------------------------------------------------
def _template_questions(facts: list[dict]) -> list[str]:
    """Generate 0-4 deterministic questions from concrete facts.
    These are the safety-net 'obviously personalized' suggestions that work
    even when the LLM is down."""
    out: list[str] = []
    seen_types: set[str] = set()

    for f in facts:
        if len(out) >= 4:
            break
        ftype = f["type"]
        if ftype in seen_types:
            continue
        # Trim long content for inline use
        snippet = f["content"][:60].strip()
        if not snippet:
            continue

        if ftype == "active_grant":
            out.append(f"What's the next deadline tied to my {snippet} award?")
        elif ftype == "irb_protocol":
            out.append(f"When does my IRB protocol on {snippet} need renewal?")
        elif ftype == "iacuc_protocol":
            out.append(f"Which IACUC SOPs apply to my {snippet} study?")
        elif ftype == "sponsor":
            out.append(f"What are upcoming {snippet} funding deadlines?")
        elif ftype == "interest":
            out.append(f"What forms or templates support {snippet}?")
        elif ftype == "goal":
            out.append(f"What's the next step toward {snippet}?")
        else:
            continue
        seen_types.add(ftype)

    return out


# ----------------------------------------------------------------------------
# LLM call
# ----------------------------------------------------------------------------
def _llm_questions(recent_queries: list[str], facts: list[dict]) -> list[str]:
    """Ask Gemini for LLM_COUNT ORA-domain follow-up questions personalized to
    this user's recent intents + known facts. Returns [] on any failure."""
    client = _get_client()
    if not client:
        return []

    fact_lines = "\n".join(f"- ({f['type']}) {f['content']}" for f in facts) or "(none yet)"
    query_lines = "\n".join(f"- {q}" for q in recent_queries) or "(none yet)"

    prompt = (
        "You generate suggested next questions for the home screen of an ORA "
        "(Office of Research Administration) chatbot at Morgan State University. "
        "Users are faculty, PIs, research staff, and department admins.\n\n"
        f"Generate exactly {LLM_COUNT} short questions this user would naturally "
        "ask next, given:\n"
        "  (a) what they have recently been asking the bot, and\n"
        "  (b) the persistent facts the bot remembers about them.\n\n"
        "RULES:\n"
        f"  - Each question max {MAX_QUESTION_CHARS} characters.\n"
        "  - Vary the angle — don't just rephrase one topic 6 times.\n"
        "  - Stay strictly inside ORA scope: pre-award, post-award, compliance "
        "(IRB / IACUC / COI), research security, F&A / fringe, NCE, effort, "
        "subawards, forms, policies, trainings, ORA staff.\n"
        "  - NEVER include grant numbers, protocol numbers, PI names, or any "
        "other PII even if the recent queries contain them. Talk about "
        "categories instead.\n"
        "  - Return ONLY a JSON array of strings. No prose, no markdown.\n\n"
        f"Recent user questions:\n{query_lines}\n\n"
        f"Known facts about the user:\n{fact_lines}\n\n"
        f"JSON array of {LLM_COUNT} questions:"
    )

    try:
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt,
            config={"temperature": 0.7, "max_output_tokens": 800},
        )
        text = (response.text or "").strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1] if "\n" in text else text[3:]
            if text.endswith("```"):
                text = text[:-3]
            text = text.strip()

        items = json.loads(text)
        if not isinstance(items, list):
            return []
        return [str(x).strip() for x in items if isinstance(x, (str, int)) and str(x).strip()]
    except Exception as e:
        print(f"   [SUGGEST] LLM call failed: {type(e).__name__}: {e}")
        return []


# ----------------------------------------------------------------------------
# Validation + assembly
# ----------------------------------------------------------------------------
_WORD_RE = re.compile(r"[A-Za-z]")


def _validate_llm_output(items: list[str]) -> list[str]:
    """Keep well-formed entries; case-insensitive dedup; cap length."""
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        q = item.strip().strip("\"'`")
        if not q or len(q) > MAX_QUESTION_CHARS:
            continue
        if not _WORD_RE.search(q):
            continue  # numeric-only or punctuation noise
        key = q.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(q)
    return out


def _default_pool_sample(k: int) -> list[str]:
    # Lazy import so we don't form a circular dep at module load (main imports us back).
    from main import DEFAULT_QUESTION_POOL
    k = min(k, len(DEFAULT_QUESTION_POOL))
    return random.sample(DEFAULT_QUESTION_POOL, k)


def _assemble_final(llm_items: list[str], templates: list[str]) -> list[str]:
    """Combine validated LLM output + template questions + filler from the
    default pool to land at exactly TARGET_COUNT, deduped, shuffled."""
    pool = list(templates)
    seen = {q.lower() for q in pool}
    for q in llm_items:
        if q.lower() not in seen:
            pool.append(q)
            seen.add(q.lower())

    if len(pool) < TARGET_COUNT:
        for q in _default_pool_sample(TARGET_COUNT + 4):
            if q.lower() not in seen:
                pool.append(q)
                seen.add(q.lower())
            if len(pool) >= TARGET_COUNT:
                break

    random.shuffle(pool)
    return pool[:TARGET_COUNT]


# ----------------------------------------------------------------------------
# Upsert
# ----------------------------------------------------------------------------
def _upsert(
    db: Session,
    user_id: int,
    questions: list[str],
    source: str,
    signature: str,
) -> None:
    row = db.query(UserSuggestedQuestions)\
        .filter(UserSuggestedQuestions.user_id == user_id).first()
    payload = json.dumps(questions)
    now = datetime.now(timezone.utc)
    if row is None:
        row = UserSuggestedQuestions(
            user_id=user_id,
            questions=payload,
            generated_at=now,
            source_signature=signature,
            source=source,
        )
        db.add(row)
    else:
        row.questions = payload
        row.generated_at = now
        row.source_signature = signature
        row.source = source
    db.commit()


# ----------------------------------------------------------------------------
# Public entry point
# ----------------------------------------------------------------------------
def regenerate_for_user(user_id: int) -> dict:
    """Background-task entry point. Safe to call repeatedly — internal throttle
    skips redundant work. Returns the row's current state as a dict (mainly
    useful for tests; production callers ignore the return value)."""
    db = SessionLocal()
    try:
        sig = current_signature(user_id, db)
        existing = db.query(UserSuggestedQuestions)\
            .filter(UserSuggestedQuestions.user_id == user_id).first()

        # Throttle: signature unchanged AND <THROTTLE_WINDOW ago -> skip
        if existing is not None and existing.source_signature == sig:
            gen_at = existing.generated_at
            if gen_at is not None:
                if gen_at.tzinfo is None:
                    gen_at = gen_at.replace(tzinfo=timezone.utc)
                if datetime.now(timezone.utc) - gen_at < THROTTLE_WINDOW:
                    return {
                        "source": existing.source,
                        "signature": existing.source_signature,
                        "skipped": True,
                    }

        recent = _fetch_recent_queries(user_id, db)
        facts = _fetch_facts(user_id, db)

        # Cold-start gate
        if len(recent) < COLD_START_TURN_THRESHOLD and len(facts) < COLD_START_FACT_THRESHOLD:
            questions = _default_pool_sample(TARGET_COUNT)
            _upsert(db, user_id, questions, source="default", signature=sig)
            print(f"   [SUGGEST] user={user_id} source=default (cold start) signature={sig}")
            return {"source": "default", "signature": sig, "skipped": False}

        templates = _template_questions(facts)
        llm_raw = _llm_questions(recent, facts)
        llm_valid = _validate_llm_output(llm_raw)

        if len(llm_valid) < 5 and len(templates) == 0:
            # Both paths failed -> fall back to pool entirely.
            questions = _default_pool_sample(TARGET_COUNT)
            _upsert(db, user_id, questions, source="default", signature=sig)
            print(f"   [SUGGEST] user={user_id} source=default (generation failed) signature={sig}")
            return {"source": "default", "signature": sig, "skipped": False}

        questions = _assemble_final(llm_valid, templates)
        _upsert(db, user_id, questions, source="personalized", signature=sig)
        print(
            f"   [SUGGEST] user={user_id} source=personalized "
            f"recent={len(recent)} facts={len(facts)} "
            f"llm={len(llm_valid)} template={len(templates)} signature={sig}"
        )
        return {"source": "personalized", "signature": sig, "skipped": False}
    except Exception as e:
        print(f"   [SUGGEST] regenerate_for_user({user_id}) failed: {type(e).__name__}: {e}")
        return {"source": "error", "skipped": False}
    finally:
        db.close()
