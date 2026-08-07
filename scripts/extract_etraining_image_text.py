#!/usr/bin/env python3
"""Transcribe the eTraining screenshots into text so their facts are answerable.

The 262 mirrored screenshots are SHOWN under chat answers but never READ, so a
question whose answer lives only inside an image -- a Banner field name, a form
layout, a menu path -- cannot be answered in words. This script runs Gemini
vision over each mirrored image and records a verbatim transcription.

It is a TRANSCRIBER, not a summarizer. The prompt forbids inference precisely
because these modules teach through worked dollar-figure problems: a model that
"reads" $21,040 as $21,400 would put a wrong number into the KB wearing the same
confident prose as everything else. Every transcription is stored with its
source image key so a suspect figure can always be checked against the picture.

Output: backend/kb_structured/_etraining_image_text.json
Load it into the KB with scripts/load_etraining_image_text.py.

Usage:
  python3 scripts/extract_etraining_image_text.py --dry-run     # 3 images, print
  python3 scripts/extract_etraining_image_text.py               # full run, resumable
  python3 scripts/extract_etraining_image_text.py --module <id> # one module
"""
from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import sys
import time
import urllib.parse
import urllib.request

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KB = os.path.join(REPO, "backend", "kb_structured")
IMAGES_JSON = os.path.join(KB, "_etraining_images.json")
OUT_JSON = os.path.join(KB, "_etraining_image_text.json")

PROJECT = os.getenv("GOOGLE_CLOUD_PROJECT", "infra-vertex-494621-v1")
LOCATION = os.getenv("GOOGLE_CLOUD_LOCATION", "us-central1")
MODEL = os.getenv("VISION_MODEL", "gemini-2.5-flash")
WORKERS = int(os.getenv("VISION_WORKERS", "6"))

# Verbatim only. "Do not infer" is load-bearing, not politeness: the alternative
# is a plausible-looking wrong number in the KB.
PROMPT = """Transcribe ALL text visible in this screenshot VERBATIM.

Rules:
- Preserve field labels, values, menu items, button text, column headers and table structure.
- Do NOT summarize, do NOT paraphrase, do NOT infer, do NOT add anything not literally on screen.
- Copy numbers, dollar amounts, codes and dates EXACTLY as shown, digit for digit.
- If a value is too small or blurry to read, write [unreadable] rather than guessing.
- If the image is a decorative photo, an icon, or a logo with no meaningful text, reply with exactly: NO_TEXT

Output the transcription as plain text with line breaks preserved."""

_client = None
_client_lock = __import__("threading").Lock()


def client():
    """The genai client, built once.

    Built under a lock and pre-warmed before the thread pool starts: constructing
    it concurrently from several workers races on credential resolution and fails
    a random subset of images with "Could not resolve API token from the
    environment" -- which reads like a broken login rather than a race, because
    the images that happened to win the race succeed in the same run.
    """
    global _client
    if _client is None:
        with _client_lock:
            if _client is None:
                from google import genai
                _client = genai.Client(vertexai=True, project=PROJECT, location=LOCATION)
    return _client


def public_url(bucket: str, module: str, key: str) -> str:
    """Public URL for a mirrored image.

    The blob NAME contains literal percent signs -- Rise's own keys are already
    URL-encoded ("Screenshot%25202024-11-11") and the mirror stored the last path
    segment verbatim, so the object is genuinely called `...%2520...`. Fetching it
    over HTTP therefore needs those `%` re-encoded to `%25`, or every screenshot
    with a space in its original filename 404s -- which is most of the real ones,
    while the stock photos (plain names, no escapes) succeed. The failure looks
    like "some images are missing from the bucket" and is not.
    """
    name = urllib.parse.quote(key.rsplit("/", 1)[-1], safe="")
    return f"https://storage.googleapis.com/{bucket}/etraining/{module}/{name}"


def mime_for(name: str) -> str:
    n = name.lower()
    if n.endswith(".png"):
        return "image/png"
    if n.endswith(".gif"):
        return "image/gif"
    if n.endswith(".webp"):
        return "image/webp"
    return "image/jpeg"


