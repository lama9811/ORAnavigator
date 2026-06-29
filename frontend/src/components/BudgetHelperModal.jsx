import React, { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { X, Plus, Trash2, Calculator, FileText, Save, AlertTriangle, CheckCircle2, Lightbulb, Info, HelpCircle, Download } from "lucide-react";
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
  project_years: 1, escalation_pct: "",
};

// Pull a "Budget cap: $500,000" out of the proposal's solicitation notes, if present.
function capFromNotes(notes) {
  if (!notes) return "";
  const m = String(notes).match(/Budget cap:\s*\$?([\d,]+)/i);
  return m ? m[1].replace(/,/g, "") : "";
}

// Pull the per-category caps out of a "Category caps: Category I — $30,000,000; …"
// notes line, if present. Returns [{category, cap}] with cap as a numeric string
// (to match the <input> value type), or [] when there's no such line.
function categoryCapsFromNotes(notes) {
  if (!notes) return [];
  const line = String(notes).match(/^Category caps:\s*(.+)$/m);
  if (!line) return [];
  return line[1]
    .split(";")
    .map((part) => {
      const m = part.match(/^\s*(.+?)\s*—\s*\$?([\d,]+)/);
      if (!m) return null;
      return { category: m[1].trim(), cap: m[2].replace(/,/g, "") };
    })
    .filter(Boolean);
}

