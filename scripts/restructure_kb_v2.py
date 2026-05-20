"""
Restructure KB v2 — nav-faithful layout that mirrors morgan.edu/ora.

Every doc lands at a folder path derived from its source_url. Folders are named
exactly as morgan.edu's URL path segments (hyphens → underscores). No
"_generated_" prefix, no synthetic subcategories.

Examples:
  /ora                                            → backend/kb_structured/  (root)
  /ora/about                                      → about/
  /ora/about/staff-directory                      → about/staff_directory/
  /ora/pre-award                                  → pre_award/
  /ora/pre-award/budget-development               → pre_award/budget_development/
  /ora/research-compliance/animal-research/iacuc-sops → research_compliance/animal_research/iacuc_sops/
  /ora/funding-sources (hub for 15 docs)          → funding_sources/<subcat-derived-folder>/

Hub pages where many docs share one source_url get subcategory-derived
subfolders so the agent can still drill in. Configured per-hub below.

Run:
    python scripts/restructure_kb_v2.py --dry-run
    python scripts/restructure_kb_v2.py
    python scripts/restructure_kb_v2.py --manifest-only
"""

from __future__ import annotations

import argparse
import collections
import json
import re
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
KB_DIR = REPO / "backend" / "kb_structured"
ADK_MANIFEST_DEST = REPO / "adk_agent" / "ora_navigator_unified" / "_kb_manifest.json"
NAV_TREE_PATH = REPO / "scripts" / "morgan_ora_nav_tree.json"


# ---------------------------------------------------------------------------
# Hub splits — for URLs that gather many docs at one URL
# ---------------------------------------------------------------------------
# These let the agent enumerate (e.g. "list federal funding sources") even
# though morgan.edu collapses them all onto one page.

def split_funding_sources(doc: dict) -> str | None:
    """Return subfolder name under funding_sources/, or None to keep at root."""
    sub = doc.get("subcategory", "")
    if sub == "overview":
        return None
    if sub == "external_database":
        return "external_databases"
    if sub == "private_foundation":
        return "private_foundations"
    if sub == "state_maryland":
        return "state_of_maryland"
    if sub.startswith("federal_category_"):
        return "federal"
    return None


def split_policies_and_guidelines(doc: dict) -> str | None:
    sub = doc.get("subcategory", "")
    if sub == "overview":
        return None
    if sub == "policy":
        return "numbered_policies"
    # PI Handbook 5 (compliance chapter) routes here too
    if doc.get("doc_id", "").startswith("form_pi_handbook"):
        return "pi_handbooks"
    return None


def split_ora_announcements(doc: dict) -> str | None:
    sub = doc.get("subcategory", "")
    if sub == "overview":
        return None
    # Individual announcements — leave flat; they're sparse (~3 docs)
    return None


def split_resources(doc: dict) -> str | None:
    """resources/ has explicit children (handbooks/templates) in morgan.edu nav.
    Most docs already route by URL into those folders. Edge case: resources_overview
    and the templates index live at the root."""
    return None


HUB_SPLITS = {
    "funding_sources":         split_funding_sources,
    "policies_and_guidelines": split_policies_and_guidelines,
    "ora_announcements":       split_ora_announcements,
    "resources":               split_resources,
}


# ---------------------------------------------------------------------------
# URL → folder path
# ---------------------------------------------------------------------------
URL_FIXES = {
    # COI hub uses a Drupal-quirk URL on morgan.edu. Normalize to "conflict_of_interest".
    "conflict-of-interest-x12058": "conflict-of-interest",
}


def url_to_folder(source_url: str) -> str:
    """Map a morgan.edu URL to a relative KB folder path (no trailing slash)."""
    path = re.sub(r"^https?://(www\.)?morgan\.edu", "", source_url or "").strip()
    # Handle /ora alias
    path = re.sub(r"^/ora(/|$)", "/office-of-research-administration\\1", path)
    # Strip the /office-of-research-administration/ prefix
    path = re.sub(r"^/office-of-research-administration/?", "", path).strip("/")
    if not path:
        return ""  # root (the ORA landing page)

    segs = path.split("/")
    fixed = [URL_FIXES.get(s, s) for s in segs]
    # Sanitize: convert hyphens, strip parentheses
    out_segs = []
    for s in fixed:
        s = s.replace("-", "_")
        s = re.sub(r"[()]", "", s)
        s = re.sub(r"_+", "_", s).strip("_")
        if s:
            out_segs.append(s)
    return "/".join(out_segs)


def derive_top_level(folder: str) -> str:
    """Top-level slug for category rewriting (e.g. "pre_award", "research_compliance")."""
    return folder.split("/", 1)[0] if folder else ""


