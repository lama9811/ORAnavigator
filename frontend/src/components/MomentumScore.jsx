import { useState, useEffect } from "react";
import { FaSync } from "@react-icons/all-files/fa/FaSync";
import { FaArrowUp } from "@react-icons/all-files/fa/FaArrowUp";
import { FaArrowDown } from "@react-icons/all-files/fa/FaArrowDown";
import { FaMinus } from "@react-icons/all-files/fa/FaMinus";
import { getApiBase } from "../lib/apiBase";
import "./MomentumScore.css";

const API = getApiBase();

const FACTOR_ICONS = {
  timeliness: { label: "On-Time", emoji: "clock" },
  trajectory: { label: "Trajectory", emoji: "chart" },
  credit_pace: { label: "Credit Pace", emoji: "grad" },
  workload: { label: "Workload", emoji: "check" },
  missing_penalty: { label: "Missing", emoji: "warn" },
};

const scoreColor = (s) => {
  if (s == null) return "#9aa0a6";
  if (s >= 80) return "#34a853";
  if (s >= 60) return "#fbbc05";
  if (s >= 40) return "#ea8600";
  return "#ea4335";
};

export default function MomentumScore({ compact = false }) {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [expanded, setExpanded] = useState(false);

  useEffect(() => {
    const token = localStorage.getItem("token");
    if (!token) { setLoading(false); return; }
    fetch(`${API}/api/momentum-score`, { headers: { Authorization: `Bearer ${token}` } })
      .then(r => r.ok ? r.json() : null)
      .then(d => { if (d) setData(d); })
      .catch(() => {})
      .finally(() => setLoading(false));
  }, []);

  if (loading) return <div className="ms-loading"><FaSync className="ms-spin" size={14} /></div>;
  if (!data || data.score == null) return null;

  const score = data.score;
  const color = scoreColor(score);
  const circumference = 2 * Math.PI * 54;
  const offset = circumference - (score / 100) * circumference;

  // Compact mode: just the gauge, no breakdown
  if (compact) {
    return (
      <div className="ms-compact" title={`Momentum: ${score}`}>
        <svg viewBox="0 0 120 120" className="ms-gauge-svg">
          <circle className="ms-gauge-bg" cx="60" cy="60" r="54" />
          <circle className="ms-gauge-fill" cx="60" cy="60" r="54"
            style={{ stroke: color, strokeDasharray: circumference, strokeDashoffset: offset }} />
        </svg>
        <span className="ms-compact-num" style={{ color }}>{Math.round(score)}</span>
      </div>
    );
  }

  return (
    <div className="ms-card" onClick={() => setExpanded(!expanded)}>
      <div className="ms-main">
        {/* Gauge */}
        <div className="ms-gauge">
          <svg viewBox="0 0 120 120" className="ms-gauge-svg">
            <circle className="ms-gauge-bg" cx="60" cy="60" r="54" />
            <circle className="ms-gauge-fill" cx="60" cy="60" r="54"
              style={{ stroke: color, strokeDasharray: circumference, strokeDashoffset: offset }} />
          </svg>
          <div className="ms-gauge-inner">
            <span className="ms-gauge-num" style={{ color }}>{Math.round(score)}</span>
            <span className="ms-gauge-label">Momentum</span>
          </div>
        </div>

        {/* Right side info */}
        <div className="ms-info">
          <div className="ms-title">
            Academic Momentum
            {data.trend === "up" && <span className="ms-trend up"><FaArrowUp size={10} /> Up</span>}
            {data.trend === "down" && <span className="ms-trend down"><FaArrowDown size={10} /> Down</span>}
            {data.trend === "stable" && <span className="ms-trend stable"><FaMinus size={10} /> Stable</span>}
          </div>
          <div className="ms-subtitle">
            Based on {data.sources?.join(", ") || "available data"}
          </div>

          {/* Mini factor bars */}
          <div className="ms-mini-factors">
            {Object.entries(data.breakdown).map(([key, f]) => {
              if (f.score == null || key === "missing_penalty") return null;
              const pct = (f.score / f.max) * 100;
              return (
                <div key={key} className="ms-mini-bar" title={`${FACTOR_ICONS[key]?.label}: ${f.detail}`}>
                  <div className="ms-mini-fill" style={{ width: `${pct}%`, background: scoreColor(pct) }} />
                </div>
              );
            })}
          </div>
        </div>
      </div>

      {/* Expanded breakdown */}
      {expanded && (
        <div className="ms-breakdown">
          {Object.entries(data.breakdown).map(([key, f]) => {
            if (f.score == null) return null;
            const info = FACTOR_ICONS[key] || { label: key };
            const isPenalty = f.max === 0;
            const pct = isPenalty ? 0 : (f.score / f.max) * 100;
            return (
              <div key={key} className={`ms-factor ${isPenalty && f.score < 0 ? "penalty" : ""}`}>
                <div className="ms-factor-header">
                  <span className="ms-factor-name">{info.label}</span>
                  <span className="ms-factor-score">
                    {isPenalty ? f.score : `${f.score}/${f.max}`}
                  </span>
                </div>
                {!isPenalty && (
                  <div className="ms-factor-bar">
                    <div className="ms-factor-fill" style={{ width: `${pct}%`, background: scoreColor(pct) }} />
                  </div>
                )}
                <div className="ms-factor-detail">{f.detail}</div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
