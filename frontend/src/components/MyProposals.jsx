// MyProposals.jsx -- per-user proposal tracker. Browse, create, and
// check off seeded checklists for in-flight grant submissions. All data
// lives in the user's own submissions / submission_tasks rows; nothing
// is shared across users.

import React, { useState, useEffect, useMemo, useCallback, useRef } from "react";
import { createPortal } from "react-dom";
import { useLocation } from "react-router-dom";
import { ArrowLeft, ArrowRight, Calculator, Calendar, CalendarPlus, Check, CheckCircle, Circle, Download, ExternalLink, FileSearch, FileText, HelpCircle, Lightbulb, ListChecks, MoreHorizontal, Plus, ShieldCheck, Trash2, X } from "lucide-react";
import { getApiBase } from "../lib/apiBase";
import SolicitationUploadModal from "./SolicitationUploadModal";
import BudgetHelperModal from "./BudgetHelperModal";
import ComplianceSentinelModal from "./ComplianceSentinelModal";
import DraftReviewModal from "./DraftReviewModal";
import SectionCheckModal from "./SectionCheckModal";
import "./MyProposals.css";

const API_BASE = getApiBase();

const SPONSORS = ["NSF", "NIH", "DoD", "DoE", "NASA", "USDA", "EPA",
                  "Foundation", "State of Maryland", "Internal"];

const STATUS_LABEL = {
  active: "Active",
  submitted: "Submitted",
  withdrawn: "Withdrawn",
};

function daysUntil(iso) {
  if (!iso) return null;
  const d = new Date(iso);
  if (isNaN(d.getTime())) return null;
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  const target = new Date(d.getFullYear(), d.getMonth(), d.getDate());
  return Math.round((target - today) / (1000 * 60 * 60 * 24));
}

// True when a proposal carries solicitation rules (budget cap / page limits /
// required attachments). Drives the Solicitation button's "attached" badge.
// Mirrors the backend SOURCE OF TRUTH in
// backend/services/proposals_service.reconstruct_solicitation_context — if the
// line-anchored notes formats (^Budget cap: / ^Page limits: / ^Required
// attachments:) or the "Prepare required attachment:" task prefix change there,
// update this helper too or the button will silently desync.
const SOLICITATION_NOTE_RES = [/^Budget cap:/m, /^Page limits:/m, /^Required attachments:/m];
function hasSolicitation(submission) {
  // A stored solicitation profile is authoritative. The notes/task heuristics
  // below still answer for proposals created before that column existed.
  if (submission?.has_solicitation_requirements) return true;
  const notes = submission?.notes || "";
  if (SOLICITATION_NOTE_RES.some((re) => re.test(notes))) return true;
  return (submission?.tasks || []).some((t) =>
    (t.title || "").trim().startsWith("Prepare required attachment:")
  );
}

// The single recommended next action for a proposal, derived purely from its
// saved state. Drives the "What's next" card + which tool gets the accent.
// Solicitation is intentionally NOT a step here: it can only be attached at
// creation today, so it's surfaced as a status (not an actionable next step).
function nextStep(submission) {
  if (!submission.has_budget) return "budget";
  if (!submission.has_compliance) return "compliance";
  // Draft Review needs a solicitation to judge against, so attaching one comes
  // first when it is missing.
  if (!submission.has_solicitation_requirements) return "solicitation";
  // The writing step nextStep() lost when the Drafting Coach was removed —
  // CLAUDE.md records the gap: "no tool now helps a PI write, only ones that
  // check what they wrote." Check a Section needs no solicitation at all and
  // works from the first sentence typed, so it's recommended before the
  // PI has anything worth running a whole-package Draft Review against.
  // Once a review is saved there is something to review again, so the
  // recommendation reverts to Draft Review, same as before this step existed.
  if (!submission.draft_review_saved_at) return "sectioncheck";
  return "review";
}

