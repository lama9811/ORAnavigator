"""Proposals tracker -- create/list/update/delete user submissions and
their seeded checklist tasks. The /api/me/submissions endpoints in
main.py are thin wrappers around these calls.

Cross-user safety: every read / write filters by user_id at the query
level so a user can never see or mutate another user's submission, even
if they construct the URL by hand.
"""

from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy.orm import Session

from models import Submission, SubmissionTask, UserMemory
from services.proposal_templates import get_template


def _now() -> datetime:
    return datetime.now(timezone.utc)


# Morgan ORA needs proposals routed internally BEFORE the sponsor's deadline.
# Surface a derived internal deadline this many business days earlier so an
# inexperienced PI plans backward from the real (earlier) institutional cutoff
# rather than the sponsor date and runs out of time.
INTERNAL_ROUTING_BUSINESS_DAYS = 5


def internal_routing_deadline(deadline: Optional[datetime],
                              business_days: int = INTERNAL_ROUTING_BUSINESS_DAYS) -> Optional[datetime]:
    """The institutional routing deadline: `business_days` weekdays before the
    sponsor deadline (skips Sat/Sun; holidays not modeled). Deterministic.
    Returns None when there is no sponsor deadline."""
    if deadline is None or business_days <= 0:
        return deadline
    d = deadline
    remaining = business_days
    while remaining > 0:
        d = d - timedelta(days=1)
        if d.weekday() < 5:        # Mon–Fri
            remaining -= 1
    return d


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
            source=t.get("source"),
        ))

    # Mirror into long-term memory
    _record_active_grant_memory(db, user_id, sub.title, sub.sponsor)

    db.commit()
    db.refresh(sub)
    return sub


def solicitation_notes_lines(extracted: dict) -> list[str]:
    """The human-readable AND machine-parseable summary of an extracted
    solicitation, one line per fact.

    Lifted out of create_submission_from_solicitation so the attach-later path
    writes exactly the same lines. It has to be the same text: the frontend's
    hasSolicitation() reads them by regex to badge the Solicitation button, and
    reconstruct_solicitation_context (below) parses them back out, so a proposal
    that demonstrably has a solicitation but is missing these lines leaves the UI
    quietly disagreeing with the database."""
    extracted = extracted or {}
    notes_lines: list[str] = []
    if extracted.get("program_id"):
        notes_lines.append(f"Program ID: {extracted['program_id']}")
    # Multi-category / recurring solicitations have several deadlines; the
    # `deadline` column carries only the earliest (most restrictive). Surface
    # the full breakdown here so the human sees every category's date.
    if extracted.get("deadline_details"):
        dd = " ".join(str(extracted["deadline_details"]).split())
        notes_lines.append(f"Deadlines: {dd}")
    if extracted.get("eligibility"):
        notes_lines.append(f"Eligibility: {extracted['eligibility']}")
    if extracted.get("budget_cap"):
        notes_lines.append(f"Budget cap: ${extracted['budget_cap']:,}")
    elif str(extracted.get("budget_cap_status") or "").strip().lower() == "not_stated":
        # A POSITIVE finding, worth a line of its own: this solicitation sets no
        # per-award maximum. Without it the PI is told once at review time and
        # then sees an empty field forever, indistinguishable from "we missed it".
        notes_lines.append("Budget cap: none stated")
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
        cap_parts = []
        for c in cap_details:
            cat = _re.sub(r"[;—]+", " ", str(c["category"]))
            cat = _re.sub(r"\s+", " ", cat).strip()
            if cat:
                cap_parts.append(f"{cat} — ${int(c['cap']):,}")
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
    # Persist the FULL required-attachments list verbatim as the authoritative
    # set; the per-attachment tasks seeded
    # elsewhere are deduped against the sponsor template and so are a lossy
    # subset. ";"-separated because attachment names contain commas
    # (e.g. "Facilities, Equipment and Other Resources").
    req_atts = [str(a).strip() for a in (extracted.get("required_attachments") or [])
                if str(a).strip()]
    if req_atts:
        notes_lines.append(f"Required attachments: {'; '.join(req_atts)}")
    return notes_lines


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

    notes_lines = solicitation_notes_lines(extracted)
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
            source=t.get("source"),
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
            # From the solicitation's contract, but with no quote behind it —
            # the attachment NAME, not the sentence that demanded it. When the
            # full requirement read lands, sync_solicitation_requirement_tasks
            # replaces these with quoted rows (the unmatched source_ref is what
            # retires them), which is also what stops one attachment appearing
            # twice under two slightly different names.
            source="solicitation",
            source_ref=f"attachment:{att_text.lower()}",
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
    # Explicit, not left to the FK. solicitation_sources declares ON DELETE
    # CASCADE, which MySQL honours and SQLite ignores unless PRAGMA
    # foreign_keys is ON — so relying on it would leave a stored solicitation
    # document behind on one engine and not the other. Deleting the proposal
    # must delete its document everywhere.
    from models import SolicitationSource
    db.query(SolicitationSource).filter(
        SolicitationSource.submission_id == submission_id
    ).delete(synchronize_session=False)
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
# Solicitation context reconstruction (see the note on the function)
# =====================================================================

