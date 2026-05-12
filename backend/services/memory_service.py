"""
Long-term User Memory Service
===============================
Tier 2 memory: consolidates daily conversations into persistent user memories
stored in RDS. Runs via cron job at 3am (after the 2am research job).

Memories give the chatbot long-term context about each student:
- What topics they care about
- Their academic interests and goals
- Interaction patterns and preferences

FERPA-safe: stored on our own RDS, not Vertex AI. No grades or PII in memory
content, only behavioral/interest summaries.
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


def build_memory_context(memories: list[dict]) -> str:
    """Build a context string from user memories for agent injection."""
    if not memories:
        return ""

    ctx = "\nUSER MEMORY (long-term context from past sessions):\n"
    for m in memories:
        ctx += f"[{m['memory_type']}] {m['content']}\n"
    ctx += "(Use this context to personalize responses. Do not repeat these facts verbatim.)\n"
    return ctx


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
                    f"Student: {c.user_query}\nBot: {c.bot_response[:200]}"
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

        project = os.getenv("GOOGLE_CLOUD_PROJECT", "csnavigator-vertex-ai")
        try:
            client = genai.Client(vertexai=True, project=project, location="us-central1")
        except Exception:
            api_key = os.getenv("GEMINI_API_KEY", "")
            if not api_key:
                print("   [MEMORY] No Gemini client available")
                return []
            client = genai.Client(api_key=api_key)

        prompt = f"""Analyze this student's conversation with CS Navigator and extract key facts worth remembering for future sessions.

RULES:
- Extract ONLY non-obvious facts about the student's interests, goals, preferences, or situation
- Do NOT include grades, GPA, student ID, or any personally identifiable academic records
- Do NOT repeat facts already in existing memories
- Keep each fact to one concise sentence
- Return valid JSON array only

CATEGORIES:
- "interest": Topics, courses, or career paths they're interested in
- "preference": How they like to interact (detailed answers, brief answers, etc.)
- "goal": Academic or career goals they mentioned
- "context": Situational context (e.g., "preparing for graduation", "looking for internships")

Existing memories:
{existing_memories}

Today's conversations:
{transcript[:4000]}

Return a JSON array like: [{{"type": "interest", "content": "Interested in machine learning and AI research"}}, ...]
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

        if len(type_memories) < 5:
            # Room for more memories of this type
            new_mem = UserMemory(
                user_id=user_id,
                memory_type=mtype,
                content=content,
            )
            db.add(new_mem)
        else:
            # Update the oldest memory of this type
            oldest = min(type_memories, key=lambda m: m.updated_at or m.created_at)
            oldest.content = content
            oldest.updated_at = datetime.utcnow()
