"""Which extracted requirements earn a tick-box, and which belong to Draft Review.

A real solicitation yields ~45 requirements and most of them are content INSIDE
one section — "Background grounded in the literature", "General plan of work".
Rendered as a checklist that is an outline of the Project Description, not a
submission checklist, and Draft Review already checks every one of them against
the PI's actual draft. Putting them in both places makes the checklist
unscannable at the moment it matters most.

So a requirement becomes a task when it names something the PI PRODUCES (a plan,
a letter, a form) or a RULE they must obey (a page limit, a cap, a title
prefix). Everything else stays where it is already handled.

Measured against tests/fixtures/nsf_23_598.py — the only human-verified
requirement list in the repo, and therefore the only honest yardstick.
"""
import os
import sys

os.environ.setdefault("TRUSTED_HOSTS", "testserver,localhost,127.0.0.1")
os.environ.setdefault("JWT_SECRET", "test-secret-for-checklist-filter")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.checklist_filter import is_checklist_task
from tests.fixtures.nsf_23_598 import EIR_REQUIREMENTS


def _by_label(fragment):
    for r in EIR_REQUIREMENTS:
        if fragment.lower() in r["label"].lower():
            return r
    raise AssertionError(f"no fixture requirement matching {fragment!r}")


# ── content requirements stay out of the checklist ──────────────────────────

def test_narrative_content_does_not_become_a_tick_box():
    """These are what you write inside the Project Description. Draft Review
    reads the draft and checks them; a checkbox can only ask the PI to assert
    it, which is not a check at all."""
    for fragment in ("overall research goals",
                     "Background grounded in the literature",
                     "General plan of work",
                     "research questions or hypotheses",
                     "Plan for scholarly dissemination",
                     "How success will be determined"):
        assert is_checklist_task(_by_label(fragment)) is False, fragment


# ── things the PI produces, and rules they must obey, stay in ───────────────

def test_a_document_the_pi_must_produce_becomes_a_task():
    assert is_checklist_task(_by_label("Letter of Institutional Support")) is True


def test_a_page_limit_becomes_a_task():
    assert is_checklist_task(_by_label("support letter is 2 pages or fewer")) is True


def test_a_budget_cap_becomes_a_task():
    assert is_checklist_task(_by_label("Equipment is 30% or less")) is True


def test_a_required_title_prefix_becomes_a_task():
    assert is_checklist_task(_by_label('Title begins "Excellence in Research:"')) is True


def test_a_prohibition_becomes_a_task():
    """"No letters of support from collaborators" is a rule that gets proposals
    returned without review. It is not narrative content."""
    assert is_checklist_task(_by_label("No letters of support")) is True


def test_a_mandated_format_becomes_a_task():
    assert is_checklist_task(
        _by_label("Collaboration letters use NSF's exact single sentence")) is True


# ── the shape of the result on a real solicitation ──────────────────────────

def test_it_cuts_the_verified_list_to_a_scannable_checklist():
    """The whole point. 24 verified requirements must not become 24 tick-boxes,
    and the ones that survive must be the actionable ones."""
    kept = [r for r in EIR_REQUIREMENTS if is_checklist_task(r)]
    assert 5 <= len(kept) <= 12, [r["label"] for r in kept]
    # Not one of the thirteen Project Description content rows survives.
    assert not any(r["section"] == "project_description" and
                   "page" not in r["label"].lower() for r in kept), \
        [r["label"] for r in kept if r["section"] == "project_description"]


def test_a_malformed_row_is_excluded_rather_than_guessed_at():
    assert is_checklist_task({}) is False
    assert is_checklist_task({"label": "", "source": ""}) is False
