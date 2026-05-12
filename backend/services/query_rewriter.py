"""
Query Rewriter for Follow-up Resolution
=========================================
Two-layer system for resolving pronouns and references in follow-up queries:

Layer 1 (deterministic): Entity focus tracker. Extracts entities from both
the user's query AND the bot's response. When both mention the same entity,
that becomes the confirmed "current focus." Pronouns are replaced using
this focus with zero LLM calls.

Layer 2 (LLM fallback): Gemini rewriter. For complex cases where regex
can't determine the entity, uses a fast Gemini call with explicit
"most recent exchange" priority.
"""

import os
import re

# Follow-up detection patterns
_PRONOUN_RE = re.compile(
    r'\b(he|him|his|she|her|hers|they|them|their|theirs|it|its)\b',
    re.IGNORECASE,
)
_REFERENCE_RE = re.compile(
    r'\b(that|this|those|these|the same|above|previous|last one|same one)\b',
    re.IGNORECASE,
)
_CONTINUATION_RE = re.compile(
    r'^(what about|how about|and |but |so |also|tell me more|more info|more detail|elaborate|explain more|go on|continue)',
    re.IGNORECASE,
)
_SHORT_FOLLOWUP_RE = re.compile(
    r'^(yes|yeah|yep|no|nah|which one|what else|anything else)[!?.\s]*$',
    re.IGNORECASE,
)

# Cached Gemini client (initialized once, reused across requests)
_gemini_client = None
_gemini_init_attempted = False

_rewrite_call_count = 0
_rewrite_window_start = 0
_REWRITE_MAX_PER_MINUTE = 30


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
        project = os.getenv("GOOGLE_CLOUD_PROJECT", "csnavigator-vertex-ai")
        try:
            _gemini_client = genai.Client(vertexai=True, project=project, location="us-central1")
            print("   [REWRITE] Gemini client initialized (Vertex AI)")
        except Exception:
            api_key = os.getenv("GEMINI_API_KEY", "")
            if api_key:
                _gemini_client = genai.Client(api_key=api_key)
                print("   [REWRITE] Gemini client initialized (API key)")
            else:
                print("   [REWRITE] No Gemini client available")
    except Exception as e:
        print(f"   [REWRITE] Client init failed: {e}")

    return _gemini_client


def is_likely_followup(query: str) -> bool:
    """Detect if a query likely needs conversation context to be understood."""
    q = query.strip()
    if not q:
        return False

    # Very short queries (2 words or fewer) with no specific entity are likely follow-ups
    # Skip if query contains a course code (e.g., COSC 350) or looks self-contained
    words = q.split()
    if len(words) <= 2 and not re.search(r'\b[A-Z]{2,4}\s*\d{3}\b', q):
        return True

    if _PRONOUN_RE.search(q):
        return True
    if _REFERENCE_RE.search(q):
        return True
    if _CONTINUATION_RE.match(q):
        return True
    if _SHORT_FOLLOWUP_RE.match(q):
        return True

    return False


# =============================================================================
# LAYER 1: Deterministic Entity Focus Tracker
# =============================================================================

# Patterns to extract entities from text
_PERSON_RE = re.compile(
    r'(?:Dr\.?\s+|Professor\s+)([A-Z][a-z]+(?:\s+(?:"[^"]+"\s+)?[A-Z][a-z]+)?)',
)
_COURSE_RE = re.compile(r'\b([A-Z]{2,4}\s*\d{3})\b')
_PROGRAM_RE = re.compile(r'\b(4\+1|accelerated master|honors program|cloud computing degree|MS in Advanced Computing)\b', re.IGNORECASE)


def _extract_focus(user_query: str, bot_response: str) -> dict:
    """Extract the confirmed focus entity from a Q&A exchange.
    Cross-references what the user asked about with what the bot answered about.
    When both sides mention the same entity, it's the confirmed focus."""

    focus = {"person": None, "course": None, "program": None}

    # Extract from user query
    user_persons = _PERSON_RE.findall(user_query)
    user_courses = _COURSE_RE.findall(user_query)
    user_programs = _PROGRAM_RE.findall(user_query)

    # Also catch "my advisor" pattern
    if re.search(r'\bmy advisor\b|\badvisor\b', user_query, re.IGNORECASE):
        # Extract advisor name from bot response
        advisor_match = _PERSON_RE.findall(bot_response[:300])
        if advisor_match:
            focus["person"] = advisor_match[0]
            return focus

    # Extract from bot response (first 300 chars, where the main answer is)
    bot_persons = _PERSON_RE.findall(bot_response[:300])
    bot_courses = _COURSE_RE.findall(bot_response[:300])

    # Cross-reference: if user asked about a person and bot answered about them
    if user_persons and bot_persons:
        # Check if any user person matches any bot person (by last name)
        for up in user_persons:
            up_last = up.split()[-1].lower()
            for bp in bot_persons:
                bp_last = bp.split()[-1].lower()
                if up_last == bp_last:
                    focus["person"] = bp  # Use bot's version (more complete name)
                    break

    # If user didn't mention a specific person but bot clearly answered about one
    if not focus["person"] and not user_persons and bot_persons:
        # Bot response starts with a person's name = that's the focus
        first_person = bot_persons[0]
        focus["person"] = first_person

    # Course focus
    if user_courses and bot_courses:
        for uc in user_courses:
            uc_norm = uc.replace(" ", "")
            for bc in bot_courses:
                if uc.replace(" ", "") == bc.replace(" ", ""):
                    focus["course"] = bc
                    break

    if not focus["course"] and user_courses:
        focus["course"] = user_courses[0]

    # Program focus
    if user_programs:
        focus["program"] = user_programs[0]

    return focus


