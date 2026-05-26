"""Proposals tracker -- create/list/update/delete user submissions and
their seeded checklist tasks. The /api/me/submissions endpoints in
main.py are thin wrappers around these calls.

Cross-user safety: every read / write filters by user_id at the query
level so a user can never see or mutate another user's submission, even
if they construct the URL by hand.
"""

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy.orm import Session

from models import Submission, SubmissionTask, UserMemory
from services.proposal_templates import get_template


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _record_active_grant_memory(db: Session, user_id: int,
                                title: str, sponsor: str) -> None:
    """When a user creates a submission, mirror it into long-term memory
    as an `active_grant` row. This is what lets the chat agent answer
    'what am I working on?' in a future session without re-reading the
    submissions table -- it goes through the same memory pipeline as
    extracted facts."""
    content = f"{sponsor}: {title}"
    # Don't create duplicate memory rows for the same active grant.
    existing = db.query(UserMemory).filter(
        UserMemory.user_id == user_id,
        UserMemory.memory_type == "active_grant",
        UserMemory.content == content,
    ).first()
    if existing:
        return
    db.add(UserMemory(
        user_id=user_id,
        memory_type="active_grant",
        content=content,
    ))
    # Caller owns the commit -- this method is part of a larger txn.


def create_submission(
    db: Session,
    user_id: int,
    title: str,
    sponsor: str,
    deadline: Optional[datetime],
    notes: Optional[str] = None,
) -> Submission:
    """Create a new submission and seed its task list from the sponsor's
    template (NSF / NIH / generic). Also writes a long-term memory row
    so the chat agent knows about the active grant in future sessions."""
    sub = Submission(
        user_id=user_id,
        title=title.strip(),
        sponsor=(sponsor or "Internal").strip() or "Internal",
        deadline=deadline,
        status="active",
        notes=notes,
    )
    db.add(sub)
    db.flush()  # populate sub.id without committing

    # Seed tasks from the template
    template = get_template(sub.sponsor)
    for order, t in enumerate(template):
        db.add(SubmissionTask(
            submission_id=sub.id,
            title=t["title"],
            description=t.get("description"),
            kb_doc_id=t.get("kb_doc_id"),
            due_offset_days=t.get("due_offset_days"),
            status="pending",
            sort_order=order,
        ))

    # Mirror into long-term memory
    _record_active_grant_memory(db, user_id, sub.title, sub.sponsor)

    db.commit()
    db.refresh(sub)
    return sub


def create_submission_from_solicitation(
    db: Session,
    user_id: int,
    extracted: dict,
    title_override: Optional[str] = None,
) -> Submission:
    """Build a Submission from an extractor dict (see services/
    solicitation_extractor.py). Title defaults to extracted program_name
    or program_id; user can override via title_override. Tasks are the
    sponsor template PLUS solicitation-specific tasks for each
    required_attachment that isn't already in the generic checklist.

    The user has reviewed/edited the extracted dict in the UI before this
    call -- we trust what's passed in. This function does not call out
    to Gemini."""
    sponsor = (extracted.get("sponsor") or "Internal").strip() or "Internal"
    program_name = extracted.get("program_name") or extracted.get("program_id") or "Proposal"
    title = (title_override or program_name).strip() or "Proposal"

    # Parse deadline from contract: ISO datetime / plain date / None
    deadline_raw = extracted.get("deadline")
    deadline: Optional[datetime] = None
    if isinstance(deadline_raw, str) and deadline_raw.strip():
        try:
            deadline = datetime.fromisoformat(deadline_raw.replace("Z", "+00:00"))
        except ValueError:
            try:
                deadline = datetime.strptime(deadline_raw[:10], "%Y-%m-%d")
            except ValueError:
                deadline = None

    # Build a structured notes blob carrying the rest of the extracted
    # metadata. This is human-readable AND machine-parseable for any
    # future downstream feature (calendar export, draft critic, etc.).
    notes_lines = []
    if extracted.get("program_id"):
        notes_lines.append(f"Program ID: {extracted['program_id']}")
    if extracted.get("eligibility"):
        notes_lines.append(f"Eligibility: {extracted['eligibility']}")
    if extracted.get("budget_cap"):
        notes_lines.append(f"Budget cap: ${extracted['budget_cap']:,}")
    if extracted.get("submission_portal"):
        notes_lines.append(f"Submission portal: {extracted['submission_portal']}")
    page_limits = extracted.get("page_limits") or {}
    if page_limits:
        pl = ", ".join(f"{k}: {v}p" for k, v in page_limits.items())
        notes_lines.append(f"Page limits: {pl}")
    notes = "\n".join(notes_lines) if notes_lines else None

    sub = Submission(
        user_id=user_id,
        title=title,
        sponsor=sponsor,
        deadline=deadline,
        status="active",
        notes=notes,
    )
    db.add(sub)
    db.flush()

    # Start with the sponsor's standard template
    base_template = get_template(sub.sponsor)
    seen_titles = {t["title"].lower() for t in base_template}
    for order, t in enumerate(base_template):
        db.add(SubmissionTask(
            submission_id=sub.id,
            title=t["title"],
            description=t.get("description"),
            kb_doc_id=t.get("kb_doc_id"),
            due_offset_days=t.get("due_offset_days"),
            status="pending",
            sort_order=order,
        ))

    # Add a task per solicitation-listed attachment that isn't already
    # covered by the base template. This is how the seeded checklist
    # diverges from the generic NSF/NIH template -- THIS solicitation
    # explicitly requires these.
    next_order = len(base_template)
    for attachment in extracted.get("required_attachments") or []:
        att_text = str(attachment).strip()
        if not att_text:
            continue
        if any(att_text.lower() in seen for seen in seen_titles):
            continue
        db.add(SubmissionTask(
            submission_id=sub.id,
            title=f"Prepare required attachment: {att_text}",
            description=(
                f"Required by the solicitation. Confirm the format and "
                f"page limit before submission."
            ),
            kb_doc_id=None,
            due_offset_days=14,
            status="pending",
            sort_order=next_order,
        ))
        next_order += 1

    # Mirror into long-term memory so the chat agent picks it up
    _record_active_grant_memory(db, user_id, sub.title, sub.sponsor)

    db.commit()
    db.refresh(sub)
    return sub


