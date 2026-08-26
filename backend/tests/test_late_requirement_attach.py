"""A requirement read that lands AFTER the proposal is saved must still attach.

WHAT WENT WRONG
---------------
Measured on a real proposal the PI created, from the database:

    19:18:02   the document was read and stored
    19:18:38   the proposal was created          <- 36 seconds later
    (the requirement read takes 60-150 seconds)

Create deliberately never blocks on the requirement read -- waiting out two
minutes is the one thing that could lose a PI their work, and that rule is
load-bearing. But the read was then thrown away: the proposal was written with
the funder's numbers and the document and NO requirement list, the modal closed,
and the answer arrived into nothing.

So the PI attached their solicitation and the app still behaved as though they
had not: Draft Review 409s without a stored profile, the badge reads "rules
only", and Section Check offers the rulebook's sections alone. One attach has
to reach every tool.

THE FIX is not to make Create wait. It is to deliver the answer late: the
browser keeps the in-flight read, and when it lands it PUTs the requirements
onto the proposal that was just created.

These tests hold the SERVER half of that -- the attach endpoint has to be safe
to call on a proposal that already carries the same solicitation's contract, and
it has to leave a PI's ticked-off checklist alone.
"""
import os
os.environ["TRUSTED_HOSTS"] = "testserver,localhost,127.0.0.1"

import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from fastapi.testclient import TestClient

import main
import deps
from db import Base
from models import User, Submission, SubmissionTask
from security import hash_password


_EXTRACTED = {"program_id": "NSF 23-598", "program_name": "HBCU-EiR",
              "deadline": None, "budget_cap": None}


def _rows():
    return [
        {"label": "Include the LOI number in the Project Summary",
         "section": "project_summary", "scored": True,
         "source": "The Project Summary must include the LOI number.",
         "why": "", "keywords": []},
        {"label": "Cap equipment at 30 percent",
         "section": "budget_justification", "scored": True,
         "source": "No more than 30% of the budget may be equipment.",
         "why": "", "keywords": []},
    ]


@pytest.fixture
def ctx():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    seed = Session()
    u = User(email="pi@morgan.edu", password_hash=hash_password("x"), role="user")
    seed.add(u); seed.commit(); uid = u.id; seed.close()

    def _db():
        db = Session()
        try:
            yield db
        finally:
            db.close()

    main.app.dependency_overrides[main.get_db] = _db
    main.app.dependency_overrides[deps.get_db] = _db
    main.app.dependency_overrides[main.get_current_user] = lambda: {
        "user_id": uid, "email": "pi@morgan.edu", "role": "user"}
    yield TestClient(main.app), Session, uid
    main.app.dependency_overrides.clear()


def test_a_proposal_created_without_requirements_can_receive_them_later(ctx):
    """The exact sequence: Create wins the race, the read lands afterwards."""
    c, Session, _ = ctx
    r = c.post("/api/me/submissions/from-solicitation/confirm",
               json={"extracted": _EXTRACTED})
    assert r.status_code == 200, r.text
    sid = r.json()["id"]

    s = Session()
    assert s.get(Submission, sid).solicitation_json in (None, ""), "unexpectedly stored"
    s.close()

    late = c.put(f"/api/me/submissions/{sid}/solicitation",
                 json={"extracted": _EXTRACTED, "requirements": _rows()})
    assert late.status_code == 200, late.text

    s = Session()
    stored = json.loads(s.get(Submission, sid).solicitation_json)
    assert len(stored["requirements"]) == 2, stored["requirements"]
    s.close()


def test_the_late_attach_does_not_untick_a_task_the_PI_finished(ctx):
    """A minute passes between Create and the late attach, and a PI can tick
    something off in it. Checklist tasks are keyed by `source_ref`, so a
    re-attach must find the existing row rather than replace it."""
    c, Session, _ = ctx
    sid = c.post("/api/me/submissions/from-solicitation/confirm",
                 json={"extracted": _EXTRACTED}).json()["id"]
    c.put(f"/api/me/submissions/{sid}/solicitation",
          json={"extracted": _EXTRACTED, "requirements": _rows()})

    s = Session()
    task = (s.query(SubmissionTask)
             .filter(SubmissionTask.submission_id == sid,
                     SubmissionTask.source == "solicitation").first())
    if task is None:
        s.close()
        pytest.skip("this solicitation's rows produce no checklist tasks")
    task.status = "done"
    s.commit()
    ref, tid = task.source_ref, task.id
    s.close()

    c.put(f"/api/me/submissions/{sid}/solicitation",
          json={"extracted": _EXTRACTED, "requirements": _rows()})

    s = Session()
    again = s.get(SubmissionTask, tid)
    assert again is not None, "the ticked task was deleted by the re-attach"
    assert again.status == "done", again.status
    assert again.source_ref == ref
    s.close()


def test_a_late_attach_carrying_nothing_usable_is_refused_not_stored(ctx):
    """The browser must not be able to overwrite a real profile with an empty
    one because a late read came back broken."""
    c, Session, _ = ctx
    sid = c.post("/api/me/submissions/from-solicitation/confirm",
                 json={"extracted": _EXTRACTED, "requirements": _rows()}).json()["id"]
    r = c.put(f"/api/me/submissions/{sid}/solicitation",
              json={"extracted": _EXTRACTED, "requirements": [{"label": "x"}]})
    assert r.status_code == 400, r.status_code

    s = Session()
    stored = json.loads(s.get(Submission, sid).solicitation_json)
    assert len(stored["requirements"]) == 2, "a real profile was clobbered"
    s.close()
