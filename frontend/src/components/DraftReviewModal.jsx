// DraftReviewModal.jsx
//
// Completeness of a draft against THIS proposal's own solicitation — whichever
// funder it came from. No solicitation is special-cased anywhere.
//
//   0. No solicitation stored -> the attach panel, NOT a review. Scoring a
//      draft against zero requirements would hand back a confident percentage
//      that means nothing at all.
//   1. PI uploads their proposal files (PDF / .docx / text — several at once,
//      because a real package IS several files), or pastes text instead.
//   2. POST /api/me/submissions/{id}/draft-review/upload  (multipart)
//      or   POST /api/me/submissions/{id}/draft-review    (pasted text)
//      -> backend loads the stored solicitation, locates the sections, runs the
//         rule-based checks, asks the model for grounded coverage, computes a
//         completeness score.
//   3. Findings render grouped by section, each with the verbatim solicitation
//      sentence behind it and (for anything found) a quote from the PI's text.
//
// A file that could not be read is shown as UNREADABLE in the extraction
// report, never silently dropped — otherwise its sections would appear in the
// findings as content the author omitted.
//
// TWO THINGS THE UI MUST NOT BLUR, both load-bearing:
//
//   * The score is COMPLETENESS against the solicitation, never a funding
//     prediction. Its caption says so, and it is absent (not zero) whenever the
//     backend withholds it.
//   * `could_not_locate` / `not_checked` are NOT failures. They render in a
//     neutral style and read as "we didn't assess this", because telling a PI
//     their sustainability plan is missing when we merely failed to find the
//     section would send them rewriting something they already wrote.

