"""Tests for the file-text extractors used by the scrape's document phase.

Loaded by file path for the same reason test_kb_scraper.py does it: importing
the kb_scraper package pulls modules that expect the backend on sys.path, and
these readers must be testable without a browser, GCP, or the Job image.
"""

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


def test_a_query_string_does_not_hide_the_extension():
    assert ex.kind_for_url("https://x/f.pdf?v=2") == "file"


def test_truncation_respects_max_chars():
    assert len(ex._truncate("a" * 500_000)) == ex.MAX_CHARS
