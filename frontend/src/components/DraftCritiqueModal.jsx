// DraftCritiqueModal.jsx
//
// Two-step UX for the "Critique my draft" feature:
//   1. User drops a draft PDF onto a Submission they're already tracking.
//   2. Frontend POSTs the file to
//      /api/me/submissions/{id}/critique
//      -> backend reconstructs the solicitation context from the
//      submission's notes + tasks, runs Draft Critic, returns the
//      structured check list.
//   3. Modal renders each check as a row with an OK / WARN / FAIL chip
//      so the user can see in seconds where the draft falls short.
//
// Draft Critic is deterministic (no LLM), so every flagged issue is a
// real, verifiable property of the PDF -- no hallucinations to second-
// guess.

import React, { useState, useRef } from "react";
import { FaTimes } from "@react-icons/all-files/fa/FaTimes";
import { FaFilePdf } from "@react-icons/all-files/fa/FaFilePdf";
import { FaCheck } from "@react-icons/all-files/fa/FaCheck";
import { FaExclamationTriangle } from "@react-icons/all-files/fa/FaExclamationTriangle";
import { FaTimesCircle } from "@react-icons/all-files/fa/FaTimesCircle";
import { FaMinusCircle } from "@react-icons/all-files/fa/FaMinusCircle";
import { FaArrowLeft } from "@react-icons/all-files/fa/FaArrowLeft";
import { getApiBase } from "../lib/apiBase";
import "./DraftCritiqueModal.css";

const API_BASE = getApiBase();

function authHeaders() {
  const token = localStorage.getItem("token");
  return token ? { Authorization: `Bearer ${token}` } : {};
}

const STATUS_META = {
  ok:      { label: "OK",       Icon: FaCheck,                cls: "status-ok" },
  warn:    { label: "Warn",     Icon: FaExclamationTriangle,  cls: "status-warn" },
  fail:    { label: "Fail",     Icon: FaTimesCircle,          cls: "status-fail" },
  skipped: { label: "Skipped",  Icon: FaMinusCircle,          cls: "status-skip" },
};

function StatusChip({ status }) {
  const meta = STATUS_META[status] || STATUS_META.skipped;
  const { Icon } = meta;
  return (
    <span className={`critique-status-chip ${meta.cls}`}>
      <Icon /> {meta.label}
    </span>
  );
}

export default function DraftCritiqueModal({ submission, onClose }) {
  // step: "pick" -> "checking" -> "results"
  const [step, setStep] = useState("pick");
  const [error, setError] = useState("");
  const [result, setResult] = useState(null);
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

    setStep("checking");
    setError("");
    try {
      const formData = new FormData();
      formData.append("file", file);
      const res = await fetch(
        `${API_BASE}/api/me/submissions/${submission.id}/critique`,
        { method: "POST", headers: authHeaders(), body: formData },
      );
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail || `${res.status} ${res.statusText}`);
      }
      const data = await res.json();
      setResult(data);
      setStep("results");
    } catch (e) {
      setError(e.message || "Couldn't critique that draft.");
      setStep("pick");
    }
  };

  const onDrop = (e) => {
    e.preventDefault();
    if (e.dataTransfer?.files?.[0]) handleFile(e.dataTransfer.files[0]);
  };
  const onDragOver = (e) => e.preventDefault();
  const triggerPicker = () => fileInputRef.current?.click();

  return (
    <div className="critique-modal-overlay" onClick={onClose}>
      <div
        className="critique-modal"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="critique-modal-header">
          {step === "results" && (
            <button
              className="critique-back-btn"
              onClick={() => {
                setResult(null);
                setStep("pick");
              }}
            >
              <FaArrowLeft /> Back
            </button>
          )}
          <h2>
            {step === "pick" && "Critique your draft"}
            {step === "checking" && "Checking draft..."}
            {step === "results" && "Critique results"}
          </h2>
          <button className="critique-close-btn" onClick={onClose} aria-label="Close">
            <FaTimes />
          </button>
        </div>

        {step === "pick" && (
          <>
            <p className="critique-subtitle">
              Drop your draft PDF here. We'll check it against{" "}
              <strong>{submission.title}</strong>'s solicitation
              requirements (page limit, required attachments, budget cap,
              and standard sections). No AI guessing -- every check is a
              direct read of your PDF.
            </p>

            <div
              className="critique-dropzone"
              onClick={triggerPicker}
              onDrop={onDrop}
              onDragOver={onDragOver}
              role="button"
              tabIndex={0}
            >
              <FaFilePdf className="critique-dropzone-icon" />
              <div>
                <strong>Drag your draft PDF here</strong>
                <span className="critique-dropzone-hint">
                  or click to browse (max 25 MB)
                </span>
              </div>
              <input
                ref={fileInputRef}
                type="file"
                accept="application/pdf,.pdf"
                style={{ display: "none" }}
                onChange={(e) => handleFile(e.target.files?.[0])}
              />
            </div>

            {error && <div className="critique-error">{error}</div>}
          </>
        )}

        {step === "checking" && (
          <div className="critique-loading">
            <div className="critique-spinner" />
            <p>Reading your PDF and running checks...</p>
          </div>
        )}

        {step === "results" && result && (
          <ResultsView result={result} />
        )}
      </div>
    </div>
  );
}

