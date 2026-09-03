// SolicitationUploadModal.jsx
//
// Two-step UX for the "Drop a sponsor PDF and let the app set up your
// proposal" feature:
//   1. User picks a PDF.
//   2. Frontend POSTs the file to /api/me/submissions/from-solicitation
//      -> gets back the extracted JSON. (Loading state ~5-15s.)
//   3. User reviews + edits each field next to its source quote.
//   4. Click Create -> POST to .../confirm with the (possibly edited)
//      dict -> server creates the Submission + tasks.
//
// The two-step flow is the key safety property: the user always
// reviews what the AI pulled out before it becomes a real proposal.

import React, { useState, useRef } from "react";
import { ArrowLeft, Check, FileText, Link as LinkIcon, Quote, X } from "lucide-react";
import { getApiBase } from "../lib/apiBase";
import "./SolicitationUploadModal.css";

const API_BASE = getApiBase();

function authHeaders() {
  const token = localStorage.getItem("token");
  return token ? { Authorization: `Bearer ${token}` } : {};
}

const SPONSORS = ["NSF", "NIH", "DoD", "DoE", "NASA", "USDA", "EPA",
                  "Foundation", "State of Maryland", "Internal"];

export default function SolicitationUploadModal({ onClose, onCreated }) {
  // step: "pick" -> "extracting" -> "review" -> "creating"
  const [step, setStep] = useState("pick");
  const [error, setError] = useState("");
  const [extracted, setExtracted] = useState(null);
  const [titleOverride, setTitleOverride] = useState("");
  const fileInputRef = useRef(null);

  const handleFile = async (file) => {
    if (!file) return;
    if (!file.name.toLowerCase().endsWith(".pdf")) {
      setError("Please upload a PDF file.");
      return;
    }
    if (file.size > 25 * 1024 * 1024) {
      setError("File is larger than 25 MB.");
      return;
    }

    setStep("extracting");
    setError("");
    try {
      const formData = new FormData();
      formData.append("file", file);
      const res = await fetch(
        `${API_BASE}/api/me/submissions/from-solicitation`,
        { method: "POST", headers: authHeaders(), body: formData },
      );
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail || `${res.status} ${res.statusText}`);
      }
      const data = await res.json();
      setExtracted(data.extracted);
      setTitleOverride(
        data.extracted?.program_name || data.extracted?.program_id || "",
      );
      setStep("review");
    } catch (e) {
      setError(e.message || "Couldn't read that PDF.");
      setStep("pick");
    }
  };

  const handleUrl = async (url) => {
    const trimmed = (url || "").trim();
    if (!trimmed) {
      setError("Paste a solicitation URL first.");
      return;
    }
    if (!/^https:\/\//i.test(trimmed)) {
      setError("Enter a full https:// link.");
      return;
    }

    setStep("extracting");
    setError("");
    try {
      const res = await fetch(
        `${API_BASE}/api/me/submissions/from-solicitation-url`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json", ...authHeaders() },
          body: JSON.stringify({ url: trimmed }),
        },
      );
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail || `${res.status} ${res.statusText}`);
      }
      const data = await res.json();
      setExtracted(data.extracted);
      setTitleOverride(
        data.extracted?.program_name || data.extracted?.program_id || "",
      );
      setStep("review");
    } catch (e) {
      setError(e.message || "Couldn't read that link.");
      setStep("pick");
    }
  };

  const handleConfirm = async () => {
    setStep("creating");
    setError("");
    try {
      const res = await fetch(
        `${API_BASE}/api/me/submissions/from-solicitation/confirm`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json", ...authHeaders() },
          body: JSON.stringify({
            extracted,
            title_override: titleOverride.trim() || null,
          }),
        },
      );
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail || `${res.status} ${res.statusText}`);
      }
      const submission = await res.json();
      onCreated(submission);
    } catch (e) {
      setError(e.message || "Couldn't create the proposal.");
      setStep("review");
    }
  };

  const updateExtracted = (field, value) => {
    setExtracted((cur) => ({ ...cur, [field]: value }));
  };

  return (
    <div className="solicitation-modal-overlay" onClick={onClose}>
      <div className="solicitation-modal" onClick={(e) => e.stopPropagation()}>
        <div className="solicitation-modal-header">
          {step === "review" ? (
            <button
              className="solicitation-back-btn"
              onClick={() => setStep("pick")}
            >
              <ArrowLeft size={11} /> Re-upload
            </button>
          ) : (
            <h2>Start from a Solicitation</h2>
          )}
          <button className="solicitation-close-btn" onClick={onClose}>
            <X />
          </button>
        </div>

        {error && <div className="solicitation-error">{error}</div>}

        {step === "pick" && (
          <PickStep
            onFile={handleFile}
            onUrl={handleUrl}
            fileInputRef={fileInputRef}
          />
        )}

        {step === "extracting" && <ExtractingStep />}

        {(step === "review" || step === "creating") && extracted && (
          <ReviewStep
            extracted={extracted}
            titleOverride={titleOverride}
            onTitleChange={setTitleOverride}
            onChange={updateExtracted}
            onConfirm={handleConfirm}
            creating={step === "creating"}
            onCancel={onClose}
          />
        )}
      </div>
    </div>
  );
}

