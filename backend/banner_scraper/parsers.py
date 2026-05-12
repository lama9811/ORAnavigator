# backend/banner_scraper/parsers.py
"""
Parsers for Morgan State Banner SSB and DegreeWorks data.
Handles both the DegreeWorks JSON API response and Banner HTML pages.
"""

import re
import json


# ======================================================================
# DegreeWorks JSON Audit Parser (PRIMARY)
# ======================================================================

def parse_degreeworks_audit_json(audit: dict) -> dict:
    """
    Parse the DegreeWorks REST API JSON audit response.

    Actual structure (Morgan State DW 5.x):
    - auditHeader: {studentId, studentName, studentSystemGpa, percentComplete, ...}
    - blockArray: [{requirementType, title, gpa, creditsApplied, ruleArray: [...]}]
    - classInformation: {classArray: [{discipline, number, letterGrade, credits, courseTitle, termLiteral, inProgress}]}
    - inProgress: {classes, credits, classArray: [{discipline, number, credits, letterGrade}]}
    - degreeInformation: {degreeDataArray: [{degree, degreeLiteral, catalogYearLit, studentLevelLiteral, ...}]}
    """
    if not audit:
        return {}

    result = {}

    # --- auditHeader: student info + GPA ---
    header = audit.get("auditHeader") or {}
    student_name = header.get("studentName", "")
    if "," in student_name:
        parts = student_name.split(",", 1)
        student_name = f"{parts[1].strip()} {parts[0].strip()}"
    result["student_name"] = student_name or None
    result["student_id"] = header.get("studentId") or None
    result["overall_gpa"] = _to_float(header.get("studentSystemGpa") or header.get("degreeworksGpa"))

    # --- degreeInformation: degree, classification, catalog year ---
    deg_info = audit.get("degreeInformation") or {}
    deg_array = deg_info.get("degreeDataArray") or []
    if deg_array:
        deg = deg_array[0]
        degree_literal = (deg.get("degreeLiteral") or "").strip()
        level_literal = (deg.get("studentLevelLiteral") or "").strip()
        catalog_lit = (deg.get("catalogYearLit") or "").strip()

        # Classification from studentLevelLiteral: "4-Senior" -> "Senior"
        if level_literal and "-" in level_literal:
            result["classification"] = level_literal.split("-", 1)[1].strip()
        elif level_literal:
            result["classification"] = level_literal

        result["catalog_year"] = catalog_lit or None

        # Degree program from blockArray DEGREE block (has the title)
        # or from degreeLiteral
        result["degree_program"] = degree_literal or None

    # --- blockArray: DEGREE block (credits) + MAJOR block (major GPA) ---
    blocks = audit.get("blockArray") or []
    for block in blocks:
        req_type = block.get("requirementType", "")

        if req_type == "DEGREE":
            credits_applied = _to_float(block.get("creditsApplied"))
            credits_required = _to_float(block.get("creditsRequired"))
            block_gpa = _to_float(block.get("gpa"))

            if credits_applied:
                result["total_credits_earned"] = credits_applied
            if credits_required:
                result["credits_required"] = credits_required
            if credits_applied and credits_required:
                result["credits_remaining"] = max(0, credits_required - credits_applied)
            if block_gpa and not result.get("overall_gpa"):
                result["overall_gpa"] = block_gpa

        elif req_type == "MAJOR":
            major_gpa = _to_float(block.get("gpa"))
            if major_gpa:
                result["major_gpa"] = major_gpa
            major_credits_req = _to_float(block.get("creditsRequired"))
            major_credits_applied = _to_float(block.get("creditsApplied"))
            if major_credits_req:
                result["major_credits_required"] = major_credits_req
            if major_credits_applied:
                result["major_credits_earned"] = major_credits_applied

    # --- Extract remaining requirements from ALL blocks ---
    remaining = []
    for block in blocks:
        block_title = (block.get("title") or block.get("requirementType") or "").strip()
        percent = block.get("percentComplete", "")
        # Skip fully completed blocks
        if str(percent) == "100":
            continue
        rules = block.get("ruleArray") or []
        for rule in rules:
            # Check if rule is not satisfied
            if rule.get("percentComplete") == "100":
                continue
            label = (rule.get("label") or rule.get("ruleTag") or "").strip()
            req_value = (rule.get("requirement") or {})
            # Extract course requirements from ifElsePart or requirement
            courses_needed = []
            if_part = rule.get("ifElsePart") or rule.get("ifPart") or {}
            class_array = (req_value.get("classesApplied") or {}).get("classArray") or []
            # Get courses still needed from the rule
            num_classes = req_value.get("numClasses") or req_value.get("classesRequired")
            credits_req = req_value.get("creditsRequired")
            if label:
                entry = {"requirement": label, "category": block_title}
                if num_classes:
                    entry["classes_needed"] = num_classes
                if credits_req:
                    entry["credits_needed"] = credits_req
                remaining.append(entry)
    if remaining:
        result["courses_remaining"] = json.dumps(remaining)

    # --- Find major from degreeInformation ---
    for deg in deg_array:
        school_lit = (deg.get("schoolLiteral") or "").strip()
        # Look for major in reportArray or goalArray
        # For now, derive from degree program context
        # The "program" field has the major
        # Check: degreeInformation may have goalArray with major

    # Check goalArray for major + advisor
    goal_array = deg_info.get("goalArray") or []
    major_name = None
    for goal in goal_array:
        code = goal.get("code", "")
        if code == "MAJOR":
            major_name = (goal.get("valueLiteral") or "").strip()
        elif code == "ADVISOR":
            advisor_name = (goal.get("advisorName") or "").strip()
            if advisor_name:
                # "Stojkovic, Vojislav" -> "Vojislav Stojkovic"
                if "," in advisor_name:
                    parts = advisor_name.split(",", 1)
                    result["advisor"] = f"{parts[1].strip()} {parts[0].strip()}"
                else:
                    result["advisor"] = advisor_name

    # Build degree_program: "Bachelor of Science in Computer Science"
    if major_name and result.get("degree_program"):
        result["degree_program"] = f"{result['degree_program']} in {major_name}"
    elif major_name:
        result["degree_program"] = major_name

    # --- classInformation.classArray: ALL courses ---
    class_info = audit.get("classInformation") or {}
    all_classes = class_info.get("classArray") or []

    completed = []
    in_progress_courses = []

    for cls in all_classes:
        if not isinstance(cls, dict):
            continue

        code = f"{cls.get('discipline', '')} {cls.get('number', '')}".strip()
        if not code or code == " ":
            continue

        name = (cls.get("courseTitle") or "").strip()
        grade = (cls.get("letterGrade") or "").strip()
        credits = _to_float(cls.get("credits"))
        term = (cls.get("termLiteral") or cls.get("termLiteralLong") or "").strip()
        is_ip = cls.get("inProgress") == "Y"

        course = {
            "code": code,
            "name": name,
            "grade": grade,
            "credits": credits,
            "semester": term,
        }

        if is_ip or grade == "IP":
            in_progress_courses.append(course)
        else:
            completed.append(course)

    # Also check inProgress section for any we missed
    ip_section = audit.get("inProgress") or {}
    ip_classes = ip_section.get("classArray") or []
    ip_codes = {c["code"] for c in in_progress_courses}
    for cls in ip_classes:
        code = f"{cls.get('discipline', '')} {cls.get('number', '')}".strip()
        if code and code not in ip_codes:
            in_progress_courses.append({
                "code": code,
                "name": cls.get("courseTitle", ""),
                "grade": "IP",
                "credits": _to_float(cls.get("credits")),
                "semester": cls.get("termLiteral", ""),
            })

    # Deduplicate: keep all attempts for retaken courses (different semester/grade),
    # only remove true duplicates (same code + semester + grade).
    seen = set()
    unique_completed = []
    for c in completed:
        key = (c["code"], c.get("semester", ""), c.get("grade", ""))
        if key not in seen:
            seen.add(key)
            unique_completed.append(c)

    seen_ip = set()
    unique_ip = []
    for c in in_progress_courses:
        key = (c["code"], c.get("semester", ""))
        if key not in seen_ip:
            seen_ip.add(key)
            unique_ip.append(c)

    result["courses_completed"] = json.dumps(unique_completed)
    result["courses_in_progress"] = json.dumps(unique_ip)

    # Raw JSON backup (truncated)
    result["raw_data"] = json.dumps(audit)[:50000]

    print(f"[PARSER] DW JSON: name={result.get('student_name')}, GPA={result.get('overall_gpa')}, "
          f"credits={result.get('total_credits_earned')}, class={result.get('classification')}, "
          f"program={result.get('degree_program')}, "
          f"completed={len(unique_completed)}, in_progress={len(unique_ip)}")

    return result


