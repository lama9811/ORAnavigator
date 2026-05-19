"""
Long-term User Memory Service
===============================
Tier 2 memory: consolidates daily conversations into persistent user memories
stored in Cloud SQL. Runs via Cloud Scheduler at 3am daily.

Memories give ORA Navigator long-term context about each user (faculty, PI,
research staff, or department admin):
- Their active grants, proposals, and sponsor relationships
- IRB/IACUC protocols they're a PI or co-PI on
- Their role (PI / co-PI / research staff / admin) and department
- Topics they regularly ask about (compliance, pre-award, post-award)
- Interaction preferences (brief vs. detailed answers, etc.)

Privacy: stored on the project's own Cloud SQL instance, NOT Vertex AI. No
personally-identifying grant amounts, salary data, or PII is stored — only
high-level context the user has already volunteered in conversation.
"""

import os
import json
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from db import SessionLocal


def fetch_user_memories(user_id: int, db: Session, limit: int = 10) -> list[dict]:
    """Fetch a user's long-term memories from RDS.

    Returns list of {memory_type, content, updated_at} dicts.
    """
    from models import UserMemory

    memories = (
        db.query(UserMemory)
        .filter(UserMemory.user_id == user_id)
        .order_by(UserMemory.updated_at.desc())
        .limit(limit)
        .all()
    )
    return [
        {
            "memory_type": m.memory_type,
            "content": m.content,
            "updated_at": m.updated_at.isoformat() if m.updated_at else "",
        }
        for m in memories
    ]


def fetch_user_memories_sync(user_id: int, limit: int = 10) -> list[dict]:
    """Fetch memories in a separate DB session (for parallel async execution)."""
    db = SessionLocal()
    try:
        return fetch_user_memories(user_id, db, limit)
    finally:
        db.close()


def build_memory_context(
    memories: list[dict],
    relevant_memories: Optional[list[dict]] = None,
    relevant_turns: Optional[list[dict]] = None,
) -> str:
    """Build a context string from user memories for agent injection.

    Phase 1+2+4 carrier: returns up to three concatenated sections:
      - USER MEMORY (long-term)              ← all stored facts (existing behavior)
      - RELEVANT FROM PAST MEMORIES          ← Phase 2 semantic recall
      - FROM PAST CONVERSATIONS              ← Phase 4 verbatim turn recall
    """
    parts: list[str] = []

    if memories:
        ctx = "\nUSER MEMORY (long-term context from past sessions):\n"
        for m in memories:
            ctx += f"[{m['memory_type']}] {m['content']}\n"
        ctx += "(Use this context to personalize responses. Do not repeat these facts verbatim.)\n"
        parts.append(ctx)

    if relevant_memories:
        ctx = "\nRELEVANT FROM PAST MEMORIES (semantically matched to current query):\n"
        for m in relevant_memories:
            ctx += f"[{m['memory_type']}] {m['content']}\n"
        parts.append(ctx)

    if relevant_turns:
        ctx = "\nFROM PAST CONVERSATIONS (you may reference these earlier exchanges):\n"
        for t in relevant_turns:
            ts = (t.get("timestamp") or "")[:10]
            uq = (t.get("user_query") or "").strip()[:200]
            br = (t.get("bot_response") or "").strip()[:400]
            ctx += f"  [{ts}] User asked: \"{uq}\"\n"
            ctx += f"     You answered: \"{br}\"\n"
        parts.append(ctx)

    return "".join(parts)


