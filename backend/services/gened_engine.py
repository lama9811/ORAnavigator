"""
General Education Progress Engine
==================================
Parses gen ed tags from DegreeWorks course names (e.g., "(SB)", "(AH)")
and computes which gen ed categories are fulfilled vs missing for the
B.S. in Computer Science degree.

Zero LLM calls. Pre-computes and injects into agent context.
"""

import json
import re
from collections import Counter

# Gen ed distribution for B.S. in Computer Science (from MSU catalog)
# https://catalog.morgan.edu/preview_program.php?catoid=26&poid=5968
GENED_REQUIREMENTS = [
    {
        "code": "EC", "label": "English Composition", "count_needed": 2,
        "specific_pairs": [
            ["ENGL 101", "ENGL 111"],  # Comp I: need one of these
            ["ENGL 102", "ENGL 112"],  # Comp II: need one of these
        ],
        "note": "C or better required in Composition II",
    },
    {
        "code": "MQ", "label": "Mathematics", "count_needed": 1,
        "specific": ["MATH 241"],
        "note": "Shared with Supporting courses",
    },
    {
        "code": "IM", "label": "Information & Technology", "count_needed": 1,
        "specific": ["COSC 111"],
        "note": "Shared with CS Major",
    },
    {"code": "HH", "label": "Health & Human Development", "count_needed": 1},
    {"code": "AH", "label": "Arts & Humanities", "count_needed": 2},
    {
        "code": "BP", "label": "Biological & Physical Sciences", "count_needed": 2,
        "note": "One must include a lab (4cr)",
    },
    {"code": "SB", "label": "Social & Behavioral Sciences", "count_needed": 2},
    {"code": "CI", "label": "Cultural & International", "count_needed": 1},
    {"code": "CT", "label": "Critical Thinking", "count_needed": 1},
    {
        "code": "OR", "label": "Freshman Orientation", "count_needed": 1,
        "specific": ["ORNS 106"],
    },
    {
        "code": "ACT", "label": "Physical Activity / FIN / MIND", "count_needed": 1,
        "specific": ["FIN 101", "MIND 101"],
        "alt_tags": ["UR"],
    },
]

_TAG_RE = re.compile(r'\(([A-Z]{2})\)')


def _extract_tags_from_courses(courses_json) -> dict[str, list[str]]:
    """Parse completed/in-progress courses and extract gen ed tags.
    Returns {tag: [course_code, ...]}"""
    if not courses_json:
        return {}
    try:
        courses = json.loads(courses_json) if isinstance(courses_json, str) else courses_json
    except Exception:
        return {}

    tag_map: dict[str, list[str]] = {}
    for c in courses:
        name = c.get("name", "")
        code = c.get("code", "")
        tags = _TAG_RE.findall(name)
        for tag in tags:
            tag_map.setdefault(tag, []).append(code)

    # Also check for specific courses that count even without tags
    code_set = {c.get("code", "").upper().replace(" ", "") for c in courses}
    specific_map = {
        "ENGL101": "EC", "ENGL111": "EC", "ENGL102": "EC", "ENGL112": "EC",
        "MATH241": "MQ", "COSC111": "IM", "ORNS106": "OR",
        "FIN101": "ACT", "MIND101": "ACT",
    }
    for raw_code, tag in specific_map.items():
        normalized = raw_code
        for c in courses:
            c_code = c.get("code", "").upper().replace(" ", "")
            if c_code == normalized and tag not in tag_map.get(tag, []):
                tag_map.setdefault(tag, []).append(c.get("code", ""))
                break

    return tag_map


