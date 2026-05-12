#!/usr/bin/env python3
"""
Convert JSON files to readable TXT format and upload to GCS + Datastore
========================================================================
Converts structured JSON data to human-readable text that the AI agent
can easily parse and provide to users, including direct links.
"""

import os
import sys
import json
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from datastore_manager import upload_document, sync_datastore, GCS_BUCKET_NAME

DATA_SOURCES_DIR = Path(__file__).parent / "data_sources"


def json_to_text_forms(data: dict) -> str:
    """Convert forms.json to readable text with all links."""
    lines = []
    lines.append("=" * 70)
    lines.append("MORGAN STATE UNIVERSITY - STUDENT FORMS DIRECTORY")
    lines.append("Complete list of forms with direct links")
    lines.append("=" * 70)
    lines.append("")

    # How to access WebSIS forms
    if "forms_overview" in data:
        overview = data["forms_overview"]
        lines.append("HOW TO ACCESS WEBSIS FORMS:")
        lines.append("-" * 40)
        if "how_to_access_websis_forms" in overview:
            for i, step in enumerate(overview["how_to_access_websis_forms"].get("steps", []), 1):
                lines.append(f"  {i}. {step}")
        lines.append("")

    # Registrar Forms
    if "registrar_forms" in data:
        reg = data["registrar_forms"]
        lines.append("=" * 70)
        lines.append("OFFICE OF THE REGISTRAR FORMS")
        lines.append("=" * 70)
        if "contact" in reg:
            c = reg["contact"]
            lines.append(f"Contact: {c.get('phone', '')} | {c.get('location', '')}")
        lines.append("")

        # Current student forms
        lines.append("CURRENT STUDENT FORMS (Access via WebSIS):")
        lines.append("-" * 50)
        for form in reg.get("current_student_forms", []):
            lines.append(f"  * {form['name']}")
            lines.append(f"    Purpose: {form.get('purpose', '')}")
            if "video_tutorial" in form:
                lines.append(f"    Tutorial: {form['video_tutorial']}")
            if "access" in form:
                lines.append(f"    Access: {form['access']}")
            lines.append("")

        # Former student forms
        lines.append("FORMER STUDENT FORMS (Direct Links):")
        lines.append("-" * 50)
        for form in reg.get("former_student_forms", []):
            lines.append(f"  * {form['name']}")
            lines.append(f"    Purpose: {form.get('purpose', '')}")
            if "link" in form:
                lines.append(f"    Link: {form['link']}")
            lines.append("")

        # Transcripts
        lines.append("TRANSCRIPTS AND DIPLOMAS:")
        lines.append("-" * 50)
        for form in reg.get("transcripts_and_diplomas", []):
            lines.append(f"  * {form['name']}")
            lines.append(f"    Purpose: {form.get('purpose', '')}")
            if "link" in form:
                lines.append(f"    Link: {form['link']}")
            if "access" in form:
                lines.append(f"    Access: {form['access']}")
            lines.append("")

    # CS Department Forms
    if "computer_science_department_forms" in data:
        cs = data["computer_science_department_forms"]
        lines.append("=" * 70)
        lines.append("COMPUTER SCIENCE DEPARTMENT FORMS")
        lines.append("=" * 70)
        if "contact" in cs:
            c = cs["contact"]
            lines.append(f"Contact: {c.get('name', '')} - {c.get('email', '')} - {c.get('phone', '')}")
        lines.append("")
        for form in cs.get("forms", []):
            lines.append(f"  * {form['name']}")
            lines.append(f"    Purpose: {form.get('purpose', '')}")
            if "process" in form:
                lines.append(f"    Process: {form['process']}")
            if "notes" in form:
                lines.append(f"    Note: {form['notes']}")
            lines.append("")

    # SCMNS Forms
    if "scmns_forms" in data:
        scmns = data["scmns_forms"]
        lines.append("=" * 70)
        lines.append("SCMNS (School of Computer, Mathematical & Natural Sciences) FORMS")
        lines.append("=" * 70)
        for form in scmns.get("forms", []):
            lines.append(f"  * {form['name']}")
            lines.append(f"    Purpose: {form.get('purpose', '')}")
            if "link" in form:
                lines.append(f"    Link: {form['link']}")
            if "instructions_pdf" in form:
                lines.append(f"    Instructions PDF: {form['instructions_pdf']}")
            if "appeals_form_pdf" in form:
                lines.append(f"    Appeals Form PDF: {form['appeals_form_pdf']}")
            if "cares_form_link" in form:
                lines.append(f"    C.A.R.E.S. Form: {form['cares_form_link']}")
            lines.append("")

    # Graduate Student Forms
    if "graduate_student_forms" in data:
        grad = data["graduate_student_forms"]
        lines.append("=" * 70)
        lines.append("GRADUATE STUDENT FORMS")
        lines.append("=" * 70)
        if "contact" in grad:
            c = grad["contact"]
            lines.append(f"Contact: {c.get('phone', '')} | {c.get('email', '')}")
        if "application_portal" in grad:
            lines.append(f"Application Portal: {grad['application_portal']}")
        lines.append("")
        for form in grad.get("forms", []):
            lines.append(f"  * {form['name']}")
            lines.append(f"    Purpose: {form.get('purpose', '')}")
            if "link" in form:
                lines.append(f"    Link: {form['link']}")
            lines.append("")

    # Financial Aid Forms
    if "financial_aid_forms" in data:
        fin = data["financial_aid_forms"]
        lines.append("=" * 70)
        lines.append("FINANCIAL AID FORMS (2026-2027)")
        lines.append("=" * 70)
        if "contact" in fin:
            c = fin["contact"]
            lines.append(f"Contact: {c.get('phone', '')} | {c.get('email', '')}")
            lines.append(f"Location: {c.get('location', '')}")
        if "fafsa_info" in fin:
            f = fin["fafsa_info"]
            lines.append(f"FAFSA Link: {f.get('link', '')}")
            lines.append(f"Morgan State School Code: {f.get('school_code', '')}")
        if "verification_portal" in fin:
            lines.append(f"Verification Portal: {fin['verification_portal']}")
        lines.append("")

        for form in fin.get("forms_2026_2027", []):
            lines.append(f"  * {form['name']}")
            lines.append(f"    Purpose: {form.get('purpose', '')}")
            if "link" in form:
                lines.append(f"    Link: {form['link']}")
            lines.append("")

    # Advising Forms
    if "advising_forms" in data:
        adv = data["advising_forms"]
        lines.append("=" * 70)
        lines.append("ADVISING FORMS")
        lines.append("=" * 70)
        for form in adv.get("forms", []):
            lines.append(f"  * {form['name']}")
            lines.append(f"    Purpose: {form.get('purpose', '')}")
            if "link" in form:
                lines.append(f"    Link: {form['link']}")
            lines.append("")

    # Student Portals
    if "student_portals" in data:
        portals = data["student_portals"]
        lines.append("=" * 70)
        lines.append("STUDENT PORTALS AND ONLINE SYSTEMS")
        lines.append("=" * 70)
        for portal in portals.get("portals", []):
            lines.append(f"  * {portal['name']}")
            lines.append(f"    Purpose: {portal.get('purpose', '')}")
            if "link" in portal:
                lines.append(f"    Link: {portal['link']}")
            if "access" in portal:
                lines.append(f"    Access: {portal['access']}")
            lines.append("")

    # Tutoring
    if "tutoring_appointments" in data:
        tut = data["tutoring_appointments"]
        lines.append("=" * 70)
        lines.append("TUTORING AND ACADEMIC SUPPORT")
        lines.append("=" * 70)
        for resource in tut.get("resources", []):
            lines.append(f"  * {resource['name']}")
            lines.append(f"    Purpose: {resource.get('purpose', '')}")
            if "appointment_link" in resource:
                lines.append(f"    Appointments: {resource['appointment_link']}")
            if "email" in resource:
                lines.append(f"    Email: {resource['email']}")
            lines.append("")

    return "\n".join(lines)