def consolidate_user_memories(hours_back: int = 24) -> dict:
    """Consolidate recent conversations into long-term memories for all active users.

    Called by cron job. For each user with conversations in the time window:
    1. Fetch their recent conversations
    2. Use Gemini to extract key facts (interests, goals, preferences)
    3. Merge with existing memories (update, don't duplicate)

    Returns summary of what was processed.
    """
    from models import UserMemory, ChatHistory

    db = SessionLocal()
    try:
        cutoff = datetime.utcnow() - timedelta(hours=hours_back)

        # Find users with recent conversations
        active_users = (
            db.query(ChatHistory.user_id, func.count(ChatHistory.id).label("msg_count"))
            .filter(ChatHistory.timestamp >= cutoff)
            .group_by(ChatHistory.user_id)
            .all()
        )

        if not active_users:
            return {"status": "no_active_users", "processed": 0}

        processed = 0
        errors = 0

        for user_id, msg_count in active_users:
            try:
                # Fetch recent conversations
                chats = (
                    db.query(ChatHistory)
                    .filter(
                        ChatHistory.user_id == user_id,
                        ChatHistory.timestamp >= cutoff,
                    )
                    .order_by(ChatHistory.timestamp.asc())
                    .limit(50)  # Cap to avoid huge prompts
                    .all()
                )

                if not chats or len(chats) < 3:
                    continue  # Skip users with very few messages

                # Build conversation transcript
                transcript = "\n".join(
                    f"User: {c.user_query}\nORA Navigator: {c.bot_response[:200]}"
                    for c in chats
                )

                # Fetch existing memories for context
                existing = (
                    db.query(UserMemory)
                    .filter(UserMemory.user_id == user_id)
                    .all()
                )
                existing_text = "\n".join(
                    f"[{m.memory_type}] {m.content}" for m in existing
                ) if existing else "None"

                # Use Gemini to extract key facts
                new_memories = _extract_memories(transcript, existing_text)

                if new_memories:
                    _merge_memories(db, user_id, new_memories, existing)
                    processed += 1

            except Exception as e:
                print(f"[MEMORY] Error consolidating user {user_id}: {e}")
                errors += 1

        db.commit()
        return {
            "status": "completed",
            "active_users": len(active_users),
            "processed": processed,
            "errors": errors,
        }

    finally:
        db.close()


def _extract_memories(transcript: str, existing_memories: str) -> list[dict]:
    """Use Gemini to extract key facts from a conversation transcript.

    Returns list of {memory_type, content} dicts.
    """
    try:
        from google import genai

        project = os.getenv("GOOGLE_CLOUD_PROJECT", "oranavigator-vertex-ai")
        try:
            client = genai.Client(vertexai=True, project=project, location="us-central1")
        except Exception:
            api_key = os.getenv("GEMINI_API_KEY", "")
            if not api_key:
                print("   [MEMORY] No Gemini client available")
                return []
            client = genai.Client(api_key=api_key)

        prompt = f"""Analyze this user's conversation with ORA Navigator (Morgan State Office of Research Administration assistant) and extract key facts worth remembering for future sessions.

The user is a faculty member, PI, co-PI, research staff member, or department admin — not a student.

RULES:
- Extract ONLY non-obvious facts about the user's research administration context, role, or preferences
- Do NOT include sensitive financial details (grant award amounts, salary, budgets), SSN, or any PII beyond what the user explicitly volunteered
- Do NOT repeat facts already in existing memories
- Keep each fact to one concise sentence — past tense or factual present
- Return valid JSON array only

CATEGORIES (use the most specific that applies):
- "role": Their position (PI on X, co-PI, department chair, research admin, grant manager)
- "department": Their academic department or research unit
- "active_grant": A grant/award they're working on (sponsor + brief topic, e.g. "PI on NIH R01 in cancer biology")
- "irb_protocol": An IRB protocol they're associated with (topic only, no PHI)
- "iacuc_protocol": An IACUC protocol they're working on (species/topic, no protocol numbers)
- "sponsor": Sponsor agencies they typically work with (NIH, NSF, USDA, foundation X, etc.)
- "interest": Recurring topics they ask about (pre-award budgeting, NCE, compliance, etc.)
- "preference": How they prefer the assistant to respond (concise, detailed, with citations, etc.)
- "goal": A near-term goal they mentioned (submitting proposal by date X, closing out award Y, etc.)
- "context": Other situational context (new faculty, transferring department, etc.)

Existing memories:
{existing_memories}

Today's conversations:
{transcript[:4000]}

Return a JSON array like: [{{"type": "role", "content": "PI on an NSF grant in environmental microbiology"}}, {{"type": "sponsor", "content": "Primarily works with NIH and NSF"}}, ...]
If nothing new worth remembering, return: []"""

        response = client.models.generate_content(
            model="gemini-2.0-flash",
            contents=prompt,
            config={"temperature": 0.1, "max_output_tokens": 1000},
        )

        text = response.text.strip()
        # Strip markdown code fences if present
        if text.startswith("```"):
            text = text.split("\n", 1)[1] if "\n" in text else text[3:]
            if text.endswith("```"):
                text = text[:-3]
            text = text.strip()

        memories = json.loads(text)
        if not isinstance(memories, list):
            return []

        return [
            {"memory_type": m.get("type", "context"), "content": m.get("content", "")}
            for m in memories
            if m.get("content")
        ]

    except Exception as e:
        print(f"   [MEMORY] Extraction failed: {e}")
        return []


