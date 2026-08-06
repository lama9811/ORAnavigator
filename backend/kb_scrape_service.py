"""
Backend side of the KB web scrape: start a run, read its progress, act on what
it found.

The work itself happens in a Cloud Run Job (see kb_scraper/). This module only
starts it and reads the rows it writes — deliberately, because the backend
cannot host the work: CPU is throttled outside requests, requests die at 300s,
and with --max-instances=20 and no session affinity, in-process job state is
invisible to whichever instance serves the next poll.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from models import KbPageFingerprint, ScrapeChange, ScrapeRun

log = logging.getLogger(__name__)

JOB_NAME = os.getenv("KB_SCRAPER_JOB", "oranavigator-kb-scraper")
JOB_REGION = os.getenv("KB_SCRAPER_REGION", os.getenv("GOOGLE_CLOUD_LOCATION", "us-central1"))
PROJECT = os.getenv("GOOGLE_CLOUD_PROJECT", "")

# A run that has not moved in this long is treated as dead. Without this a
# crashed Job — OOM, image pull failure, the missing run.invoker binding —
# leaves a row stuck in "running" and the one-at-a-time guard locks the feature
# permanently with no way back except a DB edit.
STALE_AFTER_S = 90 * 60


def _now():
    return datetime.now(timezone.utc)


def _seconds_since(dt) -> float:
    if not dt:
        return 0.0
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return (_now() - dt).total_seconds()


def active_run(db: Session):
    """The run currently in flight, if any — after reaping stale ones."""
    run = (
        db.query(ScrapeRun)
        .filter(ScrapeRun.status.in_(["queued", "running"]))
        .order_by(ScrapeRun.id.desc())
        .first()
    )
    if not run:
        return None

    reference = run.started_at or run.created_at
    if _seconds_since(reference) > STALE_AFTER_S:
        log.warning("Reaping stale scrape run %s (no progress in %ss)", run.id, STALE_AFTER_S)
        run.status = "failed"
        run.error = (
            "No progress for over 90 minutes — the job was most likely never "
            "started or died. Check the Cloud Run Job execution logs."
        )
        run.finished_at = _now()
        db.commit()
        return None
    return run


def start_run(db: Session, user_id: int | None) -> ScrapeRun:
    """Create the run row, then ask Cloud Run to execute the Job.

    The row is created FIRST and passed in as SCRAPE_RUN_ID so the job has
    somewhere to write from its very first line — and so a failure to launch is
    still visible in the UI rather than vanishing.
    """
    existing = active_run(db)
    if existing:
        raise RuntimeError(f"A scrape is already running (run #{existing.id})")

    run = ScrapeRun(status="queued", triggered_by=user_id, created_at=_now())
    db.add(run)
    db.commit()
    db.refresh(run)

    try:
        execution = _execute_job(run.id)
        run.execution_name = execution
        db.commit()
    except Exception as e:
        run.status = "failed"
        run.error = f"Could not start the scrape job: {e}"
        run.finished_at = _now()
        db.commit()
        raise

    return run


def _execute_job(run_id: int) -> str:
    """Trigger the Cloud Run Job with SCRAPE_RUN_ID overridden for this run."""
    from google.cloud import run_v2

    if not PROJECT:
        raise RuntimeError("GOOGLE_CLOUD_PROJECT is not set")

    client = run_v2.JobsClient()
    name = f"projects/{PROJECT}/locations/{JOB_REGION}/jobs/{JOB_NAME}"
    request = run_v2.RunJobRequest(
        name=name,
        overrides=run_v2.RunJobRequest.Overrides(
            container_overrides=[
                run_v2.RunJobRequest.Overrides.ContainerOverride(
                    env=[run_v2.EnvVar(name="SCRAPE_RUN_ID", value=str(run_id))]
                )
            ]
        ),
    )
    operation = client.run_job(request=request)
    # Do NOT wait for the operation — it completes when the JOB completes, which
    # is the whole thing we are avoiding. The metadata carries the execution name.
    try:
        return operation.metadata.name  # type: ignore[union-attr]
    except Exception:
        return ""


def run_to_dict(run: ScrapeRun | None) -> dict:
    if not run:
        return {"status": "idle"}

    found = run.pages_found or 0
    done = run.pages_done or 0
    elapsed = _seconds_since(run.started_at) if run.started_at else 0.0

    # Only estimate once there is enough signal to be worth showing; an ETA
    # from three pages is noise dressed as information.
    eta = None
    if run.status == "running" and done >= 10 and found > done and elapsed > 0:
        eta = int((elapsed / done) * (found - done))

    return {
        "id": run.id,
        "status": run.status,
        "pages_done": done,
        "pages_found": found,
        "pages_failed": run.pages_failed or 0,
        "changes_found": run.changes_found or 0,
        "changes_applied": run.changes_applied or 0,
        "current_url": run.current_url or "",
        "percent": int(done * 100 / found) if found else 0,
        "elapsed_s": int(elapsed),
        "eta_s": eta,
        "error": run.error or "",
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "finished_at": run.finished_at.isoformat() if run.finished_at else None,
    }


def change_to_dict(change: ScrapeChange) -> dict:
    try:
        affected = json.loads(change.affected_doc_ids) if change.affected_doc_ids else []
    except Exception:
        affected = []
    return {
        "id": change.id,
        "run_id": change.run_id,
        "url": change.url,
        "page_title": change.page_title or "",
        "change_type": change.change_type,
        "status": change.status,
        "doc_id": change.doc_id,
        "affected_doc_ids": affected,
        "what_changed": change.what_changed or "",
        "evidence_quote": change.evidence_quote or "",
        "confidence": change.confidence or "",
        "reviewed": bool(change.reviewed),
        "reverted": bool(change.reverted),
        "has_diff": bool(change.previous_content and change.new_content),
        "created_at": change.created_at.isoformat() if change.created_at else None,
    }


def approve_change(db: Session, change_id: int, user_id: int | None) -> dict:
    """Apply a proposed change — the ONLY path by which a scrape reaches a document.

    Until this runs, the scrape has written nothing to the datastore: the
    document, its content and its metadata are exactly as they were before the
    crawl. The previous content is captured here (re-read live, not trusted from
    the proposal) so an approval is still undoable.
    """
    from datastore_manager import create_kb_document, get_document_content, update_document

    change = db.query(ScrapeChange).filter(ScrapeChange.id == change_id).first()
    if not change:
        return {"success": False, "message": "Change not found"}
    if change.status != "pending":
        return {"success": False, "message": f"Only pending changes can be approved (this one is {change.status})"}
    if not (change.new_content or "").strip():
        return {
            "success": False,
            "message": "This change has no proposed content — it is a pointer to review the "
                       "page by hand, not something that can be approved.",
        }

    if change.change_type == "file_new":
        # A new FILE, unlike a new page, arrives with everything a document
        # needs: drafted content, a proposed tree path, and — the part that
        # matters — the file's own URL, which becomes procedure_url so the
        # document can actually be downloaded once it exists.
        from kb_tree import suggest_doc_id

        doc_id = change.doc_id or suggest_doc_id(change.page_title or change.url)
        result = create_kb_document(
            doc_id=doc_id,
            title=change.page_title or doc_id,
            content=change.new_content,
            kb_path=change.kb_path or "",
            procedure_url=change.url,
        )
        if not result.get("success"):
            return {"success": False, "message": result.get("message", "Create failed")}

        change.doc_id = doc_id
        change.status = "approved"
        change.reviewed = True
        change.reviewed_by = user_id
        change.reviewed_at = _now()
        db.commit()
        return {
            "success": True,
            "message": f"Created — {doc_id}",
            "doc_id": doc_id,
        }

    if change.change_type == "new":
        return {
            "success": False,
            "message": "This is a new page with no document. Create one with the New button, "
                       "choosing where it belongs in the tree.",
        }

    if not change.doc_id:
        return {"success": False, "message": "No single document to update for this page"}

    # Re-read the live content rather than trusting what the crawl captured: the
    # document may have been edited by hand between the scrape and this click,
    # and that edit is what a revert must restore.
    change.previous_content = get_document_content(change.doc_id)

    result = update_document(change.doc_id, change.new_content.encode("utf-8"))
    if not result.get("success"):
        return {"success": False, "message": result.get("message", "Update failed")}

    change.status = "approved"
    change.reviewed = True
    change.reviewed_by = user_id
    change.reviewed_at = _now()
    db.commit()
    return {"success": True, "message": f"Approved — {change.doc_id} updated", "doc_id": change.doc_id}


def dismiss_reported(db: Session, user_id: int | None) -> dict:
    """Clear every pending proposal that carries no draft.

    These are pointers, not actions: a page feeding many documents can never
    have a single replacement written, so the review list does not offer them
    and there is otherwise no way to get rid of them. Without this they
    accumulate every run — 90 after three — and keep the summary claiming work
    is waiting when none of it can be done.

    Proposals that DO carry a draft are untouched; those are real decisions.
    """
    rows = (
        db.query(ScrapeChange)
        .filter(
            ScrapeChange.status == "pending",
            (ScrapeChange.new_content.is_(None)) | (ScrapeChange.new_content == ""),
        )
        .all()
    )
    for c in rows:
        c.status = "rejected"
        c.reviewed = True
        c.reviewed_by = user_id
        c.reviewed_at = _now()
    db.commit()
    return {"success": True, "dismissed": len(rows),
            "message": f"Dismissed {len(rows)} reported page(s) with nothing to approve"}


def reject_change(db: Session, change_id: int, user_id: int | None) -> dict:
    """Dismiss a proposal. The document was never touched, so there is nothing
    to undo — this only clears it from the review queue and the tree badge."""
    change = db.query(ScrapeChange).filter(ScrapeChange.id == change_id).first()
    if not change:
        return {"success": False, "message": "Change not found"}
    if change.status not in ("pending",):
        return {"success": False, "message": f"Only pending changes can be rejected (this one is {change.status})"}

    change.status = "rejected"
    change.reviewed = True
    change.reviewed_by = user_id
    change.reviewed_at = _now()
    db.commit()
    return {"success": True, "message": "Dismissed"}


def revert_change(db: Session, change_id: int, user_id: int | None) -> dict:
    """Undo an approval, restoring the content that was there before it."""
    from datastore_manager import update_document

    change = db.query(ScrapeChange).filter(ScrapeChange.id == change_id).first()
    if not change:
        return {"success": False, "message": "Change not found"}
    if change.status != "approved":
        return {"success": False, "message": "Only approved changes can be reverted"}
    if change.reverted:
        return {"success": False, "message": "Already reverted"}
    if not change.previous_content:
        return {"success": False, "message": "No previous content was stored for this change"}

    result = update_document(change.doc_id, change.previous_content.encode("utf-8"))
    if not result.get("success"):
        return {"success": False, "message": result.get("message", "Restore failed")}

    change.reverted = True
    change.reviewed_by = user_id
    change.reviewed_at = _now()
    db.commit()
    return {"success": True, "message": f"Reverted {change.doc_id}"}


def pending_by_doc(db: Session) -> dict[str, dict]:
    """doc_id -> the pending proposal against it, for the tree's badge.

    This join is why an unapproved proposal leaves NO trace on the document
    itself — the badge is computed at render time from these rows rather than
    written into struct_data.
    """
    out: dict[str, dict] = {}
    rows = (
        db.query(ScrapeChange)
        .filter(
            ScrapeChange.status == "pending",
            ScrapeChange.doc_id.isnot(None),
            # Only proposals that carry a draft. The review panel lists exactly
            # these (`pending && has_diff`), and without the same filter the
            # badge counts rows the panel will not show — leaving an amber ⚠ on
            # the tree that an admin has no way to clear, having approved
            # everything the queue offered. A badge you cannot act on is a dead
            # end, not information.
            ScrapeChange.new_content.isnot(None),
            ScrapeChange.new_content != "",
        )
        .order_by(ScrapeChange.id.desc())
        .all()
    )
    for c in rows:
        out.setdefault(c.doc_id, {
            "change_id": c.id,
            "what_changed": c.what_changed or "",
            "confidence": c.confidence or "",
            "has_draft": bool((c.new_content or "").strip()),
            "url": c.url,
        })
    return out


def last_finished_run(db: Session):
    return (
        db.query(ScrapeRun)
        .filter(ScrapeRun.status.in_(["succeeded", "failed", "cancelled"]))
        .order_by(ScrapeRun.id.desc())
        .first()
    )


def baseline_size(db: Session) -> int:
    return db.query(KbPageFingerprint).count()