// Plain-language guidance for each step — the heart of the first-timer
// experience. `open` is the modal key the card's action button triggers.
const STEP_INFO = {
  budget: {
    title: "Build your budget",
    why: "Funders cap how much you can request. Set your numbers first so the rest of the proposal fits within them.",
    action: "Open Budget Helper", open: "budget",
  },
  compliance: {
    title: "Check what approvals you need",
    why: "Approvals like IRB, IACUC, and COI training take time to obtain. Find out early which ones apply to you.",
    action: "Check compliance", open: "compliance",
  },
  solicitation: {
    title: "Attach the funder's solicitation",
    why: "Every requirement the funder states becomes a check on your draft — and it sets your budget cap and page limits at the same time.",
    action: "Attach solicitation", open: "solicitation",
  },
  sectioncheck: {
    title: "Check a section while you write it",
    why: "Paste or upload just one section — even a first draft — and see whether it's missing anything NSF's own rules require. No finished package needed.",
    action: "Check a section", open: "sectioncheck",
  },
  review: {
    title: "Check your draft against the solicitation",
    why: "Paste or upload your whole package and see, requirement by requirement, what the funder asked for and whether your draft addresses it.",
    action: "Open Draft Review", open: "eir",
  },
  done: {
    title: "You've covered the core steps",
    why: "Work through any remaining checklist items, then submit. Nice work getting here.",
    action: null, open: null,
  },
};

function formatDeadline(iso) {
  if (!iso) return "No deadline set";
  const d = new Date(iso);
  if (isNaN(d.getTime())) return "Invalid date";
  return d.toLocaleDateString([], { month: "short", day: "numeric", year: "numeric" });
}

// Build a Google Calendar "create event" link pre-filled with one proposal's
// deadline, as an all-day event. Opening it lands the user on Google's
// "Save event?" screen (one click to save). No backend, token, or login needed.
// The day is taken straight from the date part of the stored ISO string (NOT
// new Date(), which can shift the day across timezones) so it matches the .ics.
function googleCalUrl(sub) {
  if (!sub?.deadline) return null;
  const datePart = String(sub.deadline).slice(0, 10); // "YYYY-MM-DD"
  const [y, m, d] = datePart.split("-").map(Number);
  if (!y || !m || !d) return null;
  const start = `${y}${String(m).padStart(2, "0")}${String(d).padStart(2, "0")}`;
  // Google treats an all-day event's end date as exclusive -> use the next day.
  const next = new Date(Date.UTC(y, m - 1, d + 1));
  const end =
    `${next.getUTCFullYear()}${String(next.getUTCMonth() + 1).padStart(2, "0")}` +
    `${String(next.getUTCDate()).padStart(2, "0")}`;
  const text = `${sub.title} — proposal deadline (${sub.sponsor})`;
  const details = "Proposal deadline tracked in ORA Navigator.";
  return (
    "https://calendar.google.com/calendar/render?action=TEMPLATE" +
    "&text=" + encodeURIComponent(text) +
    "&dates=" + start + "/" + end +
    "&details=" + encodeURIComponent(details)
  );
}

function openGoogleCal(sub) {
  const url = googleCalUrl(sub);
  if (url) window.open(url, "_blank", "noopener");
}

function authHeaders() {
  const token = localStorage.getItem("token");
  return token ? { Authorization: `Bearer ${token}` } : {};
}