def _detect_explicit_override(query: str) -> dict:
    """Detect explicit topic switches like 'go back to Dr. Mack' or 'what about COSC 472'.
    Returns the new focus entity if found, or empty dict."""
    override = {}

    # "go back to X", "back to X", "switch to X", "now about X"
    back_match = re.search(
        r'(?:go back to|back to|switch to|now (?:tell me )?about|let.s talk about)\s+(?:Dr\.?\s+)?(\w+)',
        query, re.IGNORECASE
    )
    if back_match:
        name = back_match.group(1)
        # Don't treat course prefixes (COSC, CLCO, etc.) as person names
        if not re.match(r'^[A-Z]{2,4}$', name) and (name[0].isupper() or re.match(r'(?:dr|professor)', query, re.IGNORECASE)):
            override["person"] = name

    # "what about COSC XXX"
    course_match = re.search(r'(?:what about|how about|switch to)\s+([A-Z]{2,4}\s*\d{3})', query, re.IGNORECASE)
    if course_match:
        override["course"] = course_match.group(1)

    return override


def _apply_focus(query: str, focus: dict) -> str:
    """Replace pronouns in query with the confirmed focus entity.
    Returns the rewritten query, or original if no replacement was made."""

    original = query
    q = query

    if focus.get("person"):
        name = focus["person"]
        # Replace gendered pronouns
        q = re.sub(r'\bhe\b(?!\w)', f'Dr. {name.split()[-1]}', q, flags=re.IGNORECASE)
        q = re.sub(r'\bhim\b(?!\w)', f'Dr. {name.split()[-1]}', q, flags=re.IGNORECASE)
        q = re.sub(r'\bhis\b(?!\w)', f"Dr. {name.split()[-1]}'s", q, flags=re.IGNORECASE)
        q = re.sub(r'\bshe\b(?!\w)', f'Dr. {name.split()[-1]}', q, flags=re.IGNORECASE)
        q = re.sub(r'\bher\b(?!\w)', f"Dr. {name.split()[-1]}'s", q, flags=re.IGNORECASE)
        q = re.sub(r'\bthey\b(?!\w)', f'Dr. {name.split()[-1]}', q, flags=re.IGNORECASE)
        q = re.sub(r'\bthem\b(?!\w)', f'Dr. {name.split()[-1]}', q, flags=re.IGNORECASE)
        q = re.sub(r'\btheir\b(?!\w)', f"Dr. {name.split()[-1]}'s", q, flags=re.IGNORECASE)

    if focus.get("course"):
        code = focus["course"]
        q = re.sub(r'\bit\b(?!\w)', code, q, flags=re.IGNORECASE, count=1)
        q = re.sub(r'\bthat course\b', code, q, flags=re.IGNORECASE)
        q = re.sub(r'\bthe course\b', code, q, flags=re.IGNORECASE)

    if focus.get("program"):
        prog = focus["program"]
        q = re.sub(r'\bthat program\b', prog, q, flags=re.IGNORECASE)
        q = re.sub(r'\bthe program\b', prog, q, flags=re.IGNORECASE)
        q = re.sub(r'\bit\b(?!\w)', prog, q, flags=re.IGNORECASE, count=1)

    if q != original:
        print(f"   [FOCUS] '{original}' -> '{q}'")
        return q

    return None  # No replacement made, fall through to LLM rewriter


# =============================================================================
# LAYER 2: LLM Rewriter (fallback for complex cases)
# =============================================================================

