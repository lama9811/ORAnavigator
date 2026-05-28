#!/usr/bin/env python3
"""Generate ORA-Navigator-Architecture-v4.pdf.

v4 corrections over v3:
  - Layer 1 description in the chat pipeline: v3 said the BACKEND prefetches
    KB chunks into memory_context. That's wrong on both counts. The TF-IDF
    prefetch lives in adk_agent/.../kb_prefetch.py and runs inside the ADK
    agent via _select_model() (a before_model_callback). The prefetched docs
    are injected into the LLM's SYSTEM INSTRUCTION via append_instructions(),
    not into memory_context (which is the long-term user-fact stream from
    the persistent memory layer). v4 fixes this.
  - Test count refreshed 158 -> 201 (+43 today).
  - Cloud Run revisions refreshed.
  - milam5@morgan.edu admin user id corrected from 2 to 6.
  - Added a new Section 10.5 "Shipped 2026-05-28" capturing today's work
    (identifier-check extension, profile fields, the prefetch fix, the
    Pydantic shadow-class bug).
"""

from datetime import date
from pathlib import Path
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib import colors
from reportlab.lib.units import inch
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak,
    KeepTogether,
)


OUTPUT_PATH = Path.home() / "Desktop" / "ORA-Navigator-Architecture-v4.pdf"

# ---- Styles ----
styles = getSampleStyleSheet()
H1 = ParagraphStyle(
    "H1", parent=styles["Heading1"], fontName="Helvetica-Bold",
    fontSize=20, textColor=colors.HexColor("#0b2545"),
    spaceAfter=8, spaceBefore=0,
)
H2 = ParagraphStyle(
    "H2", parent=styles["Heading2"], fontName="Helvetica-Bold",
    fontSize=14, textColor=colors.HexColor("#0b2545"),
    spaceAfter=6, spaceBefore=14,
)
H3 = ParagraphStyle(
    "H3", parent=styles["Heading3"], fontName="Helvetica-Bold",
    fontSize=11, textColor=colors.HexColor("#13315c"),
    spaceAfter=4, spaceBefore=8,
)
BODY = ParagraphStyle(
    "Body", parent=styles["BodyText"], fontName="Helvetica",
    fontSize=10, leading=14, spaceAfter=6, textColor=colors.HexColor("#1f2937"),
)
BULLET = ParagraphStyle(
    "Bullet", parent=BODY, leftIndent=14, bulletIndent=2, spaceAfter=3,
)
META = ParagraphStyle(
    "Meta", parent=BODY, fontSize=9, textColor=colors.HexColor("#475569"),
    spaceAfter=4,
)
FOOTER = ParagraphStyle(
    "Footer", parent=BODY, fontSize=8, alignment=1,
    textColor=colors.HexColor("#64748b"),
)


def bullet(text):
    return Paragraph(f"• {text}", BULLET)


def kv_table(rows):
    t = Table(rows, colWidths=[1.6 * inch, 4.6 * inch])
    t.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, -1), "Helvetica"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("TEXTCOLOR", (0, 0), (0, -1), colors.HexColor("#475569")),
        ("TEXTCOLOR", (1, 0), (1, -1), colors.HexColor("#1f2937")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
    ]))
    return t


def page_footer(canvas, doc):
    canvas.saveState()
    canvas.setFont("Helvetica", 8)
    canvas.setFillColor(colors.HexColor("#94a3b8"))
    canvas.drawCentredString(LETTER[0] / 2, 0.4 * inch,
                             f"ORA Navigator · Architecture & AI-Agent Reference · v4 · page {doc.page}")
    canvas.restoreState()


