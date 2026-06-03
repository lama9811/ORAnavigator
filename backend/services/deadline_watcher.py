"""Deadline Watcher -- nightly cron that emails faculty when one of their
in-flight proposals is approaching its deadline.

Trigger model:
  - Cloud Scheduler hits POST /api/admin/check-deadlines once a day (early
    morning UTC).
  - Endpoint calls send_due_reminders(db) here.
  - For every active Submission with a deadline matching one of the
    threshold buckets (14, 7, 3, 1, 0 days out), we compose + send a
    reminder email IF we haven't already sent for that (submission,
    threshold) pair.

Idempotency:
  Every successful send writes a DeadlineReminderLog row. The next run
  filters those out. This way a user who misses a day of the cron still
  gets the right reminders, and re-running the endpoint manually is safe.

What this service does NOT do:
  - It does not pick the schedule (Cloud Scheduler does).
  - It does not handle SMTP setup (email_service does).
  - It does not check authentication (the endpoint does, via the shared
    cron secret).
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone, date
from typing import Iterable, Optional

from sqlalchemy.orm import Session

from models import Submission, SubmissionTask, User, DeadlineReminderLog


log = logging.getLogger(__name__)


# The bucket schedule. Reminders fire when a Submission is THIS many days
# from its deadline. 0 = "due today". We intentionally don't add post-
# deadline negative buckets -- once a submission misses its date the user
# either submits late or marks it withdrawn; spamming overdue reminders
# would just be annoying.
THRESHOLD_DAYS = (14, 7, 3, 1, 0)


# ===========================================================================
# Date math
# ===========================================================================

def _today() -> date:
    """Wrapped so tests can freeze 'today' via monkeypatch."""
    return datetime.now(timezone.utc).date()


def days_until(deadline: Optional[datetime], today: Optional[date] = None) -> Optional[int]:
    """Whole-day diff from `today` to `deadline`. Returns None if the
    submission has no deadline (manually-created proposals can skip it).

    A deadline of today returns 0. A deadline of tomorrow returns 1. A
    deadline of yesterday returns -1. All using calendar days, not 24h
    windows -- "in 7 days" is what the user expects, not "in 168 hours"."""
    if deadline is None:
        return None
    if today is None:
        today = _today()
    d = deadline.date() if isinstance(deadline, datetime) else deadline
    return (d - today).days


def matched_threshold(days_left: Optional[int]) -> Optional[int]:
    """If `days_left` lines up with one of the THRESHOLD_DAYS buckets,
    return that bucket. Otherwise None. Buckets are exact-match: 8 days
    out doesn't fire any reminder; the user waits until 7."""
    if days_left is None:
        return None
    if days_left in THRESHOLD_DAYS:
        return days_left
    return None


# ===========================================================================
# Email composition
# ===========================================================================

def _open_tasks_html(tasks: Iterable[SubmissionTask], limit: int = 6) -> str:
    """Render the open-checklist preview as an HTML <ul>. Caps at `limit`
    so very long checklists don't bloat the email."""
    pending = [t for t in tasks if t.status != "done"]
    if not pending:
        return '<p style="color:#5f6368;font-size:14px;margin:8px 0;">'\
               'Every checklist item is checked off. Just submit.</p>'
    shown = pending[:limit]
    items = "".join(
        f'<li style="margin:4px 0;color:#202124;">{t.title}</li>' for t in shown
    )
    more = ""
    if len(pending) > limit:
        more = (f'<li style="margin:4px 0;color:#5f6368;font-style:italic;">'
                f'... and {len(pending) - limit} more</li>')
    return f'<ul style="padding-left:18px;margin:8px 0;">{items}{more}</ul>'


def _phrase_days_left(days_left: int) -> str:
    if days_left == 0:
        return "due today"
    if days_left == 1:
        return "due in 1 day"
    return f"due in {days_left} days"