import re as _re


# Anchored to line start (MULTILINE) so a decoy "Budget cap:" / "Page limits:"
# phrase embedded mid-sentence in another notes field (e.g. Eligibility) can't
# win over the real, line-leading entry.
_BUDGET_NOTE_RE = _re.compile(r"^Budget cap:\s*\$?([\d,]+)", _re.MULTILINE)
# The no-cap FINDING, distinct from the line being absent altogether (unknown).
_NO_BUDGET_CAP_NOTE_RE = _re.compile(r"^Budget cap:\s*none stated\s*$",
                                     _re.MULTILINE | _re.IGNORECASE)
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

    Returns:
        {budget_cap: int|None, page_limits: dict, required_attachments: list[str]}

    For submissions created MANUALLY (not from a solicitation), every field is
    empty/None.

    NO FEATURE READS THIS TODAY. Its consumer, Draft Critic, was removed
    2026-08-11 — Draft Review replaced it and reads the structured
    `solicitation_json` column instead. Kept because it is the only parser of
    the `notes` solicitation lines, which two write paths still produce, and its
    tests document that round-trip."""
    out: dict = {
        "budget_cap": None,
        "budget_cap_details": [],
        # None means UNKNOWN and is the right answer for every proposal whose
        # notes predate the "none stated" line — absence of the line is not
        # evidence the funder set no cap.
        "budget_cap_status": None,
        "page_limits": {},
        "required_attachments": [],
    }

    notes = sub.notes or ""
    if notes:
        m = _BUDGET_NOTE_RE.search(notes)
        if m:
            try:
                out["budget_cap"] = int(m.group(1).replace(",", ""))
                out["budget_cap_status"] = "stated"
            except ValueError:
                pass
        elif _NO_BUDGET_CAP_NOTE_RE.search(notes):
            out["budget_cap_status"] = "not_stated"
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


# ── The solicitation profile a proposal is reviewed against ─────────────────
# Stored as TEXT + json.dumps on Submission.solicitation_json (golden rule 5).
# This is what "the draft review is according to the solicitation" rests on: no
# stored profile, no review.

import json as _json

SOLICITATION_PROFILE_VERSION = 1


def save_solicitation_profile(db: Session, sub: Submission, payload: dict) -> None:
    """Persist the profile the PI confirmed.

    `checks` is dropped on the way in: it holds callables, which do not
    serialize, and load_solicitation_profile re-attaches them from code. Storing
    a stale copy of anything derivable is how two halves of one fact drift
    apart."""
    stored = {k: v for k, v in (payload or {}).items() if k not in ("checks", "sections")}
    stored.setdefault("version", SOLICITATION_PROFILE_VERSION)
    sub.solicitation_json = _json.dumps(stored)
    sub.updated_at = _now().replace(tzinfo=None)
    db.commit()
    db.refresh(sub)


def load_solicitation_profile(sub: Submission) -> Optional[dict]:
    """The stored profile, rebuilt into the shape draft_review.review_draft takes.

    Returns None — never an empty profile — when nothing is stored or the blob is
    unreadable. Reviewing a draft against zero requirements would still produce a
    confident percentage, and that number would mean nothing at all; a caller
    that gets None can tell the PI to attach their solicitation instead.

    Deterministic rows and the section universe are REBUILT here rather than
    read back, so they always track the contract the PI actually confirmed."""
    raw = getattr(sub, "solicitation_json", None)
    if not raw:
        return None
    try:
        stored = _json.loads(raw)
    except (ValueError, TypeError):
        return None
    if not isinstance(stored, dict):
        return None
    rows = [r for r in (stored.get("requirements") or [])
            if isinstance(r, dict) and r.get("kind") != "deterministic"]
    if not rows:
        return None
    from services import solicitation_profile as _sp
    return _sp.build_generic(
        stored.get("contract") or {},
        rows,
        id=stored.get("id") or "this solicitation",
        title=stored.get("title") or "",
        url=stored.get("url"),
        merit_criteria=stored.get("merit_criteria") or [],
        eligibility_notes=stored.get("eligibility_notes") or [],
    )


def solicitation_summary(sub: Submission) -> Optional[dict]:
    """The small header the UI needs — what this proposal is judged against, and
    how well the solicitation could be read — without shipping every row."""
    raw = getattr(sub, "solicitation_json", None)
    if not raw:
        return None
    try:
        stored = _json.loads(raw)
    except (ValueError, TypeError):
        return None
    if not isinstance(stored, dict):
        return None
    return {
        "id": stored.get("id"),
        "title": stored.get("title"),
        "url": stored.get("url"),
        "requirement_count": len(stored.get("requirements") or []),
        "read_report": stored.get("read_report") or {},
        "extraction": stored.get("extraction") or {},
        "extracted_at": stored.get("extracted_at"),
    }


def sync_solicitation_requirement_tasks(db: Session, sub: Submission,
                                        profile: dict) -> dict:
    """Rebuild the checklist's solicitation half from the stored requirements.

    One task per requirement, each carrying the funder's own sentence in
    `source_quote`. That quote is the whole point: before this, the checklist
    mixed tasks read out of the document with tasks guessed from the sponsor's
    name — a hardcoded "NSF requires a 2-page Data Management Plan" sat beside
    genuinely extracted items, styled identically, and a PI could not tell them
    apart. A row we cannot quote does not become a task at all (golden rule 2).

    What it does to what is already there, and why each rule exists:
      * `solicitation` tasks are keyed by `source_ref` (the requirement id), so
        re-reading is idempotent and a ticked-off task stays ticked. Requirement
        ids are NOT stable across two reads of one document (see CLAUDE.md), so
        this preserves status within a profile, not across a fresh extraction —
        which is the honest limit, not a bug to paper over.
      * `sponsor_template` tasks are RETIRED: they were a guess standing in for
        a document nobody had read, and now the document has been read. Only
        while still `pending` — deleting something the PI ticked off erases
        their record of having done it.
      * `ora_process` tasks are never touched. The Internal Routing Form is in
        no solicitation and is still mandatory.
      * A task with no `source` is never touched either: that is every task
        predating this column, plus anything the PI added by hand.

    Returns {"added", "removed", "kept"}.
    """
    from services.checklist_filter import is_checklist_task

    rows = [r for r in (profile or {}).get("requirements") or []
            if isinstance(r, dict) and (r.get("source") or "").strip()
            and (r.get("label") or "").strip() and (r.get("id") or "").strip()]
    # Only the asks a tick-box can actually help with. A real solicitation
    # yields ~45 requirements and most are content inside one section, which
    # Draft Review checks against the draft itself — a checkbox there could
    # only invite the PI to assert they had covered it. Measured on the
    # human-verified NSF 23-598 list: 24 requirements -> 7 tasks, and none of
    # the thirteen Project Description rows survives. The STORED profile keeps
    # all of them; this filters the checklist, not the review.
    rows = [r for r in rows if is_checklist_task(r)]
    wanted = {str(r["id"]): r for r in rows}

    existing = list(sub.tasks or [])
    by_ref = {t.source_ref: t for t in existing
              if t.source == "solicitation" and t.source_ref}

    removed = 0
    for t in existing:
        stale_requirement = (t.source == "solicitation" and t.source_ref not in wanted)
        superseded_guess = (t.source == "sponsor_template")
        if (stale_requirement or superseded_guess) and t.status != "done":
            db.delete(t)
            removed += 1

    next_order = max([t.sort_order or 0 for t in existing], default=-1) + 1
    added = 0
    for ref, r in wanted.items():
        found = by_ref.get(ref)
        if found is not None:
            # Refresh the wording; the PI's status and notes are theirs.
            found.title = str(r["label"]).strip()
            found.source_quote = str(r["source"]).strip()
            found.description = _requirement_task_description(r)
            continue
        db.add(SubmissionTask(
            submission_id=sub.id,
            title=str(r["label"]).strip(),
            description=_requirement_task_description(r),
            kb_doc_id=None,
            due_offset_days=r.get("due_offset_days") or 14,
            status="pending",
            sort_order=next_order,
            source="solicitation",
            source_ref=ref,
            source_quote=str(r["source"]).strip(),
        ))
        next_order += 1
        added += 1

    db.commit()
    return {"added": added, "removed": removed, "kept": len(wanted) - added}


def _requirement_task_description(r: dict) -> str:
    """One plain sentence. Deliberately does NOT restate the quote — the quote
    is a field of its own, rendered as the funder's words rather than ours."""
    if not (r.get("scored", True)):
        return ("Conditional — the solicitation asks for this only if it applies "
                "to your project.")
    return "Required by this solicitation."


