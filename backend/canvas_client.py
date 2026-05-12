"""
Canvas LMS Client for Morgan State University
================================================
Authenticates via LDAP login at morganstate.instructure.com
and pulls student data via Canvas REST API (read-only).

Data pulled:
- Current courses with grades
- Upcoming assignments and deadlines
- Missing/overdue submissions
- Course calendar events
"""

import json
import logging
import httpx
import re
from datetime import datetime, timezone

log = logging.getLogger(__name__)

CANVAS_BASE = "https://morganstate.instructure.com"
CANVAS_LOGIN_URL = f"{CANVAS_BASE}/login/ldap"
CANVAS_API = f"{CANVAS_BASE}/api/v1"
AUTH_TIMEOUT = 30
FETCH_TIMEOUT = 60


async def _paginated_get(client: httpx.AsyncClient, url: str, params: dict = None) -> list:
    """Follow Canvas Link header pagination to fetch all results."""
    results = []
    current_url = url
    current_params = params or {}
    while current_url:
        resp = await client.get(current_url, params=current_params, timeout=FETCH_TIMEOUT)
        if resp.status_code != 200:
            break
        results.extend(resp.json())
        # Canvas pagination: Link header contains rel="next" URL
        next_link = resp.headers.get("link", "")
        current_url = None
        current_params = {}  # params are baked into the next URL
        for part in next_link.split(","):
            if 'rel="next"' in part:
                current_url = part.split("<")[1].split(">")[0].strip()
                break
    return results


async def canvas_authenticate(username: str, password: str) -> httpx.AsyncClient:
    """Authenticate to Canvas via LDAP login form.
    Returns an authenticated httpx client with session cookies."""

    client = httpx.AsyncClient(
        follow_redirects=True,
        timeout=httpx.Timeout(AUTH_TIMEOUT),
        headers={"User-Agent": "CSNavigator/5.0"}
    )

    try:
        # Step 1: GET login page to extract CSRF token
        login_page = await client.get(CANVAS_LOGIN_URL)
        login_page.raise_for_status()

        # Extract authenticity_token from the login form
        html = login_page.text
        token_match = re.search(r'name="authenticity_token"\s+value="([^"]+)"', html)
        if not token_match:
            # Try alternative pattern
            token_match = re.search(r'"authenticity_token":"([^"]+)"', html)
        if not token_match:
            raise ValueError("Could not find CSRF token on Canvas login page")

        csrf_token = token_match.group(1)

        # Step 2: POST credentials
        login_data = {
            "utf8": "\u2713",
            "authenticity_token": csrf_token,
            "pseudonym_session[unique_id]": username,
            "pseudonym_session[password]": password,
            "pseudonym_session[remember_me]": "0",
        }

        response = await client.post(
            CANVAS_LOGIN_URL,
            data=login_data,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )

        # Step 3: Validate success
        final_url = str(response.url)
        if "login" in final_url and "success" not in final_url:
            raise ValueError("Invalid MSU credentials. Please check your username and password.")

        if response.status_code >= 400:
            raise ValueError(f"Canvas login failed with status {response.status_code}")

        log.info(f"[CANVAS] Authenticated as {username}")
        return client

    except httpx.HTTPStatusError as e:
        await client.aclose()
        raise ValueError(f"Canvas server error (HTTP {e.response.status_code})")
    except ValueError:
        await client.aclose()
        raise
    except Exception as e:
        await client.aclose()
        raise ValueError(f"Canvas authentication failed: {str(e)[:200]}")


