"""Calendar (.ics) export for proposal deadlines.

Two concerns, no FastAPI/DB dependency so it unit-tests in isolation:

1. A scoped token (mint/decode). Calendar clients fetch the feed URL with
   no Authorization header, so we authenticate via a query-param JWT. The
   token carries `sub` (user_id) + `scope="ics"` and DELIBERATELY no
   `email` claim -- get_current_user authenticates on `email` (deps.py:48),
   so this token is useless as a normal bearer (replay-safe).
2. An RFC 5545 VCALENDAR builder over a list of (deadline-bearing) Submissions.
"""
from datetime import datetime, timezone
from typing import Optional

from jose import jwt, JWTError

from security import JWT_SECRET, ALGORITHM

_ICS_SCOPE = "ics"
_CAL_TZID = "America/New_York"   # matches the deadline-watcher cron tz
_PRODID = "-//ORA Navigator//Deadlines//EN"


def mint_ics_token(user_id: int) -> str:
    """Long-lived (no expiry) calendar token. No `email` claim -> not a
    valid auth bearer. `sub` is stored as a string per the JWT spec
    (RFC 7519 §4.1.2 and jose's StringOrURI constraint)."""
    return jwt.encode(
        {"sub": str(user_id), "scope": _ICS_SCOPE}, JWT_SECRET, algorithm=ALGORITHM
    )


def decode_ics_token(token: str) -> Optional[int]:
    """Return the user_id for a valid ics-scoped token, else None."""
    if not token:
        return None
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[ALGORITHM])
    except JWTError:
        return None
    if payload.get("scope") != _ICS_SCOPE:
        return None
    sub = payload.get("sub")
    return int(sub) if sub is not None else None


def _escape(text: str) -> str:
    """RFC 5545 text escaping: backslash, comma, semicolon, newline."""
    if text is None:
        return ""
    return (
        str(text)
        .replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\r\n", "\\n")
        .replace("\n", "\\n")
    )


def build_calendar(submissions: list) -> str:
    """Build a VCALENDAR string with one all-day VEVENT per submission that
    has a deadline. Submissions without a deadline are skipped. Always
    returns a valid (possibly event-less) calendar."""
    now = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        f"PRODID:{_PRODID}",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "X-WR-CALNAME:ORA Proposal Deadlines",
    ]
    for s in submissions:
        if not getattr(s, "deadline", None):
            continue
        day = s.deadline.strftime("%Y%m%d")           # all-day event
        sponsor = (getattr(s, "sponsor", "") or "").strip()
        title = (getattr(s, "title", "") or "Proposal").strip()
        summary = f"{sponsor}: {title}" if sponsor else title
        lines += [
            "BEGIN:VEVENT",
            f"UID:submission-{s.id}@ora.inavigator.ai",
            f"DTSTAMP:{now}",
            f"DTSTART;VALUE=DATE:{day}",
            f"SUMMARY:{_escape(summary)} (deadline)",
            f"DESCRIPTION:{_escape('Proposal deadline tracked in ORA Navigator.')}",
            "END:VEVENT",
        ]
    lines.append("END:VCALENDAR")
    # RFC 5545 requires CRLF line endings.
    return "\r\n".join(lines) + "\r\n"