def compose_reminder_email(
    submission: Submission,
    user: User,
    days_left: int,
    app_url: str = "https://ora.inavigator.ai",
) -> tuple[str, str]:
    """Return (subject, html_body) for one reminder. Pure function -- no
    SMTP, no DB, easy to unit test."""
    phrase = _phrase_days_left(days_left)
    subject = f"{submission.sponsor} {submission.title}: {phrase}"
    greeting = (f"Hi {user.name.split()[0]}," if user.name and user.name.strip()
                else "Hi,")
    tasks_html = _open_tasks_html(submission.tasks or [])
    deadline_str = (submission.deadline.strftime("%b %d, %Y")
                    if submission.deadline else "no date set")

    html = f"""
    <div style="font-family: 'Google Sans', Arial, sans-serif; max-width: 520px; margin: 0 auto; padding: 32px;">
        <div style="text-align: center; margin-bottom: 24px;">
            <h1 style="color: #4285F4; font-size: 24px; margin: 0;">ORA Navigator</h1>
            <p style="color: #5f6368; font-size: 13px; margin: 4px 0 0;">Morgan State University &middot; Office of Research Administration</p>
        </div>
        <div style="background: #f8f9fa; border-radius: 12px; padding: 24px; border: 1px solid #dadce0;">
            <h2 style="color: #202124; font-size: 18px; margin: 0 0 12px;">
                Your proposal is {phrase}.
            </h2>
            <p style="color: #5f6368; font-size: 14px; line-height: 1.6; margin: 0 0 16px;">
                {greeting} a quick heads-up: <strong>{submission.title}</strong>
                ({submission.sponsor}) has a deadline of <strong>{deadline_str}</strong>.
            </p>
            <h3 style="color: #202124; font-size: 14px; margin: 16px 0 4px;">Open checklist items</h3>
            {tasks_html}
            <div style="text-align: center; margin: 24px 0 4px;">
                <a href="{app_url}/my-proposals" style="display: inline-block; padding: 11px 28px; background: #4285F4; color: white; text-decoration: none; border-radius: 8px; font-weight: 600; font-size: 14px;">
                    Open My Proposals
                </a>
            </div>
        </div>
        <p style="color: #9aa0a6; font-size: 11px; text-align: center; margin-top: 16px; line-height: 1.5;">
            You're getting this because you have an active proposal in ORA Navigator.<br>
            Mark the proposal submitted or withdrawn to stop further reminders.
        </p>
    </div>
    """
    return subject, html


# ===========================================================================
# ADVISORY AI email composer (Gemini) -- personalized prose + task ordering.
# Falls back to compose_reminder_email() whenever it returns None, so a
# reminder ALWAYS sends even when the model is slow/down/unavailable. The
# SUBJECT stays deterministic (inbox consistency); AI writes the body only.
# ===========================================================================

# Strict rules passed as the model's SYSTEM INSTRUCTION; the data (title,
# sponsor, days-left, tasks) is passed separately as the user content.
_REMINDER_SYSTEM = """You write the BODY of a deadline-reminder email to a university faculty member about their in-flight grant proposal. Be warm, concise, and professional. Scale urgency to DAYS_LEFT: 14 = calm heads-up; 7/3 = encouraging nudge; 1/0 = urgent but supportive.

ABSOLUTE RULES:
1. USE ONLY THE FACTS PROVIDED. The only facts you may state are PROPOSAL_TITLE, SPONSOR, DAYS_LEFT, and the OPEN_TASKS titles given to you. Invent or guess nothing else.
2. NO FABRICATED SPECIFICS. Never state a deadline date, a dollar amount, a program/award number, a person's name, or any requirement -- none of these are provided, so mentioning them would be a hallucination.
3. TASKS ARE FIXED. "prioritized_tasks" MUST be a reordering of EXACTLY the provided OPEN_TASKS titles, copied verbatim. Never invent, rename, merge, split, or drop a task. If OPEN_TASKS is empty, return [].
4. NO greeting, sign-off, links, or date -- the email template adds the greeting/button/footer. Start with the first real sentence.

Output ONLY a JSON object with EXACTLY:
{
  "body_paragraph": "2-4 sentences naming the proposal title and sponsor and reflecting the urgency.",
  "prioritized_tasks": ["<the OPEN_TASKS titles, reordered by what to do FIRST given the time left>"]
}
No markdown, no fences."""


