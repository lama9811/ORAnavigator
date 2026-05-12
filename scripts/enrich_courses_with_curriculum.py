"""
Enrich every v2 course doc with its role in the BS CS and BS Cloud Computing
curricula (core, supporting, Group A/B/C/D elective).

Source: catalog.morgan.edu degree program pages (verified 2026-05-06).
"""

import json
from pathlib import Path

V2_DIR = Path(__file__).parent.parent / "backend" / "kb_structured" / "_generated_courses_v2"

# BS Computer Science curriculum mapping (catoid 26, poid 5968)
BS_CS = {
    "core": ["COSC 111", "COSC 112", "COSC 220", "COSC 241", "COSC 281", "COSC 349",
             "COSC 351", "COSC 352", "COSC 354", "COSC 458", "COSC 459", "COSC 490"],
    "supporting": ["MATH 241", "MATH 242", "MATH 312", "MATH 331", "COSC 201"],
    "group_a": ["COSC 238", "COSC 239", "COSC 243", "COSC 251", "CLCO 261"],
    "group_b": ["COSC 320", "COSC 323", "COSC 332", "COSC 338", "COSC 383",
                "COSC 385", "COSC 386", "MATH 313", "EEGR 317"],
    "group_c": ["COSC 470", "COSC 472", "COSC 460", "COSC 480", "COSC 486",
                "COSC 491", "COSC 498", "COSC 499", "CLCO 471"],
    "group_d": ["INSS 391", "INSS 494", "EEGR 481", "EEGR 483"],
}

# BS Cloud Computing curriculum mapping (catoid 26, poid 6172)
BS_CLOUD = {
    "core": ["COSC 111", "COSC 112", "COSC 220", "COSC 241", "COSC 349", "COSC 351",
             "COSC 354", "CLCO 261", "CLCO 401", "CLCO 490"],
    "supporting": ["MATH 113", "MATH 114", "MATH 241", "MATH 312", "MGBU 200"],
    "group_a": ["COSC 238", "COSC 239", "COSC 243", "COSC 251", "COSC 281", "MATH 242"],
    "group_b": ["COSC 320", "COSC 323", "COSC 332", "COSC 358", "COSC 383",
                "COSC 385", "COSC 386", "MATH 313", "MATH 331", "EEGR 317"],
    "group_c": ["COSC 458", "COSC 459", "COSC 470", "COSC 472", "COSC 486",
                "COSC 491", "COSC 498", "COSC 499", "CLCO 471"],
    "group_d": ["MATH 345", "MATH 346", "MATH 361", "MGBU 326", "INSS 391",
                "INSS 494", "EEGR 481", "EEGR 483"],
}

# CS Minor required courses (catoid 26, poid 6078)
CS_MINOR = ["COSC 111", "COSC 112", "COSC 220", "COSC 241", "COSC 243"]


def role_in(curriculum: dict, code: str) -> str:
    for role, codes in curriculum.items():
        if code in codes:
            return role
    return ""


def main():
    updated = 0

    for path in sorted(V2_DIR.glob("*.json")):
        doc = json.loads(path.read_text())
        code = doc["course_code"]

        bs_cs_role = role_in(BS_CS, code)
        bs_cloud_role = role_in(BS_CLOUD, code)
        in_minor = code in CS_MINOR

        # Add structured fields
        doc["bs_cs_role"] = bs_cs_role  # "core", "supporting", "group_a"..."group_d", or ""
        doc["bs_cloud_role"] = bs_cloud_role
        doc["in_cs_minor"] = in_minor

        # Build a curriculum line for the content text
        roles_lines = []
        if bs_cs_role:
            label = bs_cs_role.replace("group_", "Group ").upper() if "group" in bs_cs_role else bs_cs_role.title()
            label = label.replace("GROUP ", "Group ")
            roles_lines.append(f"BS Computer Science: {label}")
        if bs_cloud_role:
            label = bs_cloud_role.replace("group_", "Group ").upper() if "group" in bs_cloud_role else bs_cloud_role.title()
            label = label.replace("GROUP ", "Group ")
            roles_lines.append(f"BS Cloud Computing: {label}")
        if in_minor:
            roles_lines.append(f"Required for CS Minor")

        # Append curriculum info to content if applicable
        if roles_lines:
            curriculum_block = "\n\nCurriculum role:\n  - " + "\n  - ".join(roles_lines)
            # If we already added one, replace it; otherwise append
            if "\n\nCurriculum role:" in doc["content"]:
                # split off old block, replace
                base = doc["content"].split("\n\nCurriculum role:")[0]
                # also drop trailing "Catalog page" line and re-append
                catalog_line = ""
                for line in doc["content"].splitlines():
                    if line.startswith("Catalog page:"):
                        catalog_line = "\n\n" + line
                        break
                doc["content"] = base + curriculum_block + catalog_line
            else:
                # insert before the Catalog page line if present
                if "\n\nCatalog page:" in doc["content"]:
                    parts = doc["content"].split("\n\nCatalog page:")
                    doc["content"] = parts[0] + curriculum_block + "\n\nCatalog page:" + parts[1]
                else:
                    doc["content"] = doc["content"] + curriculum_block

        path.write_text(json.dumps(doc, indent=2))
        updated += 1
        marker = []
        if bs_cs_role: marker.append(f"CS:{bs_cs_role}")
        if bs_cloud_role: marker.append(f"Cloud:{bs_cloud_role}")
        if in_minor: marker.append("Minor")
        marker_str = " | ".join(marker) if marker else "(no curriculum role)"
        print(f"  [{code:9s}] {marker_str}")

    print(f"\n[RESULT] Enriched {updated} course docs with BS CS / BS Cloud / Minor curriculum mappings")


if __name__ == "__main__":
    main()
