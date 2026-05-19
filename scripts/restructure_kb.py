"""
Restructure the ORA Navigator KB into a hierarchical tree.

Reads existing 382 JSON docs under backend/kb_structured/, routes each into a
topic-specific destination, rewrites the `category` field for forms (singular
"form" -> plural destination category), regenerates _all_documents.jsonl, and
emits a browseable _manifest.json (copied to adk_agent/ora_navigator_unified/
so the ADK container picks it up without a Dockerfile change).

Run:
    python scripts/restructure_kb.py --dry-run    # preview moves
    python scripts/restructure_kb.py              # execute
    python scripts/restructure_kb.py --manifest-only  # rebuild manifest from current disk
"""

from __future__ import annotations

import argparse
import collections
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
KB_DIR = REPO / "backend" / "kb_structured"
ADK_MANIFEST_DEST = REPO / "adk_agent" / "ora_navigator_unified" / "_kb_manifest.json"


# ---------------------------------------------------------------------------
# Tier 1 — per-doc overrides (used for items that URL/subcategory can't disambiguate)
# Keys are doc_id (or filename stem if doc_id missing).
# ---------------------------------------------------------------------------
OVERRIDES: dict[str, str] = {
    # PI Handbooks — URL-driven split (resources / pre_award / policies)
    "form_pi_handbook_1": "_generated_resources/handbooks",
    "form_pi_handbook_2": "_generated_resources/handbooks",
    "form_pi_handbook_3": "_generated_resources/handbooks",
    "form_pi_handbook_4": "_generated_resources/handbooks",
    "form_pi_handbook_2_grant_budgets": "_generated_pre_award/handbooks",
    "form_pi_handbook_5": "_generated_policies/handbooks",
    # IRB hub doc — canonical IRB landing page lives under compliance/irb/
    "compliance_human_subjects_research": "_generated_compliance/irb",
    # IACUC hub doc (subcategory "iacuc") routes to compliance/iacuc/ (page hub)
    "compliance_animal_research": "_generated_compliance/iacuc",
}


