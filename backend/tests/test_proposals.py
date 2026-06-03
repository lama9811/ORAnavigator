"""Tests for the proposals tracker service.

CRUD lives in services/proposals_service.py; the FastAPI endpoint is a
thin wrapper. We exercise the service against a SQLite in-memory DB --
the schema is identical (SQLAlchemy ORM), and this keeps the suite
independent of MySQL / Cloud SQL.

Run from the backend/ directory:
    cd backend && ../.venv/bin/python -m pytest tests/test_proposals.py -v
"""
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from db import Base
from models import Submission, SubmissionTask, User, UserMemory
from services import proposals_service as ps


@pytest.fixture
def db():
    """Fresh SQLite in-memory DB per test. ORM models match the live schema
    (the migration in migrate_db.py is the production equivalent)."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    # One test user shared across the test
    user = User(email="pi@morgan.edu", password_hash="x", role="user")
    session.add(user)
    session.commit()
    session.user_id = user.id  # convenience handle on the fixture
    try:
        yield session
    finally:
        session.close()


# =====================================================================
# Create
# =====================================================================

def test_create_submission_seeds_tasks_from_template(db):
    """Creating a submission with sponsor='NSF' must seed the NSF
    checklist (generic + NSF-specific add-ons)."""
    sub = ps.create_submission(
        db, user_id=db.user_id,
        title="NSF CAREER award",
        sponsor="NSF",
        deadline=datetime.now(timezone.utc) + timedelta(days=30),
    )
    assert sub.id
    assert sub.title == "NSF CAREER award"
    assert sub.sponsor == "NSF"
    assert sub.status == "active"
    # The NSF template adds 3 always-on sponsor-specific tasks on top of the
    # generic 10 (the EIR checklist is education-program-only, not seeded here).
    assert len(sub.tasks) >= 13
    # The DMP task is NSF-only -- it must be present.
    titles = [t.title for t in sub.tasks]
    assert any("Data Management Plan" in t for t in titles)


def test_create_submission_unknown_sponsor_falls_back_to_generic(db):
    """A sponsor outside {NSF, NIH} gets the generic 10-task template."""
    sub = ps.create_submission(
        db, user_id=db.user_id,
        title="DoD DURIP equipment grant",
        sponsor="DoD",
        deadline=datetime.now(timezone.utc) + timedelta(days=60),
    )
    titles = [t.title for t in sub.tasks]
    # Generic checklist has 10 entries; no NSF-specific DMP / NIH-specific Aims.
    assert len(sub.tasks) == 10
    assert not any("Data Management Plan" in t for t in titles)
    assert not any("Specific Aims" in t for t in titles)


def test_create_submission_writes_active_grant_memory(db):
    """Creating a submission must also write a UserMemory(active_grant) so
    the chat agent can recall the user's in-flight proposals next session."""
    sub = ps.create_submission(
        db, user_id=db.user_id,
        title="Cancer health disparities R01",
        sponsor="NIH",
        deadline=datetime.now(timezone.utc) + timedelta(days=45),
    )
    memories = db.query(UserMemory).filter(
        UserMemory.user_id == db.user_id,
        UserMemory.memory_type == "active_grant",
    ).all()
    assert memories, "creating a submission must record an active_grant memory"
    assert "NIH" in memories[0].content
    assert "Cancer" in memories[0].content


# =====================================================================
# Read
# =====================================================================

def test_list_submissions_scopes_to_user(db):
    """list_submissions must filter by user_id -- one user must not see
    another user's submissions."""
    other = User(email="other@morgan.edu", password_hash="x", role="user")
    db.add(other)
    db.commit()
    ps.create_submission(db, user_id=db.user_id, title="mine", sponsor="NSF", deadline=None)
    ps.create_submission(db, user_id=other.id, title="theirs", sponsor="NIH", deadline=None)
    mine = ps.list_submissions(db, user_id=db.user_id)
    assert len(mine) == 1
    assert mine[0].title == "mine"


def test_get_submission_returns_tasks(db):
    sub = ps.create_submission(db, user_id=db.user_id, title="x", sponsor="NSF", deadline=None)
    fetched = ps.get_submission(db, submission_id=sub.id, user_id=db.user_id)
    assert fetched is not None
    assert len(fetched.tasks) >= 13  # NSF template


def test_get_submission_wrong_user_returns_none(db):
    """A user must not be able to fetch another user's submission by ID."""
    other = User(email="other@morgan.edu", password_hash="x", role="user")
    db.add(other)
    db.commit()
    sub = ps.create_submission(db, user_id=other.id, title="theirs", sponsor="NSF", deadline=None)
    assert ps.get_submission(db, submission_id=sub.id, user_id=db.user_id) is None