# ---------------------------------------------------------------------------
# Migration core
# ---------------------------------------------------------------------------
def plan_moves(kb_dir: Path) -> tuple[list[dict], list[dict]]:
    moves: list[dict] = []
    unmatched: list[dict] = []

    for src in sorted(kb_dir.rglob("*.json")):
        if src.name.startswith("_"):
            continue
        try:
            data = json.loads(src.read_text())
        except Exception as e:
            unmatched.append({"path": str(src.relative_to(kb_dir)), "reason": f"parse error: {e}"})
            continue

        url = data.get("source_url", "")
        folder = url_to_folder(url)

        if not folder:
            # Docs with URL pointing to the /ora root (e.g. function-to-staff routing).
            # Route to about/staff_directory/ since they're staff/service references.
            if data.get("category") == "service_areas" or "routing" in (data.get("doc_id") or ""):
                folder = "about/staff_directory"
            else:
                unmatched.append({
                    "path": str(src.relative_to(kb_dir)),
                    "doc_id": data.get("doc_id"),
                    "reason": f"could not derive folder from url={url!r}",
                })
                continue

        # Hub split: if folder is a known hub, maybe push into a subfolder
        top = derive_top_level(folder)
        if top in HUB_SPLITS:
            sub = HUB_SPLITS[top](data)
            if sub:
                folder = f"{folder}/{sub}" if folder else top + "/" + sub

        dst = kb_dir / folder / src.name
        moves.append({
            "src": src, "dst": dst, "folder": folder, "data": data,
            "doc_id": data.get("doc_id") or src.stem, "top": top,
        })

    return moves, unmatched


def derive_category(folder: str) -> str:
    """Top-level slug becomes the new category field."""
    return derive_top_level(folder) or "general"


def execute_moves(moves: list[dict], dry_run: bool) -> None:
    for m in moves:
        src: Path = m["src"]
        dst: Path = m["dst"]
        data = m["data"]
        folder = m["folder"]

        new_cat = derive_category(folder)
        original_cat = data.get("category")
        rewrote = False
        if original_cat != new_cat:
            data.setdefault("legacy_category", original_cat)
            data["category"] = new_cat
            rewrote = True

        if src.resolve() == dst.resolve():
            if rewrote and not dry_run:
                src.write_text(json.dumps(data, indent=2, ensure_ascii=False))
            continue

        if dry_run:
            note = f"  [category: {original_cat!r} -> {new_cat!r}]" if rewrote else ""
            print(f"  MOVE {src.relative_to(KB_DIR)} -> {dst.relative_to(KB_DIR)}{note}")
            continue

        dst.parent.mkdir(parents=True, exist_ok=True)
        if rewrote:
            src.write_text(json.dumps(data, indent=2, ensure_ascii=False))
        shutil.move(str(src), str(dst))


def cleanup_empty_dirs(kb_dir: Path) -> int:
    removed = 0
    # Remove deepest first
    all_dirs = sorted([p for p in kb_dir.rglob("*") if p.is_dir()], key=lambda p: -len(p.parts))
    for d in all_dirs:
        try:
            if not any(d.iterdir()):
                d.rmdir()
                removed += 1
        except OSError:
            pass
    return removed