# ----------------------------------------------------------------------
# Student Profile
# ----------------------------------------------------------------------

def parse_student_profile(raw: dict) -> dict:
    """Parse student profile response."""
    if not raw:
        return {}
    data = raw.get("data", "")
    if raw.get("type") == "html" and isinstance(data, str):
        return _parse_profile_html(data)
    elif isinstance(data, dict):
        return _parse_profile_json(data)
    return {}


def _parse_profile_html(html: str) -> dict:
    """
    Extract profile from Banner Student Profile HTML.
    The page has structure like:
      "Student Profile - Aayush Shrestha (00367844)"
      "Overall Hours: 110.5  Overall GPA: 3.953"
      Bio fields as label: value pairs
      General Information section
      Curriculum section with Degree, Major, etc.
      Advisors section
    """
    if not html:
        return {}

    # Strip scripts/styles, get clean text
    clean = re.sub(r'<script[^>]*>.*?</script>', '', html, flags=re.DOTALL | re.IGNORECASE)
    clean = re.sub(r'<style[^>]*>.*?</style>', '', clean, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'<[^>]+>', '\n', clean)
    text = re.sub(r'[ \t]+', ' ', text)  # collapse horizontal whitespace but keep newlines
    flat = re.sub(r'\s+', ' ', text).strip()  # fully flat version for some patterns

    result = {}

    # --- Name + ID from page title ---
    # "Student Profile - Aayush Shrestha (00367844)"
    title_match = re.search(r'Student Profile\s*[-:]\s*([A-Za-z\s.]+?)\s*\((\d{5,10})\)', html)
    if title_match:
        result["name"] = title_match.group(1).strip()
        result["student_id"] = title_match.group(2)

    # Fallback name from header
    if not result.get("name"):
        header_match = re.search(r'Shrestha,?\s*(\w+)', html)
        if header_match:
            result["name"] = f"{header_match.group(1)} Shrestha"

    # --- GPA & Hours ---
    # Banner puts these in the header bar, possibly across HTML elements.
    # Strip ALL tags and HTML entities, then search the flat text.
    html_notags = re.sub(r'<[^>]+>', ' ', html)
    html_notags = html_notags.replace('&nbsp;', ' ').replace('&#160;', ' ')
    html_notags = re.sub(r'&\w+;', ' ', html_notags)  # strip all HTML entities
    html_notags = re.sub(r'\s+', ' ', html_notags)

    # Debug: check if "Overall GPA" text exists at all
    if 'overall gpa' in html_notags.lower():
        print(f"[PARSER] Found 'Overall GPA' in stripped text")
        # Extract surrounding context
        idx = html_notags.lower().index('overall gpa')
        context = html_notags[max(0, idx-10):idx+40]
        print(f"[PARSER] GPA context: [{context}]")
    else:
        print(f"[PARSER] 'Overall GPA' NOT found in stripped text ({len(html_notags)} chars)")
        # Try raw HTML
        if 'overall gpa' in html.lower() or 'overallGpa' in html:
            print(f"[PARSER] But found in raw HTML! Tag stripping issue.")

    for gpa_pat in [
        r'Overall\s+GPA[:\s]*(\d+\.\d+)',
        r'GPA[:\s]+(\d+\.\d{2,3})',
        r'overallGpa["\s:]+(\d+\.\d+)',
    ]:
        gpa_match = re.search(gpa_pat, html_notags, re.IGNORECASE)
        if not gpa_match:
            # Also try raw HTML (in case it's in a data attribute)
            gpa_match = re.search(gpa_pat, html, re.IGNORECASE)
        if gpa_match:
            gpa_val = float(gpa_match.group(1))
            if 0 < gpa_val <= 4.0:
                result["overall_gpa"] = gpa_val
                print(f"[PARSER] GPA found: {gpa_val}")
                break

    for hrs_pat in [
        r'Overall\s+Hours[:\s]*(\d+\.?\d*)',
        r'Total\s+Hours[:\s]*(\d+\.?\d*)',
        r'overallHours["\s:]+(\d+\.?\d*)',
        r'Credit\s+Hours?\s+Earned[:\s]*(\d+\.?\d*)',
    ]:
        hours_match = re.search(hrs_pat, html_notags, re.IGNORECASE)
        if not hours_match:
            hours_match = re.search(hrs_pat, html, re.IGNORECASE)
        if hours_match:
            result["total_credits_earned"] = float(hours_match.group(1))
            print(f"[PARSER] Hours found: {hours_match.group(1)}")
            break

    # --- Email ---
    email_match = re.search(r'Email[:\s]*\n?\s*([\w.+-]+@morgan\.edu)', text, re.IGNORECASE)
    if not email_match:
        email_match = re.search(r'([\w.+-]+@morgan\.edu)', flat, re.IGNORECASE)
    if email_match:
        result["email"] = email_match.group(1)

    # --- Phone ---
    phone_match = re.search(r'Phone[:\s]*\n?\s*([\d\s()-]{7,20})', text, re.IGNORECASE)
    if phone_match:
        phone = phone_match.group(1).strip()
        if phone and phone != "Not Provided":
            result["phone"] = phone

    # --- Classification (Class field) ---
    class_match = re.search(r'Class[:\s]*\n?\s*(Freshman|Sophomore|Junior|Senior|Graduate)', text, re.IGNORECASE)
    if class_match:
        result["classification"] = class_match.group(1).strip().title()

    # --- Level ---
    level_match = re.search(r'Level[:\s]*\n?\s*(Undergraduate|Graduate)', text, re.IGNORECASE)
    if level_match:
        result["level"] = level_match.group(1).strip()

    # --- Status ---
    status_match = re.search(r'Status[:\s]*\n?\s*(Active|Inactive|Continuing)', text, re.IGNORECASE)
    if status_match:
        result["status"] = status_match.group(1).strip()

    # --- Standing ---
    standing_match = re.search(r'Standing[:\s]*\n?\s*(Good Standing[^,\n]*)', flat, re.IGNORECASE)
    if standing_match:
        result["standing"] = standing_match.group(1).strip()

    # --- Degree ---
    # Match just the degree type: "Bachelor of Science", "Master of Science", etc.
    degree_match = re.search(r'Degree[:\s]*\n?\s*((?:Bachelor|Master|Associate|Doctor)\s+of\s+\w+)', text, re.IGNORECASE)
    if degree_match:
        result["degree"] = degree_match.group(1).strip()

    # --- Major ---
    major_match = re.search(r'Major[:\s]*\n?\s*([A-Z][a-zA-Z\s]{3,40}?)(?:\n|$|Not Provided|Department|Concentration)', text)
    if major_match:
        major = major_match.group(1).strip()
        if major not in ("Not Provided", "Primary", "Secondary") and len(major) > 2:
            result["major"] = major

    # Build degree_program from degree + major
    if result.get("degree") and result.get("major"):
        result["degree_program"] = f"{result['degree']} in {result['major']}"
    elif result.get("degree"):
        result["degree_program"] = result["degree"]
    elif result.get("major"):
        result["degree_program"] = result["major"]

    # --- College ---
    college_match = re.search(r'College[:\s]*\n?\s*([A-Z][^\n]{5,60})', text)
    if college_match:
        college = college_match.group(1).strip()
        if college != "Not Provided":
            result["college"] = college

    # --- Program ---
    program_match = re.search(r'Program[:\s]*\n?\s*([A-Z][a-zA-Z\s]{3,50})', text)
    if program_match:
        prog = program_match.group(1).strip()
        if prog not in ("Not Provided",):
            result["program"] = prog

    # --- Advisor ---
    advisor_match = re.search(r'(?:Primary\s*/?\s*Major|Advisor)[:\s]*\n?\s*([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)', text)
    if advisor_match:
        result["advisor"] = advisor_match.group(1).strip()
    # Also try link text pattern
    if not result.get("advisor"):
        adv_link = re.search(r'Advisors.*?<a[^>]*>([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)</a>', html, re.DOTALL | re.IGNORECASE)
        if adv_link:
            result["advisor"] = adv_link.group(1).strip()

    # --- Student Type ---
    type_match = re.search(r'Student Type[:\s]*\n?\s*(Continuing|New|Transfer|Readmit)', text, re.IGNORECASE)
    if type_match:
        result["student_type"] = type_match.group(1).strip()

    # --- Residency ---
    res_match = re.search(r'Residency[:\s]*\n?\s*(Resident|Non-Resident|In-State|Out-of-State)', text, re.IGNORECASE)
    if res_match:
        result["residency"] = res_match.group(1).strip()

    # --- Catalog Term ---
    cat_match = re.search(r'Catalog Term[:\s]*\n?\s*((?:Fall|Spring|Summer)\s+\d{4})', text, re.IGNORECASE)
    if cat_match:
        result["catalog_year"] = cat_match.group(1).strip()

    # --- Admit Term ---
    admit_match = re.search(r'Admit Term[:\s]*\n?\s*((?:Fall|Spring|Summer)\s+\d{4})', text, re.IGNORECASE)
    if admit_match:
        result["admit_term"] = admit_match.group(1).strip()

    # --- First Term Attended ---
    first_match = re.search(r'First Term Attended[:\s]*\n?\s*((?:Fall|Spring|Summer)\s+\d{4})', text, re.IGNORECASE)
    if first_match:
        result["first_term"] = first_match.group(1).strip()

    print(f"[PARSER] Profile extracted: {list(result.keys())}")
    return result