# ---------------------------------------------------------------------------
# Tier 2 — (category, subcategory) -> destination folder
# Value of None means "compute via Tier 3 function".
# ---------------------------------------------------------------------------
ROUTE: dict[tuple[str, str], str | None] = {
    # ---- about (one folder per subcat) ----
    ("about", "overview"):        "_generated_about/overview",
    ("about", "history"):         "_generated_about/history",
    ("about", "mission_vision"):  "_generated_about/mission",
    ("about", "office_contact"):  "_generated_about/contact",

    # ---- announcements (one folder per subcat) ----
    ("announcements", "overview"):           "_generated_announcements/overview",
    ("announcements", "leadership_change"):  "_generated_announcements/leadership",
    ("announcements", "regulatory_update"):  "_generated_announcements/regulatory",

    # ---- compliance pages ----
    ("compliance", "overview"): "_generated_compliance/overview",
    ("compliance", "irb"): "_generated_compliance/irb",
    ("compliance", "iacuc"): "_generated_compliance/iacuc",       # also OVERRIDDEN above
    ("compliance", "iacuc_equipment"): "_generated_compliance/iacuc",
    ("compliance", "iacuc_facility"): "_generated_compliance/iacuc",
    ("compliance", "iacuc_forms"): "_generated_compliance/iacuc",
    ("compliance", "iacuc_sops"): "_generated_compliance/iacuc",
    ("compliance", "iacuc_training"): "_generated_compliance/iacuc",
    ("compliance", "coi"): "_generated_compliance/coi",
    ("compliance", "coi_subpage"): "_generated_compliance/coi",
    ("compliance", "security"): "_generated_compliance/research_security",
    ("compliance", "research_security_subpage"): "_generated_compliance/research_security",
    ("compliance", "misconduct"): "_generated_compliance/general",
    ("compliance", "rcr"): "_generated_compliance/general",
    ("compliance", "ethics"): "_generated_compliance/general",
    ("compliance", "eeo"): "_generated_compliance/general",
    ("compliance", "drug_alcohol"): "_generated_compliance/general",
    ("compliance", "news"): "_generated_compliance/general",

    # ---- opportunities (subfolder by funding-source family) ----
    ("opportunities", "overview"):                          "_generated_opportunities/overview",
    ("opportunities", "external_database"):                 "_generated_opportunities/external_databases",
    ("opportunities", "private_foundation"):                "_generated_opportunities/private",
    ("opportunities", "state_maryland"):                    "_generated_opportunities/state",
    ("opportunities", "federal_category_arts_humanities"):  "_generated_opportunities/federal",
    ("opportunities", "federal_category_defense"):          "_generated_opportunities/federal",
    ("opportunities", "federal_category_education"):        "_generated_opportunities/federal",
    ("opportunities", "federal_category_environment"):      "_generated_opportunities/federal",
    ("opportunities", "federal_category_hbcu_msi"):         "_generated_opportunities/federal",
    ("opportunities", "federal_category_health"):           "_generated_opportunities/federal",
    ("opportunities", "federal_category_non_defense"):      "_generated_opportunities/federal",

    # ---- policies (split overview, numbered policies, handbooks) ----
    ("policies", "overview"): "_generated_policies/overview",
    ("policies", "policy"):   "_generated_policies/policy",

    # ---- post_award pages (subfolder by phase) ----
    ("post_award", "overview"):     "_generated_post_award/overview",
    ("post_award", "setup"):        "_generated_post_award/setup",
    ("post_award", "changes"):      "_generated_post_award/changes",
    ("post_award", "subaward"):     "_generated_post_award/subaward",
    ("post_award", "reporting"):    "_generated_post_award/reporting",
    ("post_award", "forms_index"):  "_generated_post_award/overview",   # merge with overview

    # ---- pre_award pages (subfolder by topic) ----
    ("pre_award", "overview"):           "_generated_pre_award/overview",
    ("pre_award", "rates"):              "_generated_pre_award/rates",
    ("pre_award", "budget"):             "_generated_pre_award/budget",
    ("pre_award", "routing"):            "_generated_pre_award/routing",
    ("pre_award", "spending"):           "_generated_pre_award/spending",
    ("pre_award", "subaward"):           "_generated_pre_award/subaward",
    ("pre_award", "limited_submission"): "_generated_pre_award/limited_submission",
    ("pre_award", "checklist"):          "_generated_pre_award/checklists",  # merge with form-level checklists

    # ---- resources (consolidate hub pages into overview) ----
    ("resources", "overview"):            "_generated_resources/overview",
    ("resources", "pi_handbooks_index"):  "_generated_resources/overview",
    ("resources", "templates"):           "_generated_resources/overview",

    # ---- service_areas / staff ----
    ("service_areas", "function_to_staff_routing"): "_generated_service_areas/overview",
    ("staff", "ora_staff_profile"):                  "_generated_staff",  # kept flat (14 profiles)

    # ---- trainings hub pages (merge each into matching topic folder) ----
    ("trainings", "overview"):              "_generated_trainings/overview",
    ("trainings", "etraining"):             "_generated_trainings/overview",
    ("trainings", "compliance_training"):   "_generated_trainings/compliance_training",
    ("trainings", "d_red_seminars"):        "_generated_trainings/d_red",
    ("trainings", "faculty_development"):   "_generated_trainings/faculty_development",
    ("trainings", "workshops"):             "_generated_trainings/workshops",
    ("trainings", "test_prep"):             "_generated_trainings/racc",
    ("trainings", "external"):              "_generated_trainings/external",

    # ---- forms (category == "form" today, will be rewritten to dest category) ----
    ("form", "iacuc_sop"): "_generated_compliance/iacuc/sops",
    ("form", "iacuc_form"): "_generated_compliance/iacuc/forms",
    ("form", "irb_form"): "_generated_compliance/irb/forms",
    ("form", "coi_form"): "_generated_compliance/coi/forms",
    ("form", "research_security_pdf"): "_generated_compliance/research_security/forms",
    ("form", "compliance_policy"): "_generated_compliance/policies",
    ("form", "compliance_pdf"): None,           # Tier 3: URL-routed
    ("form", "pre_award_pdf"): "_generated_pre_award/forms",
    ("form", "pre_award_template"): "_generated_pre_award/templates",
    ("form", "pre_award_checklist"): "_generated_pre_award/checklists",
    ("form", "docusign_post_award"): "_generated_post_award/docusign",
    ("form", "letter_template"): "_generated_post_award/templates",
    ("form", "sample_post_award"): "_generated_post_award/forms",
    ("form", "test_prep_material"): "_generated_trainings/racc",
    ("form", "training_material"): None,        # Tier 3: URL-routed
    ("form", "training_video"): "_generated_trainings/compliance_training",
    ("form", "external_training"): "_generated_trainings/external",
    # pi_handbook handled by OVERRIDES
}