const VERDICT_CLASS = {
  ready: "verdict-ready",
  minor: "verdict-minor",
  needs_review: "verdict-warn",
  critical: "verdict-fail",
};

function VerdictBanner({ verdict }) {
  if (!verdict) return null;
  const cls = VERDICT_CLASS[verdict.level] || "verdict-minor";
  return (
    <div className={`critique-verdict ${cls}`}>
      <div className="critique-verdict-label">{verdict.label}</div>
      <div className="critique-verdict-message">{verdict.message}</div>
    </div>
  );
}

function ResultsView({ result }) {
  const c = result.critique;
  const counts = c.counts || {};
  const pageStr = c.pages === 1 ? "1 page" : `${c.pages} pages`;
  const issueStr = c.issues === 1 ? "1 issue" : `${c.issues} issues`;
  return (
    <div className="critique-results">
      <VerdictBanner verdict={c.verdict} />

      <div className="critique-summary">
        <span className="critique-summary-piece status-ok">
          <FaCheck /> {counts.ok || 0} OK
        </span>
        <span className="critique-summary-piece status-warn">
          <FaExclamationTriangle /> {counts.warn || 0} Warn
        </span>
        <span className="critique-summary-piece status-fail">
          <FaTimesCircle /> {counts.fail || 0} Fail
        </span>
        <span className="critique-summary-piece status-skip">
          <FaMinusCircle /> {counts.skipped || 0} Skipped
        </span>
      </div>

      <div className="critique-meta">
        {pageStr} &middot; sponsor {c.sponsor || "Unknown"} &middot;{" "}
        {issueStr} found
      </div>

      <div className="critique-check-list">
        {c.checks.map((check, idx) => (
          <CheckRow key={idx} check={check} />
        ))}
      </div>

      <p className="critique-disclaimer">
        Draft Critic runs mechanical checks only -- it doesn't grade
        scientific quality or writing. Always read the solicitation
        yourself before submitting.
      </p>
    </div>
  );
}

function CheckRow({ check }) {
  return (
    <div className={`critique-check-row ${check.status}`}>
      <div className="critique-check-header">
        <div className="critique-check-name">{check.name}</div>
        <StatusChip status={check.status} />
      </div>
      {check.value && (
        <div className="critique-check-value">{check.value}</div>
      )}
      {check.detail && (
        <div className="critique-check-detail">{check.detail}</div>
      )}
      {(check.missing?.length > 0 || check.found?.length > 0) && (
        <div className="critique-check-lists">
          {check.found?.length > 0 && (
            <div>
              <strong>Found:</strong> {check.found.join(", ")}
            </div>
          )}
          {check.missing?.length > 0 && (
            <div className="critique-missing">
              <strong>Missing:</strong> {check.missing.join(", ")}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
