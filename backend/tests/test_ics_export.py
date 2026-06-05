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
