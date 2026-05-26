#!/usr/bin/env python3
"""Render a plain-language explainer PDF about ORA Navigator to the Desktop.

Every feature is explained as WHAT it is, then HOW it was built, in simple words.
"""
import os
from fpdf import FPDF

OUT = os.path.expanduser("~/Desktop/ORA-Navigator-Explained.pdf")

_REPL = {"—": "-", "–": "-", "‘": "'", "’": "'", "“": '"', "”": '"',
         "…": "...", "•": "-", " ": " ", "→": "->"}


def clean(s):
    s = str(s or "")
    for k, v in _REPL.items():
        s = s.replace(k, v)
    return s.encode("latin-1", "replace").decode("latin-1")


# Content model: list of (kind, text).
# kind = h1 | h2 | p | b (bullet) | how (the "How we built it" block)
CONTENT = [
    ("h1", "1. What Is ORA Navigator?"),
    ("p", "ORA Navigator is an AI assistant - a chatbot - built for the Office of Research Administration (ORA) at Morgan State University. ORA is the department that helps the university's researchers find grant money, apply for it, follow the rules, and manage the money once they win it."),
    ("p", "ORA Navigator is made for faculty, principal investigators (PIs), research staff, and department administrators - the people who do research and need answers about it."),
    ("p", "Think of it as a 24/7 expert receptionist for the research office. Instead of digging through the ORA website or emailing staff and waiting for a reply, a researcher types a question in plain English and gets a clear answer in seconds."),
    ("p", "It answers questions about: finding grants and funding; compliance (IRB for human-subjects research, IACUC for animal research, conflict of interest, responsible conduct of research, and research security); pre-award work (writing proposals and budgets); post-award work (managing a grant after you win it); official forms; university policies; and which ORA staff member to contact. It is live on the web at ora.inavigator.ai."),

    ("h1", "2. What ORA Navigator Does"),
    ("p", "The idea is simple. You ask a question in everyday language. ORA Navigator searches ORA's official knowledge base, finds the relevant information, and writes a clear answer - with links to the source documents so you can check it yourself."),
    ("p", "The single most important rule: it answers ONLY from ORA's real knowledge base. It is deliberately built NOT to make things up. If it does not have the answer, it is designed to say so honestly and point you to ORA staff rather than guess. In AI terms, the thing it is built to avoid is a 'hallucination' - a confident but made-up answer. Almost every design choice described below exists to prevent that."),

    ("h1", "3. The Technology Stack (What It Is Built With)"),
    ("p", "ORA Navigator is made of several pieces of technology working together. In plain terms:"),
    ("b", "Frontend (the website you see): React 19 with Vite - the chat window in your browser."),
    ("b", "Backend (the coordinator): FastAPI, written in Python - the middle manager that runs the safety checks."),
    ("b", "AI agent (the brain): Google's ADK (Agent Development Kit) running the Gemini 2.x Flash AI model."),
    ("b", "Search engine (retrieval): Google Vertex AI Search, which indexes the knowledge base."),
    ("b", "Database (records and memory): Google Cloud SQL - a MySQL database for accounts, chat history, and memory."),
    ("b", "Cache (the speed-up): a three-layer cache that remembers recent answers."),
    ("b", "Hosting (the cloud): Google Cloud Run, which scales automatically with demand."),
    ("p", "Section 4 explains how these pieces are arranged, and - for every feature - exactly how it was built."),

    ("h1", "4. The Architecture - Every Layer, and How It Was Built"),
    ("p", "Here is how the whole system fits together, layer by layer. For each layer there is a plain-words description, and then a 'How we built it' note explaining the actual work behind it."),

    ("h2", "4.1  The three services"),
    ("p", "ORA Navigator is really three separate programs that talk to each other: the Frontend (the website you click), the Backend (the coordinator that receives the question and runs the checks), and the ADK Agent (the AI that does the thinking). A question travels: your browser -> the Backend -> the AI Agent -> and the answer comes back the same way."),
    ("how", "We split the project into three independent programs and gave each one a single job, then connected them with standard web requests. We built it this way so each part can be updated, fixed, or given more computing power on its own, without disturbing the others."),

    ("h2", "4.2  The knowledge base"),
    ("p", "The knowledge base is ORA Navigator's source of truth: 382 documents, organised into 9 sections (about, pre-award, post-award, policies and guidelines, research compliance, trainings, resources, funding sources, and announcements). This structure mirrors the real ORA website exactly. If a fact is not in these 382 documents, ORA Navigator treats it as unknown."),
    ("how", "We collected the content of the official ORA website page by page, converted each page into a clean structured file (a JSON document), and sorted the 382 files into 9 folders that match the website's menu. We then uploaded all of them into Google Vertex AI Search, which indexes them so they can be searched instantly."),

    ("h2", "4.3  The grounding system - three layers"),
    ("p", "'Grounding' means making sure every answer is based on the real knowledge base and not on the AI's imagination. This is the most important part of ORA Navigator, and it is done in three layers."),

    ("h2", "Layer 1 - Pre-fetch"),
    ("p", "Before the AI even starts answering, the system searches the knowledge base for documents related to your question and hands them to the AI up front - so the real facts are in front of it from the first moment."),
    ("how", "We wrote a component (kb_prefetch) that keeps a copy of all 382 documents in memory and, the moment a question arrives, ranks them with a well-known keyword-scoring method called TF-IDF. It picks the few best matches and places them directly into the AI's instructions before the AI starts writing."),

    ("h2", "Layer 2 - The search tool"),
    ("p", "While the AI is writing, it also has a search tool it can use to look things up in the knowledge base on demand."),
    ("how", "We registered Google Vertex AI Search with the AI agent as a 'tool' it is allowed to call. The agent can use that tool mid-answer to pull documents from the knowledge base whenever it needs them."),

    ("h2", "Layer 3 - The verification check (regenerate-then-refuse)"),
    ("p", "This is the strongest layer. After the AI writes a draft answer, the system checks whether the answer is genuinely backed by the knowledge base. If it is too weak, the answer is regenerated once under a stricter instruction; if it is still weak, it is refused entirely and a safe fallback message is shown instead. An ungrounded answer is never delivered."),
    ("how", "We wrote verification code (in a file called vertex_agent.py) that runs after the AI produces a draft. It measures how many knowledge-base documents the answer relies on and how much of the wording is backed by them. If the answer is too weak, the code automatically re-asks the AI with a stricter prompt; if the second draft is still weak, the code discards it and returns the fallback. None of this is left to the AI's judgement - it is plain, predictable code."),

    ("h2", "4.4  The rulebook (the system prompt)"),
    ("p", "The AI is given a written set of rules every time it runs - its 'constitution'. It includes GROUNDING RULES (search the knowledge base every time, never use old training knowledge for Morgan State facts, never invent names or numbers), instructions to refuse trick questions, and instructions to stay strictly on ORA topics."),
    ("how", "We wrote a detailed instruction document - the system prompt - that spells out the grounding rules, the no-fabrication rules, and the topic boundaries. Our code attaches this document to every single request sent to the AI, so the rules are never skipped."),

    ("h2", "4.5  The identifier guard"),
    ("p", "Research administration is full of dangerous-to-get-wrong numbers - FWA numbers, the university's UEI and EIN, F&A and fringe rates, IACUC SOP numbers. ORA Navigator checks that any such identifier in an answer appears word-for-word in the retrieved knowledge-base text, or the answer is flagged."),
    ("how", "We wrote a small checker that scans the answer with pattern-matching (regular expressions) to find anything shaped like an official identifier or rate, then confirms that exact text appears in the documents that were retrieved. If it does not, the answer is flagged."),

    ("h2", "4.6  Citations (source links)"),
    ("p", "When the AI answers from the knowledge base, the system shows you the source documents as clickable links, so every answer can be traced back to where it came from."),
    ("how", "When the AI answers, Google returns hidden data listing which documents it actually used. Our code reads that list, matches each document back to its real page on the ORA website, and displays them as clickable links beneath the answer."),

    ("h2", "4.7  The knowledge-base browser"),
    ("p", "For 'list everything' style questions ('what forms are there', 'show me all the SOPs'), ORA Navigator answers from a fixed index of the knowledge base with NO AI involved at all - which means zero chance of a made-up answer, and a reply in milliseconds."),
    ("how", "We built a single index file - a map of all 382 documents and how they are organised - and wrote code (kb_browser.py) that recognises 'list' style questions. For those, it answers straight from the index and never calls the AI."),

    ("h2", "4.8  Caching (the speed-up)"),
    ("p", "To stay fast and low-cost, answers are remembered in three caches. A repeated or reworded question can be answered without calling the AI again."),
    ("how", "We built three caches that the code checks in order before ever calling the AI. The first is a small table held in the program's own memory (instant). The second is Redis, a fast database shared by all servers. The third, the 'semantic' cache, turns each question into a list of numbers (an embedding) so two differently-worded questions that mean the same thing are recognised as a match."),

    ("h2", "4.9  Long-term memory"),
    ("p", "For signed-in users, ORA Navigator remembers useful facts from past conversations - your role, your active grants, your research interests - so future answers feel personalised."),
    ("how", "Every conversation is saved to the database. A background job runs once a day, reads each user's recent chats, uses the AI to pull out the key facts, turns those facts into number-lists (embeddings), and stores them. On each new question, the code finds the saved facts most related to that question and hands them to the AI."),

    ("h2", "4.10  Accounts and access"),
    ("p", "Sign-up is restricted to morgan.edu email addresses, with email verification. Logged-in users get the full experience (history and memory); guests get a lighter version. The system is rate-limited so it cannot be abused."),
    ("how", "We built a login system. Passwords are never stored as plain text - they are scrambled with a one-way method called bcrypt. Each signed-in user receives a secure token (a JWT) that proves their identity on every request. Guests are limited to a set number of questions per minute so the service cannot be flooded."),

    ("h2", "4.11  The faithfulness exam"),
    ("p", "ORA Navigator has a faithfulness exam - a repeatable, graded test that measures how well the chatbot stays grounded and whether it hallucinates. It produces a single score, so any future change can be measured and any drop in quality caught before it goes live."),
    ("how", "We built a test set of questions, each with a known-correct answer taken from the real knowledge base, using an evaluation tool called promptfoo. A separate AI model grades each chatbot answer for how faithful it is, and a scoring script adds the results into one overall faithfulness percentage that can block a release if it drops."),

    ("h1", "5. What Makes ORA Navigator Stand Out"),
    ("p", "ORA Navigator was designed for a setting where a wrong answer has real consequences - grant rules, compliance deadlines, official identifiers. Several features were added specifically to make it trustworthy in that setting. These are the ones worth highlighting."),

    ("h2", "It refuses rather than guesses"),
    ("p", "Most chatbots, when unsure, still produce an answer. ORA Navigator's Layer 3 check actively throws away an answer it cannot back up with real documents - and regenerates it once before giving up. Choosing 'no answer' over 'a possibly-wrong answer' is the right call when the topic is compliance and funding."),
    ("how", "Built as plain verification code that measures grounding after every draft and enforces the regenerate-then-refuse rule - it is not left to the AI to decide."),

    ("h2", "It measures its own honesty"),
    ("p", "ORA Navigator does not just claim to be accurate - it has the faithfulness exam that puts a number on how grounded it is. That number can be tracked over time, so quality is protected on every update instead of being a guess."),
    ("how", "Built as an automated graded test bank plus a scoring script that acts as a quality gate before any release."),

    ("h2", "It guards official identifiers"),
    ("p", "A wrong FWA number, EIN, or F&A rate can cause real problems on a grant application. ORA Navigator has a dedicated safeguard that verifies every official identifier word-for-word against the knowledge base before the answer is shown."),
    ("how", "Built as a pattern-matching checker that cross-references each identifier in the answer against the retrieved source text."),

    ("h2", "It has a zero-error path for list questions"),
    ("p", "For 'show me everything' questions, ORA Navigator skips the AI entirely and answers from a fixed index. Those answers are instant and cannot be wrong."),
    ("how", "Built as a separate knowledge-base browser that reads a pre-built index of all 382 documents, with no AI call in the loop."),

    ("h2", "Every answer is traceable"),
    ("p", "ORA Navigator shows the source documents behind each answer, so a researcher - or an administrator - can always verify where a fact came from. That transparency is what makes the assistant safe to rely on."),
    ("how", "Built by reading the AI's source metadata and converting it into clickable links to the real ORA web pages."),

    ("p", "Taken together, these features make ORA Navigator more than a simple question-answering bot: it is a carefully grounded, self-measuring assistant designed so that, in a high-stakes research-compliance setting, it can be trusted - and so that its quality can be proven and improved with every update."),
]


