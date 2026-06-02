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
import { FaTimes } from "@react-icons/all-files/fa/FaTimes";
import { FaFilePdf } from "@react-icons/all-files/fa/FaFilePdf";
import { FaCheck } from "@react-icons/all-files/fa/FaCheck";
import { FaArrowLeft } from "@react-icons/all-files/fa/FaArrowLeft";
import { FaQuoteLeft } from "@react-icons/all-files/fa/FaQuoteLeft";
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
              <FaArrowLeft size={11} /> Re-upload
            </button>
          ) : (
            <h2>Start from a Solicitation PDF</h2>
          )}
          <button className="solicitation-close-btn" onClick={onClose}>
            <FaTimes />
          </button>
        </div>

        {error && <div className="solicitation-error">{error}</div>}

        {step === "pick" && (
          <PickStep
            onFile={handleFile}
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

function PickStep({ onFile, fileInputRef }) {
  const [dragOver, setDragOver] = useState(false);

  return (
    <div className="solicitation-pick">
      <p className="solicitation-intro">
        Upload the solicitation PDF from NSF, NIH, DoD, a foundation, or any
        sponsor. ORA Navigator will read it and pre-fill your proposal —
        deadline, page limits, required attachments, eligibility, budget cap,
        and submission portal. You'll review every field before anything is
        saved.
      </p>

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
        <FaFilePdf size={36} className="solicitation-drop-icon" />
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
        Pulling out the deadline, page limits, required attachments, and budget
        cap. This usually takes 5 to 15 seconds.
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
  const [verified, setVerified] = useState(false);
  return (
    <div className="solicitation-review">
      <p className="solicitation-review-intro">
        Review what the AI extracted. Edit anything that's wrong. Source quotes
        from the PDF are shown for trust — if something looks made up, fix it
        before creating the proposal.
      </p>

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
        <Field label="Program ID" sourceQuote={sq.program_id}>
          <input
            type="text"
            value={extracted.program_id || ""}
            onChange={(e) => onChange("program_id", e.target.value)}
            placeholder="e.g. NSF 23-573"
          />
        </Field>
      </FieldRow>

      <FieldRow>
        <Field label="Deadline" critical sourceQuote={sq.deadline}>
          <input
            type="text"
            value={extracted.deadline || ""}
            onChange={(e) => onChange("deadline", e.target.value)}
            placeholder="YYYY-MM-DD or full ISO date"
          />
        </Field>
        <Field label="Budget cap (USD)" critical sourceQuote={sq.budget_cap}>
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
      >
        <input
          type="text"
          value={extracted.submission_portal || ""}
          onChange={(e) => onChange("submission_portal", e.target.value)}
          placeholder="Research.gov / ASSIST / Grants.gov / ..."
        />
      </Field>

      <Field label="Required attachments">
        <AttachmentEditor
          value={extracted.required_attachments || []}
          onChange={(v) => onChange("required_attachments", v)}
        />
        <small className="solicitation-hint">
          Each attachment becomes a task on your checklist. Add or remove as
          needed.
        </small>
      </Field>

      <Field label="Page limits">
        <PageLimitsDisplay value={extracted.page_limits || {}} />
        <small className="solicitation-hint">
          Carried into your proposal notes for reference.
        </small>
      </Field>

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
          <FaCheck size={11} />{" "}
          {creating ? "Creating..." : "Create Proposal"}
        </button>
      </div>
    </div>
  );
}

function Field({ label, hint, sourceQuote, critical, children }) {
  return (
    <div className={`solicitation-field${critical ? " solicitation-field-critical" : ""}`}>
      <label>
        {label}
        {critical && <span className="solicitation-critical-tag">verify</span>}
      </label>
      {children}
      {critical && (
        <small className="solicitation-critical-note">
          ⚠ A wrong value here can miss the deadline or blow the budget — confirm it against the PDF.
        </small>
      )}
      {hint && <small className="solicitation-hint">{hint}</small>}
      {sourceQuote && (
        <div className="solicitation-quote">
          <FaQuoteLeft size={9} className="solicitation-quote-icon" />
          <span>{sourceQuote}</span>
        </div>
      )}
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
            <FaTimes size={10} />
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