// ============================================================
// STEP 1 -- Pick a file
// ============================================================

function PickStep({ onFile, onUrl, fileInputRef }) {
  const [dragOver, setDragOver] = useState(false);
  const [mode, setMode] = useState("pdf"); // "pdf" | "url"
  const [url, setUrl] = useState("");

  return (
    <div className="solicitation-pick">
      <p className="solicitation-intro">
        Start from a solicitation from NSF, NIH, DoD, a foundation, or any
        sponsor — upload the PDF or paste a link. ORA Navigator will read it and
        pre-fill your proposal — deadline, page limits, required attachments,
        eligibility, budget cap, and submission portal. You'll review every
        field before anything is saved.
      </p>

      <div className="solicitation-mode-toggle" role="tablist">
        <button
          type="button"
          className={`solicitation-mode-tab ${mode === "pdf" ? "active" : ""}`}
          onClick={() => setMode("pdf")}
        >
          <FileText size={13} /> Upload PDF
        </button>
        <button
          type="button"
          className={`solicitation-mode-tab ${mode === "url" ? "active" : ""}`}
          onClick={() => setMode("url")}
        >
          <LinkIcon size={13} /> Paste URL
        </button>
      </div>

      {mode === "pdf" ? (
        <>
          <div
            className={`solicitation-drop ${dragOver ? "drag-over" : ""}`}
            onClick={() => fileInputRef.current?.click()}
            onDragOver={(e) => {
              e.preventDefault();
              setDragOver(true);
            }}
            onDragLeave={() => setDragOver(false)}
            onDrop={(e) => {
              e.preventDefault();
              setDragOver(false);
              if (e.dataTransfer.files?.[0]) onFile(e.dataTransfer.files[0]);
            }}
          >
            <FileText size={36} className="solicitation-drop-icon" />
            <div className="solicitation-drop-text">
              <b>Drop a PDF here</b> or click to browse
            </div>
            <div className="solicitation-drop-hint">PDF only · 25 MB max</div>
          </div>

          <input
            ref={fileInputRef}
            type="file"
            accept=".pdf,application/pdf"
            style={{ display: "none" }}
            onChange={(e) => onFile(e.target.files?.[0])}
          />

          <p className="solicitation-note">
            Tip: text-based PDFs work best. Scanned image-only PDFs may not
            extract — for those, create your proposal manually.
          </p>
        </>
      ) : (
        <>
          <div className="solicitation-field">
            <label>Solicitation URL</label>
            <input
              type="url"
              value={url}
              autoFocus
              placeholder="https://www.nsf.gov/funding/opportunities/..."
              onChange={(e) => setUrl(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") onUrl(url);
              }}
            />
          </div>

          <button
            className="solicitation-create-btn solicitation-fetch-btn"
            onClick={() => onUrl(url)}
            disabled={!url.trim()}
          >
            Fetch &amp; read
          </button>

          <p className="solicitation-note">
            Works for a direct PDF link and many funder pages. If a page needs a
            login or doesn't load (e.g. a JavaScript app like Grants.gov),
            download the PDF and use <b>Upload PDF</b> instead.
          </p>
        </>
      )}
    </div>
  );
}

