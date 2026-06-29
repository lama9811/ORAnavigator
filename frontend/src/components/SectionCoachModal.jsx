import React, { useEffect, useState } from "react";
import { createPortal } from "react-dom";
import { X, PenLine, ListChecks, MessageSquare, Lightbulb, AlertTriangle, CheckCircle2, HelpCircle, FileText, GitCompare, Scale } from "lucide-react";
import { getApiBase } from "../lib/apiBase";
import "./SectionCoachModal.css";

const API_BASE = getApiBase();
const authHeaders = () => ({
  "Content-Type": "application/json",
  Authorization: `Bearer ${localStorage.getItem("token")}`,
});

const STATUS = {
  covered: { cls: "sc-ok", icon: <CheckCircle2 size={13} />, label: "covered" },
  partial: { cls: "sc-warn", icon: <AlertTriangle size={13} />, label: "partial" },
  missing: { cls: "sc-bad", icon: <AlertTriangle size={13} />, label: "missing" },
  unclear: { cls: "sc-bad", icon: <HelpCircle size={13} />, label: "not found" },
};

export default function SectionCoachModal({ submission, onClose }) {
  const [sections, setSections] = useState([]);
  const [sectionKey, setSectionKey] = useState("");
  const [mode, setMode] = useState("outline");   // "outline" | "review"
  const [topic, setTopic] = useState("");
  const [draft, setDraft] = useState("");
  const [result, setResult] = useState(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [drafts, setDrafts] = useState({});      // saved per-section drafts
  const [savedMsg, setSavedMsg] = useState("");

  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const [rS, rD] = await Promise.all([
          fetch(`${API_BASE}/api/me/submissions/${submission.id}/sections`, { headers: authHeaders() }),
          fetch(`${API_BASE}/api/me/submissions/${submission.id}/sections/drafts`, { headers: authHeaders() }),
        ]);
        const data = rS.ok ? await rS.json() : { sections: [] };
        const dd = rD.ok ? await rD.json() : { drafts: {} };
        if (!alive) return;
        setSections(data.sections || []);
        const first = data.sections?.[0]?.key || "";
        setSectionKey(first);
        setDrafts(dd.drafts || {});
        if (first && dd.drafts?.[first]) setDraft(dd.drafts[first]);
      } finally {
        if (alive) setLoading(false);
      }
    })();
    return () => { alive = false; };
  }, [submission.id]);

  const curSection = sections.find((s) => s.key === sectionKey) || {};
  const wordCount = draft.trim() ? draft.trim().split(/\s+/).length : 0;

  const saveDraft = async () => {
    setSavedMsg("");
    try {
      const r = await fetch(`${API_BASE}/api/me/submissions/${submission.id}/sections/drafts`, {
        method: "PUT", headers: authHeaders(),
        body: JSON.stringify({ section_key: sectionKey, text: draft }),
      });
      if (r.ok) {
        setDrafts((await r.json()).drafts || {});
        setSavedMsg("Draft saved.");
      }
    } catch { /* ignore */ }
  };

  const run = async () => {
    if (mode === "coherence") return runCoherence();
    if (!sectionKey) return;
    setBusy(true); setResult(null);
    try {
      const r = await fetch(`${API_BASE}/api/me/submissions/${submission.id}/section-coach`, {
        method: "POST", headers: authHeaders(),
        body: JSON.stringify({ section_key: sectionKey, mode, topic, draft_text: draft }),
      });
      if (r.ok) setResult((await r.json()).result);
    } finally { setBusy(false); }
  };

  // Cross-section coherence runs over the SAVED sections (no per-section input).
  const runCoherence = async () => {
    setBusy(true); setResult(null);
    try {
      const r = await fetch(`${API_BASE}/api/me/submissions/${submission.id}/coherence`, {
        method: "POST", headers: authHeaders(),
      });
      if (r.ok) setResult({ ...(await r.json()).result, mode: "coherence" });
    } finally { setBusy(false); }
  };

  const savedCount = Object.keys(drafts || {}).length;

  // Re-run is explicit (button); switching section/mode clears the old result.
  const pickSection = (k) => {
    setSectionKey(k); setResult(null); setSavedMsg("");
    setDraft(drafts[k] || "");   // restore the saved draft for this section
  };
  const pickMode = (m) => { setMode(m); setResult(null); };

  return createPortal(
    <div className="sc-overlay" onClick={onClose}>
      <div className="sc-modal" onClick={(e) => e.stopPropagation()}>
        <header className="sc-header">
          <div className="sc-title"><PenLine size={18} /> Drafting Coach
            <span className="sc-sub">{submission.title}</span></div>
          <button className="sc-close" onClick={onClose} aria-label="Close"><X size={18} /></button>
        </header>

        <p className="sc-intro">
          Pick a section. <b>Outline it</b> shows what it should contain for {submission.sponsor || "your sponsor"};
          <b> Feedback</b> reviews a draft you paste. The coach guides and reviews — it never writes the section for you.
        </p>

        {loading ? (
          <div className="sc-loading">Loading sections…</div>
        ) : (
          <div className="sc-body">
            <div className="sc-controls">
              <label className="sc-select">
                <span>Section</span>
                <select value={sectionKey} onChange={(e) => pickSection(e.target.value)}>
                  {sections.map((s) => <option key={s.key} value={s.key}>{s.label}</option>)}
                </select>
              </label>

              <div className="sc-tabs">
                <button className={mode === "outline" ? "active" : ""} onClick={() => pickMode("outline")}>
                  <ListChecks size={13} /> Outline it
                </button>
                <button className={mode === "review" ? "active" : ""} onClick={() => pickMode("review")}>
                  <MessageSquare size={13} /> Feedback on my draft
                </button>
                <button className={mode === "coherence" ? "active" : ""} onClick={() => pickMode("coherence")}
                  disabled={savedCount < 2}
                  title={savedCount < 2 ? "Save at least two sections to compare them" : "Check that your saved sections agree"}>
                  <GitCompare size={13} /> Cross-section
                </button>
              </div>
            </div>

            {mode === "outline" && (
              <label className="sc-topic">
                <span>Project topic <em>(optional — tailors the tips)</em></span>
                <input value={topic} onChange={(e) => setTopic(e.target.value)}
                  placeholder="e.g. microbial bioremediation of urban soils" />
              </label>
            )}

            {mode === "review" && (
              <label className="sc-topic">
                <span>Your draft of this section</span>
                <textarea value={draft} onChange={(e) => { setDraft(e.target.value); setSavedMsg(""); }} rows={8}
                  placeholder="Paste or write the text for this section…" />
                <LengthMeter words={wordCount} min={curSection.target_min} max={curSection.target_max} />
              </label>
            )}

            {mode === "coherence" && (
              <p className="sc-coherence-intro">
                Checks whether your <b>{savedCount} saved sections</b> agree with each other —
                e.g. does the Research Strategy address every Specific Aim, does the scope fit the
                eligibility, does the timeline match the budget? Advisory only.
              </p>
            )}

            <div className="sc-run-row">
              <button className="sc-run" onClick={run}
                disabled={busy || (mode === "review" && !draft.trim()) || (mode === "coherence" && savedCount < 2)}>
                {busy ? "Thinking…" : mode === "outline" ? "Get outline"
                  : mode === "review" ? "Get feedback" : "Check sections"}
              </button>
              {mode === "review" && (
                <button className="sc-save" onClick={saveDraft} disabled={!draft.trim()}>Save draft</button>
              )}
              {savedMsg && <span className="sc-saved">{savedMsg}</span>}
            </div>

            {result && result.mode === "outline" && <OutlineView r={result} />}
            {result && result.mode === "review" && <ReviewView r={result} />}
            {result && result.mode === "coherence" && <CoherenceView r={result} />}
          </div>
        )}
      </div>
    </div>,
    document.body
  );
}

