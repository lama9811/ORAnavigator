#!/usr/bin/env python3
"""
Render question_bank.json into a printable PDF on the Desktop.

The PDF has a title page and 5 sections (easy / hard / very hard / behavioural /
very-hard KB-grounding); every entry shows Question, Answer, and Source.
"""
import json
import os
from fpdf import FPDF

EVAL_DIR = os.path.dirname(os.path.abspath(__file__))
BANK = os.path.join(EVAL_DIR, "question_bank.json")
OUT = os.path.expanduser("~/Desktop/ORA-Navigator-Test-Questions.pdf")

SECTIONS = [
    ("easy", "Section 1  -  Easy Questions"),
    ("hard", "Section 2  -  Hard Questions"),
    ("very_hard", "Section 3  -  Very Hard Questions"),
    ("behavioural", "Section 4  -  Behavioural Questions"),
    ("kb_grounding", "Section 5  -  Very Hard KB-Grounding Questions"),
]

# Map common Unicode punctuation to ASCII so the PDF core font (latin-1) is safe.
_REPL = {
    "—": "-", "–": "-", "‒": "-", "‘": "'", "’": "'",
    "“": '"', "”": '"', "…": "...", "•": "-",
    " ": " ", "→": "->", "­": "", "​": "",
}


def clean(s):
    s = str(s or "")
    for k, v in _REPL.items():
        s = s.replace(k, v)
    return s.encode("latin-1", "replace").decode("latin-1")


class PDF(FPDF):
    def footer(self):
        self.set_y(-15)
        self.set_font("Helvetica", "I", 8)
        self.set_text_color(140, 140, 140)
        self.cell(0, 10, f"ORA Navigator Test Bank   -   page {self.page_no()}",
                  align="C")


def mc(pdf, h, txt, **kw):
    """multi_cell that always returns the cursor to the left margin."""
    pdf.set_x(pdf.l_margin)
    pdf.multi_cell(0, h, clean(txt), new_x="LMARGIN", new_y="NEXT", **kw)


def main():
    bank = json.load(open(BANK))
    pdf = PDF()
    pdf.set_auto_page_break(auto=True, margin=18)
    pdf.set_margins(18, 18, 18)

    # ---- title page --------------------------------------------------------
    pdf.add_page()
    pdf.ln(38)
    pdf.set_font("Helvetica", "B", 24)
    pdf.set_text_color(20, 40, 90)
    mc(pdf, 12, "ORA Navigator", align="C")
    pdf.set_font("Helvetica", "B", 18)
    mc(pdf, 10, "Chatbot Test-Question Bank", align="C")
    pdf.ln(8)
    pdf.set_font("Helvetica", "", 11)
    pdf.set_text_color(70, 70, 70)
    mc(pdf, 6,
       "260 questions with verified answers for testing the Morgan State "
       "University Office of Research Administration chatbot.", align="C")
    pdf.ln(10)

    total = 0
    pdf.set_font("Helvetica", "", 11)
    pdf.set_text_color(40, 40, 40)
    for key, title in SECTIONS:
        n = len(bank.get(key, []))
        total += n
        mc(pdf, 7, f"    {title}:  {n}")
    pdf.ln(3)
    pdf.set_font("Helvetica", "B", 11)
    mc(pdf, 7, f"    Total:  {total} questions")
    pdf.ln(10)
    pdf.set_font("Helvetica", "I", 9)
    pdf.set_text_color(120, 120, 120)
    mc(pdf, 5,
       "Every knowledge-base answer is sourced from a real document in "
       "backend/kb_structured/ (382 docs). Behavioural questions list the "
       "correct behaviour rather than a knowledge-base fact. Use the Answer as "
       "the grading key when testing the chatbot.", align="C")

    # ---- each section ------------------------------------------------------
    for key, title in SECTIONS:
        items = bank.get(key, [])
        pdf.add_page()
        pdf.set_font("Helvetica", "B", 15)
        pdf.set_text_color(20, 40, 90)
        mc(pdf, 9, f"{title}  ({len(items)})")
        pdf.set_draw_color(20, 40, 90)
        pdf.line(18, pdf.get_y() + 1, 192, pdf.get_y() + 1)
        pdf.ln(5)

        for e in items:
            pdf.set_font("Helvetica", "B", 10.5)
            pdf.set_text_color(15, 15, 15)
            mc(pdf, 5.6, f"{e['n']}.  {e['question']}")
            pdf.ln(0.5)
            pdf.set_font("Helvetica", "", 10)
            pdf.set_text_color(45, 45, 45)
            mc(pdf, 5.2, f"Answer:  {e['answer']}")
            pdf.set_font("Helvetica", "I", 8)
            pdf.set_text_color(130, 130, 130)
            mc(pdf, 4.5, f"Source:  {e['source']}")
            pdf.ln(3.5)

    pdf.output(OUT)
    print(f"Wrote {OUT}")
    print(f"Pages: {pdf.page_no()}   Total questions: {total}")


if __name__ == "__main__":
    main()
