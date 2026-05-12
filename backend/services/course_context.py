"""
Course Context Builder
=======================
Pre-computes prereq analysis, schedule lookups, and course eligibility
on the backend, then injects results into the agent's session state.

Zero extra LLM calls. The agent reads this like DegreeWorks/Canvas data.
"""

import json
import os
import re
from pathlib import Path
from typing import Optional

_DATA_DIR = Path(__file__).parent.parent / "data_sources"
_KB_DIR = Path(__file__).parent.parent / "kb_structured"

# --- Load prerequisite map from classes.json ---
_PREREQ_MAP = {}
try:
    _classes_path = _DATA_DIR / "classes.json"
    if _classes_path.exists():
        with open(_classes_path) as f:
            _classes = json.load(f)
        for c in _classes.get("courses", []):
            code = c.get("course_code", "").strip().upper()
            if not code:
                continue
            course_prereqs = []
            non_course = []
            for p in c.get("prerequisites", []):
                found = re.findall(r'[A-Z]{2,4}\s*\d{3}', p.upper())
                if found:
                    course_prereqs.extend(found)
                else:
                    non_course.append(p.strip())
            _PREREQ_MAP[code] = {
                "name": c.get("course_name", ""),
                "credits": c.get("credits", 0),
                "category": c.get("category", ""),
                "course_prereqs": course_prereqs,
                "non_course_prereqs": non_course,
            }
except Exception as e:
    print(f"[COURSE_CTX] Failed to load classes.json: {e}")

# --- Load schedule data from KB structured files ---
_SCHEDULES = {}
try:
    for schedule_file in _KB_DIR.glob("schedule_*.json"):
        with open(schedule_file) as f:
            doc = json.load(f)
        content = doc.get("content", "")
        first_line = content.split("\n")[0]
        sem_match = re.search(r'(Fall|Spring|Summer)\s+\d{4}', first_line)
        if not sem_match:
            continue
        sem_key = sem_match.group().lower().replace(" ", "_")
        courses = {}
        current_code = None
        for line in content.split("\n"):
            line = line.strip()
            code_match = re.match(r'^([A-Z]{2,4}\d{3})\s*-\s*(.+)', line)
            if code_match:
                raw_code = code_match.group(1)
                current_code = re.sub(r'([A-Z]+)(\d+)', r'\1 \2', raw_code)
                if current_code not in courses:
                    courses[current_code] = []
                continue
            sec_match = re.match(r'\s*Section\s+(\S+):\s*(.+)', line)
            if sec_match and current_code:
                parts = sec_match.group(2).split("|")
                instructor = parts[0].strip() if len(parts) > 0 else ""
                time_slot = parts[1].strip() if len(parts) > 1 else ""
                room = parts[2].strip().replace("Room: ", "") if len(parts) > 2 else ""
                courses[current_code].append({
                    "section": sec_match.group(1),
                    "instructor": instructor,
                    "time": time_slot,
                    "room": room,
                })
        _SCHEDULES[sem_key] = courses
except Exception as e:
    print(f"[COURSE_CTX] Failed to load schedules: {e}")

# --- Load faculty map from KB structured file ---
_FACULTY_MAP = {}  # {"wang": {name, office, phone, email, research}, ...}
_FACULTY_BY_FULL = {}  # {"shuangbao wang": {...}, "paul wang": {...}}


def _index_faculty(fac: dict):
    """Index a faculty member by last name, full name, and nickname variants."""
    name = fac["name"]
    words = name.lower().split()
    entry = {k: v for k, v in fac.items() if k != "_alt"}
    # Last name
    _FACULTY_MAP[words[-1]] = dict(entry)
    # Full name
    _FACULTY_BY_FULL[name.lower()] = dict(entry)
    # First + last
    if len(words) >= 2:
        _FACULTY_BY_FULL[f"{words[0]} {words[-1]}"] = dict(entry)
    # Middle names as standalone lookups (catches "Paul" for "Shuangbao Paul Wang")
    for w in words[1:-1]:
        if len(w) > 2:
            _FACULTY_BY_FULL[f"{w} {words[-1]}"] = dict(entry)
            _FACULTY_MAP[w] = dict(entry)  # "paul" -> Wang
    # Alternate name (e.g. "Radwan Shushane" for "Radhouane Chouchane")
    alt = fac.get("_alt", "")
    if alt:
        alt_words = alt.lower().split()
        if alt_words:
            _FACULTY_MAP[alt_words[-1]] = dict(entry)
            _FACULTY_BY_FULL[alt.lower()] = dict(entry)


