# ORA Document Coverage Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every document on the ORA site reaches the knowledge base after a scrape (new ones drafted, changed ones updated, admin approves each), and the chatbot hands the file to whoever asks for it.

**Architecture:** Two independently shippable phases joined by one field, `procedure_url`. Phase A adds a *file phase* to the existing Cloud Run Job: build a work list from the KB's own `procedure_url` values unioned with document links the crawl sees, download and SHA-256 each file, then draft new documents or per-document updates for admin approval. Phase B resolves that same `procedure_url` in backend code and renders it as a download link under chat answers — the model never writes a URL.

**Tech Stack:** Python 3.10 (Job image), FastAPI backend, SQLAlchemy + Cloud SQL, Vertex AI Search datastore, Gemini via `google-genai`, React 19 frontend.

## Global Constraints

- **Nothing reaches the datastore without an admin clicking Approve.** The Job writes only `scrape_changes` rows and `kb_page_fingerprints` rows. (2026-07-29 rule.)
- **A failed read is never a deletion.** 403/404/timeout/empty extraction → reported, document untouched.
- **Every AI claim quotes its source verbatim**, verified in code with the whitespace-collapsing test `" ".join(s.split())` (golden rule 2). Unverifiable → drop the draft, keep a pointer.
- **Graceful fallback** (golden rule 3): Gemini unavailable → report the change, never guess, never stay silent.
- **Never re-split a multi-document file.** Each derived document is drafted independently from the file text plus *its own* current content.
- **New dependencies in `kb_scraper/requirements.txt` MUST match `backend/requirements.txt` versions** where the package exists in both. `pdfplumber==0.11.1`.
- **Model pinning is per-caller.** The scraper uses `SCRAPE_MODEL` (`gemini-3.6-flash`) at `SCRAPE_MODEL_LOCATION=global`. That location override is required — 3.6-flash 404s in `us-central1`.
- **Backend test command:** `cd backend && JWT_SECRET=test-secret TRUSTED_HOSTS=testserver,localhost,127.0.0.1 python3 -m pytest -q --ignore=tests/test_agent_instruction.py`
- **Never `git push`** unless the user explicitly asks in that message.
- Scraper tests load modules by file path via the `_load()` helper already at the top of `backend/tests/test_kb_scraper.py` — importing the package pulls `google-adk`, which is not always installed.

---

## Phase A — File coverage in the scrape Job

### Task 1: Text extraction per format

**Files:**
- Create: `kb_scraper/extractors.py`
- Modify: `kb_scraper/requirements.txt`
- Test: `backend/tests/test_kb_extractors.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `extract_text(raw: bytes, content_type: str, url: str = "") -> str` — returns `""` for unsupported formats and unreadable bytes, never raises. `MAX_CHARS = 200_000`, `MAX_PDF_PAGES = 40`.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_kb_extractors.py
import importlib.util, sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent.parent
_SCRAPER = _ROOT / "kb_scraper"
sys.path.insert(0, str(_SCRAPER))


def _load(name):
    spec = importlib.util.spec_from_file_location(name, _SCRAPER / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


ex = _load("extractors")


def test_unsupported_format_returns_empty_not_an_error():
    assert ex.extract_text(b"\xd0\xcf\x11\xe0", "application/msword") == ""


def test_garbage_bytes_return_empty_rather_than_raising():
    assert ex.extract_text(b"not a real pdf", "application/pdf") == ""


def test_empty_input_returns_empty():
    assert ex.extract_text(b"", "application/pdf") == ""


def test_kind_for_url_classifies_destinations():
    assert ex.kind_for_url("https://x/f.pdf") == "file"
    assert ex.kind_for_url("https://na2.docusign.net/Member/PowerFormSigning.aspx?x=1") == "form"
    assert ex.kind_for_url("https://forms.gle/abc") == "form"
    assert ex.kind_for_url("https://www.morgan.edu/office-of-research-administration") == "page"


def test_truncation_respects_max_chars():
    assert len(ex._truncate("a" * 500_000)) == ex.MAX_CHARS
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python3 -m pytest tests/test_kb_extractors.py -v`
Expected: FAIL — `No such file or directory: .../kb_scraper/extractors.py`

- [ ] **Step 3: Write minimal implementation**

```python
# kb_scraper/extractors.py
"""Turn a downloaded file into plain text, or into nothing at all.

Every reader here is pure-Python and adds no system libraries to the image.
Returning "" is a first-class outcome, not a failure: a scanned PDF, a legacy
.doc, and a DocuSign form all have no readable body, and the caller must treat
that as "cannot read" rather than "the content is empty" — the difference
between reporting a file and deleting a document.
"""

from __future__ import annotations

import io
import logging
import re

log = logging.getLogger(__name__)

MAX_CHARS = 200_000
MAX_PDF_PAGES = 40

_FILE_EXT = (".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx")
_FORM_HOSTS = ("docusign.net", "forms.gle", "docs.google.com/forms")


def kind_for_url(url: str) -> str:
    """file | form | page — what kind of destination this URL is."""
    low = (url or "").lower()
    if any(h in low for h in _FORM_HOSTS):
        return "form"
    path = low.split("?")[0].split("#")[0]
    if path.endswith(_FILE_EXT):
        return "file"
    return "page"


def _truncate(text: str) -> str:
    return text[:MAX_CHARS]


def _clean(text: str) -> str:
    return _truncate(re.sub(r"\n{3,}", "\n\n", text).strip())


def _pdf(raw: bytes) -> str:
    import pdfplumber

    out = []
    with pdfplumber.open(io.BytesIO(raw)) as pdf:
        for page in pdf.pages[:MAX_PDF_PAGES]:
            out.append(page.extract_text() or "")
    return "\n\n".join(out)


def _docx(raw: bytes) -> str:
    import docx

    d = docx.Document(io.BytesIO(raw))
    parts = [p.text for p in d.paragraphs]
    for table in d.tables:
        for row in table.rows:
            parts.append(" | ".join(c.text.strip() for c in row.cells))
    return "\n".join(parts)


def _pptx(raw: bytes) -> str:
    from pptx import Presentation

    parts = []
    for slide in Presentation(io.BytesIO(raw)).slides:
        for shape in slide.shapes:
            if getattr(shape, "has_text_frame", False):
                parts.append(shape.text_frame.text)
    return "\n".join(parts)


def _xlsx(raw: bytes) -> str:
    import openpyxl

    wb = openpyxl.load_workbook(io.BytesIO(raw), read_only=True, data_only=True)
    parts = []
    for sheet in wb.worksheets:
        parts.append(f"# {sheet.title}")
        for row in sheet.iter_rows(values_only=True):
            cells = [str(c) for c in row if c is not None]
            if cells:
                parts.append(" | ".join(cells))
    return "\n".join(parts)


_READERS = {
    "pdf": _pdf,
    "docx": _docx,
    "pptx": _pptx,
    "xlsx": _xlsx,
    "xls": _xlsx,
}


def _format_of(content_type: str, url: str) -> str:
    ct = (content_type or "").lower()
    if "pdf" in ct:
        return "pdf"
    if "wordprocessingml" in ct:
        return "docx"
    if "presentationml" in ct:
        return "pptx"
    if "spreadsheetml" in ct or "ms-excel" in ct:
        return "xlsx"
    path = (url or "").lower().split("?")[0]
    for ext in ("pdf", "docx", "pptx", "xlsx", "xls"):
        if path.endswith("." + ext):
            return ext
    return ""


def extract_text(raw: bytes, content_type: str = "", url: str = "") -> str:
    """Plain text, or "" when this file has no readable body. Never raises."""
    if not raw:
        return ""
    reader = _READERS.get(_format_of(content_type, url))
    if reader is None:
        return ""
    try:
        return _clean(reader(raw))
    except Exception as e:
        log.warning("Extraction failed for %s: %s", url or "file", e)
        return ""
```

Append to `kb_scraper/requirements.txt`:

```
# File readers for the document phase. All pure-Python — no system libraries,
# so the image gains nothing beyond these wheels. pdfplumber's version MUST
# match backend/requirements.txt:88; the job and the solicitation extractor
# parse the same PDFs and a drift would mean they disagree.
pdfplumber==0.11.1            # backend/requirements.txt:88
python-docx==1.1.2
python-pptx==1.0.2
openpyxl==3.1.5
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && python3 -m pytest tests/test_kb_extractors.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add kb_scraper/extractors.py kb_scraper/requirements.txt backend/tests/test_kb_extractors.py
git commit -m "feat(kb-scrape): read PDF, Word, PowerPoint and Excel files

Returning empty text is a first-class outcome, not a failure: a scanned PDF,
a legacy .doc and a DocuSign form all have no readable body, and the caller
must treat that as cannot-read rather than content-is-empty."
```

