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
//   * `result.score` IS rendered (since 2026-08-20, by request) and is a
//     RULES-MET SHARE, never "how done this section is". The old concern still
//     holds and is answered by the denominator rather than by hiding the
//     number: only rules actually CHECKED are counted, so `not_checked`,
//     `could_not_locate` and `unclear` are absent and an advisory conditional
//     never enters. `score.by_source` splits it by authority — NSF's rulebook
//     and this solicitation are different obligations and one number cannot say
//     which half is failing.
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
  AlertTriangle, Check, ChevronDown, ChevronRight, ChevronUp, FileText,
  HelpCircle, Info, Lightbulb, ListChecks, MinusCircle, Quote, Upload, X, XCircle, SpellCheck} from "lucide-react";
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
  // "Addressed" read to an author as "this is good". It only ever meant "the
  // rule is satisfied" — these rules are about PRESENCE — and a 76-word Project
  // Summary carrying six of them was reported as fine. Same rename and the same
  // reason as "Not checked" -> "Not ours to check": the word was doing damage
  // the status never intended.
  addressed:        { label: "Meets the rule", Icon: Check,       cls: "scm-ok" },
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

  // THE PER-SUBMISSION ROUTE, because the picker is a property of the PROPOSAL.
  //
  // This used to call the auth-free /api/me/section-check/sections, which
  // answers for the rulebook and cannot do better -- it never sees a
  // submission. So every proposal was offered the PAPPG's seven sections
  // whatever its own solicitation asked for, and a solicitation's own
  // deliverables were unreachable: on a live NSF 23-598 proposal that was 8
  // scored Letter of Intent rules a PI had no way to check.
  //
  // The auth-free route stays the fallback, deliberately. It is the right
  // answer for a proposal with no solicitation, and if the per-proposal call
  // fails the PI still gets a working picker rather than an empty one.
  useEffect(() => {
    let live = true;
    const id = submission?.id;
    const urls = [
      id ? `${API_BASE}/api/me/submissions/${id}/section-check/sections` : null,
      `${API_BASE}/api/me/section-check/sections`,
    ].filter(Boolean);

    (async () => {
      for (const url of urls) {
        try {
          const r = await fetch(url, { headers: authHeaders() });
          if (!r.ok) continue;
          const list = (await r.json())?.sections;
          if (!live) return;
          if (Array.isArray(list) && list.length) {
            setSections(list);
            setSection((cur) => (list.some((s) => s.key === cur) ? cur : list[0].key));
            return;
          }
        } catch { /* try the next one */ }
      }
    })();
    return () => { live = false; };
  }, [submission?.id]);

  // What the picker already told us about the section being checked, so the
  // running message can be specific instead of naming a rulebook it may not be
  // the only source for.
  const pendingRuleCount = (() => {
    const s = sections.find((x) => x.key === section);
    if (!s || typeof s.solicitation_rules !== "number") return 0;
    return (s.solicitation_rules || 0) + (s.rulebook_rules || 0);
  })();

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
            {/* This read "against the PAPPG's rules ... no solicitation
                needed", which was true when that was all it did. Since
                2026-08-26 it checks your solicitation's own rules for the
                section plus NSF's basics, and a screen that describes an older
                version of itself is how a PI concludes the tool ignores their
                funder. The solicitation is named first because it leads. */}
            <div className="scm-sub">
              Check one section against your solicitation&rsquo;s rules and
              NSF&rsquo;s basics, while you&rsquo;re still writing it.
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
            {/* NOT "against the PAPPG's rules". Since 2026-08-26 a section is
                checked against the solicitation's rules AND NSF's basics, and
                naming one of the two is how a PI concludes their funder is
                being ignored -- the same stale sentence that was already fixed
                in the header and missed here. The picker knows the split, so
                say the number instead of guessing at a name. */}
            <p>
              Checking your {sectionLabel}
              {pendingRuleCount > 0 && <> against {pendingRuleCount} rule
                {pendingRuleCount === 1 ? "" : "s"}</>}
              &hellip;
            </p>
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
          {/* ONE FLAT LIST. These were grouped under headings naming where
              each section's rules came from -- "From your solicitation", "NSF
              baseline". Every section is checked against BOTH, so the headings
              described provenance and a PI read them as "these are checked
              differently". The split still appears where it changes what you
              do about it: in the score, after the run, as "NSF 4/4,
              NSF 23-598 0/1". The ORDER now carries what is left -- the order
              a proposal is actually written in. */}
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
//
// ATTRIBUTION IS PER ROW, NEVER PER MODAL. `review_section` merges the
// solicitation's OWN rows for this section in beside the baseline's, and their
// `solicitation_says` is the solicitation's sentence — so stamping the modal's
// rulebook under every quote credited the PAPPG with words NSF never wrote
// there. Baseline rows carry `rulebook`; solicitation rows carry null, and get
// no attribution rather than a wrong one.
/* ── findings grouped by what the PI has to DO about them ───────────────────
 *
 * Draft Review groups by SECTION because it spans a whole package. This modal
 * checks ONE section, so a section grouping would be a single heading over a
 * flat list — which is exactly what this was, and it was fine at five rules.
 * The PAPPG's Budget and Budget Justification section alone carries 51, and a
 * flat always-open list of 51 buries the handful that need work.
 *
 * So the grouping here is by STATUS, ordered by what it asks of the reader, and
 * only the two groups that ask for something are open. "Not checked here" is
 * last and closed: those rules are real and worth knowing, and not one of them
 * is a thing the PI can act on in this session.
 */
