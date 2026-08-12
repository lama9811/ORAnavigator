"""The checklist must say where each task came from, and only claim the
solicitation for tasks actually read out of it.

The bug this prevents: a task reading "NSF requires a 2-page Data Management
Plan. Required on all NSF proposals." sat in the same list, styled identically,
as tasks genuinely extracted from the funder's document. The "2" is hardcoded in
proposal_templates.py. It happened to be right for one solicitation and would be
silently wrong for the next, and a PI had no way to tell which tasks had been
read and which were guessed from the sponsor's name.

Three provenances, and the distinction is the feature:
  solicitation      — generated from a stored requirement, carries its quote
  ora_process       — Morgan/ORA workflow, in no solicitation, always true
  sponsor_template  — a guess from the sponsor name, retired once the real
                      solicitation is on file
"""
import os

os.environ.setdefault("TRUSTED_HOSTS", "testserver,localhost,127.0.0.1")
os.environ.setdefault("JWT_SECRET", "test-secret-for-checklist-provenance")

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from db import Base
from models import Submission, SubmissionTask, User
from security import hash_password
from services import proposals_service as ps

PROFILE = {
    "version": 1,
    "id": "NSF 26-101",
    "requirements": [
        {"id": "project_description_15_pages",
         "label": "Limit Project Description to 15 pages",
         "section": "project_description", "kind": "semantic", "scored": True,
         "source": "The Project Description must not exceed 15 pages.",
         "why": "", "keywords": []},
        {"id": "equipment_30_percent_cap",
         "label": "Cap equipment at 30 percent of total budget",
         "section": "budget", "kind": "semantic", "scored": True,
         "source": "No more than 30 percent of the total budget may be allocated to equipment.",
         "why": "", "keywords": []},
    ],
}


@pytest.fixture
def ctx():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    db = Session()
    u = User(email="pi@morgan.edu", password_hash=hash_password("password123"),
             role="user", name="Pat Investigator")
    db.add(u)
    db.commit()
    sub = Submission(user_id=u.id, title="A proposal", sponsor="NSF", status="active")
    db.add(sub)
    db.commit()
    yield db, sub
    db.close()


def _tasks(db, sub):
    return (db.query(SubmissionTask)
              .filter(SubmissionTask.submission_id == sub.id)
              .order_by(SubmissionTask.sort_order).all())


def _add(db, sub, title, source, order=0, status="pending", ref=None):
    t = SubmissionTask(submission_id=sub.id, title=title, source=source,
                       source_ref=ref, status=status, sort_order=order)
    db.add(t)
    db.commit()
    return t


# ── generating tasks from the solicitation ──────────────────────────────────

def test_each_requirement_becomes_a_task_carrying_its_verbatim_quote(ctx):
    db, sub = ctx
    ps.sync_solicitation_requirement_tasks(db, sub, PROFILE)

    made = [t for t in _tasks(db, sub) if t.source == "solicitation"]
    assert len(made) == 2
    by_ref = {t.source_ref: t for t in made}
    t = by_ref["project_description_15_pages"]
    assert t.title == "Limit Project Description to 15 pages"
    assert t.source_quote == "The Project Description must not exceed 15 pages."


def test_a_requirement_with_no_quote_never_becomes_a_task(ctx):
    """Golden rule 2. An unquotable row is exactly the kind of claim the
    checklist must not present as the funder's."""
    db, sub = ctx
    # Deliberately rule-shaped ("15 pages"), so it WOULD be a checklist task if
    # it had a quote — otherwise this passes for the wrong reason.
    profile = {"requirements": [
        {"id": "unquoted", "label": "Limit the narrative to 15 pages", "source": "  "},
    ]}
    ps.sync_solicitation_requirement_tasks(db, sub, profile)
    assert [t for t in _tasks(db, sub) if t.source == "solicitation"] == []


def test_running_it_twice_does_not_duplicate_tasks(ctx):
    db, sub = ctx
    ps.sync_solicitation_requirement_tasks(db, sub, PROFILE)
    ps.sync_solicitation_requirement_tasks(db, sub, PROFILE)
    assert len([t for t in _tasks(db, sub) if t.source == "solicitation"]) == 2


def test_a_ticked_off_requirement_stays_ticked_across_a_re_read(ctx):
    """Re-reading a solicitation must not silently un-finish the PI's work."""
    db, sub = ctx
    ps.sync_solicitation_requirement_tasks(db, sub, PROFILE)
    done = [t for t in _tasks(db, sub)
            if t.source_ref == "equipment_30_percent_cap"][0]
    done.status = "done"
    db.commit()

    ps.sync_solicitation_requirement_tasks(db, sub, PROFILE)

    again = [t for t in _tasks(db, sub)
             if t.source_ref == "equipment_30_percent_cap"][0]
    assert again.status == "done"