# ---------------------------------------------------------------------------
# Manifest builder (recursive tree)
# ---------------------------------------------------------------------------
TITLE_OVERRIDES = {
    # folder_path: display title
    "about":                                              "About ORA",
    "about/mission_and_vision":                           "Mission & Vision",
    "about/history":                                      "History",
    "about/staff_directory":                              "Staff Directory",
    "pre_award":                                          "Pre-Award",
    "pre_award/university_application_information":       "University Application Information",
    "pre_award/role_of_principal_investigator":           "Role of Principal Investigator",
    "pre_award/proposal_components":                      "Proposal Components",
    "pre_award/proposal_submission_checklist":            "Proposal Submission Checklist",
    "pre_award/internal_routing_form":                    "Internal Routing Form",
    "pre_award/budget_development":                       "Budget Development",
    "pre_award/fringe_benefit_rate":                      "Fringe Benefit Rate",
    "pre_award/fanda_cost_rates":                         "F&A Cost Rates",
    "pre_award/proposal_and_budget_examples":             "Proposal and Budget Examples",
    "pre_award/pre_award_spending":                       "Pre-Award Spending",
    "pre_award/pre_award_subawards":                      "Pre-Award Subawards",
    "pre_award/limited_submission":                       "Limited Submission",
    "post_award":                                         "Post-Award",
    "post_award/notification_and_setup_of_award":         "Notification and Setup of Award",
    "post_award/changes_to_an_award":                     "Changes to an Award",
    "post_award/post_award_subawards":                    "Post-Award Subawards",
    "post_award/reporting":                               "Reporting",
    "post_award/forms":                                   "Post-Award Forms",
    "policies_and_guidelines":                            "Policies and Guidelines",
    "policies_and_guidelines/numbered_policies":          "Numbered ORA Policies",
    "policies_and_guidelines/pi_handbooks":               "PI Handbook (Compliance chapter)",
    "research_compliance":                                "Research Compliance",
    "research_compliance/human_subjects_research":        "Human Subjects Research (IRB)",
    "research_compliance/animal_research":                "Animal Research (IACUC)",
    "research_compliance/animal_research/animal_housing_capacity": "Animal Housing Capacity",
    "research_compliance/animal_research/available_equipment":     "Available Equipment",
    "research_compliance/animal_research/iacuc_sops":              "IACUC SOPs",
    "research_compliance/animal_research/iacuc_forms":             "IACUC Forms",
    "research_compliance/animal_research/training_and_consultation":"Training and Consultation",
    "research_compliance/conflict_of_interest":                    "Conflict of Interest (COI)",
    "research_compliance/conflict_of_interest/conflict_of_interest_for_sponsored_research": "COI for Sponsored Research",
    "research_compliance/responsible_conduct_of_research":         "Responsible Conduct of Research (RCR)",
    "research_compliance/research_misconduct":                     "Research Misconduct",
    "research_compliance/research_security":                       "Research Security",
    "research_compliance/research_security/information_technology_resources":           "Information Technology Resources",
    "research_compliance/research_security/research_compliance_and_security_training":  "Research Compliance and Security Training",
    "research_compliance/research_security/technology_control_plan_tcp":                "Technology Control Plan (TCP)",
    "research_compliance/research_security/research_security_program_committee":        "Research Security Program Committee",
    "research_compliance/research_security/nspm_33_overview":      "NSPM-33 Overview",
    "research_compliance/state_of_maryland_ethics_and_financial_disclosure": "State of Maryland Ethics and Financial Disclosure",
    "research_compliance/diversity_and_eeo":                       "Diversity and EEO",
    "research_compliance/drug_alcohol_and_tobacco_policies":       "Drug, Alcohol, and Tobacco Policies",
    "research_compliance/research_compliance_updates_and_news":    "Research Compliance Updates & News",
    "trainings":                                                   "Trainings",
    "trainings/e_training":                                        "e-Training",
    "trainings/new_faculty_development_seminars":                  "New Faculty Development Seminars",
    "trainings/monthly_d_red_seminars":                            "Monthly D-RED Seminars",
    "trainings/special_workshops":                                 "Special Workshops",
    "trainings/test_prep":                                         "Test Prep (RACC)",
    "trainings/msu_trainings_outside_ora":                         "MSU Trainings Outside ORA",
    "resources":                                                   "Resources",
    "resources/principal_investigator_handbooks":                  "Principal Investigator Handbooks",
    "resources/templates":                                         "Templates",
    "funding_sources":                                             "Funding Sources",
    "funding_sources/external_databases":                          "External Funding Databases",
    "funding_sources/private_foundations":                         "Private Foundations",
    "funding_sources/state_of_maryland":                           "State of Maryland",
    "funding_sources/federal":                                     "Federal Funding (by topic)",
    "ora_announcements":                                           "ORA Announcements",
}


def title_for(folder: str) -> str:
    return TITLE_OVERRIDES.get(
        folder,
        folder.split("/")[-1].replace("_", " ").title() if folder else "ORA Knowledge Base"
    )