# =====================================================================
# Update
# =====================================================================

def test_toggle_task_status(db):
    sub = ps.create_submission(db, user_id=db.user_id, title="x", sponsor="generic", deadline=None)
    task = sub.tasks[0]
    assert task.status == "pending"

    updated = ps.update_task(
        db, submission_id=sub.id, task_id=task.id,
        user_id=db.user_id, status="done",
    )
    assert updated is not None
    assert updated.status == "done"

    # And again to flip back
    updated = ps.update_task(
        db, submission_id=sub.id, task_id=task.id,
        user_id=db.user_id, status="pending",
    )
    assert updated.status == "pending"


def test_update_task_rejects_wrong_user(db):
    """Cross-user task update must fail (return None) -- security boundary."""
    other = User(email="other@morgan.edu", password_hash="x", role="user")
    db.add(other)
    db.commit()
    sub = ps.create_submission(db, user_id=other.id, title="theirs", sponsor="NSF", deadline=None)
    result = ps.update_task(
        db, submission_id=sub.id, task_id=sub.tasks[0].id,
        user_id=db.user_id, status="done",
    )
    assert result is None


def test_update_submission_changes_title_and_status(db):
    sub = ps.create_submission(db, user_id=db.user_id, title="old", sponsor="NSF", deadline=None)
    updated = ps.update_submission(
        db, submission_id=sub.id, user_id=db.user_id,
        title="new", status="submitted",
    )
    assert updated.title == "new"
    assert updated.status == "submitted"


# =====================================================================
# Delete
# =====================================================================

def test_delete_submission_cascades_to_tasks(db):
    """Deleting a submission must also delete its tasks (FK cascade)."""
    sub = ps.create_submission(db, user_id=db.user_id, title="x", sponsor="NSF", deadline=None)
    task_ids = [t.id for t in sub.tasks]
    assert task_ids

    deleted = ps.delete_submission(db, submission_id=sub.id, user_id=db.user_id)
    assert deleted is True

    # Tasks should be gone via ON DELETE CASCADE / cascade="all, delete-orphan"
    remaining_tasks = db.query(SubmissionTask).filter(
        SubmissionTask.id.in_(task_ids)
    ).all()
    assert remaining_tasks == []


def test_delete_wrong_user_is_noop(db):
    other = User(email="other@morgan.edu", password_hash="x", role="user")
    db.add(other)
    db.commit()
    sub = ps.create_submission(db, user_id=other.id, title="theirs", sponsor="NSF", deadline=None)
    deleted = ps.delete_submission(db, submission_id=sub.id, user_id=db.user_id)
    assert deleted is False
    # The submission must still exist
    assert db.query(Submission).filter(Submission.id == sub.id).first() is not None


# =====================================================================
# Adding custom tasks
# =====================================================================

def test_add_custom_task_appends_at_end(db):
    sub = ps.create_submission(db, user_id=db.user_id, title="x", sponsor="generic", deadline=None)
    before = len(sub.tasks)
    new_task = ps.add_task(
        db, submission_id=sub.id, user_id=db.user_id,
        title="Confirm dept chair signature", description="custom",
    )
    assert new_task is not None
    assert new_task.title == "Confirm dept chair signature"
    # Refresh and check ordering
    db.refresh(sub)
    assert len(sub.tasks) == before + 1
    assert sub.tasks[-1].title == "Confirm dept chair signature"


# =====================================================================
# Solicitation-seeded submissions
# =====================================================================

def test_create_from_solicitation_seeds_sponsor_tasks_plus_extras(db):
    """A submission built from an extracted solicitation gets the
    sponsor's base template AND extra tasks for any required attachment
    not already in the template."""
    extracted = {
        "sponsor": "NSF",
        "program_id": "NSF 23-573",
        "program_name": "Faculty Early Career Development",
        "deadline": "2026-06-12",
        "page_limits": {"project_description": 15},
        "required_attachments": [
            "Biosketch",
            "Postdoc Mentoring Plan",  # Not in the base NSF template
        ],
        "eligibility": "Early-career tenure-track faculty",
        "budget_cap": 600000,
        "submission_portal": "Research.gov",
        "source_quotes": {},
    }
    sub = ps.create_submission_from_solicitation(
        db, user_id=db.user_id, extracted=extracted,
    )
    assert sub.id
    assert sub.title == "Faculty Early Career Development"
    assert sub.sponsor == "NSF"
    assert sub.deadline.year == 2026
    # NSF base = 14 tasks; "Postdoc Mentoring Plan" is solicitation-specific
    # and not in the base template, so it should be added.
    titles = [t.title for t in sub.tasks]
    assert any("Postdoc Mentoring Plan" in t for t in titles), (
        f"expected the solicitation-specific attachment to be seeded; got: {titles}")
    # And the notes blob carries the rest of the structured metadata
    assert sub.notes is not None
    assert "NSF 23-573" in sub.notes
    assert "600,000" in sub.notes
    assert "Research.gov" in sub.notes


