"""
Refresh faculty docs using authoritative data scraped from
morgan.edu/computer-science/faculty-and-staff (May 2026).

Merges:
  - Official contacts (name, title, office, phone, email, profile_link) from dept website
  - Existing research_summary / research_keywords from old _generated_faculty docs

Writes new versions to _generated_faculty_v2/ and prepares them for KB upload.
"""

import json
import re
from pathlib import Path

KB_DIR = Path(__file__).parent.parent / "backend" / "kb_structured"
OLD_DIR = KB_DIR / "_generated_faculty"
OUT_DIR = KB_DIR / "_generated_faculty_v2"

# Sourced from morgan.edu/computer-science/faculty-and-staff (verified 2026-05-06).
DEPT_WEBSITE_FACULTY = [
    {"name": "Shuangbao 'Paul' Wang", "title": "Professor and Chair", "office": "McMechen Hall 507", "phone": "(443) 885-4503", "email": "shuangbao.wang@morgan.edu", "profile_link": "https://www.morgan.edu/computer-science/faculty-and-staff/shuangbao-wang"},
    {"name": "Md Rahman", "title": "Professor, Associate Chair, Director of PhD Advanced Computing", "office": "McMechen 629", "phone": "(443) 885-1056", "email": "Md.Rahman@morgan.edu", "profile_link": "https://www.morgan.edu/computer-science/faculty-and-staff/md-rahman"},
    {"name": "Radhouane Chouchane (Radwan Shushane)", "title": "Associate Professor, Director of Undergraduate Studies", "office": "McMechen Hall 624", "phone": "(443) 885-3745", "email": "Radwan.Shushane@morgan.edu", "profile_link": "https://www.morgan.edu/computer-science/faculty-and-staff/radhouane-chouchane"},
    {"name": "Amjad Ali", "title": "Professor", "office": "McMechen Hall 502", "phone": None, "email": "amjad.ali@morgan.edu", "profile_link": "https://www.morgan.edu/computer-science/faculty-and-staff/amjad-ali"},
    {"name": "Monireh Dabaghchian", "title": "Associate Professor", "office": "McMechen Hall 508", "phone": "(443) 885-2348", "email": "Monireh.Dabaghchian@morgan.edu", "profile_link": "https://www.morgan.edu/computer-science/faculty-and-staff/monireh-dabaghchian"},
    {"name": "Jamell Dacon", "title": "Assistant Professor", "office": "McMechen Hall 625", "phone": None, "email": "jamell.dacon@morgan.edu", "profile_link": "https://www.morgan.edu/computer-science/faculty-and-staff/jamell-dacon"},
    {"name": "Jin Guo", "title": "Professor of Practice", "office": "McMechen Hall 502", "phone": None, "email": "jin.guo@morgan.edu", "profile_link": "https://www.morgan.edu/computer-science/faculty-and-staff/jin-guo"},
    {"name": "Vahid Heydari", "title": "Associate Professor", "office": "McMechen Hall 619", "phone": None, "email": "vahid.heydari@morgan.edu", "profile_link": "https://www.morgan.edu/computer-science/faculty-and-staff/vahid-heydari"},
    {"name": "Naja Mack", "title": "Assistant Professor", "office": "McMechen Hall 623; Lab: McMechen Hall 616", "phone": "(443) 885-2402", "email": "Naja.Mack@morgan.edu", "profile_link": "https://www.morgan.edu/computer-science/faculty-and-staff/naja-mack"},
    {"name": "Jianzhou Mao", "title": "Research Assistant Professor", "office": "McMechen 627", "phone": None, "email": "jianzhou.mao@morgan.edu", "profile_link": "https://www.morgan.edu/computer-science/faculty-and-staff/jianzhou-mao"},
    {"name": "Blessing Ojeme", "title": "Assistant Professor", "office": "McMechen Hall 621", "phone": None, "email": "blessing.ojeme@morgan.edu", "profile_link": "https://www.morgan.edu/computer-science/faculty-and-staff/blessing-ojeme"},
    {"name": "Roshan Paudel", "title": "Coordinator of the MS in Bioinformatics Program, Professor of Practice", "office": "McMechen Hall 507D", "phone": "(443) 885-3096", "email": "Roshan.Paudel@morgan.edu", "profile_link": "https://www.morgan.edu/computer-science/faculty-and-staff/roshan-paudel"},
    {"name": "Eric Sakk", "title": "Associate Professor", "office": "McMechen Hall 507F", "phone": "(443) 885-3270", "email": "Eric.Sakk@morgan.edu", "profile_link": "https://www.morgan.edu/computer-science/faculty-and-staff/eric-sakk"},
    {"name": "Vojislav Stojkovic", "title": "Associate Professor", "office": "McMechen Hall 507E", "phone": "(443) 885-1054", "email": "Vojislav.Stojkovic@morgan.edu", "profile_link": "https://www.morgan.edu/computer-science/faculty-and-staff/vojislav-stojkovic"},
    {"name": "Timothy Oladunni", "title": "Assistant Professor", "office": "McMechen Hall 617", "phone": None, "email": "Timothy.Oladunni@morgan.edu", "profile_link": "https://www.morgan.edu/computer-science/faculty-and-staff/timothy-oladunni"},
    {"name": "Guobin Xu", "title": "Associate Professor, Director of MS Advanced Computing", "office": "McMechen Hall 615", "phone": None, "email": "guobin.xu@morgan.edu", "profile_link": "https://www.morgan.edu/computer-science/faculty-and-staff/guobin-xu"},
    {"name": "Grace Steele", "title": "Lecturer", "office": "McMechen Hall 507C", "phone": "(443) 885-1053", "email": "Grace.Steele@morgan.edu", "profile_link": "https://www.morgan.edu/computer-science/faculty-and-staff/grace-steele"},
    {"name": "Sam Tannouri", "title": "Lecturer", "office": "McMechen Hall 628", "phone": "(443) 885-1055", "email": "Sam.Tannouri@morgan.edu", "profile_link": "https://www.morgan.edu/computer-science/faculty-and-staff/sam-tannouri"},
    {"name": "Rahmel Bailey", "title": "Engineer in Residence", "office": "McMechen 618", "phone": None, "email": "rahmel.bailey@codepath.org", "profile_link": "https://www.morgan.edu/computer-science/faculty-and-staff/rahmel-bailey"},
    {"name": "Wendy Smith", "title": "Administrative Assistant", "office": "McMechen Hall 507A", "phone": "(443) 885-3962", "email": "Wendy.Smith@morgan.edu", "profile_link": "https://www.morgan.edu/computer-science/faculty-and-staff/wendy-smith"},
]