try:
    _faculty_path = _KB_DIR / "academic_faculty.json"
    if _faculty_path.exists():
        with open(_faculty_path) as f:
            doc = json.load(f)
        content = doc.get("content", "")
        # Parse faculty entries: "Dr. Name - Title\nOffice: ...\nEmail: ...\nResearch: ..."
        current = {}
        for line in content.split("\n"):
            line = line.strip()
            # New faculty entry
            name_match = re.match(r'^(?:Dr\.\s*)?(.+?)\s*[-–]\s*(Professor|Associate|Assistant|Lecturer|Research|Coordinator|Chair)', line)
            if name_match:
                if current.get("name"):
                    _index_faculty(current)
                raw_name = name_match.group(1).strip()
                # Handle alternate names in parens: "Radhouane Chouchane (Radwan Shushane)"
                alt_match = re.search(r'\((.+?)\)', raw_name)
                alt_name = alt_match.group(1).strip() if alt_match else ""
                # Clean up quoted nicknames: Shuangbao "Paul" Wang -> Shuangbao Paul Wang
                clean_name = re.sub(r'\(.*?\)', '', raw_name)  # Remove parens
                clean_name = re.sub(r'["\']', '', clean_name).strip()
                clean_name = re.sub(r'\s+', ' ', clean_name)
                current = {"name": clean_name, "title": name_match.group(2).strip() + line[name_match.end():].strip(), "_alt": alt_name}
                current["office"] = ""
                current["phone"] = ""
                current["email"] = ""
                current["research"] = ""
                continue
            if not current.get("name"):
                continue
            if line.startswith("Office:"):
                current["office"] = line[7:].strip()
            elif line.startswith("Lab:"):
                current["lab"] = line[4:].strip()
            elif line.startswith("Phone:"):
                current["phone"] = line[6:].strip()
            elif line.startswith("Email:"):
                current["email"] = line[6:].strip()
            elif line.startswith("Research:"):
                current["research"] = line[9:].strip()
        # Save last entry
        if current.get("name"):
            _index_faculty(current)
except Exception as e:
    print(f"[COURSE_CTX] Failed to load faculty: {e}")

# Also load department leadership (Dr. Wang, Dr. Rahman) who may not be in the faculty list
try:
    _leadership_path = _KB_DIR / "general_department_leadership.json"
    if _leadership_path.exists():
        with open(_leadership_path) as f:
            doc = json.load(f)
        content = doc.get("content", "")
        # Parse structured leadership entries
        current = None
        for line in content.split("\n"):
            line = line.strip()
            if not line:
                continue
            # Detect entry boundaries by role labels
            if "DEPARTMENT CHAIR:" in line or "ASSOCIATE CHAIR:" in line:
                if current and current.get("name"):
                    _index_faculty(current)
                current = {"name": "", "title": "", "office": "", "phone": "", "email": "", "research": ""}
                continue
            if current is None:
                continue
            # Stop at non-CS leadership (PRESIDENT, PROVOST, DEAN)
            if any(x in line for x in ["UNIVERSITY PRESIDENT", "PROVOST:", "SCMNS DEAN:", "DEPARTMENT CONTACT:"]):
                if current and current.get("name"):
                    _index_faculty(current)
                current = None
                continue
            # Extract fields
            if not current.get("name") and re.search(r'Dr\.\s', line):
                # "The chair ... is Dr. Shuangbao "Paul" Wang."
                nm = re.search(r'Dr\.\s+(.+?)(?:\.|$)', line)
                if nm:
                    raw = nm.group(1).strip()
                    clean = re.sub(r'["\']', '', raw).strip()
                    clean = re.sub(r'\s+', ' ', clean)
                    current["name"] = clean
            elif "Professor and Chair" in line:
                current["title"] = "Professor and Department Chair"
            elif "Professor and Associate Chair" in line:
                current["title"] = "Professor and Associate Chair"
            elif line.startswith("Office:"):
                current["office"] = line[7:].strip()
            elif line.startswith("Phone:"):
                current["phone"] = line[6:].strip()
            elif line.startswith("Email:"):
                current["email"] = line[6:].strip()
            elif line.startswith("Research"):
                current["research"] = line.split(":", 1)[1].strip() if ":" in line else ""
        if current and current.get("name"):
            _index_faculty(current)
