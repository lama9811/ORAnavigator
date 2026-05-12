"""
Schedule Planner Engine
========================
Conversational course schedule planner. Backend pre-computes everything:
time conflict detection, level-appropriate filtering, preference scoring,
and generates 2-3 ready-made schedule options.

Zero extra LLM calls. Agent just presents the pre-computed options.
"""

import re
import json
import time as time_module
from typing import Optional
from collections import defaultdict


# =============================================================================
# TIME PARSING & CONFLICT DETECTION
# =============================================================================

def _time_to_minutes(t: str) -> int:
    """Convert '1:00PM' to minutes since midnight (780)."""
    m = re.match(r'(\d{1,2}):(\d{2})(AM|PM)', t.strip())
    if not m:
        return 0
    h, mins, period = int(m.group(1)), int(m.group(2)), m.group(3)
    if period == "PM" and h != 12:
        h += 12
    if period == "AM" and h == 12:
        h = 0
    return h * 60 + mins


def parse_time_slots(time_str: str) -> list[tuple[str, int, int]]:
    """Parse schedule time string into list of (day, start_min, end_min) tuples.

    Handles:
      'MWF 12:00PM-12:50PM'
      'TR 1:00PM-2:40PM'
      'MWF 11:00AM-11:50AM, T 11:00AM-11:50AM'
      'TBA' -> empty list (no conflict possible)
    """
    if not time_str or time_str.strip().upper() in ("TBA", ""):
        return []
    slots = []
    for part in time_str.split(","):
        part = part.strip()
        m = re.match(r'([MTWRF]+)\s+(\d{1,2}:\d{2}(?:AM|PM))\s*-\s*(\d{1,2}:\d{2}(?:AM|PM))', part)
        if not m:
            continue
        days_str, start_str, end_str = m.groups()
        start = _time_to_minutes(start_str)
        end = _time_to_minutes(end_str)
        for day in days_str:
            slots.append((day, start, end))
    return slots


def has_conflict(slots_a: list, slots_b: list) -> bool:
    """Check if two sets of time slots overlap on any day."""
    for day_a, s_a, e_a in slots_a:
        for day_b, s_b, e_b in slots_b:
            if day_a == day_b and s_a < e_b and s_b < e_a:
                return True
    return False


# =============================================================================
# LEVEL-APPROPRIATE COURSE FILTERING
# =============================================================================

LEVEL_RULES = {
    "Freshman":  {"max_level": 200, "max_300": 0, "max_400": 0},
    "Sophomore": {"max_level": 300, "max_300": 1, "max_400": 0},
    "Junior":    {"max_level": 400, "max_300": 99, "max_400": 1},
    "Senior":    {"max_level": 400, "max_300": 99, "max_400": 99},
}


