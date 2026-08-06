# -*- coding: utf-8 -*-
"""Generate two demo PDFs for the live presentation:
  1. ORA_Navigator_Demo_Script.pdf   -  full feature test plan
  2. Sample_Solicitation_for_Demo.pdf  -  a fake solicitation to upload live
Run:  python3 scripts/make_demo_pdfs.py
"""
import os
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.units import inch
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable, PageBreak,
)

OUT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NAVY = colors.HexColor("#0B2545")
BLUE = colors.HexColor("#1B3A6B")
ORANGE = colors.HexColor("#F7941E")
GREY = colors.HexColor("#475569")
LIGHT = colors.HexColor("#f1f5f9")

ss = getSampleStyleSheet()
H1 = ParagraphStyle("H1", parent=ss["Heading1"], textColor=NAVY, fontSize=18, spaceAfter=4)
H2 = ParagraphStyle("H2", parent=ss["Heading2"], textColor=BLUE, fontSize=13, spaceBefore=12, spaceAfter=4)
BODY = ParagraphStyle("BODY", parent=ss["BodyText"], fontSize=10, leading=14, textColor=colors.HexColor("#1e293b"))
STEP = ParagraphStyle("STEP", parent=BODY, leftIndent=12, spaceAfter=2)
SAMPLE = ParagraphStyle("SAMPLE", parent=BODY, fontName="Courier", fontSize=8.5, leading=11,
                        textColor=NAVY, backColor=LIGHT, borderPadding=6, leftIndent=4, rightIndent=4)
EXPECT = ParagraphStyle("EXPECT", parent=BODY, textColor=colors.HexColor("#15803d"), leftIndent=12)
SAY = ParagraphStyle("SAY", parent=BODY, textColor=ORANGE, leftIndent=12, fontName="Helvetica-Oblique")
MUTED = ParagraphStyle("MUTED", parent=BODY, textColor=GREY, fontSize=9)


def hr():
    return HRFlowable(width="100%", thickness=0.6, color=colors.HexColor("#cbd5e1"),
                      spaceBefore=8, spaceAfter=8)


def feature(story, num, title, what, steps, sample=None, expect=None, say=None):
    story.append(Paragraph(f"{num}. {title}", H2))
    story.append(Paragraph(f"<b>What it shows:</b> {what}", BODY))
    story.append(Paragraph("<b>Steps:</b>", BODY))
    for s in steps:
        story.append(Paragraph(f"- {s}", STEP))
    if sample:
        story.append(Spacer(1, 3))
        story.append(Paragraph("<b>Paste this sample:</b>", BODY))
        story.append(Paragraph(sample.replace("\n", "<br/>"), SAMPLE))
    if expect:
        story.append(Spacer(1, 3))
        story.append(Paragraph(f"<b>Expect:</b> {expect}", EXPECT))
    if say:
        story.append(Paragraph(f'Say: "{say}"', SAY))
    story.append(hr())


def page_footer(canvas, doc):
    canvas.saveState()
    canvas.setFont("Helvetica", 8)
    canvas.setFillColor(GREY)
    canvas.drawString(0.75 * inch, 0.5 * inch, "ORA Navigator  -  live demo script")
    canvas.drawRightString(LETTER[0] - 0.75 * inch, 0.5 * inch, f"Page {doc.page}")
    canvas.restoreState()