def _merge_memories(db: Session, user_id: int, new_memories: list[dict], existing: list):
    """Merge new memories with existing ones. Update if same type exists, else create."""
    from models import UserMemory

    existing_by_type = {}
    for m in existing:
        existing_by_type.setdefault(m.memory_type, []).append(m)

    for mem in new_memories:
        mtype = mem["memory_type"]
        content = mem["content"].strip()
        if not content:
            continue

        type_memories = existing_by_type.get(mtype, [])

        # Dedup: skip if an existing memory already contains this info (or vice versa)
        content_lower = content.lower()
        is_duplicate = any(
            content_lower in m.content.lower() or m.content.lower() in content_lower
            for m in type_memories
        )
        if is_duplicate:
            continue

        # Phase 2: compute embedding now so retrieve_relevant_memories can use it.
        from services.embedding_util import embed_text
        emb_vec = embed_text(content)
        emb_serialized = _serialize_embedding(emb_vec) if emb_vec else None
        emb_model = EMBEDDING_MODEL_VERSION if emb_vec else None

        if len(type_memories) < 5:
            # Room for more memories of this type
            new_mem = UserMemory(
                user_id=user_id,
                memory_type=mtype,
                content=content,
                embedding=emb_serialized,
                embedding_model=emb_model,
            )
            db.add(new_mem)
        else:
            # Update the oldest memory of this type — also refresh its embedding.
            oldest = min(type_memories, key=lambda m: m.updated_at or m.created_at)
            oldest.content = content
            oldest.embedding = emb_serialized
            oldest.embedding_model = emb_model
            oldest.updated_at = datetime.utcnow()


# ============================================================================
# Phase 1 — Within-Session Rolling Summary
# ============================================================================
# Problem: chat context window shows only the last 5 turns. Once a session
# passes ~6 turns the bot forgets the start of the conversation.
#
# Solution: after each turn commit, if there are >5 unsummarized older turns
# AND total turn count >= 8, summarize the older ones in the background and
# store the result on the row we just committed. On read, the latest non-null
# session_summary in this (user_id, session_id) is injected into the prompt
# as "EARLIER IN THIS SESSION: ..." before the last 5 raw turns.
#
# Zero added latency: runs in asyncio.create_task after response returns.


def fetch_latest_session_summary_sync(user_id: int, session_id: str) -> tuple[Optional[str], Optional[int]]:
    """Return (summary_text, summary_through_id) for the most recent summary in this session.

    Returns (None, None) if no summary exists yet. Cheap indexed lookup; safe
    to call on the hot chat path.
    """
    from models import ChatHistory

    db = SessionLocal()
    try:
        row = (
            db.query(ChatHistory.session_summary, ChatHistory.summary_through_id)
            .filter(
                ChatHistory.user_id == user_id,
                ChatHistory.session_id == session_id,
                ChatHistory.session_summary.isnot(None),
            )
            .order_by(ChatHistory.id.desc())
            .first()
        )
        if row is None:
            return None, None
        return row[0], row[1]
    finally:
        db.close()