def test_create_from_solicitation_falls_back_when_minimal(db):
    """An extractor result with very little data still produces a valid
    submission -- the user can edit later. Title falls back gracefully."""
    sub = ps.create_submission_from_solicitation(
        db, user_id=db.user_id,
        extracted={"sponsor": None, "required_attachments": []},
    )
    assert sub.id
    assert sub.title == "Proposal"  # default
    assert sub.sponsor == "Internal"  # default
    assert len(sub.tasks) > 0  # at least the generic template


def test_create_from_solicitation_respects_title_override(db):
    """If the user edits the title in the confirmation UI, we honor that."""
    extracted = {
        "sponsor": "NIH",
        "program_name": "R01 something",
        "required_attachments": [],
    }
    sub = ps.create_submission_from_solicitation(
        db, user_id=db.user_id, extracted=extracted,
        title_override="My cancer R01 — June '26",
    )
    assert sub.title == "My cancer R01 — June '26"


def test_solicitation_required_attachments_survive_template_dedup(db):
    """REGRESSION: a solicitation-MANDATED attachment that happens to
    overlap the sponsor template (e.g. 'Data Management Plan' ⊂ the NSF
    task 'Draft the Data Management Plan (2 pages max)', or 'Budget
    Justification' ⊂ 'Write the budget justification') must STILL be
    recoverable by reconstruct_solicitation_context().

    Bug: create_submission_from_solicitation only seeds a
    'Prepare required attachment: X' task for attachments NOT already in
    the template, and reconstruct rebuilt the required list solely from
    those tasks -- so template-overlapping attachments vanished. Draft
    Critic then PASSED a draft that was missing a sponsor-mandated
    attachment (the most standard ones are exactly the ones in the
    template). The full extracted list must round-trip."""
    extracted = {
        "sponsor": "NSF",
        "program_name": "AISL",
        "required_attachments": [
            "Project Summary",          # not in template -> seeds a task
            "Budget Justification",     # overlaps template task -> deduped from tasks
            "Data Management Plan",     # overlaps template task -> deduped from tasks
        ],
        "budget_cap": 500000,
        "page_limits": {"project_description": 15},
    }
    sub = ps.create_submission_from_solicitation(
        db, user_id=db.user_id, extracted=extracted,
    )
    ctx = ps.reconstruct_solicitation_context(sub)
    got = set(ctx["required_attachments"])
    assert "Data Management Plan" in got, (
        f"template-overlapping required attachment was dropped; got {sorted(got)}")
    assert "Budget Justification" in got, (
        f"template-overlapping required attachment was dropped; got {sorted(got)}")
    assert "Project Summary" in got


# ---------- NSF EIR checklist is education-program-only (not every NSF) ------

from services import proposal_templates as pt


def _titles(tasks):
    return [t["title"] for t in tasks]


def test_plain_nsf_template_has_no_eir_task():
    """A plain NSF proposal (no program info) must NOT get the EIR checklist."""
    titles = _titles(pt.get_template("NSF"))
    assert not any("EIR" in t for t in titles)
    # but the real NSF extras are still there
    assert any("Data Management Plan" in t for t in titles)


def test_nsf_education_program_gets_eir_task():
    titles = _titles(pt.get_template("NSF", program_name="Education Innovation and Research (EIR)"))
    assert any("EIR" in t for t in titles)


def test_nsf_non_education_program_no_eir_task():
    titles = _titles(pt.get_template("NSF", program_name="Coastal Sensing for Reef Restoration",
                                     program_id="NSF 26-512"))
    assert not any("EIR" in t for t in titles)


def test_eir_detector_avoids_false_positives():
    # "their" must not trip the \beir\b acronym match
    assert pt._is_education_program("Studying their reef networks", None) is False
    assert pt._is_education_program("STEM Education Pathways", None) is True
    assert pt._is_education_program(None, "IUSE-2026") is True


def test_non_nsf_sponsor_never_gets_eir_even_if_education():
    # NIH education-ish program should not pick up the NSF-specific EIR task
    titles = _titles(pt.get_template("NIH", program_name="Science Education R25"))
    assert not any("EIR" in t for t in titles)


def test_manual_nsf_submission_seeds_no_eir_task(db):
    sub = ps.create_submission(db, user_id=db.user_id, title="Reef sensors",
                               sponsor="NSF", deadline=None)
    assert not any("EIR" in t.title for t in sub.tasks)
