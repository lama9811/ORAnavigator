import { useState, useEffect } from "react";
import { useNavigate } from "react-router-dom";
import { FaBook } from "@react-icons/all-files/fa/FaBook";
import { FaExclamationTriangle } from "@react-icons/all-files/fa/FaExclamationTriangle";
import { FaCheckCircle } from "@react-icons/all-files/fa/FaCheckCircle";
import { FaClock } from "@react-icons/all-files/fa/FaClock";
import { FaSync } from "@react-icons/all-files/fa/FaSync";
import { FaCalendarAlt } from "@react-icons/all-files/fa/FaCalendarAlt";
import { FaChartLine } from "@react-icons/all-files/fa/FaChartLine";
import { getApiBase } from "../lib/apiBase";
import MomentumScore from "./MomentumScore";
import "./MyClassesPage.css";

const API_BASE = getApiBase();

const getScoreColor = (s) => {
  if (s == null) return "var(--text-tertiary)";
  if (s >= 90) return "var(--google-green)";
  if (s >= 80) return "var(--google-blue)";
  if (s >= 70) return "var(--google-yellow)";
  return "var(--google-red)";
};

const getScoreLetter = (s) => {
  if (s == null) return "";
  if (s >= 93) return "A";
  if (s >= 90) return "A-";
  if (s >= 87) return "B+";
  if (s >= 83) return "B";
  if (s >= 80) return "B-";
  if (s >= 77) return "C+";
  if (s >= 73) return "C";
  if (s >= 70) return "C-";
  if (s >= 67) return "D+";
  if (s >= 60) return "D";
  return "F";
};

const cleanName = (name) => {
  if (!name) return "Course";
  return name
    .replace(/[._](?:Spring|Fall|Summer)\s*\d{4}/gi, "")
    .replace(/\.\d{3}_/g, " ")
    .replace(/\(\d+[ap]m[^)]*\)/gi, "")
    .replace(/_/g, " ")
    .trim();
};

const cleanCode = (code) => {
  if (!code) return "";
  return code.split("_")[0].split(".")[0].toUpperCase();
};

