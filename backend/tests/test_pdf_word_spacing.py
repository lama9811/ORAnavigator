"""Words must survive the PDF, and a TeX-set proposal is where they stop doing so.

WHAT WENT WRONG
---------------
pdfplumber decides where one word ends and the next begins from the horizontal
GAP between glyphs, against a fixed 3-point tolerance. TeX does not emit a space
character between words — it moves the pen — and in a 10pt document that move is
often ~2pt, under the tolerance. So the words are welded together.

Measured on a real awarded NSF 23-598 package (Morgan State, 56 pages, LaTeX):
**863 run-together tokens over 18 characters**, including the headings the
reviewer looks for — `IntellectualMerit`, `BroaderImpacts`,
`ResultsfromPriorNSFsupport`, `sectionoftheproposal`.

WHY IT MATTERED SO MUCH MORE THAN IT LOOKS
------------------------------------------
Three separate features failed downstream, and none of them said "the text is
broken", they each said something false about the PROPOSAL:
  * the locate stage could not find Project Summary, Project Description or
    Broader Impacts, because those headings no longer existed as those strings;
  * `quote_in` (golden rule 2) rejected the reviewer's own quotes, so 21 of 34
    fix-list rows were findings the reviewer had made CORRECTLY and then
    demoted to `not_found` — each note saying the section does cover the rule;
  * the language checks reported 142 "mistakes", of which exactly ONE was real,
    the rest being artifacts like `Berlin,Heidelberg:SpringerBerlinHeidelberg`
    read as a missing space after a period.

THE FIX is `x_tolerance_ratio`, which scales the tolerance with the font size
instead of pinning it at 3 points. Measured across both awarded packages:
run-together tokens 863 -> 1 and 21 -> 1, all five heading probes recovered,
and the whole-word counts of ordinary vocabulary UNCHANGED (representation 85,
mathematics 78, undergraduate 60, research 269) — i.e. it did not buy the
spacing by chopping real words up.

THE FIXTURE builds a PDF by hand rather than pulling in a PDF writer. reportlab
is installed on this machine but is NOT in requirements.txt, so a test that used
it would pass here and fail everywhere else — and, tested, reportlab's own
output does not reproduce the bug anyway because it emits real space glyphs.
A TJ array with a positioning offset is exactly how TeX encodes the gap, so this
reproduces the real defect rather than an imitation of it.
"""

from services import document_text
from services import solicitation_extractor


def _pdf(lines, gap_thousandths=200, size=10):
    """A one-page PDF whose inter-word space is PEN MOVEMENT, not a space glyph.

    `gap_thousandths` is in thousandths of an em, so 200 at 10pt is a 2.0pt gap:
    under pdfplumber's fixed 3pt default (words weld together) and over a
    font-scaled 1.5pt one (words separate). Pass 0 for a document that uses real
    space characters, which is the control.
    """
    def show(line):
        parts = line.split(" ")
        if not gap_thousandths:
            esc = line.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")
            return f"[({esc})] TJ"
        out = "["
        for i, w in enumerate(parts):
            esc = w.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")
            if i:
                out += f" {-gap_thousandths} "
            out += f"({esc})"
        return out + "] TJ"

    body = "BT /F1 %d Tf 72 720 Td %d TL\n" % (size, size + 6)
    body += "\n".join(f"{show(l)} T*" for l in lines) + "\nET"
    objs = [
        "<</Type/Catalog/Pages 2 0 R>>",
        "<</Type/Pages/Kids[3 0 R]/Count 1>>",
        "<</Type/Page/Parent 2 0 R/MediaBox[0 0 612 792]"
        "/Resources<</Font<</F1 4 0 R>>>>/Contents 5 0 R>>",
        "<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>",
        f"<</Length {len(body)}>>\nstream\n{body}\nendstream",
    ]
    out, offs = "%PDF-1.4\n", []
    for i, o in enumerate(objs, 1):
        offs.append(len(out))
        out += f"{i} 0 obj\n{o}\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n"
    out += "".join(f"{o:010d} 00000 n \n" for o in offs)
    out += f"trailer\n<</Size {len(objs) + 1}/Root 1 0 R>>\nstartxref\n{xref}\n%%EOF\n"
    return out.encode("latin-1")


LINES = ["Intellectual Merit", "Broader Impacts",
         "Four undergraduates per year will be trained."]


def test_the_solicitation_reader_keeps_words_apart():
    """`read_pdf` is the solicitation path AND the page count behind Section
    Check's upload, so a welded read corrupts both."""
    text = solicitation_extractor.read_pdf(_pdf(LINES))["text"]
    assert "Intellectual Merit" in text, f"words welded together: {text!r}"
    assert "Broader Impacts" in text, f"words welded together: {text!r}"


def test_the_upload_path_keeps_words_apart():
    """`document_text` is what Draft Review and Section Check actually call when
    a PI uploads their proposal — the route the real package came in through."""
    got = document_text.extract_upload("proposal.pdf", _pdf(LINES))
    assert not got.get("error"), got
    assert "Intellectual Merit" in got["text"], f"words welded: {got['text']!r}"
    assert "Four undergraduates per year" in got["text"], got["text"]


def test_a_pdf_that_uses_real_spaces_is_not_chopped_up():
    """The mirror risk, and the one that would be worse.

    Loosening word segmentation can split words INSIDE themselves, which would
    break `quote_in` just as thoroughly while looking like a different bug. This
    is the control: a document with ordinary space glyphs must come back with
    its vocabulary whole.
    """
    text = solicitation_extractor.read_pdf(_pdf(LINES, gap_thousandths=0))["text"]
    for word in ("Intellectual", "Merit", "undergraduates", "trained"):
        assert word in text, f"{word!r} was fragmented: {text!r}"
    assert "Intellectual Merit" in text