def summarize_older_turns(transcript: str) -> Optional[str]:
    """Distill an older-half conversation transcript into a short narrative summary.

    Returns the summary (typically 1-2 paragraphs, ≤400 tokens) or None on
    failure. Callers should fall back to no-summary mode rather than retry.
    """
    if not transcript or not transcript.strip():
        return None

    try:
        from google import genai

        project = os.getenv("GOOGLE_CLOUD_PROJECT", "")
        try:
            if project:
                client = genai.Client(vertexai=True, project=project, location="us-central1")
            else:
                client = genai.Client(vertexai=True)
        except Exception:
            api_key = os.getenv("GEMINI_API_KEY", "")
            if not api_key:
                print("   [MEMORY] No Gemini client available for session summary")
                return None
            client = genai.Client(api_key=api_key)

        prompt = (
            "Summarize the earlier part of this conversation between a user and "
            "ORA Navigator (Morgan State Office of Research Administration assistant).\n\n"
            "Goal: a concise 1-2 paragraph summary that captures:\n"
            "- What the user asked about\n"
            "- Any specifics the user mentioned (grant numbers, deadlines, sponsors, "
            "IRB/IACUC topics, role/title, commitments)\n"
            "- What the assistant told them — especially policy guidance, contact info, "
            "specific dates/numbers, or links\n\n"
            "Be specific. Avoid filler. Aim for under 400 tokens.\n\n"
            f"Conversation:\n{transcript[:3000]}\n\nSummary:"
        )

        response = client.models.generate_content(
            model="gemini-2.0-flash",
            contents=prompt,
            config={"temperature": 0.1, "max_output_tokens": 500},
        )
        text = (response.text or "").strip()
        return text or None

    except Exception as e:
        print(f"[MEMORY] Session summary failed: {e}")
        return None


def run_session_summary(user_id: int, session_id: str) -> Optional[str]:
    """Background task: build and persist a rolling session summary.

    Triggered post-commit when total turn count >= 8 and unsummarized tail > 5.
    The task itself short-circuits if the triggers aren't met (safe to call
    on every commit if needed).

    Returns the new summary (for tests/logs) or None if no work was done.
    """
    from models import ChatHistory

    db = SessionLocal()
    try:
        # Fetch all turns for this session, oldest first.
        all_turns = (
            db.query(ChatHistory)
            .filter(
                ChatHistory.user_id == user_id,
                ChatHistory.session_id == session_id,
            )
            .order_by(ChatHistory.id.asc())
            .all()
        )
        if len(all_turns) < 8:
            return None  # not enough history yet

        # Find existing summary state.
        prior_summary: Optional[str] = None
        prior_through_id: int = 0
        for t in reversed(all_turns):
            if t.session_summary:
                prior_summary = t.session_summary
                prior_through_id = t.summary_through_id or 0
                break

        # "Older half" = everything EXCEPT the last 5 turns (which remain raw).
        older_turns = all_turns[:-5]
        new_older_turns = [t for t in older_turns if t.id > prior_through_id]
        # Only resummarize if there are >0 new older turns AND tail >5 already covered.
        if not new_older_turns:
            return None

        # Build transcript: include prior summary as compression anchor + new older turns.
        transcript_parts: list[str] = []
        if prior_summary:
            transcript_parts.append(f"EARLIER SUMMARY: {prior_summary}")
        for t in new_older_turns:
            transcript_parts.append(
                f"User: {t.user_query}\nAssistant: {(t.bot_response or '')[:500]}"
            )
        transcript = "\n\n".join(transcript_parts)

        summary = summarize_older_turns(transcript)
        if not summary:
            return None

        # Persist on the newest row (the one that triggered this run).
        latest_row = all_turns[-1]
        latest_row.session_summary = summary
        latest_row.summary_through_id = older_turns[-1].id
        db.commit()
        print(
            f"[MEMORY] Session summary updated user={user_id} session={session_id} "
            f"through_id={latest_row.summary_through_id} new_older={len(new_older_turns)}"
        )
        return summary

    except Exception as e:
        print(f"[MEMORY] run_session_summary failed: {e}")
        return None
    finally:
        db.close()


