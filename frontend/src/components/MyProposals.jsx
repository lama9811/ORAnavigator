// MyProposals.jsx -- per-user proposal tracker. Browse, create, and
// check off seeded checklists for in-flight grant submissions. All data
// lives in the user's own submissions / submission_tasks rows; nothing
// is shared across users.

import React, { useState, useEffect, useMemo, useCallback } from "react";
import { FaPlus } from "@react-icons/all-files/fa/FaPlus";
import { FaTimes } from "@react-icons/all-files/fa/FaTimes";
import { FaTrash } from "@react-icons/all-files/fa/FaTrash";
import { FaCheck } from "@react-icons/all-files/fa/FaCheck";
import { FaRegCircle } from "@react-icons/all-files/fa/FaRegCircle";
import { FaCheckCircle } from "@react-icons/all-files/fa/FaCheckCircle";
import { FaCalendarAlt } from "@react-icons/all-files/fa/FaCalendarAlt";
import { FaArrowLeft } from "@react-icons/all-files/fa/FaArrowLeft";
import { getApiBase } from "../lib/apiBase";
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

  // ---------- DETAIL VIEW ----------
  if (active) {
    return (
      <DetailView
        submission={active}
        onBack={() => setActive(null)}
        onToggleTask={toggleTask}
        onDelete={() => deleteSubmission(active.id)}
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
        <button
          className="proposals-new-btn"
          onClick={() => setShowCreate(true)}
        >
          <FaPlus size={12} /> New Proposal
        </button>
      </header>

      {error && <div className="proposals-error">{error}</div>}

      {loading ? (
        <div className="proposals-loading">Loading...</div>
      ) : submissions.length === 0 ? (
        <div className="proposals-empty">
          <div className="proposals-empty-icon">📋</div>
          <h2>No proposals yet</h2>
          <p>
            Click <b>New Proposal</b> to add your first one. You'll get a
            sponsor-specific checklist with deadline countdowns and direct
            links to every ORA form you'll need.
          </p>
        </div>
      ) : (
        <ul className="proposals-list">
          {submissions.map((s) => (
            <ProposalCard key={s.id} sub={s} onOpen={() => openDetail(s.id)} />
          ))}
        </ul>
      )}

      {showCreate && (
        <CreateModal
          onClose={() => setShowCreate(false)}
          onSubmit={handleCreate}
          busy={busy}
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
        <FaCalendarAlt size={11} />
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

function DetailView({ submission, onBack, onToggleTask, onDelete, busy, error }) {
  const tasks = submission.tasks || [];
  const done = tasks.filter((t) => t.status === "done").length;
  const total = tasks.length;
  const pct = total ? Math.round((done / total) * 100) : 0;
  const dleft = daysUntil(submission.deadline);

  return (
    <div className="proposals">
      <header className="proposals-header proposals-header-detail">
        <button className="proposals-back-btn" onClick={onBack}>
          <FaArrowLeft size={12} /> All Proposals
        </button>
        <button className="proposals-delete-btn" onClick={onDelete}>
          <FaTrash size={12} /> Delete
        </button>
      </header>

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
          <FaCheckCircle size={20} className="task-check-icon-done" />
        ) : (
          <FaRegCircle size={20} className="task-check-icon-pending" />
        )}
      </button>
      <div className="task-body">
        <div className="task-title">{task.title}</div>
        {task.description && (
          <div className="task-description">{task.description}</div>
        )}
        {task.due_offset_days != null && (
          <div className="task-meta">
            <FaCalendarAlt size={9} />
            <span>{task.due_offset_days} days before deadline</span>
          </div>
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
            <FaTimes />
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
              <FaCheck size={11} /> {busy ? "Creating..." : "Create Proposal"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