export default function MyProposals() {
  const [submissions, setSubmissions] = useState([]);
  const [active, setActive] = useState(null); // selected submission with tasks
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [showCreate, setShowCreate] = useState(false);
  const [showUpload, setShowUpload] = useState(false);
  const [prefillUrl, setPrefillUrl] = useState("");
  const [busy, setBusy] = useState(false);
  const location = useLocation();

  // Handoff from the Opportunity Finder: a solicitation URL arrives in router
  // state -> open the ingestion modal pre-filled with it. Clear the history
  // state afterward so a refresh doesn't reopen the modal.
  useEffect(() => {
    const url = location.state?.solicitationUrl;
    if (url) {
      setPrefillUrl(url);
      setShowUpload(true);
      window.history.replaceState({}, document.title);
    }
  }, [location.state]);

  const loadList = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const r = await fetch(`${API_BASE}/api/me/submissions`, {
        headers: authHeaders(),
      });
      if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
      const data = await r.json();
      setSubmissions(data.submissions || []);
    } catch (e) {
      setError("Couldn't load your proposals: " + e.message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { loadList(); }, [loadList]);

  const openDetail = async (id) => {
    setBusy(true);
    setError("");
    try {
      const r = await fetch(`${API_BASE}/api/me/submissions/${id}`, {
        headers: authHeaders(),
      });
      if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
      setActive(await r.json());
    } catch (e) {
      setError("Couldn't open that proposal: " + e.message);
    } finally {
      setBusy(false);
    }
  };

  const toggleTask = async (taskId, newStatus) => {
    if (!active) return;
    // Optimistic update
    setActive((cur) => ({
      ...cur,
      tasks: cur.tasks.map((t) =>
        t.id === taskId ? { ...t, status: newStatus } : t,
      ),
    }));
    try {
      await fetch(
        `${API_BASE}/api/me/submissions/${active.id}/tasks/${taskId}`,
        {
          method: "PATCH",
          headers: { "Content-Type": "application/json", ...authHeaders() },
          body: JSON.stringify({ status: newStatus }),
        },
      );
    } catch (e) {
      setError("Couldn't save that change.");
      openDetail(active.id);  // refetch to undo the optimistic flip
    }
  };

  const deleteSubmission = async (id) => {
    if (!window.confirm("Delete this proposal and all its tasks?")) return;
    try {
      await fetch(`${API_BASE}/api/me/submissions/${id}`, {
        method: "DELETE",
        headers: authHeaders(),
      });
      setActive(null);
      loadList();
    } catch (e) {
      setError("Delete failed: " + e.message);
    }
  };

  const handleCreate = async (form) => {
    setBusy(true);
    setError("");
    try {
      const r = await fetch(`${API_BASE}/api/me/submissions`, {
        method: "POST",
        headers: { "Content-Type": "application/json", ...authHeaders() },
        body: JSON.stringify(form),
      });
      if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
      const created = await r.json();
      setShowCreate(false);
      setActive(created);
      loadList();
    } catch (e) {
      setError("Couldn't create: " + e.message);
    } finally {
      setBusy(false);
    }
  };

  // Fetch the user's personal calendar feed URL (https, non-expiring token).
  const fetchIcsUrl = async () => {
    const token = localStorage.getItem("token");
    const r = await fetch(`${API_BASE}/api/me/deadlines-token`, {
      headers: token ? { Authorization: `Bearer ${token}` } : {},
    });
    if (!r.ok) throw new Error("couldn't get your calendar link");
    const { ics_url } = await r.json();
    return ics_url;
  };

  // Fallback for Apple Calendar / Outlook: download the .ics file. The feed
  // lives on the BACKEND origin, so a plain `<a download>` is ignored for a
  // cross-origin href; fetch it and save as a same-origin blob instead.
  const downloadIcs = async () => {
    setError("");
    try {
      const ics_url = await fetchIcsUrl();
      const fileResp = await fetch(ics_url);
      if (!fileResp.ok) throw new Error("couldn't fetch the calendar file");
      const blob = await fileResp.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = "ora-deadlines.ics";
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    } catch (e) {
      setError("Couldn't download the calendar file: " + e.message);
    }
  };

  // ---------- DETAIL VIEW ----------
  if (active) {
    return (
      <DetailView
        submission={active}
        onBack={() => setActive(null)}
        onToggleTask={toggleTask}
        onDelete={() => deleteSubmission(active.id)}
        onRefresh={() => openDetail(active.id)}
        busy={busy}
        error={error}
      />
    );
  }

  // ---------- LIST VIEW ----------
  return (
    <div className="proposals">
      <header className="proposals-header">
        <div>
          <h1>My Proposals</h1>
          <p className="proposals-subtitle">
            Track in-flight grant submissions. Each new proposal gets a seeded
            checklist tuned to its sponsor (NSF, NIH, generic) -- tick items
            off as you finish them.
          </p>
        </div>
        <div className="proposals-header-actions">
          <button
            className="proposals-upload-btn"
            onClick={() => setShowUpload(true)}
            title="Drop a sponsor PDF and let the app extract deadlines, page limits, and required attachments automatically."
          >
            <FileText size={13} /> Start from Solicitation
          </button>
          <button
            className="proposals-new-btn"
            onClick={() => setShowCreate(true)}
          >
            <Plus size={12} /> New Proposal
          </button>
        </div>
      </header>

      {error && <div className="proposals-error">{error}</div>}

      {loading ? (
        <div className="proposals-loading">Loading...</div>
      ) : submissions.length === 0 ? (
        <div className="proposals-empty">
          <div className="proposals-empty-icon">📋</div>
          <h2>No proposals yet</h2>
          <p>
            Two ways to start: <b>Start from Solicitation</b> if you have the
            sponsor's PDF — ORA Navigator will read it and pre-fill your
            proposal. Or <b>New Proposal</b> to enter the details by hand.
            Either way you'll get a sponsor-specific checklist with deadline
            countdowns.
          </p>
        </div>
      ) : (
        <>
          {submissions.some((s) => s.deadline) && (
            <div className="calendar-export-group">
              <span className="calendar-export-hint">
                Add a deadline to Google Calendar from any proposal below, or
              </span>
              <button className="calendar-export-secondary" onClick={downloadIcs}>
                <Download size={14} />
                <span>Download all deadlines as .ics (Apple Calendar / Outlook)</span>
              </button>
            </div>
          )}
          <ul className="proposals-list">
            {submissions.map((s) => (
              <ProposalCard key={s.id} sub={s} onOpen={() => openDetail(s.id)} />
            ))}
          </ul>
        </>
      )}

      {showCreate && (
        <CreateModal
          onClose={() => setShowCreate(false)}
          onSubmit={handleCreate}
          busy={busy}
        />
      )}

      {showUpload && (
        <SolicitationUploadModal
          initialUrl={prefillUrl}
          onClose={() => { setShowUpload(false); setPrefillUrl(""); }}
          onCreated={(created) => {
            setShowUpload(false);
            setPrefillUrl("");
            setActive(created);  // jump straight into the new proposal
            loadList();
          }}
        />
      )}
    </div>
  );
}

