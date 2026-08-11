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
import { createPortal } from "react-dom";
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

export default function SolicitationUploadModal({ onClose, onCreated, initialUrl = "",
                                                  submissionId = null }) {
  // step: "pick" -> "extracting" -> "review" -> "creating"
  const [step, setStep] = useState("pick");
  const [error, setError] = useState("");
  const [extracted, setExtracted] = useState(null);
  const [titleOverride, setTitleOverride] = useState("");
  const fileInputRef = useRef(null);
  // `submissionId` set = ATTACH MODE: the proposal already exists, so this
  // modal fills in its solicitation instead of creating anything.
  const attaching = submissionId != null;

  // The deep read of the whole solicitation, fired once the contract comes back
  // and the user lands on the review step. It is a separate request because it
  // takes 60-150s against a 300s request cap — far too long to sit inside the
  // contract call, and far too risky inside the save. Overlapping it with the
  // time the user already spends checking the deadline and cap makes it free.
  //
  // The File / URL is kept in state because the two requests are independent
  // and the backend runs many instances with no session affinity. The server
  // STORES the document's text on the first read and returns a source_id, which
  // is bound to the proposal at save — so this is the last time the PI is asked
  // for this document.
  const [source, setSource] = useState(null);      // {kind, file?, url?, filename?}
  const [reqState, setReqState] = useState("idle"); // idle|running|ready|failed
  const [requirements, setRequirements] = useState(null);

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
      readRequirements({ kind: "pdf", file, filename: file.name });
    } catch (e) {
      setError(e.message || "Couldn't read that PDF.");
      setStep("pick");
    }
  };

  // Read every requirement in the solicitation. Deliberately fire-and-display:
  // it never blocks Create, and a failure here costs the requirement list, not
  // the proposal.
  const readRequirements = async (src) => {
    setSource(src);
    setReqState("running");
    setRequirements(null);
    try {
      const form = new FormData();
      if (src.kind === "pdf") form.append("file", src.file, src.filename);
      else form.append("url", src.url);
      const res = await fetch(`${API_BASE}/api/me/solicitation-requirements`, {
        method: "POST", headers: authHeaders(), body: form,
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail || `${res.status} ${res.statusText}`);
      }
      setRequirements(await res.json());
      setReqState("ready");
    } catch (e) {
      setRequirements({ error: e.message || "Couldn't read the requirements." });
      setReqState("failed");
    }
  };

  const handleUrl = async (rawUrl) => {
    const url = (rawUrl || "").trim();
    if (!url) {
      setError("Please paste a solicitation URL.");
      return;
    }
    if (!/^https?:\/\//i.test(url)) {
      setError("Enter a full URL starting with http:// or https://");
      return;
    }

    setStep("extracting");
    setError("");
    try {
      const res = await fetch(
        `${API_BASE}/api/me/submissions/from-solicitation/url`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json", ...authHeaders() },
          body: JSON.stringify({ url }),
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
      readRequirements({ kind: "url", url });
    } catch (e) {
      setError(e.message || "Couldn't read that URL.");
      setStep("pick");
    }
  };

  const handleConfirm = async () => {
    setStep("creating");
    setError("");
    try {
      // The requirement list rides along ONLY when it is ready. This is the
      // save point for both flows, and the one place the solicitation is
      // written to the database.
      const solicitation = reqState === "ready" && requirements ? {
        requirements: requirements.requirements,
        merit_criteria: requirements.merit_criteria,
        eligibility_notes: requirements.eligibility_notes,
        read_report: requirements.read_report,
        extraction: requirements.extraction,
        source: { kind: source?.kind, filename: source?.filename || null,
                  url: source?.url || null },
        // Binds the stored document to this proposal. Without it the text is
        // kept but orphaned, and the PI would be asked for the file again.
        source_id: requirements.source_id,
      } : {};

      const res = attaching
        ? await fetch(`${API_BASE}/api/me/submissions/${submissionId}/solicitation`, {
            method: "PUT",
            headers: { "Content-Type": "application/json", ...authHeaders() },
            body: JSON.stringify({ extracted, ...solicitation }),
          })
        : await fetch(`${API_BASE}/api/me/submissions/from-solicitation/confirm`, {
            method: "POST",
            headers: { "Content-Type": "application/json", ...authHeaders() },
            body: JSON.stringify({
              extracted,
              title_override: titleOverride.trim() || null,
              ...solicitation,
            }),
          });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail || `${res.status} ${res.statusText}`);
      }
      const submission = await res.json();
      onCreated(submission);
    } catch (e) {
      setError(e.message || (attaching ? "Couldn't attach the solicitation."
                                       : "Couldn't create the proposal."));
      setStep("review");
    }
  };

  const updateExtracted = (field, value) => {
    setExtracted((cur) => ({ ...cur, [field]: value }));
  };

  // Rendered through a portal to <body> so the fixed overlay centers on the
  // viewport, not inside the fixed .page-content wrapper (which pinned it to
  // the top and pushed the bottom off-screen).
  return createPortal(
    <div className="solicitation-modal-overlay" onClick={onClose}>
      <div className="solicitation-modal" onClick={(e) => e.stopPropagation()}>
        <div className="solicitation-modal-header">
          {step === "review" ? (
            <button
              className="solicitation-back-btn"
              onClick={() => setStep("pick")}
            >
              <ArrowLeft size={11} /> Start over
            </button>
          ) : (
            <h2>{attaching ? "Attach the Solicitation" : "Start from a Solicitation"}</h2>
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
            initialUrl={initialUrl}
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
            attaching={attaching}
            reqState={reqState}
            requirements={requirements}
          />
        )}
      </div>
    </div>,
    document.body
  );
}