def compute_gened_progress(dw_dict: dict) -> dict:
    """Compute gen ed progress from DegreeWorks data.

    Returns:
        {
            "categories": [
                {"code": "EC", "label": ..., "needed": 2, "have": 1,
                 "courses": ["ENGL 101"], "status": "incomplete",
                 "missing_text": "NEED: ENGL 102 or ENGL 112"},
                ...
            ],
            "all_complete": bool,
            "missing_summary": str,
        }
    """
    completed_tags = _extract_tags_from_courses(dw_dict.get("courses_completed"))
    ip_tags = _extract_tags_from_courses(dw_dict.get("courses_in_progress"))

    # Merge completed + in-progress
    all_tags: dict[str, list[str]] = {}
    for tag, codes in completed_tags.items():
        all_tags.setdefault(tag, []).extend(codes)
    for tag, codes in ip_tags.items():
        all_tags.setdefault(tag, []).extend(codes)

    # Check ACT alternatives (UR tag, PHEC courses, DSVG courses)
    act_courses = all_tags.get("ACT", []) + all_tags.get("UR", [])
    # Also check for physical activity courses by code prefix
    for field in ("courses_completed", "courses_in_progress"):
        raw = dw_dict.get(field, "")
        if not raw:
            continue
        try:
            courses = json.loads(raw) if isinstance(raw, str) else raw
            for c in courses:
                code = c.get("code", "").upper()
                if code.startswith("PHEC") or code.startswith("DSVG"):
                    act_courses.append(c.get("code", ""))
        except Exception:
            pass
    if act_courses:
        all_tags["ACT"] = list(set(act_courses))

    categories = []
    missing_items = []

    for req in GENED_REQUIREMENTS:
        code = req["code"]
        label = req["label"]
        needed = req["count_needed"]

        # Get fulfilled courses for this tag
        fulfilled = list(set(all_tags.get(code, [])))
        have = len(fulfilled)

        if have >= needed:
            categories.append({
                "code": code, "label": label, "needed": needed, "have": have,
                "courses": fulfilled, "status": "complete", "missing_text": "",
            })
        else:
            # Determine what's missing
            remaining = needed - have
            if req.get("specific_pairs"):
                # EC has paired requirements (Comp I + Comp II)
                taken_normalized = {c.upper().replace(" ", "") for c in fulfilled}
                missing_options = []
                for pair in req["specific_pairs"]:
                    pair_taken = any(p.upper().replace(" ", "") in taken_normalized for p in pair)
                    if not pair_taken:
                        missing_options.append(" or ".join(pair))
                if missing_options:
                    missing_text = f"NEED: {', then '.join(missing_options)}"
                else:
                    missing_text = f"NEED: {remaining} more {label} course(s)"
            elif req.get("specific"):
                # Can give exact course codes
                taken_normalized = {c.upper().replace(" ", "") for c in fulfilled}
                options = [s for s in req["specific"]
                           if s.upper().replace(" ", "") not in taken_normalized]
                if options:
                    missing_text = f"NEED: {' or '.join(options)}"
                else:
                    missing_text = f"NEED: {remaining} more {label} course(s)"
            else:
                missing_text = f"NEED: {remaining} more {label} ({code}) course(s). Check WEBSIS for available {code} courses."

            if req.get("note"):
                missing_text += f" ({req['note']})"

            categories.append({
                "code": code, "label": label, "needed": needed, "have": have,
                "courses": fulfilled, "status": "incomplete", "missing_text": missing_text,
            })
            missing_items.append(missing_text)

    all_complete = len(missing_items) == 0
    missing_summary = " | ".join(missing_items) if missing_items else "All general education requirements complete."

    return {
        "categories": categories,
        "all_complete": all_complete,
        "missing_summary": missing_summary,
    }


def build_gened_context(dw_dict: dict) -> str:
    """Build gen ed progress text for agent context injection."""
    if not dw_dict:
        return ""

    progress = compute_gened_progress(dw_dict)
    if progress["all_complete"]:
        return "GEN ED PROGRESS: All general education requirements are complete.\n\n"

    lines = ["GEN ED PROGRESS (44 credits required for B.S. in Computer Science):"]
    for cat in progress["categories"]:
        courses_str = ", ".join(cat["courses"]) if cat["courses"] else "none"
        if cat["status"] == "complete":
            lines.append(f"  {cat['code']} ({cat['label']}): COMPLETE [{courses_str}]")
        else:
            lines.append(f"  {cat['code']} ({cat['label']}): {cat['have']}/{cat['needed']} done [{courses_str}]. {cat['missing_text']}")

    lines.append(f"\nMISSING GEN ED SUMMARY: {progress['missing_summary']}")
    lines.append("")
    return "\n".join(lines) + "\n"