def _parse_profile_json(data: dict) -> dict:
    """Parse JSON profile (fallback)."""
    if not data:
        return {}
    return {
        "name": data.get("name") or data.get("displayName") or "",
        "student_id": str(data.get("bannerId") or data.get("studentId") or ""),
        "email": data.get("email") or "",
        "classification": data.get("classification") or "",
        "major": data.get("major") or "",
        "degree_program": data.get("degree") or "",
        "advisor": data.get("advisor") or "",
    }


# ----------------------------------------------------------------------
# Current Registration
# ----------------------------------------------------------------------

def parse_registration(raw: dict) -> dict:
    """Parse registration response. The registration landing page is just a menu,
    so we won't get course data from it. Return empty."""
    if not raw:
        return {}
    # The registration page at MSU is a menu page, not a schedule.
    # Actual schedule data would need clicking into "View Registration Information"
    # which loads via AJAX. For now, return what we can.
    data = raw.get("data", "")
    if raw.get("type") == "html" and isinstance(data, str):
        return _parse_registration_html(data)
    return {}


def _parse_registration_html(html: str) -> dict:
    """Try to extract any registration data from HTML."""
    clean = re.sub(r'<script[^>]*>.*?</script>', '', html, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'<[^>]+>', '\n', clean)
    text = re.sub(r'\s+', ' ', text).strip()

    courses = []
    # Look for course patterns
    for m in re.finditer(r'([A-Z]{2,4})\s+(\d{3})\s*[-:]\s*([^\d]{3,50}?)\s+(\d+\.?\d*)\s*(?:cr|hour)', text, re.IGNORECASE):
        courses.append({
            "subject": m.group(1), "number": m.group(2),
            "title": m.group(3).strip(), "credits": _to_float(m.group(4)),
            "crn": "", "instructor": "", "times": "", "location": "",
        })

    term = ""
    term_match = re.search(r'(Fall|Spring|Summer)\s+\d{4}', text, re.IGNORECASE)
    if term_match:
        term = term_match.group(0)

    total = sum(c["credits"] for c in courses if c["credits"])
    return {"current_term": term, "courses": courses, "total_credits": total}


