"""
Build per-professor-per-semester teaching docs from schedule_*.json files.

Inverts the schedule data: instead of one doc per semester listing every course,
produces one doc per (professor x semester) listing what they teach.

This makes queries like "what is Dr. Wang teaching in Fall 2026" hit a single
targeted doc instead of forcing the LLM to grep through 56 courses.
"""

import json
import re
from collections import defaultdict
from pathlib import Path

KB_DIR = Path(__file__).parent.parent / "backend" / "kb_structured"
OUT_DIR = KB_DIR / "_generated_teaching"

# Match: BIOI511 - INTRODUCTION TO BIOINFORMATICS
COURSE_HEADER_RE = re.compile(r"^([A-Z]{2,5}\d{3})\s*-\s*(.+)$")
# Match: Section M01: Sarita Limbu | T 5:00PM-7:30PM | Room: KEYH-G68 | 3 credits | Traditional
SECTION_RE = re.compile(
    r"Section\s+(\S+):\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|\s*Room:\s*([^|]+?)\s*\|\s*(\d+)\s*credits\s*\|\s*(.+?)\s*$"
)


def parse_schedule(content: str, semester_label: str) -> list[dict]:
    """Walk content line by line, returning list of section dicts."""
    sections = []
    current_course = None

    for line in content.splitlines():
        m = COURSE_HEADER_RE.match(line.strip())
        if m:
            current_course = {"code": m.group(1), "name": m.group(2).strip()}
            continue

        m = SECTION_RE.search(line)
        if m and current_course:
            sections.append({
                "course_code": current_course["code"],
                "course_name": current_course["name"],
                "section": m.group(1).strip(),
                "instructor_raw": m.group(2).strip(),
                "time": m.group(3).strip(),
                "room": m.group(4).strip(),
                "credits": int(m.group(5)),
                "format": m.group(6).strip(),
                "semester": semester_label,
            })

    return sections


def normalize_instructor(name: str) -> str:
    """Clean instructor name for matching. Returns empty for TBA/staff."""
    name = name.strip()
    if not name or name.lower() in {"tba", "staff", "to be announced"}:
        return ""
    # Drop titles
    name = re.sub(r"^(Dr\.?|Prof\.?|Mr\.?|Ms\.?|Mrs\.?)\s+", "", name)
    return name.strip()


def slugify(name: str) -> str:
    return re.sub(r"[^a-zA-Z0-9]+", "_", name).strip("_").lower()


def main():
    OUT_DIR.mkdir(exist_ok=True)

    schedule_files = sorted(KB_DIR.glob("schedule_*.json"))
    print(f"[INFO] Found {len(schedule_files)} schedule files")

    # (professor_normalized, semester) -> list of sections
    teaching_index: dict[tuple[str, str], list[dict]] = defaultdict(list)

    for sf in schedule_files:
        data = json.loads(sf.read_text())
        # Derive a clean semester label from filename (e.g., schedule_fall_2026 -> "Fall 2026")
        slug = sf.stem.replace("schedule_", "")
        parts = slug.split("_")
        semester_label = " ".join(p.capitalize() for p in parts) if parts else slug

        sections = parse_schedule(data["content"], semester_label)
        print(f"  [{sf.name}] parsed {len(sections)} sections")

        for s in sections:
            instructor = normalize_instructor(s["instructor_raw"])
            if not instructor:
                continue
            teaching_index[(instructor, semester_label)].append(s)

    # Write one doc per (professor, semester)
    written = 0
    for (instructor, semester), sections in sorted(teaching_index.items()):
        slug = slugify(instructor)
        sem_slug = slugify(semester)
        doc_id = f"teaching_{slug}_{sem_slug}"

        course_lines = []
        course_codes = set()
        for s in sections:
            course_codes.add(s["course_code"])
            course_lines.append(
                f"  - {s['course_code']} {s['course_name']} | "
                f"Section {s['section']} | {s['time']} | "
                f"Room: {s['room']} | {s['credits']} cr | {s['format']}"
            )

        content = (
            f"{instructor} - Teaching Schedule for {semester}\n\n"
            f"Courses:\n" + "\n".join(course_lines)
        )

        doc = {
            "doc_id": doc_id,
            "title": f"Teaching: {instructor} ({semester})",
            "category": "academic",
            "subcategory": "teaching_schedule",
            "instructor_name": instructor,
            "semester": semester,
            "course_codes": sorted(course_codes),
            "section_count": len(sections),
            "content": content,
            "source_file": "schedule_*.txt",
        }

        (OUT_DIR / f"{doc_id}.json").write_text(json.dumps(doc, indent=2))
        written += 1

    print(f"\n[RESULT] Wrote {written} (professor x semester) teaching docs to {OUT_DIR}")
    print(f"[STATS]  Unique professors: {len(set(k[0] for k in teaching_index))}")
    print(f"[STATS]  Semesters covered: {len(set(k[1] for k in teaching_index))}")


if __name__ == "__main__":
    main()