def _get_course_level(code: str) -> int:
    """COSC 351 -> 300, MATH 241 -> 200, ENGL 101 -> 100."""
    m = re.search(r'\d{3}', code)
    return (int(m.group()) // 100) * 100 if m else 0


def _filter_by_level(courses: list[dict], classification: str) -> list[dict]:
    """Remove courses too advanced for the student's classification."""
    rules = LEVEL_RULES.get(classification, LEVEL_RULES["Senior"])
    count_300 = 0
    count_400 = 0
    filtered = []
    for c in courses:
        level = _get_course_level(c["id"])
        if level >= 300 and level < 400:
            if count_300 >= rules["max_300"]:
                continue
            count_300 += 1
        elif level >= 400:
            if count_400 >= rules["max_400"]:
                continue
            count_400 += 1
        if level <= rules["max_level"]:
            filtered.append(c)
    return filtered


# =============================================================================
# PLANNING INTENT DETECTION
# =============================================================================

_PLANNING_KEYWORDS = {
    "plan my", "build my schedule", "help me pick classes",
    "create a schedule", "schedule builder", "plan my semester",
    "help me plan", "make me a schedule", "build a schedule",
    "plan next semester", "plan fall", "plan spring", "plan summer",
    "what should i take", "what courses should i take",
    "what should i take next", "what to take next",
    "recommend courses", "recommend me courses",
    "what can i take", "pick my classes",
    "course recommendations", "suggest courses",
    "tell me what i should take", "tell me what to take",
    "what do i take", "which courses should",
}


def detect_planning_intent(query: str) -> bool:
    """Check if the query is asking to plan a schedule (not just a quick question)."""
    q = query.lower()
    return any(kw in q for kw in _PLANNING_KEYWORDS)


# =============================================================================
# RESPONSE PARSING (extract semester, preferences from user text)
# =============================================================================

def parse_semester_response(text: str) -> Optional[str]:
    """Extract semester key from user text. Returns 'fall_2026' etc. or None."""
    t = text.lower()
    import datetime
    year = datetime.date.today().year
    # Check for explicit year
    year_match = re.search(r'(20\d{2})', t)
    if year_match:
        year = int(year_match.group(1))

    if "fall" in t:
        return f"fall_{year}"
    elif "spring" in t:
        return f"spring_{year + 1}" if "next" in t or datetime.date.today().month > 5 else f"spring_{year}"
    elif "summer" in t:
        return f"summer_{year}"
    return None


def parse_preferences(text: str) -> dict:
    """Extract scheduling preferences from free text.

    Returns: {time_pref, max_credits, interests}
    """
    t = text.lower()

    # Time preference
    time_pref = "any"
    if any(w in t for w in ["morning", "early", " am ", "before noon", "before 12"]):
        time_pref = "morning"
    elif any(w in t for w in ["afternoon", "midday", "after noon", "after 12"]):
        time_pref = "afternoon"
    elif any(w in t for w in ["evening", "night", "late", "after 5", "after 4"]):
        time_pref = "evening"

    # Max credits
    max_credits = 15  # default
    credit_match = re.search(r'(\d{1,2})\s*(?:credits?|cr|hours?)', t)
    if credit_match:
        max_credits = min(int(credit_match.group(1)), 18)
    elif "light" in t or "easy" in t:
        max_credits = 12
    elif "heavy" in t or "full" in t or "max" in t:
        max_credits = 18

    # Interests
    interest_keywords = {
        "ai": ["artificial intelligence", "machine learning", "ai", "ml", "deep learning"],
        "security": ["security", "cyber", "cryptography", "network security"],
        "data": ["data science", "data analytics", "big data", "data"],
        "web": ["web", "mobile", "app development", "frontend"],
        "game": ["game", "graphics", "game design", "game dev"],
        "quantum": ["quantum", "quantum computing"],
        "cloud": ["cloud", "cloud computing"],
        "systems": ["operating systems", "networks", "systems", "architecture"],
    }
    interests = []
    for topic, keywords in interest_keywords.items():
        if any(kw in t for kw in keywords):
            interests.append(topic)

    return {"time_pref": time_pref, "max_credits": max_credits, "interests": interests}


# =============================================================================
# SCHEDULE GENERATION
# =============================================================================

def _score_section(section: dict, course: dict, preferences: dict) -> int:
    """Score a course section based on preferences."""
    score = 0
    time_str = section.get("time", "")
    slots = parse_time_slots(time_str)

    # Time preference scoring
    if slots and preferences.get("time_pref") != "any":
        avg_start = sum(s for _, s, _ in slots) / len(slots)
        if preferences["time_pref"] == "morning" and avg_start < 720:  # before noon
            score += 5
        elif preferences["time_pref"] == "afternoon" and 720 <= avg_start < 1020:  # noon-5pm
            score += 5
        elif preferences["time_pref"] == "evening" and avg_start >= 1020:  # after 5pm
            score += 5

    # Interest matching
    course_name = (course.get("name", "") + " " + course.get("id", "")).lower()
    interest_map = {
        "ai": ["artificial", "intelligence", "machine learning", "ml"],
        "security": ["security", "cyber", "crypto"],
        "data": ["data science", "data analytics", "data"],
        "web": ["web", "mobile"],
        "game": ["game"],
        "quantum": ["quantum"],
        "cloud": ["cloud"],
        "systems": ["operating", "network", "architecture"],
    }
    for interest in preferences.get("interests", []):
        keywords = interest_map.get(interest, [])
        if any(kw in course_name for kw in keywords):
            score += 8

    # Category priority: Required > Supporting > Electives
    cat = course.get("category", "")
    if cat == "Required":
        score += 5
    elif cat == "Supporting":
        score += 3
    else:
        score += 1

    return score


def generate_schedule_options(
    eligible_courses: list[dict],
    semester_key: str,
    preferences: dict,
    schedules: dict,
    classification: str = "Senior",
) -> list[dict]:
    """Generate 2-3 conflict-free schedule options.

    Args:
        eligible_courses: nodes from prereq graph with status='future' and prereqs met
        semester_key: e.g. 'fall_2026'
        preferences: {time_pref, max_credits, interests}
        schedules: _SCHEDULES dict from course_context
        classification: student classification for level filtering
    """
    sem_schedule = schedules.get(semester_key, {})
    if not sem_schedule:
        return []

    # Filter by level
    level_filtered = _filter_by_level(eligible_courses, classification)

    # Match eligible courses with schedule sections
    available = []
    for course in level_filtered:
        code = course["id"]
        sections = sem_schedule.get(code, [])
        if sections:
            for sec in sections:
                slots = parse_time_slots(sec.get("time", ""))
                score = _score_section(sec, course, preferences)
                available.append({
                    "code": code,
                    "name": course["name"],
                    "credits": course["credits"],
                    "category": course["category"],
                    "section": sec.get("section", ""),
                    "instructor": sec.get("instructor", ""),
                    "time": sec.get("time", "TBA"),
                    "room": sec.get("room", "TBA"),
                    "slots": slots,
                    "score": score,
                })

    if not available:
        return []

    # Generate options with different strategies
    options = []

    # Option A: Balanced (highest score, fill to max_credits)
    opt_a = _greedy_schedule(available, preferences.get("max_credits", 15))
    if opt_a:
        options.append({"label": "Balanced", "courses": opt_a})

    # Option B: Lighter load (max_credits - 3)
    lighter_credits = max(preferences.get("max_credits", 15) - 3, 9)
    opt_b = _greedy_schedule(available, lighter_credits)
    if opt_b and _option_key(opt_b) != _option_key(opt_a or []):
        options.append({"label": "Lighter Load", "courses": opt_b})

    # Option C: Interest-focused (boost interest scores)
    boosted = []
    for item in available:
        boosted_item = dict(item)
        # Re-score with higher interest weight
        course_name = (item["name"] + " " + item["code"]).lower()
        interest_map = {
            "ai": ["artificial", "intelligence", "machine learning"],
            "security": ["security", "cyber", "crypto"],
            "data": ["data science", "data analytics"],
            "game": ["game"], "quantum": ["quantum"], "cloud": ["cloud"],
        }
        bonus = 0
        for interest in preferences.get("interests", []):
            keywords = interest_map.get(interest, [])
            if any(kw in course_name for kw in keywords):
                bonus += 15
        boosted_item["score"] = item["score"] + bonus
        boosted.append(boosted_item)

    opt_c = _greedy_schedule(boosted, preferences.get("max_credits", 15))
    if opt_c and _option_key(opt_c) not in {_option_key(o["courses"]) for o in options}:
        options.append({"label": "Interest-Focused", "courses": opt_c})

    return options


def _greedy_schedule(available: list[dict], max_credits: int) -> list[dict]:
    """Greedy: pick highest-scored section for each course, no conflicts, within credit limit."""
    # Sort by score descending
    sorted_avail = sorted(available, key=lambda x: -x["score"])
    selected = []
    selected_slots = []
    total_credits = 0
    used_codes = set()

    for item in sorted_avail:
        if item["code"] in used_codes:
            continue
        if total_credits + item["credits"] > max_credits:
            continue
        # Check conflict with all selected
        if any(has_conflict(item["slots"], s) for s in selected_slots):
            continue
        selected.append(item)
        selected_slots.append(item["slots"])
        total_credits += item["credits"]
        used_codes.add(item["code"])

    return selected


def _option_key(courses: list[dict]) -> str:
    """Unique key for a schedule option (for dedup)."""
    return "|".join(sorted(c["code"] for c in courses))


# =============================================================================
# CONVERSATIONAL STATE MACHINE
# =============================================================================

# In-memory planner sessions: {user_session_key: {phase, semester, preferences, options, ...}}
_planner_sessions: dict[str, dict] = {}
_planner_timestamps: dict[str, float] = {}
_PLANNER_TTL = 600  # 10 minutes


def get_planner_state(user_id: int, session_id: str) -> Optional[dict]:
    """Get active planner state, or None if not planning / expired."""
    key = f"{user_id}_{session_id}"
    ts = _planner_timestamps.get(key, 0)
    if time_module.time() - ts > _PLANNER_TTL:
        _planner_sessions.pop(key, None)
        _planner_timestamps.pop(key, None)
        return None
    return _planner_sessions.get(key)


def set_planner_state(user_id: int, session_id: str, state: dict):
    """Store planner state."""
    key = f"{user_id}_{session_id}"
    _planner_sessions[key] = state
    _planner_timestamps[key] = time_module.time()


def clear_planner_state(user_id: int, session_id: str):
    """Clear planner state (user cancelled or flow completed)."""
    key = f"{user_id}_{session_id}"
    _planner_sessions.pop(key, None)
    _planner_timestamps.pop(key, None)


def process_planner_turn(state: dict, user_msg: str, dw_dict: dict, schedules: dict) -> Optional[dict]:
    """Advance the planner state machine. Returns new state or None if flow ends."""
    phase = state.get("phase", "")
    msg_lower = user_msg.lower()

    # Cancel detection
    if any(w in msg_lower for w in ["cancel", "never mind", "stop planning", "forget it", "nvm"]):
        return None

    if phase == "ask_semester":
        semester = parse_semester_response(user_msg)
        if semester:
            state["phase"] = "ask_preferences"
            state["semester"] = semester
        # If can't parse, stay in same phase (agent will re-ask)
        return state

    elif phase == "ask_preferences":
        prefs = parse_preferences(user_msg)
        state["preferences"] = prefs

        # Now generate schedule options
        try:
            from services.prereq_engine import build_prerequisite_graph
            graph = build_prerequisite_graph(dw_dict, None)
            eligible = [n for n in graph["nodes"] if n["status"] == "future"
                        and (not n["blocked_by"] or all(
                            any(bn == done["id"] for done in graph["nodes"]
                                if done["status"] in ("completed", "in_progress"))
                            for bn in n["blocked_by"]
                        ))]
            classification = dw_dict.get("classification", "Senior") or "Senior"
            options = generate_schedule_options(
                eligible, state["semester"], prefs, schedules, classification
            )
            state["phase"] = "present_options"
            state["options"] = options
        except Exception as e:
            state["phase"] = "error"
            state["error"] = str(e)

        return state

    elif phase == "present_options":
        # User responded to options. Flow complete.
        return None

    return state


def build_planner_context(state: dict) -> str:
    """Format planner state into text for agent context injection."""
    phase = state.get("phase", "")

    if phase == "ask_semester":
        return (
            "\n" + "=" * 40 + "\n"
            "SCHEDULE PLANNER MODE (follow exactly):\n"
            "Ask the student: 'Which semester are you planning for? (e.g., Summer 2026, Fall 2026)'\n"
            "Do NOT generate any schedule yet. Just ask this one question.\n"
            + "=" * 40 + "\n"
        )

    elif phase == "ask_preferences":
        sem = state.get("semester", "").replace("_", " ").title()
        return (
            "\n" + "=" * 40 + "\n"
            f"SCHEDULE PLANNER MODE - {sem}:\n"
            "Ask the student these questions in a natural way:\n"
            "1. Do you prefer morning, afternoon, or evening classes?\n"
            "2. How many credits do you want to take? (12-18, 15 is typical)\n"
            "3. Any subjects you're particularly interested in? (e.g., AI, cybersecurity, game design)\n"
            "Ask all 3 in one message. Keep it casual.\n"
            + "=" * 40 + "\n"
        )

    elif phase == "present_options":
        options = state.get("options", [])
        if not options:
            return (
                "\n" + "=" * 40 + "\n"
                "SCHEDULE PLANNER - No Options Found:\n"
                "Tell the student: 'I couldn't find courses that match your preferences for this semester. "
                "The schedule data may not be available yet. Check WEBSIS or contact the CS department.'\n"
                + "=" * 40 + "\n"
            )

        sem = state.get("semester", "").replace("_", " ").title()
        prefs = state.get("preferences", {})
        ctx = f"\n{'=' * 40}\n"
        ctx += f"SCHEDULE PLANNER - Present these options for {sem}:\n"
        ctx += f"Preferences: {prefs.get('time_pref', 'any')} classes, {prefs.get('max_credits', 15)} credits"
        if prefs.get("interests"):
            ctx += f", interests: {', '.join(prefs['interests'])}"
        ctx += "\n\n"

        for i, opt in enumerate(options):
            total_cr = sum(c["credits"] for c in opt["courses"])
            ctx += f"**Option {chr(65 + i)} - {opt['label']} ({total_cr} credits):**\n"
            for c in opt["courses"]:
                ctx += f"  {c['code']} - {c['name']} | {c['time']} | {c['instructor']} | {c['room']}\n"
            ctx += "  No time conflicts. All prerequisites met.\n\n"

        ctx += "Present these options exactly as shown. Ask which one they prefer or if they want to swap any courses.\n"
        ctx += "=" * 40 + "\n"
        return ctx

    elif phase == "error":
        return (
            "\nSCHEDULE PLANNER ERROR: Could not generate schedule options. "
            "Tell the student to check WEBSIS or contact the CS department.\n"
        )

    return ""