// ============================================================
// STEP 2 -- Extracting (loading)
// ============================================================

function ExtractingStep() {
  return (
    <div className="solicitation-extracting">
      <div className="solicitation-spinner" />
      <h3>Reading your solicitation…</h3>
      <p>
        Going through it <b>page by page</b> — every page is read, not just the
        first few — pulling out the deadline, page limits, required attachments,
        budget cap, and formatting rules.
      </p>
      <p className="solicitation-extracting-time">
        A typical solicitation takes about 30 seconds. A long agency guide
        (200+ pages) can take up to two minutes. Nothing is skipped.
      </p>
    </div>
  );
}

// ============================================================
// STEP 3 -- Review & edit
// ============================================================

function ReviewStep({
  extracted, titleOverride, onTitleChange, onChange,
  onConfirm, creating, onCancel,
}) {
  const sq = extracted.source_quotes || {};
  const sp = extracted.source_pages || {};
  const unv = new Set(extracted.unverified_fields || []);
  const partial = extracted.partially_verified || {};
  const coverage = extracted.coverage || null;
  const [verified, setVerified] = useState(false);
  return (
    <div className="solicitation-review">
      <p className="solicitation-review-intro">
        Review what the AI extracted. Edit anything that's wrong. Source quotes
        from the PDF are shown for trust — if something looks made up, fix it
        before creating the proposal.
      </p>

      <CoverageBanner coverage={coverage} />

      <Field
        label="Proposal title"
        hint="What this proposal will be called in your tracker"
      >
        <input
          type="text"
          value={titleOverride}
          onChange={(e) => onTitleChange(e.target.value)}
          placeholder="e.g. NSF CAREER on microbial bioremediation"
        />
      </Field>

      <FieldRow>
        <Field label="Sponsor">
          <select
            value={extracted.sponsor || "Internal"}
            onChange={(e) => onChange("sponsor", e.target.value)}
          >
            {(extracted.sponsor && !SPONSORS.includes(extracted.sponsor)
              ? [extracted.sponsor, ...SPONSORS]
              : SPONSORS
            ).map((s) => <option key={s} value={s}>{s}</option>)}
          </select>
        </Field>
        <Field label="Program ID" sourceQuote={sq.program_id} sourcePage={sp.program_id} unverified={unv.has("program_id")}>
          <input
            type="text"
            value={extracted.program_id || ""}
            onChange={(e) => onChange("program_id", e.target.value)}
            placeholder="e.g. NSF 23-573"
          />
        </Field>
      </FieldRow>

      <FieldRow>
        <Field label="Deadline" critical sourceQuote={sq.deadline} sourcePage={sp.deadline} unverified={unv.has("deadline")}>
          <input
            type="text"
            value={extracted.deadline || ""}
            onChange={(e) => onChange("deadline", e.target.value)}
            placeholder="YYYY-MM-DD or full ISO date"
          />
        </Field>
        <Field label="Budget cap (USD)" critical sourceQuote={sq.budget_cap} sourcePage={sp.budget_cap} unverified={unv.has("budget_cap")}>
          <input
            type="number"
            value={extracted.budget_cap ?? ""}
            onChange={(e) => onChange(
              "budget_cap",
              e.target.value === "" ? null : Number(e.target.value),
            )}
            placeholder="e.g. 600000"
          />
        </Field>
      </FieldRow>

      <Field
        label="Eligibility"
        sourceQuote={sq.eligibility}
        sourcePage={sp.eligibility}
        unverified={unv.has("eligibility")}
      >
        <textarea
          value={extracted.eligibility || ""}
          onChange={(e) => onChange("eligibility", e.target.value)}
          rows={2}
          placeholder="Who can apply"
        />
      </Field>

      <Field
        label="Submission portal"
        sourceQuote={sq.submission_portal}
        sourcePage={sp.submission_portal}
        unverified={unv.has("submission_portal")}
      >
        <input
          type="text"
          value={extracted.submission_portal || ""}
          onChange={(e) => onChange("submission_portal", e.target.value)}
          placeholder="Research.gov / ASSIST / Grants.gov / ..."
        />
      </Field>

      <Field
        label="Required attachments"
        unverified={unv.has("required_attachments")}
        partial={partial.required_attachments}
        sourceQuote={sq.required_attachments}
        sourcePage={sp.required_attachments}
      >
        <AttachmentEditor
          value={extracted.required_attachments || []}
          onChange={(v) => onChange("required_attachments", v)}
        />
        <small className="solicitation-hint">
          Each attachment becomes a task on your checklist. Add or remove as
          needed.
        </small>
      </Field>

      <Field
        label="Page limits"
        unverified={unv.has("page_limits")}
        partial={partial.page_limits}
        sourceQuote={sq.page_limits}
        sourcePage={sp.page_limits}
      >
        <PageLimitsDisplay value={extracted.page_limits || {}} />
        <small className="solicitation-hint">
          These are the limits for a standard full proposal. Carried into your
          proposal notes and checked against your draft later.
        </small>
      </Field>

      <VariantList
        label="Page limits for special proposal types"
        rows={extracted.page_limit_variants}
        render={(v) => `${v.section}: ${v.pages} ${v.pages === 1 ? "page" : "pages"}`}
      />

      <VariantList
        label="Budget caps for special proposal types"
        rows={extracted.budget_cap_variants}
        render={(v) => `$${Number(v.amount).toLocaleString()}`}
      />

      <FormattingDisplay formatting={extracted.formatting} page={sp.formatting} />

      <RequirementsList rows={extracted.other_requirements} />

      <label className="solicitation-verify">
        <input
          type="checkbox"
          checked={verified}
          onChange={(e) => setVerified(e.target.checked)}
          disabled={creating}
        />
        <span>
          I've checked the <b>deadline</b> and <b>budget cap</b> against the
          solicitation PDF. (These are AI-extracted — one wrong value can miss
          or over-budget the proposal.)
        </span>
      </label>

      <div className="solicitation-actions">
        <button
          type="button"
          className="btn-secondary"
          onClick={onCancel}
          disabled={creating}
        >
          Cancel
        </button>
        <button
          type="button"
          className="btn-primary"
          onClick={onConfirm}
          disabled={creating || !titleOverride.trim() || !verified}
          title={!verified
            ? "Confirm you've checked the deadline and budget cap first"
            : ""}
        >
          <Check size={11} />{" "}
          {creating ? "Creating..." : "Create Proposal"}
        </button>
      </div>
    </div>
  );
}

