#!/usr/bin/env python3
"""
Per-doc KB coverage check.

For EVERY KB doc (backend/kb_structured), this:
  1. generates ONE realistic faculty question answerable from that doc (+ gold facts),
  2. asks the live ORA Navigator backend (/chat/guest),
  3. has a Gemini judge grade the bot's answer against the doc content.

Output:
  - per_doc_results.jsonl   (one line per doc, resumable -- reruns skip graded docs)
  - per_doc_report.md       (ranked: WRONG/FABRICATED first, then PARTIAL)

Usage:
  ADC must be set (gcloud auth application-default login).
  .venv/bin/python eval/per_doc_coverage.py --limit 10          # pilot
  .venv/bin/python eval/per_doc_coverage.py                     # full run
  .venv/bin/python eval/per_doc_coverage.py --report-only       # just rebuild the report
"""
import argparse
import json
import os
import sys
import time
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

# --- config -----------------------------------------------------------------
REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
KB_DIR = os.path.join(REPO, "backend", "kb_structured")
MANIFEST = os.path.join(KB_DIR, "_all_documents.jsonl")
EVAL_DIR = os.path.dirname(os.path.abspath(__file__))
RESULTS = os.path.join(EVAL_DIR, "per_doc_results.jsonl")
REPORT = os.path.join(EVAL_DIR, "per_doc_report.md")

BACKEND_URL = os.environ.get(
    "COVERAGE_BACKEND_URL",
    "https://oranavigator-backend-ollhkgeova-uc.a.run.app",
).rstrip("/")
GUEST_ENDPOINT = BACKEND_URL + "/chat/guest"
MODEL = "gemini-2.5-flash"
WORKERS = int(os.environ.get("COVERAGE_WORKERS", "5"))

PROJECT = os.environ.get("GOOGLE_CLOUD_PROJECT", "infra-vertex-494621-v1")
LOCATION = os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1")

_print_lock = threading.Lock()
_write_lock = threading.Lock()


def log(msg):
    with _print_lock:
        print(msg, flush=True)


# --- Gemini client (lazy, thread-safe enough for our use) -------------------
def make_client():
    os.environ["GOOGLE_GENAI_USE_VERTEXAI"] = "TRUE"
    from google import genai
    return genai.Client(vertexai=True, project=PROJECT, location=LOCATION)


_client = None


def gemini(prompt, temperature=0.2):
    global _client
    if _client is None:
        _client = make_client()
    from google.genai import types
    r = _client.models.generate_content(
        model=MODEL,
        contents=prompt,
        config=types.GenerateContentConfig(temperature=temperature),
    )
    return (r.text or "").strip()


def gemini_json(prompt, temperature=0.2, retries=2):
    """Call Gemini and parse a JSON object out of the reply."""
    last = ""
    for _ in range(retries + 1):
        txt = gemini(prompt, temperature)
        last = txt
        # strip markdown fences
        if "```" in txt:
            txt = txt.split("```")[1]
            if txt.startswith("json"):
                txt = txt[4:]
        txt = txt.strip()
        s, e = txt.find("{"), txt.rfind("}")
        if s != -1 and e != -1:
            try:
                return json.loads(txt[s:e + 1])
            except Exception:
                pass
    raise ValueError(f"could not parse JSON from: {last[:200]}")


# --- doc loading ------------------------------------------------------------
def load_docs():
    docs = []
    with open(MANIFEST) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            m = json.loads(line)
            fp = os.path.join(KB_DIR, m["file_path"])
            try:
                full = json.load(open(fp))
            except Exception:
                full = {}
            content = full.get("content") or ""
            # fold a few structured fields into the gold text for richer grading
            extras = {k: v for k, v in full.items()
                      if k not in ("content", "doc_id", "title", "category",
                                   "subcategory", "display_label", "procedure_url",
                                   "source_url", "file_path")}
            gold = content
            if extras:
                gold += "\n\nStructured fields:\n" + json.dumps(extras, indent=1)[:3000]
            docs.append({
                "doc_id": m["doc_id"],
                "title": m.get("title", ""),
                "category": m.get("category", ""),
                "page_status": full.get("page_status", ""),
                "gold": gold.strip(),
            })
    return docs