def json_to_text_department(data: dict) -> str:
    """Convert Department.json to readable text."""
    lines = []
    lines.append("=" * 70)
    lines.append("MORGAN STATE UNIVERSITY - COMPUTER SCIENCE DEPARTMENT")
    lines.append("=" * 70)
    lines.append("")

    # University Leadership
    if "university_leadership" in data:
        ul = data["university_leadership"]
        lines.append("UNIVERSITY LEADERSHIP:")
        lines.append("-" * 40)
        if "president" in ul:
            p = ul["president"]
            lines.append(f"  President: Dr. {p['name']}")
            lines.append(f"    Office: {p.get('office', '')}")
            lines.append(f"    Email: {p.get('email', '')}")
        if "provost" in ul:
            p = ul["provost"]
            lines.append(f"  Provost: Dr. {p['name']}")
            lines.append(f"    Office: {p.get('office', '')}")
            lines.append(f"    Email: {p.get('email', '')}")
        if "scmns_dean" in ul:
            d = ul["scmns_dean"]
            lines.append(f"  SCMNS Dean: Dr. {d['name']}")
            lines.append(f"    Office: {d.get('office', '')}")
            lines.append(f"    Phone: {d.get('phone', '')}")
            lines.append(f"    Email: {d.get('email', '')}")
        lines.append("")

    # About Department
    if "about_department" in data:
        about = data["about_department"]
        lines.append("ABOUT THE DEPARTMENT:")
        lines.append("-" * 40)
        lines.append(about.get("description", ""))
        lines.append("")
        lines.append("DEGREES OFFERED:")
        for deg in about.get("degrees_offered", []):
            lines.append(f"  - {deg}")
        lines.append("")

    # Contact Info
    if "contact_information" in data.get("about_department", {}):
        ci = data["about_department"]["contact_information"]
        lines.append("CONTACT INFORMATION:")
        lines.append("-" * 40)
        lines.append(f"  Address: {ci.get('address', '')}")
        lines.append(f"  Phone: {ci.get('phone', '')}")
        lines.append(f"  Email: {ci.get('email', '')}")
        lines.append("")

    # Faculty
    if "faculty_and_staff" in data.get("about_department", {}):
        fs = data["about_department"]["faculty_and_staff"]

        lines.append("=" * 70)
        lines.append("FACULTY AND STAFF DIRECTORY")
        lines.append("=" * 70)
        lines.append("")

        # Department Leadership
        lines.append("DEPARTMENT LEADERSHIP:")
        lines.append("-" * 40)
        for person in fs.get("department_leadership", []):
            lines.append(f"  {person['name']}")
            lines.append(f"    Title: {person['title']}")
            lines.append(f"    Office: {person.get('office', '')}")
            lines.append(f"    Phone: {person.get('phone', '')}")
            lines.append(f"    Email: {person.get('email', '')}")
            if person.get("research_interest"):
                lines.append(f"    Research: {person['research_interest']}")
            lines.append("")

        # Professors
        lines.append("PROFESSORS:")
        lines.append("-" * 40)
        for person in fs.get("professors", []):
            lines.append(f"  {person['name']}")
            lines.append(f"    Title: {person['title']}")
            lines.append(f"    Office: {person.get('office', '')}")
            if person.get("phone"):
                lines.append(f"    Phone: {person['phone']}")
            lines.append(f"    Email: {person.get('email', '')}")
            if person.get("research_interest"):
                lines.append(f"    Research: {person['research_interest']}")
            lines.append("")

        # Lecturers
        lines.append("LECTURERS:")
        lines.append("-" * 40)
        for person in fs.get("lecturers", []):
            lines.append(f"  {person['name']}")
            lines.append(f"    Title: {person.get('title', 'Lecturer')}")
            lines.append(f"    Office: {person.get('office', '')}")
            if person.get("phone"):
                lines.append(f"    Phone: {person['phone']}")
            if person.get("email"):
                lines.append(f"    Email: {person['email']}")
            lines.append("")

        # Engineers in Residence
        if "engineers_in_residence" in fs:
            eir = fs["engineers_in_residence"]
            lines.append("ENGINEERS IN RESIDENCE:")
            lines.append("-" * 40)
            lines.append(eir.get("description", ""))
            if "current_engineer" in eir:
                ce = eir["current_engineer"]
                lines.append(f"  Current: {ce['name']}")
                lines.append(f"    Title: {ce.get('title', '')}")
                lines.append(f"    Office: {ce.get('office', '')}")
                lines.append(f"    Email: {ce.get('email', '')}")
            lines.append("")

        # Administrative Staff
        lines.append("ADMINISTRATIVE STAFF:")
        lines.append("-" * 40)
        for person in fs.get("administrative_staff", []):
            lines.append(f"  {person['name']}")
            lines.append(f"    Title: {person.get('title', '')}")
            lines.append(f"    Office: {person.get('office', '')}")
            lines.append(f"    Phone: {person.get('phone', '')}")
            lines.append(f"    Email: {person.get('email', '')}")
            lines.append("")

    # Research Areas
    if "research_areas" in data.get("about_department", {}):
        lines.append("RESEARCH AREAS:")
        lines.append("-" * 40)
        for area in data["about_department"]["research_areas"]:
            lines.append(f"  - {area}")
        lines.append("")

    # Student Organizations
    if "student_organizations" in data.get("about_department", {}):
        lines.append("STUDENT ORGANIZATIONS:")
        lines.append("-" * 40)
        for org in data["about_department"]["student_organizations"]:
            lines.append(f"  {org['name']}")
            if org.get("mission"):
                lines.append(f"    Mission: {org['mission']}")
            if org.get("instagram"):
                lines.append(f"    Instagram: {org['instagram']}")
            lines.append("")

    # Department Forms
    if "department_forms" in data.get("about_department", {}):
        df = data["about_department"]["department_forms"]
        lines.append("DEPARTMENT FORMS:")
        lines.append("-" * 40)
        for form_name, form_data in df.items():
            lines.append(f"  {form_name.replace('_', ' ').title()}")
            lines.append(f"    Purpose: {form_data.get('purpose', '')}")
            if form_data.get("cares_form_link"):
                lines.append(f"    C.A.R.E.S. Form: {form_data['cares_form_link']}")
            if form_data.get("appeals_form_link"):
                lines.append(f"    Appeals Form: {form_data['appeals_form_link']}")
            lines.append("")

    return "\n".join(lines)


