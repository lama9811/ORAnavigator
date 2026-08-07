#!/usr/bin/env python3
"""Capture ORA's video narration as text, and record the videos for the chat UI.

ORA publishes ~10 recordings -- the D-RED seminar archive plus two embedded
explainers -- and until now the KB held their URLs and nothing they SAY. That
was recorded as an unfixable limitation ("no transcripts exist"). It is not:
YouTube carries auto-generated English captions for all of them, and one video
alone yields 53,174 characters of ORA staff explaining Morgan's own procedures.

Two outputs, deliberately separate:
  * KB documents carrying the transcript text, so the narration is ANSWERABLE;
  * a videos index (`_ora_videos.json`) the chat path can use to SHOW the video
    alongside an answer, the same way screenshots and attachments are shown.

A transcript is auto-generated speech recognition, not an authored document.
Every document says so in its own first lines, because a mis-heard figure is
indistinguishable from an authored one once it is prose in a KB -- and these
recordings are full of dollar amounts and deadlines. The video link is carried
with it so a reader can always check the source.

Usage:
  python3 scripts/load_ora_video_transcripts.py --dry-run
  python3 scripts/load_ora_video_transcripts.py            # write payload json
  python3 scripts/load_ora_video_transcripts.py --push     # ...and the datastore
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.request
from datetime import date

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KB = os.path.join(REPO, "backend", "kb_structured")
MANIFEST = os.path.join(KB, "_all_documents.jsonl")
VIDEOS_JSON = os.path.join(KB, "_ora_videos.json")
PAYLOAD = os.path.join(KB, "_ora_video_docs.json")

TARGET_CHARS = 4000
MIN_SPLIT = 6000

_YT = re.compile(r"(?:youtube\.com/(?:watch\?v=|embed/)|youtu\.be/)([A-Za-z0-9_-]{11})")


def video_ids_from_kb() -> dict[str, list[str]]:
    """{video_id: [doc_ids that reference it]} -- the D-RED archive lives here."""
    out: dict[str, set] = {}
    for line in open(MANIFEST):
        row = json.loads(line)
        fp = row.get("file_path") or ""
        p = os.path.join(KB, fp)
        if not fp or not os.path.exists(p):
            continue
        try:
            blob = json.dumps(json.load(open(p)))
        except Exception:
            continue
        for vid in _YT.findall(blob):
            out.setdefault(vid, set()).add(row["doc_id"])
    return {k: sorted(v) for k, v in out.items()}


def title_of(vid: str) -> str:
    try:
        req = urllib.request.Request(
            f"https://www.youtube.com/oembed?url=https://www.youtube.com/watch?v={vid}&format=json",
            headers={"User-Agent": "Mozilla/5.0"})
        return json.loads(urllib.request.urlopen(req, timeout=30).read()).get("title", "") or vid
    except Exception:
        return vid


def transcript_of(vid: str) -> tuple[str, str]:
    """(text, error). Auto-captions are fine here; they are what exists."""
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
    except ImportError:
        return "", "youtube_transcript_api not installed"
    try:
        api = YouTubeTranscriptApi()
        fetched = api.fetch(vid)
        return " ".join(s.text for s in fetched).strip(), ""
    except Exception as e:
        try:  # older releases expose a classmethod instead
            data = YouTubeTranscriptApi.get_transcript(vid)
            return " ".join(s["text"] for s in data).strip(), ""
        except Exception:
            return "", str(e)[:200]


def split(text: str) -> list[str]:
    """Sentence-ish chunks. Auto-captions have no headings and little punctuation,
    so cut on word boundaries at a target size rather than pretending otherwise."""
    if len(text) < MIN_SPLIT:
        return [text]
    words, out, cur, n = text.split(), [], [], 0
    for w in words:
        cur.append(w)
        n += len(w) + 1
        if n >= TARGET_CHARS:
            out.append(" ".join(cur))
            cur, n = [], 0
    if cur:
        out.append(" ".join(cur))
    return out


def slug(s: str) -> str:
    return re.sub(r"_+", "_", re.sub(r"[^a-z0-9]+", "_", s.lower())).strip("_")[:60]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--push", action="store_true")
    ap.add_argument("--extra", nargs="*", default=[], help="extra video ids")
    args = ap.parse_args()

    refs = video_ids_from_kb()
    vids = sorted(set(refs) | set(args.extra))
    print(f"videos to process: {len(vids)}")

    known = {json.loads(l)["doc_id"] for l in open(MANIFEST)}
    today = date.today().isoformat()
    docs, index, failures = [], [], []
    seen_transcripts: dict[str, str] = {}

    for vid in vids:
        title = title_of(vid)
        text, err = transcript_of(vid)
        url = f"https://www.youtube.com/watch?v={vid}"
        entry = {"video_id": vid, "url": url, "title": title,
                 "referenced_by": refs.get(vid, []), "transcript_chars": len(text)}
        if err or not text:
            entry["error"] = err or "empty transcript"
            failures.append(entry)
            index.append(entry)
            print(f"  FAIL {vid} {title[:60]}: {entry['error'][:80]}")
            continue
        # ORA has uploaded the same seminar twice under different ids -- measured:
        # qD6Efajaq5M and zoDvRuwD0l4 are byte-identical 45,409-char transcripts
        # titled "ORA Subawards: What You Need to Know" and "ORA Subawards What
        # You Need to Know". Emitting both would put two copies of the same
        # narration into a KB that is otherwise deliberately non-redundant, and
        # they would compete with each other for every subaward question.
        digest = __import__("hashlib").sha256(" ".join(text.split()).encode()).hexdigest()
        if digest in seen_transcripts:
            entry["duplicate_of"] = seen_transcripts[digest]
            index.append(entry)
            print(f"  dup  {vid} identical to {seen_transcripts[digest]} — skipped")
            continue
        seen_transcripts[digest] = vid

        index.append(entry)
        parts = split(text)
        print(f"  ok   {vid} {len(text):7d} chars -> {len(parts):2d} docs  {title[:55]}")

        for n, body in enumerate(parts, 1):
            doc_id = f"video_{slug(title) or vid}"
            if len(parts) > 1:
                doc_id += f"_{n:02d}"
            if doc_id in known:
                doc_id = f"video_{vid}_{n:02d}"
            known.add(doc_id)
            head = (
                f"{title}"
                + (f" — part {n} of {len(parts)}" if len(parts) > 1 else "")
                + "\n\nORA video recording. Watch: " + url + "\n"
                "This is an AUTO-GENERATED transcript of the spoken narration, not an "
                "authored ORA document. Speech recognition can mis-hear names, dollar "
                "amounts and dates — check the recording before relying on a specific "
                "figure.\n\n"
            )
            docs.append({
                "doc_id": doc_id,
                "title": title + (f" — part {n} of {len(parts)}" if len(parts) > 1 else ""),
                "category": "trainings", "subcategory": "video_recordings",
                "kb_path": "trainings/monthly_d_red_seminars",
                "source_url": url, "procedure_url": url,
                "content": head + body, "last_scraped": today,
                "video_id": vid, "transcript": True,
            })

    print(f"\ntranscribed {len(vids) - len(failures)}/{len(vids)} videos -> "
          f"{len(docs)} documents, {sum(len(d['content']) for d in docs):,} chars")
    if failures:
        print(f"failures: {len(failures)}")

    if args.dry_run:
        print("[dry-run] nothing written")
        return 0

    json.dump({"generated_at": today, "videos": index}, open(VIDEOS_JSON, "w"), indent=1)
    json.dump({"generated_at": today, "documents": docs}, open(PAYLOAD, "w"), indent=1)
    print(f"wrote {VIDEOS_JSON}\nwrote {PAYLOAD}")

    if args.push:
        sys.path.insert(0, os.path.join(REPO, "backend"))
        import datastore_manager as dm
        ok = fail = 0
        for d in docs:
            try:
                res = dm.create_kb_document(
                    doc_id=d["doc_id"], title=d["title"], content=d["content"],
                    kb_path=d["kb_path"], source_url=d["source_url"],
                    procedure_url=d["procedure_url"])
                ok += 1 if res.get("success") else 0
                if not res.get("success"):
                    fail += 1
                    print(f"  PUSH FAIL {d['doc_id']}: {res.get('message')}")
            except Exception as e:
                fail += 1
                print(f"  PUSH FAIL {d['doc_id']}: {str(e)[:140]}")
        print(f"pushed: ok={ok} fail={fail}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
