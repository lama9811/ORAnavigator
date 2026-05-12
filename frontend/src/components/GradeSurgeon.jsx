import { useState, useEffect } from "react";
import { useNavigate } from "react-router-dom";
import { FaChartLine } from "@react-icons/all-files/fa/FaChartLine";
import { FaSync } from "@react-icons/all-files/fa/FaSync";
import { FaCheckCircle } from "@react-icons/all-files/fa/FaCheckCircle";
import { FaExclamationTriangle } from "@react-icons/all-files/fa/FaExclamationTriangle";
import { FaClock } from "@react-icons/all-files/fa/FaClock";
import { FaBullseye } from "@react-icons/all-files/fa/FaBullseye";
import { FaLightbulb } from "@react-icons/all-files/fa/FaLightbulb";
import { getApiBase } from "../lib/apiBase";
import "./GradeSurgeon.css";

const API = getApiBase();

const scoreColor = (s) => {
  if (s == null) return "var(--text-tertiary)";
  if (s >= 90) return "var(--google-green)";
  if (s >= 80) return "var(--google-blue)";
  if (s >= 70) return "var(--google-yellow)";
  return "var(--google-red)";
};

const statusBadge = (status) => {
  const map = {
    graded: { label: "Graded", cls: "gs-badge-green" },
    pending_review: { label: "Pending", cls: "gs-badge-yellow" },
    submitted: { label: "Submitted", cls: "gs-badge-blue" },
    missing: { label: "Missing", cls: "gs-badge-red" },
    upcoming: { label: "Upcoming", cls: "gs-badge-gray" },
  };
  return map[status] || map.upcoming;
};