def transcribe(url: str) -> tuple[str, str]:
    """Return (transcription, error). Empty transcription + empty error = NO_TEXT."""
    from google.genai import types
    try:
        with urllib.request.urlopen(url, timeout=45) as r:
            blob = r.read()
    except Exception as e:
        return "", f"fetch: {str(e)[:200]}"
    last = ""
    for attempt in range(3):
        try:
            resp = client().models.generate_content(
                model=MODEL,
                contents=[
                    types.Part.from_bytes(data=blob, mime_type=mime_for(url)),
                    PROMPT,
                ],
                config=types.GenerateContentConfig(temperature=0.0, max_output_tokens=4096),
            )
            text = (resp.text or "").strip()
        except Exception as e:
            last = f"model: {str(e)[:200]}"
            time.sleep(2 * (attempt + 1))
            continue
        if text.upper().startswith("NO_TEXT"):
            return "", ""
        if text:
            return text, ""
        # An empty body is not the same as NO_TEXT -- it is usually a truncated
        # or filtered response. Retry rather than silently recording "no text",
        # which would look identical to a decorative photo.
        last = "model: empty response"
        time.sleep(2 * (attempt + 1))
    return "", last


def lessons_for(mod: dict, key: str) -> list[str]:
    return [lid for lid, keys in (mod.get("by_lesson") or {}).items() if key in keys]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="transcribe 3 images and print")
    ap.add_argument("--module", help="restrict to one module doc_id")
    args = ap.parse_args()

    inv = json.load(open(IMAGES_JSON))
    bucket = inv["bucket"]
    modules = inv["modules"]

    done = {}
    if os.path.exists(OUT_JSON) and not args.dry_run:
        prev = json.load(open(OUT_JSON))
        done = {i["key"]: i for i in prev.get("images", [])}
        print(f"resuming: {len(done)} already transcribed")

    work = []
    for mod_id, mod in modules.items():
        if args.module and mod_id != args.module:
            continue
        for img in mod.get("images", []):
            key = img["key"]
            if key in done:
                continue
            work.append({
                "module": mod_id,
                "key": key,
                "filename": key.rsplit("/", 1)[-1],
                "caption": img.get("caption") or "",
                "lessons": lessons_for(mod, key),
                "url": public_url(bucket, mod_id, key),
            })

    if args.dry_run:
        work = work[:3]
    print(f"to transcribe: {len(work)}")
    if work:
        client()   # pre-warm before the pool; see client()


    results = list(done.values())
    errors = []

    def run(item):
        text, err = transcribe(item["url"])
        item = dict(item)
        item["text"] = text
        item["has_text"] = bool(text)
        item["chars"] = len(text)
        if err:
            item["error"] = err
        return item

    with concurrent.futures.ThreadPoolExecutor(max_workers=WORKERS) as ex:
        for i, item in enumerate(ex.map(run, work), 1):
            if item.get("error"):
                errors.append(item)
            results.append(item)
            flag = "ERR " if item.get("error") else ("TEXT" if item["has_text"] else "none")
            print(f"  {i}/{len(work)} {flag} {item['chars']:5d} {item['filename'][:60]}", flush=True)
            if args.dry_run:
                print("    " + (item["text"] or item.get("error", "(no text)")).replace("\n", "\n    ")[:1500])

    if args.dry_run:
        print("\n[dry-run] nothing written")
        return 0

    with_text = [r for r in results if r.get("has_text")]
    payload = {
        "generated_at": __import__("datetime").datetime.now().isoformat(timespec="seconds"),
        "model": MODEL,
        "source": "mirrored eTraining screenshots in gs://%s/etraining/" % bucket,
        "why": (
            "Screenshots were shown in chat but never read. These are VERBATIM "
            "transcriptions, not summaries, so a fact that exists only inside an "
            "image is answerable in words. Figures are transcribed digit for digit; "
            "each entry keeps its source image key for verification."
        ),
        "totals": {
            "images": len(results),
            "with_text": len(with_text),
            "no_text": len(results) - len(with_text) - len(errors),
            "errors": len(errors),
            "chars": sum(r["chars"] for r in with_text),
        },
        "images": sorted(results, key=lambda r: (r["module"], r["filename"])),
    }
    with open(OUT_JSON, "w") as f:
        json.dump(payload, f, indent=1)

    t = payload["totals"]
    print(f"\nimages={t['images']} with_text={t['with_text']} no_text={t['no_text']} "
          f"errors={t['errors']} chars={t['chars']}")
    if errors:
        print("errors:")
        for e in errors[:20]:
            print(f"  {e['filename']}: {e['error']}")
    print(f"wrote {OUT_JSON}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