except Exception as e:
    print(f"[COURSE_CTX] Failed to load leadership: {e}")

print(f"[COURSE_CTX] Loaded {len(_PREREQ_MAP)} courses, {len(_SCHEDULES)} schedules, {len(_FACULTY_MAP)} faculty")


# =============================================================================
# HELPERS
# =============================================================================

def _extract_completed_codes(dw_dict: dict) -> dict:
    """Returns {code: grade} from DegreeWorks completed courses."""
    completed = {}
    raw = dw_dict.get("courses_completed", "")
    if not raw:
        return completed
    try:
        courses = json.loads(raw) if isinstance(raw, str) else raw
        for c in courses:
            code = re.sub(r'([A-Z]+)\s*(\d+)', r'\1 \2', c.get("code", "").strip().upper())
            if code:
                completed[code] = c.get("grade", "")
    except Exception:
        pass
    return completed


def _extract_in_progress_codes(dw_dict: dict) -> set:
    ip = set()
    raw = dw_dict.get("courses_in_progress", "")
    if not raw:
        return ip
    try:
        courses = json.loads(raw) if isinstance(raw, str) else raw
        for c in courses:
            code = re.sub(r'([A-Z]+)\s*(\d+)', r'\1 \2', c.get("code", "").strip().upper())
            if code:
                ip.add(code)
    except Exception:
        pass
    return ip


def _extract_remaining_codes(dw_dict: dict) -> list:
    raw = dw_dict.get("courses_remaining", "")
    if not raw:
        return []
    try:
        courses = json.loads(raw) if isinstance(raw, str) else raw
        return [
            {"code": re.sub(r'([A-Z]+)\s*(\d+)', r'\1 \2', c.get("code", "").strip().upper()),
             "name": c.get("name", "")}
            for c in courses if c.get("code")
        ]
    except Exception:
        return []


def _get_next_semesters() -> list:
    from datetime import date
    today = date.today()
    month, year = today.month, today.year
    if month <= 5:
        return [f"summer_{year}", f"fall_{year}"]
    elif month <= 7:
        return [f"fall_{year}", f"spring_{year+1}"]
    else:
        return [f"spring_{year+1}", f"summer_{year+1}"]


# =============================================================================
# MAIN ENTRY POINT
# =============================================================================