function LengthMeter({ words, min, max }) {
  if (!min && !max) {
    return <div className="sc-meter sc-meter-open">{words} words · target: per the solicitation's page limit</div>;
  }
  const over = max && words > max * 1.1;
  const short = min && words < min * 0.5 && words > 0;
  const cls = over ? "sc-meter-over" : short ? "sc-meter-short" : "sc-meter-ok";
  const range = min && max ? `${min}–${max}` : max ? `≤ ${max}` : `≥ ${min}`;
  const pct = max ? Math.min(100, Math.round((words / max) * 100)) : 0;
  return (
    <div className={`sc-meter ${cls}`}>
      <div className="sc-meter-bar"><div className="sc-meter-fill" style={{ width: `${pct}%` }} /></div>
      <span>{words} words · target {range}{over ? " · too long" : short ? " · quite short" : ""}</span>
    </div>
  );
}

function Constraints({ c }) {
  if (!c || (!c.required_attachments && !c.eligibility && !c.page_limits)) return null;
  return (
    <div className="sc-constraints">
      <div className="sc-block-head"><ListChecks size={13} /> What this solicitation requires</div>
      {c.eligibility && <div className="sc-con-line"><b>Eligibility:</b> {c.eligibility}</div>}
      {c.page_limits && Object.keys(c.page_limits).length > 0 && (
        <div className="sc-con-line"><b>Page limits:</b> {Object.entries(c.page_limits).map(([k, v]) => `${k}: ${v}p`).join(", ")}</div>
      )}
      {c.required_attachments?.length > 0 && (
        <div className="sc-con-line"><b>Required attachments:</b> {c.required_attachments.join("; ")}</div>
      )}
    </div>
  );
}

