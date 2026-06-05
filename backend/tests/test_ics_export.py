"""Unit tests for the .ics export module (no FastAPI / no DB).

The security contract: the calendar token carries `sub` + `scope` but NO
`email` claim, so it cannot be replayed as a normal auth bearer
(get_current_user authenticates on `email`, deps.py:48).
"""
import pytest
from jose import jwt

from services import ics_export
from security import JWT_SECRET, ALGORITHM


def test_mint_then_decode_roundtrips_user_id():
    tok = ics_export.mint_ics_token(42)
    assert ics_export.decode_ics_token(tok) == 42


def test_minted_token_has_no_email_claim():
    tok = ics_export.mint_ics_token(7)
    payload = jwt.decode(tok, JWT_SECRET, algorithms=[ALGORITHM])
    assert "email" not in payload          # replay-safety invariant
    assert payload["scope"] == "ics"
    assert payload["sub"] == "7"           # JWT spec: sub is StringOrURI


def test_decode_rejects_wrong_scope():
    bad = jwt.encode({"sub": 1, "scope": "auth"}, JWT_SECRET, algorithm=ALGORITHM)
    assert ics_export.decode_ics_token(bad) is None


def test_decode_rejects_garbage():
    assert ics_export.decode_ics_token("not.a.jwt") is None


def test_decode_rejects_empty():
    assert ics_export.decode_ics_token("") is None


from datetime import datetime as _dt
from types import SimpleNamespace


def _sub(id, title, sponsor, deadline):
    return SimpleNamespace(id=id, title=title, sponsor=sponsor, deadline=deadline)


def test_build_calendar_one_vevent_per_deadline():
    subs = [
        _sub(1, "NSF CAREER", "NSF", _dt(2026, 7, 1)),
        _sub(2, "No deadline", "NIH", None),   # skipped
    ]
    cal = ics_export.build_calendar(subs)
    assert cal.count("BEGIN:VEVENT") == 1
    assert "UID:submission-1@ora.inavigator.ai" in cal
    assert "DTSTART;VALUE=DATE:20260701" in cal
    assert "SUMMARY:NSF: NSF CAREER (deadline)" in cal
    assert cal.startswith("BEGIN:VCALENDAR")
    assert cal.rstrip().endswith("END:VCALENDAR")


def test_build_calendar_escapes_special_chars():
    subs = [_sub(3, "Title, with; chars", "", _dt(2026, 8, 9))]
    cal = ics_export.build_calendar(subs)
    assert "Title\\, with\\; chars" in cal


def test_build_calendar_empty_is_valid():
    cal = ics_export.build_calendar([])
    assert "BEGIN:VCALENDAR" in cal and "END:VCALENDAR" in cal
    assert "BEGIN:VEVENT" not in cal