# ---------------------------------------------------------------------------
# Tier 3 — URL bucket routers
# ---------------------------------------------------------------------------
def route_training_material(source_url: str) -> str:
    u = (source_url or "").lower()
    if "monthly-d-red" in u or "/d-red" in u:
        return "_generated_trainings/d_red"
    if "new-faculty-development" in u or "new-faculty" in u:
        return "_generated_trainings/faculty_development"
    if "special-workshops" in u or "/workshops" in u:
        return "_generated_trainings/workshops"
    if "compliance-and-security-training" in u or "research-security/training" in u:
        return "_generated_trainings/compliance_training"
    return "_generated_trainings/materials"


def route_compliance_pdf(source_url: str) -> str:
    u = (source_url or "").lower()
    if "conflict-of-interest" in u:
        return "_generated_compliance/coi/forms"
    if "human-subjects-research" in u or "/irb" in u:
        return "_generated_compliance/irb/forms"
    if "research-security" in u:
        return "_generated_compliance/research_security/forms"
    # default catch-all for cross-cutting compliance PDFs (RCR, ethics, etc.)
    return "_generated_compliance/general/forms"


# ---------------------------------------------------------------------------
# Migration core
# ---------------------------------------------------------------------------
def derive_category_from_dst(dst_rel: str) -> str:
    """`_generated_compliance/iacuc/sops` -> `compliance`."""
    top = dst_rel.split("/", 1)[0]
    return top.removeprefix("_generated_")


def plan_moves(kb_dir: Path) -> tuple[list[dict], list[dict]]:
    """Return (moves, unmatched). Each move is a dict with src, dst, data."""
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

        doc_id = data.get("doc_id") or src.stem
        cat = data.get("category", "")
        sub = data.get("subcategory", "")
        # If we previously rewrote a form's category, fall back to "form" so the
        # ROUTE table (keyed on the original category) still matches.
        lookup_cat = "form" if data.get("legacy_category") == "form" else cat

        # Tier 1
        dst_rel = OVERRIDES.get(doc_id)

        # Tier 2
        if not dst_rel:
            dst_rel = ROUTE.get((lookup_cat, sub))
            if dst_rel is None and (lookup_cat, sub) in ROUTE:
                # Sentinel for Tier 3
                if lookup_cat == "form" and sub == "training_material":
                    dst_rel = route_training_material(data.get("source_url", ""))
                elif lookup_cat == "form" and sub == "compliance_pdf":
                    dst_rel = route_compliance_pdf(data.get("source_url", ""))

        # Tier 3 fallback for items not in ROUTE but in known buckets
        if not dst_rel and lookup_cat == "form" and sub == "pi_handbook":
            # Defensive: any handbook not in OVERRIDES goes to resources
            dst_rel = "_generated_resources/handbooks"

        if not dst_rel:
            unmatched.append({
                "path": str(src.relative_to(kb_dir)),
                "doc_id": doc_id,
                "category": cat,
                "subcategory": sub,
                "reason": "no route",
            })
            continue

        dst = kb_dir / dst_rel / src.name
        moves.append({"src": src, "dst": dst, "dst_rel": dst_rel, "data": data, "doc_id": doc_id})

    return moves, unmatched