def list_submissions(db: Session, user_id: int) -> list[Submission]:
    """All of THIS user's submissions, newest first."""
    return (
        db.query(Submission)
        .filter(Submission.user_id == user_id)
        .order_by(Submission.created_at.desc())
        .all()
    )


def get_submission(db: Session, submission_id: int,
                   user_id: int) -> Optional[Submission]:
    """Returns None if the submission doesn't exist OR belongs to a
    different user -- callers don't need to discriminate, both are 404."""
    return (
        db.query(Submission)
        .filter(Submission.id == submission_id, Submission.user_id == user_id)
        .first()
    )


def update_submission(
    db: Session,
    submission_id: int,
    user_id: int,
    title: Optional[str] = None,
    sponsor: Optional[str] = None,
    deadline: Optional[datetime] = None,
    status: Optional[str] = None,
    notes: Optional[str] = None,
) -> Optional[Submission]:
    sub = get_submission(db, submission_id, user_id)
    if sub is None:
        return None
    if title is not None:
        sub.title = title.strip() or sub.title
    if sponsor is not None:
        sub.sponsor = sponsor.strip() or sub.sponsor
    if deadline is not None:
        sub.deadline = deadline
    if status is not None and status in ("active", "submitted", "withdrawn"):
        sub.status = status
    if notes is not None:
        sub.notes = notes
    sub.updated_at = _now()
    db.commit()
    db.refresh(sub)
    return sub


def delete_submission(db: Session, submission_id: int, user_id: int) -> bool:
    """Hard delete (and cascade tasks). Returns True if a row was
    removed, False if the submission didn't exist or wasn't this user's."""
    sub = get_submission(db, submission_id, user_id)
    if sub is None:
        return False
    db.delete(sub)
    db.commit()
    return True


# =====================================================================
# Task-level operations
# =====================================================================

def _get_task(db: Session, submission_id: int, task_id: int,
              user_id: int) -> Optional[SubmissionTask]:
    """Fetch a task, gated by submission ownership."""
    return (
        db.query(SubmissionTask)
        .join(Submission, Submission.id == SubmissionTask.submission_id)
        .filter(
            SubmissionTask.id == task_id,
            SubmissionTask.submission_id == submission_id,
            Submission.user_id == user_id,
        )
        .first()
    )


def add_task(
    db: Session,
    submission_id: int,
    user_id: int,
    title: str,
    description: Optional[str] = None,
    due_offset_days: Optional[int] = None,
) -> Optional[SubmissionTask]:
    """Append a custom task to the submission. Returns None if the
    submission doesn't exist or belongs to another user."""
    sub = get_submission(db, submission_id, user_id)
    if sub is None:
        return None
    next_order = (
        db.query(SubmissionTask)
        .filter(SubmissionTask.submission_id == submission_id)
        .count()
    )
    task = SubmissionTask(
        submission_id=submission_id,
        title=title.strip(),
        description=description,
        due_offset_days=due_offset_days,
        status="pending",
        sort_order=next_order,
    )
    db.add(task)
    db.commit()
    db.refresh(task)
    return task


def update_task(
    db: Session,
    submission_id: int,
    task_id: int,
    user_id: int,
    title: Optional[str] = None,
    description: Optional[str] = None,
    status: Optional[str] = None,
    notes: Optional[str] = None,
) -> Optional[SubmissionTask]:
    task = _get_task(db, submission_id, task_id, user_id)
    if task is None:
        return None
    if title is not None:
        task.title = title.strip() or task.title
    if description is not None:
        task.description = description
    if status is not None and status in ("pending", "done"):
        task.status = status
    if notes is not None:
        task.notes = notes
    task.updated_at = _now()
    db.commit()
    db.refresh(task)
    return task


def delete_task(db: Session, submission_id: int, task_id: int,
                user_id: int) -> bool:
    task = _get_task(db, submission_id, task_id, user_id)
    if task is None:
        return False
    db.delete(task)
    db.commit()
    return True
