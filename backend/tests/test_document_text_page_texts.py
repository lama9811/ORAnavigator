"""`page_texts` must reach the caller, and must agree with `text`.

Extracting the document twice and disagreeing about it is the failure
`_extract_pdf`'s own docstring warns against.
"""
import io

import pytest

from services import document_text as dt

pdfplumber = pytest.importorskip("pdfplumber")
from reportlab.lib.pagesizes import letter          # noqa: E402
from reportlab.pdfgen import canvas                 # noqa: E402


def _pdf(pages):
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)
    for body in pages:
        c.drawString(72, 720, body)
        c.showPage()
    c.save()
    return buf.getvalue()


def test_a_pdf_upload_carries_its_per_page_text():
    out = dt.extract_upload("x.pdf", _pdf(["Alpha page one", "Beta page two"]))
    assert out["error"] is None
    assert len(out["page_texts"]) == 2
    assert "Alpha" in out["page_texts"][0]
    assert "Beta" in out["page_texts"][1]


def test_the_page_texts_join_to_the_text_we_returned():
    """The offsets every span uses are computed on this join. If the two ever
    disagree, every span in the review addresses the wrong characters."""
    out = dt.extract_upload("x.pdf", _pdf(["Alpha page one", "Beta page two"]))
    assert "\n".join(out["page_texts"]).strip() == out["text"]


def test_a_plain_text_upload_has_no_pages():
    out = dt.extract_upload("x.txt", b"just some text")
    assert out["page_texts"] == []