def test_a_requirement_dropped_from_the_new_profile_loses_its_pending_task(ctx):
    db, sub = ctx
    ps.sync_solicitation_requirement_tasks(db, sub, PROFILE)
    thinner = {"requirements": PROFILE["requirements"][:1]}

    ps.sync_solicitation_requirement_tasks(db, sub, thinner)

    refs = {t.source_ref for t in _tasks(db, sub) if t.source == "solicitation"}
    assert refs == {"project_description_15_pages"}


# ── what happens to the tasks that were already there ───────────────────────

def test_sponsor_guess_tasks_are_retired_once_the_real_solicitation_is_read(ctx):
    """"NSF requires a 2-page Data Management Plan" is a guess from the sponsor
    name. Once the document itself has been read, it is noise at best and a
    contradiction at worst."""
    db, sub = ctx
    _add(db, sub, "Draft the Data Management Plan (2 pages max)", "sponsor_template", 10)

    ps.sync_solicitation_requirement_tasks(db, sub, PROFILE)

    titles = [t.title for t in _tasks(db, sub)]
    assert "Draft the Data Management Plan (2 pages max)" not in titles


def test_a_sponsor_guess_the_pi_already_finished_is_left_alone(ctx):
    """Deleting a task someone ticked off erases their record of doing it."""
    db, sub = ctx
    _add(db, sub, "Draft Current & Pending Support for each senior person",
         "sponsor_template", 11, status="done")

    ps.sync_solicitation_requirement_tasks(db, sub, PROFILE)

    titles = [t.title for t in _tasks(db, sub)]
    assert "Draft Current & Pending Support for each senior person" in titles


def test_ora_process_tasks_are_never_touched(ctx):
    """The Internal Routing Form appears in no solicitation and is still
    mandatory. Attaching a solicitation must not remove it."""
    db, sub = ctx
    _add(db, sub, "Complete & sign the Internal Routing Form", "ora_process", 7)
    _add(db, sub, "ORA submits to sponsor", "ora_process", 9)

    ps.sync_solicitation_requirement_tasks(db, sub, PROFILE)

    titles = [t.title for t in _tasks(db, sub)]
    assert "Complete & sign the Internal Routing Form" in titles
    assert "ORA submits to sponsor" in titles


def test_a_task_with_no_recorded_source_is_left_alone(ctx):
    """Every task predating this column, plus anything the PI added by hand.
    Unlabelled is not the same as ours to delete."""
    db, sub = ctx
    _add(db, sub, "My own reminder", None, 3)

    ps.sync_solicitation_requirement_tasks(db, sub, PROFILE)

    assert "My own reminder" in [t.title for t in _tasks(db, sub)]


# ── attachments: a safety net that must not double up ───────────────────────
# The endpoint runs the requirement sync and then the attachment seeding, in
# that order. Both are "from the solicitation"; only the first can quote it.

_ATTACH_PROFILE = {"requirements": [
    {"id": "facilities_statement",
     "label": "Attach a Facilities, Equipment and Other Resources statement",
     "section": "attachments", "scored": True,
     "source": "3. A Facilities, Equipment and Other Resources statement."},
]}


def test_an_attachment_the_requirements_already_cover_is_not_listed_twice(ctx):
    """This shipped: "Draft Facilities, Equipment, and Other Resources" and
    "Prepare required attachment: Facilities, Equipment and Other Resources
    statement" were one deliverable, listed twice, on the same screen."""
    db, sub = ctx
    ps.sync_solicitation_requirement_tasks(db, sub, _ATTACH_PROFILE)
    ps.sync_required_attachment_tasks(db, sub, {
        "required_attachments": ["Facilities, Equipment and Other Resources statement"]})

    facilities = [t for t in _tasks(db, sub) if "Facilities" in t.title]
    assert len(facilities) == 1, [t.title for t in facilities]
    assert facilities[0].source_quote


def test_an_attachment_the_requirement_read_missed_is_still_seeded(ctx):
    """required_attachments is a separate contract field. A missing attachment
    is a compliance rejection, so the extractor overlooking one must not mean
    the PI never hears about it."""
    db, sub = ctx
    ps.sync_solicitation_requirement_tasks(db, sub, _ATTACH_PROFILE)
    ps.sync_required_attachment_tasks(db, sub, {
        "required_attachments": ["Data Management Plan"]})

    assert any("Data Management Plan" in t.title for t in _tasks(db, sub))


# ── the template itself ─────────────────────────────────────────────────────

def test_the_seeded_template_labels_process_and_guess_tasks_differently():
    from services.proposal_templates import get_template
    tasks = get_template("NSF")
    by_title = {t["title"]: t.get("source") for t in tasks}
    assert by_title["Complete & sign the Internal Routing Form"] == "ora_process"
    assert by_title["Draft the Data Management Plan (2 pages max)"] == "sponsor_template"
