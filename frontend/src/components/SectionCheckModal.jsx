// SectionCheckModal.jsx
//
// Check ONE section against NSF's rules WHILE THE PI IS STILL WRITING IT — the
// writing step nextStep() lost when the Drafting Coach was removed. Draft
// Review checks a finished package against a solicitation; this checks one
// section against a named RULEBOOK ("the PAPPG") and needs no solicitation at
// all, so it works from the first sentence a PI types.
//
//   1. GET  /api/me/section-check/sections        -> the picker (auth-free)
//   2. POST /api/me/submissions/{id}/section-check         (pasted text)
//      or   /api/me/submissions/{id}/section-check/upload  (one file)
//   -> {section, label, rulebook, findings, mistakes, skeleton, score, ai,
//       word_count, message}
//
// THREE RULES CARRIED OVER FROM DraftReviewModal, deliberately, because this
// modal reuses its finding-row approach and status vocabulary and must not
// contradict it on screen:
//
//   * `result.score` is ALWAYS null. These are NSF's floor for one section, not
//     a completeness universe — a percentage here would read as "your Project
//     Summary is 60% done", which nothing in this tool measures. No score
//     panel exists in this file at all.
//   * `not_checked` (the page-count ESTIMATE from pasted text) renders neutral,
//     same as `could_not_locate` in Draft Review — never a pass, never a fail.
//   * The skeleton's "not a real proposal" caveat is written ONCE by the
//     backend into `skeleton.note` and rendered ONCE, in the skeleton panel.
//     It is never repeated per finding — that is the exact failure that made a
//     PI say the delegation notice "completely crowded the draft review".
//
// Every field read here is optional. `skeleton` is null for References Cited
// and Facilities (only Project Summary and Project Description have one) —
// DraftReviewModal took the whole app down for two days dereferencing a field
// that had quietly gone missing from a response, so nothing here assumes a
// key exists without checking first.