// Proof that the whole document was read, not just the opening pages. The
// extractor slices the PDF so every page reaches the model, and reports back
// exactly how many it processed -- so this is a measurement, not a promise.
function CoverageBanner({ coverage }) {
  if (!coverage || !coverage.pages_total) return null;
  const { pages_total: total, pages_read: read, slices_failed: failed } = coverage;
  const complete = read >= total && !failed;
  return (
    <div className={"solicitation-coverage" + (complete ? "" : " solicitation-coverage-partial")}>
      {complete ? (
        <>
          <Check size={11} />
          <span>
            Read <b>all {total} pages</b> of your solicitation, page by page.
          </span>
        </>
      ) : (
        <span>
          ⚠ Read <b>{read} of {total} pages</b>
          {failed ? ` (${failed} section${failed === 1 ? "" : "s"} failed to process)` : ""}.
          Some requirements may be missing — check anything that looks incomplete.
        </span>
      )}
    </div>
  );
}

// Every other MUST / MUST-NOT in the solicitation: budget percentage caps,
// prohibited costs, required travel, proposal-count limits, character limits,
// naming conventions, content that has to appear inside a named component.
// Before this list existed they were simply lost -- a measured audit of NSF
// 23-598 found only 41% of its 34 hard requirements survived extraction, and
// every loss traced to having no field to put them in.
function RequirementsList({ rows }) {
  if (!Array.isArray(rows) || rows.length === 0) return null;
  const groups = rows.reduce((acc, r) => {
    const k = r.category || "other";
    (acc[k] = acc[k] || []).push(r);
    return acc;
  }, {});
  const LABELS = {
    budget: "Budget", eligibility: "Eligibility", submission: "Submission",
    content: "Proposal content", process: "Process", format: "Formatting",
    other: "Other",
  };
  return (
    <div className="solicitation-field solicitation-reqs">
      <label>Other requirements in this solicitation ({rows.length})</label>
      {Object.entries(groups).map(([cat, items]) => (
        <div key={cat} className="solicitation-req-group">
          <div className="solicitation-req-cat">{LABELS[cat] || cat}</div>
          <ul className="solicitation-req-list">
            {items.map((r, i) => (
              <li key={i} title={r.quote || ""}>
                <span>{r.requirement}</span>
                {r.page ? <span className="solicitation-variant-page">p.{r.page}</span> : null}
              </li>
            ))}
          </ul>
        </div>
      ))}
      <small className="solicitation-hint">
        Saved with your proposal so you can check the draft against them. Hover
        any line to see the sentence it came from.
      </small>
    </div>
  );
}

