"""Regression: Fundability must collapse whitespace before verifying a quote,
or a hard-wrapped pasted draft demotes every strong/adequate rating to
'unclear' (same class of bug as the section_coach fix)."""
import os
os.environ.setdefault("JWT_SECRET", "test-secret")
os.environ.setdefault("TRUSTED_HOSTS", "testserver,localhost,127.0.0.1")
from services.fundability import _verify

WRAPPED = ("The work advances data cyberinfrastructure by developing\n"
           "shared standards for linking environmental-exposure data with\n"
           "community-health outcomes at national scale.")

def test_wrapped_draft_keeps_strong_rating():
    res = [{"key": "im", "label": "Intellectual Merit", "rating": "strong",
            "comment": "ok", "fix": "",
            "evidence": "The work advances data cyberinfrastructure by developing shared standards"}]
    out = _verify(res, WRAPPED)
    assert out[0]["rating"] == "strong", out

def test_fabricated_quote_demoted():
    res = [{"key": "im", "label": "Intellectual Merit", "rating": "strong",
            "comment": "x", "fix": "", "evidence": "nowhere in the draft at all"}]
    out = _verify(res, WRAPPED)
    assert out[0]["rating"] == "unclear" and out[0]["evidence"] == ""
