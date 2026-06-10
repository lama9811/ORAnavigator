import React, { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { X, ShieldCheck, Save, ExternalLink, AlertTriangle, CheckCircle2, HelpCircle, ListPlus } from "lucide-react";
import { getApiBase } from "../lib/apiBase";
import "./ComplianceSentinelModal.css";

const API_BASE = getApiBase();
const authHeaders = () => ({
  "Content-Type": "application/json",
  Authorization: `Bearer ${localStorage.getItem("token")}`,
});

const STATUS_META = {
  required: { label: "Required", cls: "req", Icon: AlertTriangle },
  review: { label: "Review", cls: "rev", Icon: HelpCircle },
  not_required: { label: "Not required", cls: "na", Icon: CheckCircle2 },
};
const ORDER = { required: 0, review: 1, not_required: 2 };

export default function ComplianceSentinelModal({ submission, onClose, onSaved }) {
  const [questions, setQuestions] = useState([]);
  const [sponsorNote, setSponsorNote] = useState("");
  const [answers, setAnswers] = useState({});
  const [result, setResult] = useState(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [adding, setAdding] = useState(false);
  const [msg, setMsg] = useState("");
  const debounceRef = useRef(null);

  // Load the questionnaire + any saved answers/assessment for this proposal.
  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const [rQ, rC] = await Promise.all([
          fetch(`${API_BASE}/api/compliance/questions`, { headers: authHeaders() }),
          fetch(`${API_BASE}/api/me/submissions/${submission.id}/compliance`, { headers: authHeaders() }),
        ]);
        const q = rQ.ok ? await rQ.json() : { questions: [] };
        const c = rC.ok ? await rC.json() : null;
        if (!alive) return;
        setQuestions(q.questions || []);
        setSponsorNote(q.sponsor_note || "");
        setAnswers(c?.answers || {});
        setResult(c?.result || null);
      } finally {
        if (alive) setLoading(false);
      }
    })();
    return () => { alive = false; };
  }, [submission.id]);

  // Debounced live re-assessment whenever an answer changes.
  useEffect(() => {
    if (loading) return;
    clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(async () => {
      try {
        const r = await fetch(`${API_BASE}/api/compliance/assess`, {
          method: "POST", headers: authHeaders(),
          body: JSON.stringify({ answers, sponsor: submission.sponsor }),
        });
        if (r.ok) setResult(await r.json());
      } catch { /* keep last good result */ }
    }, 200);
    return () => clearTimeout(debounceRef.current);
  }, [answers, loading, submission.sponsor]);

  const setAnswer = (key, val) =>
    setAnswers((p) => ({ ...p, [key]: p[key] === val ? "" : val }));

  const save = async () => {
    setSaving(true); setMsg("");
    try {
      const r = await fetch(`${API_BASE}/api/me/submissions/${submission.id}/compliance`, {
        method: "PUT", headers: authHeaders(), body: JSON.stringify({ answers }),
      });
      if (r.ok) { setMsg("Saved"); onSaved?.(); }
      else setMsg("Could not save");
    } finally { setSaving(false); }
  };

  const addTasks = async () => {
    setAdding(true); setMsg("");
    try {
      const r = await fetch(`${API_BASE}/api/me/submissions/${submission.id}/compliance/tasks`, {
        method: "POST", headers: authHeaders(), body: JSON.stringify({ answers }),
      });
      if (r.ok) {
        const data = await r.json();
        const n = (data.created || []).length;
        setMsg(n ? `Added ${n} task${n === 1 ? "" : "s"} to your proposal` : "Already up to date — no new tasks");
        onSaved?.();
      } else setMsg("Could not add tasks");
    } finally { setAdding(false); }
  };

  const items = (result?.items || []).slice().sort((a, b) => ORDER[a.status] - ORDER[b.status]);
  const summary = result?.summary || { required: 0, review: 0, not_required: 0 };

  return createPortal(
    <div className="cs-overlay" onClick={onClose}>
      <div className="cs-modal" onClick={(e) => e.stopPropagation()}>
        <header className="cs-header">
          <div className="cs-title">
            <ShieldCheck size={18} />
            Compliance Sentinel
            <span className="cs-sub">{submission.title}</span>
          </div>
          <button className="cs-close" onClick={onClose} aria-label="Close"><X size={18} /></button>
        </header>

        {loading ? (
          <div className="cs-loading">Loading…</div>
        ) : (
          <div className="cs-body">
            {/* LEFT — questionnaire */}
            <div className="cs-form">
              <h4>Tell us about your project</h4>
              <p className="cs-note">{sponsorNote}</p>
              {questions.map((q) => (
                <div className="cs-q" key={q.key}>
                  <div className="cs-q-text">
                    <span className="cs-q-label">{q.label}</span>
                    {q.help && <span className="cs-q-help">{q.help}</span>}
                  </div>
                  <div className="cs-toggle">
                    <button
                      className={`cs-yn ${answers[q.key] === "yes" ? "on yes" : ""}`}
                      onClick={() => setAnswer(q.key, "yes")}
                    >Yes</button>
                    <button
                      className={`cs-yn ${answers[q.key] === "no" ? "on no" : ""}`}
                      onClick={() => setAnswer(q.key, "no")}
                    >No</button>
                  </div>
                </div>
              ))}
            </div>

            {/* RIGHT — live checklist */}
            <div className="cs-summary">
              <div className="cs-summary-head">
                <h4>Your compliance checklist</h4>
                <div className="cs-counts">
                  <span className="cs-pill req">{summary.required} required</span>
                  <span className="cs-pill rev">{summary.review} review</span>
                  <span className="cs-pill na">{summary.not_required} clear</span>
                </div>
              </div>

              <div className="cs-items">
                {items.map((it) => {
                  const m = STATUS_META[it.status] || STATUS_META.review;
                  return (
                    <div className={`cs-item ${m.cls}`} key={it.id}>
                      <div className="cs-item-head">
                        <m.Icon size={15} className="cs-item-icon" />
                        <span className="cs-item-title">{it.title}</span>
                        <span className={`cs-badge ${m.cls}`}>{m.label}</span>
                      </div>
                      <p className="cs-why">{it.why}</p>
                      {it.timing && <p className="cs-timing">{it.timing}</p>}
                      {it.kb_doc_url && (
                        <a className="cs-link" href={it.kb_doc_url} target="_blank" rel="noopener noreferrer">
                          Open {it.kb_doc_title || "form"} <ExternalLink size={12} />
                        </a>
                      )}
                    </div>
                  );
                })}
              </div>

              <div className="cs-actions">
                <button className="cs-btn cs-btn-primary" onClick={save} disabled={saving}>
                  <Save size={14} /> {saving ? "Saving…" : "Save"}
                </button>
                <button className="cs-btn" onClick={addTasks} disabled={adding || !summary.required}>
                  <ListPlus size={14} /> {adding ? "Adding…" : "Add required to my proposal"}
                </button>
                {msg && <span className="cs-msg">{msg}</span>}
              </div>
              <p className="cs-disclaimer">
                This checklist is guidance based on your answers and sponsor. Confirm specifics
                with the Office of Research Administration.
              </p>
            </div>
          </div>
        )}
      </div>
    </div>,
    document.body
  );
}
