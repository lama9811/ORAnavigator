"""
Course code normalization and shared utilities.
Handles the format differences between Canvas, DegreeWorks, and classes.json.
"""

import re


def normalize_course_code(raw: str) -> str:
    """Normalize any course code format to 'DEPT NNN' (e.g. 'COSC 220').

    Handles:
    - Canvas: "COSC.220_Spring 2026", "COSC 220.001_Spring 2026"
    - DegreeWorks: "COSC 220", "COSC220"
    - classes.json: "COSC 220"
    - Prereq strings: "COSC 112 (Grade C or higher)"
    """
    if not raw or not isinstance(raw, str):
        return ""
    # Strip semester info, section numbers, dots, parenthetical notes
    cleaned = raw.split("_")[0].split("(")[0].strip()
    cleaned = cleaned.replace(".", " ")
    # Extract department + number
    match = re.match(r'([A-Z]{2,4})\s*(\d{3})', cleaned.upper())
    if match:
        return f"{match.group(1)} {match.group(2)}"
    return cleaned.upper().strip()


def extract_course_codes(text: str) -> list[str]:
    """Extract all course codes from a string (e.g. prerequisite text).

    Returns list of normalized codes like ['COSC 112', 'MATH 241'].
    """
    if not text or not isinstance(text, str):
        return []
    matches = re.findall(r'[A-Z]{2,4}\s*\d{3}', text.upper())
    return [normalize_course_code(m) for m in matches]
