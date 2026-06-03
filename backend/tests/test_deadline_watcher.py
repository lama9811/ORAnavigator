"""Tests for the Deadline Watcher cron service.

Watcher logic: scan active Submissions, find any whose deadline sits
exactly on one of {14, 7, 3, 1, 0} days from today, send one email per
(submission, threshold) pair, write a log row to prevent doubles.

We exercise the service against a SQLite in-memory DB and an injected
fake send_email_fn so no SMTP traffic happens. The date math takes a
`today` override so we can freeze time without monkeypatching.

Run from the backend/ directory:
    cd backend && ../.venv/bin/python -m pytest tests/test_deadline_watcher.py -v
"""
from datetime import datetime, timedelta, timezone, date

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from db import Base
from models import Submission, SubmissionTask, User, DeadlineReminderLog
from services import deadline_watcher as dw


# ---------- fixture --------------------------------------------------------

@pytest.fixture
def db():
    """Fresh SQLite in-memory DB per test, with a single seeded faculty
    user. Each test creates its own submissions on top."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    user = User(
        email="pi@morgan.edu",
        password_hash="x",
        role="user",
        name="Pat Investigator",
    )
    session.add(user)
    session.commit()
    session.user_id = user.id  # convenience
    try:
        yield session
    finally:
        session.close()


def _make_submission(db, days_to_deadline: int, status: str = "active",
                     title: str = "Test Proposal",
                     sponsor: str = "NSF",
                     tasks: list[str] | None = None) -> Submission:
    """Helper: create one submission whose deadline is days_to_deadline
    from today (positive = future, negative = past). Optionally seed an
    open-tasks list so we can assert on email body rendering."""
    today = dw._today()
    deadline = datetime.combine(today + timedelta(days=days_to_deadline),
                                datetime.min.time(), tzinfo=timezone.utc)
    sub = Submission(
        user_id=db.user_id,
        title=title,
        sponsor=sponsor,
        deadline=deadline,
        status=status,
    )
    db.add(sub)
    db.flush()
    if tasks:
        for i, t in enumerate(tasks):
            db.add(SubmissionTask(
                submission_id=sub.id,
                title=t,
                status="pending",
                sort_order=i,
            ))
    db.commit()
    db.refresh(sub)
    return sub


# ---------- days_until / matched_threshold (pure date math) ----------------

def test_days_until_future_deadline():
    today = date(2026, 5, 27)
    deadline = datetime(2026, 6, 3, tzinfo=timezone.utc)  # 7 days out
    assert dw.days_until(deadline, today) == 7


def test_days_until_today_is_zero():
    today = date(2026, 5, 27)
    deadline = datetime(2026, 5, 27, tzinfo=timezone.utc)
    assert dw.days_until(deadline, today) == 0


def test_days_until_past_deadline_is_negative():
    today = date(2026, 5, 27)
    deadline = datetime(2026, 5, 20, tzinfo=timezone.utc)
    assert dw.days_until(deadline, today) == -7


def test_days_until_none_when_no_deadline():
    """Manually-created proposals can skip the deadline -- the watcher
    must tolerate None instead of crashing."""
    assert dw.days_until(None) is None


def test_matched_threshold_hits_only_exact_buckets():
    """Bucket logic is exact-match. 8 days out fires nothing; the user
    waits one more day until the 7-day bucket."""
    assert dw.matched_threshold(14) == 14
    assert dw.matched_threshold(7) == 7
    assert dw.matched_threshold(3) == 3
    assert dw.matched_threshold(1) == 1
    assert dw.matched_threshold(0) == 0
    # Misses
    assert dw.matched_threshold(15) is None
    assert dw.matched_threshold(8) is None
    assert dw.matched_threshold(2) is None
    assert dw.matched_threshold(-1) is None  # post-deadline never fires
    assert dw.matched_threshold(None) is None


# ---------- find_due_reminders --------------------------------------------

def test_find_due_finds_submission_at_seven_days(db):
    """The standard happy path -- one proposal sitting on the 7-day
    bucket, no prior log entries -> exactly one due reminder."""
    sub = _make_submission(db, days_to_deadline=7)
    due = dw.find_due_reminders(db)
    assert len(due) == 1
    found_sub, found_user, threshold = due[0]
    assert found_sub.id == sub.id
    assert found_user.email == "pi@morgan.edu"
    assert threshold == 7


def test_find_due_ignores_submissions_off_bucket(db):
    """A proposal 10 days out doesn't fire anything -- watch buckets are
    exact-match."""
    _make_submission(db, days_to_deadline=10)
    due = dw.find_due_reminders(db)
    assert due == []


def test_find_due_ignores_submitted_or_withdrawn(db):
    """Only `status='active'` submissions get reminders. Once the user
    marks a proposal submitted, we stop emailing them about it."""
    _make_submission(db, days_to_deadline=7, status="submitted")
    _make_submission(db, days_to_deadline=7, status="withdrawn")
    due = dw.find_due_reminders(db)
    assert due == []


def test_find_due_ignores_submissions_with_no_deadline(db):
    """A proposal without a deadline can't fire the watcher."""
    sub = Submission(
        user_id=db.user_id,
        title="No deadline yet",
        sponsor="NSF",
        deadline=None,
        status="active",
    )
    db.add(sub); db.commit()
    due = dw.find_due_reminders(db)
    assert due == []