def execute_moves(moves: list[dict], dry_run: bool) -> None:
    """Execute moves. Rewrites `category` for forms (singular -> plural dest cat)."""
    for m in moves:
        src: Path = m["src"]
        dst: Path = m["dst"]
        data = m["data"]
        dst_rel = m["dst_rel"]

        if src.resolve() == dst.resolve():
            continue  # idempotent

        original_cat = data.get("category")
        new_cat = derive_category_from_dst(dst_rel)
        rewrote = False
        if original_cat == "form" and new_cat != "form":
            data["legacy_category"] = "form"
            data["category"] = new_cat
            rewrote = True

        if dry_run:
            rewrite_note = f"  [rewrite category: form -> {new_cat}]" if rewrote else ""
            print(f"  MOVE {src.relative_to(KB_DIR)} -> {dst.relative_to(KB_DIR)}{rewrite_note}")
            continue

        dst.parent.mkdir(parents=True, exist_ok=True)
        if rewrote:
            src.write_text(json.dumps(data, indent=2, ensure_ascii=False))
        shutil.move(str(src), str(dst))


def cleanup_empty_dirs(kb_dir: Path, dry_run: bool) -> None:
    """Remove now-empty _generated_* subdirectories after moves."""
    for d in sorted(kb_dir.rglob("_generated_*"), key=lambda p: -len(str(p))):
        if d.is_dir() and not any(d.iterdir()):
            if dry_run:
                print(f"  RMDIR {d.relative_to(kb_dir)}")
            else:
                d.rmdir()


# ---------------------------------------------------------------------------
# Manifest builder
# ---------------------------------------------------------------------------
CATEGORY_DISPLAY: dict[str, dict[str, str]] = {
    "pre_award":     {"label": "Pre-Award",          "description": "Proposal preparation, F&A and fringe rates, budgets, routing, institutional IDs, PI handbooks."},
    "post_award":    {"label": "Post-Award",         "description": "Setup, award changes (NCE), subawards, reporting (final / effort), DocuSign forms, letter templates."},
    "compliance":    {"label": "Research Compliance","description": "IRB, IACUC, COI, research security, RCR, misconduct, ethics, EEO."},
    "trainings":     {"label": "Trainings",          "description": "eTraining, monthly D-RED seminars, new-faculty development, workshops, RACC test prep."},
    "policies":      {"label": "Policies & Guidelines","description": "ORA policies, PI Handbook 5 (compliance chapter)."},
    "opportunities": {"label": "Funding Opportunities","description": "Federal, state, and private funding databases and category landing pages."},
    "resources":     {"label": "Resources",          "description": "PI handbook hub, templates index, general resources."},
    "staff":         {"label": "ORA Staff",          "description": "Staff profiles, contact info, role-based routing."},
    "about":         {"label": "About ORA",          "description": "ORA mission, history, office contact info."},
    "announcements": {"label": "Announcements",      "description": "Regulatory updates, leadership transitions, news posts."},
    "service_areas": {"label": "Service Areas",      "description": "Function-to-staff routing map."},
}