# ============================================================================
# Phase 2 — Distilled-fact embeddings + semantic recall
# ============================================================================
# Adds an embedding column to user_memories so retrieval can rank the top-k
# most relevant facts instead of dumping all 50 into every prompt. Uses the
# same text-embedding-004 @ 256 dims as cache.py (single source of truth).

EMBEDDING_MODEL_VERSION = "text-embedding-004@256"


def _serialize_embedding(vec: Optional[list[float]]) -> Optional[str]:
    """Serialize a float vector to JSON for TEXT-column storage."""
    if not vec:
        return None
    return json.dumps(vec)


def _deserialize_embedding(text: Optional[str]) -> Optional[list[float]]:
    """Best-effort decode of a stored JSON embedding. Returns None on bad data."""
    if not text:
        return None
    try:
        vec = json.loads(text)
        if isinstance(vec, list) and vec and isinstance(vec[0], (int, float)):
            return vec
    except (ValueError, TypeError):
        pass
    return None


def _semantic_recall_enabled() -> bool:
    return os.getenv("USE_SEMANTIC_MEMORY_RECALL", "true").lower() in ("1", "true", "yes")


def _verbatim_recall_enabled() -> bool:
    return os.getenv("ENABLE_VERBATIM_RECALL", "true").lower() in ("1", "true", "yes")


def retrieve_relevant_memories(
    user_id: int,
    query: str,
    k: int = 5,
    threshold: float = 0.55,
) -> list[dict]:
    """Rank a user's UserMemory rows by semantic similarity to the query.

    Returns up to k rows above threshold (descending sim). Skips paused rows
    and rows missing an embedding. Always returns a list — failures degrade
    silently to no-recall.
    """
    from models import UserMemory
    from services.embedding_util import embed_text, cosine_sim

    if not _semantic_recall_enabled() or not query or not query.strip():
        return []

    q_vec = embed_text(query)
    if not q_vec:
        return []

    db = SessionLocal()
    try:
        rows = (
            db.query(UserMemory)
            .filter(
                UserMemory.user_id == user_id,
                UserMemory.paused == False,  # noqa: E712 — SQLAlchemy needs ==
                UserMemory.embedding.isnot(None),
            )
            .all()
        )
        scored: list[tuple[float, "UserMemory"]] = []
        for r in rows:
            vec = _deserialize_embedding(r.embedding)
            if not vec:
                continue
            sim = cosine_sim(q_vec, vec)
            if sim >= threshold:
                scored.append((sim, r))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [
            {
                "memory_type": r.memory_type,
                "content": r.content,
                "updated_at": r.updated_at.isoformat() if r.updated_at else "",
                "similarity": round(sim, 3),
            }
            for sim, r in scored[:k]
        ]
    except Exception as e:
        print(f"[MEMORY] retrieve_relevant_memories failed: {e}")
        return []
    finally:
        db.close()


def fetch_user_memories_with_recall(user_id: int, query: str, limit: int = 10, k: int = 5) -> tuple[list[dict], list[dict]]:
    """One DB session: long-term memories list + semantic top-k for `query`.

    Returns (long_term, relevant). Used as the unified parallel-fetch target so
    the chat path doesn't open two DB sessions.
    """
    long_term = fetch_user_memories_sync(user_id, limit)
    relevant = retrieve_relevant_memories(user_id, query, k=k)
    return long_term, relevant


# ============================================================================
# Phase 4 — Verbatim turn-level recall (the "strong memory")
# ============================================================================
# Every chat turn's Q+A gets an embedding. On every new query we surface the
# top 3 most-relevant past turns from PRIOR sessions. This is what lets the
# bot "remember what you said last week" — not just facts about you, but
# the actual exchanges.