async def fetch_canvas_data(client: httpx.AsyncClient, progress_callback=None) -> dict:
    """Fetch all student data from Canvas API using authenticated client.
    Returns structured data dict."""

    data = {
        "courses": [],
        "assignments": [],
        "missing": [],
        "grades": {},
        "gradebook": {},
        "profile": {},
    }

    try:
        # 1. Profile
        if progress_callback:
            await progress_callback("Fetching your Canvas profile...")
        resp = await client.get(f"{CANVAS_API}/users/self/profile")
        if resp.status_code == 200:
            p = resp.json()
            data["profile"] = {
                "canvas_id": p.get("id"),
                "name": p.get("name"),
                "login_id": p.get("login_id"),
                "email": p.get("primary_email"),
            }

        # 2. Current courses
        if progress_callback:
            await progress_callback("Fetching your courses...")
        resp = await client.get(f"{CANVAS_API}/courses", params={
            "enrollment_state": "active",
            "enrollment_type": "student",
            "include[]": ["total_scores", "current_grading_period_scores", "term"],
            "per_page": 50,
            "state[]": "available",
        })
        if resp.status_code == 200:
            courses = resp.json()

            # Determine current semester from course codes (e.g., "Spring 2026")
            # Pick the most recent semester string found in course codes
            import re as _re
            from datetime import datetime as _dt
            now = _dt.now()
            current_year = now.year
            semesters_found = {}
            for c in courses:
                code = c.get("course_code", "")
                match = _re.search(r'(Spring|Summer|Fall)\s*(\d{4})', code, _re.IGNORECASE)
                if match:
                    sem, year = match.group(1).title(), int(match.group(2))
                    sem_order = {"Spring": 0, "Summer": 1, "Fall": 2}
                    key = f"{sem} {year}"
                    semesters_found[key] = year * 10 + sem_order.get(sem, 0)

            # Current semester = the one with highest sort value
            current_sem = max(semesters_found, key=semesters_found.get) if semesters_found else None
            log.info(f"[CANVAS] Detected current semester: {current_sem} (from {len(semesters_found)} semesters)")

            for c in courses:
                code = c.get("course_code", "")
                # Only include courses from current semester
                if current_sem and current_sem not in code:
                    continue

                course_data = {
                    "id": c.get("id"),
                    "name": c.get("name"),
                    "code": c.get("course_code"),
                    "term": c.get("enrollment_term_id"),
                    "term_name": current_sem,
                }
                data["courses"].append(course_data)

                # Get grade for each course
                try:
                    grade_resp = await client.get(
                        f"{CANVAS_API}/courses/{c['id']}/enrollments",
                        params={"user_id": "self"}
                    )
                    if grade_resp.status_code == 200:
                        enrollments = grade_resp.json()
                        for e in enrollments:
                            if e.get("role") == "StudentEnrollment":
                                grades = e.get("grades", {})
                                data["grades"][c["id"]] = {
                                    "current_score": grades.get("current_score"),
                                    "current_grade": grades.get("current_grade"),
                                    "final_score": grades.get("final_score"),
                                    "final_grade": grades.get("final_grade"),
                                }
                except Exception:
                    pass

                # Get full gradebook: assignment groups + all assignments with submissions
                if progress_callback:
                    await progress_callback(f"Fetching gradebook for {c.get('name', 'course')}...")
                try:
                    cid = c["id"]
                    # Fetch assignment groups and assignments in parallel
                    import asyncio as _asyncio
                    groups_task = _paginated_get(
                        client,
                        f"{CANVAS_API}/courses/{cid}/assignment_groups",
                        {"per_page": 50}
                    )
                    assignments_task = _paginated_get(
                        client,
                        f"{CANVAS_API}/courses/{cid}/assignments",
                        {"per_page": 100, "include[]": "submission", "order_by": "due_at"}
                    )
                    ag_list, asn_list = await _asyncio.gather(groups_task, assignments_task)

                    # Determine grading type from assignment group weights
                    grading_type = "total_points"
                    parsed_groups = []
                    for ag in ag_list:
                        weight = ag.get("group_weight", 0)
                        if weight > 0:
                            grading_type = "weighted"
                        parsed_groups.append({
                            "id": ag.get("id"),
                            "name": ag.get("name", ""),
                            "weight": weight,
                        })

                    # Parse assignments with submission data
                    parsed_assignments = []
                    for asn in asn_list:
                        sub = asn.get("submission") or {}
                        parsed_assignments.append({
                            "id": asn.get("id"),
                            "name": asn.get("name", ""),
                            "due_at": asn.get("due_at"),
                            "points_possible": asn.get("points_possible"),
                            "assignment_group_id": asn.get("assignment_group_id"),
                            "submission": {
                                "score": sub.get("score"),
                                "submitted_at": sub.get("submitted_at"),
                                "late": sub.get("late", False),
                                "missing": sub.get("missing", False),
                                "workflow_state": sub.get("workflow_state", ""),
                            }
                        })

                    data["gradebook"][cid] = {
                        "grading_type": grading_type,
                        "assignment_groups": parsed_groups,
                        "assignments": parsed_assignments,
                    }
                except Exception as e:
                    log.warning(f"[CANVAS] Could not fetch gradebook for course {c.get('id')}: {e}")
                    # Skip gracefully - don't break sync for one restricted course

        # 3. Upcoming assignments (next 30 days)
        if progress_callback:
            await progress_callback("Fetching upcoming assignments...")
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        resp = await client.get(f"{CANVAS_API}/planner/items", params={
            "start_date": now,
            "per_page": 50,
        })
        if resp.status_code == 200:
            items = resp.json()
            for item in items:
                plannable = item.get("plannable", {})
                data["assignments"].append({
                    "title": plannable.get("title"),
                    "type": item.get("plannable_type"),
                    "due_at": plannable.get("due_at"),
                    "points": plannable.get("points_possible"),
                    "course_name": item.get("context_name"),
                    "course_id": item.get("course_id"),
                    "submitted": item.get("submissions", {}).get("submitted", False) if item.get("submissions") else False,
                    "url": item.get("html_url"),
                })

        # 4. Missing submissions
        if progress_callback:
            await progress_callback("Checking for missing assignments...")
        resp = await client.get(f"{CANVAS_API}/users/self/missing_submissions", params={
            "include[]": "planner_overrides",
            "filter[]": "submittable",
            "per_page": 20,
        })
        if resp.status_code == 200:
            missing = resp.json()
            for m in missing:
                data["missing"].append({
                    "title": m.get("name"),
                    "course_id": m.get("course_id"),
                    "due_at": m.get("due_at"),
                    "points": m.get("points_possible"),
                    "url": m.get("html_url"),
                })

        if progress_callback:
            await progress_callback("Canvas sync complete!")

        return data

    except Exception as e:
        log.error(f"[CANVAS] Data fetch error: {e}")
        raise


async def sync_canvas(username: str, password: str, progress_callback=None) -> dict:
    """Full Canvas sync: authenticate + fetch all data."""
    if progress_callback:
        await progress_callback("Logging into Canvas...")

    client = await canvas_authenticate(username, password)
    try:
        data = await fetch_canvas_data(client, progress_callback)
        return data
    finally:
        await client.aclose()