// The panel's actual scoring criteria + (when available) an AI note per
// criterion on how a reviewer would judge THIS draft. Rubric is deterministic
// (always present); notes are advisory and only appear when the AI is on.
function ReviewerPanel({ rubric, notes }) {
  if (!rubric?.length) return null;
  const noteFor = {};
  (notes || []).forEach((n) => { if (n.criterion) noteFor[n.criterion] = n.note; });
  return (
    <div className="sc-reviewer">
      <div className="sc-block-head"><Scale size={13} /> How reviewers will score this</div>
      <ul className="sc-rubric">
        {rubric.map((c, i) => (
          <li key={i}>
            <b>{c.criterion}</b>{c.weight ? <span className="sc-weight"> · {c.weight}</span> : null}
            <div className="sc-asks">{c.asks}</div>
            {noteFor[c.criterion] && <div className="sc-rev-note">{noteFor[c.criterion]}</div>}
          </li>
        ))}
      </ul>
    </div>
  );
}

// A link to a hosted, authored sample proposal showing what a strong version of
// this section reads like. Opens the public download endpoint in a new tab.
function SampleLink({ sample }) {
  if (!sample?.id) return null;
  return (
    <a className="sc-sample" href={`${API_BASE}/api/sample-proposals/${sample.id}/download`}
       target="_blank" rel="noopener noreferrer">
      <FileText size={12} /> See a worked example{sample.title ? `: ${sample.title}` : ""}
    </a>
  );
}

function OutlineView({ r }) {
  return (
    <div className="sc-result">
      <Constraints c={r.solicitation_constraints} />
      <ReviewerPanel rubric={r.rubric} />
      <div className="sc-purpose">{r.purpose}</div>
      <div className="sc-meta">Target length: {r.target_words}</div>
      <SampleLink sample={r.sample} />
      <ol className="sc-outline">
        {r.outline.map((o, i) => (
          <li key={i}>
            <b>{o.heading}</b>
            {o.guidance && <div className="sc-guidance">{o.guidance}</div>}
          </li>
        ))}
      </ol>
      {r.pitfalls?.length > 0 && (
        <div className="sc-pitfalls">
          <div className="sc-block-head"><AlertTriangle size={13} /> Common pitfalls</div>
          <ul>{r.pitfalls.map((p, i) => <li key={i}>{p}</li>)}</ul>
        </div>
      )}
      {r.kb_hint && <div className="sc-kbhint"><Lightbulb size={12} /> {r.kb_hint}</div>}
    </div>
  );
}