SUBCATEGORY_DISPLAY: dict[tuple[str, str], str] = {
    # pre_award
    ("pre_award", "overview"):           "Hub & Overview Pages",
    ("pre_award", "rates"):              "F&A and Fringe Rates",
    ("pre_award", "budget"):             "Budget Development",
    ("pre_award", "routing"):            "Internal Routing",
    ("pre_award", "spending"):           "Pre-Award Spending",
    ("pre_award", "subaward"):           "Pre-Award Subawards",
    ("pre_award", "limited_submission"): "Limited Submission",
    ("pre_award", "forms"):              "Pre-Award Forms (PDFs)",
    ("pre_award", "templates"):          "Pre-Award Templates",
    ("pre_award", "checklists"):         "Proposal Submission Checklists",
    ("pre_award", "handbooks"):          "PI Handbook (Pre-Award chapter)",

    # post_award
    ("post_award", "overview"):    "Hub & Forms Index",
    ("post_award", "setup"):       "Notification & Award Setup",
    ("post_award", "changes"):     "Changes to an Award (NCE, etc.)",
    ("post_award", "subaward"):    "Post-Award Subawards",
    ("post_award", "reporting"):   "Reporting (Final, Effort)",
    ("post_award", "docusign"):    "DocuSign Forms",
    ("post_award", "templates"):   "Letter Templates (NCE, change-of-PI, subaward, etc.)",
    ("post_award", "forms"):       "Sample Post-Award Forms",

    # compliance
    ("compliance", "overview"):          "Compliance Hub",
    ("compliance", "irb"):               "IRB (Human Subjects Research)",
    ("compliance", "iacuc"):             "IACUC (Animal Research)",
    ("compliance", "coi"):               "Conflict of Interest",
    ("compliance", "research_security"): "Research Security",
    ("compliance", "general"):           "RCR, Misconduct, Ethics, EEO, Drug/Alcohol, News",
    ("compliance", "policies"):          "Compliance Policy PDFs",

    # trainings
    ("trainings", "overview"):              "Hub & e-Training Pages",
    ("trainings", "d_red"):                 "Monthly D-RED Seminars",
    ("trainings", "faculty_development"):   "New Faculty Development Series",
    ("trainings", "workshops"):             "Special Workshops",
    ("trainings", "compliance_training"):   "Compliance & Security Training",
    ("trainings", "racc"):                  "RACC Test Prep",
    ("trainings", "materials"):             "Other Training Materials",
    ("trainings", "external"):              "External Trainings (ASCEND, etc.)",

    # policies
    ("policies", "overview"):  "Policy Hub",
    ("policies", "policy"):    "Numbered ORA Policies",
    ("policies", "handbooks"): "PI Handbook (Policies chapter)",

    # opportunities (5 buckets)
    ("opportunities", "overview"):           "Hub & Overview",
    ("opportunities", "external_databases"): "External Funding Databases",
    ("opportunities", "private"):            "Private Foundations",
    ("opportunities", "state"):              "State of Maryland",
    ("opportunities", "federal"):            "Federal Funding (by topic — health, defense, education, environment, arts, HBCU/MSI, non-defense)",

    # resources
    ("resources", "overview"):  "Resources Hub (PI Handbook index, Templates index)",
    ("resources", "handbooks"): "PI Handbooks (volumes 1–4)",

    # staff (kept flat)
    ("staff", "ora_staff_profile"): "Staff Profiles",

    # about
    ("about", "overview"):       "About ORA",
    ("about", "history"):        "History",
    ("about", "mission"):        "Mission & Vision",
    ("about", "contact"):        "Office Contact",

    # announcements
    ("announcements", "overview"):    "Announcements Hub",
    ("announcements", "leadership"):  "Leadership Changes",
    ("announcements", "regulatory"):  "Regulatory Updates",

    # service_areas
    ("service_areas", "overview"):    "Function → Staff Routing",
}


def manifest_subcategory_key(rel_path: Path, data: dict) -> str:
    """Decide which manifest subcategory bucket a file belongs to.

    Files in a sub-folder under the category root use the sub-folder name.
    Files directly at the category root use the doc's `subcategory` field.
    """
    parts = rel_path.parts
    # parts[0] is _generated_<category>
    if len(parts) == 2:
        # File at category top level
        return data.get("subcategory") or "uncategorized"
    return parts[1]