import React, { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import {
  AlertTriangle, Check, ChevronDown, ChevronUp, FileText, HelpCircle, Info,
  MinusCircle, Quote, Upload, X, XCircle,
} from "lucide-react";
import { getApiBase } from "../lib/apiBase";
import "./SectionCheckModal.css";

const API_BASE = getApiBase();

function authHeaders() {
  const token = localStorage.getItem("token");
  return token ? { Authorization: `Bearer ${token}` } : {};
}

// Only "the PAPPG" has rules on file today (services/rulebook_baseline.py).
// Not a picker: a solicitation may cite others later, but there is nothing
// else to choose from yet, and a dropdown with one option is worse than none.
const RULEBOOK = "the PAPPG";

// Used only if the sections fetch fails — the same four sections
// rulebook_baseline.sections_offered() would return, in Research.gov's order,
// so a network hiccup doesn't leave the picker with nothing in it.
const FALLBACK_SECTIONS = [
  { key: "project_summary", label: "Project Summary" },
  { key: "project_description", label: "Project Description" },
  { key: "references_cited", label: "References Cited" },
  { key: "facilities_equipment_and_other_resources",
    label: "Facilities, Equipment and Other Resources" },
];

// Status vocabulary mirrors DraftReviewModal.STATUS_META exactly — same words,
// same colors, same `neutral` flag — because a PI reading both tools in one
// session must not learn two visual languages for "we did not assess this".
const STATUS_META = {
  addressed:        { label: "Addressed",    Icon: Check,         cls: "scm-ok" },
  partial:          { label: "Partial",      Icon: AlertTriangle, cls: "scm-warn" },
  not_found:        { label: "Not found",    Icon: XCircle,       cls: "scm-fail" },
  clear:            { label: "Clear",        Icon: Check,         cls: "scm-ok" },
  flagged:          { label: "Flagged",      Icon: XCircle,       cls: "scm-fail" },
  could_not_locate: { label: "Not located",  Icon: HelpCircle,    cls: "scm-skip", neutral: true },
  // The page-count ESTIMATE from pasted text. Never a pass or a fail — only an
  // uploaded PDF gets a real page count (see rb_page_limit in
  // services/rulebook_checks.py).
  not_checked:      { label: "Not checked",  Icon: MinusCircle,   cls: "scm-skip", neutral: true },
  unclear:          { label: "Unassessed",   Icon: MinusCircle,   cls: "scm-skip", neutral: true },
  delegated:        { label: "Not ours to check", Icon: HelpCircle, cls: "scm-skip", neutral: true },
  not_in_draft:     { label: "Done at submission", Icon: MinusCircle, cls: "scm-skip", neutral: true },
};

function StatusChip({ status }) {
  const meta = STATUS_META[status] || STATUS_META.unclear;
  const { Icon } = meta;
  return (
    <span className={`scm-chip ${meta.cls}`}>
      <Icon size={12} /> {meta.label}
    </span>
  );
}

const ACCEPT = ".pdf,.docx,.txt,.md,.markdown,.text,.rst,.csv,.tex";

function humanSize(bytes) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export default function SectionCheckModal({ submission, onClose }) {
  const [sections, setSections] = useState(FALLBACK_SECTIONS);
  const [section, setSection] = useState("project_summary");
  const [mode, setMode] = useState("paste"); // paste | upload
  const [text, setText] = useState("");
  const [file, setFile] = useState(null);
  const [step, setStep] = useState("input"); // input | running | results
  const [result, setResult] = useState(null);
  const [extraction, setExtraction] = useState(null);
  const [error, setError] = useState("");

  // Auth-free on the backend (a static list of section names), but sending the
  // header anyway costs nothing and keeps this call looking like every other
  // one in the file rather than a special case someone has to remember.
  useEffect(() => {
    let live = true;
    fetch(`${API_BASE}/api/me/section-check/sections`, { headers: authHeaders() })
      .then((r) => (r.ok ? r.json() : null))
      .then((body) => {
        if (!live) return;
        const list = body?.sections;
        if (Array.isArray(list) && list.length) {
          setSections(list);
          setSection((cur) => (list.some((s) => s.key === cur) ? cur : list[0].key));
        }
      })
      .catch(() => { /* keep the fallback list */ });
    return () => { live = false; };
  }, []);

  const words = text.trim() ? text.trim().split(/\s+/).length : 0;
  const canRun = mode === "upload" ? Boolean(file) : Boolean(text.trim());
  const sectionLabel =
    sections.find((s) => s.key === section)?.label
    || FALLBACK_SECTIONS.find((s) => s.key === section)?.label
    || section;

  function addFile(incoming) {
    const f = incoming?.[0];
    if (!f) return;
    setError("");
    setFile(f);
  }

  async function run() {
    setStep("running");
    setError("");
    setExtraction(null);
    try {
      let res;
      if (mode === "upload") {
        const form = new FormData();
        form.append("section", section);
        form.append("rulebook", RULEBOOK);
        form.append("file", file, file.name);
        res = await fetch(
          `${API_BASE}/api/me/submissions/${submission.id}/section-check/upload`,
          { method: "POST", headers: { ...authHeaders() }, body: form }
        );
      } else {
        res = await fetch(
          `${API_BASE}/api/me/submissions/${submission.id}/section-check`,
          {
            method: "POST",
            headers: { "Content-Type": "application/json", ...authHeaders() },
            body: JSON.stringify({ section, text, rulebook: RULEBOOK }),
          }
        );
      }
      if (!res.ok) {
        let detail = `Check failed (${res.status})`;
        try {
          const j = await res.json();
          if (j.detail) detail = j.detail;
        } catch { /* non-JSON error body — keep the status message */ }
        throw new Error(detail);
      }
      const body = await res.json();
      setExtraction(body.extraction || null);
      // The upload path returns result:null when the file had no readable
      // text — same shape as DraftReviewModal's upload failure, and the same
      // reason: reporting every rule "not found" against an unreadable file
      // would look like a real review of content that was never read.
      if (!body.result) {
        setError(body.error || "Couldn't read any text from that file.");
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

  return createPortal(
    <div className="scm-overlay" onClick={onClose}>
      <div className="scm-modal" onClick={(e) => e.stopPropagation()}>
        <header className="scm-header">
          <div>
            <h2>Check a Section</h2>
            <div className="scm-sub">
              Check one section against the PAPPG's rules while you're still
              writing it &mdash; no solicitation needed.
            </div>
          </div>
          <button className="scm-close" onClick={onClose} aria-label="Close">
            <X size={18} />
          </button>
        </header>

        {step === "input" && (
          <InputView
            sections={sections} section={section} setSection={setSection}
            mode={mode} setMode={setMode}
            text={text} setText={setText} words={words}
            file={file} addFile={addFile} removeFile={() => setFile(null)}
            error={error} canRun={canRun} onRun={run}
          />
        )}

        {step === "running" && (
          <div className="scm-loading">
            <div className="scm-spinner" />
            <p>Checking {sectionLabel} against the PAPPG&rsquo;s rules&hellip;</p>
          </div>
        )}

        {step === "results" && result && (
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

function InputView({
  sections, section, setSection, mode, setMode,
  text, setText, words, file, addFile, removeFile,
  error, canRun, onRun,
}) {
  const inputRef = useRef(null);
  const [over, setOver] = useState(false);

  return (
    <div className="scm-input-view">
      <label className="scm-field">
        <span className="scm-field-label">Which section?</span>
        <select
          className="scm-select"
          value={section}
          onChange={(e) => setSection(e.target.value)}
        >
          {sections.map((s) => (
            <option key={s.key} value={s.key}>{s.label}</option>
          ))}
        </select>
      </label>

      <p className="scm-intro">
        Paste what you have so far, or upload a single file for this section.
        Either works &mdash; you don&rsquo;t need a finished draft or the rest
        of the proposal.
      </p>

      <div className="scm-modes" role="tablist">
        <button
          role="tab" aria-selected={mode === "paste"}
          className={`scm-mode${mode === "paste" ? " is-active" : ""}`}
          onClick={() => setMode("paste")}
        >
          Paste text
        </button>
        <button
          role="tab" aria-selected={mode === "upload"}
          className={`scm-mode${mode === "upload" ? " is-active" : ""}`}
          onClick={() => setMode("upload")}
        >
          Upload a file
        </button>
      </div>

      {mode === "paste" ? (
        <textarea
          className="scm-textarea"
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder={
            "Overview\n...\n\nIntellectual Merit\n...\n\nBroader Impacts\n..."
          }
          spellCheck={false}
        />
      ) : (
        <>
          <div
            className={`scm-drop${over ? " is-over" : ""}`}
            onDragOver={(e) => { e.preventDefault(); setOver(true); }}
            onDragLeave={() => setOver(false)}
            onDrop={(e) => {
              e.preventDefault();
              setOver(false);
              addFile(e.dataTransfer.files);
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
            <Upload size={20} />
            <div className="scm-drop-main">
              Drop one file here, or <span>browse</span>
            </div>
            <div className="scm-drop-hint">PDF, Word (.docx), or plain text</div>
            <input
              ref={inputRef}
              type="file"
              accept={ACCEPT}
              className="scm-file-input"
              onChange={(e) => { addFile(e.target.files); e.target.value = ""; }}
            />
          </div>
          {file && (
            <div className="scm-fileitem">
              <FileText size={14} />
              <span className="scm-filename">{file.name}</span>
              <span className="scm-filesize">{humanSize(file.size)}</span>
              <button
                className="scm-fileremove"
                onClick={(e) => { e.stopPropagation(); removeFile(); }}
                aria-label={`Remove ${file.name}`}
              >
                <X size={13} />
              </button>
            </div>
          )}
        </>
      )}

      {/* The one honest line this feature depends on: a paste can only ever
          give a word-count ESTIMATE of length, never a real page count. */}
      <p className="scm-hint">
        Upload the PDF if you want the page limit checked properly &mdash;
        from pasted text we can only estimate it.
      </p>

      {error && <div className="scm-errorbox"><AlertTriangle size={14} /> {error}</div>}

      <div className="scm-input-footer">
        <span className="scm-wordcount">
          {mode === "upload"
            ? (file ? "1 file selected" : "No file selected")
            : `${words.toLocaleString()} ${words === 1 ? "word" : "words"}`}
        </span>
        <button className="scm-run" onClick={onRun} disabled={!canRun}>
          Check this section
        </button>
      </div>

      <p className="scm-privacy">
        Your text is read in memory and discarded &mdash; nothing is saved or
        stored.
      </p>
    </div>
  );
}

// The structural shape a section is expected to take — NOT a sample proposal
// and not written about the PI's own science. Collapsed by default so it
// doesn't compete with the findings for attention; its "not a real proposal"
// caveat lives in `skeleton.note`, rendered here and NOWHERE else in this
// modal.
function SkeletonPanel({ skeleton }) {
  const [open, setOpen] = useState(false);
  if (!skeleton) return null;
  return (
    <div className="scm-skeleton">
      <button
        type="button"
        className="scm-skeleton-toggle"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
      >
        <span>{skeleton.title || "How this section is laid out"}</span>
        {open ? <ChevronUp size={15} /> : <ChevronDown size={15} />}
      </button>
      {open && (
        <div className="scm-skeleton-body">
          {skeleton.note && <p className="scm-skeleton-note">{skeleton.note}</p>}
          {skeleton.body && <pre className="scm-skeleton-pre">{skeleton.body}</pre>}
        </div>
      )}
    </div>
  );
}

// Same shape and rendering approach as DraftReviewModal's FindingRow: status
// chip, optional prohibition/advisory/defers tags, note, verified evidence
// quote, and a "why" disclosure holding the funder's own sentence. No section
// grouping here — the whole list IS one section, so a group header would
// repeat what the modal's own picker already says.
function FindingRow({ f }) {
  const [open, setOpen] = useState(false);
  const meta = STATUS_META[f.status] || STATUS_META.unclear;
  return (
    <div className={`scm-finding ${meta.cls}${meta.neutral ? " is-neutral" : ""}`}>
      <div className="scm-finding-head">
        <span className="scm-finding-label">
          {f.label}
          {f.prohibition && <span className="scm-tag">prohibited</span>}
          {!f.scored && <span className="scm-tag scm-tag-soft">advisory</span>}
          {f.delegated_to && (
            <span className="scm-tag scm-tag-defer">defers to {f.delegated_to}</span>
          )}
        </span>
        <StatusChip status={f.status} />
      </div>

      {f.note && <div className="scm-finding-note">{f.note}</div>}

      {f.evidence && (
        <div className="scm-evidence">
          <Quote size={12} /> <span>{f.evidence}</span>
        </div>
      )}

      <button className="scm-why-toggle" onClick={() => setOpen((o) => !o)}>
        {open ? "Hide" : "Why is this required?"}
      </button>
      {open && (
        <div className="scm-why">
          {f.why && <p className="scm-why-plain">{f.why}</p>}
          {f.solicitation_says && (
            <blockquote className="scm-why-source">
              &ldquo;{f.solicitation_says}&rdquo;
              <cite>the PAPPG</cite>
            </blockquote>
          )}
        </div>
      )}
    </div>
  );
}

// Mechanical errors: found by code, quoted from the pasted/uploaded text, and
// deliberately NOT part of any score — this modal has no score at all, but the
// heading says so anyway because "mistakes" reads like a grade if nothing
// tells the PI otherwise.
function MistakesPanel({ mistakes }) {
  if (!mistakes?.length) return null;
  return (
    <div className="scm-mistakes">
      <div className="scm-mistakes-head">
        <AlertTriangle size={14} />
        {mistakes.length === 1
          ? "1 mistake to fix"
          : `${mistakes.length} mistakes to fix`}
      </div>
      <p className="scm-mistakes-lede">
        Found by a rule, not a judgement &mdash; each one quotes where it is.
        These are errors, not coverage, and are not part of any score.
      </p>
      {mistakes.map((m, i) => (
        <div key={`${m.kind || "mistake"}:${i}`} className="scm-mistake">
          <div className="scm-mistake-label">{m.label}</div>
          <div className="scm-mistake-detail">{m.detail}</div>
          {m.evidence && (
            <div className="scm-evidence">
              <Quote size={12} /> <span>{m.evidence}</span>
            </div>
          )}
        </div>
      ))}
    </div>
  );
}

// A single read-report line for the upload path — one file, so there is at
// most one row to show, unlike Draft Review's multi-file list.
function ExtractionLine({ extraction }) {
  if (!extraction || !extraction.filename) return null;
  return (
    <div className={`scm-extract-row${extraction.error ? " is-bad" : ""}`}>
      {extraction.error ? <XCircle size={13} /> : <Check size={13} />}
      <span className="scm-filename">{extraction.filename}</span>
      <span className="scm-extract-detail">
        {extraction.error
          ? extraction.error
          : `${(extraction.chars || 0).toLocaleString()} characters` +
            (extraction.pages ? ` · ${extraction.pages} pages` : "")}
      </span>
    </div>
  );
}

function ResultsView({ result, extraction, onBack }) {
  const findings = result.findings || [];
  const mistakes = result.mistakes || [];

  return (
    <div className="scm-results">
      {result.message && (
        <div className="scm-banner">
          <Info size={14} /> {result.message}
        </div>
      )}

      <ExtractionLine extraction={extraction} />

      <SkeletonPanel skeleton={result.skeleton} />

      {findings.length > 0 ? (
        <section className="scm-group">
          <h3 className="scm-group-title">
            {result.label || "This section"}
            {result.word_count != null && (
              <span className="scm-group-words">
                {result.word_count.toLocaleString()}{" "}
                {result.word_count === 1 ? "word" : "words"}
              </span>
            )}
          </h3>
          {findings.map((f) => (
            <FindingRow key={f.id} f={f} />
          ))}
        </section>
      ) : (
        !result.message && (
          <p className="scm-empty">No rules are on file for this section yet.</p>
        )
      )}

      <MistakesPanel mistakes={mistakes} />

      <p className="scm-disclaimer">
        Checked against {result.rulebook || "the PAPPG"}&rsquo;s rules for{" "}
        {result.label || "this section"}. There is no score here &mdash;
        these are NSF&rsquo;s floor for one section, not a measure of how
        complete or fundable your proposal is.
      </p>

      <div className="scm-results-actions">
        <button className="scm-back" onClick={onBack}>Check another section</button>
      </div>
    </div>
  );
}