# --- the three steps per doc ------------------------------------------------
GEN_PROMPT = """You are building a QA test for a university research-administration chatbot.
Below is ONE knowledge-base document. Write ONE natural question a faculty member or
research administrator would realistically ask, whose answer is contained in THIS document.

Rules:
- The question must be answerable from this document alone.
- Prefer a concrete, factual question (a process, a contact, a number, a deadline, a policy).
- If the document is a STUB / "coming soon" / not-yet-populated page, set "is_stub": true and
  ask a question about that topic anyway (the bot SHOULD say it's not yet published).
- Also list the 1-4 key gold facts the correct answer must contain.

Return ONLY JSON:
{{"question": "...", "gold_facts": ["...", "..."], "is_stub": true/false}}

DOCUMENT TITLE: {title}
CATEGORY: {category}
DOCUMENT CONTENT:
{gold}
"""

GRADE_PROMPT = """You are grading whether a research-administration chatbot answered a question
correctly. IMPORTANT CONTEXT: the chatbot answers from a LARGE 382-document knowledge base,
but you are shown only ONE source document. So the answer will often contain additional, correct
ORA facts (other staff, contacts, forms, compliance programs, emails, phone numbers) that are NOT
in this one document. That is EXPECTED and is NOT fabrication. Only judge against THIS document
for (a) the gold facts and (b) direct contradictions.

Grade on two things only:
  1. Does the answer actually answer the question, and are the GOLD FACTS present (not missing)?
  2. Does the answer CONTRADICT anything stated in this source document?

Verdicts (pick exactly one):
- CORRECT: it answers the question, the gold facts are present, and nothing contradicts the document.
  (Extra correct-sounding ORA context, contacts, or detail is FINE -- do not penalize it.)
- PARTIAL: it answers, but a key gold fact is missing or too vague. No contradiction.
- WRONG: it does not answer the question, answers a different thing, or CONTRADICTS the document
  (e.g. states a different title/number/date than the document says).
- FABRICATED: reserve ONLY for a specific IDENTIFIER (phone, email, EIN, UEI, FWA, SOP number,
  policy number, dollar amount, or date) that DIRECTLY CONTRADICTS the document's value, or is
  clearly invented/implausible. Do NOT use FABRICATED for extra job responsibilities, general
  descriptions, or facts that could plausibly come from another KB document.
- ABSTAINED_OK: the document is a stub/unpublished AND the bot correctly said it's not available /
  routed to ORA. (PASS for stubs.)

When unsure whether extra info is invented vs. drawn from elsewhere in the KB, do NOT flag it --
only flag CLEAR contradictions of THIS document or a missing gold fact.

Return ONLY JSON: {{"verdict": "...", "reason": "one short sentence citing the specific contradiction or missing gold fact, if any"}}

QUESTION: {question}
GOLD FACTS: {gold_facts}
IS_STUB: {is_stub}

SOURCE DOCUMENT:
{gold}

CHATBOT ANSWER:
{answer}
"""


def ask_backend(question, retries=4):
    for attempt in range(retries + 1):
        try:
            r = requests.post(GUEST_ENDPOINT, json={"query": question}, timeout=120)
            if r.status_code == 429:
                time.sleep(5 * (attempt + 1))
                continue
            r.raise_for_status()
            d = r.json()
            return d.get("response") or d.get("answer") or d.get("message") or ""
        except Exception as e:
            if attempt == retries:
                return f"__ASK_ERROR__: {e}"
            time.sleep(3 * (attempt + 1))
    return "__ASK_ERROR__: exhausted retries"


def process(doc):
    did = doc["doc_id"]
    try:
        gen = gemini_json(GEN_PROMPT.format(
            title=doc["title"], category=doc["category"], gold=doc["gold"][:8000]))
        question = gen.get("question", "").strip()
        gold_facts = gen.get("gold_facts", [])
        is_stub = bool(gen.get("is_stub")) or str(doc.get("page_status", "")).startswith("stub")
        if not question:
            raise ValueError("empty generated question")

        answer = ask_backend(question)

        grade = gemini_json(GRADE_PROMPT.format(
            question=question, gold_facts=json.dumps(gold_facts, ensure_ascii=False),
            is_stub=is_stub, gold=doc["gold"][:8000], answer=answer[:6000]))
        verdict = grade.get("verdict", "WRONG").upper()
        reason = grade.get("reason", "")

        rec = {"doc_id": did, "title": doc["title"], "category": doc["category"],
               "question": question, "is_stub": is_stub, "gold_facts": gold_facts,
               "answer": answer, "verdict": verdict, "reason": reason}
    except Exception as e:
        rec = {"doc_id": did, "title": doc["title"], "category": doc["category"],
               "question": "", "is_stub": False, "gold_facts": [], "answer": "",
               "verdict": "ERROR", "reason": f"{type(e).__name__}: {e}"}

    with _write_lock:
        with open(RESULTS, "a") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    log(f"  [{rec['verdict']:>12}] {did} :: {rec['reason'][:80]}")
    return rec


