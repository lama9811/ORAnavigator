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

import React, { useState, useRef, useEffect } from "react";
import { createPortal } from "react-dom";
import { ArrowLeft, Check, FileText, Link as LinkIcon, Quote, X } from "lucide-react";
import { getApiBase } from "../lib/apiBase";
import { timeAgo } from "../lib/timeAgo";
import "./SolicitationUploadModal.css";

const API_BASE = getApiBase();

function authHeaders() {
  const token = localStorage.getItem("token");
  return token ? { Authorization: `Bearer ${token}` } : {};
}

const SPONSORS = ["NSF", "NIH", "DoD", "DoE", "NASA", "USDA", "EPA",
                  "Foundation", "State of Maryland", "Internal"];

// How long Create will wait for a requirement read that is already in flight.
// Deliberately short: it exists to catch a read that lands in the same instant
// as the click, NOT to make the PI wait out a 60-150s read. Anything longer
// would re-introduce the exact risk the "never blocked on the read" rule
// exists to prevent.
const REQUIREMENT_GRACE_MS = 2000;

export default function SolicitationUploadModal({ onClose, onCreated, initialUrl = "",
                                                  submissionId = null,
                                                  initialSourceId = null,
                                                  onLateAttach = null }) {
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
  // The stored document's id, returned by the CONTRACT read — i.e. as soon as
  // the PI hands the file over, before the slow requirement read has started.
  // It is sent at save no matter how the requirement read ends, which is the
  // whole point: a PI who clicks Create early used to leave the text orphaned
  // and get asked for the same file again from Draft Review.
  const [sourceId, setSourceId] = useState(null);

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
      setSourceId(data.source_id || null);
      setTitleOverride(
        data.extracted?.program_name || data.extracted?.program_id || "",
      );
      setStep("review");
      readRequirements({ kind: "pdf", file, filename: file.name,
                         sourceId: data.source_id || null });
    } catch (e) {
      setError(e.message || "Couldn't read that PDF.");
      setStep("pick");
    }
  };

  // THE SAME READ, MIRRORED INTO A REF, and the ref is what the save reads.
  //
  // handleConfirm used to gate on `reqState` — a value captured by the render
  // that painted the Create button. A read landing between that paint and the
  // click was therefore thrown away while sitting in memory. Observed in
  // production: the requirement read returned 200 at 14:49:23.9 and Create hit
  // the server at 14:49:24.1, so the proposal was written with no requirements
  // and the PI paid a second 28s read to recover a list we already had.
  //
  // A ref is read at CLICK time, so the window closes entirely.
  const readRef = useRef({ state: "idle", data: null, promise: null });

  // Read every requirement in the solicitation. Deliberately fire-and-display:
  // it never blocks Create, and a failure here costs the requirement list, not
  // the proposal.
  const readRequirements = (src) => {
    setSource(src);
    setReqState("running");
    setRequirements(null);
    readRef.current = { state: "running", data: null, promise: null };
    const run = (async () => {
      try {
        const form = new FormData();
        // The contract step already read and STORED this document. Reading it
        // back beats re-uploading it: one read of the file, one row per
        // solicitation, and no second trip over a 25MB PDF.
        if (src.sourceId) form.append("source_id", String(src.sourceId));
        else if (src.kind === "pdf") form.append("file", src.file, src.filename);
        else form.append("url", src.url);
        const res = await fetch(`${API_BASE}/api/me/solicitation-requirements`, {
          method: "POST", headers: authHeaders(), body: form,
        });
        if (!res.ok) {
          const body = await res.json().catch(() => ({}));
          throw new Error(body.detail || `${res.status} ${res.statusText}`);
        }
        const data = await res.json();
        readRef.current = { state: "ready", data, promise: readRef.current.promise };
        setRequirements(data);
        setReqState("ready");
      } catch (e) {
        const failed = { error: e.message || "Couldn't read the requirements." };
        readRef.current = { state: "failed", data: failed, promise: readRef.current.promise };
        setRequirements(failed);
        setReqState("failed");
      }
    })();
    readRef.current.promise = run;
    return run;
  };

  // Reuse a document this PI already read but never attached. Same request as
  // the upload path with source_id instead of a file, so everything downstream
  // — the review step, the requirement read, the save — is unchanged. The point
  // is not speed (the model call still runs); it is never making someone hunt
  // for a file we are already holding.
  const handleStoredSource = async (src) => {
    setStep("extracting");
    setError("");
    try {
      const form = new FormData();
      form.append("source_id", String(src.id));
      const res = await fetch(`${API_BASE}/api/me/submissions/from-solicitation`,
                              { method: "POST", headers: authHeaders(), body: form });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail || `${res.status} ${res.statusText}`);
      }
      const data = await res.json();
      setExtracted(data.extracted);
      setSourceId(data.source_id || null);
      setTitleOverride(data.extracted?.program_name || data.extracted?.program_id || "");
      setStep("review");
      readRequirements({ kind: src.url ? "url" : "pdf", url: src.url,
                         filename: src.filename, sourceId: data.source_id || null });
    } catch (e) {
      // The reaper may have removed it between listing and picking. Falling back
      // to the dropzone is the honest outcome — never a dead end.
      setError(e.message || "That stored solicitation is no longer available.");
      setStep("pick");
    }
  };

  // Draft Review already offered this document and the PI said yes, so opening
  // on the file picker would ask the same question twice. Runs once; a failure
  // falls back to the picker like any other bad source_id.
  const autoRan = useRef(false);
  useEffect(() => {
    if (!initialSourceId || autoRan.current) return;
    autoRan.current = true;
    handleStoredSource({ id: initialSourceId });
    // handleStoredSource is deliberately out of the deps: it is redefined every
    // render, and the ref above already makes this run exactly once. Including
    // it would re-fire the extraction on each render.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [initialSourceId]);

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
      setSourceId(data.source_id || null);
      setTitleOverride(
        data.extracted?.program_name || data.extracted?.program_id || "",
      );
      setStep("review");
      readRequirements({ kind: "url", url, sourceId: data.source_id || null });
    } catch (e) {
      setError(e.message || "Couldn't read that URL.");
      setStep("pick");
    }
  };

  const handleConfirm = async () => {
    setStep("creating");
    setError("");
    try {
      // STILL never blocked on the read — waiting out a 150s call is the one
      // thing that could lose the PI their work, and that rule stands. But a
      // read that is a moment from landing is not a slow read, and discarding
      // it costs them a second 28s pass over the same document. So an IN-FLIGHT
      // read gets a short, bounded grace and nothing more; if it has not
      // arrived by then we save without it exactly as before.
      //
      // Saving WITH a list after the button offered to save without one is not
      // a broken promise — it is strictly the outcome the PI wanted.
      if (readRef.current.state === "running" && readRef.current.promise) {
        await Promise.race([
          readRef.current.promise,
          new Promise((resolve) => setTimeout(resolve, REQUIREMENT_GRACE_MS)),
        ]);
      }
      // Read at CLICK time, never from a render snapshot — see readRef above.
      const read = readRef.current;
      // The requirement list rides along ONLY when it is ready. This is the
      // save point for both flows, and the one place the solicitation is
      // written to the database.
      const solicitation = read.state === "ready" && read.data ? {
        requirements: read.data.requirements,
        merit_criteria: read.data.merit_criteria,
        eligibility_notes: read.data.eligibility_notes,
        read_report: read.data.read_report,
        extraction: read.data.extraction,
        source: { kind: source?.kind, filename: source?.filename || null,
                  url: source?.url || null },
      } : {};
      // The DOCUMENT is bound unconditionally, outside that gate. It used to sit
      // inside it, so a PI who hit Create before the 60-150s read finished — or
      // whose read failed — left the stored text orphaned and was asked to
      // upload the very same file again when they opened Draft Review. Binding
      // it here means the proposal owns the document whatever the read did.
      const boundSourceId = sourceId || read.data?.source_id || null;

      const res = attaching
        ? await fetch(`${API_BASE}/api/me/submissions/${submissionId}/solicitation`, {
            method: "PUT",
            headers: { "Content-Type": "application/json", ...authHeaders() },
            body: JSON.stringify({ extracted, ...solicitation,
                                   source_id: boundSourceId }),
          })
        : await fetch(`${API_BASE}/api/me/submissions/from-solicitation/confirm`, {
            method: "POST",
            headers: { "Content-Type": "application/json", ...authHeaders() },
            body: JSON.stringify({
              extracted,
              title_override: titleOverride.trim() || null,
              ...solicitation,
              source_id: boundSourceId,
            }),
          });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail || `${res.status} ${res.statusText}`);
      }
      const submission = await res.json();
      onCreated(submission);

      // DELIVER A LATE READ INSTEAD OF DISCARDING IT.
      //
      // Create never blocks on the requirement read -- waiting out 60-150s is
      // the one thing that could lose a PI their work, and that rule does not
      // change here. What changes is what happens to a read that lands AFTER
      // the save. It used to be thrown away.
      //
      // Measured on a real proposal: the document was read at 19:18:02, the PI
      // clicked Create at 19:18:38, and the requirement read finished into a
      // closing modal. The proposal kept the funder's numbers and the document
      // and no requirement list -- so Draft Review 409'd, the badge read "rules
      // only", and Section Check offered the rulebook's sections alone. One
      // attach has to reach every tool.
      //
      // The PUT is the SAME endpoint the attach flow uses, and re-attaching is
      // keyed by requirement id, so a task the PI ticked off in the intervening
      // minute survives (tests/test_late_requirement_attach.py). A failure here
      // changes nothing: the proposal already exists and can be re-attached by
      // hand, so this never surfaces an error over work that succeeded.
      const lateId = submission?.id || submissionId;
      if (lateId && readRef.current.state === "running" && readRef.current.promise) {
        readRef.current.promise
          .then(async () => {
            const late = readRef.current;
            if (late.state !== "ready" || !late.data?.requirements?.length) return;
            const r = await fetch(
              `${API_BASE}/api/me/submissions/${lateId}/solicitation`, {
                method: "PUT",
                headers: { "Content-Type": "application/json", ...authHeaders() },
                body: JSON.stringify({
                  extracted,
                  requirements: late.data.requirements,
                  merit_criteria: late.data.merit_criteria,
                  eligibility_notes: late.data.eligibility_notes,
                  read_report: late.data.read_report,
                  extraction: late.data.extraction,
                  source: { kind: source?.kind, filename: source?.filename || null,
                            url: source?.url || null },
                  source_id: boundSourceId,
                }),
              });
            if (r.ok && onLateAttach) onLateAttach(await r.json());
          })
          .catch(() => { /* the proposal is already saved; nothing to undo */ });
      }
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
            onStored={handleStoredSource}
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