// ============================================================
// STEP 1 -- Pick a file
// ============================================================

function PickStep({ onFile, onUrl, fileInputRef, initialUrl = "" }) {
  const [dragOver, setDragOver] = useState(false);
  const [url, setUrl] = useState(initialUrl);

  return (
    <div className="solicitation-pick">
      <p className="solicitation-intro">
        Upload the solicitation PDF — or paste a link to it — from NSF, NIH,
        DoD, a foundation, or any sponsor. ORA Navigator will read it and
        pre-fill your proposal — deadline, page limits, required attachments,
        eligibility, budget cap, and submission portal. You'll review every
        field before anything is saved.
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

      <div className="solicitation-or">
        <span>or</span>
      </div>

      <form
        className="solicitation-url-row"
        onSubmit={(e) => {
          e.preventDefault();
          onUrl(url);
        }}
      >
        <input
          type="url"
          className="solicitation-url-input"
          value={url}
          onChange={(e) => setUrl(e.target.value)}
          placeholder="Paste a solicitation URL (funder page or PDF link)"
        />
        <button
          type="submit"
          className="btn-primary solicitation-url-btn"
          disabled={!url.trim()}
        >
          <LinkIcon size={11} /> Fetch &amp; extract
        </button>
      </form>

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

// What the whole-document read found, shown while the user checks the fields
// above it. This is the list the Draft Review will judge their draft against,
// so they get to see every ask and the sentence it came from BEFORE it is
// saved — the only real check on a requirement list a model produced.
function RequirementsPanel({ state, data }) {
  const [open, setOpen] = useState(false);
  if (state === "idle") return null;

  if (state === "running") {
    return (
      <div className="solicitation-requirements">
        <strong>Reading the whole solicitation…</strong>
        <p>
          Every requirement it states, quoted. This takes about a minute — keep
          checking the fields above, and you can create the proposal at any time.
        </p>
      </div>
    );
  }

  if (state === "failed") {
    return (
      <div className="solicitation-requirements">
        <strong>Couldn't read the requirements</strong>
        <p>
          {data?.error} You can still create the proposal and attach the
          solicitation later — nothing is lost.
        </p>
      </div>
    );
  }

  const rows = data?.requirements || [];
  const warnings = data?.warnings || [];
  const rr = data?.read_report || {};
  return (
    <div className="solicitation-requirements">
      <strong>
        {rows.length} requirement{rows.length === 1 ? "" : "s"} found
      </strong>
      {(rr.pages || rr.chars) && (
        <p className="solicitation-read-report">
          {rr.pages ? `${rr.pages} pages read` : `${rr.chars.toLocaleString()} characters read`}
          {rr.pages_without_text ? `, ${rr.pages_without_text} with no text layer` : ""}.
        </p>
      )}
      {warnings.map((w, i) => (
        <p key={i} className="solicitation-requirements-warning">{w}</p>
      ))}
      {rows.length > 0 && (
        <>
          <button type="button" className="solicitation-req-toggle"
                  onClick={() => setOpen((v) => !v)}>
            {open ? "Hide" : "Show"} what your draft will be checked against
          </button>
          {open && (
            <ul className="solicitation-req-list">
              {rows.map((r) => (
                <li key={r.id}>
                  <span className="solicitation-req-label">{r.label}</span>
                  <span className="solicitation-req-quote">
                    <Quote size={10} /> {r.source}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </>
      )}
    </div>
  );
}

function ReviewStep({
  extracted, titleOverride, onTitleChange, onChange,
  onConfirm, creating, onCancel, attaching = false,
  reqState = "idle", requirements = null,
}) {
  const sq = extracted.source_quotes || {};
  const unv = new Set(extracted.unverified_fields || []);
  const [verified, setVerified] = useState(false);
  return (
    <div className="solicitation-review">
      <p className="solicitation-review-intro">
        Review what the AI extracted. Edit anything that's wrong. Source quotes
        from the solicitation are shown for trust — if something looks made up,
        fix it before creating the proposal.
      </p>

      {/* Attach mode fills in an EXISTING proposal, so its title is not up for
          renaming here. */}
      {!attaching && (
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
      )}

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
        <Field label="Program ID" sourceQuote={sq.program_id} unverified={unv.has("program_id")}>
          <input
            type="text"
            value={extracted.program_id || ""}
            onChange={(e) => onChange("program_id", e.target.value)}
            placeholder="e.g. NSF 23-573"
          />
        </Field>
      </FieldRow>

      <FieldRow>
        <Field label="Deadline" critical sourceQuote={sq.deadline} unverified={unv.has("deadline")}>
          <input
            type="text"
            value={extracted.deadline || ""}
            onChange={(e) => onChange("deadline", e.target.value)}
            placeholder="YYYY-MM-DD or full ISO date"
          />
        </Field>
        <Field label="Budget cap (USD)" critical sourceQuote={sq.budget_cap} unverified={unv.has("budget_cap")}>
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

      {extracted.deadline_details && (
        <Field
          label="All deadlines (by category)"
          hint="This solicitation lists more than one deadline. The Deadline above is the earliest (most restrictive); the full list is saved to your proposal notes. If you're applying to a different category, set the Deadline to match."
        >
          <textarea
            value={extracted.deadline_details}
            onChange={(e) => onChange("deadline_details", e.target.value)}
            rows={2}
          />
        </Field>
      )}

      <Field
        label="Eligibility"
        sourceQuote={sq.eligibility}
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
        unverified={unv.has("submission_portal")}
      >
        <input
          type="text"
          value={extracted.submission_portal || ""}
          onChange={(e) => onChange("submission_portal", e.target.value)}
          placeholder="Research.gov / ASSIST / Grants.gov / ..."
        />
      </Field>

      <Field label="Required attachments" unverified={unv.has("required_attachments")}>
        <AttachmentEditor
          value={extracted.required_attachments || []}
          onChange={(v) => onChange("required_attachments", v)}
        />
        <small className="solicitation-hint">
          Each attachment becomes a task on your checklist. Add or remove as
          needed.
        </small>
      </Field>

      <Field label="Page limits" unverified={unv.has("page_limits")}>
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
          solicitation. (These are AI-extracted — one wrong value can miss
          or over-budget the proposal.)
        </span>
      </label>

      <RequirementsPanel state={reqState} data={requirements} />

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
          // NEVER blocked on the requirement read. If it is still running the
          // button says so and saves without the list; the solicitation can be
          // attached afterwards. Waiting on a slow read would be the one thing
          // that could lose the PI their work.
          disabled={creating || (!attaching && !titleOverride.trim()) || !verified}
          title={!verified
            ? "Confirm you've checked the deadline and budget cap first"
            : ""}
        >
          <Check size={11} />{" "}
          {creating
            ? (attaching ? "Attaching..." : "Creating...")
            : reqState === "running"
              ? (attaching ? "Attach without the requirement list"
                           : "Create without the requirement list")
              : (attaching ? "Attach to this proposal" : "Create Proposal")}
        </button>
      </div>
    </div>
  );
}

function Field({ label, hint, sourceQuote, critical, unverified, children }) {
  const cls = "solicitation-field"
    + (critical ? " solicitation-field-critical" : "")
    + (unverified ? " solicitation-field-unverified" : "");
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
      {critical && (
        <small className="solicitation-critical-note">
          ⚠ A wrong value here can miss the deadline or blow the budget — confirm it against the PDF.
        </small>
      )}
      {hint && <small className="solicitation-hint">{hint}</small>}
      {sourceQuote && (
        <div className="solicitation-quote">
          <Quote size={9} className="solicitation-quote-icon" />
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
