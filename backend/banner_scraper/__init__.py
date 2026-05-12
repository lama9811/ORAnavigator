# backend/banner_scraper/__init__.py
"""
Banner / DegreeWorks integration module.
Authenticates via WSO2 SSO, then calls DegreeWorks REST API
to get the full degree audit as JSON. Also fetches Student Profile HTML.
"""

import json
import base64
from banner_scraper.cas_auth import cas_authenticate

# DegreeWorks API base
DW_API_BASE = "https://ndwpjasrv.morgan.edu:9904/Dashboard/api"
DW_AUDIT_ACCEPT = "application/vnd.net.hedtech.degreeworks.dashboard.audit.v1+json"


def _extract_student_id_from_cookies(client) -> str | None:
    """Extract studentId from the X-AUTH-TOKEN JWT cookie."""
    for cookie in client.cookies.jar:
        if cookie.name == "X-AUTH-TOKEN":
            try:
                # JWT is Bearer+<token>, strip the prefix
                token = cookie.value.replace("Bearer+", "").replace("Bearer ", "")
                # Decode JWT payload (base64, no verification needed)
                payload = token.split(".")[1]
                # Add padding
                payload += "=" * (4 - len(payload) % 4)
                decoded = json.loads(base64.urlsafe_b64decode(payload))
                return decoded.get("sub") or decoded.get("internalId")
            except Exception:
                continue
    return None


async def sync_banner(username: str, password: str, progress_callback=None):
    """
    Full sync: WSO2 auth -> DegreeWorks JSON API + Student Profile HTML.
    """
    async def _progress(step, detail=""):
        if progress_callback:
            await progress_callback(step, detail)

    # Step 1: Authenticate to DegreeWorks via WSO2 SSO
    await _progress("auth", "Logging into MSU...")
    client = await cas_authenticate(username, password, service="degreeworks")

    results = {}
    student_id = None

    # Step 2: Get student ID - try /api/myself first, fallback to JWT cookie
    await _progress("profile", "Fetching student info...")
    try:
        resp = await client.get(
            f"{DW_API_BASE}/myself",
            headers={"Accept": "application/json"},
        )
        if resp.status_code == 200:
            myself = resp.json()
            student_id = myself.get("studentId") or myself.get("id") or myself.get("bannerId")
            print(f"[BANNER] /api/myself: studentId={student_id}, data={list(myself.keys())}")
            results["myself"] = myself
        else:
            print(f"[BANNER] /api/myself returned HTTP {resp.status_code}")
    except Exception as e:
        print(f"[BANNER] /api/myself failed: {e}")

    # Fallback: extract from JWT cookie
    if not student_id:
        student_id = _extract_student_id_from_cookies(client)
        if student_id:
            print(f"[BANNER] Got studentId from JWT cookie: {student_id}")
        else:
            print("[BANNER] Could not determine studentId from API or cookies")

    # Step 3: Fetch the full degree audit JSON
    if student_id:
        await _progress("degreeworks", "Fetching DegreeWorks audit...")
        try:
            audit_url = (
                f"{DW_API_BASE}/audit"
                f"?studentId={student_id}"
                f"&school=UG&degree=BS"
                f"&is-process-new=false"
                f"&audit-type=AA"
                f"&auditId="
                f"&include-inprogress=true"
                f"&include-preregistered=true"
                f"&aid-term="
            )
            resp = await client.get(
                audit_url,
                headers={"Accept": DW_AUDIT_ACCEPT},
            )
            if resp.status_code == 200:
                audit_json = resp.json()
                results["degreeworks_json"] = audit_json
                top_keys = list(audit_json.keys()) if isinstance(audit_json, dict) else type(audit_json).__name__
                print(f"[BANNER] DegreeWorks audit: {len(resp.text)} chars, keys={top_keys}")
                # Debug: save JSON structure to temp file
                try:
                    import json as _json
                    with open("/tmp/dw_audit_sample.json", "w") as f:
                        _json.dump(audit_json, f, indent=2, default=str)
                    print("[BANNER] Saved audit JSON to /tmp/dw_audit_sample.json")
                except Exception as de:
                    print(f"[BANNER] Could not save debug JSON: {de}")
            else:
                print(f"[BANNER] DegreeWorks audit: HTTP {resp.status_code}")
                # Try to read error body
                try:
                    print(f"[BANNER] Response: {resp.text[:500]}")
                except:
                    pass
                results["degreeworks_json"] = None
        except Exception as e:
            print(f"[BANNER] DegreeWorks audit error: {e}")
            results["degreeworks_json"] = None
    else:
        results["degreeworks_json"] = None

    # Step 4: Also fetch Student Profile from Banner SSB
    await _progress("banner_profile", "Fetching Banner profile...")
    try:
        banner_client = await cas_authenticate(username, password, service="banner")
        profile_url = "https://lbssb1nprod.morgan.edu/StudentSelfService/ssb/StudentProfile/"
        resp = await banner_client.get(profile_url)
        if resp.status_code == 200 and len(resp.text) > 1000:
            results["profile_html"] = resp.text
            print(f"[BANNER] Profile HTML: {len(resp.text)} chars")
        else:
            results["profile_html"] = None
        await banner_client.aclose()
    except Exception as e:
        print(f"[BANNER] Profile fetch failed: {e}")
        results["profile_html"] = None

    await _progress("done", "Sync complete!")
    await client.aclose()

    return results