// Rules that apply only to a special proposal type (RAPID, EAGER, planning,
// Ideas Lab...). Kept OUT of the headline numbers on purpose: applying a
// 2-page Ideas Lab limit to an ordinary 15-page proposal would be wrong.
// Shown here so nothing found in the document is lost.
function VariantList({ label, rows, render }) {
  if (!Array.isArray(rows) || rows.length === 0) return null;
  return (
    <div className="solicitation-field solicitation-variants">
      <label>{label}</label>
      <ul className="solicitation-variant-list">
        {rows.map((v, i) => (
          <li key={i}>
            <span className="solicitation-variant-applies">{v.applies_to}</span>
            <span className="solicitation-variant-value">{render(v)}</span>
            {v.page ? <span className="solicitation-variant-page">p.{v.page}</span> : null}
          </li>
        ))}
      </ul>
      <small className="solicitation-hint">
        These do <b>not</b> apply to a standard full proposal — they're listed so
        you have them if you're submitting one of these types.
      </small>
    </div>
  );
}

// Font / margin / spacing rules. Sponsors return proposals without review over
// these, and until now they were read out of the PDF and then discarded.
function FormattingDisplay({ formatting, page }) {
  const f = formatting || {};
  const rows = [
    ["Font", f.font],
    ["Margins", f.margins],
    ["Line spacing", f.line_spacing],
  ].filter(([, v]) => v);
  if (rows.length === 0) return null;
  return (
    <div className="solicitation-field">
      <label>
        Formatting rules
        {page ? <span className="solicitation-variant-page">p.{page}</span> : null}
      </label>
      <dl className="solicitation-formatting">
        {rows.map(([k, v]) => (
          <div key={k}>
            <dt>{k}</dt>
            <dd>{v}</dd>
          </div>
        ))}
      </dl>
      <small className="solicitation-hint">
        Your draft is checked against these when you run Critique Draft.
      </small>
    </div>
  );
}