def build_course_context(dw_dict: Optional[dict], query: str) -> str:
    """Pre-compute course-aware context based on the query and DegreeWorks data.

    Called in parallel with other fetches in the chat endpoint.
    Returns a compact text string injected into the agent's session state.
    """
    if not dw_dict:
        return ""

    query_lower = query.lower()
    parts = []

    completed = _extract_completed_codes(dw_dict)
    in_progress = _extract_in_progress_codes(dw_dict)
    remaining = _extract_remaining_codes(dw_dict)

    # Detect intent
    asks_prereq = any(kw in query_lower for kw in ["prerequisite", "prereq", "can i take", "eligible", "ready for"])
    asks_schedule = any(kw in query_lower for kw in ["schedule", "when is", "who teaches", "offered", "section"])
    asks_recommend = any(kw in query_lower for kw in ["recommend", "should i take", "what to take", "suggest", "next semester", "what courses"])

    # Extract course codes from query
    query_codes = [re.sub(r'([A-Z]+)\s*(\d+)', r'\1 \2', m.upper())
                   for m in re.findall(r'[A-Z]{2,4}\s*\d{3}', query, re.IGNORECASE)]

    # --- PREREQ ANALYSIS ---
    if asks_prereq or query_codes:
        codes_to_check = query_codes if query_codes else [r["code"] for r in remaining[:8]]
        lines = []
        for code in codes_to_check:
            info = _PREREQ_MAP.get(code)
            if not info:
                continue
            met, missing = [], []
            for p in info["course_prereqs"]:
                if p in completed:
                    met.append(f"{p} ({completed[p]})")
                elif p in in_progress:
                    met.append(f"{p} (in progress)")
                else:
                    missing.append(p)
            status = "ELIGIBLE" if not missing else "NOT ELIGIBLE"
            line = f"{code} - {info['name']} [{info['credits']}cr]: {status}"
            if met:
                line += f" | Met: {', '.join(met)}"
            if missing:
                line += f" | Missing: {', '.join(missing)}"
            if info["non_course_prereqs"]:
                line += f" | Other requirements: {', '.join(info['non_course_prereqs'])}"
            lines.append(line)
        if lines:
            parts.append("PREREQUISITE CHECK:\n" + "\n".join(lines))

    # --- SCHEDULE FOR RELEVANT COURSES ---
    if asks_schedule or asks_recommend:
        next_sems = _get_next_semesters()
        for sem_key in next_sems:
            schedule = _SCHEDULES.get(sem_key)
            if not schedule:
                continue
            sem_label = sem_key.replace("_", " ").title()

            if query_codes:
                relevant = {c: schedule[c] for c in query_codes if c in schedule}
            elif asks_recommend and remaining:
                rem_codes = {r["code"] for r in remaining}
                relevant = {c: schedule[c] for c in rem_codes if c in schedule}
            else:
                continue

            if relevant:
                lines = [f"SCHEDULE ({sem_label}):"]
                for code, sections in sorted(relevant.items()):
                    name = _PREREQ_MAP.get(code, {}).get("name", "")
                    for s in sections:
                        lines.append(f"  {code} {name} | {s['instructor']} | {s['time']} | {s['room']}")
                parts.append("\n".join(lines))

    # --- ELIGIBLE REMAINING COURSES ---
    if asks_recommend and remaining:
        eligible, blocked = [], []
        for r in remaining:
            info = _PREREQ_MAP.get(r["code"])
            if not info:
                eligible.append(f"{r['code']} - {r['name']} (prereqs unknown)")
                continue
            missing = [p for p in info["course_prereqs"] if p not in completed and p not in in_progress]
            if not missing:
                eligible.append(f"{r['code']} - {info['name']} ({info['credits']}cr) READY")
            else:
                blocked.append(f"{r['code']} - {info['name']} needs {', '.join(missing)}")
        if eligible:
            parts.append("ELIGIBLE COURSES (prereqs met):\n" + "\n".join(eligible))
        if blocked:
            parts.append("BLOCKED COURSES (prereqs missing):\n" + "\n".join(blocked))

    # --- FACULTY LOOKUP ---
    faculty_ctx = build_faculty_context(query)
    if faculty_ctx:
        parts.append(faculty_ctx)

    # --- ADVISOR AUTO-ATTACH ---
    if any(kw in query_lower for kw in ["advisor", "advising", "my advisor"]):
        advisor_ctx = build_advisor_context(dw_dict)
        if advisor_ctx:
            parts.append(advisor_ctx)

    # --- FUZZY COURSE MATCH ---
    fuzzy_ctx = build_fuzzy_course_context(query)
    if fuzzy_ctx:
        parts.append(fuzzy_ctx)

    if not parts:
        return ""

    return "\n".join(parts) + "\n"


# =============================================================================
# FACULTY CONTEXT INJECTION
# =============================================================================