def _ai_tasks_html(ordered_titles: list[str], limit: int = 6) -> str:
    """Render an AI-ordered task list with the same styling as
    _open_tasks_html (which takes SubmissionTask objects)."""
    if not ordered_titles:
        return '<p style="color:#5f6368;font-size:14px;margin:8px 0;">'\
               'Every checklist item is checked off. Just submit.</p>'
    shown = ordered_titles[:limit]
    items = "".join(
        f'<li style="margin:4px 0;color:#202124;">{t}</li>' for t in shown
    )
    more = ""
    if len(ordered_titles) > limit:
        more = (f'<li style="margin:4px 0;color:#5f6368;font-style:italic;">'
                f'... and {len(ordered_titles) - limit} more</li>')
    return f'<ul style="padding-left:18px;margin:8px 0;">{items}{more}</ul>'


def compose_reminder_email_ai(
    submission: Submission,
    user: User,
    days_left: int,
    app_url: str,
    open_tasks: list,
) -> Optional[tuple[str, str]]:
    """AI-personalized reminder. Returns (subject, html) or None on ANY
    failure / unavailability so the caller falls back to the template.
    Subject is deterministic; only the body prose + task order are AI."""
    try:
        from services import gemini_client

        open_titles = [t.title for t in (open_tasks or []) if getattr(t, "title", None)]
        first_name = (user.name.split()[0] if user.name and user.name.strip() else "there")
        # Data only; the strict rules are the system instruction.
        prompt = (
            f"PI_FIRST_NAME: {first_name}"
            + f"\nPROPOSAL_TITLE: {submission.title}"
            + f"\nSPONSOR: {submission.sponsor}"
            + f"\nDAYS_LEFT: {days_left}"
            + "\nOPEN_TASKS (JSON array of titles): " + json.dumps(open_titles, ensure_ascii=False)
        )
        data = gemini_client.generate_json(
            prompt, temperature=0.3, max_output_tokens=1024, timeout_s=12,
            system_instruction=_REMINDER_SYSTEM,
        )
        if not isinstance(data, dict):
            return None

        body = data.get("body_paragraph")
        if not isinstance(body, str) or not body.strip():
            return None
        body = body.strip()

        # Use the AI ordering ONLY if it's a valid permutation/subset of the
        # real open tasks; otherwise keep the original order (never show a
        # hallucinated task title).
        raw_order = data.get("prioritized_tasks")
        ordered = open_titles
        if isinstance(raw_order, list):
            valid = [t for t in raw_order if isinstance(t, str) and t in open_titles]
            if valid and set(valid) == set(open_titles):
                ordered = valid

        phrase = _phrase_days_left(days_left)
        subject = f"{submission.sponsor} {submission.title}: {phrase}"
        greeting = f"Hi {first_name}," if first_name != "there" else "Hi,"
        tasks_html = _ai_tasks_html(ordered)
        deadline_str = (submission.deadline.strftime("%b %d, %Y")
                        if submission.deadline else "no date set")

        html = f"""
    <div style="font-family: 'Google Sans', Arial, sans-serif; max-width: 520px; margin: 0 auto; padding: 32px;">
        <div style="text-align: center; margin-bottom: 24px;">
            <h1 style="color: #4285F4; font-size: 24px; margin: 0;">ORA Navigator</h1>
            <p style="color: #5f6368; font-size: 13px; margin: 4px 0 0;">Morgan State University &middot; Office of Research Administration</p>
        </div>
        <div style="background: #f8f9fa; border-radius: 12px; padding: 24px; border: 1px solid #dadce0;">
            <h2 style="color: #202124; font-size: 18px; margin: 0 0 12px;">
                Your proposal is {phrase}.
            </h2>
            <p style="color: #5f6368; font-size: 14px; line-height: 1.6; margin: 0 0 16px;">
                {greeting} {body}
            </p>
            <h3 style="color: #202124; font-size: 14px; margin: 16px 0 4px;">What to tackle next</h3>
            {tasks_html}
            <div style="text-align: center; margin: 24px 0 4px;">
                <a href="{app_url}/my-proposals" style="display: inline-block; padding: 11px 28px; background: #4285F4; color: white; text-decoration: none; border-radius: 8px; font-weight: 600; font-size: 14px;">
                    Open My Proposals
                </a>
            </div>
        </div>
        <p style="color: #9aa0a6; font-size: 11px; text-align: center; margin-top: 16px; line-height: 1.5;">
            You're getting this because you have an active proposal in ORA Navigator.<br>
            Mark the proposal submitted or withdrawn to stop further reminders.
        </p>
    </div>
    """
        return subject, html
    except Exception as e:
        log.warning(f"[DEADLINE] AI compose failed, will fall back to template: {e}")
        return None


