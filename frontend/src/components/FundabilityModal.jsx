import React, { useEffect, useState } from "react";
import { createPortal } from "react-dom";
import { X, Target, ShieldQuestion, MessageSquare, AlertTriangle, CheckCircle2, HelpCircle, Lightbulb } from "lucide-react";
import { getApiBase } from "../lib/apiBase";
import "./FundabilityModal.css";

const API_BASE = getApiBase();
const authHeaders = () => ({
  "Content-Type": "application/json",
  Authorization: `Bearer ${localStorage.getItem("token")}`,
});

const RATING = {
  strong: { cls: "fd-ok", label: "strong" },
  adequate: { cls: "fd-ok", label: "adequate" },
  weak: { cls: "fd-bad", label: "weak" },
  unclear: { cls: "fd-warn", label: "unclear" },
};
const ELIG_STATUS = {
  ok: { cls: "fd-ok", icon: <CheckCircle2 size={13} /> },
  stop: { cls: "fd-bad", icon: <AlertTriangle size={13} /> },
  check: { cls: "fd-warn", icon: <HelpCircle size={13} /> },
  coordinate: { cls: "fd-warn", icon: <AlertTriangle size={13} /> },
};
const OVERALL = {
  go: { cls: "fd-banner-go", text: "Looks eligible — good to proceed." },
  caution: { cls: "fd-banner-caution", text: "Eligible with items to confirm — check the flagged points." },
  stop: { cls: "fd-banner-stop", text: "Possible eligibility problem — confirm with ORA before investing more time." },
};

export default function FundabilityModal({ submission, onClose }) {
  const [meta, setMeta] = useState(null);
  const [tab, setTab] = useState("eligibility");
  const [answers, setAnswers] = useState({});
  const [elig, setElig] = useState(null);
  const [draft, setDraft] = useState("");
  const [review, setReview] = useState(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const r = await fetch(`${API_BASE}/api/me/submissions/${submission.id}/fundability/criteria`,
          { headers: authHeaders() });
        if (alive && r.ok) setMeta(await r.json());
      } finally { if (alive) setLoading(false); }
    })();
    return () => { alive = false; };
  }, [submission.id]);

  const checkElig = async () => {
    setBusy(true); setElig(null);
    try {
      const r = await fetch(`${API_BASE}/api/me/submissions/${submission.id}/eligibility`, {
        method: "POST", headers: authHeaders(), body: JSON.stringify({ answers }),
      });
      if (r.ok) setElig((await r.json()).result);
    } finally { setBusy(false); }
  };

  const runReview = async () => {
    setBusy(true); setReview(null);
    try {
      const r = await fetch(`${API_BASE}/api/me/submissions/${submission.id}/fundability`, {
        method: "POST", headers: authHeaders(), body: JSON.stringify({ draft_text: draft }),
      });
      if (r.ok) setReview((await r.json()).result);
    } finally { setBusy(false); }
  };

  return createPortal(
    <div className="fd-overlay" onClick={onClose}>
      <div className="fd-modal" onClick={(e) => e.stopPropagation()}>
        <header className="fd-header">
          <div className="fd-title"><Target size={18} /> Fundability Check
            <span className="fd-sub">{submission.title}</span></div>
          <button className="fd-close" onClick={onClose} aria-label="Close"><X size={18} /></button>
        </header>

        <p className="fd-intro">
          Advisory only — this is a candid second read, <b>not</b> a funding guarantee or a compliance gate.
        </p>

        {loading ? <div className="fd-loading">Loading…</div> : (
          <div className="fd-body">
            <div className="fd-tabs">
              <button className={tab === "eligibility" ? "active" : ""} onClick={() => setTab("eligibility")}>
                <ShieldQuestion size={13} /> Eligibility (go / no-go)
              </button>
              <button className={tab === "review" ? "active" : ""} onClick={() => setTab("review")}>
                <MessageSquare size={13} /> Reviewer feedback
              </button>
            </div>

            {tab === "eligibility" && (
              <div className="fd-section">
                {meta?.eligibility_text && (
                  <div className="fd-elig-text">
                    <b>From the solicitation:</b> {meta.eligibility_text}
                  </div>
                )}
                {(meta?.eligibility_questions || []).map((q) => (
                  <div className="fd-question" key={q.id}>
                    <div className="fd-q">{q.q}</div>
                    <div className="fd-choices">
                      {["yes", "no", "unsure"].map((v) => (
                        <label key={v} className={answers[q.id] === v ? "sel" : ""}>
                          <input type="radio" name={q.id} value={v}
                            checked={answers[q.id] === v}
                            onChange={() => setAnswers((a) => ({ ...a, [q.id]: v }))} />
                          {v}
                        </label>
                      ))}
                    </div>
                  </div>
                ))}
                <button className="fd-run" onClick={checkElig} disabled={busy}>
                  {busy ? "Checking…" : "Check eligibility"}
                </button>

                {elig && (
                  <div className="fd-result">
                    <div className={`fd-banner ${OVERALL[elig.overall]?.cls || ""}`}>
                      {OVERALL[elig.overall]?.text || elig.overall}
                    </div>
                    <ul className="fd-elig-list">
                      {elig.items.map((it, i) => {
                        const s = ELIG_STATUS[it.status] || ELIG_STATUS.check;
                        return <li key={i} className={`fd-elig-item ${s.cls}`}>{s.icon} <span>{it.message}</span></li>;
                      })}
                    </ul>
                  </div>
                )}
              </div>
            )}

            {tab === "review" && (
              <div className="fd-section">
                <div className="fd-criteria-note">
                  Scored against {meta?.sponsor || "the sponsor"}'s criteria: {(meta?.criteria || []).map((c) => c.label).join(", ")}.
                </div>
                <label className="fd-draft">
                  <span>Paste your draft (a section or the whole narrative)</span>
                  <textarea value={draft} onChange={(e) => setDraft(e.target.value)} rows={8}
                    placeholder="Paste your draft text…" />
                </label>
                <button className="fd-run" onClick={runReview} disabled={busy || !draft.trim()}>
                  {busy ? "Reading…" : "Get reviewer feedback"}
                </button>

                {review && (
                  <div className="fd-result">
                    <div className="fd-summary">{review.summary}</div>
                    <ul className="fd-criteria">
                      {review.criteria.map((c, i) => {
                        const r = RATING[c.rating] || RATING.unclear;
                        return (
                          <li key={i} className={`fd-crit ${r.cls}`}>
                            <div className="fd-crit-head"><b>{c.label}</b> <span className="fd-rating">{r.label}</span></div>
                            {c.comment && <div className="fd-note">{c.comment}</div>}
                            {c.evidence && <div className="fd-evidence">“{c.evidence}”</div>}
                            {c.fix && <div className="fd-fix">Strengthen: {c.fix}</div>}
                          </li>
                        );
                      })}
                    </ul>
                    {review.top_risks?.length > 0 && (
                      <div className="fd-risks">
                        <div className="fd-block-head"><AlertTriangle size={13} /> Biggest risks</div>
                        <ul>{review.top_risks.map((r, i) => <li key={i}>{r}</li>)}</ul>
                      </div>
                    )}
                    {!review.ai && (
                      <div className="fd-fallback"><Lightbulb size={12} /> AI reviewer offline — criteria shown for self-check; have a funded colleague read it too.</div>
                    )}
                  </div>
                )}
              </div>
            )}
          </div>
        )}
      </div>
    </div>,
    document.body
  );
}