const BUCKETS = [
  // SCORED not_found/flagged only. A conditional ask the engine left unscored
  // ("if you request consultants, detail them") is not work — it may not apply.
  // Measured live on a clean Budget Justification: 14 rows came back not_found
  // and 10 were conditionals that draft was never subject to.
  { key: "fix", label: "Needs work", match: ["not_found", "flagged"],
    scoredOnly: true, openByDefault: true },
  { key: "partial", label: "Partly there", match: ["partial"],
    openByDefault: true },
  { key: "maybe", label: "If this applies to you", match: ["not_found", "flagged"],
    advisoryOnly: true, openByDefault: false },
  { key: "ok", label: "Addressed", match: ["addressed", "clear"],
    openByDefault: false },
  // could_not_locate is NOT "missing" and never says so — see the engine. It
  // sits here with not_checked because both mean the same thing to a reader:
  // nobody looked, and re-running will not change that by itself.
  { key: "skipped", label: "Not checked here",
    match: ["not_checked", "could_not_locate", "not_in_draft", "unclear",
            "delegated"],
    openByDefault: false },
];

/* ── how much of the page this uses, and what to do first ────────────────────
 *
 * Both computed in code (services/section_guidance.py). The report that prompted
 * them: a 76-word Project Summary told six of eight rules were "Addressed",
 * where two runs of the same paste disagreed about how many passed. A model
 * asked "is this thin?" would be inconsistent on exactly the question the
 * author already distrusts — so a word count and an ordering are arithmetic.
 *
 * The length line is a MEASUREMENT, never a verdict. The PAPPG sets a maximum
 * for a section and never a minimum, so "your summary is too short" would
 * invent the rule; "you are using 14% of your page" is a fact the author can
 * act on and argue with.
 */
function GuidancePanel({ guidance }) {
  // The length MEASUREMENT moved into the score panel, where the number it
  // qualifies actually is. Rendering it here too would put one fact in two
  // places -- the failure this repo already had to unship when the delegation
  // caveat appeared four times and buried the reviewer's real feedback.
  // It stays here in ONE case: over the limit, which is a real violation and
  // belongs next to the to-do list rather than beside a score it does not enter.
  const length = guidance?.length;
  const over = length && length.pct > 100 ? length : null;
  const priorities = guidance?.priorities || [];
  if (!over && !priorities.length) return null;
  return (
    <div className="scm-guidance">
      {over && <p className="scm-length is-over">{over.message}</p>}
      {priorities.length > 0 && (
        <>
          <div className="scm-guidance-title">
            {/* The heading is DECIDED BY THE BACKEND from what is in the
                list. "Do this first" over a section that met every rule reads
                as failure — reported by a PI whose Project Summary passed all
                four of its checkable rules and was handed a to-do list. */}
            <ListChecks size={13} /> {guidance.priorities_heading || "Do this first"}
          </div>
          <ol className="scm-guidance-list">
            {priorities.map((p) => (
              <li key={p.id}>
                <span className="scm-guidance-rule">{p.label}</span>
                {p.advisory && <span className="scm-tag scm-tag-soft">if it applies</span>}
                <span className="scm-guidance-text">{p.text}</span>
              </li>
            ))}
          </ol>
        </>
      )}
    </div>
  );
}