def test_find_due_skips_already_logged_threshold(db):
    """If we've already sent a 7-day reminder for this submission, we do
    NOT pick it up again on the next run."""
    sub = _make_submission(db, days_to_deadline=7)
    db.add(DeadlineReminderLog(
        submission_id=sub.id,
        threshold_days=7,
        sent_to="pi@morgan.edu",
    ))
    db.commit()
    due = dw.find_due_reminders(db)
    assert due == []


def test_find_due_still_fires_other_thresholds_for_same_submission(db):
    """We log per-(submission, threshold). A submission whose 14-day
    reminder was sent last week should still fire its 7-day reminder
    today."""
    sub = _make_submission(db, days_to_deadline=7)
    db.add(DeadlineReminderLog(
        submission_id=sub.id,
        threshold_days=14,
        sent_to="pi@morgan.edu",
    ))
    db.commit()
    due = dw.find_due_reminders(db)
    assert len(due) == 1
    assert due[0][2] == 7


# ---------- compose_reminder_email ----------------------------------------

def test_compose_subject_uses_due_phrase_for_today():
    """Subject phrasing for due-today must NOT say 'in 0 days'."""
    user = User(email="pi@morgan.edu", name="Pat Investigator")
    sub = Submission(
        title="Quantum sensing",
        sponsor="NSF",
        deadline=datetime(2026, 5, 27, tzinfo=timezone.utc),
    )
    subject, _ = dw.compose_reminder_email(sub, user, days_left=0)
    assert "due today" in subject.lower()


def test_compose_subject_singular_for_one_day():
    user = User(email="pi@morgan.edu", name="Pat Investigator")
    sub = Submission(title="X", sponsor="NIH",
                     deadline=datetime(2026, 5, 28, tzinfo=timezone.utc))
    subject, _ = dw.compose_reminder_email(sub, user, days_left=1)
    assert "1 day" in subject and "1 days" not in subject


def test_compose_email_includes_open_tasks(db):
    """Open checklist items must appear in the email body so the user
    knows what's left without opening the app."""
    sub = _make_submission(db, days_to_deadline=7,
                           tasks=["Data Management Plan", "Biosketch",
                                  "Budget Justification"])
    user = db.query(User).first()
    _, html = dw.compose_reminder_email(sub, user, days_left=7)
    assert "Data Management Plan" in html
    assert "Biosketch" in html
    assert "Budget Justification" in html