// ============================================================
// LIST CARD
// ============================================================

function ProposalCard({ sub, onOpen }) {
  // Count down to the internal ORA deadline (the date a PI must actually hit);
  // fall back to the sponsor deadline when there's no internal one.
  const headlineDeadline = sub.internal_deadline || sub.deadline;
  const dleft = daysUntil(headlineDeadline);
  const overdue = dleft !== null && dleft < 0 && sub.status === "active";
  const urgent = dleft !== null && dleft >= 0 && dleft <= 7 && sub.status === "active";

  return (
    <li className="proposal-card" onClick={onOpen}>
      <div className="proposal-card-top">
        <div className="proposal-card-sponsor">{sub.sponsor}</div>
        <div className={`proposal-card-status status-${sub.status}`}>
          {STATUS_LABEL[sub.status] || sub.status}
        </div>
      </div>
      <div className="proposal-card-title">{sub.title}</div>
      <div className="proposal-card-meta">
        <Calendar size={11} />
        <span title={sub.internal_deadline ? "Internal ORA deadline" : "Sponsor deadline"}>
          {formatDeadline(headlineDeadline)}
          {sub.internal_deadline && <span className="proposal-card-deadline-tag"> · internal</span>}
        </span>
        {dleft !== null && sub.status === "active" && (
          <span className={`proposal-card-countdown ${overdue ? "overdue" : urgent ? "urgent" : ""}`}>
            {overdue
              ? `${Math.abs(dleft)}d overdue`
              : dleft === 0
                ? "Due today"
                : `${dleft}d left`}
          </span>
        )}
      </div>
      {sub.deadline && (
        <button
          className="proposal-card-gcal"
          title="Add this deadline to your Google Calendar"
          onClick={(e) => { e.stopPropagation(); openGoogleCal(sub); }}
        >
          <CalendarPlus size={12} />
          <span>Add to Google Calendar</span>
        </button>
      )}
    </li>
  );
}

// ============================================================
// GUIDED PATHWAY pieces (presentation only — no new tool logic)
// ============================================================

// One tool in a lifecycle stage. Calm outline by default; `primary` gives it
// the single "do this next" accent. Optional status badge; optional disabled.
function ToolButton({ icon, label, status, statusDone, primary, disabled, onClick, title }) {
  const Icon = icon;   // capitalized local so the lint config recognizes the JSX use
  return (
    <button
      type="button"
      className={`lc-tool${primary ? " is-primary" : ""}${disabled ? " is-disabled" : ""}`}
      onClick={disabled ? undefined : onClick}
      disabled={disabled}
      title={title}
    >
      <span className="lc-tool-main"><Icon size={14} /> {label}</span>
      {status != null && (
        <span className={`lc-tool-status${statusDone ? " is-done" : ""}`}>
          {statusDone && <Check size={11} />} {status}
        </span>
      )}
    </button>
  );
}

// A labeled group of tools = one phase of the proposal lifecycle.
function LifecycleStage({ label, children }) {
  return (
    <div className="lc-stage">
      <span className="lc-stage-label">{label}</span>
      <div className="lc-stage-tools">{children}</div>
    </div>
  );
}