# ----------------------------------------------------------------------
# Registration History
# ----------------------------------------------------------------------

def parse_registration_history(raw: dict) -> list:
    """Parse registration history HTML (can be very large)."""
    if not raw:
        return []
    data = raw.get("data", "")
    if raw.get("type") == "html" and isinstance(data, str):
        return _parse_reg_history_html(data)
    return []


def _parse_reg_history_html(html: str) -> list:
    """Extract registration history from Banner HTML."""
    clean = re.sub(r'<script[^>]*>.*?</script>', '', html, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'<[^>]+>', '\n', clean)
    flat = re.sub(r'\s+', ' ', text).strip()

    history = []
    # Look for term headers like "Fall 2025", "Spring 2024"
    terms = re.findall(r'((?:Fall|Spring|Summer)\s+\d{4})', flat, re.IGNORECASE)
    # Deduplicate while preserving order
    seen = set()
    unique_terms = []
    for t in terms:
        t_norm = t.strip().title()
        if t_norm not in seen:
            seen.add(t_norm)
            unique_terms.append(t_norm)

    for term in unique_terms:
        history.append({
            "term": term,
            "courses": [],
            "term_gpa": None,
            "credits_attempted": None,
            "credits_earned": None,
        })

    return history


# ----------------------------------------------------------------------
# Grades / Academic Transcript
# ----------------------------------------------------------------------