---

### Task 2: The file work list, download and hash

**Files:**
- Create: `kb_scraper/files.py`
- Test: `backend/tests/test_kb_files.py`

**Interfaces:**
- Consumes: `extractors.extract_text`, `extractors.kind_for_url` (Task 1).
- Produces:
  - `FileResult` dataclass: `url, status, digest, content_type, size, text, error` + properties `unreadable -> bool`, `kind -> str`.
  - `known_files(docs: dict, snapshot_rows: list[dict]) -> dict[str, list[str]]` — `{file_url: [doc_id, ...]}`.
  - `fetch(url: str, opener=None) -> FileResult` — `opener(url) -> (status, content_type, bytes)`, injectable for tests.
  - `fetch_all(urls, on_file=None, should_stop=None, opener=None, workers=6) -> Iterator[FileResult]`.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_kb_files.py
import hashlib, importlib.util, sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent.parent
_SCRAPER = _ROOT / "kb_scraper"
sys.path.insert(0, str(_SCRAPER))


def _load(name):
    spec = importlib.util.spec_from_file_location(name, _SCRAPER / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


fl = _load("files")

PDF = "https://www.morgan.edu/Documents/ADMINISTRATION/OFFICES/ora/PI/Handbook5.pdf"


def _opener(status=200, ctype="application/pdf", body=b"%PDF-1.4 body"):
    def _open(url):
        return status, ctype, body
    return _open


def test_hash_is_stable_across_repeated_fetches():
    a = fl.fetch(PDF, opener=_opener())
    b = fl.fetch(PDF, opener=_opener())
    assert a.digest == b.digest == hashlib.sha256(b"%PDF-1.4 body").hexdigest()
    assert not a.unreadable


def test_a_403_is_unreadable_and_carries_no_digest():
    r = fl.fetch(PDF, opener=_opener(status=403, body=b""))
    assert r.unreadable
    assert r.digest == ""


def test_an_empty_body_is_unreadable_not_an_empty_document():
    r = fl.fetch(PDF, opener=_opener(body=b""))
    assert r.unreadable


def test_transport_failure_is_unreadable_not_a_crash():
    def boom(url):
        raise TimeoutError("connection reset")
    r = fl.fetch(PDF, opener=boom)
    assert r.unreadable and "connection reset" in r.error


def test_known_files_maps_procedure_url_to_every_document_it_feeds():
    snapshot = [
        {"doc_id": "a", "procedure_url": PDF},
        {"doc_id": "b", "procedure_url": PDF},
        {"doc_id": "c", "procedure_url": "https://www.morgan.edu/office-of-research-administration"},
        {"doc_id": "d", "procedure_url": ""},
    ]
    out = fl.known_files({"a": {}, "b": {}, "c": {}, "d": {}}, snapshot)
    assert out == {PDF: ["a", "b"]}


def test_docusign_and_google_forms_are_never_hashed():
    """A PowerForm URL serves a dynamic HTML page with per-request tokens. Its
    bytes differ on every fetch, so hashing it would report a change every run,
    forever. Forms are linkable, not checkable."""
    snapshot = [
        {"doc_id": "a", "procedure_url": "https://na2.docusign.net/Member/PowerFormSigning.aspx?PowerFormId=abc"},
        {"doc_id": "b", "procedure_url": "https://forms.gle/abc"},
    ]
    assert fl.known_files({"a": {}, "b": {}}, snapshot) == {}


def test_known_files_ignores_snapshot_rows_whose_document_is_gone():
    snapshot = [{"doc_id": "deleted", "procedure_url": PDF}]
    assert fl.known_files({}, snapshot) == {}


def test_live_struct_data_overlays_the_snapshot():
    other = "https://www.morgan.edu/Documents/ADMINISTRATION/OFFICES/ora/new.pdf"
    snapshot = [{"doc_id": "a", "procedure_url": PDF}]
    out = fl.known_files({"a": {"procedure_url": other}}, snapshot)
    assert out == {other: ["a"]}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python3 -m pytest tests/test_kb_files.py -v`
Expected: FAIL — `kb_scraper/files.py` does not exist

- [ ] **Step 3: Write minimal implementation**

```python
# kb_scraper/files.py
"""The file phase: what to check, fetch it, hash it.

Detection hashes the BYTES, deliberately. morgan.edu is served by more than one
backend node and each keeps its own metadata: the same unchanged PI Handbook
returned two different ETags and two different Last-Modified values across five
probes (2026-08-05), while its SHA-256 was identical every time. Last-Modified
is worse than useless — 10 distinct values across 235 files, all stamped by one
bulk re-upload. A HEAD-based check would therefore report files as changed at
random, which is the same defect that forced --audit on the Gemini page engine.

Cost of doing it properly: ~178 MB per run, median file 210 KB.
"""

from __future__ import annotations

import hashlib
import logging
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
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
        return FileResult(url=url, status=status, size=len(raw or b""),
                          error="" if status == 200 else f"HTTP {status}")

    return FileResult(
        url=url,
        status=200,
        digest=hashlib.sha256(raw).hexdigest(),
        content_type=ctype,
        size=len(raw),
        text=extract_text(raw, ctype, url),
    )


def fetch_all(
    urls: list[str],
    on_file: Optional[Callable] = None,
    should_stop: Optional[Callable[[], bool]] = None,
    opener: Optional[Callable] = None,
    workers: int = WORKERS,
) -> Iterator[FileResult]:
    """Yield one FileResult per URL, downloading concurrently."""
    targets = [u for u in dict.fromkeys(urls) if u]
    total = len(targets)
    done = 0
    log.info("File phase: %d files", total)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        for result in pool.map(lambda u: fetch(u, opener), targets):
            done += 1
            if result.unreadable:
                log.warning("Unreadable file: %s (%s)", result.url, result.error or result.status)
            else:
                log.info("Hashed %d bytes (%d chars text): %s",
                         result.size, len(result.text), result.url)
            if on_file:
                on_file(result, done, total)
            yield result
            if should_stop and should_stop():
                log.info("Cancelled after %d files", done)
                break


def known_files(docs: dict, snapshot_rows: list[dict]) -> dict[str, list[str]]:
    """{file_url: [doc_id, ...]} from procedure_url.

    Snapshot first, live struct_data overlaid on top — the same precedence and
    the same reason as run._load_url_index: seeded documents carry no
    procedure_url in the datastore, so a datastore-only index maps nothing.
    Only documents that still exist are mapped, so a snapshot row for a deleted
    document cannot resurrect it in the report.
    """
    url_of: dict[str, str] = {}
    for row in snapshot_rows:
        url = (row.get("procedure_url") or "").strip()
        if url and row.get("doc_id"):
            url_of[row["doc_id"]] = url

    for doc_id, d in (docs or {}).items():
        live = (d.get("procedure_url") or "").strip()
        if live:
            url_of[doc_id] = live

    by_url: dict[str, list[str]] = {}
    for doc_id in (docs or {}):
        url = url_of.get(doc_id, "")
        # kind "file" ONLY. A DocuSign PowerForm or Google form serves a dynamic
        # HTML page carrying per-request tokens, so its bytes differ on every
        # fetch — hashing one would report a change on every run, forever. Forms
        # are linkable (the chat attachment feature renders them) but they are
        # not checkable.
        if url and kind_for_url(url) == "file":
            by_url.setdefault(url, []).append(doc_id)
    for urls in by_url.values():
        urls.sort()
    return by_url
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && python3 -m pytest tests/test_kb_files.py -v`
Expected: PASS (7 tests)

- [ ] **Step 5: Commit**

```bash
git add kb_scraper/files.py backend/tests/test_kb_files.py
git commit -m "feat(kb-scrape): build the file work list, download and hash it

Hashes bytes rather than trusting HTTP validators. The same unchanged PI
Handbook returned two different ETags and two different Last-Modified values
across five probes while its SHA-256 was identical every time -- morgan.edu is
multi-node and each node keeps its own metadata."
```

---

### Task 3: Stop discarding document links during the crawl

**Files:**
- Modify: `kb_scraper/crawler.py` (the `PageResult` dataclass, and the link filter at the end of `_fetch`)
- Test: `backend/tests/test_kb_scraper.py` (append)

**Interfaces:**
- Consumes: `extractors.kind_for_url` (Task 1).
- Produces: `PageResult.file_links: list[str]` — absolute morgan.edu URLs whose kind is `file` or `form`, deduped, discovered on that page. `is_in_scope` is NOT modified, so page crawling is unchanged.

- [ ] **Step 1: Write the failing test**

```python
# append to backend/tests/test_kb_scraper.py
def test_page_result_defaults_to_no_file_links():
    crawler = _load("crawler")
    r = crawler.PageResult(url="https://www.morgan.edu/ora")
    assert r.file_links == []


def test_collect_file_links_keeps_documents_and_forms_only():
    crawler = _load("crawler")
    raw = [
        "/Documents/ADMINISTRATION/OFFICES/ora/PI/Handbook5.pdf",
        "/Documents/ADMINISTRATION/OFFICES/ora/Templates/x.docx",
        "https://na2.docusign.net/Member/PowerFormSigning.aspx?PowerFormId=abc",
        "/office-of-research-administration/pre-award",
        "https://example.com/other.pdf",
        "/Images/Shared/logo.png",
    ]
    out = crawler._collect_file_links(raw, "https://www.morgan.edu/ora")
    assert out == [
        "https://www.morgan.edu/Documents/ADMINISTRATION/OFFICES/ora/PI/Handbook5.pdf",
        "https://www.morgan.edu/Documents/ADMINISTRATION/OFFICES/ora/Templates/x.docx",
        "https://na2.docusign.net/Member/PowerFormSigning.aspx?PowerFormId=abc",
    ]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python3 -m pytest tests/test_kb_scraper.py -k file_link -v`
Expected: FAIL — `PageResult` has no attribute `file_links`

- [ ] **Step 3: Write minimal implementation**

In `kb_scraper/crawler.py`, add to the imports:

```python
from extractors import kind_for_url
```

Add the field to `PageResult` (beside `links`):

```python
    # Document/form URLs seen on this page. Kept separately from `links`
    # because is_in_scope() deliberately rejects /Documents/... — page crawling
    # must not follow them, but the file phase needs to know they exist. This
    # is the only way a file with no KB document is ever discovered.
    file_links: list[str] = field(default_factory=list)
```

Add the helper near `_expand_accordions`:

```python
def _collect_file_links(raw_links, base: str) -> list[str]:
    """Absolute morgan.edu document and form URLs found on a page."""
    out = []
    for link in raw_links:
        url = normalize_url(link, base)
        if not url:
            continue
        low = url.lower()
        if kind_for_url(url) == "file" and "morgan.edu" not in low:
            continue          # only our own documents
        if kind_for_url(url) in ("file", "form") and url not in out:
            out.append(url)
    return out
```

In `_fetch`, beside the existing `result.links = [...]` assignment:

```python
        result.file_links = _collect_file_links(raw_links, url)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && python3 -m pytest tests/test_kb_scraper.py -v`
Expected: PASS (existing tests plus 2 new)

- [ ] **Step 5: Commit**

```bash
git add kb_scraper/crawler.py backend/tests/test_kb_scraper.py
git commit -m "feat(kb-scrape): keep the document links the crawl already sees

is_in_scope rejects /Documents/... so page crawling never follows them, and the
links were being discarded at the moment they were found. Collecting them is
the only way a file with no KB document is ever discovered."
```

---

### Task 4: Draft a document from a file, and update one from a changed file

**Files:**
- Create: `kb_scraper/file_adjudicator.py`
- Test: `backend/tests/test_kb_file_adjudicator.py`

**Interfaces:**
- Consumes: `adjudicator._quote_in`, `adjudicator._parse` (existing, reused — do not reimplement).
- Produces:
  - `Draft` dataclass: `title, content, category, subcategory, what_changed, quote, confidence, grounded` + property `applicable -> bool` (grounded, confidence high|medium, non-empty content).
  - `draft_new(file_text, url, title_hint="", generate=None) -> Draft`
  - `draft_update(file_text, stored_content, doc_title, generate=None) -> Draft`
  - `generate(prompt: str, system: str) -> str` is injectable; default calls Gemini exactly as `adjudicator.adjudicate` does.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_kb_file_adjudicator.py
import importlib.util, json, sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent.parent
_SCRAPER = _ROOT / "kb_scraper"
sys.path.insert(0, str(_SCRAPER))


def _load(name):
    spec = importlib.util.spec_from_file_location(name, _SCRAPER / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


fa = _load("file_adjudicator")

FILE_TEXT = (
    "Morgan State University PI Handbook 5\n"
    "The facilities and administrative rate for on-campus research is 54%.\n"
    "Questions to ask.ora@morgan.edu."
)


def _gen(payload):
    def _g(prompt, system):
        return json.dumps(payload)
    return _g


def test_a_grounded_draft_is_applicable():
    d = fa.draft_new(FILE_TEXT, "https://x/h5.pdf", generate=_gen({
        "title": "PI Handbook 5",
        "content": "The on-campus F&A rate is 54%.",
        "category": "pre_award",
        "subcategory": "handbooks",
        "quote": "The facilities and administrative rate for on-campus research is 54%.",
        "confidence": "high",
    }))
    assert d.grounded and d.applicable
    assert d.title == "PI Handbook 5"


def test_a_quote_absent_from_the_file_is_dropped_and_the_draft_refused():
    d = fa.draft_new(FILE_TEXT, "https://x/h5.pdf", generate=_gen({
        "title": "PI Handbook 5",
        "content": "The on-campus F&A rate is 99%.",
        "quote": "The rate is 99% as of 2027.",
        "confidence": "high",
    }))
    assert not d.grounded
    assert not d.applicable
    assert d.quote == ""


def test_a_quote_differing_only_in_whitespace_still_verifies():
    d = fa.draft_new(FILE_TEXT, "https://x/h5.pdf", generate=_gen({
        "title": "T", "content": "c",
        "quote": "The facilities and administrative rate\n   for on-campus research is 54%.",
        "confidence": "high",
    }))
    assert d.grounded


def test_empty_file_text_never_produces_a_draft():
    d = fa.draft_new("", "https://x/h5.pdf", generate=_gen({"title": "x", "content": "y"}))
    assert not d.applicable and d.content == ""


def test_model_failure_degrades_to_an_unapplicable_draft():
    def boom(prompt, system):
        raise RuntimeError("503")
    d = fa.draft_new(FILE_TEXT, "https://x/h5.pdf", generate=boom)
    assert not d.applicable


def test_update_preserves_detail_the_file_does_not_contradict():
    stored = "The on-campus F&A rate is 50%. The IRB chair is Benjamin Welsh, Ph.D."
    d = fa.draft_update(FILE_TEXT, stored, "F&A Rates", generate=_gen({
        "content": "The on-campus F&A rate is 54%. The IRB chair is Benjamin Welsh, Ph.D.",
        "what_changed": "On-campus F&A rate 50% -> 54%",
        "quote": "The facilities and administrative rate for on-campus research is 54%.",
        "confidence": "high",
    }))
    assert d.applicable
    assert "Benjamin Welsh" in d.content


def test_update_with_no_stored_content_is_not_applicable():
    d = fa.draft_update(FILE_TEXT, "", "T", generate=_gen({"content": "x", "quote": "y"}))
    assert not d.applicable
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python3 -m pytest tests/test_kb_file_adjudicator.py -v`
Expected: FAIL — `kb_scraper/file_adjudicator.py` does not exist

- [ ] **Step 3: Write minimal implementation**

```python
# kb_scraper/file_adjudicator.py
"""Draft a KB document from a file, or update one whose file changed.

Two prompts, one grounding rule. Every draft must quote the file verbatim, and
the quote is verified in code against the extracted text with the same
whitespace-collapsing test the page adjudicator and section_coach use. A draft
that cannot prove where it came from is dropped and the caller reports a
pointer instead — better nothing than a confident invention.

draft_update is deliberately NOT "rewrite this document from the file". The
stored content is LLM-summarised prose plus hand-authored material a scrape
cannot regenerate (key_facts in 51 documents, leadership_history,
irb_voting_members, staff phone/office). The prompt therefore receives both
sides and is told to change only what the file contradicts.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Callable, Optional

from adjudicator import _parse, _quote_in     # one grounding test, one parser

log = logging.getLogger(__name__)

MODEL = os.getenv("SCRAPE_MODEL", "gemini-2.5-flash")
_MAX_FILE_CHARS = 24_000


@dataclass
class Draft:
    title: str = ""
    content: str = ""
    category: str = ""
    subcategory: str = ""
    what_changed: str = ""
    quote: str = ""
    confidence: str = "low"
    grounded: bool = False

    @property
    def applicable(self) -> bool:
        return (
            self.grounded
            and self.confidence in ("high", "medium")
            and bool(self.content.strip())
        )


_NEW_SYSTEM = """You maintain the Morgan State University Office of Research Administration knowledge base.

You are given the text of a document published on morgan.edu that the knowledge base does not yet cover. Write the knowledge base entry for it.

Return STRICT JSON, no markdown fence:
{
  "title": "a short human title, e.g. 'PI Handbook 5 — Grant-Related Processes'",
  "content": "the knowledge base entry",
  "category": "pre_award|post_award|research_compliance|trainings|resources|about|policies_and_guidelines|funding_sources",
  "subcategory": "a short slug",
  "quote": "a VERBATIM span copied from the document text. Copy exactly; do not paraphrase or fix anything.",
  "confidence": "high"|"medium"|"low"
}

Rules for content:
  * A concise reference a research administrator can act on — the facts, rules,
    deadlines, rates, contacts and required steps. Not a page dump, not a summary
    of what the document is "about".
  * Every statement must be supported by the document text. Invent nothing.
  * If the text is too fragmentary to write a useful entry, set confidence "low".
"""

_UPDATE_SYSTEM = """You maintain the Morgan State University Office of Research Administration knowledge base.

A source document on morgan.edu changed. You are given the CURRENT document text and the EXISTING knowledge base entry written from an earlier version.

Update the entry so it matches the current document.

Return STRICT JSON, no markdown fence:
{
  "content": "the full updated knowledge base entry",
  "what_changed": "one sentence, concrete — name the old and new value",
  "quote": "a VERBATIM span copied from the CURRENT document text showing the change",
  "confidence": "high"|"medium"|"low"
}

Rules — read these carefully:
  * You are EDITING, not rewriting. Change only what the current document
    contradicts.
  * PRESERVE every detail the current document does not address. The existing
    entry contains hand-written material that does not appear in the document at
    all — committee member lists, staff phone numbers and offices, historical
    notes. Deleting any of it is a failure, even if the document does not mention it.
  * Invent nothing absent from the current document.
"""


def _default_generate(prompt: str, system: str) -> str:
    from google import genai
    from google.genai import types

    client = genai.Client(
        vertexai=True,
        project=os.getenv("GOOGLE_CLOUD_PROJECT"),
        # gemini-3.6-flash 404s in us-central1 and answers only on `global`.
        location=os.getenv(
            "SCRAPE_MODEL_LOCATION", os.getenv("GOOGLE_CLOUD_LOCATION", "us-central1")
        ),
    )
    response = client.models.generate_content(
        model=MODEL,
        contents=prompt,
        config=types.GenerateContentConfig(
            system_instruction=system,
            temperature=0.1,
            max_output_tokens=8192,
            thinking_config=types.ThinkingConfig(thinking_budget=0),
        ),
    )
    return getattr(response, "text", "") or ""


def _run(prompt: str, system: str, file_text: str, generate: Optional[Callable]) -> Optional[dict]:
    generate = generate or _default_generate
    try:
        return _parse(generate(prompt, system))
    except Exception as e:
        log.warning("File adjudication failed: %s", e)
        return None


def draft_new(file_text: str, url: str, title_hint: str = "",
              generate: Optional[Callable] = None) -> Draft:
    """Write a KB entry for a document the KB does not cover. Never raises."""
    text = (file_text or "")[:_MAX_FILE_CHARS]
    if not text.strip():
        return Draft(what_changed="The file has no readable text.")

    prompt = (
        f"DOCUMENT URL: {url}\n"
        f"SUGGESTED TITLE: {title_hint or url.rsplit('/', 1)[-1]}\n\n"
        f"=== DOCUMENT TEXT ===\n{text}\n"
    )
    data = _run(prompt, _NEW_SYSTEM, text, generate)
    if not isinstance(data, dict):
        return Draft(what_changed="A draft could not be produced for this file.")

    quote = str(data.get("quote") or "").strip()
    draft = Draft(
        title=str(data.get("title") or "").strip()[:300],
        content=str(data.get("content") or "").strip(),
        category=str(data.get("category") or "").strip(),
        subcategory=str(data.get("subcategory") or "").strip(),
        quote=quote,
        confidence=str(data.get("confidence") or "low").lower(),
        grounded=_quote_in(quote, text),
    )
    if not draft.grounded:
        log.info("Ungrounded draft for %s; reporting without one", url)
        draft.quote = ""
        draft.confidence = "low"
    if draft.confidence not in ("high", "medium", "low"):
        draft.confidence = "low"
    return draft


def draft_update(file_text: str, stored_content: str, doc_title: str = "",
                 generate: Optional[Callable] = None) -> Draft:
    """Update one existing entry from its changed source file. Never raises."""
    text = (file_text or "")[:_MAX_FILE_CHARS]
    if not text.strip() or not (stored_content or "").strip():
        return Draft(what_changed="The file changed but could not be compared automatically.")

    prompt = (
        f"ENTRY TITLE: {doc_title}\n\n"
        f"=== CURRENT DOCUMENT TEXT ===\n{text}\n\n"
        f"=== EXISTING KNOWLEDGE BASE ENTRY ===\n{stored_content}\n"
    )
    data = _run(prompt, _UPDATE_SYSTEM, text, generate)
    if not isinstance(data, dict):
        return Draft(what_changed="The file changed. No draft was produced — review it by hand.")

    quote = str(data.get("quote") or "").strip()
    draft = Draft(
        content=str(data.get("content") or "").strip(),
        what_changed=str(data.get("what_changed") or "").strip()[:1000],
        quote=quote,
        confidence=str(data.get("confidence") or "low").lower(),
        grounded=_quote_in(quote, text),
    )
    if not draft.grounded:
        log.info("Ungrounded update for %r; reporting without a draft", doc_title)
        draft.quote = ""
        draft.confidence = "low"
    if draft.confidence not in ("high", "medium", "low"):
        draft.confidence = "low"
    return draft
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && python3 -m pytest tests/test_kb_file_adjudicator.py -v`
Expected: PASS (7 tests)

- [ ] **Step 5: Commit**

```bash
git add kb_scraper/file_adjudicator.py backend/tests/test_kb_file_adjudicator.py
git commit -m "feat(kb-scrape): draft a document from a file, update one from a changed file

draft_update is deliberately not rewrite-from-the-file. The stored content is
LLM-summarised prose plus hand-authored material a scrape cannot regenerate --
key_facts in 51 documents, leadership_history, irb_voting_members, staff
phone/office -- so the prompt receives both sides and changes only what the
file contradicts."
```

---

### Task 5: Wire the file phase into the Job

**Files:**
- Modify: `kb_scraper/run.py` (imports; collect `file_links` in the page loop; new `_file_phase()` called after it)
- Modify: `backend/models.py` (`ScrapeChange.change_type` comment only — the column is already `String(20)`)
- Test: `backend/tests/test_kb_scraper.py` (append)

**Interfaces:**
- Consumes: `files.known_files`, `files.fetch_all`, `files.FileResult` (Task 2); `file_adjudicator.draft_new`, `draft_update` (Task 4); `PageResult.file_links` (Task 3).
- Produces: `_file_phase(session, run, docs, snapshot_rows, seen_file_links, get_content, dry_run) -> dict` returning stats `{files, unreadable, unchanged, new, updated, reported}`. Writes `ScrapeChange` rows with `change_type in ("file_new", "file_changed", "file_missing")` and `KbPageFingerprint` rows with `engine="file"`.

- [ ] **Step 1: Write the failing test**

```python
# append to backend/tests/test_kb_scraper.py
def test_classify_file_picks_the_right_change_type():
    assert run._classify_file(known=None, doc_ids=[]) == "file_new"
    assert run._classify_file(known="abc", doc_ids=["a"]) == "file_changed"
    assert run._classify_file(known="abc", doc_ids=["a", "b"]) == "file_changed"


def test_first_sighting_of_a_file_with_documents_is_a_baseline_not_a_change():
    # A file we have never hashed, but whose documents already exist, is a
    # baseline. Reporting it would mark every one of ~236 files as changed on
    # the first run.
    assert run._is_file_baseline(known=None, doc_ids=["a"]) is True
    assert run._is_file_baseline(known=None, doc_ids=[]) is False
    assert run._is_file_baseline(known="abc", doc_ids=["a"]) is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python3 -m pytest tests/test_kb_scraper.py -k file -v`
Expected: FAIL — `module 'run' has no attribute '_classify_file'`

- [ ] **Step 3: Write minimal implementation**

In `kb_scraper/run.py`, add near the other imports:

```python
from file_adjudicator import draft_new, draft_update                  # noqa: E402
from files import fetch_all, known_files                              # noqa: E402
```

Add the two pure classifiers (this is what the test drives):

```python
def _is_file_baseline(known, doc_ids) -> bool:
    """First sighting of a file whose documents already exist.

    Record the hash and say nothing. Without this every file reports as changed
    on the first run, because there is no prior hash to compare against.
    """
    return known is None and bool(doc_ids)


def _classify_file(known, doc_ids) -> str:
    return "file_new" if not doc_ids else "file_changed"
```

Collect file links in the page loop — inside `for result in page_source:`, immediately after `seen_urls.add(url)`:

```python
        seen_file_links.update(getattr(result, "file_links", []) or [])
```

and initialise `seen_file_links: set[str] = set()` next to `seen_urls`.

Add the phase itself:

```python
def _file_phase(session, run, docs, snapshot_rows, seen_file_links,
                get_content, dry_run: bool) -> dict:
    """Hash every ORA document, draft what changed and what is new.

    Runs after the page crawl and shares its run row. Nothing here writes to the
    datastore: new documents and updates become pending ScrapeChange rows that an
    admin approves.
    """
    from models import KbPageFingerprint, ScrapeChange

    from extractors import kind_for_url

    by_url = known_files(docs, snapshot_rows)
    # Forms are excluded here too, for the same reason known_files excludes
    # them: their bytes change on every request, so they can never be hashed.
    work = sorted({u for u in (set(by_url) | set(seen_file_links))
                   if kind_for_url(u) == "file"})
    log.info("File phase: %d known, %d seen on site, %d to check",
             len(by_url), len(seen_file_links), len(work))

    prior = {}
    if not dry_run:
        prior = {
            f.url: f.fingerprint
            for f in session.query(KbPageFingerprint)
            .filter(KbPageFingerprint.engine == "file").all()
        }

    stats = {"files": 0, "unreadable": 0, "unchanged": 0,
             "new": 0, "updated": 0, "reported": 0, "baselined": 0}

    def on_file(result, done, total):
        if dry_run:
            return
        run.current_url = result.url[:500]
        if done % PROGRESS_EVERY == 0 or done == total:
            session.commit()

    for result in fetch_all(work, on_file=on_file,
                            should_stop=(None if dry_run else lambda: bool(run.cancel_requested))):
        stats["files"] += 1
        url = result.url
        doc_ids = by_url.get(url, [])

        # A file we could not READ is not a file that was deleted.
        if result.unreadable:
            stats["unreadable"] += 1
            if not dry_run:
                session.add(ScrapeChange(
                    run_id=run.id, url=url, page_title=url.rsplit("/", 1)[-1][:500],
                    change_type="file_missing", status="skipped",
                    doc_id=doc_ids[0] if len(doc_ids) == 1 else None,
                    affected_doc_ids=json.dumps(doc_ids) if doc_ids else None,
                    what_changed=f"Could not read the file ({result.error or result.status}). "
                                 f"Document left unchanged.",
                ))
                session.commit()
            continue

        known = prior.get(url)
        if known == result.digest:
            stats["unchanged"] += 1
            continue

        if _is_file_baseline(known, doc_ids):
            stats["baselined"] += 1
            if not dry_run:
                _upsert_file_fingerprint(session, result, doc_ids)
                session.commit()
            continue

        if dry_run:
            log.info("  %-14s %d doc(s)  %s", _classify_file(known, doc_ids), len(doc_ids), url)
            continue

        kind = _classify_file(known, doc_ids)
        title = url.rsplit("/", 1)[-1].replace("%20", " ")

        if kind == "file_new":
            draft = draft_new(result.text, url, title_hint=title)
            change = ScrapeChange(
                run_id=run.id, url=url, page_title=(draft.title or title)[:500],
                change_type="file_new", status="pending",
                what_changed=draft.what_changed or
                    "New document on morgan.edu with no knowledge base entry. "
                    "Approve to create one from it.",
                evidence_quote=draft.quote or None,
                confidence=draft.confidence,
                new_content=draft.content if draft.applicable else None,
            )
            session.add(change)
            stats["new"] += 1
        else:
            # One draft PER derived document. The file is never re-split across
            # them: each draft sees this file plus that document's own content.
            for doc_id in doc_ids:
                stored = get_content(doc_id) or ""
                draft = draft_update(result.text, stored, doc_id)
                session.add(ScrapeChange(
                    run_id=run.id, url=url, page_title=title[:500],
                    change_type="file_changed", status="pending",
                    doc_id=doc_id,
                    affected_doc_ids=json.dumps(doc_ids) if len(doc_ids) > 1 else None,
                    what_changed=draft.what_changed or "The source file changed.",
                    evidence_quote=draft.quote or None,
                    confidence=draft.confidence,
                    previous_content=stored if draft.applicable else None,
                    new_content=draft.content if draft.applicable else None,
                ))
                if draft.applicable:
                    stats["updated"] += 1
                else:
                    stats["reported"] += 1

        _upsert_file_fingerprint(session, result, doc_ids)
        session.commit()

    log.info("File phase done: %s", stats)
    return stats


def _upsert_file_fingerprint(session, result, doc_ids):
    """Advance the baseline for a file we successfully read.

    engine="file" partitions these from page fingerprints, so the two readers
    can never mistake one another's hashes for their own.
    """
    from models import KbPageFingerprint

    fp = session.query(KbPageFingerprint).filter(
        KbPageFingerprint.url == result.url).first()
    if not fp:
        fp = KbPageFingerprint(url=result.url, fingerprint=result.digest, created_at=_now())
        session.add(fp)
    fp.fingerprint = result.digest
    fp.engine = "file"
    fp.title = result.url.rsplit("/", 1)[-1][:500]
    fp.doc_ids = json.dumps(doc_ids)
    fp.char_count = result.size
    fp.last_seen_at = _now()
    fp.last_changed_at = _now()
```

Call it after the page loop, before the "pages that vanished" block:

```python
    file_stats = _file_phase(
        session, run, docs, _snapshot_rows(), seen_file_links, get_content, args.dry_run
    )
    stats["files"] = file_stats
```

Add `_snapshot_rows()` beside `_load_url_index`:

```python
def _snapshot_rows() -> list[dict]:
    """The committed snapshot, as rows. Source of procedure_url."""
    import json as _json
    from pathlib import Path

    path = Path(__file__).resolve().parent.parent / "backend" / "kb_structured" / "_all_documents.jsonl"
    rows = []
    try:
        for line in path.read_text().splitlines():
            if line.strip():
                rows.append(_json.loads(line))
    except Exception as e:
        log.warning("Could not read the KB snapshot for file mapping (%s)", e)
    return rows
```

Update the `ScrapeChange.change_type` comment in `backend/models.py`:

```python
    # modified | new | removed | unreadable | file_new | file_changed | file_missing
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && JWT_SECRET=test-secret TRUSTED_HOSTS=testserver,localhost,127.0.0.1 python3 -m pytest tests/test_kb_scraper.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add kb_scraper/run.py backend/models.py backend/tests/test_kb_scraper.py
git commit -m "feat(kb-scrape): add the file phase to the job

Hashes every ORA document after the page crawl, drafts an entry for files the
KB does not cover, and drafts one update PER derived document for files that
changed -- never re-splitting one file across the documents that came from it.
First sighting baselines rather than reporting, or all ~236 files would read as
changed on run one."
```

---

### Task 6: Approving a new-file draft creates a downloadable document

**Files:**
- Modify: `backend/datastore_manager.py` (`create_kb_document` signature + `data` dict)
- Modify: `backend/kb_scrape_service.py` (`approve_change` — add the `file_new` branch)
- Test: `backend/tests/test_datastore_metadata.py` (append), `backend/tests/test_kb_scrape_service.py` (create)

**Interfaces:**
- Consumes: `ScrapeChange` rows with `change_type="file_new"` (Task 5).
- Produces: `create_kb_document(doc_id, title, content, kb_path="", source_url="", procedure_url="")` — writes `procedure_url` into `struct_data` when non-empty. `approve_change` handles `file_new` by creating a document; `file_changed` flows through the existing update path unchanged.

- [ ] **Step 1: Write the failing test**

```python
# append to backend/tests/test_datastore_metadata.py
def test_create_kb_document_stores_the_download_link(monkeypatch):
    """procedure_url is what makes the file reachable. Without it a document is
    created, answered from, and the file it came from cannot be opened."""
    captured = {}

    class _Client:
        def update_document(self, request):
            captured["struct"] = dict(request.document.struct_data)
            return object()

    import datastore_manager as dm
    monkeypatch.setattr(dm, "_get_doc_client", lambda: _Client())
    monkeypatch.setattr(dm, "document_exists", lambda doc_id: False)
    monkeypatch.setattr(dm, "invalidate_content_cache", lambda: None)

    result = dm.create_kb_document(
        doc_id="form_x", title="Form X", content="body",
        kb_path="post_award/forms",
        source_url="https://www.morgan.edu/office-of-research-administration/post-award/forms",
        procedure_url="https://www.morgan.edu/Documents/ADMINISTRATION/OFFICES/ora/x.pdf",
    )
    assert result["success"]
    assert captured["struct"]["procedure_url"].endswith("/x.pdf")
    assert captured["struct"]["source_url"].endswith("/post-award/forms")


def test_create_kb_document_omits_procedure_url_when_absent(monkeypatch):
    captured = {}

    class _Client:
        def update_document(self, request):
            captured["struct"] = dict(request.document.struct_data)
            return object()

    import datastore_manager as dm
    monkeypatch.setattr(dm, "_get_doc_client", lambda: _Client())
    monkeypatch.setattr(dm, "document_exists", lambda doc_id: False)
    monkeypatch.setattr(dm, "invalidate_content_cache", lambda: None)

    dm.create_kb_document(doc_id="d", title="T", content="c")
    assert "procedure_url" not in captured["struct"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && JWT_SECRET=test-secret TRUSTED_HOSTS=testserver,localhost,127.0.0.1 python3 -m pytest tests/test_datastore_metadata.py -k procedure -v`
Expected: FAIL — `create_kb_document() got an unexpected keyword argument 'procedure_url'`

- [ ] **Step 3: Write minimal implementation**

In `backend/datastore_manager.py`, change the signature:

```python
def create_kb_document(
    doc_id: str,
    title: str,
    content: str,
    kb_path: str = "",
    source_url: str = "",
    procedure_url: str = "",
) -> dict:
```

and after the `source_url` block in `data`:

```python
    if procedure_url.strip():
        # The file itself, as distinct from source_url (the page that links it).
        # This is the download link: the chat attachment feature renders it, and
        # the next scrape uses it to map the file back to this document.
        data["procedure_url"] = procedure_url.strip()
```

In `backend/kb_scrape_service.py`, inside `approve_change`, replace the existing `change_type == "new"` guard block with:

```python
    if change.change_type == "file_new":
        from kb_tree import suggest_doc_id

        doc_id = change.doc_id or suggest_doc_id(change.page_title or change.url)
        result = create_kb_document(
            doc_id=doc_id,
            title=change.page_title or doc_id,
            content=change.new_content,
            kb_path=change.kb_path or "",
            source_url="",
            procedure_url=change.url,      # the file — this is the download link
        )
        if not result.get("success"):
            return {"success": False, "message": result.get("message", "Create failed")}
        change.doc_id = doc_id
        change.status = "approved"
        change.reviewed = True
        change.reviewed_by = user_id
        change.reviewed_at = _now()
        db.commit()
        return {"success": True, "message": f"Created {doc_id}", "doc_id": doc_id}

    if change.change_type == "new":
        return {
            "success": False,
            "message": "This is a new page with no document. Create one with the New button, "
                       "choosing where it belongs in the tree.",
        }
```

Add the `kb_path` column to `ScrapeChange` in `backend/models.py`:

```python
    # Proposed tree placement for a file_new draft. Validated against
    # kb_tree.node_paths() before use; blank lands the document in Unfiled,
    # which is visible and one click from filed.
    kb_path = Column(String(255), nullable=True)
```

and the self-healing migration in `backend/main.py:init_db()`, beside the others:

```python
        try:
            db.execute(text("SELECT kb_path FROM scrape_changes LIMIT 1"))
        except Exception:
            db.execute(text("ALTER TABLE scrape_changes ADD COLUMN kb_path VARCHAR(255)"))
            db.commit()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && JWT_SECRET=test-secret TRUSTED_HOSTS=testserver,localhost,127.0.0.1 python3 -m pytest tests/test_datastore_metadata.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/datastore_manager.py backend/kb_scrape_service.py backend/models.py backend/main.py backend/tests/test_datastore_metadata.py
git commit -m "feat(kb): approving a new-file draft creates a downloadable document

create_kb_document wrote source_url and had no procedure_url at all, so a
document drafted from a newly-found PDF would be created, answered from, and
offer no way to reach the file. The two URLs now mean different things:
source_url is the page (provenance, what citations point at), procedure_url is
the file (the download)."
```

---

### Task 7: Show the new change types in the admin panel

**Files:**
- Modify: `frontend/src/components/KbScrapePanel.jsx` (`TYPE_LABEL`)
- Test: manual — `cd frontend && npm run build`

**Interfaces:**
- Consumes: `change_type` values from Task 5.
- Produces: no new exports.

- [ ] **Step 1: Add the labels**

```javascript
const TYPE_LABEL = {
  modified: "changed",
  new: "new page",
  removed: "gone from site",
  unreadable: "couldn't read",
  file_new: "new document",
  file_changed: "document changed",
  file_missing: "couldn't fetch file",
};
```

- [ ] **Step 2: Verify the build**

Run: `cd frontend && npm run build`
Expected: build succeeds

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/KbScrapePanel.jsx
git commit -m "feat(admin): label the file change types in the scrape review list"
```

---

## Phase B — The chatbot hands over the document

### Task 8: Resolve a document's download link

**Files:**
- Modify: `backend/services/forms_catalog.py` (add `attachments_for_titles`, add the live overlay to `_all_docs_by_id`)
- Test: `backend/tests/test_document_attachments.py`

**Interfaces:**
- Consumes: existing `resolve_kb_doc`, `_all_docs_by_id`.
- Produces:
  - `attachments_for_titles(titles: list[str], limit: int = 3) -> list[dict]` — each `{title, url, kind}` with `kind in ("file", "form")`, deduped, retrieval order preserved.
  - `_norm_title(s: str) -> str` — the shared normaliser.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_document_attachments.py
import pytest
from services import forms_catalog as fc


@pytest.fixture(autouse=True)
def _fake_docs(monkeypatch):
    docs = {
        "form_pf10": {
            "doc_id": "form_pf10",
            "title": "PF-10 Contractual Personnel Request Form (DocuSign)",
            "url": "https://na2.docusign.net/Member/PowerFormSigning.aspx?PowerFormId=abc",
            "source_url": "https://www.morgan.edu/office-of-research-administration/post-award/forms",
        },
        "fa_rates": {
            "doc_id": "fa_rates",
            "title": "F&A Cost Rates",
            "url": "https://www.morgan.edu/Documents/ADMINISTRATION/OFFICES/ora/IDC/rates.pdf",
            "source_url": "https://www.morgan.edu/office-of-research-administration/pre-award/f-a-cost-rates",
        },
        "page_only": {
            "doc_id": "page_only",
            "title": "Pre-Award Overview",
            "url": "https://www.morgan.edu/office-of-research-administration/pre-award",
            "source_url": "https://www.morgan.edu/office-of-research-administration/pre-award",
        },
    }
    monkeypatch.setattr(fc, "_all_docs_by_id", lambda: docs)
    fc.attachments_for_titles.cache_clear() if hasattr(fc.attachments_for_titles, "cache_clear") else None
    return docs


def test_a_form_document_produces_one_attachment_with_the_exact_url():
    out = fc.attachments_for_titles(["PF-10 Contractual Personnel Request Form (DocuSign)"])
    assert out == [{
        "title": "PF-10 Contractual Personnel Request Form (DocuSign)",
        "url": "https://na2.docusign.net/Member/PowerFormSigning.aspx?PowerFormId=abc",
        "kind": "form",
    }]


def test_a_file_destination_is_attached_as_a_file():
    out = fc.attachments_for_titles(["F&A Cost Rates"])
    assert out[0]["kind"] == "file"
    assert out[0]["url"].endswith("/rates.pdf")


def test_a_web_page_destination_is_not_attached():
    assert fc.attachments_for_titles(["Pre-Award Overview"]) == []


def test_unknown_titles_are_ignored():
    assert fc.attachments_for_titles(["Something Nobody Wrote"]) == []


def test_titles_match_despite_whitespace_and_case():
    out = fc.attachments_for_titles(["  f&a   COST rates  "])
    assert len(out) == 1


def test_duplicates_collapse_and_order_is_preserved():
    out = fc.attachments_for_titles(["F&A Cost Rates", "PF-10 Contractual Personnel Request Form (DocuSign)", "F&A Cost Rates"])
    assert [a["doc_id"] if "doc_id" in a else a["title"] for a in out] == [
        "F&A Cost Rates",
        "PF-10 Contractual Personnel Request Form (DocuSign)",
    ]


def test_the_cap_is_respected():
    out = fc.attachments_for_titles(["F&A Cost Rates", "PF-10 Contractual Personnel Request Form (DocuSign)"], limit=1)
    assert len(out) == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && JWT_SECRET=test-secret TRUSTED_HOSTS=testserver,localhost,127.0.0.1 python3 -m pytest tests/test_document_attachments.py -v`
Expected: FAIL — `module 'services.forms_catalog' has no attribute 'attachments_for_titles'`

- [ ] **Step 3: Write minimal implementation**

Append to `backend/services/forms_catalog.py`:

```python
_FILE_EXT = (".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx")
_FORM_HOSTS = ("docusign.net", "forms.gle", "docs.google.com/forms")


def _norm_title(s: str) -> str:
    return " ".join((s or "").split()).lower()


def _destination_kind(url: str) -> str:
    """file | form | page — only the first two are worth attaching. A page
    destination is already covered by the Sources block."""
    low = (url or "").lower()
    if any(h in low for h in _FORM_HOSTS):
        return "form"
    if low.split("?")[0].split("#")[0].endswith(_FILE_EXT):
        return "file"
    return "page"


@lru_cache(maxsize=1)
def _docs_by_title() -> dict:
    """normalized title -> resolved doc row."""
    out = {}
    for row in _all_docs_by_id().values():
        key = _norm_title(row.get("title"))
        if key and key not in out:
            out[key] = row
    return out


def attachments_for_titles(titles, limit: int = 3) -> list:
    """The documents behind these retrieved chunk titles, as download links.

    Deterministic on purpose. A DocuSign PowerForm URL is ~150 characters of
    opaque GUIDs; a model reproducing one will eventually corrupt a character and
    produce a plausible link to a dead page that no grounding check would catch.
    The model describes the form, this function supplies the URL.
    """
    by_title = _docs_by_title()
    out, seen = [], set()
    for title in titles or []:
        row = by_title.get(_norm_title(title))
        if not row:
            continue
        url = (row.get("url") or "").strip()
        kind = _destination_kind(url)
        if kind == "page" or not url or url in seen:
            continue
        seen.add(url)
        out.append({"title": row.get("title") or title, "url": url, "kind": kind})
        if len(out) >= limit:
            break
    return out
```

Add the live overlay inside `_all_docs_by_id`, immediately before `return out`:

```python
    # Overlay the datastore's own procedure_url values. Required, not cosmetic:
    # a document created by the file scrape exists ONLY in the datastore, so a
    # snapshot-only lookup would give every newly-added document no download
    # link — the exact failure this feature exists to prevent.
    try:
        from datastore_manager import list_datastore_documents

        for doc in list_datastore_documents():
            did = doc.get("id") or doc.get("doc_id")
            live = (doc.get("procedure_url") or "").strip()
            if not did or not live:
                continue
            row = out.setdefault(did, {"doc_id": did, "title": doc.get("title") or did,
                                       "url": "", "source_url": doc.get("source_url") or ""})
            row["url"] = live
    except Exception:
        # The snapshot alone is a usable answer; a datastore blip must not take
        # the forms catalog down with it.
        pass
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && JWT_SECRET=test-secret TRUSTED_HOSTS=testserver,localhost,127.0.0.1 python3 -m pytest tests/test_document_attachments.py -v`
Expected: PASS (7 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/services/forms_catalog.py backend/tests/test_document_attachments.py
git commit -m "feat(chat): resolve a retrieved document to its download link

Deterministic by design: a DocuSign PowerForm URL is ~150 characters of opaque
GUIDs, and a model reproducing one will eventually corrupt a character into a
plausible link to a dead page that no grounding check would catch."
```

---

### Task 9: Attach the links to chat answers

**Files:**
- Modify: `backend/vertex_agent.py` (DELIVER step of `_run_verified` and `_run_verified_stream`)
- Test: `backend/tests/test_document_attachments.py` (append)

**Interfaces:**
- Consumes: `forms_catalog.attachments_for_titles` (Task 8); existing `_is_non_kb_reply`, `_is_personal_identity`.
- Produces: `result["attachments"]: list[dict]` on both chat paths. `_attachments_for_result(message, text, result) -> list` — the single helper both paths call.

- [ ] **Step 1: Write the failing test**

```python
# append to backend/tests/test_document_attachments.py
def test_a_small_talk_turn_gets_no_attachments(monkeypatch):
    import vertex_agent as va
    monkeypatch.setattr(va, "_chunk_titles", lambda r: ["F&A Cost Rates"])
    out = va._attachments_for_result("thanks!", "You're welcome!", {"citations": []})
    assert out == []


def test_a_refusal_gets_no_attachments(monkeypatch):
    import vertex_agent as va
    monkeypatch.setattr(va, "_chunk_titles", lambda r: ["F&A Cost Rates"])
    out = va._attachments_for_result(
        "who is the president of the US?",
        "I can only help with Morgan State University Office of Research Administration questions.",
        {"citations": []},
    )
    assert out == []


def test_a_personal_identity_turn_gets_no_attachments(monkeypatch):
    import vertex_agent as va
    monkeypatch.setattr(va, "_chunk_titles", lambda r: ["F&A Cost Rates"])
    out = va._attachments_for_result("what department am I in?", "You are in Physics.", {"citations": []})
    assert out == []


def test_a_real_kb_answer_gets_its_document(monkeypatch):
    import vertex_agent as va
    from services import forms_catalog as fc
    monkeypatch.setattr(va, "_chunk_titles", lambda r: ["F&A Cost Rates"])
    monkeypatch.setattr(fc, "_all_docs_by_id", lambda: {
        "fa": {"doc_id": "fa", "title": "F&A Cost Rates",
               "url": "https://www.morgan.edu/Documents/x/rates.pdf", "source_url": "https://p"},
    })
    out = va._attachments_for_result("what is the on-campus F&A rate?",
                                     "The on-campus rate is 54%.", {"citations": []})
    assert out and out[0]["url"].endswith("rates.pdf")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && JWT_SECRET=test-secret TRUSTED_HOSTS=testserver,localhost,127.0.0.1 python3 -m pytest tests/test_document_attachments.py -k attachments_for_result -v`
Expected: FAIL — `module 'vertex_agent' has no attribute '_attachments_for_result'`

- [ ] **Step 3: Write minimal implementation**

In `backend/vertex_agent.py`, add near `_extract_citations`:

```python
def _chunk_titles(result: dict) -> list:
    """Titles of the documents this turn actually retrieved, in retrieval order."""
    return [c.get("title", "") for c in (result.get("citations") or []) if c.get("title")]


def _attachments_for_result(message: str, text: str, result: dict) -> list:
    """Download links for the documents behind this answer.

    Cleared on exactly the turns citations are cleared. Stapling KB Sources onto
    "thanks!" was a real production bug in July — the model runs a stray KB
    search on a non-KB turn — and an attachment reintroduces it in a more
    embarrassing form: a DocuSign form offered in reply to "how are you".
    """
    if _is_non_kb_reply(message, text) or _is_personal_identity(message):
        return []
    try:
        from services.forms_catalog import attachments_for_titles

        return attachments_for_titles(_chunk_titles(result))
    except Exception:
        return []
```

In the DELIVER step of **both** `_run_verified` and `_run_verified_stream`, beside where citations are cleared/attached:

```python
        result["attachments"] = _attachments_for_result(message, text, result)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && JWT_SECRET=test-secret TRUSTED_HOSTS=testserver,localhost,127.0.0.1 python3 -m pytest tests/test_document_attachments.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/vertex_agent.py backend/tests/test_document_attachments.py
git commit -m "feat(chat): attach the document behind the answer

Cleared by the same guards that clear citations, so a DocuSign form is never
offered in reply to 'how are you' -- the July Sources-on-smalltalk bug in a
more embarrassing form."
```

---

### Task 10: Attachments survive a cache hit

**Files:**
- Modify: `backend/cache.py` (an `att:` key alongside the existing `cit:` key)
- Modify: `backend/main.py` (`/chat`, `/chat/stream`, `/chat/guest` — store and re-emit)
- Test: `backend/tests/test_document_attachments.py` (append)

**Interfaces:**
- Consumes: `result["attachments"]` (Task 9).
- Produces: `query_cache.set_attachments(query, model, attachments)` and `query_cache.get_attachments(query, model) -> list`.

- [ ] **Step 1: Write the failing test**

```python
# append to backend/tests/test_document_attachments.py
def test_attachments_round_trip_through_the_cache():
    from cache import query_cache
    atts = [{"title": "F&A Cost Rates", "url": "https://x/rates.pdf", "kind": "file"}]
    query_cache.set_attachments("what is the f&a rate?", "m", atts)
    assert query_cache.get_attachments("what is the f&a rate?", "m") == atts


def test_missing_attachments_return_an_empty_list_not_none():
    from cache import query_cache
    assert query_cache.get_attachments("never asked before xyzzy", "m") == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && JWT_SECRET=test-secret TRUSTED_HOSTS=testserver,localhost,127.0.0.1 python3 -m pytest tests/test_document_attachments.py -k cache -v`
Expected: FAIL — `'QueryCache' object has no attribute 'set_attachments'`

- [ ] **Step 3: Write minimal implementation**

In `backend/cache.py`, beside the citation methods (mirroring them exactly — same tiers, same TTLs):

```python
    def set_attachments(self, query: str, model: str, attachments: list) -> None:
        """Attachments ride their own key, for the same reason citations do: a
        cache HIT must still hand over the form, or the second person to ask
        gets the answer without it."""
        if not attachments:
            return
        self._set_side("att:", query, model, attachments)

    def get_attachments(self, query: str, model: str) -> list:
        return self._get_side("att:", query, model) or []
```

If `_set_side`/`_get_side` do not exist, factor the existing `cit:` read/write into them first, so citations and attachments share one implementation rather than a copy.

In `backend/main.py`, at each of the three chat endpoints (`/chat`, `/chat/stream`, `/chat/guest`), beside the existing citation lines. On a MISS, after the answer is produced:

```python
        query_cache.set_attachments(message, model_name, result.get("attachments") or [])
```

On a HIT, where cached citations are restored:

```python
        cached_attachments = query_cache.get_attachments(message, model_name)
```

and include `"attachments": cached_attachments` in the response payload (and in the terminal `done` SSE event for `/chat/stream`) exactly where `citations` already appears.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && JWT_SECRET=test-secret TRUSTED_HOSTS=testserver,localhost,127.0.0.1 python3 -m pytest tests/test_document_attachments.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/cache.py backend/main.py backend/tests/test_document_attachments.py
git commit -m "feat(chat): attachments survive a cache hit

Same reason citations ride a parallel key: without it the second person to ask
for PF-10 gets the answer and no form."
```

---

### Task 11: Render the Documents block

**Files:**
- Modify: `frontend/src/components/Chatbox.jsx`, `frontend/src/components/GuestChatbox.jsx`
- Modify: `frontend/src/components/Chatbox.css`
- Test: manual — `cd frontend && npm run build`, then a hard-reloaded browser check

**Interfaces:**
- Consumes: `attachments` on the chat response and the terminal `done` SSE event (Tasks 9, 10).
- Produces: no new exports.

- [ ] **Step 1: Render the block**

Where the Sources block is rendered in `Chatbox.jsx`, add below it:

```jsx
{msg.attachments?.length > 0 && (
  <div className="chat-attachments">
    <span className="chat-attachments-label">Documents</span>
    {msg.attachments.map((a) => (
      <a
        key={a.url}
        href={a.url}
        target="_blank"
        rel="noreferrer"
        className="chat-attachment"
      >
        <FileText size={13} />
        <span>{a.title}</span>
        <ExternalLink size={11} />
      </a>
    ))}
  </div>
)}
```

Import the icons: `import { FileText, ExternalLink } from "lucide-react";` (merge with the existing lucide import). Carry `attachments` through wherever the message object is built from the response and from the `done` event, next to `citations`. Repeat in `GuestChatbox.jsx`.

- [ ] **Step 2: Style it**

```css
.chat-attachments {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
  margin-top: 10px;
}
.chat-attachments-label {
  font-size: 11px;
  font-weight: 600;
  letter-spacing: 0.04em;
  text-transform: uppercase;
  opacity: 0.65;
}
.chat-attachment {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 5px 10px;
  border: 1px solid var(--border, #d8dee9);
  border-radius: 6px;
  font-size: 13px;
  text-decoration: none;
  color: inherit;
}
.chat-attachment:hover { background: rgba(0, 0, 0, 0.04); }
```

- [ ] **Step 3: Verify the build**

Run: `cd frontend && npm run build`
Expected: build succeeds

- [ ] **Step 4: Verify in a browser**

It is a PWA — use a fresh/incognito window. Ask "give me this form pf-10 contractual personal request" and confirm a **Documents** row appears with a working DocuSign link. Then say "thanks" and confirm **no** Documents row appears.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/Chatbox.jsx frontend/src/components/GuestChatbox.jsx frontend/src/components/Chatbox.css
git commit -m "feat(chat): render the Documents block under an answer"
```

---

### Task 12: Full suite, then the end-to-end check

**Files:** none modified.

- [ ] **Step 1: Run the whole backend suite**

Run: `cd backend && JWT_SECRET=test-secret TRUSTED_HOSTS=testserver,localhost,127.0.0.1 python3 -m pytest -q --ignore=tests/test_agent_instruction.py`
Expected: all pass. The suite was 682 passing before this work; it should be that plus the ~30 added here.

- [ ] **Step 2: Build the frontend**

Run: `cd frontend && npm run build`
Expected: success.

- [ ] **Step 3: Validate the Job build config**

Run: `python3 -c "import yaml; yaml.safe_load(open('cloudbuild.kb-scraper.yaml'))"`
Expected: no output. A typo here blocks the whole scraper deploy.

- [ ] **Step 4: Smoke-test the file phase without touching the database**

Run (needs `gcloud` ADC and the scraper deps installed locally):
`cd kb_scraper && python3 run.py --dry-run --limit 3`
Expected: the page phase reports 3 pages, then `File phase: N known, M seen on site, K to check` and per-file classifications. No database writes.

- [ ] **Step 5: The acceptance test that spans both phases**

Neither half is finished if `procedure_url` is lost in the middle. With the Job image rebuilt and the backend deployed:

1. Run a scrape from the admin dashboard.
2. Find a **new document** row, open its draft, click **Approve**.
3. Ask the chatbot about that document.
4. Confirm a **Documents** link appears and opens the actual file.

- [ ] **Step 6: Commit any fixes, then report**

Do **not** push. Report what passed, what did not, and what was skipped.

---

## Deployment notes (not tasks — read before shipping)

- **The Job image must be rebuilt** or none of Phase A runs, and the rebuild is separate from the main deploy:
  `gcloud builds submit --config=cloudbuild.kb-scraper.yaml . --substitutions=SHORT_SHA=$(git rev-parse --short HEAD)`
  The `SHORT_SHA` substitution is required — it is empty for a local directory submit, which makes the image tag the invalid `kb-scraper:`.
- The currently deployed image is `kb-scraper:557da89` (2026-07-29), which predates even the engine-scoped fingerprints on `main`. Rebuilding also lands those.
- **Backend and frontend deploy together** via `gcloud builds submit --config=cloudbuild.yaml .` — there is no Cloud Build trigger, so a merge to `main` ships nothing. Confirm with
  `gcloud run services describe oranavigator-backend --region=us-central1 --format='value(status.latestReadyRevisionName)'`.
- The first real run after this ships will draft **~34 new documents** at once. That is expected: change detection baselines on its first pass and proposes nothing, so only the new files draft.
