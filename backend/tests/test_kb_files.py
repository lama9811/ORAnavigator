"""Tests for the scrape's file phase: what gets checked, and what a failed read
means. The distinction that matters most here is unreadable-vs-empty — treating
a 403 as "the content was deleted" is the worst thing this job could do.
"""

import hashlib
import importlib.util
import sys
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


_load("extractors")
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


def test_different_bytes_produce_a_different_digest():
    a = fl.fetch(PDF, opener=_opener(body=b"version one"))
    b = fl.fetch(PDF, opener=_opener(body=b"version two"))
    assert a.digest != b.digest


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
        {"doc_id": "a", "procedure_url":
            "https://na2.docusign.net/Member/PowerFormSigning.aspx?PowerFormId=abc"},
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


def test_fetch_all_yields_one_result_per_unique_url():
    seen = []
    results = list(fl.fetch_all([PDF, PDF], opener=_opener(), on_file=lambda r, d, t: seen.append(d)))
    assert len(results) == 1
    assert seen == [1]
