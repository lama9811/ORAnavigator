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

import React, { useRef, useState } from "react";
import { createPortal } from "react-dom";
import {
  AlertTriangle, Check, ExternalLink, FileText, HelpCircle, Info,
  MinusCircle, Quote, Trash2, Upload, X, XCircle,
} from "lucide-react";
import { getApiBase } from "../lib/apiBase";
import "./DraftReviewModal.css";

const API_BASE = getApiBase();

function authHeaders() {
  const token = localStorage.getItem("token");
  return token ? { Authorization: `Bearer ${token}` } : {};
}

// Status vocabulary mirrors services/eir_review.py exactly. `neutral: true`
// marks the two "we did not assess this" states so they can never be styled or
// counted as failures.
const STATUS_META = {
  addressed:        { label: "Addressed",    Icon: Check,         cls: "eir-ok" },
  partial:          { label: "Partial",      Icon: AlertTriangle, cls: "eir-warn" },
  not_found:        { label: "Not found",    Icon: XCircle,       cls: "eir-fail" },
  clear:            { label: "Clear",        Icon: Check,         cls: "eir-ok" },
  flagged:          { label: "Flagged",      Icon: XCircle,       cls: "eir-fail" },
  could_not_locate: { label: "Not located",  Icon: HelpCircle,    cls: "eir-skip", neutral: true },
  not_checked:      { label: "Not checked",  Icon: MinusCircle,   cls: "eir-skip", neutral: true },
  unclear:          { label: "Unassessed",   Icon: MinusCircle,   cls: "eir-skip", neutral: true },
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
                <button className="eir-run" onClick={onAttach}>
                  {priorSolicitation ? "Read the solicitation" : "Attach the solicitation"}
                </button>
              )}
              <button className="eir-secondary" onClick={onClose}>Not now</button>
            </div>
          </div>
        )}

        {hasSolicitation && summary && <ReadReport summary={summary} />}

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
      <p className="eir-intro">
        Upload your whole EiR package &mdash; Project Summary, Project
        Description, letters, budget justification. Upload them together; the
        reviewer reads each file and checks the package as a whole.
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
        <>
          <DropZone files={files} addFiles={addFiles} removeFile={removeFile} />
          <p className="eir-filenote">
            Keep the filenames descriptive &mdash; the reviewer uses them to tell
            your sections apart, so <em>Letter of Institutional Support.pdf</em>
            {" "}works better than <em>doc3.pdf</em>.
          </p>
        </>
      ) : (
        <textarea
          className="eir-textarea"
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          placeholder={
            "Excellence in Research: Your Title Here\n\n" +
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
function ScorePanel({ score }) {
  if (!score) return null;
  return (
    <div className={`eir-score eir-score-${score.band}`}>
      <div className="eir-score-num">
        {score.percent}<span className="eir-score-pct">%</span>
      </div>
      <div className="eir-score-body">
        <div className="eir-score-title">Completeness against NSF 23-598</div>
        <div className="eir-score-basis">{score.basis}</div>
        <div className="eir-score-bar">
          <div className="eir-score-fill" style={{ width: `${score.percent}%` }} />
        </div>
      </div>
    </div>
  );
}

function SectionMap({ located, missing }) {
  return (
    <div className="eir-sectionmap">
      <div className="eir-sectionmap-title">What the reviewer found in your paste</div>
      <div className="eir-sectionmap-rows">
        {located.map((s) => (
          <span key={s.key} className="eir-pill eir-pill-found">
            <Check size={11} /> {s.label}
            <span className="eir-pill-count">{s.word_count.toLocaleString()}w</span>
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

function FindingRow({ f }) {
  const [open, setOpen] = useState(false);
  const meta = STATUS_META[f.status] || STATUS_META.unclear;
  return (
    <div className={`eir-finding ${meta.cls}${meta.neutral ? " is-neutral" : ""}`}>
      <div className="eir-finding-head">
        <span className="eir-finding-label">
          {f.label}
          {f.prohibition && <span className="eir-tag">prohibited</span>}
          {!f.scored && <span className="eir-tag eir-tag-soft">advisory</span>}
        </span>
        <StatusChip status={f.status} />
      </div>

      {f.note && <div className="eir-finding-note">{f.note}</div>}

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
            <cite>NSF 23-598</cite>
          </blockquote>
        </div>
      )}
    </div>
  );
}

function ResultsView({ result, extraction, onBack }) {
  const findings = result.findings || [];
  const labels = sectionLabels(result);
  // Group in solicitation order; the backend already sorted, so first-seen
  // section order is the authored order.
  const groups = [];
  for (const f of findings) {
    const key = f.section || "_whole";
    let g = groups.find((x) => x.key === key);
    if (!g) {
      g = { key, label: key === "_whole" ? "Whole proposal"
                                       : (labels[key] || titleCase(key)), items: [] };
      groups.push(g);
    }
    g.items.push(f);
  }

  const actionable = findings.filter(
    (f) => f.status === "not_found" || f.status === "flagged"
  );

  return (
    <div className="eir-results">
      {result.message && (
        <div className="eir-banner">
          <Info size={14} /> {result.message}
        </div>
      )}

      <ScorePanel score={result.score} />

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
              <li key={f.id}>{f.label}</li>
            ))}
          </ul>
        </div>
      )}

      <SectionMap
        located={result.sections_located || []}
        missing={result.sections_missing || []}
      />

      {groups.map((g) => (
        <section key={g.key} className="eir-group">
          <h3 className="eir-group-title">{g.label}</h3>
          {g.items.map((f) => <FindingRow key={f.id} f={f} />)}
        </section>
      ))}

      {result.reviewer_notes?.length > 0 && (
        <section className="eir-group">
          <h3 className="eir-group-title">How a panel might read this</h3>
          <p className="eir-group-sub">
            Advisory impressions against NSF&rsquo;s two merit review criteria.
            These are opinions, not coverage checks &mdash; nothing here affects
            the score.
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

      <div className="eir-cycle">
        <strong>{result.solicitation.id}</strong> &middot; Letter of Intent{" "}
        {result.solicitation.cycle.loi_deadline} (required) &middot; Full
        proposal {result.solicitation.cycle.full_proposal_deadline}
        <a href={result.solicitation.url} target="_blank" rel="noreferrer">
          Read the solicitation <ExternalLink size={11} />
        </a>
      </div>

      <p className="eir-disclaimer">
        This checks completeness against NSF 23-598. It does not judge the
        science, and it is not a prediction of funding. Read the solicitation
        yourself before you submit.
      </p>

      <button className="eir-back" onClick={onBack}>Review another draft</button>
    </div>
  );
}
