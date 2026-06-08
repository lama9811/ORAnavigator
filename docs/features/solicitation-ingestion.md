# Solicitation Ingestion (AI Agent)

**In one line:** drop a sponsor's funding PDF and it becomes a tracked proposal with a task list.

## What it does (plain English)
Upload an NSF/NIH/DoD solicitation PDF. The agent reads it and pulls out the deadline, budget cap,
page limits, and required attachments. You **review** the extracted fields (wrong AI guesses are
flagged red), confirm, and it creates a Submission with a checklist of tasks.

## Where it lives
- `backend/services/solicitation_extractor.py` (pdfplumber + Gemini JSON contract).
- `backend/services/gemini_client.py` (shared Gemini access).

## How it works
- A strict `_EXTRACT_SYSTEM` prompt extracts structured JSON, quoting every value from the PDF.
- `_verify_source_quotes` flags any field whose quote isn't actually in the PDF
  (`unverified_fields`, shown red) — **flag, don't drop**, since the human reviews.
- **Two-step** by design: extract → human confirms → commit. A wrong AI deadline never
  auto-commits. This is the canonical "AI proposes, human confirms" pattern.

## API & data
- Tables: `submissions`, `submission_tasks`.

## Don't regress (load-bearing)
- Keep the two-step confirm — never auto-commit extracted fields.
- Gemini model id stays `gemini-2.5-flash`.

## Status
✅ Built & deployed.
