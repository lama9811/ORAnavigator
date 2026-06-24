"""Regression: a pasted/hard-wrapped draft has newlines inside sentences, but
Gemini quotes evidence with single spaces. _verify_evidence must collapse
whitespace on both sides before the substring match, or every 'covered'
element gets wrongly demoted to NOT FOUND (see the Project Summary bug)."""
import os

os.environ.setdefault("JWT_SECRET", "test-secret")
os.environ.setdefault("TRUSTED_HOSTS", "testserver,localhost,127.0.0.1")

from services.section_coach import _verify_evidence

WRAPPED_DRAFT = (
    "Overview: This project will design a planning framework for a national-scale\n"
    "Integrated Data System that brings together environmental, health,\n"
    "and socioeconomic datasets.\n\n"
    "Intellectual Merit: The work advances data cyberinfrastructure by developing\n"
    "shared standards for linking environmental-exposure data."
)


def test_wrapped_draft_evidence_stays_covered():
    # Gemini returns the quote with single spaces (no newlines)
    checklist = [
        {"item": "Overview", "status": "covered", "note": "clear",
         "evidence": "This project will design a planning framework for a national-scale Integrated Data System"},
        {"item": "Intellectual Merit", "status": "covered", "note": "present",
         "evidence": "The work advances data cyberinfrastructure by developing shared standards"},
    ]
    out = _verify_evidence(checklist, WRAPPED_DRAFT)
    assert all(c["status"] == "covered" for c in out), out


def test_fabricated_evidence_is_still_dropped():
    checklist = [
        {"item": "made up", "status": "covered", "note": "x",
         "evidence": "this sentence is nowhere in the draft"},
    ]
    out = _verify_evidence(checklist, WRAPPED_DRAFT)
    assert out[0]["status"] == "unclear"
    assert out[0]["evidence"] == ""
