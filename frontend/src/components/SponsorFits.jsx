// SponsorFits.jsx -- Sponsor Fit-Finder UI.
//
// Reads GET /api/me/sponsor-fits, renders the ranked funding-source
// matches as a card grid. Each card shows: rank, sponsor category,
// title, match score, the deterministic "matched signals" chips, the
// LLM-generated "Why this matches you" sentence, and a link out to the
// source URL.
//
// Empty state: a user with no profile signals (new account, never
// chatted, no proposals yet) still gets the HBCU-default ranking, but
// we surface a hint that filling in the Profile page will tighten the
// matches.

import React, { useState, useEffect, useMemo, useCallback } from "react";
import { useNavigate } from "react-router-dom";
import { FaSearch } from "@react-icons/all-files/fa/FaSearch";
import { FaSync } from "@react-icons/all-files/fa/FaSync";
import { FaExternalLinkAlt } from "@react-icons/all-files/fa/FaExternalLinkAlt";
import { FaTrophy } from "@react-icons/all-files/fa/FaTrophy";
import { FaInfoCircle } from "@react-icons/all-files/fa/FaInfoCircle";
import { getApiBase } from "../lib/apiBase";
import "./SponsorFits.css";

const API_BASE = getApiBase();

function authHeaders() {
  const token = localStorage.getItem("token");
  return token ? { Authorization: `Bearer ${token}` } : {};
}

// Map a KB doc subcategory onto a friendly group label for the filter
// chips. Keeps the user-facing chip set short (~6 buckets) rather than
// exposing the underlying ~15 subcategory codes verbatim.
function groupOf(sub) {
  if (!sub) return "Other";
  const s = sub.toLowerCase();
  if (s.includes("hbcu") || s.includes("msi")) return "HBCU/MSI";
  if (s.startsWith("federal")) return "Federal";
  if (s.includes("state_of_maryland") || s.includes("maryland")) return "Maryland";
  if (s.includes("private") || s.includes("foundation")) return "Foundations";
  if (s.includes("external") || s.includes("opportunity_db") || s.includes("database")) return "Databases";
  return "Other";
}

const ALL_GROUPS = ["All", "HBCU/MSI", "Federal", "Maryland", "Foundations", "Databases", "Other"];

export default function SponsorFits() {
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState("");
  const [data, setData] = useState(null);
  const [filter, setFilter] = useState("All");
  const [query, setQuery] = useState("");
  const navigate = useNavigate();

  const load = useCallback(async (opts = {}) => {
    if (opts.refresh) setRefreshing(true); else setLoading(true);
    setError("");
    try {
      const r = await fetch(`${API_BASE}/api/me/sponsor-fits?limit=15`, {
        headers: authHeaders(),
      });
      if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
      const body = await r.json();
      setData(body);
    } catch (e) {
      setError("Couldn't load funding matches: " + e.message);
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const filtered = useMemo(() => {
    if (!data) return [];
    const q = query.trim().toLowerCase();
    return data.matches.filter((m) => {
      if (filter !== "All" && groupOf(m.subcategory) !== filter) return false;
      if (q && !m.title.toLowerCase().includes(q)
            && !(m.explanation || "").toLowerCase().includes(q)) return false;
      return true;
    });
  }, [data, filter, query]);

  const profileSignalCount = useMemo(() => {
    if (!data?.profile) return 0;
    const p = data.profile;
    return (p.department ? 1 : 0)
      + (p.role ? 1 : 0)
      + (p.interests?.length || 0)
      + (p.sponsors_seen?.length || 0);
  }, [data]);

  if (loading) {
    return (
      <div className="sponsor-fits">
        <header className="sponsor-fits-header">
          <h1>Funding Matches</h1>
        </header>
        <div className="sponsor-fits-loading">Looking through funding sources…</div>
      </div>
    );
  }

  return (
    <div className="sponsor-fits">
      <header className="sponsor-fits-header">
        <div>
          <h1>Funding Matches</h1>
          <p className="sponsor-fits-subtitle">
            Funding sources ranked against your profile. Scoring is
            transparent — every card lists the signals that earned the
            match. {data?.total_sources_scanned ?? "—"} sources scanned.
          </p>
        </div>
        <button
          className="sponsor-fits-refresh"
          onClick={() => load({ refresh: true })}
          disabled={refreshing}
        >
          <FaSync className={refreshing ? "spin" : ""} size={12} />{" "}
          {refreshing ? "Refreshing…" : "Refresh"}
        </button>
      </header>

      {error && <div className="sponsor-fits-error">{error}</div>}

      {profileSignalCount < 2 && (
        <div className="sponsor-fits-hint">
          <FaInfoCircle size={14} />
          <div>
            <strong>Tip:</strong> these matches use the HBCU/MSI default.
            Add your department, role, and research interests on the{" "}
            <button className="sponsor-fits-link"
                    onClick={() => navigate("/profile")}>
              Profile page
            </button>{" "}
            to tighten the ranking.
          </div>
        </div>
      )}

      <div className="sponsor-fits-controls">
        <div className="sponsor-fits-search-wrap">
          <FaSearch size={12} className="sponsor-fits-search-icon" />
          <input
            type="text"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search matches…"
            className="sponsor-fits-search"
          />
        </div>
        <div className="sponsor-fits-chips">
          {ALL_GROUPS.map((g) => (
            <button
              key={g}
              className={"chip " + (filter === g ? "chip-active" : "")}
              onClick={() => setFilter(g)}
            >
              {g}
            </button>
          ))}
        </div>
      </div>

      {filtered.length === 0 ? (
        <div className="sponsor-fits-empty">
          No matches for the current filter. Try clearing the search or
          picking <strong>All</strong>.
        </div>
      ) : (
        <ul className="sponsor-fits-list">
          {filtered.map((m, idx) => (
            <MatchCard key={m.doc_id} match={m} rank={idx + 1} />
          ))}
        </ul>
      )}
    </div>
  );
}

function scoreBucket(score) {
  if (score >= 60) return { label: "Excellent fit", cls: "score-strong" };
  if (score >= 35) return { label: "Strong fit", cls: "score-good" };
  if (score >= 20) return { label: "Possible fit", cls: "score-ok" };
  return { label: "Baseline", cls: "score-low" };
}

function MatchCard({ match, rank }) {
  const bucket = scoreBucket(match.score);
  const group = groupOf(match.subcategory);
  return (
    <li className="sponsor-fit-card">
      <div className="sponsor-fit-rank">
        {rank <= 3
          ? <FaTrophy size={14} className={`trophy trophy-${rank}`} />
          : <span className="rank-num">#{rank}</span>}
      </div>
      <div className="sponsor-fit-body">
        <div className="sponsor-fit-title-row">
          <h3 className="sponsor-fit-title">{match.title}</h3>
          <span className={`sponsor-fit-score ${bucket.cls}`}>
            {bucket.label} · {match.score}
          </span>
        </div>
        <div className="sponsor-fit-meta">
          <span className="sponsor-fit-group">{group}</span>
        </div>
        {match.explanation && (
          <p className="sponsor-fit-why">{match.explanation}</p>
        )}
        {match.matched_signals?.length > 0 && (
          <ul className="sponsor-fit-signals">
            {match.matched_signals.map((s, i) => (
              <li key={i} className="signal-chip">{s}</li>
            ))}
          </ul>
        )}
        {match.source_url && (
          <a
            className="sponsor-fit-link"
            href={match.source_url}
            target="_blank"
            rel="noopener noreferrer"
          >
            Open on morgan.edu <FaExternalLinkAlt size={10} />
          </a>
        )}
      </div>
    </li>
  );
}
