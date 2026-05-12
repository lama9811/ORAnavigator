"""
Build v2 course docs from catalog-extracted course data.
Replaces _generated_courses/ with richer descriptions, prereqs, and terms_offered.
"""

import json
import re
from pathlib import Path

ROOT = Path(__file__).parent.parent
DATA = ROOT / "scripts" / "course_data.json"
OUT_DIR = ROOT / "backend" / "kb_structured" / "_generated_courses_v2"
OUT_DIR.mkdir(exist_ok=True)


def slugify_code(code: str) -> str:
    return code.replace(" ", "_").lower()


def extract_prereq_codes(prereq_text: str) -> list[str]:
    return re.findall(r"(?:COSC|CLCO|MATH|PHYS|ENGL|EEGR|MGBU|INSS) ?\d+", prereq_text)


def main():
    courses = json.loads(DATA.read_text())
    written = 0

    for c in courses:
        code = c["code"]
        slug = slugify_code(code)
        doc_id = f"course_{slug}"

        prereq_codes = extract_prereq_codes(c.get("prerequisites", ""))
        # Normalize codes (e.g., "COSC241" -> "COSC 241")
        prereq_codes = [re.sub(r"([A-Z]+)(\d+)", r"\1 \2", x) for x in prereq_codes]

        content_lines = [
            f"{code} - {c['title']}",
            f"Credits: {c['credits']}",
        ]
        if c.get("prerequisites"):
            content_lines.append(f"Prerequisites: {c['prerequisites']}")
        if c.get("terms_offered"):
            content_lines.append(f"Offered: {c['terms_offered']}")
        if c.get("description"):
            content_lines.append(f"\nDescription: {c['description']}")
        catalog_url = f"https://catalog.morgan.edu/preview_course_nopop.php?catoid=26&coid={c['coid']}"
        content_lines.append(f"\nCatalog page: {catalog_url}")

        doc = {
            "doc_id": doc_id,
            "title": f"Course: {code} - {c['title']}",
            "category": "academic",
            "subcategory": "course_detail",
            "course_code": code,
            "course_name": c["title"],
            "credits": c["credits"],
            "description": c.get("description", ""),
            "prerequisites_text": c.get("prerequisites", ""),
            "prerequisite_codes": prereq_codes,
            "terms_offered": c.get("terms_offered", ""),
            "catalog_url": catalog_url,
            "content": "\n".join(content_lines),
            "source_file": f"catalog.morgan.edu (coid={c['coid']}, accessed 2026-05-06)",
        }

        out = OUT_DIR / f"{doc_id}.json"
        out.write_text(json.dumps(doc, indent=2))
        written += 1
        print(f"  [OK] {doc_id:25s} | {code} | {c['title']} | {c['credits']} cr")

    print(f"\n[RESULT] Wrote {written} v2 course docs to {OUT_DIR}")


if __name__ == "__main__":
    main()