export default function BudgetHelperModal({ submission, onClose, onSaved }) {
  const [inputs, setInputs] = useState(EMPTY);
  const [computed, setComputed] = useState(null);
  const [rates, setRates] = useState(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [savedMsg, setSavedMsg] = useState("");
  const [justification, setJustification] = useState("");
  const [perLine, setPerLine] = useState([]);
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
          // fresh — if the solicitation has multiple category caps, leave the
          // cap blank so the PI must pick a category; otherwise prefill the
          // single cap as before.
          const cats = categoryCapsFromNotes(submission.notes);
          setInputs((p) => ({
            ...p,
            cap: cats.length >= 2 ? "" : capFromNotes(submission.notes),
          }));
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
  const categoryCaps = categoryCapsFromNotes(submission.notes);

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
      if (r.ok) {
        const data = await r.json();
        setJustification(data.justification || "");
        setPerLine(data.per_line || []);
      }
    } finally { setJustifying(false); }
  };

  const downloadCsv = async () => {
    // Save current inputs first so the export reflects what's on screen.
    await fetch(`${API_BASE}/api/me/submissions/${submission.id}/budget`, {
      method: "PUT", headers: authHeaders(), body: JSON.stringify({ inputs }),
    }).catch(() => {});
    const r = await fetch(`${API_BASE}/api/me/submissions/${submission.id}/budget.csv`,
      { headers: authHeaders() });
    if (!r.ok) return;
    const blob = await r.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url; a.download = `budget-${submission.id}.csv`;
    document.body.appendChild(a); a.click(); a.remove();
    URL.revokeObjectURL(url);
  };

  const cap = computed?.cap_status;
  const guide = rates || {};
  const Tip = ({ text }) =>
    text ? <span className="bh-tip" title={text} aria-label={text}><HelpCircle size={12} /></span> : null;
  const numField = (label, key, hint, tipKey) => (
    <label className="bh-field">
      <span>{label}{hint && <em className="bh-hint"> {hint}</em>}<Tip text={guide.category_guidance?.[tipKey]} /></span>
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

        <div className="bh-scroll">
        {loading ? (
          <div className="bh-loading">Loading rates…</div>
        ) : (
          <div className="bh-body">
            {/* LEFT — line items */}
            <div className="bh-form">
              <h4>People &amp; effort <Tip text={guide.fringe_guidance} /></h4>
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
                {numField("Equipment", "equipment", "(F&A-exempt)", "equipment")}
                {numField("Travel", "travel", null, "travel")}
                {numField("Materials & supplies", "supplies", null, "supplies")}
                {numField("Participant support", "participant_support", "(F&A-exempt)", "participant_support")}
                {numField("Other", "other", null, "other")}
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
                <label className="bh-field"><span>Rate type<Tip text={guide.fa_guidance} /></span>
                  <select value={inputs.fa_rate_key} onChange={(e) => set({ fa_rate_key: e.target.value })}>
                    {faOptions.map((o) => (
                      <option key={o.key} value={o.key}>{o.label} ({Math.round(o.rate * 100)}%)</option>
                    ))}
                  </select>
                </label>
                {categoryCaps.length >= 2 && (
                  <label className="bh-field"><span>Funding category</span>
                    <select
                      value={categoryCaps.find((c) => c.cap === String(inputs.cap))?.category || ""}
                      onChange={(e) => {
                        const picked = categoryCaps.find((c) => c.category === e.target.value);
                        set({ cap: picked ? picked.cap : "" });
                      }}>
                      <option value="">Select your category…</option>
                      {categoryCaps.map((c) => (
                        <option key={c.category} value={c.category}>
                          {c.category} — {fmt(c.cap)}
                        </option>
                      ))}
                    </select>
                  </label>
                )}
                {numField("Sponsor cap", "cap", "(optional)")}
              </div>

              <h4>Project length</h4>
              <div className="bh-grid">
                <label className="bh-field"><span>Years</span>
                  <select value={inputs.project_years}
                    onChange={(e) => set({ project_years: Number(e.target.value) })}>
                    {[1, 2, 3, 4, 5].map((n) => <option key={n} value={n}>{n}</option>)}
                  </select>
                </label>
                {inputs.project_years > 1 && (
                  <label className="bh-field">
                    <span>Annual escalation %<em className="bh-hint"> (e.g. 3)</em>
                      <Tip text={guide.escalation_guidance} /></span>
                    <input type="number" min="0" inputMode="decimal" value={inputs.escalation_pct}
                      onChange={(e) => set({ escalation_pct: e.target.value })} placeholder="$0" />
                  </label>
                )}
              </div>

              <EffortHelper people={inputs.people} />
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
              <div className="bh-line bh-total">
                <span>{computed?.multi_year ? "TOTAL (Year 1)" : "TOTAL"}</span><b>{fmt(computed?.total)}</b>
              </div>

              {computed?.multi_year && (
                <div className="bh-multiyear">
                  <div className="bh-multiyear-head">
                    {computed.multi_year.project_years}-year projection
                    {computed.multi_year.escalation_pct ? ` · ${computed.multi_year.escalation_pct}%/yr` : ""}
                  </div>
                  {computed.multi_year.years.map((y) => (
                    <div className="bh-line bh-muted" key={y.year}>
                      <span>Year {y.year}
                        {y.salary_delta > 0 &&
                          <em className="bh-delta"> salary +{fmt(y.salary_delta)} ({y.salary_delta_pct}%)</em>}
                      </span>
                      <span>{fmt(y.total)}</span>
                    </div>
                  ))}
                  <div className="bh-line bh-total"><span>All years</span><b>{fmt(computed.multi_year.cumulative.total)}</b></div>
                  {computed.multi_year.cap_status === "over" && (
                    <div className="bh-cap bh-cap-over"><AlertTriangle size={14} /> Over the {fmt(computed.multi_year.cap)} {computed.multi_year.project_years}-year TOTAL cap by {fmt(computed.multi_year.cap_overage)} (cumulative)</div>
                  )}
                  {computed.multi_year.cap_status === "ok" && (
                    <div className="bh-cap bh-cap-ok"><CheckCircle2 size={14} /> Under the {fmt(computed.multi_year.cap)} {computed.multi_year.project_years}-year TOTAL cap (cumulative)</div>
                  )}
                </div>
              )}

              {!computed?.multi_year && cap === "ok" && (
                <div className="bh-cap bh-cap-ok"><CheckCircle2 size={14} /> Under the {fmt(computed.cap)} cap</div>
              )}
              {!computed?.multi_year && cap === "over" && (
                <div className="bh-cap bh-cap-over"><AlertTriangle size={14} /> Over cap by {fmt(computed.cap_overage)}</div>
              )}
              {!computed?.multi_year && cap === "none" && <div className="bh-cap bh-cap-none">No sponsor cap set</div>}

              {computed?.warnings?.length > 0 && (
                <ul className="bh-warnings">
                  {computed.warnings.map((w, i) => <li key={i}><AlertTriangle size={12} /> {w}</li>)}
                </ul>
              )}

              {/* Coaching: things to double-check (advisory, never blocks) */}
              {computed?.advisories?.length > 0 && (
                <div className="bh-coach">
                  <div className="bh-coach-head"><Info size={13} /> Things to double-check</div>
                  <ul className="bh-coach-list">
                    {computed.advisories.map((a, i) => (
                      <li key={i} className={`bh-advisory bh-advisory-${a.severity}`}>
                        <div className="bh-advisory-msg">{a.message}</div>
                        {a.fix && <div className="bh-advisory-fix">{a.fix}</div>}
                      </li>
                    ))}
                  </ul>
                </div>
              )}

              {/* Coaching: how to get under the cap (only when over) */}
              {computed?.trim_suggestions?.length > 0 && (
                <div className="bh-coach bh-coach-trim">
                  <div className="bh-coach-head"><Lightbulb size={13} /> Ideas to get under the cap</div>
                  <ul className="bh-coach-list">
                    {computed.trim_suggestions.map((t, i) => (
                      <li key={i} className="bh-trim">
                        <div className="bh-trim-line"><b>{t.line}</b> — reduce by {fmt(t.reduce_by)}</div>
                        <div className="bh-advisory-fix">{t.rationale}</div>
                      </li>
                    ))}
                  </ul>
                </div>
              )}

              <div className="bh-actions">
                <button className="bh-btn bh-btn-primary" onClick={save} disabled={saving}>
                  <Save size={14} /> {saving ? "Saving…" : "Save budget"}
                </button>
                <button className="bh-btn" onClick={draft} disabled={justifying}>
                  <FileText size={14} /> {justifying ? "Drafting…" : "Draft justification"}
                </button>
                <button className="bh-btn" onClick={downloadCsv} title="Download the budget as a CSV (opens in Excel/Sheets).">
                  <Download size={14} /> Export CSV
                </button>
              </div>
              {savedMsg && <div className="bh-saved">{savedMsg}</div>}
            </aside>
          </div>
        )}

        {computed?.table?.rows?.length > 0 && <BudgetTable table={computed.table} />}

        {justification && (
          <div className="bh-justification">
            <div className="bh-justification-head">
              <span>Budget justification (draft — review before use)</span>
              <div className="bh-justification-actions">
                <button onClick={() => navigator.clipboard?.writeText(justification)}>Copy</button>
                <button
                  className="bh-justification-close"
                  onClick={() => { setJustification(""); setPerLine([]); }}
                  aria-label="Close justification draft"
                  title="Close"
                >
                  <X size={14} />
                </button>
              </div>
            </div>
            <textarea readOnly value={justification} rows={10} />
            {perLine.length > 0 && (
              <div className="bh-perline">
                <div className="bh-perline-head">Line-by-line</div>
                <ul>
                  {perLine.map((l, i) => (
                    <li key={i}><b>{l.line}</b> — {l.text}</li>
                  ))}
                </ul>
              </div>
            )}
          </div>
        )}
        </div>
      </div>
    </div>,
    document.body
  );
}