class PDF(FPDF):
    def footer(self):
        self.set_y(-15)
        self.set_font("Helvetica", "I", 8)
        self.set_text_color(150, 150, 150)
        self.cell(0, 10, f"ORA Navigator - Explained    |    page {self.page_no()}",
                  align="C")


def mc(pdf, h, txt, **kw):
    pdf.set_x(pdf.l_margin)
    pdf.multi_cell(0, h, clean(txt), new_x="LMARGIN", new_y="NEXT", **kw)


def main():
    pdf = PDF()
    pdf.set_auto_page_break(auto=True, margin=20)
    pdf.set_margins(20, 20, 20)

    # ---- title page --------------------------------------------------------
    pdf.add_page()
    pdf.ln(46)
    pdf.set_font("Helvetica", "B", 28)
    pdf.set_text_color(20, 40, 90)
    mc(pdf, 14, "ORA Navigator", align="C")
    pdf.set_font("Helvetica", "B", 16)
    mc(pdf, 10, "How It Works - Explained Simply", align="C")
    pdf.ln(10)
    pdf.set_font("Helvetica", "", 11)
    pdf.set_text_color(70, 70, 70)
    mc(pdf, 6, "What it is, the technology behind it, every layer of its "
              "design - and, in simple words, how each part was built.",
       align="C")
    pdf.ln(14)
    pdf.set_font("Helvetica", "I", 10)
    pdf.set_text_color(120, 120, 120)
    mc(pdf, 6, "Morgan State University - Office of Research Administration",
       align="C")

    # ---- body --------------------------------------------------------------
    for kind, text in CONTENT:
        if kind == "h1":
            pdf.add_page()
            pdf.set_font("Helvetica", "B", 16)
            pdf.set_text_color(20, 40, 90)
            mc(pdf, 9, text)
            pdf.set_draw_color(20, 40, 90)
            pdf.line(20, pdf.get_y() + 1, 190, pdf.get_y() + 1)
            pdf.ln(5)
        elif kind == "h2":
            pdf.ln(2)
            pdf.set_font("Helvetica", "B", 11.5)
            pdf.set_text_color(35, 55, 105)
            mc(pdf, 6.5, text)
            pdf.ln(1)
        elif kind == "p":
            pdf.set_font("Helvetica", "", 10.5)
            pdf.set_text_color(40, 40, 40)
            mc(pdf, 5.8, text)
            pdf.ln(2.5)
        elif kind == "b":
            pdf.set_font("Helvetica", "", 10.5)
            pdf.set_text_color(40, 40, 40)
            x = pdf.l_margin
            pdf.set_xy(x, pdf.get_y())
            pdf.cell(6, 5.8, "-")
            pdf.set_x(x + 6)
            pdf.multi_cell(0, 5.8, clean(text), new_x="LMARGIN", new_y="NEXT")
            pdf.ln(2)
        elif kind == "how":
            pdf.set_font("Helvetica", "BI", 9)
            pdf.set_text_color(150, 95, 20)
            mc(pdf, 5, "How we built it")
            pdf.set_font("Helvetica", "", 10)
            pdf.set_text_color(70, 70, 70)
            mc(pdf, 5.5, text)
            pdf.ln(3)

    pdf.output(OUT)
    print(f"Wrote {OUT}")
    print(f"Pages: {pdf.page_no()}")


if __name__ == "__main__":
    main()