def parse_grades(raw: dict) -> dict:
    """Parse grades/transcript HTML."""
    if not raw:
        return {}
    data = raw.get("data", "")
    if raw.get("type") == "html" and isinstance(data, str):
        return _parse_grades_html(data)
    elif isinstance(data, dict):
        return {"grade_history": [], "cumulative_gpa": _to_float(data.get("gpa")),
                "total_credits_earned": None, "total_credits_attempted": None, "deans_list_terms": []}
    return {}


def _parse_grades_html(html: str) -> dict:
    """
    Extract grades from Banner academic transcript HTML.
    This page is large (~1.3MB) and contains all course history.
    """
    clean = re.sub(r'<script[^>]*>.*?</script>', '', html, flags=re.DOTALL | re.IGNORECASE)
    clean = re.sub(r'<style[^>]*>.*?</style>', '', clean, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'<[^>]+>', '\n', clean)
    flat = re.sub(r'\s+', ' ', text).strip()

    result = {
        "grade_history": [],
        "cumulative_gpa": None,
        "total_credits_earned": None,
        "total_credits_attempted": None,
        "deans_list_terms": [],
    }

    # --- Cumulative GPA ---
    for pattern in [
        r'(?:Cumulative|Overall|Career|Institutional)\s+GPA[:\s]*(\d+\.\d+)',
        r'GPA[:\s]*(\d+\.\d+)',
    ]:
        m = re.search(pattern, flat, re.IGNORECASE)
        if m:
            gpa = float(m.group(1))
            if 0 < gpa <= 4.0:
                result["cumulative_gpa"] = gpa
                break

    # --- Total Credits ---
    for pattern in [
        r'(?:Total|Cumulative|Overall)\s+(?:Credit|Hour)s?\s+(?:Earned|Completed)[:\s]*(\d+\.?\d*)',
        r'Overall\s+Hours[:\s]*(\d+\.?\d*)',
    ]:
        m = re.search(pattern, flat, re.IGNORECASE)
        if m:
            result["total_credits_earned"] = float(m.group(1))
            break

    # --- Course grades: pattern like "COSC 470 A 3.000" or "COSC 354 Software Engineering A 3" ---
    dept_prefixes = r'(?:COSC|MATH|CLCO|EEGR|INSS|PHYS|BIOL|CHEM|ENGL|HIST|PSYC|PHIL|HLTH|WGST|FIN|ORTR|THEA|PHEC|ACCT|ECON|MUSC|SOCI|POLI|BUAD|MGMT|MKTG|SPCH|FREN|SPAN|GERM|ART|KNES)'
    course_grades = []
    for m in re.finditer(
        rf'({dept_prefixes})\s+(\d{{3}})\s+([A-Za-z][\w\s]{{0,40}}?)\s+([ABCDF][+-]?|W|IP|AU|S|U|P|NP|TRA|TRB|TRC|TRD)\s+(\d+\.?\d*)',
        flat, re.IGNORECASE
    ):
        code = f"{m.group(1)} {m.group(2)}"
        title = m.group(3).strip()
        grade = m.group(4).upper()
        credits = _to_float(m.group(5))
        # Skip if title looks like a grade or number
        if len(title) > 2 and not title[0].isdigit():
            course_grades.append({"code": code, "title": title, "grade": grade, "credits": credits})

    # --- Group courses by term ---
    # Find term sections in the text
    term_sections = re.split(r'((?:Fall|Spring|Summer)\s+\d{4})', flat, flags=re.IGNORECASE)

    grade_history = []
    current_term = None
    deans_list = []

    for i, section in enumerate(term_sections):
        term_match = re.match(r'(Fall|Spring|Summer)\s+(\d{4})', section, re.IGNORECASE)
        if term_match:
            current_term = f"{term_match.group(1).title()} {term_match.group(2)}"
            # Get the next section (content after this term header)
            if i + 1 < len(term_sections):
                content = term_sections[i + 1]

                # Extract term GPA
                term_gpa = None
                gpa_m = re.search(r'(?:Term|Semester)\s+GPA[:\s]*(\d+\.\d+)', content, re.IGNORECASE)
                if gpa_m:
                    term_gpa = float(gpa_m.group(1))

                # Extract courses in this term section
                term_courses = []
                for cm in re.finditer(
                    rf'({dept_prefixes})\s+(\d{{3}})\s+([A-Za-z][\w\s]{{0,40}}?)\s+([ABCDF][+-]?|W|IP|AU|S|U|P|TRA|TRB|TRC)\s+(\d+\.?\d*)',
                    content, re.IGNORECASE
                ):
                    term_courses.append({
                        "code": f"{cm.group(1)} {cm.group(2)}",
                        "title": cm.group(3).strip(),
                        "grade": cm.group(4).upper(),
                        "credits": _to_float(cm.group(5)),
                    })

                if term_courses or term_gpa:
                    grade_history.append({
                        "term": current_term,
                        "courses": term_courses,
                        "term_gpa": term_gpa,
                    })

                # Check dean's list
                if re.search(r"dean'?s?\s+list", content, re.IGNORECASE):
                    deans_list.append(current_term)

    result["grade_history"] = grade_history
    result["deans_list_terms"] = deans_list

    # If we found course grades but couldn't group by term, store them flat
    if not grade_history and course_grades:
        result["grade_history"] = [{"term": "All Terms", "courses": course_grades, "term_gpa": None}]

    print(f"[PARSER] Grades extracted: GPA={result['cumulative_gpa']}, terms={len(result['grade_history'])}, "
          f"courses={sum(len(t.get('courses',[])) for t in result['grade_history'])}")
    return result


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------

def _to_float(val) -> float | None:
    if val is None:
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None