def build_manifest(kb_dir: Path) -> dict:
    """Build a recursive tree manifest. Folders become nodes; docs hang off
    the folder whose path matches their location."""
    # Collect all docs grouped by their folder
    docs_by_folder: dict[str, list[dict]] = collections.defaultdict(list)
    for src in sorted(kb_dir.rglob("*.json")):
        if src.name.startswith("_"):
            continue
        rel = src.relative_to(kb_dir)
        folder = "/".join(rel.parts[:-1])  # may be ""
        data = json.loads(src.read_text())
        docs_by_folder[folder].append({
            "doc_id": data.get("doc_id") or src.stem,
            "title": data.get("title", ""),
            "source_url": data.get("source_url", ""),
            "procedure_url": data.get("procedure_url", ""),
            "file_path": str(rel),
            "playwright_verified": bool(data.get("playwright_verified", False)),
        })

    # Collect every folder that contains docs OR is an ancestor of one
    all_folders: set[str] = set()
    for f in docs_by_folder.keys():
        parts = f.split("/") if f else []
        for i in range(len(parts) + 1):
            ancestor = "/".join(parts[:i])
            all_folders.add(ancestor)

    # Build tree as nested dict, then materialize
    def total_docs(folder: str) -> int:
        n = len(docs_by_folder.get(folder, []))
        if folder == "":
            # Root: sum every folder
            for other, docs in docs_by_folder.items():
                if other != "":
                    n += len(docs)
            return n
        prefix = folder + "/"
        for other, docs in docs_by_folder.items():
            if other != folder and other.startswith(prefix):
                n += len(docs)
        return n

    def direct_children(folder: str) -> list[str]:
        prefix = folder + "/" if folder else ""
        kids: set[str] = set()
        for other in all_folders:
            if not other:
                continue
            if folder == "" and "/" not in other:
                kids.add(other)
            elif other.startswith(prefix) and "/" not in other[len(prefix):]:
                kids.add(other)
        return sorted(kids)

    def build_node(folder: str) -> dict:
        docs = sorted(docs_by_folder.get(folder, []), key=lambda d: (d["title"], d["doc_id"]))
        children = [build_node(c) for c in direct_children(folder)]
        return {
            "slug": folder.split("/")[-1] if folder else "",
            "path": folder,
            "title": title_for(folder),
            "doc_count": total_docs(folder),
            "direct_doc_count": len(docs),
            "docs": docs,
            "children": children,
        }

    root = build_node("")
    return {
        "version": 2,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "kb_dir": "backend/kb_structured",
        "total_docs": root["doc_count"],
        "tree": root["children"],   # skip the empty root, start at top-level sections
    }


def regenerate_all_documents_jsonl(kb_dir: Path) -> int:
    lines: list[str] = []
    for src in sorted(kb_dir.rglob("*.json")):
        if src.name.startswith("_"):
            continue
        data = json.loads(src.read_text())
        rel = src.relative_to(kb_dir)
        lines.append(json.dumps({
            "doc_id": data.get("doc_id") or src.stem,
            "title": data.get("title", ""),
            "category": data.get("category", ""),
            "subcategory": data.get("subcategory", ""),
            "display_label": data.get("display_label", ""),
            "source_url": data.get("source_url", ""),
            "procedure_url": data.get("procedure_url", ""),
            "playwright_verified": bool(data.get("playwright_verified", False)),
            "file_path": str(rel),
        }, ensure_ascii=False))
    (kb_dir / "_all_documents.jsonl").write_text("\n".join(lines) + "\n")
    return len(lines)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--manifest-only", action="store_true")
    args = ap.parse_args()

    if not KB_DIR.exists():
        print(f"[FATAL] {KB_DIR} not found")
        return 1

    if not args.manifest_only:
        moves, unmatched = plan_moves(KB_DIR)
        print(f"[PLAN] {len(moves)} files to process, {len(unmatched)} unmatched")

        by_folder = collections.Counter(m["folder"] for m in moves)
        print("[PLAN] Destinations (top 30 by count):")
        for f, n in sorted(by_folder.items()):
            print(f"  {n:4d}  {f}")

        if unmatched:
            print()
            print("[UNMATCHED]")
            for u in unmatched[:20]:
                print(f"  {u}")
            return 2

        if args.dry_run:
            print()
            print("[DRY-RUN] (first 30 moves)")
            execute_moves(moves[:30], dry_run=True)
            return 0

        print()
        print("[EXECUTE] Moving files...")
        execute_moves(moves, dry_run=False)
        removed = cleanup_empty_dirs(KB_DIR)
        print(f"[CLEANUP] Removed {removed} empty directories")

    print()
    print("[INDEX] Regenerating _all_documents.jsonl...")
    n = regenerate_all_documents_jsonl(KB_DIR)
    print(f"[INDEX] Wrote {n} lines")

    print("[INDEX] Building _manifest.json (recursive tree)...")
    manifest = build_manifest(KB_DIR)
    canonical = KB_DIR / "_manifest.json"
    canonical.write_text(json.dumps(manifest, indent=2, ensure_ascii=False))
    ADK_MANIFEST_DEST.parent.mkdir(parents=True, exist_ok=True)
    ADK_MANIFEST_DEST.write_text(json.dumps(manifest, indent=2, ensure_ascii=False))
    print(f"[INDEX] Wrote {canonical}")
    print(f"[INDEX] Wrote {ADK_MANIFEST_DEST}")
    print(f"[INDEX] total_docs = {manifest['total_docs']}")
    for node in manifest["tree"]:
        kids = len(node["children"])
        print(f"  {node['doc_count']:4d}  {node['path']:30}  ({kids} sub-pages)")

    return 0


if __name__ == "__main__":
    sys.exit(main())