# --- report -----------------------------------------------------------------
def build_report():
    if not os.path.exists(RESULTS):
        log("no results yet")
        return
    recs = [json.loads(l) for l in open(RESULTS) if l.strip()]
    # dedup by doc_id, keep last
    by_id = {r["doc_id"]: r for r in recs}
    recs = list(by_id.values())
    order = {"WRONG": 0, "FABRICATED": 1, "PARTIAL": 2, "ERROR": 3,
             "ABSTAINED_OK": 4, "CORRECT": 5}
    recs.sort(key=lambda r: (order.get(r["verdict"], 9), r["category"], r["doc_id"]))
    counts = {}
    for r in recs:
        counts[r["verdict"]] = counts.get(r["verdict"], 0) + 1
    total = len(recs)
    passing = counts.get("CORRECT", 0) + counts.get("ABSTAINED_OK", 0)
    pct = (100.0 * passing / total) if total else 0.0

    lines = [f"# Per-doc KB coverage report",
             f"",
             f"**{passing}/{total} docs answered correctly ({pct:.1f}%)**  "
             f"(CORRECT + ABSTAINED_OK = pass)",
             f"",
             "| verdict | count |", "|---|---|"]
    for v in ["CORRECT", "ABSTAINED_OK", "PARTIAL", "WRONG", "FABRICATED", "ERROR"]:
        lines.append(f"| {v} | {counts.get(v,0)} |")
    lines.append("")
    lines.append("> Reminder: the judge is itself an LLM. Eyeball the WRONG/FABRICATED "
                 "rows — some will be grader false-negatives (the bot was right). Real "
                 "bugs are the ones to fix.")
    lines.append("")

    for bucket in ["WRONG", "FABRICATED", "PARTIAL", "ERROR"]:
        rows = [r for r in recs if r["verdict"] == bucket]
        if not rows:
            continue
        lines.append(f"## {bucket} ({len(rows)})\n")
        for r in rows:
            lines.append(f"### `{r['doc_id']}` — {r['title']}  _({r['category']})_")
            lines.append(f"- **Q:** {r['question']}")
            lines.append(f"- **Why flagged:** {r['reason']}")
            ans = (r['answer'] or '').replace('\n', ' ')[:400]
            lines.append(f"- **Bot said:** {ans}")
            if r.get("gold_facts"):
                lines.append(f"- **Gold facts:** {'; '.join(r['gold_facts'])}")
            lines.append("")
    open(REPORT, "w").write("\n".join(lines))
    log(f"\nWROTE {REPORT}  ({passing}/{total} = {pct:.1f}% pass)")


# --- main -------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="only process N docs (pilot)")
    ap.add_argument("--report-only", action="store_true")
    ap.add_argument("--fresh", action="store_true", help="ignore existing results")
    args = ap.parse_args()

    if args.report_only:
        build_report()
        return

    if args.fresh and os.path.exists(RESULTS):
        os.remove(RESULTS)

    done = set()
    if os.path.exists(RESULTS):
        for l in open(RESULTS):
            try:
                done.add(json.loads(l)["doc_id"])
            except Exception:
                pass

    docs = load_docs()
    todo = [d for d in docs if d["doc_id"] not in done]
    if args.limit:
        todo = todo[:args.limit]
    log(f"docs total={len(docs)} already_done={len(done)} this_run={len(todo)} "
        f"workers={WORKERS} backend={BACKEND_URL}")

    t0 = time.time()
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        futs = [ex.submit(process, d) for d in todo]
        for _ in as_completed(futs):
            pass
    log(f"done in {time.time()-t0:.0f}s")
    build_report()


if __name__ == "__main__":
    main()