// The "⋯" menu holding non-tool actions (Delete, Add to Calendar) so they don't
// compete with the real work. Closes on Escape and outside-click.
function OverflowMenu({ items }) {
  const [open, setOpen] = useState(false);
  const ref = useRef(null);
  useEffect(() => {
    if (!open) return undefined;
    const onDoc = (e) => { if (ref.current && !ref.current.contains(e.target)) setOpen(false); };
    const onEsc = (e) => { if (e.key === "Escape") setOpen(false); };
    document.addEventListener("mousedown", onDoc);
    document.addEventListener("keydown", onEsc);
    return () => { document.removeEventListener("mousedown", onDoc); document.removeEventListener("keydown", onEsc); };
  }, [open]);
  return (
    <div className="lc-overflow" ref={ref}>
      <button type="button" className="lc-overflow-btn" aria-haspopup="menu" aria-expanded={open}
        aria-label="More actions" onClick={() => setOpen((o) => !o)}>
        <MoreHorizontal size={16} />
      </button>
      {open && (
        <div className="lc-overflow-menu" role="menu">
          {items.map((it) => (
            <button key={it.key} type="button" role="menuitem"
              className={`lc-overflow-item${it.danger ? " is-danger" : ""}`}
              onClick={() => { setOpen(false); it.onClick(); }}>
              <it.icon size={13} /> {it.label}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

// Plain-language "what to do next" card — the central first-timer affordance.
function NextStepCard({ stepKey, solicited, onAction }) {
  const info = STEP_INFO[stepKey] || STEP_INFO.done;
  return (
    <div className="np-card">
      <div className="np-head"><Lightbulb size={15} /> What to do next</div>
      <div className="np-title">{info.title}</div>
      <div className="np-why">{info.why}</div>
      {!solicited && (
        <div className="np-tip">
          Tip: attaching the funder's solicitation turns every requirement it states into a check on your draft.
        </div>
      )}
      {info.open && (
        <button type="button" className="np-action" onClick={() => onAction(info.open)}>
          {info.action} <ArrowRight size={14} />
        </button>
      )}
    </div>
  );
}

// ============================================================
// DETAIL VIEW (single submission + tasks)
// ============================================================

function DetailView({ submission, onBack, onToggleTask, onDelete, onRefresh, busy, error }) {
  const tasks = submission.tasks || [];
  const done = tasks.filter((t) => t.status === "done").length;
  const total = tasks.length;
  const pct = total ? Math.round((done / total) * 100) : 0;
  // The internal ORA routing deadline is the date a PI actually has to hit, so
  // it's the headline + countdown; the sponsor deadline is shown as secondary.
  const headlineDeadline = submission.internal_deadline || submission.deadline;
  const dleft = daysUntil(headlineDeadline);
  const [showBudget, setShowBudget] = useState(false);
  const [showCompliance, setShowCompliance] = useState(false);
  const [showEir, setShowEir] = useState(false);
  const [showSectionCheck, setShowSectionCheck] = useState(false);
  const [showSolicitation, setShowSolicitation] = useState(false);
  // Set when Draft Review offers back an abandoned upload, so the modal
  // opens straight onto that document instead of the file picker.
  const [attachSourceId, setAttachSourceId] = useState(null);

  const next = nextStep(submission);
  const solicited = hasSolicitation(submission);
  const openModal = (key) => {
    if (key === "budget") setShowBudget(true);
    else if (key === "compliance") setShowCompliance(true);
    else if (key === "solicitation") setShowSolicitation(true);
    else if (key === "eir") setShowEir(true);
    else if (key === "sectioncheck") setShowSectionCheck(true);
  };

  return (
    <div className="proposals">
      <header className="proposals-header proposals-header-detail">
        <button className="proposals-back-btn" onClick={onBack}>
          <ArrowLeft size={12} /> All Proposals
        </button>
        <div className="proposals-header-actions">
          <LifecycleStage label="Set up">
            <ToolButton
              icon={FileText}
              label="Solicitation"
              status={submission.has_solicitation_requirements ? "attached"
                      : solicited ? "rules only" : "not attached"}
              statusDone={submission.has_solicitation_requirements}
              primary={next === "solicitation"}
              onClick={() => setShowSolicitation(true)}
              title={submission.has_solicitation_requirements
                ? "This proposal's solicitation is stored, including every requirement Draft Review checks against."
                : solicited
                  ? "This proposal has the funder's numbers (cap, page limits, attachments) but not its requirement list — that was added later. Upload the solicitation once to read it in full."
                  : "Attach the funder's solicitation — it sets the budget cap and page limits, and every requirement in it becomes a Draft Review check."}
            />
          </LifecycleStage>

          <LifecycleStage label="Build">
            <ToolButton
              icon={Calculator}
              label="Budget draft"
              status={submission.has_budget ? "set" : null}
              statusDone={submission.has_budget}
              primary={next === "budget"}
              onClick={() => setShowBudget(true)}
              title="Build a sponsor-compliant budget (direct costs, F&A, total) and draft the justification."
            />
            <ToolButton
              icon={FileSearch}
              label="Check a section"
              primary={next === "sectioncheck"}
              onClick={() => setShowSectionCheck(true)}
              title="Check one section — even a first draft — against NSF's own rules while you're still writing it. No solicitation needed."
            />
          </LifecycleStage>

          <LifecycleStage label="Review & submit">
            <ToolButton
              icon={ShieldCheck}
              label="Check compliance"
              status={submission.has_compliance ? "reviewed" : null}
              statusDone={submission.has_compliance}
              primary={next === "compliance"}
              onClick={() => setShowCompliance(true)}
              title="Check which approvals your project needs — IRB, IACUC, COI, RCR, export control."
            />
            <ToolButton
              icon={ListChecks}
              label="Draft review"
              status={submission.has_solicitation_requirements ? null : "needs solicitation"}
              primary={next === "review"}
              onClick={() => setShowEir(true)}
              title={submission.has_solicitation_requirements
                ? "Paste or upload your whole package and check it against every requirement in this proposal's solicitation."
                : "Attach this proposal's solicitation, then every requirement in it becomes a check."}
            />
          </LifecycleStage>

          <OverflowMenu
            items={[
              ...(submission.deadline ? [{
                key: "cal", label: "Add to Google Calendar", icon: CalendarPlus,
                onClick: () => openGoogleCal(submission),
              }] : []),
              { key: "del", label: "Delete proposal", icon: Trash2, danger: true, onClick: onDelete },
            ]}
          />
        </div>
      </header>

      {showBudget && (
        <BudgetHelperModal
          submission={submission}
          onClose={() => setShowBudget(false)}
          onSaved={onRefresh}
        />
      )}

      {showCompliance && (
        <ComplianceSentinelModal
          submission={submission}
          onClose={() => setShowCompliance(false)}
          onSaved={onRefresh}
        />
      )}

      {showSectionCheck && (
        <SectionCheckModal
          submission={submission}
          onClose={() => setShowSectionCheck(false)}
        />
      )}

      {showEir && (
        <DraftReviewModal
          submission={submission}
          onClose={() => setShowEir(false)}
          onAttach={(sourceId) => {
            // The panel may have found a document this PI uploaded and never
            // attached. Hand its id to the one existing save path rather than
            // binding here — a second way to attach a solicitation would drift
            // from this one.
            setAttachSourceId(sourceId ?? null);
            setShowEir(false);
            setShowSolicitation(true);
          }}
          onRefresh={onRefresh}
        />
      )}

      {/* One attach UI, reused: the same modal that starts a proposal from a
          solicitation fills one in on a proposal that already exists. */}
      {showSolicitation && (
        <SolicitationUploadModal
          submissionId={submission.id}
          initialSourceId={attachSourceId}
          onClose={() => { setShowSolicitation(false); setAttachSourceId(null); }}
          onCreated={() => { setShowSolicitation(false); setAttachSourceId(null); onRefresh(); }}
        />
      )}

      <section className="proposal-detail-summary">
        <div className="proposal-detail-title-row">
          <div className="proposal-detail-sponsor">{submission.sponsor}</div>
          <h1>{submission.title}</h1>
        </div>
        <div className="proposal-detail-meta">
          <div className="proposal-detail-meta-item">
            <span className="meta-label">
              {submission.internal_deadline ? "Internal ORA deadline" : "Deadline"}
            </span>
            <span className="meta-value"
              title={submission.internal_deadline
                ? "Morgan ORA needs proposals routed internally before the sponsor's deadline — about 5 business days earlier."
                : undefined}>
              {formatDeadline(headlineDeadline)}
            </span>
            {dleft !== null && submission.status === "active" && (
              <span className="meta-countdown">
                {dleft < 0 ? `${Math.abs(dleft)} days overdue`
                  : dleft === 0 ? "Today"
                    : `in ${dleft} days`}
              </span>
            )}
            {submission.internal_deadline && (
              <span className="meta-internal">
                Sponsor deadline: {formatDeadline(submission.deadline)}
              </span>
            )}
          </div>
          <div className="proposal-detail-meta-item">
            <span className="meta-label">Status</span>
            <span className={`meta-value status-pill status-${submission.status}`}>
              {STATUS_LABEL[submission.status] || submission.status}
            </span>
          </div>
          <div className="proposal-detail-meta-item">
            <span className="meta-label">Progress</span>
            <span className="meta-value">
              {done} / {total} tasks ({pct}%)
            </span>
          </div>
        </div>
        <div className="proposal-progress-bar">
          <div
            className="proposal-progress-fill"
            style={{ width: `${pct}%` }}
          />
        </div>
      </section>

      {error && <div className="proposals-error">{error}</div>}

      {submission.status === "active" && (
        <NextStepCard stepKey={next} solicited={solicited} onAction={openModal} />
      )}

      <ChecklistBySource
        tasks={tasks}
        onToggleTask={onToggleTask}
        deadline={submission.deadline}
      />
    </div>
  );
}

// The checklist, split by WHERE EACH TASK CAME FROM.
//
// One flat list put "NSF requires a 2-page Data Management Plan" — a number
// hardcoded in proposal_templates.py — beside tasks read out of the PI's actual
// solicitation, in identical styling. The PI had no way to tell which was
// which, and the hardcoded one is wrong for any solicitation that says
// something else. Grouping is the fix: a task claiming to come from the
// solicitation now sits under that heading and shows the funder's own sentence.
//
// Unlabelled tasks (everything predating the `source` column, plus anything the
// PI added themselves) fall into the process group rather than being claimed
// as the solicitation's — an absent label is not evidence.
function ChecklistBySource({ tasks, onToggleTask, deadline }) {
  const fromSolicitation = tasks.filter((t) => t.source === "solicitation");
  const rest = tasks.filter((t) => t.source !== "solicitation");
  const groups = [
    fromSolicitation.length && {
      key: "sol",
      title: "From this solicitation",
      hint: "Read out of the funder's own document — each shows the sentence it came from.",
      items: fromSolicitation,
    },
    rest.length && {
      key: "ora",
      title: "Morgan State / ORA process",
      hint: "How a proposal gets submitted here. Required regardless of funder, and stated in no solicitation.",
      items: rest,
    },
  ].filter(Boolean);

  return (
    <section className="proposal-tasks">
      <h2>Checklist</h2>
      {groups.map((g) => (
        <div className="task-group" key={g.key}>
          <div className="task-group-head">
            <h3 className="task-group-title">{g.title}</h3>
            <span className="task-group-count">{g.items.length}</span>
          </div>
          <p className="task-group-hint">{g.hint}</p>
          <ul className="task-list">
            {g.items.map((t) => (
              <TaskRow key={t.id} task={t} onToggle={onToggleTask} deadline={deadline} />
            ))}
          </ul>
        </div>
      ))}
    </section>
  );
}

// Google Calendar link for a single task, dated (deadline - due_offset_days).
function taskCalUrl(task, deadline) {
  if (!deadline || task.due_offset_days == null) return null;
  const due = new Date(deadline);
  if (isNaN(due)) return null;
  due.setDate(due.getDate() - task.due_offset_days);
  const ymd = due.toISOString().slice(0, 10).replace(/-/g, "");
  const params = new URLSearchParams({
    action: "TEMPLATE",
    text: `[Proposal] ${task.title}`,
    details: task.description || "ORA Navigator proposal task.",
    dates: `${ymd}/${ymd}`,
  });
  return `https://calendar.google.com/calendar/render?${params.toString()}`;
}

function TaskRow({ task, onToggle, deadline }) {
  const isDone = task.status === "done";
  const toggle = () => onToggle(task.id, isDone ? "pending" : "done");
  const [showHow, setShowHow] = useState(false);
  const calUrl = taskCalUrl(task, deadline);
  return (
    <li className={`task-row ${isDone ? "task-done" : ""}`}>
      <button
        className="task-check"
        onClick={toggle}
        aria-label={isDone ? "Mark as pending" : "Mark as done"}
      >
        {isDone ? (
          <CheckCircle size={20} className="task-check-icon-done" />
        ) : (
          <Circle size={20} className="task-check-icon-pending" />
        )}
      </button>
      <div className="task-body">
        <div className="task-title">{task.title}</div>
        {task.description && (
          <div className="task-description">{task.description}</div>
        )}
        {/* The funder's own sentence, verbatim. This is what makes the group
            heading checkable rather than a claim the PI has to trust. */}
        {task.source_quote && (
          <blockquote className="task-quote">{task.source_quote}</blockquote>
        )}
        <div className="task-meta-row">
          {task.due_offset_days != null && (
            <div className="task-meta">
              <Calendar size={9} />
              <span>{task.due_offset_days} days before deadline</span>
            </div>
          )}
          {calUrl && (
            <a className="task-cal-link" href={calUrl} target="_blank" rel="noopener noreferrer">
              <CalendarPlus size={11} /> Add to calendar
            </a>
          )}
          {task.guidance && (
            <button className="task-how-toggle" onClick={() => setShowHow((s) => !s)}>
              <HelpCircle size={11} /> {showHow ? "Hide help" : "How do I do this?"}
            </button>
          )}
        </div>
        {showHow && task.guidance && (
          <div className="task-how">
            <div className="task-how-text">{task.guidance.how_to}</div>
            {task.guidance.sample && (
              <div className="task-how-sample"><b>Example:</b> {task.guidance.sample}</div>
            )}
          </div>
        )}
        {task.kb_doc_url && (
          <a
            className="task-form-link"
            href={task.kb_doc_url}
            target="_blank"
            rel="noopener noreferrer"
          >
            <ExternalLink size={12} />
            <span>Open {task.kb_doc_title || "form"}</span>
          </a>
        )}
      </div>
    </li>
  );
}

// ============================================================
// CREATE MODAL
// ============================================================

function CreateModal({ onClose, onSubmit, busy }) {
  const [title, setTitle] = useState("");
  const [sponsor, setSponsor] = useState("NSF");
  const [deadline, setDeadline] = useState("");
  const [notes, setNotes] = useState("");

  const submit = (e) => {
    e.preventDefault();
    if (!title.trim()) return;
    onSubmit({
      title: title.trim(),
      sponsor,
      deadline: deadline || null,
      notes: notes.trim() || null,
    });
  };

  const dleft = useMemo(() => daysUntil(deadline), [deadline]);

  return createPortal(
    <div className="proposals-modal-overlay" onClick={onClose}>
      <div className="proposals-modal" onClick={(e) => e.stopPropagation()}>
        <div className="proposals-modal-header">
          <h2>New Proposal</h2>
          <button className="proposals-modal-close" onClick={onClose}>
            <X />
          </button>
        </div>
        <form onSubmit={submit}>
          <div className="proposals-field">
            <label>Title</label>
            <input
              autoFocus
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              placeholder="e.g. NSF CAREER award on cancer health disparities"
              required
            />
          </div>
          <div className="proposals-field-row">
            <div className="proposals-field">
              <label>Sponsor</label>
              <select value={sponsor} onChange={(e) => setSponsor(e.target.value)}>
                {SPONSORS.map((s) => <option key={s} value={s}>{s}</option>)}
              </select>
              <small className="proposals-hint">
                NSF and NIH get sponsor-specific checklist add-ons; others get the
                generic 10-step proposal checklist.
              </small>
            </div>
            <div className="proposals-field">
              <label>Deadline</label>
              <input
                type="date"
                value={deadline}
                onChange={(e) => setDeadline(e.target.value)}
              />
              {dleft !== null && dleft >= 0 && (
                <small className="proposals-hint">{dleft} days from today</small>
              )}
              {dleft !== null && dleft < 0 && (
                <small className="proposals-hint proposals-hint-warn">
                  That date is in the past.
                </small>
              )}
            </div>
          </div>
          <div className="proposals-field">
            <label>Notes (optional)</label>
            <textarea
              value={notes}
              onChange={(e) => setNotes(e.target.value)}
              rows={3}
              placeholder="Solicitation number, co-PIs, anything you want to remember..."
            />
          </div>
          <div className="proposals-modal-actions">
            <button type="button" className="btn-secondary" onClick={onClose}>
              Cancel
            </button>
            <button
              type="submit"
              className="btn-primary"
              disabled={busy || !title.trim()}
            >
              <Check size={11} /> {busy ? "Creating..." : "Create Proposal"}
            </button>
          </div>
        </form>
      </div>
    </div>,
    document.body
  );
}
