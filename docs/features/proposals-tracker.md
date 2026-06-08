# Proposals Tracker ("My Proposals")

**In one line:** the workspace where a user sees their proposals, tasks, and deadlines.

## What it does (plain English)
Lists a user's proposals (Submissions), each with its checklist of tasks, deadline, and actions
("Open form", "Critique Draft", "Add deadlines to calendar"). The hub that the proposal agents
all feed into.

## Where it lives
- Frontend: `frontend/src/MyProposals.jsx` (route `/my-proposals`).
- Backend: proposals/tasks endpoints in `backend/main.py`; `backend/services/proposal_templates.py`
  seeds the task lists.

## How it works
- A Submission has many `submission_tasks`. `_submission_task_to_dict` emits each task plus, when a
  task is linked to a form, `kb_doc_url` / `kb_doc_title` resolved from the forms catalog so the UI
  can render an "Open form ↗" link (unlinked tasks show no button).
- This is the **shared proposal record** the agents coordinate through (Solicitation Ingestion
  writes it, Draft Critic and Deadline Watcher read it).

## API & data
- Tables: `submissions`, `submission_tasks`.
- E2E coverage: `backend/tests/test_proposals_api_e2e.py`.

## Don't regress (load-bearing)
- Task `kb_doc_id`s must point at real docs (verified in `_all_documents.jsonl` + forms catalog) —
  a bad id makes a dead "Open form" link. Tasks with no matching form are correctly left unlinked.

## Status
✅ Built & deployed.