def retrieve_relevant_turns(
    user_id: int,
    query: str,
    k: int = 3,
    threshold: float = 0.62,
    exclude_session_id: Optional[str] = None,
    scan_limit: int = 1000,
) -> list[dict]:
    """Return the user's top-k most-similar past turns (excluding current session).

    The current session is excluded because its recent turns are already in
    the prompt's PRIOR CONVERSATION window. Scan is bounded to the most
    recent scan_limit embedded turns to keep cosine ranking sub-10ms.
    """
    from models import ChatHistory
    from services.embedding_util import embed_text, cosine_sim

    if not _verbatim_recall_enabled() or not query or not query.strip():
        return []

    q_vec = embed_text(query)
    if not q_vec:
        return []

    db = SessionLocal()
    try:
        q = db.query(ChatHistory).filter(
            ChatHistory.user_id == user_id,
            ChatHistory.embedding.isnot(None),
        )
        if exclude_session_id:
            q = q.filter(ChatHistory.session_id != exclude_session_id)
        rows = q.order_by(ChatHistory.id.desc()).limit(scan_limit).all()

        scored: list[tuple[float, "ChatHistory"]] = []
        for r in rows:
            vec = _deserialize_embedding(r.embedding)
            if not vec:
                continue
            sim = cosine_sim(q_vec, vec)
            if sim >= threshold:
                scored.append((sim, r))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [
            {
                "id": r.id,
                "session_id": r.session_id,
                "timestamp": r.timestamp.isoformat() if r.timestamp else "",
                "user_query": r.user_query,
                "bot_response": r.bot_response,
                "topic_label": r.topic_label,
                "similarity": round(sim, 3),
            }
            for sim, r in scored[:k]
        ]
    except Exception as e:
        print(f"[MEMORY] retrieve_relevant_turns failed: {e}")
        return []
    finally:
        db.close()


def embed_and_store_turn(chat_history_id: int) -> bool:
    """Background task: embed a freshly-committed chat turn and persist.

    Idempotent — short-circuits if the turn already has an embedding. Quietly
    no-ops if the verbatim-recall flag is off (embedding column stays NULL,
    which is fine; retrieval just won't find it).
    """
    if not _verbatim_recall_enabled():
        return False

    from models import ChatHistory
    from services.embedding_util import embed_text

    db = SessionLocal()
    try:
        row = db.query(ChatHistory).filter(ChatHistory.id == chat_history_id).first()
        if not row:
            return False
        if row.embedding:
            return True  # already embedded

        uq = (row.user_query or "").strip()
        br = (row.bot_response or "").strip()
        if not uq and not br:
            return False
        # Q+A together captures topic context better than Q alone.
        # Cap response at 1500 chars to keep embedding input under model limits.
        combined = f"User: {uq}\nAssistant: {br[:1500]}"
        vec = embed_text(combined)
        if not vec:
            return False

        row.embedding = _serialize_embedding(vec)
        row.embedding_model = EMBEDDING_MODEL_VERSION
        db.commit()
        return True
    except Exception as e:
        print(f"[MEMORY] embed_and_store_turn failed id={chat_history_id}: {e}")
        return False
    finally:
        db.close()


# ============================================================================
# Phase 3 — Real-time extraction (kill the 24h lag)
# ============================================================================
# Today: a 3am cron extracts new facts from the last 24h. So anything you
# said today is invisible to the bot until tomorrow.
#
# After Phase 3: facts are extracted every 6 turns AND on an idle sweep
# (5 min after a user goes idle). The 3am cron stays as a final safety net.


def _realtime_enabled() -> bool:
    return os.getenv("ENABLE_REALTIME_MEMORY", "true").lower() in ("1", "true", "yes")