# ---------------------------------------------------------------------------
# PDF 1  -  Demo script
# ---------------------------------------------------------------------------
def build_demo_script():
    path = os.path.join(OUT, "ORA_Navigator_Demo_Script.pdf")
    doc = SimpleDocTemplate(path, pagesize=LETTER, topMargin=0.7 * inch,
                            bottomMargin=0.8 * inch, leftMargin=0.75 * inch, rightMargin=0.75 * inch)
    s = []
    s.append(Paragraph("ORA Navigator  -  Live Demo & Test Script", H1))
    s.append(Paragraph("A step-by-step walkthrough of every feature, with ready-to-paste samples and "
                       "expected results. Green = what you should see. Orange = a line you can say.", MUTED))
    s.append(hr())

    s.append(Paragraph("Suggested demo order (~10 min)", H2))
    for i, line in enumerate([
        "Start from a Solicitation (upload the Sample Solicitation PDF, or paste an NSF URL)",
        "Walk the generated checklist -&gt; show per-task 'How do I do this?' + 'Add to calendar'",
        "Build budget -&gt; coaching advisories, ideas to get under cap, multi-year, effort helper, CSV, justification",
        "Drafting coach -&gt; outline a section, paste a draft for feedback (length meter + clarity + solicitation match)",
        "Fundability -&gt; eligibility go/no-go, then reviewer feedback on a draft",
        "Ask the chatbot a real ORA question",
    ], 1):
        s.append(Paragraph(f"{i}. {line}", STEP))
    s.append(hr())

    feature(s, 1, "Start from a Solicitation",
            "Turns a sponsor solicitation (a PDF you upload OR a web link you paste) into a pre-filled "
            "proposal  -  deadline, budget cap, page limits, eligibility, required attachments  -  for review before saving.",
            ["Open My Proposals -&gt; 'Start from Solicitation'.",
             "Option A (offline-safe for the demo): drag in the Sample_Solicitation_for_Demo.pdf.",
             "Option B (live): paste the NSF URL below.",
             "Review the extracted fields, then click Create Proposal."],
            sample="https://www.nsf.gov/funding/opportunities/idss-integrated-data-systems-services/nsf26-509/solicitation",
            expect="A review screen pre-fills sponsor=NSF, a program ID, the deadline, budget cap, page limits, "
                   "eligibility, and required attachments. Multi-deadline solicitations list ALL category deadlines in the notes.",
            say="It reads the solicitation for you and sets up the whole proposal  -  but you review every field before anything is saved.")

    feature(s, 2, "Guided checklist (how-to + samples + calendar)",
            "Every checklist task carries a plain-English how-to, an example, and a per-task calendar link.",
            ["Open the proposal you just created.",
             "On any task (e.g. 'Write the budget justification'), click 'How do I do this?'.",
             "Click 'Add to calendar' on a task with a timing."],
            expect="The task expands to show a how-to + a short example. 'Add to calendar' opens Google Calendar dated "
                   "at (deadline - the task's lead time).",
            say="A first-time PI doesn't just get a checklist  -  each step teaches them how to do it.")

    feature(s, 3, "Budget Helper + Budget Coach",
            "A correct F&A/MTDC calculator PLUS coaching: sanity warnings, cap-trim ideas, tooltips, multi-year, "
            "an effort&lt;-&gt;months helper, per-line justification, and CSV export.",
            ["Open the proposal -&gt; Build budget.",
             "Add a person: Base salary 100000, % effort 25, Faculty (Academic Year).",
             "Equipment 80000. Sponsor cap 50000.",
             "Set Project length -&gt; Years = 3, escalation = 3.",
             "Open the 'Effort &lt;-&gt; months helper' and type 25.",
             "Click Draft justification, then Export CSV."],
            expect="MTDC excludes equipment (F&A on the modified base). 'Things to double-check' flags the equipment-heavy "
                   "budget; 'Ideas to get under the cap' suggests concrete cuts. Years 1-3 + cumulative show with escalation. "
                   "The effort helper converts 25% -&gt; person-months. Justification drafts prose + a line-by-line list. CSV downloads.",
            say="The numbers are always computed by code  -  the AI only explains them, it can never change a dollar amount.")

    feature(s, 4, "Drafting Coach",
            "Section-by-section writing help: an outline of what the section needs, plus advisory feedback on YOUR draft "
            "(covered/missing elements, a length meter, a clarity check, and what the solicitation requires). It never writes the prose for you.",
            ["Open the proposal -&gt; Drafting coach.",
             "Pick 'Project Summary' -&gt; Outline it.",
             "Switch to 'Feedback on my draft' -&gt; paste the sample below -&gt; Get feedback.",
             "Add a Broader Impacts sentence and re-run to watch it flip to 'covered'. Click Save draft."],
            sample=("Overview: This project builds an integrated data system that lets small municipalities share "
                    "environmental sensor data in real time. Intellectual Merit: The work advances methods for fusing "
                    "data across many sensor types and produces open algorithms the research community can reuse."),
            expect="Outline shows the 3 required NSF parts. Feedback marks Overview + Intellectual Merit covered but "
                   "Broader Impacts MISSING, shows a length bar (~45 words, 'quite short'), a clarity note, and a "
                   "'What this solicitation requires' panel from the real solicitation.",
            say="It coaches and reviews  -  it never writes the proposal for them, so the work stays theirs.")

    feature(s, 5, "Fundability  -  eligibility + reviewer lens",
            "A go/no-go eligibility self-check, plus a 'what would a reviewer say' read against the sponsor's real review criteria.",
            ["Open the proposal -&gt; Fundability.",
             "Eligibility tab: set the first question to 'No' -&gt; see a red STOP. Set all to 'Yes' -&gt; green.",
             "Reviewer feedback tab: paste the same draft from #4 -&gt; Get reviewer feedback."],
            expect="Eligibility returns GO / CAUTION / STOP with reasons. Reviewer feedback rates the draft against "
                   "NSF's Intellectual Merit + Broader Impacts and lists the biggest risks.",
            say="This catches the heartbreaker  -  writing a whole proposal you were never eligible to submit.")

    feature(s, 6, "Ask the chatbot",
            "The grounded assistant answers ORA questions from the knowledge base (no made-up facts).",
            ["Open the chatbot.",
             "Ask the question below."],
            sample="What are the rules for spending grant funds on travel or equipment?",
            expect="A grounded answer with sources. (If the model is briefly rate-limited it now retries/falls back "
                   "gracefully instead of showing 'system is busy'.)",
            say="Everything it says is grounded in Morgan State's real policies, not the model's memory.")

    s.append(Paragraph("Safety story (good to mention)", H2))
    for line in [
        "AI is advisory only  -  every number and pass/fail is computed by deterministic code.",
        "AI feedback must quote your own text; unverifiable claims are dropped (no hallucination).",
        "If the AI is ever down, every feature falls back to a simpler version instead of erroring.",
        "Nothing is saved until the human reviews and confirms.",
    ]:
        s.append(Paragraph(f"- {line}", STEP))

    doc.build(s, onFirstPage=page_footer, onLaterPages=page_footer)
    return path


