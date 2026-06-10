// MyProposals.jsx -- per-user proposal tracker. Browse, create, and
// check off seeded checklists for in-flight grant submissions. All data
// lives in the user's own submissions / submission_tasks rows; nothing
// is shared across users.

import React, { useState, useEffect, useMemo, useCallback } from "react";
import { ArrowLeft, Calculator, Calendar, CalendarPlus, Check, CheckCircle, Circle, ClipboardCheck, ExternalLink, FileText, Plus, ShieldCheck, Trash2, X } from "lucide-react";
import { getApiBase } from "../lib/apiBase";
import SolicitationUploadModal from "./SolicitationUploadModal";
import DraftCritiqueModal from "./DraftCritiqueModal";
import BudgetHelperModal from "./BudgetHelperModal";
import ComplianceSentinelModal from "./ComplianceSentinelModal";
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
// required attachments) for Draft Critic to check a draft against. Manual
// proposals have none, so the "Critique Draft" button is hidden for them.
// Mirrors the backend SOURCE OF TRUTH in
// backend/services/proposals_service.reconstruct_solicitation_context — if the
// line-anchored notes formats (^Budget cap: / ^Page limits: / ^Required
// attachments:) or the "Prepare required attachment:" task prefix change there,
// update this helper too or the button will silently desync.
const SOLICITATION_NOTE_RES = [/^Budget cap:/m, /^Page limits:/m, /^Required attachments:/m];
function hasSolicitation(submission) {
  const notes = submission?.notes || "";
  if (SOLICITATION_NOTE_RES.some((re) => re.test(notes))) return true;
  return (submission?.tasks || []).some((t) =>
    (t.title || "").trim().startsWith("Prepare required attachment:")
  );
}

function formatDeadline(iso) {
  if (!iso) return "No deadline set";
  const d = new Date(iso);
  if (isNaN(d.getTime())) return "Invalid date";
  return d.toLocaleDateString([], { month: "short", day: "numeric", year: "numeric" });
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
  const [busy, setBusy] = useState(false);

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

  const addToCalendar = async () => {
    const token = localStorage.getItem("token");
    const r = await fetch(`${API_BASE}/api/me/deadlines-token`, {
      headers: token ? { Authorization: `Bearer ${token}` } : {},
    });
    if (!r.ok) return;
    const { ics_url } = await r.json();
    const a = document.createElement("a");
    a.href = ics_url;
    a.download = "ora-deadlines.ics";
    document.body.appendChild(a);
    a.click();
    a.remove();
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
            <button className="calendar-export" onClick={addToCalendar}>
              <CalendarPlus size={16} />
              <span>Add deadlines to calendar</span>
            </button>
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
          onClose={() => setShowUpload(false)}
          onCreated={(created) => {
            setShowUpload(false);
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
  const dleft = daysUntil(sub.deadline);
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
        <span>{formatDeadline(sub.deadline)}</span>
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
    </li>
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
  const dleft = daysUntil(submission.deadline);
  const [showCritique, setShowCritique] = useState(false);
  const [showBudget, setShowBudget] = useState(false);
  const [showCompliance, setShowCompliance] = useState(false);

  return (
    <div className="proposals">
      <header className="proposals-header proposals-header-detail">
        <button className="proposals-back-btn" onClick={onBack}>
          <ArrowLeft size={12} /> All Proposals
        </button>
        <div className="proposals-header-actions">
          <button
            className="proposals-budget-btn"
            onClick={() => setShowBudget(true)}
            title="Build a sponsor-compliant budget (direct costs, F&A, total) and draft the justification."
          >
            <Calculator size={13} /> {submission.has_budget ? "Edit budget" : "Build budget"}
          </button>
          <button
            className="proposals-compliance-btn"
            onClick={() => setShowCompliance(true)}
            title="Check which approvals your project needs — IRB, IACUC, COI, RCR, export control."
          >
            <ShieldCheck size={13} /> Check compliance
          </button>
          {hasSolicitation(submission) && (
            <button
              className="proposals-critique-btn"
              onClick={() => setShowCritique(true)}
              title="Upload a draft PDF and check it against this proposal's solicitation requirements."
            >
              <ClipboardCheck size={13} /> Critique Draft
            </button>
          )}
          <button className="proposals-delete-btn" onClick={onDelete}>
            <Trash2 size={12} /> Delete
          </button>
        </div>
      </header>

      {showCritique && (
        <DraftCritiqueModal
          submission={submission}
          onClose={() => setShowCritique(false)}
        />
      )}

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

      <section className="proposal-detail-summary">
        <div className="proposal-detail-title-row">
          <div className="proposal-detail-sponsor">{submission.sponsor}</div>
          <h1>{submission.title}</h1>
        </div>
        <div className="proposal-detail-meta">
          <div className="proposal-detail-meta-item">
            <span className="meta-label">Deadline</span>
            <span className="meta-value">{formatDeadline(submission.deadline)}</span>
            {dleft !== null && submission.status === "active" && (
              <span className="meta-countdown">
                {dleft < 0 ? `${Math.abs(dleft)} days overdue`
                  : dleft === 0 ? "Today"
                    : `in ${dleft} days`}
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

      <section className="proposal-tasks">
        <h2>Checklist</h2>
        <ul className="task-list">
          {tasks.map((t) => (
            <TaskRow key={t.id} task={t} onToggle={onToggleTask} />
          ))}
        </ul>
      </section>
    </div>
  );
}

function TaskRow({ task, onToggle }) {
  const isDone = task.status === "done";
  const toggle = () => onToggle(task.id, isDone ? "pending" : "done");
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
        {task.due_offset_days != null && (
          <div className="task-meta">
            <Calendar size={9} />
            <span>{task.due_offset_days} days before deadline</span>
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

  return (
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
    </div>
  );
}
