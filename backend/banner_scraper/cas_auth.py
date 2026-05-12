# backend/banner_scraper/cas_auth.py
"""
Morgan State University SSO authentication via WSO2 Identity Server.
Supports authenticating to multiple services (Banner SSB, DegreeWorks)
through the same SSO at lbpsso.morgan.edu.

Flow:
1. GET target service URL -> follows redirect chain to WSO2 login page
2. Extract sessionDataKey from the login form
3. POST credentials to WSO2 /commonauth endpoint
4. Follow redirects back to target service (picks up session cookies)
5. Return authenticated httpx.AsyncClient with valid cookies
"""

import os
import re
import httpx

# WSO2 Identity Server base (SSO provider)
WSO2_BASE = os.getenv("WSO2_BASE", "https://lbpsso.morgan.edu")

# Service entry points
BANNER_SSB_DASHBOARD = os.getenv(
    "SSB_LOGIN_URL",
    "https://lbssb1nprod.morgan.edu/StudentSelfService/ssb/studentCommonDashboard"
)
DEGREEWORKS_URL = os.getenv(
    "DEGREEWORKS_URL",
    "https://ndwpjasrv.morgan.edu:9904/Dashboard/worksheets/WEB31"
)

# Request timeout (seconds)
AUTH_TIMEOUT = int(os.getenv("CAS_TIMEOUT", "45"))


async def cas_authenticate(username: str, password: str, service: str = "degreeworks") -> httpx.AsyncClient:
    """
    Authenticate against MSU WSO2 Identity Server and return an
    httpx.AsyncClient with session cookies valid for the requested service.

    Args:
        username: MSU username (e.g., 'aashr3')
        password: MSU password
        service: 'degreeworks' or 'banner' - which service to authenticate to

    Raises:
        ValueError: On invalid credentials or auth errors.
    """
    # Pick target URL based on service
    target_url = DEGREEWORKS_URL if service == "degreeworks" else BANNER_SSB_DASHBOARD

    client = httpx.AsyncClient(
        follow_redirects=True,
        timeout=httpx.Timeout(AUTH_TIMEOUT),
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        },
    )

    try:
        # Step 1: Hit target URL -> follows redirect chain to WSO2 login page
        print(f"[AUTH] Step 1: Hitting {service} URL: {target_url}")
        resp = await client.get(target_url)
        resp.raise_for_status()

        html = resp.text
        final_url = str(resp.url)
        print(f"[AUTH] Landed on: {final_url[:120]}...")

        # Step 2: Extract sessionDataKey and form action from WSO2 login page
        session_data_key = _extract_field(html, "sessionDataKey")
        form_action = _extract_form_action(html)

        if not session_data_key:
            # Try extracting from the URL (WSO2 puts it there too)
            sdk_match = re.search(r'sessionDataKey=([^&"\']+)', final_url)
            if sdk_match:
                session_data_key = sdk_match.group(1)

        if not session_data_key:
            # Maybe we're already authenticated (redirected past login)
            if "Dashboard" in final_url or "studentCommon" in final_url:
                print(f"[AUTH] Already authenticated to {service}")
                return client
            # Try HTML body as last resort
            sdk_match = re.search(r'sessionDataKey=([^&"\']+)', html)
            if sdk_match:
                session_data_key = sdk_match.group(1)
            else:
                raise ValueError(
                    "Could not reach MSU login page. "
                    "The SSO server may be down. Please try again later."
                )

        # Determine POST URL
        if form_action:
            if form_action.startswith("http"):
                post_url = form_action
            elif form_action.startswith("../"):
                post_url = f"{WSO2_BASE}/{form_action.lstrip('./')}"
            elif form_action.startswith("/"):
                post_url = f"{WSO2_BASE}{form_action}"
            else:
                post_url = f"{WSO2_BASE}/{form_action}"
        else:
            post_url = f"{WSO2_BASE}/commonauth"

        print(f"[AUTH] Step 2: Posting credentials to: {post_url}")

        # Step 3: POST credentials to WSO2
        resp = await client.post(
            post_url,
            data={
                "username": username,
                "password": password,
                "sessionDataKey": session_data_key,
            },
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Referer": final_url,
            },
        )
        resp.raise_for_status()

        # Step 4: Check if login succeeded
        response_url = str(resp.url)

        if "authenticationendpoint/login.do" in response_url:
            raise ValueError("Invalid MSU credentials. Please check your username and password.")

        if "authFailure=true" in response_url:
            raise ValueError("Invalid MSU credentials. Please check your username and password.")

        print(f"[AUTH] Final URL: {response_url[:120]}...")
        print(f"[AUTH] Cookies: {[c.name for c in client.cookies.jar]}")
        print(f"[AUTH] Authentication successful for {service} (user: {username})")

        return client

    except httpx.HTTPStatusError as e:
        await client.aclose()
        raise ValueError(f"SSO server error (HTTP {e.response.status_code}). Please try again later.")
    except ValueError:
        await client.aclose()
        raise
    except Exception as e:
        await client.aclose()
        raise ValueError(f"Authentication failed: {str(e)}")


def _extract_field(html: str, field_name: str) -> str | None:
    """Extract a hidden form field value from HTML."""
    patterns = [
        rf'name="{field_name}"\s+value="([^"]*)"',
        rf"name='{field_name}'\s+value='([^']*)'",
        rf'name="{field_name}"[^>]*value="([^"]*)"',
        rf'value="([^"]*)"\s+name="{field_name}"',
    ]
    for pattern in patterns:
        match = re.search(pattern, html, re.IGNORECASE | re.DOTALL)
        if match:
            return match.group(1)
    return None


def _extract_form_action(html: str) -> str | None:
    """Extract the form action URL from the login page."""
    patterns = [
        r'<form[^>]*id="loginForm"[^>]*action="([^"]*)"',
        r'<form[^>]*action="([^"]*commonauth[^"]*)"',
        r'<form[^>]*action="([^"]*)"[^>]*method="post"',
    ]
    for pattern in patterns:
        match = re.search(pattern, html, re.IGNORECASE | re.DOTALL)
        if match:
            return match.group(1).replace("&amp;", "&")
    return None
