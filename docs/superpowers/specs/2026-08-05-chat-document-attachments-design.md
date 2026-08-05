# Chat: hand over the document, not just a description of it

**Date:** 2026-08-05
**Status:** Design approved, pending implementation
**Branch:** `fixes`

## Goal

Ask the chatbot for a form and get the form. "give me this form pf-10 contractual
personal request" should return the answer it already returns *plus* a clickable link
to the actual DocuSign form.

## Problem

Reported from production. The user asked for the PF-10 form; the assistant described it
accurately — DocuSign, revisions and continuations only, new employees go through EPAF —
and gave no way to reach it. The `/forms` page shows the same form with a working link.

The link exists. The chatbot cannot see it.

The KB document carries it:

```
doc_id       : form_docusign_pf10_contractual_personnel
procedure_url: https://na2.docusign.net/Member/PowerFormSigning.aspx?PowerFormId=7bf272d8-…
source_url   : https://www.morgan.edu/office-of-research-administration/post-award/forms
```

The live Vertex AI Search datastore does not. Fetched 2026-08-05 from
`oranavigator-kb-v8`:

```
structData keys: [category, doc_id, file_path, playwright_verified,
                  source_file, subcategory, title]
   procedure_url = None
   source_url    = None
```

`setup_kb_datastore_v8.py` never wrote either field, so no amount of prompting can make
the model produce a link it was never given. The answer was correct and complete for the
data available to it.

This is the "two copies of the KB metadata and nothing syncs them" problem surfacing as
a user-visible symptom: `/forms` reads the committed snapshot (which has
`procedure_url`), chat reads the datastore (which does not).

Chat's own link map does not help either. `vertex_agent._get_kb_url_map()` (line 433)
builds `{normalized title → source_url}` — deliberately the *page*, for provenance — so
even a Sources footer would offer `/post-award/forms` rather than the form.

## Approach

**Resolve links in code and attach them below the answer. The model never writes a URL.**

This is not a stylistic preference. A DocuSign PowerForm URL is ~150 characters of
opaque GUIDs. A model reproducing one from a retrieved chunk will eventually corrupt a
character, and the result is a plausible-looking link to a dead page — an error no
grounding check catches, because the sentence around it is true. Deterministic
resolution makes the link either exactly right or absent. Same principle as golden rule
1: the deterministic core is authoritative, the model explains.

The resolver already exists and is already used by `/forms`
(`backend/services/forms_catalog.py:234`):

> `resolve_kb_doc(doc_id)` → `{doc_id, title, url, source_url}` for **any** KB document,
> where `url = procedure_url or source_url`.

So this is mostly wiring.

### What gets attached

Two blocks with distinct meanings, which keeps both honest:

- **Sources** (unchanged) — the *page* a claim came from. Provenance.
- **Documents** (new) — the *thing itself*. A file or form to open.

A document qualifies when its `procedure_url` is a distinct destination **and** that
destination is a file or a form, not another web page. Measured across the 383
documents:

```
no procedure_url                    :  14
procedure_url same as source_url    :  65   → nothing extra to offer
distinct destination                : 304
    262  file download (pdf/docx/xlsx/pptx)
     12  DocuSign form
      8  Google form
     22  other web page              → excluded; Sources already covers it
```

So **282 documents** can carry an attachment. This is not only forms: ask about F&A
rates and the document points at `2024-2026_MorganState_FandA_ltr.pdf`, the actual rate
letter.

### Data flow

1. The turn completes and `result["citations"]` / the retrieved chunks are in hand at
   the DELIVER step of `_run_verified` and `_run_verified_stream` — the same place
   citations are attached today, so the streaming path is covered when the terminal
   `done` event replaces the streamed preview.
2. Map each retrieved chunk's **title** → `doc_id`, reusing the normalisation already
   used by `_get_kb_url_map` (titles are what chunks carry; `doc_id` is not exposed).
3. `resolve_kb_doc(doc_id)` → keep entries whose `url` is a file or form destination.
4. Deduplicate, preserve retrieval order, **cap at 3**, attach as
   `result["attachments"] = [{title, url, kind}]` where `kind ∈ {form, file}`.

### Suppression

Attachments are cleared wherever citations are cleared — the existing
`_is_non_kb_reply(message, text)` and `_is_personal_identity(message)` guards in both
DELIVER steps. Small talk, off-topic refusals, outages, acknowledgements and personal
recall get no attachments.

This is not hypothetical tidiness: stapling KB Sources onto "thanks!" was a real
production bug fixed in July, caused by the model running a stray KB search on a
non-KB turn. Attachments would reintroduce exactly that symptom in a more embarrassing
form — a DocuSign form offered in reply to "how are you". Reuse the guards; do not
write new ones.

### Caching

Chat answers are cached and citations ride a parallel `cit:` key so a hit re-emits
Sources. Attachments need the same, or the second person to ask for PF-10 gets the
answer without the form. Store under an `att:` key with the same L1/L2 lifetimes.

### Frontend

A **Documents** block under the answer in `Chatbox.jsx`, styled like the existing
Sources block, and the same in `GuestChatbox.jsx`. Each row is title + an open-in-new-tab
link. Nothing else changes.

## Non-goals

- Backfilling `procedure_url` into the datastore so the model can write links inline.
  Considered; rejected for this pass because it puts URL fidelity back in the model's
  hands. Worth revisiting *alongside* deterministic rendering, never instead of it.
- Changing what Sources point at. They stay page-level provenance.
- Serving files from our own domain. Links point at morgan.edu / DocuSign, as `/forms`
  already does; nothing is rehosted.
- Attaching to the 22 documents whose `procedure_url` is just another web page.

## Testing

`backend/tests/` alongside the existing chat tests, no network:

1. A retrieved form document produces one attachment with the exact `procedure_url`.
2. A document whose `procedure_url` equals its `source_url` produces none.
3. A document whose destination is a web page produces none.
4. More than three qualifying documents are capped at three, in retrieval order.
5. Duplicate titles across chunks collapse to one attachment.
6. Small talk, refusal, outage and personal-recall turns produce none.
7. A cache hit re-emits attachments.
8. Title normalisation matches the same way `_get_kb_url_map` does (whitespace, case).

## Risks

- **The snapshot is the source of truth for links and can go stale.** A document
  authored in the admin dashboard after the snapshot was committed resolves to nothing,
  so it silently gets no attachment. Acceptable — the failure is a missing link, not a
  wrong one — but it is the same staleness that caused this bug and it will recur.
- **A dead `procedure_url` attaches just as confidently as a live one.** The KB already
  contains at least one broken target (`HR02 Accident Investigation`, 403). The file
  scrape designed in `2026-08-05-kb-scrape-pdf-coverage-design.md` is what detects
  those; this feature should be read alongside it.
- **Three may be too few or too many.** The cap is a guess. Worth revisiting once real
  questions are observed.
