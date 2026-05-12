"""
Split academic_courses.json into one doc per course with structured fields.
Implements Plan A2: structured metadata for course-specific search.
"""

import json
import re
from pathlib import Path

KB_DIR = Path(__file__).parent.parent / "backend" / "kb_structured"
OUT_DIR = KB_DIR / "_generated_courses"
SOURCE_FILE = KB_DIR / "academic_courses.json"


def parse_courses(content: str) -> list[dict]:
    """Split content into per-course chunks."""
    chunks = re.split(r"\n(?=(?:COSC|CLCO|MATH|PHYS|ENGL) \d+\s*-\s*)", content)
    courses = []

    for chunk in chunks:
        chunk = chunk.strip()
        if not re.match(r"^(?:COSC|CLCO|MATH|PHYS|ENGL) \d+", chunk):
            continue

        first_line = chunk.split("\n")[0].strip()
        m = re.match(r"((?:COSC|CLCO|MATH|PHYS|ENGL) \d+)\s*-\s*(.+)", first_line)
        if not m:
            continue

        course = {
            "code": m.group(1).strip(),
            "name": m.group(2).strip(),
            "raw": chunk,
        }

        for field, pattern in [
            ("credits", r"Credits:\s*(\d+)"),
            ("prerequisites", r"Prerequisites?:\s*(.+?)(?:\n[A-Z]|\Z)"),
            ("offered", r"Offered:\s*(.+?)(?:\n[A-Z]|\Z)"),
        ]:
            mm = re.search(pattern, chunk, re.DOTALL)
            if mm:
                course[field] = mm.group(1).strip().replace("\n", " ")

        courses.append(course)

    return courses


def build_doc(course: dict) -> dict:
    code = course["code"]
    slug = code.replace(" ", "_").lower()
    doc_id = f"course_{slug}"

    prereq_codes = []
    if course.get("prerequisites"):
        prereq_codes = re.findall(
            r"(?:COSC|CLCO|MATH|PHYS|ENGL) \d+", course["prerequisites"]
        )

    parts = [f"{code} - {course['name']}"]
    if course.get("credits"):
        parts.append(f"Credits: {course['credits']}")
    if course.get("prerequisites"):
        parts.append(f"Prerequisites: {course['prerequisites']}")
    if course.get("offered"):
        parts.append(f"Offered: {course['offered']}")

    return {
        "doc_id": doc_id,
        "title": f"Course: {code} - {course['name']}",
        "category": "academic",
        "subcategory": "course_detail",
        "course_code": code,
        "course_name": course["name"],
        "credits": int(course["credits"]) if course.get("credits", "").isdigit() else 0,
        "prerequisites_text": course.get("prerequisites", ""),
        "prerequisite_codes": prereq_codes,
        "offered": course.get("offered", ""),
        "content": "\n".join(parts),
        "source_file": "academic_courses.txt",
    }


def main():
    OUT_DIR.mkdir(exist_ok=True)
    source = json.loads(SOURCE_FILE.read_text())
    courses = parse_courses(source["content"])

    print(f"[INFO] Parsed {len(courses)} courses from {SOURCE_FILE.name}")

    seen = set()
    for c in courses:
        doc = build_doc(c)
        if doc["doc_id"] in seen:
            continue
        seen.add(doc["doc_id"])
        out_path = OUT_DIR / f"{doc['doc_id']}.json"
        out_path.write_text(json.dumps(doc, indent=2))
        print(f"  [OK] {doc['doc_id']:30s} | {c['code']} | {c['name']}")

    print(f"\n[RESULT] Wrote {len(seen)} unique course docs to {OUT_DIR}")


if __name__ == "__main__":
    main()