// Read-only spreadsheet view of the computed budget: every line as a row and,
// for a multi-year project, each year as a column. Binds straight to the
// backend's render-ready `table` model ({columns, rows}) — it formats numbers
// but computes none, so it can never disagree with the summary or the CSV.
function BudgetTable({ table }) {
  if (!table?.rows?.length) return null;
  const { columns, rows } = table;
  return (
    <div className="bh-grid-wrap">
      <div className="bh-grid-head"><Calculator size={14} /> Budget table</div>
      <div className="bh-grid-scroll">
        <table className="bh-table">
          <thead>
            <tr>
              <th className="bh-grid-cat">Category</th>
              <th className="bh-grid-detail">Detail</th>
              {columns.map((c) => <th key={c} className="bh-grid-num">{c}</th>)}
            </tr>
          </thead>
          <tbody>
            {rows.map((r, i) => (
              <tr key={i} className={`bh-grid-${r.kind}`}>
                <td className="bh-grid-cat">{r.label}</td>
                <td className="bh-grid-detail">{r.detail}</td>
                {r.values.map((v, j) => (
                  <td key={j} className="bh-grid-num">{fmt(v)}</td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

// Effort -> person-months readout (academic 9-mo, summer 3-mo, calendar
// 12-mo). READ-ONLY: the % effort comes straight from each person in the
// People rows above, so this can never disagree with the budget. To change
// effort, edit the person's "% effort" field — not here.
function EffortHelper({ people }) {
  const [open, setOpen] = useState(false);
  const months = (pct, base) => (base * pct / 100).toFixed(2);
  const rows = (people || [])
    .map((p, i) => ({ name: (p.name || "").trim() || `Person ${i + 1}`, pct: Number(p.effort_pct) }))
    .filter((r) => r.pct > 0);
  return (
    <div className="bh-effort">
      <button type="button" className="bh-effort-toggle" onClick={() => setOpen((o) => !o)}>
        <HelpCircle size={12} /> {open ? "Hide" : "Effort → person-months"}
      </button>
      {open && (
        <div className="bh-effort-body">
          {rows.length === 0 ? (
            <small>Enter a % effort for someone in “People &amp; effort” above to see their person-months.</small>
          ) : (
            rows.map((r, i) => (
              <div className="bh-effort-row" key={i}>
                <span className="bh-effort-name">{r.name} — {r.pct}% effort</span>
                <div className="bh-effort-out">
                  <span>Academic (9 mo): <b>{months(r.pct, 9)}</b></span>
                  <span>Summer (3 mo): <b>{months(r.pct, 3)}</b></span>
                  <span>Calendar (12 mo): <b>{months(r.pct, 12)}</b></span>
                </div>
              </div>
            ))
          )}
          <small>person-months = appointment months × % effort (read from the People rows above)</small>
        </div>
      )}
    </div>
  );
}