// A field's supporting evidence. The extractor may return either one quote for
// the field or an object of one quote PER ENTRY (page limits usually do) --
// rendering that object directly would crash React, so handle both shapes.
function SourceQuote({ quote, page }) {
  if (!quote) return null;
  const entries = typeof quote === "object" && !Array.isArray(quote)
    ? Object.entries(quote)
    : [[null, quote]];
  // Never hand React a raw object: the backend normalizes source_pages to a
  // number, but a stale saved extraction (or a future contract change) must
  // degrade to "no page shown" rather than blanking the whole modal.
  const pageLabel =
    typeof page === "number" || typeof page === "string" ? page : null;
  return (
    <div className="solicitation-quote">
      <Quote size={9} className="solicitation-quote-icon" />
      <div className="solicitation-quote-body">
        {entries.map(([k, v], i) => (
          <div key={i}>
            {k && <b className="solicitation-quote-key">{k}: </b>}
            <span>{String(v)}</span>
          </div>
        ))}
        {pageLabel ? <span className="solicitation-quote-page">page {pageLabel}</span> : null}
      </div>
    </div>
  );
}

function Field({ label, hint, sourceQuote, sourcePage, critical, unverified,
                 partial, children }) {
  const partialList = Array.isArray(partial) ? partial : [];
  const cls = "solicitation-field"
    + (critical ? " solicitation-field-critical" : "")
    + (unverified ? " solicitation-field-unverified" : "")
    + (!unverified && partialList.length ? " solicitation-field-partial" : "");
  return (
    <div className={cls}>
      <label>
        {label}
        {critical && <span className="solicitation-critical-tag">verify</span>}
        {unverified && <span className="solicitation-unverified-tag">unverified</span>}
      </label>
      {children}
      {unverified && (
        <small className="solicitation-unverified-note">
          ⚠ The AI couldn’t back this with a quote from the PDF — double-check it before saving.
        </small>
      )}
      {!unverified && partialList.length > 0 && (
        <small className="solicitation-partial-note">
          ⚠ Backed by the PDF, except: <b>{partialList.join(", ")}</b> — check
          just {partialList.length === 1 ? "that one" : "those"}.
        </small>
      )}
      {critical && (
        <small className="solicitation-critical-note">
          ⚠ A wrong value here can miss the deadline or blow the budget — confirm it against the PDF.
        </small>
      )}
      {hint && <small className="solicitation-hint">{hint}</small>}
      <SourceQuote quote={sourceQuote} page={sourcePage} />
    </div>
  );
}

function FieldRow({ children }) {
  return <div className="solicitation-field-row">{children}</div>;
}

function AttachmentEditor({ value, onChange }) {
  const [newItem, setNewItem] = useState("");
  const add = () => {
    const t = newItem.trim();
    if (!t) return;
    onChange([...value, t]);
    setNewItem("");
  };
  const remove = (i) => onChange(value.filter((_, idx) => idx !== i));
  return (
    <div className="solicitation-attachments">
      {value.map((a, i) => (
        <span key={i} className="solicitation-attachment-tag">
          {a}
          <button
            type="button"
            className="solicitation-attachment-remove"
            onClick={() => remove(i)}
            aria-label="Remove"
          >
            <X size={10} />
          </button>
        </span>
      ))}
      <div className="solicitation-attachment-add">
        <input
          type="text"
          value={newItem}
          onChange={(e) => setNewItem(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              e.preventDefault();
              add();
            }
          }}
          placeholder="Add an attachment..."
        />
        <button type="button" onClick={add}>Add</button>
      </div>
    </div>
  );
}

function PageLimitsDisplay({ value }) {
  const entries = Object.entries(value);
  if (entries.length === 0) {
    return (
      <div className="solicitation-pagelimits-empty">
        No page limits extracted from this PDF.
      </div>
    );
  }
  return (
    <div className="solicitation-pagelimits">
      {entries.map(([section, n]) => (
        <span key={section} className="solicitation-pagelimit">
          <b>{section}:</b> {n}p
        </span>
      ))}
    </div>
  );
}