import React, { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import {
  AlertTriangle, Check, ChevronDown, ChevronRight, ExternalLink, FileText,
  HelpCircle, Info, Lightbulb, MinusCircle, Quote, Trash2, Upload, X, XCircle,
} from "lucide-react";
import { getApiBase } from "../lib/apiBase";
import { timeAgo } from "../lib/timeAgo";
import "./DraftReviewModal.css";

const API_BASE = getApiBase();

function authHeaders() {
  const token = localStorage.getItem("token");
  return token ? { Authorization: `Bearer ${token}` } : {};
}

// Status vocabulary mirrors services/draft_review.py exactly. `neutral: true`
// marks the "we did not assess this" states so they can never be styled or
// counted as failures.
const STATUS_META = {
  // "Addressed" read to an author as "this is good". It only ever meant "the
  // rule is satisfied" — most of these rules are about PRESENCE — and a 76-word
  // Project Summary carrying six of them was reported as fine. Same rename and
  // the same reason as "Not checked" -> "Not ours to check".
  addressed:        { label: "Meets the rule", Icon: Check,       cls: "eir-ok" },
  partial:          { label: "Partial",      Icon: AlertTriangle, cls: "eir-warn" },
  not_found:        { label: "Not found",    Icon: XCircle,       cls: "eir-fail" },
  clear:            { label: "Clear",        Icon: Check,         cls: "eir-ok" },
  flagged:          { label: "Flagged",      Icon: XCircle,       cls: "eir-fail" },
  could_not_locate: { label: "Not located",  Icon: HelpCircle,    cls: "eir-skip", neutral: true },
  not_checked:      { label: "Not checked",  Icon: MinusCircle,   cls: "eir-skip", neutral: true },
  unclear:          { label: "Unassessed",   Icon: MinusCircle,   cls: "eir-skip", neutral: true },
  // The rule lives in a rulebook this app never read, so nothing about it was
  // verified. Neutral like the other "we did not assess this" states — but it
  // gets its OWN word, because "Not checked" reads as an omission on our side
  // that a re-run might fix, and this one never will.
  delegated:        { label: "Not ours to check", Icon: ExternalLink, cls: "eir-skip", neutral: true },
  // A real requirement that no DOCUMENT can satisfy — a portal click, who signs
  // the submission, how many proposals a PI may hold. Measured: 7 of 29 assessed
  // rows on a real proposal, every one scored `not_found` and listed under "Fix
  // these first", none fixable by writing. Neutral, out of the score, and its
  // note names the tool that DOES handle it.
  not_in_draft:     { label: "Done at submission", Icon: MinusCircle, cls: "eir-skip", neutral: true },
};

// Section labels come from the RESULT, not a table here: the sections are
// whatever this solicitation names, so any hardcoded map would be wrong for
// every funder but the one it was written for.
function sectionLabels(result) {
  const out = {};
  for (const s of result?.sections_located || []) out[s.key] = s.label;
  for (const s of result?.sections_missing || []) out[s.key] = s.label;
  return out;
}

function titleCase(key) {
  return String(key || "").split("_").filter(Boolean)
    .map((w) => w.charAt(0).toUpperCase() + w.slice(1)).join(" ");
}

function StatusChip({ status }) {
  const meta = STATUS_META[status] || STATUS_META.unclear;
  const { Icon } = meta;
  return (
    <span className={`eir-chip ${meta.cls}`}>
      <Icon size={12} /> {meta.label}
    </span>
  );
}

const ACCEPT = ".pdf,.docx,.txt,.md,.markdown,.text,.rst,.csv,.tex";
const MAX_FILES = 12;

function humanSize(bytes) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export default function DraftReviewModal({ submission, onClose, onAttach, onRefresh }) {
  const [mode, setMode] = useState("upload");  // upload | paste
  const [files, setFiles] = useState([]);
  const [draft, setDraft] = useState("");
  const [step, setStep] = useState("input");   // input | running | results
  const [result, setResult] = useState(null);
  const [extraction, setExtraction] = useState(null);
  const [error, setError] = useState("");
  // A review saved earlier. Offered on the input step so the PI can reopen it
  // instead of pasting and waiting again — the whole point of saving.
  const [savedAt, setSavedAt] = useState(submission?.draft_review_saved_at || null);
  const [loadingSaved, setLoadingSaved] = useState(false);

  async function openSaved() {
    setLoadingSaved(true);
    setError("");
    try {
      const res = await fetch(
        `${API_BASE}/api/me/submissions/${submission.id}/draft-review/saved`,
        { headers: authHeaders() },
      );
      if (!res.ok) throw new Error("That saved review is no longer available.");
      const body = await res.json();
      setResult(body.result);
      setExtraction(body.extraction || null);
      setSavedAt(body.saved_at || savedAt);
      setStep("results");
    } catch (e) {
      setError(e.message || "Couldn't open the saved review.");
    } finally {
      setLoadingSaved(false);
    }
  }

  const words = draft.trim() ? draft.trim().split(/\s+/).length : 0;
  const canRun = mode === "upload" ? files.length > 0 : Boolean(draft.trim());

  function addFiles(incoming) {
    const list = Array.from(incoming || []);
    if (!list.length) return;
    setError("");
    setFiles((prev) => {
      // De-dupe by name+size so dropping the same file twice doesn't send it twice.
      const seen = new Set(prev.map((f) => `${f.name}:${f.size}`));
      const merged = [...prev];
      for (const f of list) {
        const key = `${f.name}:${f.size}`;
        if (!seen.has(key)) { seen.add(key); merged.push(f); }
      }
      if (merged.length > MAX_FILES) {
        setError(`You can upload at most ${MAX_FILES} files.`);
        return merged.slice(0, MAX_FILES);
      }
      return merged;
    });
  }

  async function run() {
    setStep("running");
    setError("");
    setExtraction(null);
    try {
      let res;
      if (mode === "upload") {
        const form = new FormData();
        files.forEach((f) => form.append("files", f, f.name));
        res = await fetch(
          `${API_BASE}/api/me/submissions/${submission.id}/draft-review/upload`,
          { method: "POST", headers: { ...authHeaders() }, body: form }
        );
      } else {
        res = await fetch(
          `${API_BASE}/api/me/submissions/${submission.id}/draft-review`,
          {
            method: "POST",
            headers: { "Content-Type": "application/json", ...authHeaders() },
            body: JSON.stringify({ draft_text: draft }),
          }
        );
      }
      if (!res.ok) {
        let detail = `Review failed (${res.status})`;
        try {
          const j = await res.json();
          if (j.detail) detail = j.detail;
        } catch { /* non-JSON error body — keep the status message */ }
        throw new Error(detail);
      }
      const body = await res.json();
      setExtraction(body.extraction || null);
      // Nothing readable: the backend deliberately returns no review rather than
      // one that would report every requirement as missing.
      if (!body.result) {
        setError(body.error || "Couldn't read any text from those files.");
        setStep("input");
        return;
      }
      setResult(body.result);
      setStep("results");
    } catch (e) {
      setError(e.message || "Something went wrong.");
      setStep("input");
    }
  }

  const summary = submission?.solicitation_summary || null;
  const hasSolicitation = Boolean(submission?.has_solicitation_requirements);
  // This proposal WAS set up from a solicitation — its notes carry the funder's
  // numbers — but predates requirement extraction. It is a different situation
  // from a hand-made proposal, and gets different words below.
  const priorSolicitation = !hasSolicitation
    && /^(Budget cap|Page limits|Required attachments|Program ID):/m.test(submission?.notes || "");
  // The document itself is on file, so the requirements can be read again
  // without asking for it. This is what storing the text buys.
  const canReread = Boolean(submission?.has_solicitation_source);
  const [rereading, setRereading] = useState(false);
  // A document this PI read but never attached to anything. Without this the
  // panel below told them "the document's text was never stored, so it has to
  // be read once" while we were holding a copy — false, and it sent them off to
  // find a file they had already handed over. Only fetched when this proposal
  // has no document of its own; an empty list changes nothing.
  const [orphan, setOrphan] = useState(null);
  useEffect(() => {
    if (canReread || hasSolicitation) return undefined;
    let live = true;
    fetch(`${API_BASE}/api/me/solicitation-sources/unbound`, { headers: authHeaders() })
      .then((r) => (r.ok ? r.json() : { sources: [] }))
      .then((d) => { if (live) setOrphan((d.sources || [])[0] || null); })
      .catch(() => {});
    return () => { live = false; };
  }, [canReread, hasSolicitation]);

  async function reread() {
    setRereading(true);
    setError("");
    try {
      const res = await fetch(
        `${API_BASE}/api/me/submissions/${submission.id}/solicitation/reread`,
        { method: "POST", headers: authHeaders() },
      );
      if (!res.ok) {
        const b = await res.json().catch(() => ({}));
        throw new Error(b.detail || `Re-read failed (${res.status})`);
      }
      const body = await res.json();
      const save = await fetch(
        `${API_BASE}/api/me/submissions/${submission.id}/solicitation`,
        {
          method: "PUT",
          headers: { "Content-Type": "application/json", ...authHeaders() },
          body: JSON.stringify({
            extracted: body.contract || {},
            requirements: body.requirements,
            merit_criteria: body.merit_criteria,
            eligibility_notes: body.eligibility_notes,
            read_report: body.read_report,
            extraction: body.extraction,
            source_id: body.source_id,
          }),
        },
      );
      if (!save.ok) {
        const b = await save.json().catch(() => ({}));
        throw new Error(b.detail || `Couldn't save the requirements (${save.status})`);
      }
      onRefresh?.();
    } catch (e) {
      setError(e.message || "Couldn't re-read the solicitation.");
    } finally {
      setRereading(false);
    }
  }

  return createPortal(
    <div className="eir-overlay" onClick={onClose}>
      <div className="eir-modal" onClick={(e) => e.stopPropagation()}>
        <header className="eir-header">
          <div>
            <h2>Draft Review</h2>
            <div className="eir-sub">
              {summary
                ? <>Completeness against <b>{summary.id}</b>
                    {summary.title ? ` — ${summary.title}` : ""}</>
                : "Checked against the solicitation attached to this proposal"}
            </div>
          </div>
          <button className="eir-close" onClick={onClose} aria-label="Close">
            <X size={18} />
          </button>
        </header>

        {/* No solicitation, no review. Scoring a draft against zero
            requirements would produce a confident percentage that means
            nothing, so this asks for the solicitation instead. */}
        {!hasSolicitation && (
          <div className="eir-attach-first">
            {/* A proposal STARTED from a solicitation still lands here, and
                telling that PI to "attach the solicitation" would read as though
                the app lost what they already gave it. It didn't: it kept a
                summary (cap, page limits, attachments) and never kept the
                document's text, so the requirement list has to be read from the
                document once. Say that, rather than implying they skipped a step. */}
            {canReread ? (
              <>
                <h3>Read this proposal's solicitation for its requirements</h3>
                <p>
                  The solicitation is already on file — we just have not read out
                  its requirement list yet. No upload needed; this takes about a
                  minute and every requirement it states, quoted, becomes a check
                  on your draft.
                </p>
              </>
            ) : orphan ? (
              <>
                <h3>Use the solicitation you already uploaded?</h3>
                <p>
                  You read <b>{orphan.filename || orphan.url || "a solicitation"}</b>{" "}
                  {timeAgo(orphan.created_at)} but never attached it to a proposal,
                  so we still have it — {orphan.chars?.toLocaleString()} characters.
                  No need to find the file again.
                </p>
                <p>
                  Attach it here and every requirement in it, quoted, becomes a
                  check on your draft. If it belongs to a different proposal,
                  upload the right one instead.
                </p>
              </>
            ) : priorSolicitation ? (
              <>
                <h3>This proposal needs its solicitation read in full</h3>
                <p>
                  We have this funder's <b>numbers</b> from when you set the
                  proposal up — budget cap, page limits, required attachments —
                  but not the list of everything the solicitation actually asks
                  you to include. Reading requirements is new, and the document's
                  text was never stored, so it has to be read once.
                </p>
                <p>
                  Upload the same solicitation again and every requirement in it,
                  quoted, becomes a check on your draft. Proposals you start from
                  now on will already have this.
                </p>
              </>
            ) : (
              <>
                <h3>Attach this proposal's solicitation first</h3>
                <p>
                  This review checks your draft against the funder's own
                  requirements — every one it states, quoted from the document.
                  Once the solicitation is attached, each requirement becomes a
                  check.
                </p>
              </>
            )}
            {error && <div className="eir-error">{error}</div>}
            <div className="eir-attach-actions">
              {canReread ? (
                <button className="eir-run" onClick={reread} disabled={rereading}>
                  {rereading ? "Reading the solicitation…" : "Read its requirements"}
                </button>
              ) : onAttach && (
                <>
                  <button className="eir-run" onClick={() => onAttach(orphan?.id ?? null)}>
                    {orphan ? "Use that document"
                      : priorSolicitation ? "Read the solicitation"
                      : "Attach the solicitation"}
                  </button>
                  {orphan && (
                    <button className="eir-secondary" onClick={() => onAttach(null)}>
                      Upload a different one
                    </button>
                  )}
                </>
              )}
              <button className="eir-secondary" onClick={onClose}>Not now</button>
            </div>
          </div>
        )}

        {hasSolicitation && summary && <ReadReport summary={summary} />}

        {hasSolicitation && step === "input" && savedAt && (
          <div className="eir-saved-banner">
            <FileText size={13} />
            <span>
              You saved a review of this proposal {timeAgo(savedAt)}.
            </span>
            <button className="eir-secondary" onClick={openSaved} disabled={loadingSaved}>
              {loadingSaved ? "Opening…" : "Open it"}
            </button>
          </div>
        )}

        {hasSolicitation && step === "input" && (
          <InputView
            mode={mode} setMode={setMode}
            files={files} addFiles={addFiles}
            removeFile={(i) => setFiles((p) => p.filter((_, n) => n !== i))}
            draft={draft} setDraft={setDraft} words={words}
            error={error} extraction={extraction}
            canRun={canRun} onRun={run}
          />
        )}

        {hasSolicitation && step === "running" && (
          <div className="eir-loading">
            <div className="eir-spinner" />
            <p>
              {mode === "upload"
                ? "Reading your files, then checking each requirement…"
                : "Finding your sections, then checking each requirement…"}
            </p>
            <p className="eir-loading-hint">
              This reads the whole proposal, so it takes about 20 seconds.
            </p>
          </div>
        )}

        {hasSolicitation && step === "results" && result && (
          <ResultsView
            result={result}
            extraction={extraction}
            submission={submission}
            savedAt={savedAt}
            onSaved={(at) => { setSavedAt(at); onRefresh?.(); }}
            onBack={() => setStep("input")}
          />
        )}
      </div>
    </div>,
    document.body
  );
}

// How well the solicitation could be read, shown ONLY when it is not clean.
// A silent partial read is how "few requirements" gets mistaken for "few
// requirements exist".
function ReadReport({ summary }) {
  const rr = summary.read_report || {};
  const ex = summary.extraction || {};
  const problems = [];
  if (rr.pages_without_text) {
    problems.push(
      `${rr.pages_without_text} of ${rr.pages} pages had no extractable text, so ` +
      "requirements on those pages were not read.");
  }
  if (ex.hit_time_cap) problems.push("Reading ran out of time, so the list may be incomplete.");
  if (ex.hit_round_cap) problems.push("Reading stopped at its round limit, so the list may be incomplete.");
  if (!problems.length) return null;
  return (
    <div className="eir-read-report">
      <AlertTriangle size={13} />
      <div>{problems.map((p, i) => <p key={i}>{p}</p>)}</div>
    </div>
  );
}

// Drag-and-drop + file picker. Filenames matter downstream: the backend uses
// each one as a section heading, so "Letter of Institutional Support.pdf"
// locates a section that is otherwise easy to miss.
function DropZone({ files, addFiles, removeFile }) {
  const [over, setOver] = useState(false);
  const inputRef = useRef(null);

  return (
    <>
      <div
        className={`eir-drop${over ? " is-over" : ""}`}
        onDragOver={(e) => { e.preventDefault(); setOver(true); }}
        onDragLeave={() => setOver(false)}
        onDrop={(e) => {
          e.preventDefault();
          setOver(false);
          addFiles(e.dataTransfer.files);
        }}
        onClick={() => inputRef.current?.click()}
        role="button"
        tabIndex={0}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            inputRef.current?.click();
          }
        }}
      >
        <Upload size={22} />
        <div className="eir-drop-main">
          Drop your proposal files here, or <span>browse</span>
        </div>
        <div className="eir-drop-hint">
          PDF, Word (.docx), or plain text &middot; several files at once
        </div>
        <input
          ref={inputRef}
          type="file"
          multiple
          accept={ACCEPT}
          className="eir-file-input"
          onChange={(e) => { addFiles(e.target.files); e.target.value = ""; }}
        />
      </div>

      {files.length > 0 && (
        <ul className="eir-filelist">
          {files.map((f, i) => (
            <li key={`${f.name}:${f.size}`} className="eir-fileitem">
              <FileText size={14} />
              <span className="eir-filename">{f.name}</span>
              <span className="eir-filesize">{humanSize(f.size)}</span>
              <button
                className="eir-fileremove"
                onClick={(e) => { e.stopPropagation(); removeFile(i); }}
                aria-label={`Remove ${f.name}`}
              >
                <Trash2 size={13} />
              </button>
            </li>
          ))}
        </ul>
      )}
    </>
  );
}