function StatusGroup({ label, items, openByDefault }) {
  const [open, setOpen] = useState(openByDefault);
  return (
    <section className={`scm-group${open ? "" : " scm-group-closed"}`}>
      <h3 className="scm-group-title">
        <button type="button" className="scm-group-toggle"
                aria-expanded={open} onClick={() => setOpen((v) => !v)}>
          {open ? <ChevronDown size={15} /> : <ChevronRight size={15} />}
          <span className="scm-group-name">{label}</span>
          <span className="scm-group-count">{items.length}</span>
        </button>
      </h3>
      {open && items.map((f) => <FindingRow key={f.id} f={f} />)}
    </section>
  );
}


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

      {/* Carried on EVERY row, including the ones that pass — that is the whole
          point. A passing row used to show praise ("The draft clearly states the
          overarching objective"), which tells an author nothing and makes
          "Meets the rule" read as "you are done here". */}
      {f.suggestion && (
        <div className="scm-suggestion">
          <Lightbulb size={12} /> <span>{f.suggestion}</span>
        </div>
      )}

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
              {f.rulebook && <cite>{f.rulebook}</cite>}
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

// THE SCORE for one section — the share of checkable rules this text meets.
//
// Two things it must keep saying out loud, because both were live mistakes in
// this repo before: the caption states what the denominator IS (rules checked,
// not rules that exist), and the split names each authority separately. A PI
// who meets every PAPPG rule and misses every solicitation rule sees the same
// combined number as one who did the reverse — and only the second is likely to
// come back from NSF without review.
function SectionScorePanel({ score, length, verdict }) {
  if (!score && !verdict) return null;
  const sources = Object.entries((score || {}).by_source || {});
  // THE VERDICT DRIVES THE COLOUR, not the rules percentage. Measured before
  // this existed: a Project Summary with a doubled word, two misspellings, a
  // wrong word and eleven more problems met all five of its rules and painted
  // the panel green at 100%. The rules number is still shown and still true —
  // it just is not the whole news, and the box must not look finished while
  // fifteen errors sit under it.
  const level = verdict ? verdict.level : (score || {}).band;
  const issues = (verdict || {}).issues;
  return (
    <div className={`scm-score scm-score-${level}`}>
      {verdict && (
        <div className="scm-verdict-label">{verdict.label}</div>
      )}
      {score && (
      <div className="scm-score-head">
        <div className="scm-score-count">
          <span className="scm-score-earned">{score.earned}</span>
          <span className="scm-score-of"> of {score.assessed}</span>
        </div>
        <div className="scm-score-body">
          <div className="scm-score-title">
            rules met <span className="scm-score-pct">({score.percent}%)</span>
          </div>
          <div className="scm-score-basis">{score.basis}</div>
        </div>
      </div>
      )}

      {/* ISSUES AT THE SAME WEIGHT AS THE RULES COUNT. Deliberately NOT folded
          into the percentage: an error is verifiable, a weight is an opinion,
          and blending them would mean deciding what fraction of a missing
          Broader Impacts statement one typo is worth. Two counts, one verdict
          sentence that reads both. */}
      {issues && issues.total > 0 && (
        <div className="scm-score-issues">
          <div className="scm-score-count">
            <span className="scm-score-earned">{issues.total}</span>
          </div>
          <div className="scm-score-body">
            <div className="scm-score-title">
              writing {issues.total === 1 ? "problem" : "problems"} found
            </div>
            <div className="scm-score-basis">
              {issues.mistakes > 0 && `${issues.mistakes} found by a rule`}
              {issues.mistakes > 0 && issues.wording > 0 && " \u00b7 "}
              {issues.wording > 0 && `${issues.wording} found by the proofreader`}
              {" \u2014 listed below, each quoting your text."}
            </div>
          </div>
        </div>
      )}

      {verdict && (
        <div className="scm-verdict-summary">{verdict.summary}</div>
      )}

      {/* THE LENGTH SITS IN THIS BOX, not in a grey line further down. A PI
          seeing a large "100%" beside a faint "uses 28% of the page" reads the
          first and not the second — which is exactly the report that prompted
          this. Same weight, same border, one glance. It is a MEASUREMENT: NSF
          sets a maximum and never a minimum, so this never says "too short". */}
      {(length || sources.length > 1) && (
        <div className="scm-score-split">
          {length && (
            <div className="scm-score-src">
              <span className="scm-score-src-name">length</span>
              <span className="scm-score-src-num">{length.words} words</span>
              <span className="scm-score-src-of">
                {length.pct}% of your {length.page_limit === 1
                  ? "one page" : `${length.page_limit} pages`}
              </span>
            </div>
          )}
          {sources.length > 1 && sources.map(([name, s]) => (
            <div className="scm-score-src" key={name}>
              <span className="scm-score-src-name">{name}</span>
              <span className="scm-score-src-num">{s.earned} of {s.assessed}</span>
              <span className="scm-score-src-of">{s.percent}%</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// PROOFREADING — model-found language errors, kept visibly APART from the
// deterministic list above it.
//
// `mistakes` is captioned "found by a rule, not a judgement" and that has to
// stay true, so these live under their own key and say plainly where they came
// from. They are errors, not style: the prompt forbids tone, readability and
// rewrites, which is the line between this and the Drafting Coach that was
// deleted by product decision. They change no number.
function WordingPanel({ wording }) {
  if (!wording?.length) return null;
  return (
    <div className="scm-wording">
      <div className="scm-wording-head">
        <SpellCheck size={14} />
        {wording.length === 1
          ? "1 wording issue"
          : `${wording.length} wording issues`}
      </div>
      <p className="scm-wording-lede">
        Spelling, grammar and punctuation, read by AI and quoted from your text.
        Check each one yourself &mdash; this is a second reader, not a rule, and
        it is not part of any score.
      </p>
      {wording.map((w, i) => (
        <div key={`${w.kind || "w"}:${i}`} className="scm-wording-row">
          <div className="scm-wording-label">
            {w.label}
            <span className="scm-tag scm-tag-soft">AI</span>
            {/* WHERE, when we could place it. Computed in code, absent rather
                than guessed — a wrong line number is acted on, a missing one
                is not. A quote alone still means hunting 500 words for it. */}
            {w.where && (
              <span className="scm-wording-where">
                {w.where.heading && <>{w.where.heading} &middot; </>}
                line {w.where.line}
              </span>
            )}
          </div>
          <div className="scm-wording-detail">{w.detail}</div>
          {w.evidence && (
            <div className="scm-evidence">
              <Quote size={12} /> <span>{w.evidence}</span>
            </div>
          )}
          {/* The whole line as the author wrote it, so the fragment above can
              be found by eye rather than by Ctrl-F. Suppressed when the quote
              IS the line, which is the common short-quote case. */}
          {w.where?.context && w.where.context !== w.evidence && (
            <div className="scm-wording-context">{w.where.context}</div>
          )}
        </div>
      ))}
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

      {/* Score BELOW the skeleton and ABOVE the to-do list: a number is what
          the eye lands on first, and it must not be the first thing a PI sees
          before the structural guidance that tells them what the section is
          for. It sits above the findings so the count it summarises follows. */}
      <SectionScorePanel score={result.score}
                         verdict={result.verdict}
                         length={result.guidance?.length} />

      <GuidancePanel guidance={result.guidance} />

      {findings.length > 0 ? (
        <>
          <h3 className="scm-group-title scm-section-head">
            {result.label || "This section"}
            {result.word_count != null && (
              <span className="scm-group-words">
                {result.word_count.toLocaleString()}{" "}
                {result.word_count === 1 ? "word" : "words"}
              </span>
            )}
          </h3>
          {BUCKETS.map((b) => {
            const items = findings.filter(
              (f) => b.match.includes(f.status)
                && (!b.scoredOnly || f.scored !== false)
                && (!b.advisoryOnly || f.scored === false));
            if (!items.length) return null;
            return (
              <StatusGroup key={b.key} label={b.label} items={items}
                           openByDefault={b.openByDefault} />
            );
          })}
        </>
      ) : (
        !result.message && (
          <p className="scm-empty">No rules are on file for this section yet.</p>
        )
      )}



      <MistakesPanel mistakes={mistakes} />

      <WordingPanel wording={result.wording} />

      {/* The SOURCES are named by `score.basis`, which derives them from the
          rules that actually scored. Restating them here got it wrong -- it
          credited the PAPPG on sections where every rule came from the
          solicitation -- and put one fact in two places, which is the failure
          this modal has already had to unship once. What survives is the part
          `basis` does not carry. */}
      <p className="scm-disclaimer">
        The score counts only the rules that could be checked against your text
        &mdash; it is not a measure of how well written {result.label
          ? `your ${result.label}` : "this section"} is, or of how likely the
        proposal is to be funded.
      </p>

      <div className="scm-results-actions">
        <button className="scm-back" onClick={onBack}>Check another section</button>
      </div>
    </div>
  );
}