def build_faculty_context(query: str) -> str:
    """If the query mentions a professor name, inject their full profile.
    Saves 2-3s of KB search time per faculty query."""
    if not _FACULTY_MAP:
        return ""

    query_lower = query.lower()

    # Extract professor names from query
    all_names = set()

    # "Dr. Wang", "Dr. Naja Mack", "professor sakk"
    for m in re.finditer(r'(?:dr\.?\s+|professor\s+|prof\.?\s+)(\w+(?:\s+\w+)?)', query_lower):
        words = m.group(1).split()
        all_names.update(words)  # Add each word individually

    # "who is X", "about X", "email of X"
    for m in re.finditer(r'(?:who is|about|email of|contact|reach out to)\s+(?:dr\.?\s+)?(\w+)', query_lower):
        all_names.add(m.group(1))

    # Filter out common words that aren't names
    stop_words = {"the", "a", "an", "my", "and", "or", "to", "for", "in", "on", "at", "is",
                  "are", "do", "does", "what", "who", "how", "research", "teach", "teaches",
                  "office", "email", "phone", "hours", "professor", "also", "can", "his", "her"}
    all_names = [n for n in all_names if n not in stop_words and len(n) > 2]

    matched = []
    seen = set()
    for name in all_names:
        name = name.strip()
        # Try last name match
        if name in _FACULTY_MAP and name not in seen:
            seen.add(name)
            matched.append(_FACULTY_MAP[name])
            continue
        # Try full name match
        if name in _FACULTY_BY_FULL and name not in seen:
            seen.add(name)
            matched.append(_FACULTY_BY_FULL[name])
            continue
        # Try partial match (first or last name anywhere)
        for key, fac in _FACULTY_BY_FULL.items():
            if name in key and fac["name"] not in seen:
                seen.add(fac["name"])
                matched.append(fac)
                break

    if not matched:
        # Check for research topic queries: "who does cybersecurity research"
        research_keywords = re.findall(
            r'(?:research(?:es|ing)?|(?:who|which)\s+.*?(?:does|do))\s+(.+?)(?:\s+research|\?|$)',
            query_lower
        )
        if research_keywords:
            topic = research_keywords[0].strip().rstrip("?. ")
            for fac in _FACULTY_MAP.values():
                if topic in fac.get("research", "").lower() and fac["name"] not in seen:
                    seen.add(fac["name"])
                    matched.append(fac)

    if not matched:
        return ""

    lines = ["FACULTY DATA (pre-loaded):"]
    for f in matched:
        line = f"  {f['name']}"
        if f.get("title"):
            line += f" - {f['title']}"
        if f.get("office"):
            line += f" | Office: {f['office']}"
        if f.get("phone"):
            line += f" | Phone: {f['phone']}"
        if f.get("email"):
            line += f" | Email: {f['email']}"
        if f.get("research"):
            line += f" | Research: {f['research']}"
        lines.append(line)

    return "\n".join(lines)


# =============================================================================
# ADVISOR AUTO-ATTACH
# =============================================================================

def build_advisor_context(dw_dict: Optional[dict]) -> str:
    """When query mentions advisor, inject their full contact from faculty map."""
    if not dw_dict:
        return ""

    advisor_name = dw_dict.get("advisor", "")
    if not advisor_name:
        return ""

    # Look up advisor in faculty map
    last_name = advisor_name.strip().split()[-1].lower() if advisor_name else ""
    fac = _FACULTY_MAP.get(last_name)
    if not fac:
        # Try full name match
        for key, f in _FACULTY_BY_FULL.items():
            if last_name in key:
                fac = f
                break

    if not fac:
        return f"YOUR ADVISOR: {advisor_name} (contact details not in faculty database)"

    parts = [f"YOUR ADVISOR: {fac['name']}"]
    if fac.get("office"):
        parts.append(f"  Office: {fac['office']}")
    if fac.get("phone"):
        parts.append(f"  Phone: {fac['phone']}")
    if fac.get("email"):
        parts.append(f"  Email: {fac['email']}")

    return "\n".join(parts)


# =============================================================================
# FUZZY COURSE CODE MATCHING
# =============================================================================

def build_fuzzy_course_context(query: str) -> str:
    """If query mentions a course code that doesn't exist, suggest closest matches."""
    query_codes = re.findall(r'[A-Z]{2,4}\s*\d{3}', query, re.IGNORECASE)
    if not query_codes:
        return ""

    suggestions = []
    for raw_code in query_codes:
        code = re.sub(r'([A-Z]+)\s*(\d+)', r'\1 \2', raw_code.upper())
        if code in _PREREQ_MAP:
            continue  # Code exists, no fuzzy needed

        # Find closest matches by prefix + nearby numbers
        prefix = re.match(r'([A-Z]+)', code).group(1) if re.match(r'([A-Z]+)', code) else ""
        num = re.search(r'(\d+)', code)
        if not prefix or not num:
            continue
        num_val = int(num.group(1))

        close = []
        for existing_code, info in _PREREQ_MAP.items():
            if not existing_code.startswith(prefix):
                continue
            existing_num = re.search(r'(\d+)', existing_code)
            if existing_num and abs(int(existing_num.group(1)) - num_val) <= 20:
                close.append(f"{existing_code} - {info['name']}")

        if close:
            suggestions.append(f"COURSE NOT FOUND: {code}. Similar courses: {', '.join(close[:4])}")

    if not suggestions:
        return ""

    return "\n".join(suggestions)