def consolidate_user_memories_single(user_id: int, hours_back: int = 2) -> dict:
    """Run the consolidation pipeline for ONE user. Same logic as the daily
    cron's per-user loop, factored out so it can be triggered on demand
    (post-commit every 6 turns, idle sweep, or manually).

    Returns a status dict for logging.
    """
    if not _realtime_enabled():
        return {"status": "disabled"}

    from models import UserMemory, ChatHistory

    db = SessionLocal()
    try:
        cutoff = datetime.utcnow() - timedelta(hours=hours_back)

        chats = (
            db.query(ChatHistory)
            .filter(
                ChatHistory.user_id == user_id,
                ChatHistory.timestamp >= cutoff,
            )
            .order_by(ChatHistory.timestamp.asc())
            .limit(50)
            .all()
        )
        if not chats or len(chats) < 3:
            return {"status": "skipped_too_few_messages", "user_id": user_id, "count": len(chats)}

        transcript = "\n".join(
            f"User: {c.user_query}\nORA Navigator: {(c.bot_response or '')[:200]}"
            for c in chats
        )

        existing = db.query(UserMemory).filter(UserMemory.user_id == user_id).all()
        existing_text = "\n".join(
            f"[{m.memory_type}] {m.content}" for m in existing
        ) if existing else "None"

        new_memories = _extract_memories(transcript, existing_text)
        if not new_memories:
            return {"status": "no_new_facts", "user_id": user_id}

        _merge_memories(db, user_id, new_memories, existing)
        db.commit()

        print(
            f"[MEMORY] realtime extract user={user_id} new_or_updated={len(new_memories)} "
            f"hours_back={hours_back}"
        )
        return {"status": "ok", "user_id": user_id, "new_facts": len(new_memories)}

    except Exception as e:
        print(f"[MEMORY] consolidate_user_memories_single failed user={user_id}: {e}")
        return {"status": "error", "user_id": user_id, "error": str(e)}
    finally:
        db.close()


def touch_user_last_chat_at(user_id: int) -> None:
    """Update users.last_chat_at = now(). Cheap single-column UPDATE used by
    the idle-sweep job to find users who've been quiet for 5-10 minutes.
    """
    from models import User

    db = SessionLocal()
    try:
        db.query(User).filter(User.id == user_id).update(
            {User.last_chat_at: datetime.utcnow()}, synchronize_session=False
        )
        db.commit()
    except Exception as e:
        # last_chat_at column may not exist yet if migrate hasn't run.
        print(f"[MEMORY] touch_user_last_chat_at failed user={user_id}: {e}")
    finally:
        db.close()


def consolidate_idle_users(idle_min: int = 5, idle_max: int = 10) -> dict:
    """Find users whose last chat was 5-10 min ago and run extraction on
    each. Triggered by Cloud Scheduler every 5 minutes.

    Window approach (not "last extraction predates last_chat_at"): each user
    gets at most one realtime extraction per idle period, and the 5-min cron
    cadence guarantees they get hit once. Cheap idempotency: re-extraction
    just rewrites the same facts via _merge_memories' dedup.
    """
    if not _realtime_enabled():
        return {"status": "disabled"}

    from models import User

    db = SessionLocal()
    try:
        max_cutoff = datetime.utcnow() - timedelta(minutes=idle_min)
        min_cutoff = datetime.utcnow() - timedelta(minutes=idle_max)
        users = (
            db.query(User.id)
            .filter(User.last_chat_at.isnot(None))
            .filter(User.last_chat_at <= max_cutoff)
            .filter(User.last_chat_at >= min_cutoff)
            .all()
        )
    except Exception as e:
        print(f"[MEMORY] consolidate_idle_users query failed: {e}")
        return {"status": "error", "error": str(e)}
    finally:
        db.close()

    if not users:
        return {"status": "no_idle_users", "processed": 0}

    processed = 0
    errors = 0
    for (uid,) in users:
        try:
            consolidate_user_memories_single(uid, hours_back=2)
            processed += 1
        except Exception as e:
            print(f"[MEMORY] idle-sweep user={uid} failed: {e}")
            errors += 1

    return {"status": "completed", "processed": processed, "errors": errors}