export default function GradeSurgeon() {
  const navigate = useNavigate();
  const [courses, setCourses] = useState([]);
  const [selected, setSelected] = useState(null);
  const [detail, setDetail] = useState(null);
  const [loading, setLoading] = useState(true);
  const [loadingDetail, setLoadingDetail] = useState(false);
  const [expandedGroups, setExpandedGroups] = useState({});
  const [targetGrade, setTargetGrade] = useState("for_B");

  const token = localStorage.getItem("token");

  useEffect(() => {
    if (!token) { setLoading(false); return; }
    fetch(`${API}/api/grade-analysis`, { headers: { Authorization: `Bearer ${token}` } })
      .then(r => r.ok ? r.json() : null)
      .then(d => {
        if (d?.courses) {
          setCourses(d.courses);
          if (d.courses.length > 0) setSelected(d.courses[0].course_id);
        }
      })
      .catch(() => {})
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    if (!selected || !token) return;
    setLoadingDetail(true);
    fetch(`${API}/api/grade-analysis/${selected}`, { headers: { Authorization: `Bearer ${token}` } })
      .then(r => r.ok ? r.json() : null)
      .then(d => { if (d) setDetail(d); })
      .catch(() => {})
      .finally(() => setLoadingDetail(false));
  }, [selected]);

  const toggleGroup = (id) => setExpandedGroups(prev => ({ ...prev, [id]: !prev[id] }));

  if (loading) return (
    <div className="gs"><div className="gs-center"><FaSync className="gs-spin" size={20} /><p>Loading grade data...</p></div></div>
  );

  if (!courses.length) return (
    <div className="gs">
      <div className="gs-center">
        <FaChartLine size={28} color="var(--msu-blue)" />
        <h2>No Gradebook Data</h2>
        <p className="gs-muted">Sync Canvas to see your grade analysis.</p>
        <button className="gs-primary-btn" onClick={() => navigate("/profile")}><FaSync size={13} /> Sync Canvas</button>
      </div>
    </div>
  );

  const win = detail?.what_i_need?.[targetGrade];

  return (
    <div className="gs">
      <header className="gs-head">
        <div>
          <h1>Grade Surgeon</h1>
          <span className="gs-muted">Detailed grade analysis and strategy</span>
        </div>
      </header>

      {/* Course Tabs */}
      <nav className="gs-tabs">
        {courses.map(c => (
          <button
            key={c.course_id}
            className={`gs-tab ${selected === c.course_id ? "on" : ""}`}
            onClick={() => setSelected(c.course_id)}
          >
            <span className="gs-tab-name">{c.course_name?.split("(")[0]?.split("_")[0]?.trim()}</span>
            <span className="gs-tab-grade" style={{ color: scoreColor(c.current_grade?.score) }}>
              {c.current_grade?.score != null ? `${c.current_grade.score}%` : "--"}
            </span>
          </button>
        ))}
      </nav>

      {loadingDetail ? (
        <div className="gs-center" style={{ padding: "60px 0" }}><FaSync className="gs-spin" size={18} /></div>
      ) : detail ? (
        <div className="gs-body">
          {/* Current Grade Hero */}
          <div className="gs-hero">
            <div className="gs-hero-grade" style={{ color: scoreColor(detail.current_grade?.score) }}>
              {detail.current_grade?.score != null ? `${detail.current_grade.score}%` : "--"}
            </div>
            <div className="gs-hero-letter" style={{ color: scoreColor(detail.current_grade?.score) }}>
              {detail.current_grade?.letter || ""}
            </div>
            <div className="gs-hero-meta">
              <span>{detail.stats?.graded}/{detail.stats?.total_assignments} graded</span>
              {detail.stats?.missing > 0 && <span className="gs-hero-missing">{detail.stats.missing} missing</span>}
              <span className="gs-hero-type">{detail.grading_type === "weighted" ? "Weighted" : "Total Points"}</span>
            </div>
          </div>

          {/* What Do I Need */}
          <section className="gs-section">
            <h2><FaBullseye size={16} /> What Do I Need?</h2>
            <div className="gs-target-picker">
              {Object.entries({ for_A: "A (90%)", for_B: "B (80%)", for_C: "C (70%)" }).map(([key, label]) => (
                <button
                  key={key}
                  className={`gs-target-btn ${targetGrade === key ? "on" : ""}`}
                  onClick={() => setTargetGrade(key)}
                >
                  {label}
                </button>
              ))}
            </div>
            {win && (
              <div className={`gs-target-result ${win.achievable ? "achievable" : "not-achievable"}`}>
                {win.required_avg != null ? (
                  <>
                    <div className="gs-target-num">{win.required_avg}%</div>
                    <div className="gs-target-label">
                      average needed on remaining work
                      {win.achievable
                        ? <span className="gs-achievable"><FaCheckCircle size={12} /> Achievable</span>
                        : <span className="gs-not-achievable"><FaExclamationTriangle size={12} /> Not achievable</span>
                      }
                    </div>
                  </>
                ) : (
                  <div className="gs-target-label">
                    {win.achievable
                      ? <span className="gs-achievable"><FaCheckCircle size={14} /> Already secured</span>
                      : <span className="gs-not-achievable"><FaExclamationTriangle size={14} /> No remaining work</span>
                    }
                  </div>
                )}
              </div>
            )}
          </section>

          {/* Performance DNA */}
          {Object.keys(detail.performance_dna || {}).length > 0 && (
            <section className="gs-section">
              <h2><FaChartLine size={16} /> Performance DNA</h2>
              <div className="gs-dna">
                {Object.entries(detail.performance_dna).sort((a, b) => b[1] - a[1]).map(([name, pct]) => (
                  <div key={name} className="gs-dna-row">
                    <span className="gs-dna-label">{name}</span>
                    <div className="gs-dna-bar-wrap">
                      <div className="gs-dna-bar" style={{ width: `${Math.min(pct, 100)}%`, background: scoreColor(pct) }} />
                    </div>
                    <span className="gs-dna-pct" style={{ color: scoreColor(pct) }}>{pct}%</span>
                  </div>
                ))}
              </div>
            </section>
          )}

          {/* Strategies */}
          {detail.strategies?.length > 0 && (
            <section className="gs-section">
              <h2><FaLightbulb size={16} /> Strategy</h2>
              <div className="gs-strategies">
                {detail.strategies.map((s, i) => (
                  <div key={i} className="gs-strategy-card">
                    <div className="gs-strategy-action">{s.action}</div>
                    <div className="gs-strategy-reason">{s.reason}</div>
                    <div className="gs-strategy-impact">{s.impact}</div>
                  </div>
                ))}
              </div>
            </section>
          )}

          {/* Assignment Groups */}
          <section className="gs-section">
            <h2>Assignment Breakdown</h2>
            {detail.assignment_groups?.filter(g => g.total_count > 0 || g.weight > 0).map(g => (
              <div key={g.id} className="gs-group">
                <button className="gs-group-header" onClick={() => toggleGroup(g.id)}>
                  <div className="gs-group-left">
                    <span className="gs-group-name">{g.name}</span>
                    {g.weight > 0 && <span className="gs-group-weight">{g.weight}%</span>}
                  </div>
                  <div className="gs-group-right">
                    {g.current_avg != null && (
                      <span className="gs-group-avg" style={{ color: scoreColor(g.current_avg) }}>{g.current_avg}%</span>
                    )}
                    <span className="gs-group-count">{g.graded_count}/{g.total_count}</span>
                    <span className="gs-chevron">{expandedGroups[g.id] ? "\u25B2" : "\u25BC"}</span>
                  </div>
                </button>
                {expandedGroups[g.id] && (
                  <div className="gs-assignments">
                    {g.assignments.map((a, i) => {
                      const badge = statusBadge(a.status);
                      return (
                        <div key={i} className="gs-asn-row">
                          <span className={`gs-asn-badge ${badge.cls}`}>{badge.label}</span>
                          <span className="gs-asn-name">{a.name}</span>
                          <span className="gs-asn-score">
                            {a.score != null ? `${a.score}/${a.points_possible}` : `--/${a.points_possible || 0}`}
                          </span>
                          {a.extra_credit && <span className="gs-ec-tag">Extra Credit</span>}
                          {a.late && <span className="gs-late-tag">Late</span>}
                        </div>
                      );
                    })}
                  </div>
                )}
              </div>
            ))}
          </section>
        </div>
      ) : null}
    </div>
  );
}
