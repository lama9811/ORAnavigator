// SampleProposalsLibrary.jsx -- a browseable shelf of real, public example
// proposals a first-time PI can read for reference. Hits GET /api/sample-
// proposals (public, no auth) and filters client-side by category chip. Every
// card links OUT to the source; we never host third-party proposals.

import React, { useState, useEffect, useMemo } from "react";
import { BookOpen, ExternalLink, Lock } from "lucide-react";
import { getApiBase } from "../lib/apiBase";
import "./SampleProposalsLibrary.css";

const API_BASE = getApiBase();

export default function SampleProposalsLibrary() {
  const [proposals, setProposals] = useState([]);
  const [categories, setCategories] = useState([]);
  const [active, setActive] = useState(""); // "" = All
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  // One fetch on mount; the category chips filter the loaded list client-side
  // (the whole catalog is ~a dozen entries, so there's no need to refetch).
  useEffect(() => {
    setLoading(true);
    setError("");
    fetch(`${API_BASE}/api/sample-proposals`)
      .then(async (r) => {
        if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
        return r.json();
      })
      .then((data) => {
        setProposals(data.proposals || []);
        setCategories(data.categories || []);
      })
      .catch((e) => setError("Couldn't load the sample proposals: " + e.message))
      .finally(() => setLoading(false));
  }, []);

  const visible = useMemo(() => {
    if (!active) return proposals;
    return proposals.filter((p) => (p.categories || []).includes(active));
  }, [proposals, active]);

  return (
    <div className="samples-library">
      <header className="samples-header">
        <h1>
          <BookOpen className="samples-header-icon" size={26} />
          Sample Proposals
        </h1>
        <p className="samples-subtitle">
          Real, funded proposals you can read end-to-end — see how a strong
          Project Description, budget, and broader-impacts section actually read
          before you write your own.
        </p>
        <p className="samples-disclaimer">
          These are external resources maintained by third parties. Links open in
          a new tab.
        </p>
      </header>

      {!loading && !error && categories.length > 0 && (
        <div className="samples-filters" role="tablist" aria-label="Filter by category">
          <button
            type="button"
            role="tab"
            aria-selected={active === ""}
            className={"samples-chip" + (active === "" ? " active" : "")}
            onClick={() => setActive("")}
          >
            All
          </button>
          {categories.map((c) => (
            <button
              key={c}
              type="button"
              role="tab"
              aria-selected={active === c}
              className={"samples-chip" + (active === c ? " active" : "")}
              onClick={() => setActive(c)}
            >
              {c}
            </button>
          ))}
        </div>
      )}

      <div className="samples-result-meta" role="status">
        {loading
          ? "Loading…"
          : error
            ? error
            : `${visible.length} example${visible.length === 1 ? "" : "s"}`}
      </div>

      <ul className="samples-grid">
        {!loading && !error && visible.length === 0 && (
          <li className="samples-empty">
            No examples in this category yet. Try the “All” filter.
          </li>
        )}
        {visible.map((p) => (
          <li key={p.id} className="sample-card">
            <a
              className="sample-card-link"
              href={p.url}
              target="_blank"
              rel="noopener noreferrer"
            >
              <div className="sample-card-body">
                <div className="sample-card-titlerow">
                  <span className="sample-card-title">{p.title}</span>
                  {p.access === "partial" && (
                    <span
                      className="sample-card-badge"
                      title="Some content needs a free account or is partly paywalled"
                    >
                      <Lock size={11} /> Partly paywalled
                    </span>
                  )}
                </div>
                <div className="sample-card-source">{p.source}</div>
                {p.kind && <div className="sample-card-kind">{p.kind}</div>}
                {p.why && <div className="sample-card-why">{p.why}</div>}
                <div className="sample-card-tags">
                  {(p.categories || []).map((c) => (
                    <span key={c} className="sample-tag">{c}</span>
                  ))}
                </div>
              </div>
              <span className="sample-card-open">
                Open <ExternalLink size={14} />
              </span>
            </a>
          </li>
        ))}
      </ul>
    </div>
  );
}