function PickStep({ onFile, onUrl, onStored, fileInputRef, initialUrl = "" }) {
  const [dragOver, setDragOver] = useState(false);
  const [url, setUrl] = useState(initialUrl);
  // Documents this PI read but never attached. Purely an affordance: if the
  // fetch fails the block just does not render and uploading works as before,
  // so it is never allowed to gate the flow.
  const [stored, setStored] = useState([]);
  useEffect(() => {
    let live = true;
    fetch(`${API_BASE}/api/me/solicitation-sources/unbound`, { headers: authHeaders() })
      .then((r) => (r.ok ? r.json() : { sources: [] }))
      .then((d) => { if (live) setStored(d.sources || []); })
      .catch(() => {});
    return () => { live = false; };
  }, []);

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

      {stored.length > 0 && (
        <>
          <div className="solicitation-or">
            <span>or</span>
          </div>
          <div className="solicitation-stored">
            <div className="solicitation-stored-head">
              {stored.length === 1
                ? "You uploaded this recently but never attached it:"
                : "You uploaded these recently but never attached them:"}
            </div>
            {stored.map((s) => (
              <button
                key={s.id}
                type="button"
                className="solicitation-stored-row"
                onClick={() => onStored(s)}
              >
                <FileText size={16} className="solicitation-stored-icon" />
                <span className="solicitation-stored-meta">
                  <b>{s.filename || s.url || "Solicitation"}</b>
                  <small>
                    {s.chars?.toLocaleString()} characters · {timeAgo(s.created_at)}
                  </small>
                </span>
                <span className="solicitation-stored-use">Use it</span>
              </button>
            ))}
          </div>
        </>
      )}

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
        {/* An empty cap means one of two different things, and the PI must be
            able to tell them apart. "not_stated" is a FINDING — the funder sets
            no per-award maximum — so the field goes calm: no "verify" tag, no
            warning to go hunt. Hunting is actively harmful on such a
            solicitation: NSF 23-598's only dollar figure in 17 pages is the
            $28,000,000 program-wide pool, i.e. exactly the wrong number to
            type here. A null status still nags, because then we really don't
            know. Typing a cap flips it back to "stated". */}
        <Field
          label="Budget cap (USD)"
          critical={extracted.budget_cap_status !== "not_stated"}
          sourceQuote={sq.budget_cap || (extracted.budget_cap_status === "not_stated"
            ? sq.budget_cap_status : null)}
          unverified={unv.has("budget_cap")}
          hint={extracted.budget_cap_status === "not_stated"
            ? "This solicitation states no per-award cap — leave this blank. Budget to the scope of the work, and check the solicitation for limits on particular cost categories (e.g. equipment as a share of the budget)."
            : undefined}
        >
          <input
            type="number"
            value={extracted.budget_cap ?? ""}
            onChange={(e) => {
              const v = e.target.value === "" ? null : Number(e.target.value);
              onChange("budget_cap", v);
              // Keep the status honest against what is actually in the box, so
              // the calm note can never sit above a filled-in number.
              if (v !== null && extracted.budget_cap_status !== "stated") {
                onChange("budget_cap_status", "stated");
              } else if (v === null && extracted.budget_cap_status === "stated") {
                onChange("budget_cap_status", null);
              }
            }}
            placeholder={extracted.budget_cap_status === "not_stated"
              ? "No cap stated" : "e.g. 600000"}
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

// Page-limit keys are INTERNAL: `section_key()` produces them so the reviewer
// and the checklist can look a section up under one name. They were being
// printed to the PI raw — "letter_of_institutional_support: 2p" — the only
// snake_case in a modal that is otherwise plain English, and it reads like a
// leaked variable rather than a rule from their solicitation.
const _SMALL_WORDS = new Set(["of", "and", "or", "the", "a", "an", "for", "in", "to"]);

function humanizeSectionKey(key) {
  const words = String(key || "").split(/[_\s]+/).filter(Boolean);
  return words
    .map((w, i) => (i > 0 && _SMALL_WORDS.has(w.toLowerCase())
      ? w.toLowerCase()
      : w.charAt(0).toUpperCase() + w.slice(1)))
    .join(" ");
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
          <b>{humanizeSectionKey(section)}:</b>{" "}
          {n} {Number(n) === 1 ? "page" : "pages"}
        </span>
      ))}
    </div>
  );
}
