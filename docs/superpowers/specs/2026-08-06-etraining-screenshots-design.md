# Show the eTraining screenshots in chat answers

**Date:** 2026-08-06
**Status:** Design approved, pending implementation
**Branch:** `main`

## Goal

Ask *"how do I check my budget in Banner?"* and get the steps, a link to the lesson — and **the actual Banner screens**. The modules teach by showing; the knowledge base currently holds only what they say.

## Problem

The eTraining extraction captured 100% of the prose and **none of the 466 images**. That is the largest remaining gap, and it is concentrated exactly where it hurts: **Navigating Banner (99 images)** and **Purchasing (123)** teach by pointing at fields on a screen. A PI can be told the process and still not know which box to type in.

Measured across three modules (238 images): **185 are real screenshots, 53 are icons or logos** — a ~78/22 split by file size, with no ambiguous middle.

## What the module data already gives us

Two facts make this cheap:

1. **Every image is publicly fetchable** on `articulateusercontent.com/{key}` — verified: HTTP 200, real bytes, no authentication.
2. **Every image sits inside a named step**, so the caption is free and needs no AI: *"Enter Project Index(s)"*, *"Banner Links Homepage"*, *"Workflow: My Processes"*.

What the payload does **not** give: alt text, captions, or usable dimensions (`width`/`height` are `None`). Hence file size as the icon filter.

## Approach

**Show, do not read.** No vision model. The chatbot answers from the text it already has and displays the relevant screenshots beneath. This does not let it answer a question whose answer exists *only* inside an image — that would need vision extraction, deliberately out of scope.

### 1. Capture

The Rise extractor gains image capture beside the existing prose walk. Per lesson: `{key, caption, bytes}` where `caption` is the enclosing step title. Images under **60 KB** are dropped as icons. Crude, and correct in the sample — the failure mode is a stray logo in an answer, never a wrong instruction.

### 2. Mirror to our own storage

A new bucket, **`oranavigator-kb-assets`**, holding `etraining/{module_doc_id}/{filename}`. ~200 MB.

`scripts/mirror_etraining_images.py` downloads from Articulate and uploads to GCS, **skipping objects that already exist** so it is safe to re-run and cheap to refresh.

**Public-read, deliberately.** These images are already served with no authentication from Articulate's CDN, so a public bucket adds no exposure — and it avoids proxying every image through a backend that runs a single uvicorn worker with CPU throttled outside requests.

The project has no application bucket today (only `..._cloudbuild`), and the backend service account holds `roles/storage.objectViewer` — read only. Both are one-time setup.

### 3. Reference

Each **lesson** document gains an `images` list — `[{url, caption}]` — written into `struct_data` alongside `procedure_url`, and into the committed snapshot. Module overview documents get none: instructions live in lessons.

### 4. Serve and render

Rides the path built for the Documents block on 2026-08-05: resolve from the cited lesson, **cap at 4**, and inherit the existing suppression guards (`_is_non_kb_reply`, `_is_personal_identity`) so a screenshot can never appear under "thanks!". That reuse is the point — a second set of guards would drift from the first.

The chat UI renders thumbnails under the answer; click opens full size.

## Non-goals

- **Vision/OCR extraction** of what the screenshots say. The single biggest possible win and explicitly deferred; it needs ~360 model calls and the same grounding discipline as every other AI path, or it invents field names.
- Images on the 8 module overview documents.
- Independently searchable images.
- Alt text generation.

## Testing

Backend, no network:

1. An image under the size threshold is dropped; one over is kept.
2. A lesson with images resolves to `[{url, caption}]`; one without resolves to `[]`.
3. The cap holds at 4, in document order.
4. Small talk, refusal, outage and personal-recall turns yield no images — the same fixture set the attachment tests use.
5. A document whose `images` is absent or malformed yields `[]` rather than raising.
6. The mirror script skips an object that already exists (dry-run assertion, no GCS call).

Frontend: `npm run build`, then a browser check that thumbnails render and open.

## Risks

- **Republishing breaks source keys.** The image URL embeds the Rise course id, so republishing a module changes it. Because we mirror, existing answers keep working; new screenshots need a re-run of extract + mirror — the same refresh the text already needs. This has already happened once: three modules were republished and the KB kept the superseded links.
- **One caption per step.** Where a step holds several screenshots they share a caption, so an individual image may be loosely labelled. The surrounding lesson text carries the detail.
- **60 KB is a heuristic.** It separated 185 from 53 cleanly in the sample, but a small screenshot or a large logo would be misclassified. Cheap to tune; the consequence is cosmetic.
- **Bucket cost and lifecycle.** ~200 MB is pennies per month, but it is the project's first application bucket — someone must own it. Public-read is a deliberate choice recorded above, not an oversight to be "fixed" later without re-reading that reasoning.
