# backend/banner_scraper/client.py
"""
Authenticated Banner Student Self Service client for Morgan State University.
Uses an httpx.AsyncClient (from cas_auth) with valid session cookies
to fetch data from Banner SSB pages.

MSU-specific URLs (discovered from the actual portal):
- Student Profile: /StudentSelfService/ssb/StudentProfile/
- Registration: /StudentRegistrationSsb/ssb/registration
- Student Dashboard: /StudentSelfService/ssb/studentCommonDashboard
- View Grades: /StudentSelfService/ssb/grades (or similar)
"""

import os
import re
import json
import httpx

BANNER_SSB_BASE = os.getenv(
    "BANNER_SSB_BASE",
    "https://lbssb1nprod.morgan.edu"
)


class BannerClient:
    """Authenticated client for Morgan State Banner SSB."""

    def __init__(self, session: httpx.AsyncClient):
        self.session = session
        self.base = BANNER_SSB_BASE

    async def close(self):
        """Close the underlying HTTP session."""
        await self.session.aclose()

    # ------------------------------------------------------------------
    # DegreeWorks
    # ------------------------------------------------------------------

    async def get_degreeworks(self) -> dict:
        """
        Fetch DegreeWorks audit page HTML.
        DegreeWorks is on a separate server but shares the SSO session.
        """
        urls = [
            "https://ndwpjasrv.morgan.edu:9904/Dashboard/worksheets/WEB31",
            "https://ndwpjasrv.morgan.edu:9904/Dashboard",
        ]

        for url in urls:
            try:
                resp = await self.session.get(url, timeout=httpx.Timeout(30.0))
                if resp.status_code == 200:
                    html = resp.text
                    if html and len(html) > 500:
                        print(f"[BANNER] DegreeWorks HTML from: {url} ({len(html)} chars)")
                        return {"type": "html", "data": html, "url": url}
            except Exception as e:
                print(f"[BANNER] DegreeWorks error at {url}: {e}")
                continue

        print("[BANNER] Could not fetch DegreeWorks from any endpoint")
        return {}

    # ------------------------------------------------------------------
    # Student Profile
    # ------------------------------------------------------------------

    async def get_student_profile(self) -> dict:
        """
        Fetch student profile from Banner Student Profile page.
        Tries the profile page and extracts data from HTML or JSON.
        """
        urls = [
            f"{self.base}/StudentSelfService/ssb/StudentProfile/",
            f"{self.base}/StudentSelfService/ssb/StudentProfile/studentProfile",
            f"{self.base}/StudentSelfService/ssb/studentProfile/studentProfile",
            f"{self.base}/StudentSelfService/ssb/studentCommonDashboard",
        ]

        for url in urls:
            try:
                resp = await self.session.get(url)
                if resp.status_code == 200:
                    content_type = resp.headers.get("content-type", "")
                    if "json" in content_type:
                        data = resp.json()
                        if data:
                            print(f"[BANNER] Profile JSON from: {url}")
                            return {"type": "json", "data": data, "url": url}
                    else:
                        html = resp.text
                        if html and len(html) > 200:
                            print(f"[BANNER] Profile HTML from: {url} ({len(html)} chars)")
                            return {"type": "html", "data": html, "url": url}
            except Exception as e:
                print(f"[BANNER] Profile error at {url}: {e}")
                continue

        # Try the dashboard page (we know this works, it has the student name)
        try:
            resp = await self.session.get(f"{self.base}/StudentSelfService/ssb/studentCommonDashboard")
            if resp.status_code == 200:
                print(f"[BANNER] Using dashboard HTML for profile")
                return {"type": "html", "data": resp.text, "url": "dashboard"}
        except Exception as e:
            print(f"[BANNER] Dashboard error: {e}")

        return {}

    # ------------------------------------------------------------------
    # Current Registration
    # ------------------------------------------------------------------

    async def get_current_registration(self) -> dict:
        """
        Fetch current registration/schedule.
        MSU uses a separate app: /StudentRegistrationSsb/
        """
        urls = [
            # MSU's registration app
            f"{self.base}/StudentRegistrationSsb/ssb/registration",
            f"{self.base}/StudentRegistrationSsb/ssb/registration/registeredCourses",
            f"{self.base}/StudentRegistrationSsb/ssb/searchResults/getStudentSchedule",
            # Try under main SSB too
            f"{self.base}/StudentSelfService/ssb/registration",
            f"{self.base}/StudentSelfService/ssb/registration/registeredCourses",
        ]

        for url in urls:
            try:
                resp = await self.session.get(url)
                if resp.status_code == 200:
                    content_type = resp.headers.get("content-type", "")
                    if "json" in content_type:
                        data = resp.json()
                        if data:
                            print(f"[BANNER] Registration JSON from: {url}")
                            return {"type": "json", "data": data, "url": url}
                    else:
                        html = resp.text
                        if html and len(html) > 200:
                            print(f"[BANNER] Registration HTML from: {url} ({len(html)} chars)")
                            return {"type": "html", "data": html, "url": url}
            except Exception as e:
                print(f"[BANNER] Registration error at {url}: {e}")
                continue

        return {}

    # ------------------------------------------------------------------
    # Registration History / Grades
    # ------------------------------------------------------------------

    async def get_registration_history(self) -> dict:
        """Fetch registration history."""
        urls = [
            f"{self.base}/StudentSelfService/ssb/registrationHistory",
            f"{self.base}/StudentSelfService/ssb/registrationHistory/list",
            f"{self.base}/StudentSelfService/ssb/registrationHistory/registrationHistory",
        ]

        for url in urls:
            try:
                resp = await self.session.get(url)
                if resp.status_code == 200:
                    content_type = resp.headers.get("content-type", "")
                    if "json" in content_type:
                        data = resp.json()
                        if data:
                            print(f"[BANNER] Reg history JSON from: {url}")
                            return {"type": "json", "data": data, "url": url}
                    else:
                        html = resp.text
                        if html and len(html) > 200:
                            print(f"[BANNER] Reg history HTML from: {url} ({len(html)} chars)")
                            return {"type": "html", "data": html, "url": url}
            except Exception as e:
                continue

        return {}

    # ------------------------------------------------------------------
    # Grades
    # ------------------------------------------------------------------

    async def get_grades(self) -> dict:
        """
        Fetch grade history. Tries multiple known Ellucian endpoints.
        """
        urls = [
            f"{self.base}/StudentSelfService/ssb/grades",
            f"{self.base}/StudentSelfService/ssb/grades/getGrades",
            f"{self.base}/StudentSelfService/ssb/academicTranscript",
            f"{self.base}/StudentSelfService/ssb/gradeHistory",
        ]

        for url in urls:
            try:
                resp = await self.session.get(url)
                if resp.status_code == 200:
                    content_type = resp.headers.get("content-type", "")
                    if "json" in content_type:
                        data = resp.json()
                        if data:
                            print(f"[BANNER] Grades JSON from: {url}")
                            return {"type": "json", "data": data, "url": url}
                    else:
                        html = resp.text
                        if html and len(html) > 200:
                            print(f"[BANNER] Grades HTML from: {url} ({len(html)} chars)")
                            return {"type": "html", "data": html, "url": url}
            except Exception as e:
                continue

        return {}