def rewrite_query(query: str, history: list[dict]) -> str:
    """Rewrite a follow-up query to be self-contained.

    Layer 1: Deterministic focus tracker (cross-references user query + bot response)
    Layer 2: Gemini rewriter (fallback for cases regex can't handle)

    Args:
        query: The user's current message
        history: Recent conversation turns [{user_query, bot_response}, ...]

    Returns:
        Rewritten query (or original if not a follow-up / rewrite fails)
    """
    if not history or not is_likely_followup(query):
        return query

    # Layer 0: Check for explicit topic overrides ("go back to Dr. Mack")
    override = _detect_explicit_override(query)
    if override:
        # User explicitly named who/what they want. Use it as focus.
        print(f"   [OVERRIDE] Detected explicit switch: {override}")
        focused = _apply_focus(query, override)
        if focused:
            return focused

    # Layer 1: Try deterministic focus replacement from last turn
    # Only replaces when we have a HIGH-CONFIDENCE match (both user and bot agree on entity)
    # SKIP if the current query already has its own clear entities (prevents context bleed)
    has_own_course = bool(_COURSE_RE.search(query))
    has_own_person = bool(_PERSON_RE.search(query))
    has_own_topic = bool(re.search(r'\b(calc|physics|gpa|grade|class|major|minor|credit)\b', query, re.IGNORECASE))

    if not has_own_course and not has_own_person and not has_own_topic:
        last_turn = history[-1]
        focus = _extract_focus(last_turn["user_query"], last_turn["bot_response"])
        focused = _apply_focus(query, focus)
        if focused:
            return focused

    # Layer 2: LLM rewriter - only for very short ambiguous follow-ups.
    # For 5+ word queries, the ADK agent has full session context and handles them fine.
    # This saves ~200-400ms per follow-up by skipping the Gemini Flash call.
    if len(query.split()) >= 5:
        return query

    client = _get_client()
    if not client:
        return query  # No LLM available, agent handles it with session context

    recent = history[-3:]
    ctx = ""
    for h in recent:
        ctx += f"Q: {h['user_query'][:100]}\nA: {h['bot_response'][:200]}\n"

    try:
        response = client.models.generate_content(
            model="gemini-2.0-flash",
            contents=(
                "Rewrite the follow-up question to be self-contained by replacing pronouns and references "
                "with the specific names/entities they refer to.\n\n"
                "CRITICAL RULES:\n"
                "1. Pronouns like 'he', 'she', 'they', 'it' refer to the entity in the MOST RECENT exchange.\n"
                "2. If you are NOT SURE what the pronoun or reference refers to, return the ORIGINAL question "
                "EXACTLY as written. Do NOT guess. The chatbot has full conversation history and will handle it.\n"
                "3. 'tell me more' or 'explain more simply' -> return ORIGINAL unchanged. The chatbot already has context.\n"
                "4. 'what about that class' without a clear specific class in recent history -> return ORIGINAL unchanged.\n"
                "5. Generic follow-ups like 'what do I do first', 'thanks but thats not what i asked' -> return ORIGINAL unchanged.\n"
                "6. ONLY rewrite when you can confidently replace a pronoun with a SPECIFIC named entity.\n\n"
                f"Recent conversation:\n{ctx}\n"
                f"Follow-up question: {query}\n"
                "Rewritten question (return ONLY the rewritten question, nothing else):"
            ),
            config={"temperature": 0.0, "max_output_tokens": 100},
        )
        rewritten = response.text.strip().strip('"').strip("'")

        if rewritten and 5 < len(rewritten) < 300:
            # If the rewriter just returned the same thing, let the agent handle it
            if rewritten.lower().strip("?. ") == query.lower().strip("?. "):
                print(f"   [REWRITE] Unchanged -> agent will handle with session context")
                return query
            # Safety: verify rewrite didn't completely change the topic
            # Extract key nouns/entities from both and check overlap
            orig_words = set(re.findall(r'\b[a-z]{4,}\b', query.lower()))
            new_words = set(re.findall(r'\b[a-z]{4,}\b', rewritten.lower()))
            # Also check course codes
            orig_codes = set(_COURSE_RE.findall(query.upper()))
            new_codes = set(_COURSE_RE.findall(rewritten.upper()))
            shared = orig_words & new_words
            # If rewrite shares fewer than 2 content words AND no course codes match, reject it
            if len(shared) < 2 and not (orig_codes & new_codes) and not orig_codes.issubset(new_codes):
                print(f"   [REWRITE] Rejected (intent drift): '{query}' -> '{rewritten}' (shared: {shared})")
                return query
            print(f"   [REWRITE] '{query}' -> '{rewritten}'")
            return rewritten

    except Exception as e:
        print(f"   [REWRITE] Failed ({type(e).__name__}: {e})")

    # All layers failed -> pass original to agent. Agent has session history
    # and will either answer from context or ask for clarification.
    return query
