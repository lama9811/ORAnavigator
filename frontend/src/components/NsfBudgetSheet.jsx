import React, { useEffect, useMemo, useRef, useState } from "react";
import {
  AlertTriangle, CheckCircle2, Download, FileText, Info, Plus, Save, Trash2,
} from "lucide-react";
import { getApiBase } from "../lib/apiBase";
import "./NsfBudgetSheet.css";

const API_BASE = getApiBase();
const fmt = (n) => `$${Number(n || 0).toLocaleString(undefined, { maximumFractionDigits: 0 })}`;
const token = () => localStorage.getItem("token");
const authHeaders = () => ({
  "Content-Type": "application/json",
  Authorization: `Bearer ${token()}`,
});

// Form 1030 line B rows, in the form's own order.
const OTHER_PERSONNEL = [
  ["postdocs", "Postdoctoral Scholars", true],
  ["other_professionals", "Other Professionals (Technician, Programmer, etc.)", true],
  ["grad_students", "Graduate Students", false],
  ["undergrads", "Undergraduate Students", false],
  ["clerical", "Secretarial – Clerical (if charged directly)", false],
  ["other", "Other", false],
];

const G_LINES = [
  ["materials_supplies", "1. Materials and Supplies"],
  ["publication", "2. Publication Costs / Documentation / Dissemination"],
  ["consultant", "3. Consultant Services"],
  ["computer_services", "4. Computer Services"],
];

// Immutably set a value at a nested path, e.g. ["travel", "domestic"].
function setIn(obj, path, value) {
  if (!path.length) return value;
  const [head, ...rest] = path;
  const next = Array.isArray(obj) ? [...obj] : { ...obj };
  next[head] = setIn(obj?.[head], rest, value);
  return next;
}
const getIn = (obj, path) => path.reduce((o, k) => o?.[k], obj);

/** A money box. Blank means "not filled in" — never renders a placeholder 0. */
function Money({ value, onChange, readOnly }) {
  if (readOnly) return <span className="nsf-computed">{fmt(value)}</span>;
  return (
    <input
      className="nsf-money" type="number" min="0" inputMode="decimal"
      value={value ?? ""} placeholder="—"
      onChange={(e) => onChange(e.target.value === "" ? null : Number(e.target.value))}
    />
  );
}