function ReviewView({ r }) {
  return (
    <div className="sc-result">
      <Constraints c={r.solicitation_constraints} />
      <div className="sc-summary">{r.summary}</div>
      <ReviewerPanel rubric={r.rubric} notes={r.reviewer_notes} />
      <SampleLink sample={r.sample} />
      {typeof r.word_count === "number" && r.word_count > 0 && (
        <div className="sc-meta">
          ~{r.word_count} words · target: {r.target_words}
          {r.length_status === "long" && <span className="sc-len-bad"> · too long</span>}
          {r.length_status === "short" && <span className="sc-len-bad"> · quite short</span>}
        </div>
      )}
      {r.clarity?.length > 0 && (
        <div className="sc-clarity">
          <div className="sc-block-head"><MessageSquare size={13} /> Clarity</div>
          <ul>{r.clarity.map((c, i) => <li key={i}>{c.message}</li>)}</ul>
        </div>
      )}
      <ul className="sc-checklist">
        {r.checklist.map((c, i) => {
          const s = STATUS[c.status] || STATUS.unclear;
          return (
            <li key={i} className={`sc-check ${s.cls}`}>
              <div className="sc-check-head">{s.icon} <b>{c.item}</b> <span className="sc-status">{s.label}</span></div>
              {c.note && <div className="sc-note">{c.note}</div>}
              {c.evidence && <div className="sc-evidence">“{c.evidence}”</div>}
            </li>
          );
        })}
      </ul>
      {r.suggestions?.length > 0 && (
        <div className="sc-suggestions">
          <div className="sc-block-head"><Lightbulb size={13} /> Suggestions</div>
          <ul>{r.suggestions.map((sug, i) => <li key={i}>{sug}</li>)}</ul>
        </div>
      )}
      {!r.ai && (
        <div className="sc-fallback">Quick keyword check (AI offline) — clear, labeled headings help most.</div>
      )}
    </div>
  );
}

const PAIR_STATUS = {
  aligned: { cls: "sc-ok", icon: <CheckCircle2 size={13} />, label: "aligned" },
  gap: { cls: "sc-bad", icon: <AlertTriangle size={13} />, label: "gap" },
  unclear: { cls: "sc-warn", icon: <HelpCircle size={13} />, label: "check by hand" },
};

function CoherenceView({ r }) {
  if (!r.ready) {
    return <div className="sc-result"><div className="sc-summary">{r.summary}</div></div>;
  }
  return (
    <div className="sc-result">
      <div className="sc-summary">{r.summary}</div>
      <ul className="sc-checklist">
        {r.pairs.map((p, i) => {
          const s = PAIR_STATUS[p.status] || PAIR_STATUS.unclear;
          return (
            <li key={i} className={`sc-check ${s.cls}`}>
              <div className="sc-check-head">{s.icon} <b>{p.a} ↔ {p.b}</b> <span className="sc-status">{s.label}</span></div>
              {p.note && <div className="sc-note">{p.note}</div>}
              {p.evidence_a && <div className="sc-evidence">{p.a}: “{p.evidence_a}”</div>}
              {p.evidence_b && <div className="sc-evidence">{p.b}: “{p.evidence_b}”</div>}
            </li>
          );
        })}
      </ul>
      {r.suggestions?.length > 0 && (
        <div className="sc-suggestions">
          <div className="sc-block-head"><Lightbulb size={13} /> Suggestions</div>
          <ul>{r.suggestions.map((sug, i) => <li key={i}>{sug}</li>)}</ul>
        </div>
      )}
      {!r.ai && (
        <div className="sc-fallback">Offline mode — compare these pairs by hand; no AI grounding was applied.</div>
      )}
    </div>
  );
}
