"""
Split academic_faculty.json into one doc per faculty member.
Creates individual KB files like faculty_paul_wang.json with structured fields.
This dramatically improves retrieval precision (one doc per query target).
"""

import json
import re
from pathlib import Path

KB_DIR = Path(__file__).parent.parent / "backend" / "kb_structured"
OUT_DIR = KB_DIR / "_generated_faculty"
SOURCE_FILE = KB_DIR / "academic_faculty.json"


def parse_faculty(content: str) -> list[dict]:
    """Parse the faculty blob into individual records."""
    chunks = re.split(r"\n(?=Dr\. )", content)
    faculty = []

    for chunk in chunks:
        chunk = chunk.strip()
        if not chunk.startswith("Dr."):
            continue

        record = {"raw": chunk}

        # Name + title (first line)
        first_line = chunk.split("\n")[0].strip()
        name_match = re.match(r"(Dr\. [^-(]+?)(?:\s*\(([^)]+)\))?\s*-\s*(.+)", first_line)
        if not name_match:
            continue
        record["name"] = name_match.group(1).strip()
        record["alt_name"] = (name_match.group(2) or "").strip()
        record["title"] = name_match.group(3).strip()

        # Pull labeled fields
        for field in ["Office", "Phone", "Email", "Research"]:
            m = re.search(rf"{field}:\s*(.+?)(?:\n[A-Z][a-z]+:|\Z)", chunk, re.DOTALL)
            if m:
                record[field.lower()] = m.group(1).strip().replace("\n", " ")

        faculty.append(record)

    return faculty


def slugify(name: str) -> str:
    """Convert 'Dr. Paul Wang' -> 'paul_wang'."""
    cleaned = name.replace("Dr.", "").strip()
    return re.sub(r"[^a-zA-Z0-9]+", "_", cleaned).strip("_").lower()


def build_doc(faculty: dict) -> dict:
    """Build a KB JSON doc for one faculty member."""
    name = faculty["name"]
    slug = slugify(name)
    doc_id = f"faculty_{slug}"

    content_parts = [f"{name} - {faculty['title']}"]
    if faculty.get("alt_name"):
        content_parts.append(f"Also known as: {faculty['alt_name']}")
    if faculty.get("office"):
        content_parts.append(f"Office: {faculty['office']}")
    if faculty.get("phone"):
        content_parts.append(f"Phone: {faculty['phone']}")
    if faculty.get("email"):
        content_parts.append(f"Email: {faculty['email']}")
    if faculty.get("research"):
        content_parts.append(f"Research interests: {faculty['research']}")

    research_keywords = []
    if faculty.get("research"):
        research_keywords = [
            kw.strip().lower()
            for kw in re.split(r"[,;.]", faculty["research"])
            if kw.strip() and len(kw.strip()) > 3
        ]

    return {
        "doc_id": doc_id,
        "title": f"Faculty: {name}",
        "category": "academic",
        "subcategory": "faculty_profile",
        "faculty_name": name,
        "faculty_title": faculty["title"],
        "office": faculty.get("office", ""),
        "phone": faculty.get("phone", ""),
        "email": faculty.get("email", ""),
        "research_summary": faculty.get("research", ""),
        "research_keywords": research_keywords,
        "content": "\n".join(content_parts),
        "source_file": "academic_faculty.txt",
    }


def main():
    OUT_DIR.mkdir(exist_ok=True)
    source = json.loads(SOURCE_FILE.read_text())
    faculty = parse_faculty(source["content"])

    print(f"[INFO] Parsed {len(faculty)} faculty from {SOURCE_FILE.name}")

    written = []
    for f in faculty:
        doc = build_doc(f)
        out_path = OUT_DIR / f"{doc['doc_id']}.json"
        out_path.write_text(json.dumps(doc, indent=2))
        written.append(doc["doc_id"])
        print(f"  [OK] {doc['doc_id']} ({f.get('email','no email')})")

    print(f"\n[RESULT] Wrote {len(written)} faculty docs to {OUT_DIR}")
    return written


if __name__ == "__main__":
    main()