def slugify(name: str) -> str:
    cleaned = re.sub(r"[\"'()]", "", name).replace("Dr.", "").strip()
    # Use only first part (drop alt-name parens)
    cleaned = cleaned.split(" - ")[0]
    return re.sub(r"[^a-zA-Z0-9]+", "_", cleaned).strip("_").lower()


def load_old_research_data() -> dict[str, dict]:
    """Load research_summary + keywords from old faculty docs, keyed by surname slug."""
    if not OLD_DIR.exists():
        return {}
    research = {}
    for f in OLD_DIR.glob("*.json"):
        d = json.loads(f.read_text())
        slug = f.stem.replace("faculty_", "")
        research[slug] = {
            "research_summary": d.get("research_summary", ""),
            "research_keywords": d.get("research_keywords", []),
        }
    return research


def best_match(new_name: str, old_research: dict) -> dict:
    """Match dept-website name to old slug. Try last word, last 2 words, or alt-name."""
    parts = new_name.replace("'", "").replace('"', "").split()
    candidates = []
    if len(parts) >= 2:
        candidates.append(f"{parts[-2]}_{parts[-1]}".lower())
        candidates.append(parts[-1].lower())
    if "(" in new_name:
        m = re.search(r"\(([^)]+)\)", new_name)
        if m:
            alt = m.group(1).split()
            if len(alt) >= 2:
                candidates.append(f"{alt[-2]}_{alt[-1]}".lower())

    for c in candidates:
        if c in old_research:
            return old_research[c]
    return {"research_summary": "", "research_keywords": []}


def main():
    OUT_DIR.mkdir(exist_ok=True)
    old_research = load_old_research_data()
    written = 0

    for f in DEPT_WEBSITE_FACULTY:
        slug = slugify(f["name"])
        doc_id = f"faculty_{slug}"
        match = best_match(f["name"], old_research)

        content_lines = [
            f"{f['name']} - {f['title']}",
            f"Department: Computer Science, Morgan State University",
            f"Office: {f['office']}",
        ]
        if f.get("phone"):
            content_lines.append(f"Phone: {f['phone']}")
        if f.get("email"):
            content_lines.append(f"Email: {f['email']}")
        if f.get("profile_link"):
            content_lines.append(f"Faculty profile: {f['profile_link']}")
        if match["research_summary"]:
            content_lines.append(f"Research interests: {match['research_summary']}")

        doc = {
            "doc_id": doc_id,
            "title": f"Faculty: {f['name']}",
            "category": "academic",
            "subcategory": "faculty_profile",
            "faculty_name": f["name"],
            "faculty_title": f["title"],
            "office": f["office"],
            "phone": f["phone"] or "",
            "email": f["email"],
            "profile_link": f["profile_link"],
            "research_summary": match["research_summary"],
            "research_keywords": match["research_keywords"],
            "content": "\n".join(content_lines),
            "source_file": "morgan.edu/computer-science/faculty-and-staff (2026-05-06)",
        }

        out_path = OUT_DIR / f"{doc_id}.json"
        out_path.write_text(json.dumps(doc, indent=2))
        written += 1
        print(f"  [OK] {doc_id:35s} | {f['title']}")

    print(f"\n[RESULT] Wrote {written} fresh faculty docs to {OUT_DIR}")
    print("[NEXT]   Run scripts/setup_kb_datastore.py to upload them.")


if __name__ == "__main__":
    main()