def build():
    story = []

    # ===== COVER =====
    story.append(Paragraph("ORA Navigator", H1))
    story.append(Paragraph("Architecture &amp; AI-Agent Reference · v4", META))
    story.append(Paragraph(
        f"Built for Morgan State University, Office of Research Administration · "
        f"Generated {date.today().isoformat()}", META,
    ))
    story.append(Spacer(1, 8))

    story.append(Paragraph(
        "ORA Navigator is an AI assistant for Morgan State faculty, principal "
        "investigators, research staff, and department admins. It helps them "
        "navigate the Office of Research Administration — its policies, forms, "
        "deadlines, and contacts. The app lives at <b>ora.inavigator.ai</b> and "
        "combines a Gemini-powered chatbot, a 382-document knowledge base, a "
        "browseable forms catalog, a per-user proposal tracker, and four AI "
        "agents that together cover the full grant-writing lifecycle.",
        BODY,
    ))

    story.append(Spacer(1, 6))
    story.append(kv_table([
        ["Audience", "Faculty / PIs / research staff / department chairs"],
        ["Live URL", "https://ora.inavigator.ai"],
        ["Stack", "Vite + React 19  /  FastAPI + SQLAlchemy  /  Google ADK + Gemini 2.5 Flash"],
        ["Cloud", "Google Cloud Run (3 services) · Cloud SQL MySQL · Vertex AI Search · Redis Cloud"],
        ["KB size", "382 docs in a 9-section tree mirroring morgan.edu/ora"],
        ["AI agents", "Solicitation Ingestion · Draft Critic · Deadline Watcher · Sponsor Fit-Finder"],
        ["Tests", "<b>201 backend tests passing</b> (+43 today over yesterday's 158)"],
        ["Repo", "github.com/lama9811/ORAnavigator"],
    ]))

    story.append(Spacer(1, 10))
    story.append(Paragraph(
        "<b>New since v3 (May 28, 2026):</b> profile fields surface Department, "
        "Title, Role, and research Interests to the chatbot on every turn; the "
        "identifier-faithfulness guard now also catches hallucinated dates, "
        "dollar amounts, emails, and phone numbers; the TF-IDF prefetch no "
        "longer silently disables itself after the first tool call in a "
        "session. Also: v3's Layer 1 description was wrong about where "
        "prefetch lives — corrected here in Section 3.",
        BODY,
    ))

    story.append(PageBreak())

    # ===== 1. ARCHITECTURE =====
    story.append(Paragraph("1. Architecture — three services on Cloud Run", H2))
    story.append(Paragraph(
        "ORA Navigator runs as three Cloud Run services. Each has one job and "
        "talks to the others over HTTPS.", BODY,
    ))

    story.append(Paragraph("Frontend (port 3001)", H3))
    story.append(bullet("Vite + React 19 SPA, served by nginx. Installable as a PWA."))
    story.append(bullet("Routes: <code>/</code> (chat), <code>/forms</code>, <code>/my-proposals</code>, "
                        "<code>/funding-matches</code>, <code>/profile</code>, <code>/admin</code>."))
    story.append(bullet("Talks only to the backend — never directly to the ADK or Vertex AI."))

    story.append(Paragraph("Backend (port 5002)", H3))
    story.append(bullet("FastAPI + SQLAlchemy + JWT auth. The only service that touches the database and cache."))
    story.append(bullet("Owns chat orchestration, persistent memory, cache, Forms / Proposals / Solicitation "
                        "endpoints, plus the four agent endpoints."))
    story.append(bullet("Connects to Cloud SQL via TCP+SSL locally and via Unix socket through the Cloud SQL "
                        "Auth Proxy in production."))

    story.append(Paragraph("ADK agent (port 8081)", H3))
    story.append(bullet("Google ADK wrapping Gemini 2.5 Flash, bound to a Vertex AI Search tool over the KB."))
    story.append(bullet("Owns the TF-IDF prefetch (Layer 1 of the chat pipeline — see Section 3)."))
    story.append(bullet("Internal only — the backend calls it with a Bearer token; the browser never reaches it."))

    # ===== 2. KB =====
    story.append(Paragraph("2. The knowledge base — 382 docs", H2))
    story.append(Paragraph(
        "The KB is the single source of truth. It lives in two places: as JSON "
        "files in the repo (for traversal + the TF-IDF prefetch) and as a "
        "Vertex AI Search datastore (for semantic retrieval).", BODY,
    ))
    story.append(bullet("Nine top-level sections mirror the morgan.edu/ora left nav: <code>about</code>, "
                        "<code>pre_award</code>, <code>post_award</code>, <code>policies_and_guidelines</code>, "
                        "<code>research_compliance</code>, <code>trainings</code>, <code>resources</code>, "
                        "<code>funding_sources</code>, <code>ora_announcements</code>."))
    story.append(bullet("Vertex AI Search datastore <code>oranavigator-kb-v8</code> in location <code>us</code>. "
                        "Dense + sparse retrieval out of the box."))
    story.append(bullet("<code>backend/kb_browser.py</code> enumerates the tree deterministically for direct "
                        "browse questions (\"list all forms\")."))
    story.append(bullet("<code>adk_agent/.../kb_prefetch.py</code> caches the 382 docs in RAM on agent startup "
                        "for the TF-IDF prefetch — refreshed every 5 minutes."))

    # ===== 3. CHAT PIPELINE =====
    story.append(Paragraph("3. The chat pipeline — three layers", H2))
    story.append(Paragraph(
        "Every chat turn goes through three layers. Each one exists because the "
        "layer above it has known failure modes.", BODY,
    ))

    story.append(Paragraph("Pre-flight: cache lookup", H3))
    story.append(bullet("Query is hashed and checked in three tiers: L1 (memory) → L2 (Redis) → L3 (semantic similarity)."))
    story.append(bullet("Never cached: personal-recall questions (<i>am I</i>, <i>my X</i>, <i>remind me</i>) "
                        "and refusal responses."))

    story.append(PageBreak())

    story.append(Paragraph("Layer 1: TF-IDF prefetch + agent call", H3))
    story.append(Paragraph(
        "<b>Corrected from v3.</b> v3 said the backend prefetches KB chunks "
        "into <code>memory_context</code>. Both halves were wrong: the prefetch "
        "lives in the ADK agent (not the backend), and it injects into the "
        "model's <i>system instruction</i> (not <code>memory_context</code>, "
        "which carries long-term user facts).", BODY,
    ))
    story.append(bullet("Inside the ADK agent, <code>_select_model()</code> in "
                        "<code>agent.py</code> is registered as the <code>before_model_callback</code> "
                        "— it fires before every LLM call."))
    story.append(bullet("It calls <code>prefetch_kb_context(user_text)</code> in "
                        "<code>kb_prefetch.py</code>: a deterministic TF-IDF search over the in-memory "
                        "cache of 382 docs, with an ORA-specific entity boost (IRB, IACUC, SOP #, F&amp;A, "
                        "FWA, etc.). ~30 ms, zero LLM cost."))
    story.append(bullet("The top-5 docs are appended to the system instruction via "
                        "<code>llm_request.append_instructions(...)</code>. The model now sees them "
                        "for every turn — even before deciding whether to call the Vertex AI Search tool."))
    story.append(bullet("Gemini then gets question + chat history + profile + persistent memory + the "
                        "prefetched docs, and decides whether to also call its <code>VertexAiSearchTool</code> "
                        "for semantic search."))

    story.append(Paragraph("Layer 2: grounding metadata + citations", H3))
    story.append(bullet("Backend extracts grounding chunks, resolves each title back to a morgan.edu URL — "
                        "becomes the Sources block under every answer."))

    story.append(Paragraph("Layer 3: regenerate-then-deliver", H3))
    story.append(bullet("<code>_evaluate_grounding()</code> classifies the Pass-1 answer as <code>ok</code> "
                        "or <code>weak</code>. <code>weak</code> → Pass 2 regenerates with a strict KB-only prefix."))
    story.append(bullet("Personal-recall short-circuit: <i>am I / did I / my X / remind me</i> forces "
                        "<code>verdict=ok</code> since the answer lives in chat history, not the KB."))
    story.append(bullet("Faithfulness footers append a \"verify with ORA\" disclaimer when the answer mentions "
                        "an identifier (SOP / FWA / EIN / UEI / F&amp;A rate / <b>date / dollar amount / email "
                        "/ phone</b>) that doesn't appear verbatim in the retrieved KB chunks. ORA's own "
                        "contact info (443-885-4044, ask.ora@morgan.edu) is whitelisted so canned refusals "
                        "don't self-flag."))

    # ===== 4. PERSISTENT MEMORY =====
    story.append(Paragraph("4. Persistent memory — four layers", H2))
    story.append(Paragraph(
        "Persistent memory lets the chat remember what the user said yesterday, "
        "last week, or in their very first conversation.", BODY,
    ))
    story.append(Paragraph("Layer 1 — chat_history (verbatim turns)", H3))
    story.append(bullet("Every (query, response) pair stored verbatim, keyed by <code>user_id</code> and "
                        "<code>session_id</code>."))
    story.append(Paragraph("Layer 2 — session_summary (rolling)", H3))
    story.append(bullet("After ~8 turns, older half summarized by Gemini and written to "
                        "<code>chat_history.session_summary</code>."))
    story.append(Paragraph("Layer 3 — user_memories (cross-session facts)", H3))
    story.append(bullet("Stores extracted facts: <code>department</code>, <code>role</code>, "
                        "<code>active_grant</code>, <code>sponsor</code>, <code>interest</code>, "
                        "<code>preference</code>. Each row carries an embedding for semantic recall."))
    story.append(bullet("When a user creates a proposal, an <code>active_grant</code> memory is also written."))
    story.append(bullet("<b>New 2026-05-28.</b> When a user fills in Profile fields (Department / Role / "
                        "Interests), <code>mirror_profile_to_memories()</code> upserts matching "
                        "<code>UserMemory</code> rows so the agent's <code>memory_context</code> and Sponsor "
                        "Fit-Finder see them on the next request — no code change needed in those readers."))
    story.append(Paragraph("Layer 4 — user_suggested_questions (personalized)", H3))
    story.append(bullet("Home screen's suggestions precomputed per user from <code>user_memories</code> + chat history."))

    # ===== 5. CACHE =====
    story.append(Paragraph("5. The cache — three tiers", H2))
    story.append(bullet("<b>L1</b> — in-memory <code>TTLCache</code>, per instance, 24 h TTL, ~1 µs lookup."))
    story.append(bullet("<b>L2</b> — Redis Cloud, shared across instances, ~7 d TTL."))
    story.append(bullet("<b>L3</b> — semantic similarity via <code>text-embedding-004</code> at 0.95 cosine; "
                        "catches paraphrases of cached questions."))

    # ===== 6. FORMS CATALOG =====
    story.append(Paragraph("6. Forms catalog — /forms", H2))
    story.append(Paragraph(
        "Chat is great for \"tell me about X.\" The Forms catalog is the one-click "
        "alternative for \"just give me the PDF.\"", BODY,
    ))
    story.append(bullet("Reads <code>_all_documents.jsonl</code> once at startup. Filters to ~74 form-like docs."))
    story.append(bullet("Sponsor tags derived by keyword match on title + content; role tags by category heuristic."))
    story.append(bullet("<code>GET /api/forms?category=&amp;sponsor=&amp;role=</code> — auth-required. No LLM call."))

    story.append(PageBreak())

    # ===== 7. PROPOSALS =====
    story.append(Paragraph("7. Proposals tracker — /my-proposals", H2))
    story.append(Paragraph(
        "Forms answer \"where's the PDF?\" The Proposals tracker answers "
        "\"am I done yet?\"", BODY,
    ))
    story.append(Paragraph("Schema (Cloud SQL)", H3))
    story.append(bullet("<code>submissions</code> — id, user_id, title, sponsor, deadline, status, notes."))
    story.append(bullet("<code>submission_tasks</code> — id, submission_id (FK ON DELETE CASCADE), title, "
                        "kb_doc_id, due_offset_days, status, sort_order."))
    story.append(bullet("<code>deadline_reminder_log</code> — id, submission_id, threshold_days, sent_at, "
                        "sent_to. Prevents Deadline Watcher double-sends."))
    story.append(Paragraph("Sponsor templates", H3))
    story.append(bullet("Generic (10 tasks), NSF (14 tasks: + DMP, C&amp;P, Facilities, NSF EIR), NIH (14 tasks: "
                        "+ Specific Aims, PMCID, RCR)."))

    # ===== 8. PROFILE FIELDS (NEW SECTION FOR v4) =====
    story.append(Paragraph("8. User profile fields (new 2026-05-28)", H2))
    story.append(Paragraph(
        "The chatbot used to see only <code>Name</code> and <code>Email</code> in "
        "its USER PROFILE prompt block. New fields surface real research-admin "
        "context on every turn.", BODY,
    ))
    story.append(bullet("<code>users.department</code> VARCHAR(128), free text."))
    story.append(bullet("<code>users.title</code> VARCHAR(128), free text."))
    story.append(bullet("<code>users.primary_role</code> VARCHAR(32), enum validated by "
                        "<code>deps.PROFILE_ROLE_ENUM</code>: PI, Co-PI, Research Staff, Department Admin, "
                        "Faculty, Postdoc, Student."))
    story.append(bullet("Interests — comma-separated input. Each token becomes one "
                        "<code>UserMemory(memory_type=\"interest\")</code> row. Case-insensitively deduped, "
                        "whitespace stripped, replace-all on save."))
    story.append(Paragraph(
        "<b>The mirror is what makes it instant.</b> "
        "<code>services/memory_service.mirror_profile_to_memories()</code> upserts "
        "matching <code>UserMemory</code> rows on every profile save. Sponsor "
        "Fit-Finder and <code>build_memory_context()</code> already read those "
        "rows — so the new values flow through both with zero code change in "
        "those services. Profile values WIN over previously auto-extracted "
        "memory.", BODY,
    ))

    # ===== 9. AI AGENTS =====
    story.append(PageBreak())
    story.append(Paragraph("9. The four AI agents — what they do, with examples", H2))
    story.append(Paragraph(
        "Together these four agents cover the entire grant-writing lifecycle. "
        "They share data through the proposal record (Section 10 explains how), "
        "so one upload lights up all four.", BODY,
    ))

    story.append(Paragraph("9.1 Solicitation Ingestion — read the sponsor PDF for you", H3))
    story.append(Paragraph(
        "<b>What it does in plain English.</b> Drop a sponsor's funding "
        "announcement (a 20-60 page PDF). The agent reads it, finds the "
        "deadline, page limits, required attachments, budget cap, and "
        "submission portal — then pre-fills your proposal tracker. You review "
        "and click \"Create.\"", BODY,
    ))
    story.append(Paragraph(
        "<b>Example.</b> Dr. Garcia downloads the NSF Cyber-Physical Systems "
        "26-518 solicitation — a 50-page PDF. She drops it in. 15 seconds "
        "later, the app shows: deadline August 14, 2026; $1.2 M cap; 9 required "
        "attachments; page limits per section. Each field has a source quote: "
        "\"we got the deadline from page 3: 'due no later than 5:00 p.m. on "
        "August 14...'\" She clicks Create. Now she has a tracked proposal "
        "with a 14-item checklist tuned to this exact solicitation.", BODY,
    ))

    story.append(Paragraph("9.2 Draft Critic — pre-submission sanity check", H3))
    story.append(Paragraph(
        "<b>What it does.</b> Upload your proposal draft PDF. The agent "
        "compares it against the rules from your specific solicitation (the "
        "one you already saved) and tells you what's wrong before you submit. "
        "Verdict banner at the top: 🟢 Ready / 🟡 Minor / 🟠 Needs review / 🔴 Critical.", BODY,
    ))
    story.append(Paragraph(
        "<b>Example.</b> Dr. Garcia's draft is 17 pages — her solicitation "
        "caps Project Description at 15. Draft Critic flags: Page count 17/15 "
        "— trim 2 pages. It also notices: Missing required attachments: Data "
        "Management Plan, Postdoctoral Mentoring Plan.", BODY,
    ))

    story.append(Paragraph("9.3 Deadline Watcher — emails before deadlines", H3))
    story.append(Paragraph(
        "<b>What it does.</b> Every morning at 7 a.m. ET, the agent looks at "
        "all your active proposals. If any are exactly 14, 7, 3, 1, or 0 days "
        "from their deadline, it emails you with the title, deadline date, and "
        "your remaining checklist items.", BODY,
    ))
    story.append(Paragraph(
        "<b>What makes it agentic.</b> Runs autonomously via Cloud Scheduler "
        "(endpoint exists; scheduler not yet enabled on this GCP project — "
        "one <code>gcloud</code> command turns it on). The "
        "<code>deadline_reminder_log</code> table tracks every (proposal, "
        "threshold) pair already sent so re-runs never double-email.", BODY,
    ))

    story.append(Paragraph("9.4 Sponsor Fit-Finder — funding you might've missed", H3))
    story.append(Paragraph(
        "<b>What it does.</b> Looks at your department, stated research "
        "interests, and past sponsors. Ranks all 15 funding-source categories "
        "in the KB against your profile. Each match has a one-sentence \"why "
        "this fits you\" explanation generated by Gemini Flash, with a "
        "deterministic template fallback when the LLM is unavailable.", BODY,
    ))
    story.append(Paragraph(
        "<b>Five-signal deterministic scoring</b> (no embeddings — 15 docs is "
        "small enough that keyword matching beats embeddings on latency and "
        "interpretability): base 10 + HBCU/MSI +30 + discipline keyword "
        "+6/match cap 30 + interest +8/match cap 24 + sponsor history "
        "+15/match cap 30 + role bias ±. Max ~132.", BODY,
    ))
    story.append(Paragraph(
        "<b>Example.</b> Dr. Garcia is in Computer Science. Her chat history "
        "mentions \"cyber-physical systems\" and \"AI safety.\" She has one "
        "prior NSF grant. Funding Matches now ranks: <b>NSF HBCU-UP Computer "
        "Science Track</b> (94, Excellent) — \"Matches HBCU/MSI eligibility, "
        "Computer Science discipline, and your NSF sponsor history.\"", BODY,
    ))

    # ===== 10. COLLABORATION =====
    story.append(PageBreak())
    story.append(Paragraph("10. How the four agents collaborate", H2))
    story.append(Paragraph(
        "The agents don't talk to each other directly. They share data through "
        "one thing: the proposal record in the database. One source of truth, "
        "multiple specialized readers.", BODY,
    ))
    story.append(bullet("<b>Solicitation Ingestion</b> writes the proposal record (sponsor, deadline, "
                        "page limits, budget cap, required-attachment tasks)."))
    story.append(bullet("<b>Draft Critic</b> reads the proposal's <code>notes</code> (page limits, budget cap) "
                        "+ task list (required attachments) when grading the draft."))
    story.append(bullet("<b>Deadline Watcher</b> reads the deadline + open task list for the email body."))
    story.append(bullet("<b>Sponsor Fit-Finder</b> reads the proposal's sponsor (via the "
                        "<code>active_grant</code> memory written at create-time) to weight future "
                        "opportunities."))
    story.append(Paragraph("Why this matters architecturally", H3))
    story.append(bullet("No agent needs to know the others exist — they just read shared state."))
    story.append(bullet("Adding a fifth agent (Compliance Sentinel, Budget Helper, ...) is additive: "
                        "another reader, no changes to the existing four."))

    # ===== 10.5 SHIPPED 2026-05-28 (NEW) =====
    story.append(Paragraph("10.5 Shipped 2026-05-28", H2))
    story.append(Paragraph(
        "Today's four changes: extended the identifier guard, surfaced richer "
        "user context to the chatbot, and squashed two production bugs that "
        "had been silently masking each other.", BODY,
    ))
    story.append(bullet("<b>Identifier-faithfulness extension</b> — dates, dollar amounts, emails, and phone "
                        "numbers now join SOP / FWA / EIN / UEI / F&amp;A% in the verbatim-check. ORA's own "
                        "contact info (443-885-4044, ask.ora@morgan.edu) is whitelisted so canned refusals "
                        "don't self-flag. 16 new tests."))
    story.append(bullet("<b>Profile fields</b> — Department, Title, Role, and research Interests. The mirror "
                        "(<code>services/memory_service.mirror_profile_to_memories</code>) writes matching "
                        "<code>UserMemory</code> rows so Sponsor Fit-Finder and <code>build_memory_context()</code> "
                        "see them automatically. 22 tests."))
    story.append(bullet("<b>Multi-turn prefetch bug fix</b> — <code>_select_model</code> in the ADK agent "
                        "scanned ALL contents for any <code>function_response</code> and silently disabled "
                        "the TF-IDF prefetch for the rest of any session once the model called a tool. "
                        "Symptom: follow-up training-video requests returned \"I'm sorry, I couldn't generate "
                        "a response.\" Fix: only check the LAST content. 5 regression tests."))
    story.append(bullet("<b>ProfileUpdateRequest shadow-class fix</b> — a local <code>class "
                        "ProfileUpdateRequest(BaseModel)</code> in <code>main.py</code> shadowed the extended "
                        "version in <code>deps.py</code>; the PUT handler bound to the stripped local stub. "
                        "Took ~2 hours to diagnose because the redeploys were honest — the bug was a code "
                        "path, not a deploy-pipeline issue."))

    # ===== 11. DEPLOY =====
    story.append(Paragraph("11. Deploy &amp; tests", H2))
    story.append(Paragraph("Live revisions", H3))
    story.append(kv_table([
        ["Backend",  "oranavigator-backend-00052-8zp"],
        ["Frontend", "oranavigator-frontend-00023-f5z"],
        ["ADK",      "oranavigator-adk-00014-9w4"],
    ]))

    story.append(Paragraph("Test status", H3))
    story.append(bullet("<b>201 backend tests passing</b> (158 → 201, +43 today)."))
    story.append(bullet("<code>test_grounding.py</code>: +16 identifier-check tests, +5 prefetch tests."))
    story.append(bullet("<code>test_profile_fields.py</code>: 22 tests (validation, single-value upsert, "
                        "multi-value replace-all, cross-user isolation)."))
    story.append(bullet("<code>test_draft_critic.py</code>: 43. <code>test_deadline_watcher.py</code>: 20. "
                        "<code>test_sponsor_fit_finder.py</code>: 19."))
    story.append(bullet("Frontend builds clean (Vite + React 19, no errors)."))

    story.append(Paragraph("Operational notes", H3))
    story.append(bullet("Cloud Scheduler not yet enabled in this GCP project. Deadline Watcher endpoint "
                        "exists but isn't on a schedule — one <code>gcloud</code> command turns it on."))
    story.append(bullet("Deploys wipe <code>SMTP_*</code> / <code>API_URL</code> / <code>RESEARCH_SECRET</code> "
                        "env vars. Recovery: <code>bash /tmp/post_deploy_backend.sh</code> (~30 sec)."))

    # ===== 12. AUTH =====
    story.append(PageBreak())
    story.append(Paragraph("12. Authentication, authorization, privacy", H2))
    story.append(Paragraph("Sign-up + login", H3))
    story.append(bullet("Email + password via the <code>users</code> table. Passwords hashed with bcrypt. "
                        "JWTs signed with <code>ora-jwt-secret</code> from Secret Manager."))
    story.append(bullet("Sign-up restricted to <code>@morgan.edu</code> addresses by "
                        "<code>backend/routers/auth.py</code>."))
    story.append(bullet("Email verification wired up via Gmail SMTP. Verify-first flow: new users receive a "
                        "verification link before any DB row is created."))

    story.append(Paragraph("Roles", H3))
    story.append(bullet("Two roles: <code>user</code> (default) and <code>admin</code>. Admin endpoints "
                        "(KB editing, research/feedback aggregation) are gated on <code>user.role == \"admin\"</code>."))
    story.append(bullet("Production admin: <code>milam5@morgan.edu</code> (<b>id=6</b>, promoted 2026-05-28; "
                        "v3 said id=2 — that was stale)."))

    story.append(Paragraph("Cross-user safety", H3))
    story.append(bullet("Every read and write in <code>proposals_service.py</code> + "
                        "<code>sponsor_fit_finder.py</code> filters by <code>user_id</code>."))
    story.append(bullet("Memory queries filter by <code>user_id</code> and respect pause flags."))
    story.append(bullet("Cache layer never stores personal-recall responses — closes the practical "
                        "user-to-user leak path."))

    story.append(Spacer(1, 16))
    story.append(Paragraph(
        "End of document · ORA Navigator Architecture v4 · " + date.today().isoformat(), FOOTER,
    ))

    # ===== BUILD =====
    doc = SimpleDocTemplate(
        str(OUTPUT_PATH), pagesize=LETTER,
        leftMargin=0.7 * inch, rightMargin=0.7 * inch,
        topMargin=0.7 * inch, bottomMargin=0.7 * inch,
        title="ORA Navigator — Architecture & AI-Agent Reference v4",
        author="Mingma Lama",
    )
    doc.build(story, onFirstPage=page_footer, onLaterPages=page_footer)
    print(f"Wrote {OUTPUT_PATH}")


if __name__ == "__main__":
    build()
