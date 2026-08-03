"""
Cloud Run Job entrypoint: crawl, compare, adjudicate, PROPOSE.

Why a Job and not a background task in the backend — all three of these are
load-bearing:

  * The backend runs with CPU throttled outside requests, so a detached asyncio
    task stalls the moment the response closes.
  * Cloud Run caps a request at 300s. A 382-page crawl does not fit.
  * The backend runs --max-instances=20 with no session affinity, so in-process
    job state is invisible to a progress poll routed elsewhere.

A Job has CPU for its whole life, no request deadline, and writes its progress
to Cloud SQL — where any of the 20 backend instances can read it.

This job NEVER modifies a knowledge base document. Not its content, not its
metadata. Every material change becomes a row in `scrape_changes` with status
`pending`, and the document changes only when an admin clicks Approve in the
dashboard. The badge shown in the admin tree is computed by joining pending rows
at render time, so an unapproved proposal leaves no trace on the document at all.

Usage:
    python run.py --run-id 12            # normal, invoked by the Job
    python run.py --run-id 12 --audit    # one-time: also check never-seen pages
    python run.py --dry-run              # crawl and report, touch no database
    python run.py --dry-run --limit 20   # quick smoke test
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backend"))

from adjudicator import Verdict, adjudicate                           # noqa: E402
from crawler import SEED_URLS, crawl                                  # noqa: E402
from fingerprint import summarize_diff                                # noqa: E402

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", stream=sys.stdout
)
log = logging.getLogger("kb_scraper")

PROGRESS_EVERY = 5      # rows are cheap, but not one write per page


def _now():
    return datetime.now(timezone.utc)


def _load_url_index():
    """morgan.edu URL -> [doc_id, ...] and doc_id -> stored content.

    The URLs come from the committed snapshot, NOT from the datastore. This is
    not a preference — the seeded documents have no source_url in struct_data at
    all (setup_kb_datastore_v8.py writes doc_id/title/category/subcategory/
    source_file/file_path/playwright_verified and stops), so a datastore-only
    index maps zero pages and every page reports as brand new.

    struct_data IS consulted, but only as an overlay for documents authored in
    the admin dashboard after the snapshot was generated — those do carry a
    source_url. Live documents remain the source of truth for CONTENT; the
    snapshot only answers "which page did this come from".
    """
    import json as _json
    from pathlib import Path

    from datastore_manager import get_document_content, list_datastore_documents

    docs: dict[str, dict] = {d["id"]: d for d in list_datastore_documents()}

    url_of: dict[str, str] = {}
    snapshot = Path(__file__).resolve().parent.parent / "backend" / "kb_structured" / "_all_documents.jsonl"
    try:
        for line in snapshot.read_text().splitlines():
            if not line.strip():
                continue
            row = _json.loads(line)
            url = (row.get("source_url") or "").strip().rstrip("/")
            if url:
                url_of[row["doc_id"]] = url
        log.info("Snapshot: %d doc_id -> URL mappings", len(url_of))
    except Exception as e:
        log.warning("Could not read the KB snapshot (%s); falling back to struct_data only", e)

    for doc_id, d in docs.items():
        live_url = (d.get("source_url") or "").strip().rstrip("/")
        if live_url:
            url_of[doc_id] = live_url

    # Only map documents that actually exist in the datastore — a snapshot entry
    # for a deleted document would otherwise resurrect it in the change report.
    by_url: dict[str, list[str]] = {}
    unmapped = 0
    for doc_id in docs:
        url = url_of.get(doc_id)
        if url:
            by_url.setdefault(url, []).append(doc_id)
        else:
            unmapped += 1

    log.info(
        "Datastore: %d documents, %d distinct source URLs, %d unmapped",
        len(docs), len(by_url), unmapped,
    )
    return by_url, docs, get_document_content


def build_parser() -> argparse.ArgumentParser:
    """The CLI surface. Separate from main() so the defaults are testable.

    SCRAPE_ENGINE is read here rather than at import time so the environment
    override can be exercised in tests.
    """
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-id", type=int, default=int(os.getenv("SCRAPE_RUN_ID") or 0))
    ap.add_argument("--dry-run", action="store_true", help="crawl and report; write nothing at all")
    ap.add_argument(
        "--audit", action="store_true",
        help="adjudicate pages on FIRST sighting too, instead of just recording a "
             "baseline. Surfaces drift that accumulated before fingerprinting "
             "existed — the KB was written in May and no page has been compared "
             "since. Costs one model call per mapped page, so this is a one-time "
             "sweep, not a routine run. Nothing is applied either way — it only "
             "changes how many proposals you get to review.",
    )
    ap.add_argument("--limit", type=int, default=0, help="stop after N pages (smoke test)")
    ap.add_argument("--seed", action="append", default=[], help="override seed URL(s)")
    ap.add_argument(
        "--engine", choices=("gemini", "playwright"),
        default=os.getenv("SCRAPE_ENGINE", "playwright"),
        help="who reads the pages. playwright (default): headless Chromium, "
             "walks the site, expands accordions, and returns the page verbatim "
             "so quotes can be verified against it. gemini: gemini-3.6-flash via "
             "the URL Context tool, no browser, works only on URLs the KB "
             "already knows, and is refused outright on 26 of 59 ORA pages.",
    )
    return ap


def resolve_audit(engine: str, audit: bool) -> bool:
    """Should this run adjudicate first sightings as well as changed pages?

    The Gemini engine's page text is a model rendering, not a transcript, so it
    is NOT byte-stable: the same unchanged page read three times at temperature
    0 measured 1444, 1466 and 1478 chars — three different hashes. A fingerprint
    that never matches cannot gate anything, and leaving the gate in would mean
    silently claiming "this page moved" on every page of every run — so that
    engine adjudicates everything and lets the model be the change signal.

    Playwright measured identical hashes across three reads of the same page
    (verified 2026-08-03), so its gate works and is left alone.
    """
    if engine == "gemini":
        return True
    return audit


def load_baseline(session, engine: str) -> dict[str, str]:
    """{url: fingerprint} for pages THIS engine has read before.

    Scoped by engine on purpose. A fingerprint is a hash of the extracted text,
    and the two engines extract the same unchanged page differently — so a hash
    written by the other engine is not a baseline, it is noise that would make
    every page look changed. Rows with engine NULL predate the column and are
    treated the same way.

    This also scopes the removed-page sweep at the end of a run, which is
    `prior` minus the URLs seen: without the filter, switching engines would
    propose that every previously-known page had been deleted from the site.
    """
    from models import KbPageFingerprint

    rows = (
        session.query(KbPageFingerprint)
        .filter(KbPageFingerprint.engine == engine)
        .all()
    )
    return {r.url: r.fingerprint for r in rows}


def upsert_fingerprint(session, *, url: str, digest: str, engine: str, title: str,
                       doc_ids: list, char_count: int, changed: bool):
    """Record what this engine saw at this URL, updating the row if one exists.

    Must be an upsert rather than an insert: `url` is unique, so a page that
    already carries another engine's row would raise IntegrityError on a bare
    add. `last_changed_at` is only stamped when the page actually moved, so it
    keeps meaning "when did this page last change" rather than "when did we
    last look".
    """
    from models import KbPageFingerprint

    fp = session.query(KbPageFingerprint).filter(KbPageFingerprint.url == url).first()
    if fp is None:
        fp = KbPageFingerprint(url=url, created_at=_now())
        session.add(fp)
    fp.fingerprint = digest
    fp.engine = engine
    fp.title = (title or "")[:500]
    fp.doc_ids = json.dumps(doc_ids)
    fp.char_count = char_count
    fp.last_seen_at = _now()
    if changed:
        fp.last_changed_at = _now()
    return fp


def main() -> int:
    args = build_parser().parse_args()

    was_audit = args.audit
    args.audit = resolve_audit(args.engine, args.audit)
    if args.audit and not was_audit:
        log.info("Gemini engine: fingerprint gate disabled (extraction is not byte-stable)")

    from crawler import MAX_PAGES

    max_pages = args.limit or MAX_PAGES
    seeds = args.seed or SEED_URLS

    session = None
    run = None
    if not args.dry_run:
        from db import SessionLocal
        from models import KbPageFingerprint, ScrapeChange, ScrapeRun

        session = SessionLocal()
        run = session.query(ScrapeRun).filter(ScrapeRun.id == args.run_id).first()
        if not run:
            log.error("No ScrapeRun with id=%s", args.run_id)
            return 2
        run.status = "running"
        run.started_at = _now()
        session.commit()

    by_url, docs, get_content = _load_url_index()

    # Baseline from the previous run. Empty on run 1, which is why run 1 only
    # records fingerprints (unless --audit) and every run after examines only
    # what actually moved.
    prior: dict[str, str] = {}
    if not args.dry_run:
        prior = load_baseline(session, args.engine)
    log.info("Baseline: %d known page fingerprints for engine=%s", len(prior), args.engine)

    stats = {"pages": 0, "unreadable": 0, "unchanged": 0, "cosmetic": 0,
             "pending": 0, "new": 0}
    seen_urls: set[str] = set()

    def should_stop() -> bool:
        if args.dry_run:
            return False
        session.refresh(run)
        return bool(run.cancel_requested)

    def on_page(result, done, found):
        if args.dry_run:
            return
        # Write every Nth page, and always on the last one, so the bar lands on
        # 100% rather than stopping at 380/382.
        if done % PROGRESS_EVERY != 0 and done != found:
            return
        run.pages_done = done
        run.pages_found = found
        run.current_url = result.url[:500]
        session.commit()

    # The Gemini engine cannot discover links, so its work list is the KB's own
    # known morgan.edu URLs rather than a walk of the site. That also means it
    # can never surface a page the KB has no document for — use the playwright
    # engine when the question is "what is new on the site".
    if args.engine == "gemini":
        from gemini_crawler import crawl as engine_crawl

        page_source = engine_crawl(
            urls=sorted(by_url), max_pages=max_pages, on_page=on_page, should_stop=should_stop
        )
        log.info("Engine: gemini (%d known URLs, no link discovery)", len(by_url))
    else:
        page_source = crawl(
            seeds=seeds, max_pages=max_pages, on_page=on_page, should_stop=should_stop
        )
        log.info("Engine: playwright (crawling from %d seed(s))", len(seeds))

    for result in page_source:
        stats["pages"] += 1
        url = result.url.rstrip("/")
        seen_urls.add(url)
        doc_ids = by_url.get(url, [])

        # --- Unreadable is NOT a change. Leave the document alone. -----------
        if result.unreadable:
            stats["unreadable"] += 1
            log.warning("Unreadable (%s): %s", result.status or result.error[:60], url)
            if not args.dry_run:
                run.pages_failed = stats["unreadable"]
                session.add(ScrapeChange(
                    run_id=run.id, url=url, page_title=result.title,
                    change_type="unreadable", status="skipped",
                    doc_id=doc_ids[0] if len(doc_ids) == 1 else None,
                    affected_doc_ids=json.dumps(doc_ids) if doc_ids else None,
                    what_changed=f"Could not read the page (status {result.status}). "
                                 f"Document left unchanged.",
                ))
                session.commit()
            continue

        digest = result.digest
        known = prior.get(url)

        # --audit ignores the baseline entirely: compare EVERY mapped page
        # against its document, whether or not the page has moved since we last
        # looked. That is the point of an audit — the KB was written in May and
        # its drift is invisible to a scrape-to-scrape comparison that starts
        # today.
        if known == digest and not (args.audit and doc_ids):
            stats["unchanged"] += 1
            if not args.dry_run:
                fp = session.query(KbPageFingerprint).filter(KbPageFingerprint.url == url).first()
                if fp:
                    fp.last_seen_at = _now()
                    session.commit()
            continue

        # --- First sighting of a page we already have documents for ----------
        # This is a BASELINE, not a change. We have never fingerprinted this URL,
        # so nothing is known to have moved — and the stored content is an LLM
        # summary written in May, not the page text, so an AI comparison would
        # find "material differences" on essentially every page and rewrite
        # documents that are perfectly correct. Record the fingerprint and move
        # on; real changes are only meaningful relative to a baseline.
        if known is None and doc_ids and not args.audit:
            stats["baselined"] = stats.get("baselined", 0) + 1
            if not args.dry_run:
                upsert_fingerprint(
                    session, url=url, digest=digest, engine=args.engine,
                    title=result.title, doc_ids=doc_ids,
                    char_count=len(result.text), changed=False,
                )
                session.commit()
            continue

        # --- Something moved. -------------------------------------------------
        is_new_page = known is None and not doc_ids
        stored = get_content(doc_ids[0]) if len(doc_ids) == 1 else ""

        if args.dry_run:
            # The mapping shape is the thing worth learning from a dry run: a
            # page feeding exactly one document can be updated automatically,
            # one feeding many never can.
            if is_new_page:
                label, bucket = "NEW PAGE — no document", "new"
            elif len(doc_ids) == 1:
                label, bucket = f"1 doc: {doc_ids[0]}", "auto"
            else:
                label, bucket = f"{len(doc_ids)} docs — manual", "manual"
            log.info("  %-46s %s", label, url.replace("https://www.morgan.edu", ""))
            stats[bucket] = stats.get(bucket, 0) + 1
            continue

        # A page with no document has nothing to compare against, so there is no
        # materiality question to ask — skip the model call entirely.
        verdict = (
            adjudicate(result.text, stored, result.title)
            if not is_new_page
            else Verdict(material=True, what_changed="", new_content="", quote="",
                         confidence="low", grounded=False)
        )

        change = ScrapeChange(
            run_id=run.id, url=url, page_title=result.title,
            doc_id=doc_ids[0] if len(doc_ids) == 1 else None,
            affected_doc_ids=json.dumps(doc_ids) if doc_ids else None,
            what_changed=verdict.what_changed or summarize_diff("", result.text),
            evidence_quote=verdict.quote or None,
            confidence=verdict.confidence,
        )

        # NOTHING below writes to the datastore. Every material change becomes a
        # PROPOSAL the admin approves in the dashboard; the document is not
        # touched — not its content, not its metadata — until that click. The
        # scrape's only side effects are rows in scrape_changes and an advanced
        # fingerprint baseline.
        if is_new_page:
            change.change_type = "new"
            change.status = "pending"
            change.new_content = result.text[:60000]
            change.what_changed = (
                "New page on morgan.edu with no knowledge base document. "
                "Approve to create one from the page text."
            )
            stats["new"] += 1

        elif not verdict.material:
            # Fingerprint moved but the meaning did not. Record it, advance the
            # baseline, and never mention it again.
            change.change_type = "modified"
            change.status = "cosmetic"
            stats["cosmetic"] += 1

        elif len(doc_ids) > 1:
            # One page, many documents — the IACUC SOPs page became 52. There is
            # no single document to propose a replacement for, so this is a
            # pointer to go look, not a change that can be approved.
            change.change_type = "modified"
            change.status = "pending"
            change.what_changed = (
                f"{verdict.what_changed} — {len(doc_ids)} documents derive from this "
                f"page, so there is no single replacement to approve. Review them by hand."
            )
            stats["pending"] = stats.get("pending", 0) + 1

        elif verdict.applicable and len(doc_ids) == 1:
            # A concrete, grounded proposal: this exact document, this exact new
            # content, approvable in one click.
            change.change_type = "modified"
            change.status = "pending"
            change.previous_content = stored
            change.new_content = verdict.new_content
            stats["pending"] = stats.get("pending", 0) + 1
            log.info("PROPOSED %s -> %s", url, doc_ids[0])

        else:
            # Material, but the AI could not ground it or had low confidence — so
            # there is no draft worth offering. Flag the page, propose nothing.
            change.change_type = "modified"
            change.status = "pending"
            change.what_changed = (
                f"{verdict.what_changed or 'This page changed.'} "
                f"No draft was produced — review the page against the document by hand."
            )
            stats["pending"] = stats.get("pending", 0) + 1

        session.add(change)

        # Baseline advances for everything we successfully READ, including
        # cosmetic changes — otherwise the same immaterial edit is re-judged,
        # and re-paid for, on every future run.
        upsert_fingerprint(
            session, url=url, digest=digest, engine=args.engine,
            title=result.title, doc_ids=doc_ids,
            char_count=len(result.text), changed=True,
        )

        run.changes_found = stats["pending"] + stats["new"]
        session.commit()

    # --- Pages that vanished from the site --------------------------------
    if not args.dry_run and not run.cancel_requested:
        gone = [u for u in prior if u not in seen_urls]
        for url in gone:
            doc_ids = by_url.get(url, [])
            session.add(ScrapeChange(
                run_id=run.id, url=url, change_type="removed", status="pending",
                doc_id=doc_ids[0] if len(doc_ids) == 1 else None,
                affected_doc_ids=json.dumps(doc_ids) if doc_ids else None,
                what_changed="This page is no longer on morgan.edu. Documents are "
                             "never deleted automatically — approve only after checking "
                             "whether the content moved elsewhere.",
            ))
            stats["pending"] += 1
        if gone:
            log.info("%d page(s) no longer on the site", len(gone))
        session.commit()

    log.info("Done: %s", stats)

    if not args.dry_run:
        run.status = "cancelled" if run.cancel_requested else "succeeded"
        run.finished_at = _now()
        run.changes_found = stats["pending"] + stats["new"]
        run.pages_failed = stats["unreadable"]
        session.commit()
        session.close()

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception as exc:                    # never die without recording why
        log.exception("Scrape failed")
        try:
            from db import SessionLocal
            from models import ScrapeRun
            rid = int(os.getenv("SCRAPE_RUN_ID") or 0)
            if rid:
                s = SessionLocal()
                r = s.query(ScrapeRun).filter(ScrapeRun.id == rid).first()
                if r:
                    r.status, r.error, r.finished_at = "failed", str(exc)[:2000], _now()
                    s.commit()
                s.close()
        except Exception:
            pass
        raise SystemExit(1)