def build_manifest(kb_dir: Path) -> dict:
    """Walk the (already-restructured) tree and build the nested manifest."""
    categories: dict[str, dict] = {}

    for src in sorted(kb_dir.rglob("*.json")):
        if src.name.startswith("_"):
            continue
        rel = src.relative_to(kb_dir)
        parts = rel.parts
        if not parts[0].startswith("_generated_"):
            continue
        cat = parts[0].removeprefix("_generated_")
        data = json.loads(src.read_text())
        subcat = manifest_subcategory_key(rel, data)

        cat_node = categories.setdefault(cat, {
            "display_label": CATEGORY_DISPLAY.get(cat, {}).get("label", cat.title()),
            "description":   CATEGORY_DISPLAY.get(cat, {}).get("description", ""),
            "doc_count": 0,
            "subcategories": {},
        })
        sub_node = cat_node["subcategories"].setdefault(subcat, {
            "display_label": SUBCATEGORY_DISPLAY.get((cat, subcat), subcat.replace("_", " ").title()),
            "doc_count": 0,
            "docs": [],
        })
        sub_node["docs"].append({
            "doc_id": data.get("doc_id") or src.stem,
            "title": data.get("title", ""),
            "source_url": data.get("source_url", ""),
            "file_path": str(rel),
            "playwright_verified": bool(data.get("playwright_verified", False)),
        })
        sub_node["doc_count"] += 1
        cat_node["doc_count"] += 1

    # Sort docs within each subcategory by title for stable output
    for cat_node in categories.values():
        for sub_node in cat_node["subcategories"].values():
            sub_node["docs"].sort(key=lambda d: (d["title"], d["doc_id"]))

    total = sum(c["doc_count"] for c in categories.values())
    return {
        "version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "kb_dir": "backend/kb_structured",
        "total_docs": total,
        "categories": categories,
    }


def regenerate_all_documents_jsonl(kb_dir: Path) -> int:
    """Regenerate _all_documents.jsonl with new file_path values."""
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
    ap.add_argument("--dry-run", action="store_true", help="Preview moves without changing disk.")
    ap.add_argument("--manifest-only", action="store_true",
                    help="Skip moves; rebuild _manifest.json and _all_documents.jsonl from current disk.")
    args = ap.parse_args()

    if not KB_DIR.exists():
        print(f"[FATAL] KB directory not found: {KB_DIR}")
        return 1

    if not args.manifest_only:
        moves, unmatched = plan_moves(KB_DIR)
        print(f"[PLAN] {len(moves)} files to process, {len(unmatched)} unmatched")

        # Tally by destination for sanity
        by_dst = collections.Counter(m["dst_rel"] for m in moves)
        print("[PLAN] Destinations:")
        for dst, n in sorted(by_dst.items()):
            print(f"  {n:4d}  {dst}")

        if unmatched:
            print()
            print("[UNMATCHED] These docs have no route — extend OVERRIDES or ROUTE:")
            for u in unmatched:
                print(f"  {u}")
            return 2  # exit early; do not move anything

        if args.dry_run:
            print()
            print("[DRY-RUN] Detailed move plan:")
            execute_moves(moves, dry_run=True)
            print()
            print("[DRY-RUN] Empty directories to clean up:")
            # Simulate by counting files per directory post-move
            return 0

        print()
        print("[EXECUTE] Moving files...")
        execute_moves(moves, dry_run=False)
        cleanup_empty_dirs(KB_DIR, dry_run=False)

    # Always rebuild manifest + index at the end
    print()
    print("[INDEX] Regenerating _all_documents.jsonl...")
    n = regenerate_all_documents_jsonl(KB_DIR)
    print(f"[INDEX] Wrote {n} lines")

    print("[INDEX] Building _manifest.json...")
    manifest = build_manifest(KB_DIR)
    canonical = KB_DIR / "_manifest.json"
    canonical.write_text(json.dumps(manifest, indent=2, ensure_ascii=False))
    ADK_MANIFEST_DEST.parent.mkdir(parents=True, exist_ok=True)
    ADK_MANIFEST_DEST.write_text(json.dumps(manifest, indent=2, ensure_ascii=False))
    print(f"[INDEX] Wrote {canonical}")
    print(f"[INDEX] Wrote {ADK_MANIFEST_DEST}")
    print(f"[INDEX] total_docs = {manifest['total_docs']}")
    for cat, node in sorted(manifest["categories"].items()):
        print(f"  {node['doc_count']:4d}  {cat}  ({len(node['subcategories'])} subcategories)")

    return 0


if __name__ == "__main__":
    sys.exit(main())