# ---------------------------------------------------------------------------
# PDF 2  -  Sample solicitation to upload live
# ---------------------------------------------------------------------------
def build_sample_solicitation():
    path = os.path.join(OUT, "Sample_Solicitation_for_Demo.pdf")
    doc = SimpleDocTemplate(path, pagesize=LETTER, topMargin=0.8 * inch,
                            bottomMargin=0.8 * inch, leftMargin=1 * inch, rightMargin=1 * inch)
    body = ParagraphStyle("sol", parent=BODY, fontSize=10.5, leading=15, spaceAfter=8)
    head = ParagraphStyle("solh", parent=ss["Heading2"], textColor=NAVY, fontSize=12, spaceBefore=10, spaceAfter=3)
    s = []
    s.append(Paragraph("NATIONAL SCIENCE FOUNDATION", H1))
    s.append(Paragraph("Program Solicitation NSF 26-999", ParagraphStyle("pid", parent=BODY, fontSize=11, textColor=BLUE)))
    s.append(Paragraph("Demonstration Program in Integrated Research Methods (DEMO)", body))
    s.append(hr())
    s.append(Paragraph("Full Proposal Deadline(s):", head))
    s.append(Paragraph("October 15, 2026, by 5:00 p.m. submitter's local time. Proposals are not accepted after the deadline.", body))
    s.append(Paragraph("Award Information", head))
    s.append(Paragraph("NSF anticipates making 8-10 awards. Awards are up to $500,000 total for project periods of up "
                       "to three years. The maximum award is $500,000 per proposal.", body))
    s.append(Paragraph("Who May Submit Proposals", head))
    s.append(Paragraph("Proposals may only be submitted by accredited institutions of higher education (IHEs) located "
                       "in the United States. An individual may be designated as PI on no more than one proposal per deadline.", body))
    s.append(Paragraph("Proposal Preparation Instructions", head))
    s.append(Paragraph("The Project Description is limited to 15 pages. The Project Summary is limited to 1 page and must "
                       "contain three separately labeled sections: Overview, Intellectual Merit, and Broader Impacts. "
                       "A Data Management Plan of no more than 2 pages is required.", body))
    s.append(Paragraph("Required Attachments", head))
    s.append(Paragraph("Project Summary; Project Description; References Cited; Biographical Sketch(es); Budget and "
                       "Budget Justification; Current and Pending Support; Facilities, Equipment and Other Resources; "
                       "Data Management Plan.", body))
    s.append(Paragraph("Submission", head))
    s.append(Paragraph("Proposals must be submitted via Research.gov.", body))
    s.append(Spacer(1, 10))
    s.append(Paragraph("[ This is a FICTIONAL solicitation created only for demonstrating ORA Navigator. ]", MUTED))
    doc.build(s)
    return path


if __name__ == "__main__":
    p1 = build_demo_script()
    p2 = build_sample_solicitation()
    print("Wrote:", p1)
    print("Wrote:", p2)
