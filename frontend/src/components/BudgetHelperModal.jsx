import React, { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { X, Plus, Trash2, Calculator, FileText, Save, AlertTriangle, CheckCircle2 } from "lucide-react";
import { getApiBase } from "../lib/apiBase";
import "./BudgetHelperModal.css";

const API_BASE = getApiBase();
const fmt = (n) => `$${Number(n || 0).toLocaleString(undefined, { maximumFractionDigits: 0 })}`;
const authHeaders = () => ({
  "Content-Type": "application/json",
  Authorization: `Bearer ${localStorage.getItem("token")}`,
});

const EMPTY = {
  people: [{ name: "", base_salary: "", effort_pct: "", fringe: "faculty_ay" }],
  equipment: "", travel: "", supplies: "", participant_support: "", other: "",
  subawards: [],
  fa_year: "fy_2025_2026", fa_rate_key: "organized_research_on_campus",
  cap: "",
};

// Pull a "Budget cap: $500,000" out of the proposal's solicitation notes, if present.
function capFromNotes(notes) {
  if (!notes) return "";
  const m = String(notes).match(/Budget cap:\s*\$?([\d,]+)/i);
  return m ? m[1].replace(/,/g, "") : "";
}

export default function BudgetHelperModal({ submission, onClose, onSaved }) {
  const [inputs, setInputs] = useState(EMPTY);
  const [computed, setComputed] = useState(null);
  const [rates, setRates] = useState(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [savedMsg, setSavedMsg] = useState("");
  const [justification, setJustification] = useState("");
  const [justifying, setJustifying] = useState(false);
  const debounceRef = useRef(null);

  // Load rate tables + any previously-saved budget for this proposal.
  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const [rRates, rBudget] = await Promise.all([
          fetch(`${API_BASE}/api/budget/rates`, { headers: authHeaders() }),
          fetch(`${API_BASE}/api/me/submissions/${submission.id}/budget`, { headers: authHeaders() }),
        ]);
        const ratesData = rRates.ok ? await rRates.json() : null;
        const budgetData = rBudget.ok ? await rBudget.json() : null;
        if (!alive) return;
        setRates(ratesData);
        const saved = budgetData?.inputs && Object.keys(budgetData.inputs).length ? budgetData.inputs : null;
        if (saved) {
          setInputs({ ...EMPTY, ...saved, people: saved.people?.length ? saved.people : EMPTY.people });
          setComputed(budgetData.computed);
        } else {
          // fresh — prefill the cap from the solicitation if we can find one
          setInputs((p) => ({ ...p, cap: capFromNotes(submission.notes) }));
        }
      } finally {
        if (alive) setLoading(false);
      }
    })();
    return () => { alive = false; };
  }, [submission.id, submission.notes]);

  // Debounced live recompute whenever the inputs change.
  useEffect(() => {
    if (loading) return;
    clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(async () => {
      try {
        const r = await fetch(`${API_BASE}/api/budget/compute`, {
          method: "POST", headers: authHeaders(), body: JSON.stringify(inputs),
        });
        if (r.ok) setComputed(await r.json());
      } catch { /* keep last good total */ }
    }, 300);
    return () => clearTimeout(debounceRef.current);
  }, [inputs, loading]);

  const set = (patch) => { setInputs((p) => ({ ...p, ...patch })); setSavedMsg(""); };
  const setPerson = (i, patch) =>
    setInputs((p) => ({ ...p, people: p.people.map((row, j) => (j === i ? { ...row, ...patch } : row)) }));
  const addPerson = () =>
    set({ people: [...inputs.people, { name: "", base_salary: "", effort_pct: "", fringe: "faculty_ay" }] });
  const removePerson = (i) => set({ people: inputs.people.filter((_, j) => j !== i) });
  const setSubaward = (i, v) => set({ subawards: inputs.subawards.map((s, j) => (j === i ? v : s)) });
  const addSubaward = () => set({ subawards: [...inputs.subawards, ""] });
  const removeSubaward = (i) => set({ subawards: inputs.subawards.filter((_, j) => j !== i) });

  const faOptions = rates?.fa_rates?.[inputs.fa_year] || [];
  const fringeOptions = rates?.fringe_rates || [];

  const save = async () => {
    setSaving(true); setSavedMsg("");
    try {
      const r = await fetch(`${API_BASE}/api/me/submissions/${submission.id}/budget`, {
        method: "PUT", headers: authHeaders(), body: JSON.stringify({ inputs }),
      });
      if (r.ok) {
        const data = await r.json();
        setComputed(data.computed);
        setSavedMsg("Budget saved.");
        onSaved?.();
      } else {
        setSavedMsg("Could not save — try again.");
      }
    } finally { setSaving(false); }
  };

  const draft = async () => {
    setJustifying(true);
    try {
      const r = await fetch(`${API_BASE}/api/budget/justification`, {
        method: "POST", headers: authHeaders(), body: JSON.stringify({ inputs, use_ai: true }),
      });
      if (r.ok) setJustification((await r.json()).justification || "");
    } finally { setJustifying(false); }
  };

  const cap = computed?.cap_status;
  const numField = (label, key, hint) => (
    <label className="bh-field">
      <span>{label}{hint && <em className="bh-hint"> {hint}</em>}</span>
      <input type="number" min="0" inputMode="decimal" value={inputs[key]}
        onChange={(e) => set({ [key]: e.target.value })} placeholder="$0" />
    </label>
  );

  return createPortal(
    <div className="bh-overlay" onClick={onClose}>
      <div className="bh-modal" onClick={(e) => e.stopPropagation()}>
        <header className="bh-header">
          <div className="bh-title"><Calculator size={18} /> Budget Helper
            <span className="bh-sub">{submission.title}</span></div>
          <button className="bh-close" onClick={onClose} aria-label="Close"><X size={18} /></button>
        </header>

        {loading ? (
          <div className="bh-loading">Loading rates…</div>
        ) : (
          <div className="bh-body">
            {/* LEFT — line items */}
            <div className="bh-form">
              <h4>People &amp; effort</h4>
              {inputs.people.map((p, i) => (
                <div className="bh-person" key={i}>
                  <input className="bh-name" placeholder="Name / role" value={p.name}
                    onChange={(e) => setPerson(i, { name: e.target.value })} />
                  <input type="number" min="0" placeholder="Base salary" value={p.base_salary}
                    onChange={(e) => setPerson(i, { base_salary: e.target.value })} />
                  <input type="number" min="0" max="100" placeholder="% effort" value={p.effort_pct}
                    onChange={(e) => setPerson(i, { effort_pct: e.target.value })} />
                  <select value={p.fringe} onChange={(e) => setPerson(i, { fringe: e.target.value })}>
                    {fringeOptions.map((f) => (
                      <option key={f.key} value={f.key}>{f.label} ({Math.round(f.rate * 100)}%)</option>
                    ))}
                  </select>
                  <button className="bh-row-del" onClick={() => removePerson(i)} aria-label="Remove"><Trash2 size={14} /></button>
                </div>
              ))}
              <button className="bh-add" onClick={addPerson}><Plus size={14} /> Add person</button>

              <h4>Other direct costs</h4>
              <div className="bh-grid">
                {numField("Equipment", "equipment", "(F&A-exempt)")}
                {numField("Travel", "travel")}
                {numField("Materials & supplies", "supplies")}
                {numField("Participant support", "participant_support", "(F&A-exempt)")}
                {numField("Other", "other")}
              </div>

              <h4>Subawards <em className="bh-hint">(only first $25k of each is F&amp;A-eligible)</em></h4>
              {inputs.subawards.map((s, i) => (
                <div className="bh-subaward" key={i}>
                  <input type="number" min="0" placeholder="Subaward total" value={s}
                    onChange={(e) => setSubaward(i, e.target.value)} />
                  <button className="bh-row-del" onClick={() => removeSubaward(i)} aria-label="Remove"><Trash2 size={14} /></button>
                </div>
              ))}
              <button className="bh-add" onClick={addSubaward}><Plus size={14} /> Add subaward</button>

              <h4>F&amp;A (indirect) rate</h4>
              <div className="bh-grid">
                <label className="bh-field"><span>Fiscal year</span>
                  <select value={inputs.fa_year} onChange={(e) => set({ fa_year: e.target.value })}>
                    <option value="fy_2025_2026">FY 2025–2026</option>
                    <option value="fy_2024_2025">FY 2024–2025</option>
                  </select>
                </label>
                <label className="bh-field"><span>Rate type</span>
                  <select value={inputs.fa_rate_key} onChange={(e) => set({ fa_rate_key: e.target.value })}>
                    {faOptions.map((o) => (
                      <option key={o.key} value={o.key}>{o.label} ({Math.round(o.rate * 100)}%)</option>
                    ))}
                  </select>
                </label>
                {numField("Sponsor cap", "cap", "(optional)")}
              </div>
            </div>

            {/* RIGHT — live summary */}
            <aside className="bh-summary">
              <h4>Summary</h4>
              <div className="bh-line"><span>Direct costs</span><b>{fmt(computed?.direct_costs)}</b></div>
              <div className="bh-line bh-muted"><span>MTDC base</span><span>{fmt(computed?.mtdc_base)}</span></div>
              <div className="bh-line bh-muted">
                <span>F&amp;A {computed ? `${Math.round(computed.fa_rate * 100)}%` : ""}</span>
                <span>{fmt(computed?.fa_amount)}</span>
              </div>
              <div className="bh-line bh-total"><span>TOTAL</span><b>{fmt(computed?.total)}</b></div>

              {cap === "ok" && (
                <div className="bh-cap bh-cap-ok"><CheckCircle2 size={14} /> Under the {fmt(computed.cap)} cap</div>
              )}
              {cap === "over" && (
                <div className="bh-cap bh-cap-over"><AlertTriangle size={14} /> Over cap by {fmt(computed.cap_overage)}</div>
              )}
              {cap === "none" && <div className="bh-cap bh-cap-none">No sponsor cap set</div>}

              {computed?.warnings?.length > 0 && (
                <ul className="bh-warnings">
                  {computed.warnings.map((w, i) => <li key={i}><AlertTriangle size={12} /> {w}</li>)}
                </ul>
              )}

              <div className="bh-actions">
                <button className="bh-btn bh-btn-primary" onClick={save} disabled={saving}>
                  <Save size={14} /> {saving ? "Saving…" : "Save budget"}
                </button>
                <button className="bh-btn" onClick={draft} disabled={justifying}>
                  <FileText size={14} /> {justifying ? "Drafting…" : "Draft justification"}
                </button>
              </div>
              {savedMsg && <div className="bh-saved">{savedMsg}</div>}
            </aside>
          </div>
        )}

        {justification && (
          <div className="bh-justification">
            <div className="bh-justification-head">
              <span>Budget justification (draft — review before use)</span>
              <button onClick={() => navigator.clipboard?.writeText(justification)}>Copy</button>
            </div>
            <textarea readOnly value={justification} rows={10} />
          </div>
        )}
      </div>
    </div>,
    document.body
  );
}
