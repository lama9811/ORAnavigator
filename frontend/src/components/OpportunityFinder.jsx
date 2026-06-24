// OpportunityFinder.jsx -- the discovery surface for a new PI. Describe your
// work in plain text -> ranked, LIVE, open federal opportunities (Grants.gov),
// each with a grounded fit explanation, a deterministic institution-eligibility
// verdict, a PI-level eligibility advisory, and a mechanism note. "Start a
// proposal from this" hands the solicitation straight into the proposal pipeline.
//
// Hits POST /api/opportunities/search. The deterministic core lives in the
// backend (services/opportunity_finder.py); this is presentation only.

import React, { useState, useEffect } from "react";
import { useNavigate } from "react-router-dom";
import {
  Compass, Search, CalendarClock, CircleCheck, CircleAlert,
  CircleHelp, CircleX, ArrowRight, ExternalLink, Building2,
} from "lucide-react";
import { getApiBase } from "../lib/apiBase";
import "./OpportunityFinder.css";

const API_BASE = getApiBase();

// The app authenticates with a Bearer token in localStorage (same as MyProposals).
function authHeaders() {
  const token = localStorage.getItem("token");
  return token ? { Authorization: `Bearer ${token}` } : {};
}

// Deterministic institution-eligibility verdict -> badge styling + copy.
const ELIGIBILITY = {
  eligible:     { cls: "ok",   Icon: CircleCheck, label: "Your institution is eligible" },
  unrestricted: { cls: "ok",   Icon: CircleCheck, label: "Open to all applicants" },
  see_text:     { cls: "warn", Icon: CircleHelp,  label: "Eligibility — verify the details" },
  ineligible:   { cls: "no",   Icon: CircleX,     label: "Your institution may not be eligible" },
};

export default function OpportunityFinder() {
  const navigate = useNavigate();
  const [description, setDescription] = useState("");
  const [results, setResults] = useState(null); // null = not searched yet
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  // Pre-fill a hint from the saved profile interests so the box is never blank.
  useEffect(() => {
    fetch(`${API_BASE}/api/profile`, { headers: authHeaders() })
      .then((r) => (r.ok ? r.json() : null))
      .then((p) => {
        if (p?.interests && !description) {
          setDescription(`My research focuses on ${p.interests}. `);
        }
      })
      .catch(() => {});
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function runSearch(e) {
    e?.preventDefault();
    const desc = description.trim();
    if (desc.length < 15) {
      setError("Tell me a bit more about your work (at least a sentence) for good matches.");
      return;
    }
    setLoading(true);
    setError("");
    try {
      const r = await fetch(`${API_BASE}/api/opportunities/search`, {
        method: "POST",
        headers: { "Content-Type": "application/json", ...authHeaders() },
        body: JSON.stringify({ description: desc }),
      });
      if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
      const data = await r.json();
      setResults(data.opportunities || []);
    } catch (err) {
      setError(
        "Couldn't reach the federal opportunity database right now. Please try again in a moment."
      );
      setResults(null);
    } finally {
      setLoading(false);
    }
  }

  function startProposal(o) {
    // Hand the solicitation to the existing ingestion flow on My Proposals.
    navigate("/my-proposals", { state: { solicitationUrl: o.solicitation_url } });
  }

  return (
    <div className="oppf">
      <header className="oppf-header">
        <h1>
          <Compass className="oppf-header-icon" size={26} />
          Find Funding
        </h1>
        <p className="oppf-subtitle">
          Describe your research in your own words. We search <strong>live, open
          federal opportunities</strong> and show which fit, whether you’re
          eligible, and what to do next — so you don’t spend months on the wrong
          program.
        </p>
      </header>

      <form className="oppf-searchbox" onSubmit={runSearch}>
        <textarea
          className="oppf-textarea"
          value={description}
          onChange={(e) => setDescription(e.target.value)}
          placeholder="e.g. I study machine-learning methods to detect cybersecurity threats in campus networks, and I want to train undergraduates in the work."
          rows={4}
        />
        <div className="oppf-searchrow">
          <span className="oppf-hint">
            Matched against your description and your saved research interests.
          </span>
          <button type="submit" className="oppf-search-btn" disabled={loading}>
            <Search size={16} /> {loading ? "Searching…" : "Find opportunities"}
          </button>
        </div>
      </form>

      {error && <div className="oppf-error" role="alert">{error}</div>}

      {loading && (
        <div className="oppf-status" role="status">
          Searching federal opportunities and checking eligibility…
        </div>
      )}

      {!loading && results !== null && results.length === 0 && !error && (
        <div className="oppf-empty">
          No open federal opportunities matched that description. Try broadening
          it or using different keywords.
        </div>
      )}

      {!loading && results && results.length > 0 && (
        <>
          <div className="oppf-result-meta" role="status">
            {results.length} open opportunit{results.length === 1 ? "y" : "ies"},
            best fit first
          </div>
          <ul className="oppf-grid">
            {results.map((o) => (
              <OpportunityCard key={o.id} o={o} onStart={() => startProposal(o)} />
            ))}
          </ul>
        </>
      )}
    </div>
  );
}

function OpportunityCard({ o, onStart }) {
  const elig = ELIGIBILITY[o.institution_eligibility] || ELIGIBILITY.see_text;
  const EligIcon = elig.Icon;
  return (
    <li className="oppf-card">
      <div className="oppf-card-top">
        <span className="oppf-card-title">{o.title}</span>
        <span className="oppf-card-agency"><Building2 size={12} /> {o.agency}</span>
      </div>

      <div className={`oppf-elig oppf-elig-${elig.cls}`}>
        <EligIcon size={14} /> {elig.label}
      </div>

      {o.fit_explanation && (
        <p className="oppf-fit">{o.fit_explanation}</p>
      )}
      {o.fit_quote && (
        <blockquote className="oppf-quote">“{o.fit_quote}”</blockquote>
      )}

      <div className="oppf-meta-row">
        {o.close_date && (
          <span className="oppf-meta">
            <CalendarClock size={13} /> Sponsor close: <b>{o.close_date}</b>
          </span>
        )}
        {o.internal_deadline && (
          <span className="oppf-meta oppf-meta-internal" title="Submit to ORA by this date — 5 business days before the sponsor deadline.">
            <CircleAlert size={13} /> ORA routing by: <b>{o.internal_deadline}</b>
          </span>
        )}
        {o.award_ceiling && (
          <span className="oppf-meta">Award up to <b>{o.award_ceiling}</b></span>
        )}
      </div>

      {o.mechanism_note && <div className="oppf-mechanism">{o.mechanism_note}</div>}

      {o.pi_eligibility_note && (
        <details className="oppf-pi-elig">
          <summary>Eligibility fine print — verify before you apply</summary>
          <p>{o.pi_eligibility_note}</p>
        </details>
      )}

      <div className="oppf-card-actions">
        <button className="oppf-start-btn" onClick={onStart} disabled={!o.solicitation_url}>
          Start a proposal from this <ArrowRight size={14} />
        </button>
        {o.solicitation_url && (
          <a
            className="oppf-open-link"
            href={o.solicitation_url}
            target="_blank"
            rel="noopener noreferrer"
          >
            View solicitation <ExternalLink size={13} />
          </a>
        )}
      </div>
    </li>
  );
}