const formatDue = (d) => {
  if (!d) return { label: "No date", cls: "", full: "" };
  const due = new Date(d);
  const now = new Date();
  const h = (due - now) / 3.6e6;
  const full = due.toLocaleDateString("en-US", { weekday: "short", month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
  if (h < 0) return { label: "Overdue", cls: "overdue", full };
  if (h < 12) return { label: `${Math.floor(h)}h left`, cls: "critical", full };
  if (h < 24) return { label: "Today", cls: "urgent", full };
  if (h < 48) return { label: "Tomorrow", cls: "soon", full };
  if (h < 168) return { label: `${Math.ceil(h / 24)}d`, cls: "", full };
  return { label: due.toLocaleDateString("en-US", { month: "short", day: "numeric" }), cls: "", full };
};

export default function MyClassesPage() {
  const navigate = useNavigate();
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [tab, setTab] = useState("overview");

  useEffect(() => {
    const token = localStorage.getItem("token");
    if (!token) { setLoading(false); return; }
    fetch(`${API_BASE}/api/canvas`, { headers: { Authorization: `Bearer ${token}` } })
      .then(r => r.ok ? r.json() : null)
      .then(d => { if (d?.connected) setData(d); })
      .catch(() => {})
      .finally(() => setLoading(false));
  }, []);

  if (loading) return (
    <div className="mc"><div className="mc-center"><FaSync className="mc-spin" size={20} /><p>Loading Canvas...</p></div></div>
  );

  if (!data) return (
    <div className="mc">
      <div className="mc-center">
        <div className="mc-empty-art">
          <svg width="72" height="72" viewBox="0 0 72 72" fill="none"><rect width="72" height="72" rx="18" fill="var(--msu-blue-50)"/><path d="M22 24h28a2 2 0 012 2v20a2 2 0 01-2 2H22a2 2 0 01-2-2V26a2 2 0 012-2z" stroke="var(--msu-blue)" strokeWidth="2"/><path d="M20 30h32M30 24v24" stroke="var(--msu-blue)" strokeWidth="1.5" opacity="0.4"/><circle cx="42" cy="40" r="6" fill="var(--msu-blue)" opacity="0.2"/><path d="M40 40l2 2 4-4" stroke="var(--msu-blue)" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/></svg>
        </div>
        <h2>Connect Canvas</h2>
        <p className="mc-muted">Sync your Canvas to see courses, grades, and upcoming assignments.</p>
        <button className="mc-primary-btn" onClick={() => navigate("/profile")}><FaSync size={13} /> Connect in Profile</button>
      </div>
    </div>
  );

  const courses = data.courses || [];
  const assignments = (data.upcoming_assignments || []).filter(a => a.type !== "calendar_event");
  const missing = data.missing_assignments || [];
  const graded = courses.filter(c => c.current_score != null);
  const avg = graded.length ? (graded.reduce((s, c) => s + c.current_score, 0) / graded.length) : null;
  const dueSoon = assignments.filter(a => a.due_at && (new Date(a.due_at) - new Date()) / 3.6e6 < 72 && (new Date(a.due_at) - new Date()) > 0);

  return (
    <div className="mc">
      <header className="mc-head">
        <div>
          <h1>My Classes</h1>
          <span className="mc-muted">{data.updated_at ? `Synced ${new Date(data.updated_at).toLocaleDateString()}` : ""}</span>
        </div>
        <button className="mc-ghost-btn" onClick={() => navigate("/profile")}><FaSync size={11} /> Re-sync</button>
      </header>

      <div className="mc-stats">
        <div className="mc-stat">
          <div className="mc-stat-icon blue"><FaBook size={16} /></div>
          <div><div className="mc-stat-val">{courses.length}</div><div className="mc-stat-lbl">Courses</div></div>
        </div>
        {avg != null && (
          <div className="mc-stat">
            <div className="mc-stat-icon green"><FaChartLine size={16} /></div>
            <div><div className="mc-stat-val" style={{ color: getScoreColor(avg) }}>{avg.toFixed(1)}%</div><div className="mc-stat-lbl">Average</div></div>
          </div>
        )}
        <div className="mc-stat">
          <div className="mc-stat-icon gold"><FaCalendarAlt size={16} /></div>
          <div><div className="mc-stat-val">{assignments.length}</div><div className="mc-stat-lbl">Upcoming</div></div>
        </div>
        {dueSoon.length > 0 && (
          <div className="mc-stat warn">
            <div className="mc-stat-icon amber"><FaClock size={16} /></div>
            <div><div className="mc-stat-val">{dueSoon.length}</div><div className="mc-stat-lbl">Due Soon</div></div>
          </div>
        )}
        {missing.length > 0 && (
          <div className="mc-stat danger">
            <div className="mc-stat-icon red"><FaExclamationTriangle size={16} /></div>
            <div><div className="mc-stat-val">{missing.length}</div><div className="mc-stat-lbl">Missing</div></div>
          </div>
        )}
      </div>

      <MomentumScore />

      <nav className="mc-nav">
        {["overview", "assignments", "grades"].map(t => (
          <button key={t} className={`mc-nav-btn ${tab === t ? "on" : ""}`} onClick={() => setTab(t)}>
            {t === "overview" && <FaBook size={12} />}
            {t === "assignments" && <FaCalendarAlt size={12} />}
            {t === "grades" && <FaChartLine size={12} />}
            {t.charAt(0).toUpperCase() + t.slice(1)}
          </button>
        ))}
      </nav>

      {tab === "overview" && (
        <section className="mc-section">
          {missing.length > 0 && (
            <div className="mc-banner red">
              <FaExclamationTriangle size={15} />
              <div>
                <strong>{missing.length} Missing</strong>
                {missing.map((m, i) => <span key={i} className="mc-banner-item">{m.title} ({m.points}pts)</span>)}
              </div>
            </div>
          )}
          {dueSoon.length > 0 && (
            <div className="mc-banner amber">
              <FaClock size={15} />
              <div>
                <strong>{dueSoon.length} Due Soon</strong>
                {dueSoon.map((a, i) => <span key={i} className="mc-banner-item">{a.title} - {formatDue(a.due_at).label}</span>)}
              </div>
            </div>
          )}

          <div className="mc-grid">
            {courses.map((c) => (
              <div key={c.id} className="mc-card">
                <div className="mc-card-top">
                  <span className="mc-badge">{cleanCode(c.code)}</span>
                  {c.current_score != null && (
                    <span className="mc-score" style={{ color: getScoreColor(c.current_score) }}>
                      {c.current_score.toFixed(1)}%
                    </span>
                  )}
                </div>
                <div className="mc-card-name">{cleanName(c.name)}</div>
                {c.current_score != null && (
                  <div className="mc-bar-wrap">
                    <div className="mc-bar">
                      <div className="mc-bar-fill" style={{ width: `${Math.min(c.current_score, 100)}%`, background: getScoreColor(c.current_score) }} />
                    </div>
                    <span className="mc-letter" style={{ color: getScoreColor(c.current_score) }}>{c.current_grade || getScoreLetter(c.current_score)}</span>
                  </div>
                )}
              </div>
            ))}
          </div>
        </section>
      )}

      {tab === "assignments" && (
        <section className="mc-section">
          {assignments.length === 0 ? (
            <div className="mc-center" style={{ padding: "48px 0" }}>
              <FaCheckCircle size={28} color="var(--google-green)" />
              <p className="mc-muted">You're all caught up!</p>
            </div>
          ) : (
            <div className="mc-list">
              {assignments.map((a, i) => {
                const due = formatDue(a.due_at);
                return (
                  <div key={i} className={`mc-row ${due.cls}`}>
                    <div className={`mc-type ${a.type === "quiz" ? "purple" : a.type === "discussion_topic" ? "teal" : "blue"}`}>
                      {a.type === "quiz" ? "Q" : a.type === "discussion_topic" ? "D" : "A"}
                    </div>
                    <div className="mc-row-body">
                      <div className="mc-row-title">{a.title}</div>
                      <div className="mc-row-sub">{a.course_name}</div>
                    </div>
                    <div className="mc-row-right">
                      <span className={`mc-due ${due.cls}`} title={due.full}>{due.label}</span>
                      {a.points && <span className="mc-pts">{a.points} pts</span>}
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </section>
      )}

      {tab === "grades" && (
        <section className="mc-section">
          <div className="mc-list">
            {courses.map((c) => (
              <div key={c.id} className="mc-row">
                <div className="mc-row-body" style={{ flex: "0 0 220px" }}>
                  <div className="mc-row-code">{cleanCode(c.code)}</div>
                  <div className="mc-row-sub">{cleanName(c.name)}</div>
                </div>
                <div className="mc-grade-area">
                  {c.current_score != null ? (
                    <>
                      <div className="mc-grade-bar">
                        <div className="mc-grade-fill" style={{ width: `${Math.min(c.current_score, 100)}%`, background: getScoreColor(c.current_score) }} />
                      </div>
                      <span className="mc-grade-num" style={{ color: getScoreColor(c.current_score) }}>
                        {c.current_score.toFixed(1)}%
                      </span>
                      <span className="mc-grade-letter" style={{ background: getScoreColor(c.current_score) }}>
                        {c.current_grade || getScoreLetter(c.current_score)}
                      </span>
                    </>
                  ) : (
                    <span className="mc-muted" style={{ fontSize: "0.85rem" }}>No grade</span>
                  )}
                </div>
              </div>
            ))}
          </div>
        </section>
      )}
    </div>
  );
}
