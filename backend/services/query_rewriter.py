"""
Query Rewriter for Follow-up Resolution
=========================================
Two-layer system for resolving pronouns and references in follow-up queries.
Tuned for the ORA Navigator domain: grants, awards, IRB/IACUC protocols,
SOPs, policies, pre-/post-award processes, and ORA staff.

Layer 1 (deterministic): Entity focus tracker. Extracts entities from both
the user's query AND the bot's response. When both mention the same entity,
that becomes the confirmed "current focus." Pronouns are replaced using
this focus with zero LLM calls.

Layer 2 (LLM fallback): Gemini rewriter. For complex cases where regex
can't determine the entity, uses a fast Gemini call with explicit
"most recent exchange" priority and a "when unsure, pass through" rule.
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
        project = os.getenv("GOOGLE_CLOUD_PROJECT", "oranavigator-vertex-ai")
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


# =============================================================================
# Entity patterns (ORA domain)
# =============================================================================

# People: ORA staff use a mix of Dr./Prof./Mr./Ms./Mrs. titles
_PERSON_RE = re.compile(
    r'(?:Dr\.?\s+|Professor\s+|Prof\.?\s+|Mr\.?\s+|Ms\.?\s+|Mrs\.?\s+)'
    r'([A-Z][a-z]+(?:\s+(?:"[^"]+"\s+)?[A-Z][a-z]+)?)',
)

# Identifiers: grant/award IDs, IRB/IACUC protocols, SOPs, policy numbers.
# Single compound alternation so a findall captures whichever class applies.
_IDENTIFIER_RE = re.compile(
    r'\b('
    r'(?:[A-Z]\d{2}[-\s]?[A-Z]{0,2}\d{4,})'              # NIH (R01-XXXXXX, K99, T32)
    r'|(?:NSF[-\s]?\d{5,})'                               # NSF (NSF-2XXXXXX)
    r'|(?:DE[-\s]?[A-Z]{2}\d{2}[-\s]?[A-Z0-9]+)'          # DOE (DE-SC0012345)
    r'|(?:(?:MSU[-\s])?IRB[-#\s]?\d{2,}(?:[-\s]\d+)?)'    # IRB protocol
    r'|(?:(?:MSU[-\s])?IACUC[-#\s]?\d{2,}(?:[-\s]\d+)?)'  # IACUC protocol
    r'|(?:SOP\s+\d{1,3})'                                  # IACUC SOP
    r'|(?:Policy\s+\d+(?:\.\d+)?)'                         # Policy 5.4
    r')\b',
    re.IGNORECASE,
)

# Areas: ORA program / process areas the user might refer to as "that area"
_AREA_RE = re.compile(
    r'\b('
    r'pre-?award|post-?award|'
    r'IRB|IACUC|COI|conflict of interest|'
    r'research security|NSPM-?33|TCP|technology control plan|'
    r'RCR|responsible conduct of research|'
    r'human subjects|animal research|'
    r'F&A rate|fringe (?:benefit )?rate|indirect cost rate|'
    r'no-?cost extension|NCE|'
    r'effort report(?:ing)?|'
    r'PI handbook|principal investigator handbook|'
    r'export control|misconduct'
    r')\b',
    re.IGNORECASE,
)

# Topic keywords that signal the query is self-contained — presence of any
# means "trust the query, don't bleed in prior turn's focus"
_OWN_TOPIC_RE = re.compile(
    r'\b('
    r'grant|proposal|protocol|award|budget|funding|sponsor|'
    r'irb|iacuc|coi|nce|f&a|fringe|effort|rcr|nspm|'
    r'subaward|advance account|fwa|uei|ein|'
    r'compliance|misconduct|policy|sop|form|template|checklist'
    r')\b',
    re.IGNORECASE,
)

# "My X" — user references their own ORA artifacts or assigned ORA contact
_MY_ARTIFACT_RE = re.compile(
    r'\bmy\s+(PI|grant|proposal|protocol|award|project|budget|effort|'
    r'IRB|IACUC|COI|analyst|accountant|grant officer|pre-?award|post-?award)\b',
    re.IGNORECASE,
)


def is_likely_followup(query: str) -> bool:
    """Detect if a query likely needs conversation context to be understood."""
    q = query.strip()
    if not q:
        return False

    # Very short queries (≤ 2 words) without a specific ORA identifier are likely
    # follow-ups. "SOP 38", "IRB-2025-001", "R01-AB12345" are self-contained.
    words = q.split()
    if len(words) <= 2 and not _IDENTIFIER_RE.search(q):
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

def _extract_focus(user_query: str, bot_response: str) -> dict:
    """Extract the confirmed focus entity from a Q&A exchange.
    Cross-references what the user asked about with what the bot answered about.
    When both sides mention the same entity, it's the confirmed focus.

    Returns: {"person": str|None, "identifier": str|None, "area": str|None}
    """

    focus = {"person": None, "identifier": None, "area": None}

    user_persons = _PERSON_RE.findall(user_query)
    user_ids = _IDENTIFIER_RE.findall(user_query)
    user_areas = _AREA_RE.findall(user_query)

    # "my PI" / "my analyst" -> pull the actual name from the bot reply
    if _MY_ARTIFACT_RE.search(user_query):
        ref_persons = _PERSON_RE.findall(bot_response[:400])
        if ref_persons:
            focus["person"] = ref_persons[0]
            return focus

    bot_persons = _PERSON_RE.findall(bot_response[:400])
    bot_ids = _IDENTIFIER_RE.findall(bot_response[:400])
    bot_areas = _AREA_RE.findall(bot_response[:400])

    # Cross-reference person: user asked about a person, bot answered about them
    if user_persons and bot_persons:
        for up in user_persons:
            up_last = up.split()[-1].lower()
            for bp in bot_persons:
                bp_last = bp.split()[-1].lower()
                if up_last == bp_last:
                    focus["person"] = bp  # bot's version tends to be more complete
                    break

    # User didn't name a person, but bot clearly answered about one
    if not focus["person"] and not user_persons and bot_persons:
        focus["person"] = bot_persons[0]

    # Identifier focus
    if user_ids and bot_ids:
        for uid in user_ids:
            uid_norm = uid.replace(" ", "").lower()
            for bid in bot_ids:
                if bid.replace(" ", "").lower() == uid_norm:
                    focus["identifier"] = bid
                    break
    if not focus["identifier"] and user_ids:
        focus["identifier"] = user_ids[0]

    # Area focus (user's last-mentioned area wins — they set the topic)
    if user_areas:
        focus["area"] = user_areas[-1]
    elif bot_areas:
        focus["area"] = bot_areas[0]

    return focus


def _detect_explicit_override(query: str) -> dict:
    """Detect explicit topic switches like 'go back to Ms. Smith' or
    'what about IRB' or 'switch to SOP 38'.
    Returns the new focus entity if found, or empty dict."""
    override = {}

    # Stop at sentence-ending [?!,;] but NOT '.' — periods are common inside
    # titles like "Ms." or "Dr." and stopping there truncates the target name.
    phrase = re.search(
        r"(?:go back to|back to|switch to|now (?:tell me )?about|"
        r"let'?s talk about|what about|how about)\s+"
        r"(?:the\s+)?(.{1,60}?)(?:[?!,;]|$)",
        query, re.IGNORECASE,
    )
    if not phrase:
        return override

    target = phrase.group(1).strip()
    if not target:
        return override

    # Identifier wins (most specific)
    ident_match = _IDENTIFIER_RE.search(target)
    if ident_match:
        override["identifier"] = ident_match.group(0)
        return override

    # Then area
    area_match = _AREA_RE.search(target)
    if area_match:
        override["area"] = area_match.group(0)
        return override

    # Then person (title optional, capitalized name)
    person_match = re.match(
        r"(?:Dr\.?\s+|Prof\.?\s+|Professor\s+|Mr\.?\s+|Ms\.?\s+|Mrs\.?\s+)?"
        r"([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)",
        target,
    )
    if person_match:
        override["person"] = person_match.group(1)

    return override


def _apply_focus(query: str, focus: dict) -> str | None:
    """Replace pronouns / references in `query` using the confirmed focus.
    Returns the rewritten query, or None if no replacement was made."""

    original = query
    q = query

    if focus.get("person"):
        name = focus["person"]
        # Use the captured name as-is — not all ORA staff are "Dr."
        q = re.sub(r'\bhe\b(?!\w)', name, q, flags=re.IGNORECASE)
        q = re.sub(r'\bhim\b(?!\w)', name, q, flags=re.IGNORECASE)
        q = re.sub(r'\bhis\b(?!\w)', f"{name}'s", q, flags=re.IGNORECASE)
        q = re.sub(r'\bshe\b(?!\w)', name, q, flags=re.IGNORECASE)
        q = re.sub(r'\bher\b(?!\w)', f"{name}'s", q, flags=re.IGNORECASE)
        q = re.sub(r'\bthey\b(?!\w)', name, q, flags=re.IGNORECASE)
        q = re.sub(r'\bthem\b(?!\w)', name, q, flags=re.IGNORECASE)
        q = re.sub(r'\btheir\b(?!\w)', f"{name}'s", q, flags=re.IGNORECASE)

    if focus.get("identifier"):
        ident = focus["identifier"]
        q = re.sub(r'\bit\b(?!\w)', ident, q, flags=re.IGNORECASE, count=1)
        q = re.sub(r'\bthat (?:grant|award|protocol|sop|policy|form|number)\b', ident, q, flags=re.IGNORECASE)
        q = re.sub(r'\bthe (?:grant|award|protocol|sop|policy|form|number)\b', ident, q, flags=re.IGNORECASE)

    if focus.get("area"):
        area = focus["area"]
        q = re.sub(r'\bthat (?:area|process|program|topic)\b', area, q, flags=re.IGNORECASE)
        q = re.sub(r'\bthe (?:area|process|program|topic)\b', area, q, flags=re.IGNORECASE)
        # Only consume "it" if identifier didn't already
        if not focus.get("identifier"):
            q = re.sub(r'\bit\b(?!\w)', area, q, flags=re.IGNORECASE, count=1)

    if q != original:
        print(f"   [FOCUS] '{original}' -> '{q}'")
        return q

    return None


# =============================================================================
# LAYER 2: LLM Rewriter (fallback for complex cases)
# =============================================================================

def rewrite_query(query: str, history: list[dict]) -> str:
    """Rewrite a follow-up query to be self-contained.

    Layer 0: Regex override ("go back to X", "what about IRB")
    Layer 1: Deterministic focus tracker (cross-references user query + bot response)
    Layer 2: Gemini rewriter (fallback for ambiguous short follow-ups)

    Args:
        query: The user's current message
        history: Recent conversation turns [{user_query, bot_response}, ...]

    Returns:
        Rewritten query (or original if not a follow-up / rewrite fails)
    """
    if not history or not is_likely_followup(query):
        return query

    # Layer 0: explicit topic overrides
    override = _detect_explicit_override(query)
    if override:
        print(f"   [OVERRIDE] Detected explicit switch: {override}")
        focused = _apply_focus(query, override)
        if focused:
            return focused

    # Smart skip: if the query already names its own entities, don't smuggle
    # the previous turn's focus into it. Prevents context-bleed bugs.
    has_own_person = bool(_PERSON_RE.search(query))
    has_own_identifier = bool(_IDENTIFIER_RE.search(query))
    has_own_area = bool(_AREA_RE.search(query))
    has_own_topic = bool(_OWN_TOPIC_RE.search(query))

    if not (has_own_person or has_own_identifier or has_own_area or has_own_topic):
        # Layer 1: deterministic focus replacement from the last turn
        last_turn = history[-1]
        focus = _extract_focus(last_turn["user_query"], last_turn["bot_response"])
        focused = _apply_focus(query, focus)
        if focused:
            return focused

    # Layer 2: LLM rewriter — only for very short ambiguous follow-ups.
    # 5+ word queries: the ADK agent has full session context and handles them.
    # Skipping the LLM here saves ~200-400ms per follow-up.
    if len(query.split()) >= 5:
        return query

    client = _get_client()
    if not client:
        return query

    recent = history[-3:]
    ctx = ""
    for h in recent:
        ctx += f"Q: {h['user_query'][:100]}\nA: {h['bot_response'][:200]}\n"

    try:
        response = client.models.generate_content(
            model="gemini-2.0-flash",
            contents=(
                "You rewrite follow-up questions in an ORA (research administration) chatbot "
                "to be self-contained — replacing pronouns and references with the specific "
                "names, grant/protocol IDs, or program areas they refer to.\n\n"
                "CRITICAL RULES:\n"
                "1. Pronouns like 'he', 'she', 'they', 'it' refer to the entity in the MOST RECENT exchange.\n"
                "2. If you are NOT SURE what the pronoun or reference points to, return the ORIGINAL "
                "question EXACTLY as written. Do NOT guess. The chatbot has full conversation history "
                "and will handle it.\n"
                "3. 'tell me more' or 'explain more simply' -> return ORIGINAL unchanged.\n"
                "4. 'what about that protocol/grant/policy' WITHOUT a clear specific one in recent "
                "history -> return ORIGINAL unchanged.\n"
                "5. Generic follow-ups like 'what do I do first', 'thanks but that's not what i asked' "
                "-> return ORIGINAL unchanged.\n"
                "6. ONLY rewrite when you can confidently replace a pronoun with a SPECIFIC named "
                "entity (a person, an identifier like IRB-2025-001 or SOP 38, or an area like IACUC "
                "or pre-award).\n\n"
                f"Recent conversation:\n{ctx}\n"
                f"Follow-up question: {query}\n"
                "Rewritten question (return ONLY the rewritten question, nothing else):"
            ),
            config={"temperature": 0.0, "max_output_tokens": 100},
        )
        rewritten = response.text.strip().strip('"').strip("'")

        if rewritten and 5 < len(rewritten) < 300:
            if rewritten.lower().strip("?. ") == query.lower().strip("?. "):
                print(f"   [REWRITE] Unchanged -> agent will handle with session context")
                return query
            # Intent-drift guard: reject rewrites that share fewer than 2 content
            # words AND no identifiers with the original.
            orig_words = set(re.findall(r'\b[a-z]{4,}\b', query.lower()))
            new_words = set(re.findall(r'\b[a-z]{4,}\b', rewritten.lower()))
            orig_ids = set(_IDENTIFIER_RE.findall(query))
            new_ids = set(_IDENTIFIER_RE.findall(rewritten))
            shared = orig_words & new_words
            if len(shared) < 2 and not (orig_ids & new_ids) and not orig_ids.issubset(new_ids):
                print(f"   [REWRITE] Rejected (intent drift): '{query}' -> '{rewritten}' (shared: {shared})")
                return query
            print(f"   [REWRITE] '{query}' -> '{rewritten}'")
            return rewritten

    except Exception as e:
        print(f"   [REWRITE] Failed ({type(e).__name__}: {e})")

    return query