// Per-file read report. Shown after a failed run so an unreadable file is
// visible as unreadable — never mistaken for content the author left out.
function ExtractionReport({ extraction }) {
  if (!extraction?.files?.length) return null;
  return (
    <div className="eir-extract">
      <div className="eir-extract-title">What was read</div>
      {extraction.files.map((f) => (
        <div key={f.filename} className={`eir-extract-row${f.error ? " is-bad" : ""}`}>
          {f.error ? <XCircle size={13} /> : <Check size={13} />}
          <span className="eir-filename">{f.filename}</span>
          <span className="eir-extract-detail">
            {f.error
              ? f.error
              : `${f.chars.toLocaleString()} characters${f.pages ? ` · ${f.pages} pages` : ""}`}
          </span>
        </div>
      ))}
      {extraction.files.some((f) => f.truncated) && (
        <div className="eir-extract-warn">
          <AlertTriangle size={13} /> A file was too long to read in full. The
          sections near its end were not checked &mdash; split it and re-run.
        </div>
      )}
    </div>
  );
}

function InputView({
  mode, setMode, files, addFiles, removeFile,
  draft, setDraft, words, error, extraction, canRun, onRun,
}) {
  return (
    <div className="eir-input-view">
      {/* BOTH shapes, said plainly. This read as though a multi-file package
          were required — "upload them together", then a note about filenames
          telling your sections apart — so a PI holding one combined PDF, which
          is what most people have, could reasonably think they had to split it
          up first. One file works: `document_text.combine` emits the filename
          as a heading and the locate stage then segments on the headings
          INSIDE the text, which is where they are in a single document. */}
      <p className="eir-intro">
        Upload your whole proposal &mdash; one combined PDF, or the separate
        files if that is how you keep it. Either works. Whatever you give it,
        the reviewer reads the lot and checks the package as a whole, so include
        the letters, the budget justification and every required attachment.
      </p>

      <div className="eir-modes" role="tablist">
        <button
          role="tab" aria-selected={mode === "upload"}
          className={`eir-mode${mode === "upload" ? " is-active" : ""}`}
          onClick={() => setMode("upload")}
        >
          Upload files
        </button>
        <button
          role="tab" aria-selected={mode === "paste"}
          className={`eir-mode${mode === "paste" ? " is-active" : ""}`}
          onClick={() => setMode("paste")}
        >
          Paste text
        </button>
      </div>

      {mode === "upload" ? (
        <DropZone files={files} addFiles={addFiles} removeFile={removeFile} />
      ) : (
        <textarea
          className="eir-textarea"
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          placeholder={
            "Your Proposal Title\n\n" +
            "Project Summary\n...\n\n" +
            "Project Description\n...\n\n" +
            "Broader Impacts\n...\n\n" +
            "Budget Justification\n..."
          }
          spellCheck={false}
        />
      )}

      {error && <div className="eir-errorbox"><AlertTriangle size={14} /> {error}</div>}

      <ExtractionReport extraction={extraction} />

      <div className="eir-input-footer">
        <span className="eir-wordcount">
          {mode === "upload"
            ? `${files.length} ${files.length === 1 ? "file" : "files"} selected`
            : `${words.toLocaleString()} ${words === 1 ? "word" : "words"}`}
        </span>
        <button className="eir-run" onClick={onRun} disabled={!canRun}>
          Review draft
        </button>
      </div>

      <p className="eir-privacy">
        Your files are read in memory and discarded &mdash; nothing is saved or
        stored.
      </p>
    </div>
  );
}