export default function NsfBudgetSheet({ submission, doc, onChange, onSaved }) {
  const [activeYear, setActiveYear] = useState(1);   // 0 = the Cumulative tab
  const [computed, setComputed] = useState(null);
  const [saving, setSaving] = useState(false);
  const [msg, setMsg] = useState("");
  const [justification, setJustification] = useState("");
  const [justifying, setJustifying] = useState(false);
  const debounceRef = useRef(null);

  // Debounced recompute. Keeps the last good totals if a request fails.
  useEffect(() => {
    if (!doc) return;
    clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(async () => {
      try {
        const r = await fetch(`${API_BASE}/api/budget/nsf/compute`, {
          method: "POST", headers: authHeaders(), body: JSON.stringify({ inputs: doc }),
        });
        if (r.ok) setComputed(await r.json());
      } catch { /* keep the last good totals */ }
    }, 300);
    return () => clearTimeout(debounceRef.current);
  }, [doc]);

  const years = doc?.years || [];
  const isCumulative = activeYear === 0;
  const idx = activeYear - 1;
  const sheet = isCumulative ? null : years[idx];
  const yc = isCumulative ? computed?.cumulative : computed?.years?.[idx];
  const L = yc?.lines;
  const ro = isCumulative;                            // the cumulative tab is read-only

  const flags = useMemo(() => {
    if (!computed) return [];
    return isCumulative
      ? computed.flags || []
      : (computed.flags || []).filter((f) => !f.year || f.year === activeYear);
  }, [computed, isCumulative, activeYear]);

  // ── mutation helpers ────────────────────────────────────────────────────
  const patch = (path, value) => {
    setMsg("");
    onChange(setIn(doc, ["years", idx, ...path], value));
  };
  const patchItem = (path, i, key, value) => {
    const list = [...(getIn(sheet, path) || [])];
    list[i] = { ...list[i], [key]: value };
    patch(path, list);
  };
  const addItem = (path, blank) => patch(path, [...(getIn(sheet, path) || []), blank]);
  const removeItem = (path, i) =>
    patch(path, (getIn(sheet, path) || []).filter((_, j) => j !== i));

  const addYear = async () => {
    const r = await fetch(`${API_BASE}/api/budget/nsf/add-year`, {
      method: "POST", headers: authHeaders(), body: JSON.stringify({ inputs: doc }),
    });
    if (!r.ok) { setMsg("Could not add a year — try again."); return; }
    const next = (await r.json()).document;
    onChange(next);
    setActiveYear(next.years.length);
  };

  const removeYear = () => {
    if (years.length <= 1) return;
    onChange({
      ...doc,
      years: years.filter((_, j) => j !== idx).map((y, j) => ({ ...y, year: j + 1 })),
    });
    setActiveYear(Math.max(1, activeYear - 1));
  };

  const save = async () => {
    setSaving(true); setMsg("");
    try {
      const r = await fetch(`${API_BASE}/api/me/submissions/${submission.id}/budget`, {
        method: "PUT", headers: authHeaders(), body: JSON.stringify({ inputs: doc }),
      });
      if (r.ok) {
        setComputed((await r.json()).computed);
        setMsg("Budget saved.");
        onSaved?.();
      } else setMsg("Could not save — try again.");
    } finally { setSaving(false); }
  };

  const draft = async () => {
    setJustifying(true);
    try {
      const r = await fetch(`${API_BASE}/api/budget/nsf/justification`, {
        method: "POST", headers: authHeaders(),
        body: JSON.stringify({ inputs: doc, use_ai: true }),
      });
      if (r.ok) setJustification((await r.json()).justification || "");
    } finally { setJustifying(false); }
  };

  // The .xlsx lives on the BACKEND origin, so an <a download> would be ignored
  // cross-origin. Fetch it and save a same-origin blob instead.
  const downloadXlsx = async () => {
    setMsg("");
    try {
      const r = await fetch(
        `${API_BASE}/api/me/submissions/${submission.id}/budget.xlsx`,
        { headers: { Authorization: `Bearer ${token()}` } },
      );
      if (!r.ok) { setMsg("Save the budget first, then download."); return; }
      const url = URL.createObjectURL(await r.blob());
      const a = document.createElement("a");
      a.href = url;
      a.download = `${submission.title || "budget"}.xlsx`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    } catch {
      setMsg("Could not download the workbook.");
    }
  };

  if (!doc) return <div className="nsf-loading">Loading the NSF form…</div>;

  const settings = doc.settings || {};
  const meta = doc.meta || {};

  return (
    <div className="nsf-wrap">
      {/* ── year tabs ─────────────────────────────────────────────────── */}
      <div className="nsf-tabs">
        {years.map((y, i) => (
          <button key={i} className={`nsf-tab ${activeYear === i + 1 ? "is-active" : ""}`}
            onClick={() => setActiveYear(i + 1)}>Year {i + 1}</button>
        ))}
        <button className="nsf-tab nsf-tab-add" onClick={addYear}>
          <Plus size={13} /> Add year
        </button>
        <button className={`nsf-tab nsf-tab-cum ${isCumulative ? "is-active" : ""}`}
          onClick={() => setActiveYear(0)}>Cumulative</button>
      </div>

      <div className="nsf-body">
        {/* ── the A–M sheet ───────────────────────────────────────────── */}
        <div className="nsf-sheet">
          {isCumulative && (
            <p className="nsf-note">
              Read-only. Every line is the sum of the {years.length} year sheets.
            </p>
          )}

          {/* A */}
          <h4 className="nsf-line-head">A. Senior / Key Personnel</h4>
          <div className="nsf-col-head">
            <span>Name / role</span><span>Base salary</span>
            <span>CAL</span><span>ACAD</span><span>SUMR</span>
            <span>Fringe</span><span>Requested</span><span />
          </div>
          {ro ? (
            <div className="nsf-total-row"><span>Total senior / key personnel</span>
              <span className="nsf-computed">{fmt(L?.A?.total)}</span></div>
          ) : (
            <>
              {(sheet.senior || []).map((p, i) => {
                const row = L?.A?.rows?.[i];
                return (
                  <div className="nsf-senior" key={i}>
                    <input placeholder="Name" value={p.name || ""}
                      onChange={(e) => patchItem(["senior"], i, "name", e.target.value)} />
                    <input type="number" min="0" placeholder="Base salary"
                      value={p.base_salary ?? ""}
                      onChange={(e) => patchItem(["senior"], i, "base_salary",
                        e.target.value === "" ? null : Number(e.target.value))} />
                    {["cal", "acad", "sumr"].map((k) => (
                      <input key={k} type="number" min="0" max="12" step="0.1" placeholder="0"
                        value={p[k] ?? ""}
                        onChange={(e) => patchItem(["senior"], i, k,
                          e.target.value === "" ? 0 : Number(e.target.value))} />
                    ))}
                    <select value={p.fringe_key || "faculty_ay"}
                      onChange={(e) => patchItem(["senior"], i, "fringe_key", e.target.value)}>
                      <option value="faculty_ay">Faculty AY (42%)</option>
                      <option value="faculty_summer">Faculty Summer (9%)</option>
                      <option value="full_time">Full-time (42%)</option>
                      <option value="contractual">Contractual (9%)</option>
                    </select>
                    <span className="nsf-computed">
                      {fmt(row?.salary)}
                      {row?.effort_pct > 0 && (
                        <em className="nsf-effort">{row.effort_pct.toFixed(0)}% effort</em>
                      )}
                    </span>
                    <button className="nsf-del" aria-label="Remove"
                      onClick={() => removeItem(["senior"], i)}><Trash2 size={13} /></button>
                  </div>
                );
              })}
              <div className="nsf-row-actions">
                <button className="nsf-add" onClick={() => addItem(["senior"], {
                  name: "", role: "", appointment_basis: "academic_9", base_salary: null,
                  cal: 0, acad: 0, sumr: 0, fringe_key: "faculty_ay",
                })}><Plus size={13} /> Add senior person</button>
                <select className="nsf-basis"
                  value={sheet.senior?.[0]?.appointment_basis || "academic_9"}
                  onChange={(e) => patch(["senior"],
                    (sheet.senior || []).map((p) => ({ ...p, appointment_basis: e.target.value })))}>
                  <option value="academic_9">9-month academic appointment</option>
                  <option value="calendar_12">12-month calendar appointment</option>
                </select>
              </div>
              <div className="nsf-total-row"><span>Total senior / key personnel</span>
                <span className="nsf-computed">{fmt(L?.A?.total)}</span></div>
            </>
          )}

          {/* B */}
          <h4 className="nsf-line-head">B. Other Personnel</h4>
          {OTHER_PERSONNEL.map(([key, label, hasMonths]) => {
            const row = sheet?.other_personnel?.[key] || {};
            return (
              <div className="nsf-item" key={key}>
                {!ro && (
                  <input className="nsf-count" type="number" min="0" placeholder="0"
                    value={row.count ?? ""}
                    onChange={(e) => patch(["other_personnel", key],
                      { ...row, count: Number(e.target.value || 0) })} />
                )}
                <span className="nsf-item-label">{label}</span>
                {hasMonths && !ro && (
                  <input className="nsf-months" type="number" min="0" max="12" step="0.1"
                    placeholder="mo" value={row.months ?? ""}
                    onChange={(e) => patch(["other_personnel", key],
                      { ...row, months: Number(e.target.value || 0) })} />
                )}
                <Money readOnly={ro}
                  value={ro
                    ? L?.B?.rows?.find((r) => r.key === key)?.amount
                    : row.amount}
                  onChange={(v) => patch(["other_personnel", key], { ...row, amount: v })} />
              </div>
            );
          })}
          <div className="nsf-total-row"><span>Total salaries and wages (A + B)</span>
            <span className="nsf-computed">{fmt(L?.salaries_and_wages)}</span></div>

          {/* C */}
          <h4 className="nsf-line-head">C. Fringe Benefits</h4>
          <div className="nsf-item">
            <span className="nsf-item-label">
              Computed per person at Morgan's category rates, summed
            </span>
            <span className="nsf-computed">{fmt(L?.C)}</span>
          </div>
          <div className="nsf-total-row"><span>Total salaries, wages and fringe (A + B + C)</span>
            <span className="nsf-computed">{fmt(L?.salaries_wages_fringe)}</span></div>

          {/* D */}
          <h4 className="nsf-line-head">
            D. Equipment
            <em>list each item over $10,000; items under the ${
              Number(settings.capitalization_level || 5000).toLocaleString()
            } capitalization level belong in G.1</em>
          </h4>
          {ro ? (
            <div className="nsf-total-row"><span>Total equipment</span>
              <span className="nsf-computed">{fmt(L?.D?.total)}</span></div>
          ) : (
            <>
              {(sheet.equipment || []).map((it, i) => (
                <div className="nsf-item" key={i}>
                  <input className="nsf-desc" placeholder="Item description"
                    value={it.description || ""}
                    onChange={(e) => patchItem(["equipment"], i, "description", e.target.value)} />
                  <Money value={it.amount}
                    onChange={(v) => patchItem(["equipment"], i, "amount", v)} />
                  <button className="nsf-del" aria-label="Remove"
                    onClick={() => removeItem(["equipment"], i)}><Trash2 size={13} /></button>
                </div>
              ))}
              <button className="nsf-add" onClick={() =>
                addItem(["equipment"], { description: "", amount: null })}>
                <Plus size={13} /> Add equipment
              </button>
              <div className="nsf-total-row"><span>Total equipment</span>
                <span className="nsf-computed">{fmt(L?.D?.total)}</span></div>
            </>
          )}

          {/* E */}
          <h4 className="nsf-line-head">E. Travel</h4>
          {[["domestic", "1. Domestic (incl. U.S. possessions)"],
            ["international", "2. International"]].map(([key, label]) => (
            <div key={key}>
              <div className="nsf-sub-head">{label}</div>
              {ro ? (
                <div className="nsf-item"><span className="nsf-item-label">{label}</span>
                  <span className="nsf-computed">{fmt(L?.E?.[key])}</span></div>
              ) : (
                <>
                  {(sheet.travel?.[key] || []).map((it, i) => (
                    <div className="nsf-item" key={i}>
                      <input className="nsf-desc" placeholder="Purpose"
                        value={it.description || ""}
                        onChange={(e) =>
                          patchItem(["travel", key], i, "description", e.target.value)} />
                      <Money value={it.amount}
                        onChange={(v) => patchItem(["travel", key], i, "amount", v)} />
                      <button className="nsf-del" aria-label="Remove"
                        onClick={() => removeItem(["travel", key], i)}><Trash2 size={13} /></button>
                    </div>
                  ))}
                  <button className="nsf-add" onClick={() =>
                    addItem(["travel", key], { description: "", amount: null })}>
                    <Plus size={13} /> Add trip
                  </button>
                </>
              )}
            </div>
          ))}
          <div className="nsf-total-row"><span>Total travel</span>
            <span className="nsf-computed">{fmt(L?.E?.total)}</span></div>

          {/* F */}
          <h4 className="nsf-line-head">
            F. Participant Support Costs <em>no F&amp;A is charged on this line</em>
          </h4>
          {[["stipends", "1. Stipends"], ["travel", "2. Travel"],
            ["subsistence", "3. Subsistence"], ["other", "4. Other"]].map(([key, label]) => (
            <div className="nsf-item" key={key}>
              <span className="nsf-item-label">{label}</span>
              <Money readOnly={ro} value={ro ? L?.F?.[key] : sheet.participant_support?.[key]}
                onChange={(v) => patch(["participant_support"],
                  { ...sheet.participant_support, [key]: v })} />
            </div>
          ))}
          <div className="nsf-item">
            <span className="nsf-item-label">Total number of participants</span>
            {ro ? <span className="nsf-computed">{L?.F?.count ?? 0}</span> : (
              <input className="nsf-count" type="number" min="0" placeholder="0"
                value={sheet.participant_support?.count ?? ""}
                onChange={(e) => patch(["participant_support"],
                  { ...sheet.participant_support, count: Number(e.target.value || 0) })} />
            )}
          </div>
          <div className="nsf-total-row"><span>Total participant costs</span>
            <span className="nsf-computed">{fmt(L?.F?.total)}</span></div>

          {/* G */}
          <h4 className="nsf-line-head">G. Other Direct Costs</h4>
          {G_LINES.map(([key, label]) => (
            <div key={key}>
              <div className="nsf-sub-head">{label}</div>
              {ro ? (
                <div className="nsf-item"><span className="nsf-item-label">{label}</span>
                  <span className="nsf-computed">{fmt(L?.G?.[key])}</span></div>
              ) : (
                <>
                  {(sheet.other_direct?.[key] || []).map((it, i) => (
                    <div className="nsf-item" key={i}>
                      <input className="nsf-desc" placeholder="Description"
                        value={it.description || ""}
                        onChange={(e) =>
                          patchItem(["other_direct", key], i, "description", e.target.value)} />
                      <Money value={it.amount}
                        onChange={(v) => patchItem(["other_direct", key], i, "amount", v)} />
                      <button className="nsf-del" aria-label="Remove"
                        onClick={() => removeItem(["other_direct", key], i)}>
                        <Trash2 size={13} /></button>
                    </div>
                  ))}
                  <button className="nsf-add" onClick={() =>
                    addItem(["other_direct", key], { description: "", amount: null })}>
                    <Plus size={13} /> Add line
                  </button>
                </>
              )}
            </div>
          ))}

          <div className="nsf-sub-head">
            5. Subawards <em>only the first $25,000 of each is in the F&amp;A base</em>
          </div>
          {ro ? (
            <div className="nsf-item"><span className="nsf-item-label">Subawards</span>
              <span className="nsf-computed">{fmt(L?.G?.subawards?.total)}</span></div>
          ) : (
            <>
              {(sheet.other_direct?.subawards || []).map((s, i) => (
                <div className="nsf-item" key={i}>
                  <input className="nsf-desc" placeholder="Subrecipient organization"
                    value={s.organization || ""}
                    onChange={(e) =>
                      patchItem(["other_direct", "subawards"], i, "organization", e.target.value)} />
                  <Money value={s.amount}
                    onChange={(v) => patchItem(["other_direct", "subawards"], i, "amount", v)} />
                  <button className="nsf-del" aria-label="Remove"
                    onClick={() => removeItem(["other_direct", "subawards"], i)}>
                    <Trash2 size={13} /></button>
                </div>
              ))}
              <button className="nsf-add" onClick={() =>
                addItem(["other_direct", "subawards"], { organization: "", amount: null })}>
                <Plus size={13} /> Add subaward
              </button>
            </>
          )}

          <div className="nsf-sub-head">6. Other</div>
          {ro ? (
            <div className="nsf-item"><span className="nsf-item-label">Other</span>
              <span className="nsf-computed">{fmt(L?.G?.other)}</span></div>
          ) : (
            <>
              {(sheet.other_direct?.other || []).map((it, i) => (
                <div className="nsf-item nsf-item-exempt" key={i}>
                  <input className="nsf-desc" placeholder="Description"
                    value={it.description || ""}
                    onChange={(e) =>
                      patchItem(["other_direct", "other"], i, "description", e.target.value)} />
                  <label className="nsf-exempt"
                    title="Tuition remission, scholarships, rent, patient care — these are direct costs but carry no F&A">
                    <input type="checkbox" checked={!!it.mtdc_exempt}
                      onChange={(e) =>
                        patchItem(["other_direct", "other"], i, "mtdc_exempt", e.target.checked)} />
                    no F&amp;A
                  </label>
                  <Money value={it.amount}
                    onChange={(v) => patchItem(["other_direct", "other"], i, "amount", v)} />
                  <button className="nsf-del" aria-label="Remove"
                    onClick={() => removeItem(["other_direct", "other"], i)}>
                    <Trash2 size={13} /></button>
                </div>
              ))}
              <button className="nsf-add" onClick={() => addItem(["other_direct", "other"],
                { description: "", amount: null, mtdc_exempt: false })}>
                <Plus size={13} /> Add line
              </button>
            </>
          )}
          <div className="nsf-total-row"><span>Total other direct costs</span>
            <span className="nsf-computed">{fmt(L?.G?.total)}</span></div>

          {/* K / M */}
          <h4 className="nsf-line-head">K. Fee <em>SBIR/STTR and Major Facilities only</em></h4>
          <div className="nsf-item">
            <span className="nsf-item-label">Fee</span>
            <Money readOnly={ro} value={ro ? L?.K : sheet.fee}
              onChange={(v) => patch(["fee"], v)} />
          </div>

          <h4 className="nsf-line-head">
            M. Cost Sharing <em>voluntary committed cost sharing is prohibited</em>
          </h4>
          <div className="nsf-item">
            <span className="nsf-item-label">Proposed level</span>
            <Money readOnly={ro} value={ro ? L?.M : sheet.cost_sharing?.proposed}
              onChange={(v) => patch(["cost_sharing"], { ...sheet.cost_sharing, proposed: v })} />
          </div>

          {!isCumulative && years.length > 1 && (
            <button className="nsf-remove-year" onClick={removeYear}>
              Remove Year {activeYear}
            </button>
          )}
        </div>

        {/* ── sticky summary rail ─────────────────────────────────────── */}
        <aside className="nsf-rail">
          <h4>{isCumulative ? "Cumulative" : `Year ${activeYear}`}</h4>
          <div className="nsf-line"><span>H. Total direct costs</span><b>{fmt(L?.H)}</b></div>
          <div className="nsf-line nsf-muted">
            <span>MTDC base</span><span>{fmt(yc?.mtdc?.base)}</span>
          </div>
          <div className="nsf-line nsf-muted">
            {/* The cumulative sheet spans years, so it carries no single rate. */}
            <span>I. F&amp;A {isCumulative
              ? "(all years)"
              : yc?.fa ? `@ ${Math.round(yc.fa.rate * 100)}%` : ""}</span>
            <span>{fmt(L?.I)}</span>
          </div>
          <div className="nsf-line"><span>J. Direct + indirect</span><b>{fmt(L?.J)}</b></div>
          {L?.K > 0 && <div className="nsf-line nsf-muted"><span>K. Fee</span><span>{fmt(L.K)}</span></div>}
          <div className="nsf-line nsf-total"><span>L. AMOUNT REQUESTED</span><b>{fmt(L?.L)}</b></div>

          {computed?.cap?.status === "ok" && (
            <div className="nsf-cap nsf-cap-ok">
              <CheckCircle2 size={13} /> Under the {fmt(computed.cap.value)} cap
            </div>
          )}
          {computed?.cap?.status === "over" && (
            <div className="nsf-cap nsf-cap-over">
              <AlertTriangle size={13} /> Over cap by {fmt(computed.cap.overage)}
            </div>
          )}

          <div className="nsf-exclusions">
            <span>Excluded from the F&amp;A base</span>
            <div><span>Equipment</span><span>{fmt(yc?.mtdc?.exclusions?.equipment)}</span></div>
            <div><span>Participant support</span>
              <span>{fmt(yc?.mtdc?.exclusions?.participant_support)}</span></div>
            <div><span>Subawards over $25k</span>
              <span>{fmt(yc?.mtdc?.exclusions?.subaward_over_25k)}</span></div>
            <div><span>Marked no-F&amp;A</span>
              <span>{fmt(yc?.mtdc?.exclusions?.mtdc_exempt)}</span></div>
          </div>

          <div className="nsf-settings">
            <label><span>Project duration (months)</span>
              <input type="number" min="0" value={meta.duration_months ?? ""}
                onChange={(e) => onChange({
                  ...doc,
                  meta: { ...meta, duration_months: Number(e.target.value || 0) },
                })} />
            </label>
            <label><span>Program</span>
              <select value={meta.sponsor_program || "standard"}
                onChange={(e) => onChange({
                  ...doc, meta: { ...meta, sponsor_program: e.target.value },
                })}>
                <option value="standard">Standard</option>
                <option value="sbir_sttr">SBIR / STTR</option>
                <option value="major_facility">Major Facility</option>
              </select>
            </label>
            <label className="nsf-check">
              <input type="checkbox" checked={!!meta.mandatory_cost_sharing}
                onChange={(e) => onChange({
                  ...doc, meta: { ...meta, mandatory_cost_sharing: e.target.checked },
                })} />
              The solicitation mandates cost sharing
            </label>
          </div>

          <div className="nsf-actions">
            <button className="nsf-btn nsf-btn-primary" onClick={save} disabled={saving}>
              <Save size={14} /> {saving ? "Saving…" : "Save budget"}
            </button>
            <button className="nsf-btn" onClick={draft} disabled={justifying}>
              <FileText size={14} /> {justifying ? "Drafting…" : "Draft justification"}
            </button>
            <button className="nsf-btn" onClick={downloadXlsx}>
              <Download size={14} /> Download .xlsx
            </button>
          </div>
          {msg && <div className="nsf-msg">{msg}</div>}
        </aside>
      </div>

      {/* ── flags ───────────────────────────────────────────────────────── */}
      {flags.length > 0 && (
        <div className="nsf-flags">
          <h4>NSF checks <em>advisory — nothing here blocks you</em></h4>
          {flags.map((f, i) => (
            <div className={`nsf-flag nsf-flag-${f.severity}`} key={i}>
              {f.severity === "warn" ? <AlertTriangle size={13} /> : <Info size={13} />}
              <div>
                <b>{f.line !== "-" && <span className="nsf-flag-line">{f.line}</span>} {f.title}</b>
                {f.detail && <p className="nsf-flag-detail">{f.detail}</p>}
                <p className="nsf-flag-msg">{f.message}</p>
                <span className="nsf-flag-cite">{f.citation}</span>
              </div>
            </div>
          ))}
        </div>
      )}

      {computed?.warnings?.length > 0 && (
        <ul className="nsf-input-warnings">
          {computed.warnings.map((w, i) => <li key={i}><AlertTriangle size={12} /> {w}</li>)}
        </ul>
      )}

      {justification && (
        <div className="nsf-justification">
          <div className="nsf-justification-head">
            <span>Budget justification (draft — review before use)</span>
            <button onClick={() => navigator.clipboard?.writeText(justification)}>Copy</button>
          </div>
          <textarea readOnly value={justification} rows={12} />
        </div>
      )}
    </div>
  );
}
