"""The file phase: what to check, fetch it, hash it.

Detection hashes the BYTES, deliberately. morgan.edu is served by more than one
backend node and each keeps its own metadata: the same unchanged PI Handbook
returned two different ETags and two different Last-Modified values across five
probes (measured 2026-08-05), while its SHA-256 was identical every time.
Last-Modified is worse than useless — 10 distinct values across 235 files, all
stamped by one bulk re-upload. A HEAD-based check would therefore report files
as changed at random, which is the same defect that forced --audit on the Gemini
page engine.

Cost of doing it properly: ~178 MB per run, median file 210 KB.
"""

from __future__ import annotations

import hashlib
import logging
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Callable, Iterator, Optional

from extractors import extract_text, kind_for_url

log = logging.getLogger(__name__)

TIMEOUT_S = 90
WORKERS = 6
_UA = "Mozilla/5.0 (compatible; ORANavigatorKB/1.0; +https://ora.inavigator.ai)"


@dataclass
class FileResult:
    url: str
    status: int = 0
    digest: str = ""
    content_type: str = ""
    size: int = 0
    text: str = ""
    error: str = ""

    @property
    def unreadable(self) -> bool:
        """True when we could not READ the file. Never means 'it is empty'."""
        return bool(self.error) or self.status != 200 or self.size == 0

    @property
    def kind(self) -> str:
        return kind_for_url(self.url)


def _default_opener(url: str):
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
        return resp.status, resp.headers.get("Content-Type", ""), resp.read()


def fetch(url: str, opener: Optional[Callable] = None) -> FileResult:
    """Download one file and hash it. Never raises."""
    opener = opener or _default_opener
    try:
        status, ctype, raw = opener(url)
    except Exception as e:
        return FileResult(url=url, error=str(e)[:300])

    if status != 200 or not raw:
        return FileResult(
            url=url,
            status=status,
            size=len(raw or b""),
            error="" if status == 200 else f"HTTP {status}",
        )

    return FileResult(
        url=url,
        status=200,
        digest=hashlib.sha256(raw).hexdigest(),
        content_type=ctype,
        size=len(raw),
        text=extract_text(raw, ctype, url),
    )


def fetch_all(
    urls: list,
    on_file: Optional[Callable] = None,
    should_stop: Optional[Callable[[], bool]] = None,
    opener: Optional[Callable] = None,
    workers: int = WORKERS,
) -> Iterator[FileResult]:
    """Yield one FileResult per unique URL, downloading concurrently."""
    targets = [u for u in dict.fromkeys(urls) if u]
    total = len(targets)
    done = 0
    log.info("File phase: %d files to fetch", total)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        for result in pool.map(lambda u: fetch(u, opener), targets):
            done += 1
            if result.unreadable:
                log.warning(
                    "Unreadable file: %s (%s)", result.url, result.error or result.status
                )
            else:
                log.info(
                    "Hashed %d bytes (%d chars of text): %s",
                    result.size, len(result.text), result.url,
                )
            if on_file:
                on_file(result, done, total)
            yield result
            if should_stop and should_stop():
                log.info("Cancelled after %d files", done)
                break


def known_files(docs: dict, snapshot_rows: list) -> dict:
    """{file_url: [doc_id, ...]} from procedure_url.

    Snapshot first, live struct_data overlaid on top — the same precedence and
    the same reason as run._load_url_index: seeded documents carry no
    procedure_url in the datastore, so a datastore-only index maps nothing.

    Only documents that still exist are mapped, so a snapshot row for a deleted
    document cannot resurrect it in the change report.
    """
    url_of = {}
    for row in snapshot_rows or []:
        url = (row.get("procedure_url") or "").strip()
        if url and row.get("doc_id"):
            url_of[row["doc_id"]] = url

    for doc_id, d in (docs or {}).items():
        live = ((d or {}).get("procedure_url") or "").strip()
        if live:
            url_of[doc_id] = live

    by_url = {}
    for doc_id in (docs or {}):
        url = url_of.get(doc_id, "")
        # kind "file" ONLY. A DocuSign PowerForm or Google form serves a dynamic
        # HTML page carrying per-request tokens, so its bytes differ on every
        # fetch — hashing one would report a change on every run, forever. Forms
        # are linkable (the chat attachment feature renders them) but they are
        # not checkable.
        if url and kind_for_url(url) == "file":
            by_url.setdefault(url, []).append(doc_id)

    for doc_ids in by_url.values():
        doc_ids.sort()
    return by_url