// The completeness figure. Rendered ONLY when the backend supplies one; when the
// AI layer is down the backend sends null and we show its message instead of a
// misleadingly low number.
function ScorePanel({ score, solicitationId }) {
  if (!score) return null;
  return (
    <div className={`eir-score eir-score-${score.band}`}>
      <div className="eir-score-num">
        {score.percent}<span className="eir-score-pct">%</span>
      </div>
      <div className="eir-score-body">
        <div className="eir-score-title">
          Completeness against {solicitationId || "this solicitation"}
        </div>
        <div className="eir-score-basis">{score.basis}</div>
        <div className="eir-score-bar">
          <div className="eir-score-fill" style={{ width: `${score.percent}%` }} />
        </div>
        {/* WHICH AUTHORITY the draft is failing. A proposal is judged against
            NSF's standing rulebook and this solicitation's own asks at once,
            and they are not interchangeable: missing every solicitation rule
            while meeting every PAPPG rule gives the same combined number as
            the reverse, and only the first gets returned without review.
            Rendered only when there is more than one source to name. */}
        {Object.keys(score.by_source || {}).length > 1 && (
          <div className="eir-score-split">
            {Object.entries(score.by_source).map(([name, s]) => (
              <div className="eir-score-src" key={name}>
                <span className="eir-score-src-name">{name}</span>
                <span className="eir-score-src-num">{s.percent}%</span>
                <span className="eir-score-src-of">{s.earned} of {s.assessed}</span>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

// WHERE each section was found — a locate result, never a verdict.
//
// This carried a ✓ in the same green as an "Addressed" finding, so an 85-word
// Project Summary appeared under a green tick and read as approved. All that
// was ever measured here is that a heading by that name exists in the paste.
// The word count is kept and given prominence for the same reason the pill
// lost its tick: it is the one honest signal about size, and a PI can judge
// "85 words" for themselves where the tool cannot state a minimum the
// solicitation never sets.
function SectionMap({ located, missing }) {
  return (
    <div className="eir-sectionmap">
      <div className="eir-sectionmap-title">Where your sections were found</div>
      <p className="eir-sectionmap-lede">
        Found, not judged &mdash; the checks below do the judging.
      </p>
      <div className="eir-sectionmap-rows">
        {located.map((s) => (
          <span key={s.key} className="eir-pill eir-pill-found">
            <FileText size={11} /> {s.label}
            <span className="eir-pill-count">{s.word_count.toLocaleString()} words</span>
          </span>
        ))}
        {missing.map((s) => (
          <span key={s.key} className="eir-pill eir-pill-missing">
            <HelpCircle size={11} /> {s.label}
          </span>
        ))}
      </div>
      {missing.length > 0 && (
        <div className="eir-sectionmap-note">
          Greyed sections were not found in what you pasted. Their requirements
          are marked <em>Not located</em> below and left out of the score &mdash;
          not counted as missing content.
        </div>
      )}
    </div>
  );
}

function FindingRow({ f, solicitationId }) {
  const [open, setOpen] = useState(false);
  const meta = STATUS_META[f.status] || STATUS_META.unclear;
  return (
    <div className={`eir-finding ${meta.cls}${meta.neutral ? " is-neutral" : ""}`}>
      <div className="eir-finding-head">
        <span className="eir-finding-label">
          {f.label}
          {f.prohibition && <span className="eir-tag">prohibited</span>}
          {!f.scored && <span className="eir-tag eir-tag-soft">advisory</span>}
          {/* Same mark as SectionCheckModal's, and it must stay the same word:
              the two modals render the same findings and this repo already has
              an open note about them presenting the same thing differently. */}
          {f.borderline && (
            <span className="eir-tag eir-tag-soft" title="The three readers disagreed on this one — worth checking yourself.">borderline</span>
          )}
          {/* Shown on RIDERS too — rows that were genuinely checked but whose
              rule continues in a document we never read. Without it, a row
              reading "Addressed" hides that only part of it was verified. */}
          {f.delegated_to && (
            <span className="eir-tag eir-tag-defer">defers to {f.delegated_to}</span>
          )}
        </span>
        <StatusChip status={f.status} />
      </div>

      {f.note && <div className="eir-finding-note">{f.note}</div>}

      {/* On EVERY row, including the ones that pass. A passing row used to show
          praise, which tells an author nothing and makes "Meets the rule" read
          as "you are done here". */}
      {f.suggestion && (
        <div className="eir-suggestion">
          <Lightbulb size={12} /> <span>{f.suggestion}</span>
        </div>
      )}

      {f.evidence && (
        <div className="eir-evidence">
          <Quote size={12} /> <span>{f.evidence}</span>
        </div>
      )}

      <button className="eir-why-toggle" onClick={() => setOpen((o) => !o)}>
        {open ? "Hide" : "Why is this required?"}
      </button>
      {open && (
        <div className="eir-why">
          {f.why && <p className="eir-why-plain">{f.why}</p>}
          <blockquote className="eir-why-source">
            &ldquo;{f.solicitation_says}&rdquo;
            {solicitationId && <cite>{solicitationId}</cite>}
          </blockquote>
        </div>
      )}
    </div>
  );
}

// Mechanical errors: found by code, quoted from the draft, and NOT scored.
//
// Placed ABOVE the score on purpose. These are the cheapest things to fix and
// the most embarrassing to submit — a leftover "[insert name]" reads as an
// unfinished proposal however complete the draft is against the solicitation.
// They stay out of the percentage because a placeholder is not incompleteness,
// and because a number that already gets over-read should not absorb a
// different kind of problem.
function MistakesPanel({ mistakes }) {
  if (!mistakes?.length) return null;
  return (
    <div className="eir-mistakes">
      <div className="eir-mistakes-head">
        <AlertTriangle size={14} />
        {mistakes.length === 1
          ? "1 mistake to fix before you submit"
          : `${mistakes.length} mistakes to fix before you submit`}
      </div>
      <p className="eir-mistakes-lede">
        Found in your text by a rule, not a judgement &mdash; each one quotes
        where it is. These do not affect the completeness score.
      </p>
      {mistakes.map((m, i) => (
        <div key={`${m.kind}:${i}`} className="eir-mistake">
          <div className="eir-mistake-label">{m.label}</div>
          <div className="eir-mistake-detail">{m.detail}</div>
          {m.evidence && (
            <div className="eir-evidence">
              <Quote size={12} /> <span>{m.evidence}</span>
            </div>
          )}
        </div>
      ))}
    </div>
  );
}

// What this solicitation hands off to someone else's rulebook.
//
// It sits NEXT TO THE SCORE deliberately: the score is completeness against the
// requirements we could read, and this is the honest statement of what that
// excludes. A PI whose Project Summary requirement is one line long ("include
// the LOI number in addition to all the requirements outlined in the PAPPG")
// otherwise reads "Addressed" as "my summary is fine", when the rules that
// would fail it were never in the document we read.
function DelegationNotice({ books }) {
  if (!books?.length) return null;
  // The count is TOTALLED here rather than printed per rulebook. Printed per
  // rulebook it produced the identical sentence twice — "1 requirement in your
  // solicitation points here and could not be checked", once for the PAPPG and
  // once for Build America Buy America — which is the same say-it-twice problem
  // that crowded this panel before.
  const unchecked = books.reduce((n, b) => n + (b.unchecked || 0), 0);
  // `covered_sections` is PER RULEBOOK (services/delegated_rules.summarize).
  // The backend has named the parts it now checks since the baseline shipped
  // and nothing here read it, so this panel went on saying "this review has not
  // read them" beside fourteen checked PAPPG findings.
  const anyCovered = books.some((b) => b.covered_sections?.length);
  return (
    <div className="eir-delegation">
      <div className="eir-delegation-head">
        <ExternalLink size={13} />{" "}
        {anyCovered
          ? "Your solicitation says to follow these. This review checked part of them."
          : "Your solicitation says to follow these. This review has not read them."}
      </div>
      <ul className="eir-delegation-list">
        {books.map((b) => (
          <li key={b.name}>
            {b.url ? (
              <a className="eir-delegation-name" href={b.url}
                 target="_blank" rel="noreferrer">
                {b.name} <ExternalLink size={10} />
              </a>
            ) : (
              <span className="eir-delegation-name">{b.name}</span>
            )}
            {b.short && <span className="eir-delegation-short">, {b.short}</span>}
            {/* Named per rulebook, never once for all of them: we hold the
                PAPPG's rules for four sections and NOTHING of the Build
                America, Buy America Act, and one line covering both would claim
                coverage of a law nothing read. */}
            {b.covered_sections?.length > 0 && (
              <span className="eir-delegation-covered">
                Checked here against {b.name}: {b.covered_sections.join(", ")}.
                The rest of {b.name} was not read.
              </span>
            )}
          </li>
        ))}
      </ul>
      <p className="eir-delegation-foot">
        {/* WHY it could not be checked, not just that it wasn't. Asked directly
            — "why can't it be checked?" — and the honest answer is that the
            solicitation states a pointer rather than the rule, so there is no
            rule here to hold the draft against. */}
        {unchecked > 0 && (
          <>
            {unchecked} requirement{unchecked === 1 ? "" : "s"} in your
            solicitation say{unchecked === 1 ? "s" : ""} only &ldquo;follow{" "}
            {books.length === 1 ? "this" : "one of these"}&rdquo; &mdash; the
            rule itself is in that document, not in your solicitation, so there
            was nothing to check your draft against.{" "}
          </>
        )}
        {anyCovered
          ? `Read ${books.length === 1 ? "it" : "them"} yourself for anything not listed above.`
          : `Read ${books.length === 1 ? "it" : "them"} yourself before you submit.`}
      </p>
    </div>
  );
}

// The review as a file the PI can keep, print, or send to ORA. Built here from
// the same object on screen, so the copy can never disagree with what they read.
// Markdown because it opens anywhere and stays readable as plain text.
function reviewToMarkdown(result, submission) {
  const sol = result.solicitation || {};
  const L = [];
  L.push(`# Draft Review — ${submission?.title || "proposal"}`);
  L.push("");
  L.push(`Checked against: ${sol.id || "the attached solicitation"}` +
         (sol.title ? ` — ${sol.title}` : ""));
  L.push(`Reviewed: ${new Date().toLocaleString()}`);
  if (result.score) {
    L.push("");
    L.push(`## Completeness: ${result.score.percent}%`);
    L.push(result.score.basis);
  }
  if (result.mistakes?.length) {
    L.push("");
    L.push(`## Mistakes to fix (${result.mistakes.length})`);
    L.push("_Found by a rule, not a judgement. These do not affect the score._");
    for (const m of result.mistakes) {
      L.push("");
      L.push(`- **${m.label}** — ${m.detail}`);
      if (m.evidence) L.push(`  > ${m.evidence}`);
    }
  }
  if (result.delegated?.length) {
    // Same honesty as the on-screen notice: when part of a rulebook WAS
    // checked, this heading must not claim none of it was.
    const anyCovered = result.delegated.some((b) => b.covered_sections?.length);
    L.push("");
    L.push(anyCovered
      ? "## Rules from outside your solicitation"
      : "## Rules this review did not read");
    for (const b of result.delegated) {
      L.push(`- **${b.name}**${b.short ? `, ${b.short}` : ""}${b.url ? ` — ${b.url}` : ""}`);
      if (b.covered_sections?.length) {
        L.push(`  - Checked here against ${b.name}: ${b.covered_sections.join(", ")}. ` +
               `The rest of ${b.name} was not read.`);
      }
    }
  }
  const labels = sectionLabels(result);
  const groups = {};
  for (const f of result.findings || []) {
    if (f.status === "delegated") continue;
    (groups[f.section || "_whole"] ||= []).push(f);
  }
  L.push("");
  L.push("## Requirements");
  for (const [key, items] of Object.entries(groups)) {
    L.push("");
    L.push(`### ${key === "_whole" ? "Whole proposal" : (labels[key] || titleCase(key))}`);
    for (const f of items) {
      const meta = STATUS_META[f.status] || STATUS_META.unclear;
      L.push("");
      L.push(`**${f.label}** — ${meta.label}` +
             (f.delegated_to ? ` (defers to ${f.delegated_to})` : ""));
      if (f.note) L.push(f.note);
      if (f.evidence) L.push(`  > Your text: ${f.evidence}`);
      if (f.solicitation_says) L.push(`  > ${sol.id || "Solicitation"}: ${f.solicitation_says}`);
    }
  }
  if (result.eligibility_notes?.length) {
    L.push("");
    L.push("## Eligibility — check these yourself");
    for (const n of result.eligibility_notes) L.push(`- ${n}`);
  }
  L.push("");
  L.push("_Completeness against the solicitation. Not a judgement of the science, " +
         "and not a prediction of funding._");
  return L.join("\n");
}

/* ── one section, collapsible ────────────────────────────────────────────────
 *
 * WHY THIS IS NOT COSMETIC. Until 2026-08-17 this list rendered every finding
 * always-open, which was fine at 44 findings from four sections of curated
 * rules. Reading the PAPPG takes a real review to 209 findings across fourteen
 * sections, and CLAUDE.md already records what that does: "shipping either
 * extraction would take a review from 44 findings to ~640 and bury the four
 * rows that matter". The fix-list is the thing a PI acts on, and a wall of
 * satisfied rules pushes it off the screen.
 *
 * OPEN BY DEFAULT IFF THE SECTION HAS SOMETHING TO FIX. Not "first N open", not
 * "all closed" — the sections carrying a `not_found` or a `flagged` are exactly
 * the ones the PI has work in, so those stay open and everything else folds. A
 * section that is entirely addressed, entirely not-checked, or that could not be
 * located is real information and stays reachable in one click; it is just not
 * what you are here for.
 *
 * The count summary is on the CLOSED header for the same reason the section map
 * shows word counts: a fold that hides how much it hides is worse than no fold.
 */
function FindingGroup({ label, words, items, solicitationId, forced }) {
  const counts = items.reduce((acc, f) => {
    // A row the engine did not score is a CONDITIONAL ask — "if you request
    // consultants, detail them". It is not work the PI has to do, and counting
    // it as such is how a clean Budget Justification got a 14-item fix list of
    // which 10 were rules that did not apply to it. It still shows, in its
    // section, with its status; it just does not open the section or claim a
    // slot in "Fix these first".
    const advisory = f.scored === false;
    const bucket =
      (f.status === "not_found" || f.status === "flagged") && !advisory ? "fix"
      : f.status === "not_found" || f.status === "flagged" ? "maybe"
      : f.status === "partial" ? "partial"
      : f.status === "addressed" || f.status === "clear" ? "ok"
      : "skipped";
    acc[bucket] = (acc[bucket] || 0) + 1;
    return acc;
  }, {});
  const [open, setOpen] = useState((counts.fix || 0) > 0);
  // `forced` lets Expand all / Collapse all override, without losing the
  // per-section state the PI has set since.
  useEffect(() => { if (forced !== null) setOpen(forced); }, [forced]);

  const summary = [
    counts.fix ? `${counts.fix} to fix` : null,
    counts.partial ? `${counts.partial} partial` : null,
    counts.maybe ? `${counts.maybe} if applicable` : null,
    counts.ok ? `${counts.ok} addressed` : null,
    counts.skipped ? `${counts.skipped} not checked` : null,
  ].filter(Boolean).join(" · ");

  return (
    <section className={`eir-group${open ? "" : " eir-group-closed"}`}>
      <h3 className="eir-group-title">
        <button
          type="button"
          className="eir-group-toggle"
          aria-expanded={open}
          onClick={() => setOpen((v) => !v)}
        >
          {open ? <ChevronDown size={15} /> : <ChevronRight size={15} />}
          <span className="eir-group-name">{label}</span>
          {words != null && (
            <span className="eir-group-words">
              {words.toLocaleString()} {words === 1 ? "word" : "words"}
            </span>
          )}
          <span className="eir-group-summary">{summary}</span>
        </button>
      </h3>
      {open && items.map((f) => (
        <FindingRow key={f.id} f={f} solicitationId={solicitationId} />
      ))}
    </section>
  );
}



function ResultsView({ result, extraction, onBack, submission, onSaved, savedAt }) {
  const [saving, setSaving] = useState(false);
  const [saveMsg, setSaveMsg] = useState("");

  async function onSave() {
    setSaving(true);
    setSaveMsg("");
    try {
      const res = await fetch(
        `${API_BASE}/api/me/submissions/${submission.id}/draft-review/save`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json", ...authHeaders() },
          body: JSON.stringify({ result, extraction }),
        },
      );
      if (!res.ok) throw new Error(`Couldn't save (${res.status})`);
      const body = await res.json();
      onSaved?.(body.saved_at);
      setSaveMsg("Saved to this proposal — you can reopen it without re-running.");
    } catch (e) {
      setSaveMsg(e.message || "Couldn't save this review.");
    } finally {
      setSaving(false);
    }
  }

  // null = every section decides for itself (open iff it has something to fix);
  // true/false = the PI hit Expand all / Collapse all. Reset to null is
  // deliberate: after an override the per-section state is theirs to set.
  const [forced, setForced] = useState(null);
  const findings = result.findings || [];
  // Whatever solicitation this proposal actually came from. Read defensively:
  // the engine takes a profile now, and a profile with no id is a valid one.
  const sol = result.solicitation || {};
  const solId = sol.id || "";
  const labels = sectionLabels(result);
  // section key -> words located under it. Only located sections have one; a
  // section that was never found has no count, which is correct — there is no
  // text to measure, and a "0 words" badge would read as an empty section
  // rather than an absent one.
  const wordCounts = Object.fromEntries(
    (result.sections_located || []).map((s) => [s.key, s.word_count]));
  // Group in solicitation order; the backend already sorted, so first-seen
  // section order is the authored order.
  const groups = [];
  for (const f of findings) {
    // A POINTER-ONLY row ("Follow PAPPG guidelines for proposal preparation")
    // says nothing about the draft — its whole content is "go and read that
    // document", which the notice above states ONCE, grouped by rulebook, with
    // a plain description and a link. Rendering it again as a finding put the
    // identical sentence on screen twice and spent a slot in the findings list,
    // beside rows that DO say something about the PI's text. It stays in the
    // payload (the API reports everything) and out of the display.
    if (f.status === "delegated") continue;
    const key = f.section || "_whole";
    let g = groups.find((x) => x.key === key);
    if (!g) {
      g = { key, label: key === "_whole" ? "Whole proposal"
                                       : (labels[key] || titleCase(key)), items: [] };
      groups.push(g);
    }
    g.items.push(f);
  }

  // SCORED rows only. A conditional ask the engine left unscored ("if you
  // request consultants, detail them") is not something to fix first — it may
  // not apply at all. Measured on a live Budget Justification: 14 rows came back
  // not_found and 10 of them were conditionals the draft was never subject to,
  // so the fix-list was 71% noise until this filter existed.
  const actionable = findings.filter(
    (f) => (f.status === "not_found" || f.status === "flagged") && f.scored !== false
  );

  return (
    <div className="eir-results">
      {result.message && (
        <div className="eir-banner">
          <Info size={14} /> {result.message}
        </div>
      )}

      {/* ABOVE the score, deliberately. The score is a percentage of the
          requirements this reviewer could assess, and if part of the package
          was never read then part of it could never be assessed — so a PI has
          to meet that fact before the number, not after it. Absent whenever
          the package fitted, which is the normal case. */}
      {result.truncated && (
        <div className="eir-banner eir-banner-warn">
          <AlertTriangle size={14} /> Your package is{" "}
          {result.truncated.chars.toLocaleString()} characters and only the
          first {result.truncated.read.toLocaleString()} were read. Sections
          near the end were not checked, and the score below is calculated
          without them &mdash; split the package and re-run to cover it all.
        </div>
      )}

      {/* WHEN TOO LITTLE OF THE PACKAGE COULD BE PLACED. Above the score, for the
          same reason the truncation banner is: a reader must meet the caveat
          before the number, not after it. Measured on the awarded proposal as one
          combined Research.gov PDF — 2 of 9 sections found, 21 of 70 rules
          assessable, a funded proposal scoring 48% on every run. A steady wrong
          number reads as a settled one, which is worse than a varying one. */}
      {result.coverage_warning && (
        <div className="eir-banner eir-banner-warn">
          <AlertTriangle size={14} /> {result.coverage_warning}
        </div>
      )}

      <MistakesPanel mistakes={result.mistakes} />

      <ScorePanel score={result.score} solicitationId={solId} />

      <DelegationNotice books={result.delegated} />

      {/* Shown on results too: if one of five files failed to read, that fact
          has to travel WITH the score, or the score silently means less than
          it appears to. */}
      {extraction?.files?.some((f) => f.error || f.truncated) && (
        <ExtractionReport extraction={extraction} />
      )}

      {actionable.length > 0 && (
        <div className="eir-priority">
          <div className="eir-priority-title">
            Fix these {actionable.length} first
          </div>
          <ul>
            {actionable.map((f) => (
              <li key={f.id}>
                {f.label}
                {/* WHAT to do, not just which rule failed. A list of rule names
                    tells the author where the problem is and nothing about how
                    to fix it. */}
                {f.suggestion && (
                  <span className="eir-priority-do">{f.suggestion}</span>
                )}
              </li>
            ))}
          </ul>
        </div>
      )}

      <SectionMap
        located={result.sections_located || []}
        missing={result.sections_missing || []}
      />

      {groups.length > 1 && (
        <div className="eir-group-controls">
          <span className="eir-group-controls-count">
            {findings.length.toLocaleString()} checks across {groups.length} sections
          </span>
          <button type="button" onClick={() => setForced(true)}>Expand all</button>
          <button type="button" onClick={() => setForced(false)}>Collapse all</button>
        </div>
      )}

      {groups.map((g) => (
        // How much text this section holds, next to its findings. The section
        // map has it too, but that is a panel you scroll past — this is where
        // you are when you read "Addressed", and it is the difference between a
        // section that exists and one that says something.
        <FindingGroup
          key={g.key}
          label={g.label}
          words={wordCounts[g.key]}
          items={g.items}
          solicitationId={solId}
          forced={forced}
        />
      ))}

      {result.reviewer_notes?.length > 0 && (
        <section className="eir-group">
          <h3 className="eir-group-title">How a panel might read this</h3>
          <p className="eir-group-sub">
            Advisory impressions against the merit review criteria this
            solicitation states. These are opinions, not coverage checks
            &mdash; nothing here affects the score.
          </p>
          {result.reviewer_notes.map((n) => (
            <div key={n.criterion} className="eir-note">
              <div className="eir-note-criterion">{n.criterion}</div>
              <div className="eir-note-body">{n.note}</div>
            </div>
          ))}
        </section>
      )}

      {result.eligibility_notes?.length > 0 && (
        <section className="eir-group">
          <h3 className="eir-group-title">Eligibility &mdash; check these yourself</h3>
          <p className="eir-group-sub">
            No draft text can prove these. They are the rules that disqualify a
            proposal outright.
          </p>
          <ul className="eir-eligibility">
            {result.eligibility_notes.map((n, i) => <li key={i}>{n}</li>)}
          </ul>
        </section>
      )}

      {/* The LOI / full-proposal `cycle` dates went with the NSF 23-598
          hardcoding: they were computed from that one solicitation's recurrence
          rules, and no other solicitation states its deadlines as rules. This
          block kept reading them, so `cycle.loi_deadline` threw on EVERY
          successful review and the whole app fell to the error boundary. The
          proposal's own deadline is what a PI needs and it is on the detail
          screen already — this footer just names the document reviewed. */}
      {(solId || sol.title || sol.url) && (
        <div className="eir-cycle">
          {solId && <strong>{solId}</strong>}
          {sol.title ? `${solId ? " · " : ""}${sol.title}` : ""}
          {sol.url && (
            <a href={sol.url} target="_blank" rel="noreferrer">
              Read the solicitation <ExternalLink size={11} />
            </a>
          )}
        </div>
      )}

      <p className="eir-disclaimer">
        This checks completeness against {solId || "the attached solicitation"}.
        It does not judge the science, and it is not a prediction of funding.
        Read the solicitation yourself before you submit.
      </p>

      <div className="eir-results-actions">
        <button className="eir-back" onClick={onBack}>Review another draft</button>
        <button className="eir-secondary" onClick={() => {
          const blob = new Blob([reviewToMarkdown(result, submission)],
                                { type: "text/markdown;charset=utf-8" });
          const a = document.createElement("a");
          a.href = URL.createObjectURL(blob);
          a.download = `draft-review-${(submission?.title || "proposal")
            .replace(/[^a-z0-9]+/gi, "-").toLowerCase().slice(0, 60)}.md`;
          document.body.appendChild(a);
          a.click();
          a.remove();
          URL.revokeObjectURL(a.href);
        }}>
          Download a copy
        </button>
        <button className="eir-run eir-save" onClick={onSave} disabled={saving}>
          {saving ? "Saving…" : savedAt ? "Update saved review" : "Save this review"}
        </button>
      </div>
      {saveMsg && <p className="eir-savemsg">{saveMsg}</p>}
    </div>
  );
}