def test_compose_email_handles_no_open_tasks(db):
    """When every task is done, the email must NOT render a stale 'open
    items' list -- it should say everything is ready."""
    sub = _make_submission(db, days_to_deadline=3,
                           tasks=["Already done"])
    # Mark the one task done
    for t in sub.tasks:
        t.status = "done"
    db.commit()
    user = db.query(User).first()
    _, html = dw.compose_reminder_email(sub, user, days_left=3)
    assert "submit" in html.lower()


def test_compose_email_handles_user_without_name():
    """Some users haven't set a profile name. Greeting falls back to
    'Hi,' rather than 'Hi None,'."""
    user = User(email="anon@morgan.edu", name=None)
    sub = Submission(title="X", sponsor="NSF",
                     deadline=datetime(2026, 6, 3, tzinfo=timezone.utc))
    _, html = dw.compose_reminder_email(sub, user, days_left=7)
    assert "None" not in html
    assert "Hi," in html


# ---------- send_due_reminders (orchestrator) -----------------------------

def test_send_writes_log_and_returns_summary(db):
    """End-to-end on the orchestration layer with an injected
    send-email stub. Successful sends produce log rows; the summary
    reflects what happened."""
    sent_calls: list[tuple[str, str]] = []

    def fake_send(to_email, subject, html):
        sent_calls.append((to_email, subject))
        return True

    _make_submission(db, days_to_deadline=14, title="Proposal A")
    _make_submission(db, days_to_deadline=7, title="Proposal B")

    result = dw.send_due_reminders(db, send_email_fn=fake_send)
    assert result["scanned"] == 2
    assert result["sent"] == 2
    assert result["failed"] == 0
    assert len(sent_calls) == 2
    # Each got logged
    assert db.query(DeadlineReminderLog).count() == 2


def test_send_does_not_log_when_smtp_fails(db):
    """If SMTP returns False (or throws), we must NOT write the log
    row -- next run should retry."""
    _make_submission(db, days_to_deadline=7)

    def failing_send(to_email, subject, html):
        return False

    result = dw.send_due_reminders(db, send_email_fn=failing_send)
    assert result["sent"] == 0
    assert result["failed"] == 1
    assert db.query(DeadlineReminderLog).count() == 0


def test_send_is_idempotent_across_repeat_runs(db):
    """Running the cron twice in a row sends each reminder exactly
    once. Critical: a Cloud Scheduler retry must not double-email."""
    sent_calls = []

    def fake_send(to_email, subject, html):
        sent_calls.append(subject)
        return True

    _make_submission(db, days_to_deadline=7)
    dw.send_due_reminders(db, send_email_fn=fake_send)
    dw.send_due_reminders(db, send_email_fn=fake_send)
    assert len(sent_calls) == 1


def test_send_email_exception_is_swallowed(db):
    """If the email layer throws an exception (e.g. SMTP timeout), the
    watcher must not crash the whole cron -- it logs and continues so
    later proposals still get checked."""
    _make_submission(db, days_to_deadline=7, title="A")
    _make_submission(db, days_to_deadline=14, title="B")

    call_count = [0]

    def flaky_send(to_email, subject, html):
        call_count[0] += 1
        if call_count[0] == 1:
            raise RuntimeError("SMTP timeout simulation")
        return True

    result = dw.send_due_reminders(db, send_email_fn=flaky_send)
    # 2 due, 1 raised (failed), 1 succeeded
    assert result["scanned"] == 2
    assert result["failed"] == 1
    assert result["sent"] == 1
    # Only the successful one got logged.
    assert db.query(DeadlineReminderLog).count() == 1


# ---------- AI personalization layer (advisory, with hard fallback) --------

