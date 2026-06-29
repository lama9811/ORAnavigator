// SampleProposalsLibrary.jsx -- a browseable shelf of real, public example
// proposals a first-time PI can read for reference. Hits GET /api/sample-
// proposals (public, no auth) and filters client-side by category chip. Every
// card links OUT to the source; we never host third-party proposals.

import React, { useState, useEffect, useMemo } from "react";
import { BookOpen, Download, ExternalLink, Lock, Search } from "lucide-react";
import { getApiBase } from "../lib/apiBase";
import "./SampleProposalsLibrary.css";

const API_BASE = getApiBase();
const authHeaders = () => ({
  "Content-Type": "application/json",
  Authorization: `Bearer ${localStorage.getItem("token")}`,
});

export default function SampleProposalsLibrary() {
  const [proposals, setProposals] = useState([]);
  const [categories, setCategories] = useState([]);
  const [active, setActive] = useState(""); // "" = All
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [query, setQuery] = useState("");
  const [searching, setSearching] = useState(false);
  const [matched, setMatched] = useState(false);  // list is ranked to an interest

  // Browse load: the full shelf, authored-first. The category chips filter this
  // client-side; the interest box (below) re-ranks it server-side.
  const loadAll = () => {
    setLoading(true);
    setError("");
    return fetch(`${API_BASE}/api/sample-proposals`)
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
  };

  useEffect(() => { loadAll(); }, []);

  // Rank the shelf to the PI's typed interest (+ their saved interests, folded
  // in server-side). Deterministic keyword match; best matches first.
  const runSearch = async () => {
    if (!query.trim()) return;
    setSearching(true);
    setError("");
    try {
      const r = await fetch(`${API_BASE}/api/sample-proposals/search`, {
        method: "POST", headers: authHeaders(),
        body: JSON.stringify({ query }),
      });
      if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
      const data = await r.json();
      setProposals(data.proposals || []);
      setMatched(true);
    } catch (e) {
      setError("Couldn't rank the samples: " + e.message);
    } finally {
      setSearching(false);
    }
  };

  const clearSearch = () => {
    setQuery("");
    setMatched(false);
    loadAll();   // back to authored-first browse order
  };

  const visible = useMemo(() => {
    if (!active) return proposals;
    return proposals.filter((p) => (p.categories || []).includes(active));
  }, [proposals, active]);

  // True when a search ran but nothing actually keyword-matched (so the order is
  // just the default shelf, not a real ranking — say so rather than imply a fit).
  const noMatches = matched && !proposals.some((p) => p.match);

  return (
    <div className="samples-library">
      <header className="samples-header">
        <h1>
          <BookOpen className="samples-header-icon" size={26} />
          Sample Proposals
        </h1>
        <p className="samples-subtitle">
          See how a strong proposal actually reads before you write your own.
          <strong> Download</strong> our annotated sample proposals to read
          offline, or <strong>browse</strong> authoritative external libraries of
          real funded proposals.
        </p>
        <p className="samples-disclaimer">
          The downloadable PDFs are original samples written by ORA Navigator for
          reference. External links open in a new tab and are maintained by third
          parties; entries marked <strong>Community</strong> are researcher-shared
          via Open Grants (ogrants.org).
        </p>
      </header>

      <div className="samples-search">
        <textarea
          className="samples-search-input"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) runSearch(); }}
          rows={3}
          placeholder="Describe your research and we'll surface the closest samples — e.g. “My work focuses on AI and robotics.”"
        />
        <div className="samples-search-row">
          <span className="samples-search-hint">
            Matched against your description and your saved research interests.
          </span>
          <div className="samples-search-actions">
            {matched && (
              <button type="button" className="samples-search-clear" onClick={clearSearch}>
                Clear
              </button>
            )}
            <button
              type="button"
              className="samples-search-btn"
              onClick={runSearch}
              disabled={searching || !query.trim()}
            >
              <Search size={15} /> {searching ? "Finding…" : "Find samples"}
            </button>
          </div>
        </div>
      </div>

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
            : `${visible.length} example${visible.length === 1 ? "" : "s"}`
              + (noMatches
                  ? " · no close matches yet — showing the full library"
                  : matched ? " · ranked by fit to your interest" : "")}
      </div>

      <ul className="samples-grid">
        {!loading && !error && visible.length === 0 && (
          <li className="samples-empty">
            No examples in this category yet. Try the “All” filter.
          </li>
        )}
        {visible.map((p) => {
          const isPdf = p.type === "pdf";
          // Authored PDFs download from our backend; links open the source.
          const href = isPdf
            ? `${API_BASE}/api/sample-proposals/${p.id}/download`
            : p.url;
          return (
            <li
              key={p.id}
              className={"sample-card" + (isPdf ? " sample-card-pdf" : "")}
            >
              <a
                className="sample-card-link"
                href={href}
                target="_blank"
                rel="noopener noreferrer"
                {...(isPdf ? { download: "" } : {})}
              >
                <div className="sample-card-body">
                  <div className="sample-card-titlerow">
                    <span className="sample-card-title">{p.title}</span>
                    {isPdf && (
                      <span className="sample-card-badge sample-badge-pdf">
                        PDF
                      </span>
                    )}
                    {p.access === "partial" && (
                      <span
                        className="sample-card-badge"
                        title="Some content needs a free account or is partly paywalled"
                      >
                        <Lock size={11} /> Partly paywalled
                      </span>
                    )}
                    {p.community && (
                      <span
                        className="sample-card-badge sample-badge-community"
                        title="Researcher-shared via Open Grants (ogrants.org), a community library"
                      >
                        Community
                      </span>
                    )}
                  </div>
                  <div className="sample-card-source">{p.source}</div>
                  {p.kind && <div className="sample-card-kind">{p.kind}</div>}
                  {p.why && <div className="sample-card-why">{p.why}</div>}
                  {matched && p.match?.terms?.length > 0 && (
                    <div className="sample-card-match">
                      Matches your interest: {p.match.terms.slice(0, 5).join(", ")}
                    </div>
                  )}
                  <div className="sample-card-tags">
                    {(p.categories || []).map((c) => (
                      <span key={c} className="sample-tag">{c}</span>
                    ))}
                  </div>
                </div>
                <span className="sample-card-open">
                  {isPdf ? (
                    <>
                      Download PDF <Download size={14} />
                    </>
                  ) : (
                    <>
                      Open <ExternalLink size={14} />
                    </>
                  )}
                </span>
              </a>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