# ===========================================================================
# Query: which submissions are due for a reminder right now?
# ===========================================================================

def find_due_reminders(
    db: Session,
    today: Optional[date] = None,
) -> list[tuple[Submission, User, int]]:
    """Scan all active submissions and return (submission, user,
    threshold_days) tuples for the ones that:
      - have a deadline,
      - sit exactly on a THRESHOLD_DAYS bucket today, and
      - haven't already been notified for that (submission, threshold).

    Pure read -- no inserts. The caller (send_due_reminders) writes the
    log entry only after a successful SMTP send."""
    today = today or _today()
    due: list[tuple[Submission, User, int]] = []

    active_subs = (
        db.query(Submission)
        .filter(Submission.status == "active", Submission.deadline.isnot(None))
        .all()
    )
    for sub in active_subs:
        days_left = days_until(sub.deadline, today)
        threshold = matched_threshold(days_left)
        if threshold is None:
            continue
        already = (
            db.query(DeadlineReminderLog)
            .filter(
                DeadlineReminderLog.submission_id == sub.id,
                DeadlineReminderLog.threshold_days == threshold,
            )
            .first()
        )
        if already:
            continue
        user = db.query(User).filter(User.id == sub.user_id).first()
        if not user or not user.email:
            continue
        due.append((sub, user, threshold))
    return due


# ===========================================================================
# Orchestrator
# ===========================================================================

def send_due_reminders(
    db: Session,
    today: Optional[date] = None,
    send_email_fn=None,
    app_url: str = "https://ora.inavigator.ai",
    use_ai: bool = True,
) -> dict:
    """Run one pass of the watcher: find due reminders, send each one,
    log a row on success. Returns a summary the cron endpoint can return
    as JSON for observability.

    `send_email_fn` is injectable for tests -- defaults to the project's
    SMTP layer. Signature: fn(to_email: str, subject: str, html: str) -> bool.
    On failure (non-True return) we do NOT write the log row, so the
    next run will retry.

    `use_ai` (default True) tries an AI-personalized body first and falls
    back to the deterministic template when the model is unavailable / fails.
    The email ALWAYS sends regardless; pass use_ai=False for deterministic
    tests."""
    if send_email_fn is None:
        from email_service import _send_email as send_email_fn  # type: ignore

    due = find_due_reminders(db, today=today)
    sent = 0
    failed = 0
    for sub, user, threshold in due:
        days_left = days_until(sub.deadline, today)
        effective_days = days_left if days_left is not None else threshold

        # Try the AI-personalized body first; HARD fallback to the template
        # so a reminder always sends even if Gemini is slow/down/unavailable.
        subject = html = None
        if use_ai:
            open_tasks = [t for t in (sub.tasks or []) if t.status != "done"]
            try:
                res = compose_reminder_email_ai(sub, user, effective_days, app_url, open_tasks)
                if res is not None:
                    subject, html = res
            except Exception as e:
                log.warning(f"[DEADLINE] AI compose raised, falling back: {e}")
                subject = html = None
        if subject is None:
            subject, html = compose_reminder_email(
                sub, user, effective_days, app_url=app_url,
            )
        ok = False
        try:
            ok = bool(send_email_fn(user.email, subject, html))
        except Exception as e:
            log.exception(f"[DEADLINE] SMTP send to {user.email} failed: {e}")
            ok = False
        if ok:
            db.add(DeadlineReminderLog(
                submission_id=sub.id,
                threshold_days=threshold,
                sent_to=user.email,
            ))
            db.commit()
            sent += 1
        else:
            failed += 1

    return {
        "scanned": len(due),
        "sent": sent,
        "failed": failed,
        "thresholds": list(THRESHOLD_DAYS),
        "ran_at": datetime.now(timezone.utc).isoformat(),
    }