def test_ai_body_used_when_available_subject_stays_deterministic(db, monkeypatch):
    """When the AI composer returns a (subject, html), the email body is the
    AI one but the SUBJECT must equal the deterministic subject; the send +
    idempotency log still happen."""
    sub = _make_submission(db, days_to_deadline=7, title="Coral Proposal",
                           sponsor="NSF", tasks=["Draft budget", "Write DMP"])
    user = db.query(User).first()
    det_subject, _ = dw.compose_reminder_email(sub, user, days_left=7)

    monkeypatch.setattr(dw, "compose_reminder_email_ai",
                        lambda *a, **k: (det_subject, "<p>AI-PERSONALIZED BODY</p>"))

    captured = {}
    def fake_send(to_email, subject, html):
        captured["subject"] = subject
        captured["html"] = html
        return True

    result = dw.send_due_reminders(db, send_email_fn=fake_send, use_ai=True)
    assert result["sent"] == 1
    assert captured["html"] == "<p>AI-PERSONALIZED BODY</p>"
    assert captured["subject"] == det_subject          # subject is deterministic
    assert db.query(DeadlineReminderLog).count() == 1   # idempotency log written


def test_falls_back_to_template_when_ai_returns_none(db, monkeypatch):
    """AI unavailable/failed -> the deterministic template body is sent; the
    email still goes out and is logged."""
    sub = _make_submission(db, days_to_deadline=7, title="Coral Proposal",
                           sponsor="NSF", tasks=["Draft budget"])
    user = db.query(User).first()
    expected_subject, expected_html = dw.compose_reminder_email(sub, user, days_left=7)

    monkeypatch.setattr(dw, "compose_reminder_email_ai", lambda *a, **k: None)

    captured = {}
    def fake_send(to_email, subject, html):
        captured["subject"] = subject
        captured["html"] = html
        return True

    result = dw.send_due_reminders(db, send_email_fn=fake_send, use_ai=True)
    assert result["sent"] == 1
    assert captured["html"] == expected_html   # template fallback
    assert captured["subject"] == expected_subject
    assert db.query(DeadlineReminderLog).count() == 1


def test_use_ai_false_never_calls_ai_composer(db, monkeypatch):
    sub = _make_submission(db, days_to_deadline=7)
    called = {"n": 0}
    def spy(*a, **k):
        called["n"] += 1
        return ("S", "<p>x</p>")
    monkeypatch.setattr(dw, "compose_reminder_email_ai", spy)

    dw.send_due_reminders(db, send_email_fn=lambda *a: True, use_ai=False)
    assert called["n"] == 0


def test_no_credentials_falls_back_naturally(db):
    """With no ADC (the conftest autouse pins get_client -> None), the REAL
    compose_reminder_email_ai returns None and the template is used. The
    email still sends + logs -- proving the offline path needs no mocking."""
    sub = _make_submission(db, days_to_deadline=7, title="X", sponsor="NSF",
                           tasks=["Draft budget"])
    user = db.query(User).first()
    # Real AI composer, but get_client() is None -> returns None.
    assert dw.compose_reminder_email_ai(sub, user, 7, "https://x", list(sub.tasks)) is None

    result = dw.send_due_reminders(db, send_email_fn=lambda *a: True, use_ai=True)
    assert result["sent"] == 1
    assert db.query(DeadlineReminderLog).count() == 1


def test_ai_drops_hallucinated_task_order(db, monkeypatch):
    """If the AI's prioritized_tasks isn't a clean permutation of the real
    open tasks, compose_reminder_email_ai keeps the original order (never
    renders an invented task title)."""
    sub = _make_submission(db, days_to_deadline=3, title="X", sponsor="NSF",
                           tasks=["Draft budget", "Write DMP"])
    user = db.query(User).first()

    # AI returns a body + a hallucinated extra task.
    from services import gemini_client
    monkeypatch.setattr(gemini_client, "generate_json", lambda *a, **k: {
        "body_paragraph": "Time is short; please prioritize.",
        "prioritized_tasks": ["Write DMP", "Draft budget", "INVENTED TASK"],
    })
    res = dw.compose_reminder_email_ai(sub, user, 3, "https://x", list(sub.tasks))
    assert res is not None
    _, html = res
    assert "INVENTED TASK" not in html
    assert "Draft budget" in html and "Write DMP" in html