def sync_required_attachment_tasks(db: Session, sub: Submission,
                                   extracted: dict) -> int:
    """Seed a checklist task per required attachment that has none yet.

    The attach-later counterpart to the seeding create_submission_from_solicitation
    does. Additive and idempotent: a task the PI already has (from the sponsor
    template or a previous attach) is left alone, including one they have already
    ticked off. Returns how many were added."""
    existing = {(t.title or "").strip().lower() for t in (sub.tasks or [])}
    next_order = max([t.sort_order or 0 for t in (sub.tasks or [])], default=-1) + 1
    added = 0
    for attachment in (extracted or {}).get("required_attachments") or []:
        att_text = str(attachment).strip()
        if not att_text:
            continue
        title = f"{_REQUIRED_ATTACHMENT_TASK_PREFIX} {att_text}"
        if title.lower() in existing:
            continue
        if any(att_text.lower() in t for t in existing):
            continue          # the sponsor template already covers it
        db.add(SubmissionTask(
            submission_id=sub.id,
            title=title,
            description=("Required by the solicitation. Confirm the format and "
                         "page limit before submission."),
            kb_doc_id=None,
            due_offset_days=14,
            status="pending",
            sort_order=next_order,
        ))
        existing.add(title.lower())
        next_order += 1
        added += 1
    if added:
        db.commit()
    return added


