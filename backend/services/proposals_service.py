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
    # Multi-category / recurring solicitations have several deadlines; the
    # `deadline` field above carries only the earliest (most restrictive). Surface
    # the full breakdown here so the human sees every category's date.
    if extracted.get("deadline_details"):
        dd = " ".join(str(extracted["deadline_details"]).split())
        notes_lines.append(f"Deadlines: {dd}")
    if extracted.get("eligibility"):
        notes_lines.append(f"Eligibility: {extracted['eligibility']}")
    if extracted.get("budget_cap"):
        notes_lines.append(f"Budget cap: ${extracted['budget_cap']:,}")
    # Multi-category solicitations (NSF/NIH Category I/II/III, tracks) carry a
    # different award max per category; `budget_cap` above is only the smallest.
    # Surface every category cap as a parseable line so the Budget Helper can
    # offer the PI a "Funding category" picker. Em-dash separated; "; " between
    # entries. Only written when there are 2+ categories.
    cap_details = [
        c for c in (extracted.get("budget_cap_details") or [])
        if isinstance(c, dict) and c.get("category") and c.get("cap")
    ]
    if len(cap_details) >= 2:
        cap_parts = [f"{c['category']} — ${int(c['cap']):,}" for c in cap_details]
        notes_lines.append(f"Category caps: {'; '.join(cap_parts)}")
    if extracted.get("submission_portal"):
        notes_lines.append(f"Submission portal: {extracted['submission_portal']}")
    # Sanitize keys (strip the ',;:' that would corrupt the comma-separated
    # round-trip) and emit only positive-integer values, so reconstruct can
    # parse every entry back cleanly.
    page_limits = extracted.get("page_limits") or {}
    if page_limits:
        parts = []
        for k, v in page_limits.items():
            key = _re.sub(r"[,:;]+", " ", str(k))
            key = _re.sub(r"\s+", " ", key).strip()
            mv = _re.search(r"\d+", str(v))
            if key and mv:
                parts.append(f"{key}: {int(mv.group())}p")
        if parts:
            notes_lines.append(f"Page limits: {', '.join(parts)}")
    # Persist the FULL required-attachments list verbatim. Draft Critic
    # reads this as the authoritative set; the per-attachment tasks seeded
    # below are deduped against the sponsor template and so are a lossy
    # subset. ";"-separated because attachment names contain commas
    # (e.g. "Facilities, Equipment and Other Resources").
    req_atts = [str(a).strip() for a in (extracted.get("required_attachments") or [])
                if str(a).strip()]
    if req_atts:
        notes_lines.append(f"Required attachments: {'; '.join(req_atts)}")
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

    # Start with the sponsor's standard template. Pass the extracted program
    # so the NSF EIR checklist is added ONLY for actual Education/EIR programs.
    base_template = get_template(
        sub.sponsor,
        program_name=extracted.get("program_name"),
        program_id=extracted.get("program_id"),
    )
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
    kb_doc_id: Optional[str] = None,
) -> Optional[SubmissionTask]:
    """Append a custom task to the submission. Returns None if the
    submission doesn't exist or belongs to another user. An optional
    kb_doc_id links the task to a KB form/page (resolved to an 'Open form'
    link by _submission_task_to_dict)."""
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
        kb_doc_id=kb_doc_id,
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


# =====================================================================
# Solicitation context reconstruction (for Draft Critic, etc.)
# =====================================================================

import re as _re


# Anchored to line start (MULTILINE) so a decoy "Budget cap:" / "Page limits:"
# phrase embedded mid-sentence in another notes field (e.g. Eligibility) can't
# win over the real, line-leading entry.
_BUDGET_NOTE_RE = _re.compile(r"^Budget cap:\s*\$?([\d,]+)", _re.MULTILINE)
_CATEGORY_CAPS_NOTE_RE = _re.compile(r"^Category caps:\s*(.+)", _re.MULTILINE)
_PAGE_LIMITS_NOTE_RE = _re.compile(r"^Page limits:\s*(.+)", _re.MULTILINE)
_REQUIRED_ATTACHMENTS_NOTE_RE = _re.compile(r"^Required attachments:\s*(.+)", _re.MULTILINE)
_REQUIRED_ATTACHMENT_TASK_PREFIX = "Prepare required attachment:"


def reconstruct_solicitation_context(sub: Submission) -> dict:
    """Pull the structured solicitation context back out of a Submission
    that was created via the from-solicitation flow. Required for Draft
    Critic without a schema change.

    Sources:
      - budget_cap: parsed from notes line "Budget cap: $600,000"
      - page_limits: parsed from notes line "Page limits: project_description: 15p, ..."
      - required_attachments: read from tasks titled "Prepare required attachment: X"

    Returns the shape Draft Critic expects:
        {budget_cap: int|None, page_limits: dict, required_attachments: list[str]}

    For submissions created MANUALLY (not from a solicitation), every
    field is empty/None and Draft Critic falls back to sponsor defaults."""
    out: dict = {
        "budget_cap": None,
        "budget_cap_details": [],
        "page_limits": {},
        "required_attachments": [],
    }

    notes = sub.notes or ""
    if notes:
        m = _BUDGET_NOTE_RE.search(notes)
        if m:
            try:
                out["budget_cap"] = int(m.group(1).replace(",", ""))
            except ValueError:
                pass
        cc = _CATEGORY_CAPS_NOTE_RE.search(notes)
        if cc:
            # "Category I — $30,000,000; Category III — $500,000"
            caps = []
            for part in cc.group(1).split(";"):
                seg = part.split("—", 1)          # split on the em dash
                if len(seg) != 2:
                    continue
                cat = seg[0].strip()
                amt = _re.sub(r"[^\d]", "", seg[1])
                if cat and amt:
                    caps.append({"category": cat, "cap": int(amt)})
            out["budget_cap_details"] = caps
        pm = _PAGE_LIMITS_NOTE_RE.search(notes)
        if pm:
            # Format: "project_description: 15p, data_management_plan: 2p"
            parts = pm.group(1).split(",")
            page_limits: dict = {}
            for part in parts:
                if ":" not in part:
                    continue
                k, v = part.split(":", 1)
                k = k.strip()
                v = v.strip().rstrip("p").rstrip("P").strip()
                try:
                    page_limits[k] = int(v)
                except ValueError:
                    continue
            out["page_limits"] = page_limits

    # Required attachments: the notes line carries the FULL extracted list
    # (authoritative); the "Prepare required attachment: X" tasks are a lossy
    # subset (deduped against the sponsor template at create-time) plus
    # anything the user added by hand. Union them -- notes first, then any
    # task-only extras -- with case-insensitive de-duplication so neither
    # source is lost. Manually-created submissions (no notes line, no such
    # tasks) still yield an empty list.
    ordered: list[str] = []
    seen_lc: set[str] = set()
    if notes:
        ra = _REQUIRED_ATTACHMENTS_NOTE_RE.search(notes)
        if ra:
            for part in ra.group(1).split(";"):
                p = part.strip()
                if p and p.lower() not in seen_lc:
                    seen_lc.add(p.lower())
                    ordered.append(p)
    for task in (sub.tasks or []):
        title = (task.title or "").strip()
        if title.startswith(_REQUIRED_ATTACHMENT_TASK_PREFIX):
            att = title[len(_REQUIRED_ATTACHMENT_TASK_PREFIX):].strip()
            if att and att.lower() not in seen_lc:
                seen_lc.add(att.lower())
                ordered.append(att)
    out["required_attachments"] = ordered

    return out