def convert_and_upload_file(json_filename: str, txt_filename: str, converter_func) -> bool:
    """Convert a JSON file to TXT and upload."""
    json_path = DATA_SOURCES_DIR / json_filename

    if not json_path.exists():
        print(f"   [ERROR] File not found: {json_path}")
        return False

    try:
        # Load JSON
        with open(json_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        # Convert to text
        text_content = converter_func(data)

        # Upload
        print(f"   [UPLOAD] {txt_filename}...", end=" ", flush=True)
        result = upload_document(txt_filename, text_content.encode("utf-8"), "text/plain")

        if result["success"]:
            print("[OK]")
            return True
        else:
            print(f"[FAIL] {result['message']}")
            return False

    except Exception as e:
        print(f"[ERROR] {e}")
        return False


def main():
    print("=" * 60)
    print("  CS Navigator - Convert & Upload KB Files")
    print("=" * 60)

    print("\n[CONVERT] Converting JSON to readable TXT format...")
    print(f"   Target: gs://{GCS_BUCKET_NAME}/\n")

    success = 0
    failed = 0

    # Upload forms.json as forms_directory.txt
    if convert_and_upload_file("forms.json", "forms_directory.txt", json_to_text_forms):
        success += 1
    else:
        failed += 1

    # Upload Department.json as department_info.txt
    if convert_and_upload_file("Department.json", "department_info.txt", json_to_text_department):
        success += 1
    else:
        failed += 1

    print(f"\n   [OK] Uploaded: {success}")
    if failed:
        print(f"   [FAIL] Failed: {failed}")

    # Sync datastore
    print("\n[SYNC] Syncing datastore...")
    result = sync_datastore()
    if result["success"]:
        print(f"   [OK] {result['message']}")
    else:
        print(f"   [FAIL] {result['message']}")

    print("\n[DONE] Complete!\n")


if __name__ == "__main__":
    main()