# ── The stored solicitation document ────────────────────────────────────────
# Written once when the document is READ, so a PI is never asked for the same
# solicitation twice and its requirements can be re-read without them.

import hashlib as _hashlib

# An unbound row is a read the user started and never confirmed. Reaped rather
# than kept forever; a day is long enough for any realistic session.
_UNBOUND_SOURCE_TTL = timedelta(days=1)


def save_solicitation_source(db: Session, *, user_id: int, text: str,
                             source_kind: str = "pdf",
                             filename: Optional[str] = None,
                             url: Optional[str] = None) -> Optional[int]:
    """Persist a solicitation's text and return its id, or None if empty.

    Written UNBOUND (submission_id NULL) because on the create flow the proposal
    does not exist yet; bind_solicitation_source attaches it at confirm."""
    text = text or ""
    if not text.strip():
        return None
    from models import SolicitationSource

    # Reap abandoned reads before adding another.
    cutoff = _now().replace(tzinfo=None) - _UNBOUND_SOURCE_TTL
    db.query(SolicitationSource).filter(
        SolicitationSource.user_id == user_id,
        SolicitationSource.submission_id.is_(None),
        SolicitationSource.created_at < cutoff,
    ).delete(synchronize_session=False)

    row = SolicitationSource(
        user_id=user_id, submission_id=None, text=text, chars=len(text),
        source_kind=source_kind, filename=filename, url=url,
        sha256=_hashlib.sha256(text.encode("utf-8", "ignore")).hexdigest(),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row.id


def bind_solicitation_source(db: Session, *, source_id, user_id: int,
                             submission_id: int) -> bool:
    """Attach a stored document to a submission. Returns False if it does not
    exist or belongs to someone else — a source id from the client is never
    trusted to name a row this user may not touch.

    Any document previously bound to this submission is replaced, so re-attaching
    a newer solicitation leaves exactly one source per proposal."""
    try:
        source_id = int(source_id)
    except (TypeError, ValueError):
        return False
    from models import SolicitationSource
    row = db.query(SolicitationSource).filter(
        SolicitationSource.id == source_id,
        SolicitationSource.user_id == user_id,
    ).first()
    if row is None:
        return False
    db.query(SolicitationSource).filter(
        SolicitationSource.submission_id == submission_id,
        SolicitationSource.id != row.id,
    ).delete(synchronize_session=False)
    row.submission_id = submission_id
    db.commit()
    return True


def load_solicitation_source_by_id(db: Session, *, source_id,
                                   user_id: int) -> Optional[dict]:
    """A stored document by its own id, scoped to its owner.

    Used by the requirements read so the create flow reads one document once
    instead of uploading it twice. The id comes from the client, and the row
    holds the PI's unpublished solicitation, so ownership is a filter in the
    query rather than a check after it — None when it does not exist OR belongs
    to somebody else, which the caller reports identically."""
    try:
        source_id = int(source_id)
    except (TypeError, ValueError):
        return None
    from models import SolicitationSource
    row = db.query(SolicitationSource).filter(
        SolicitationSource.id == source_id,
        SolicitationSource.user_id == user_id,
    ).first()
    if row is None:
        return None
    return {"id": row.id, "text": row.text, "chars": row.chars,
            "source_kind": row.source_kind, "filename": row.filename,
            "url": row.url, "created_at": row.created_at}


def load_solicitation_source(db: Session, submission_id: int) -> Optional[dict]:
    """The stored document for a submission: {id, text, chars, source_kind,
    filename, url, created_at}. None when nothing was kept — which is every
    proposal whose solicitation was attached before this table existed."""
    from models import SolicitationSource
    row = (db.query(SolicitationSource)
             .filter(SolicitationSource.submission_id == submission_id)
             .order_by(SolicitationSource.id.desc())
             .first())
    if row is None:
        return None
    return {"id": row.id, "text": row.text, "chars": row.chars,
            "source_kind": row.source_kind, "filename": row.filename,
            "url": row.url, "created_at": row.created_at}


def has_solicitation_source(db: Session, submission_id: int) -> bool:
    from models import SolicitationSource
    return db.query(SolicitationSource.id).filter(
        SolicitationSource.submission_id == submission_id).first() is not None
