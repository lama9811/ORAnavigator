// FormsCatalog.jsx -- browseable catalog of ORA forms / templates /
// checklists / memos. Hits GET /api/forms with optional ?category=,
// ?sponsor=, ?role= filters. No LLM in the loop; everything is a static
// read of the bundled KB on the backend.

import React, { useState, useEffect, useMemo } from "react";
import { FaFilePdf } from "@react-icons/all-files/fa/FaFilePdf";
import { FaFileWord } from "@react-icons/all-files/fa/FaFileWord";
import { FaFileExcel } from "@react-icons/all-files/fa/FaFileExcel";
import { FaFileAlt } from "@react-icons/all-files/fa/FaFileAlt";
import { FaExternalLinkAlt } from "@react-icons/all-files/fa/FaExternalLinkAlt";
import { FaSearch } from "@react-icons/all-files/fa/FaSearch";
import { getApiBase } from "../lib/apiBase";
import "./FormsCatalog.css";

const API_BASE = getApiBase();

// Static option lists. Backend can return more values but these cover the
// real distribution today (Internal 61, NSF 9, NIH 4, State of MD 2).
const CATEGORIES = [
  { value: "", label: "All Categories" },
  { value: "pre_award", label: "Pre-Award" },
  { value: "post_award", label: "Post-Award" },
  { value: "research_compliance", label: "Compliance" },
  { value: "resources", label: "Resources" },
];
const SPONSORS = [
  { value: "", label: "All Sponsors" },
  { value: "Internal", label: "Internal / MSU" },
  { value: "NSF", label: "NSF" },
  { value: "NIH", label: "NIH" },
  { value: "DoD", label: "DoD" },
  { value: "DoE", label: "DoE" },
  { value: "NASA", label: "NASA" },
  { value: "Foundation", label: "Foundation" },
  { value: "State of Maryland", label: "State of Maryland" },
];
const ROLES = [
  { value: "", label: "All Roles" },
  { value: "PI", label: "PI / Faculty" },
  { value: "Staff", label: "Research Staff" },
  { value: "Admin", label: "Admin / Chair" },
];

// File-type icon from the form's URL extension. DocuSign and morgan.edu
// PDF links are the most common; everything else falls back to generic.
function fileIconFor(url) {
  const u = (url || "").toLowerCase();
  if (u.includes("docusign")) return <FaFileAlt className="form-card-icon docusign" />;
  if (u.endsWith(".pdf")) return <FaFilePdf className="form-card-icon pdf" />;
  if (u.endsWith(".doc") || u.endsWith(".docx")) return <FaFileWord className="form-card-icon word" />;
  if (u.endsWith(".xls") || u.endsWith(".xlsx")) return <FaFileExcel className="form-card-icon excel" />;
  return <FaFileAlt className="form-card-icon generic" />;
}

const CATEGORY_LABELS = {
  pre_award: "Pre-Award",
  post_award: "Post-Award",
  research_compliance: "Compliance",
  resources: "Resources",
};

export default function FormsCatalog() {
  const [forms, setForms] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [category, setCategory] = useState("");
  const [sponsor, setSponsor] = useState("");
  const [role, setRole] = useState("");
  const [search, setSearch] = useState("");

  // Refetch when any filter changes. Search is client-side so it doesn't
  // trigger a network round-trip.
  useEffect(() => {
    const token = localStorage.getItem("token");
    if (!token) {
      setError("Please log in to browse the Forms catalog.");
      setLoading(false);
      return;
    }
    const params = new URLSearchParams();
    if (category) params.set("category", category);
    if (sponsor) params.set("sponsor", sponsor);
    if (role) params.set("role", role);
    const qs = params.toString();

    setLoading(true);
    setError("");
    fetch(`${API_BASE}/api/forms${qs ? "?" + qs : ""}`, {
      headers: { Authorization: `Bearer ${token}` },
    })
      .then(async (r) => {
        if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
        return r.json();
      })
      .then((data) => setForms(data.forms || []))
      .catch((e) => setError("Couldn't load the forms catalog: " + e.message))
      .finally(() => setLoading(false));
  }, [category, sponsor, role]);

  // Client-side title search on top of the server-filtered list.
  const visible = useMemo(() => {
    const q = search.trim().toLowerCase();
    if (!q) return forms;
    return forms.filter(
      (f) =>
        f.title.toLowerCase().includes(q) ||
        (f.summary || "").toLowerCase().includes(q),
    );
  }, [forms, search]);

  return (
    <div className="forms-catalog">
      <header className="forms-header">
        <h1>Forms &amp; Templates</h1>
        <p className="forms-subtitle">
          Every ORA form, template, checklist, and memo in one place. Click any
          card to open the live form. No chat required.
        </p>
      </header>

      <section className="forms-filters" aria-label="Filters">
        <div className="forms-filter-group">
          <label htmlFor="filter-category">Category</label>
          <select
            id="filter-category"
            value={category}
            onChange={(e) => setCategory(e.target.value)}
          >
            {CATEGORIES.map((c) => (
              <option key={c.value} value={c.value}>{c.label}</option>
            ))}
          </select>
        </div>

        <div className="forms-filter-group">
          <label htmlFor="filter-sponsor">Sponsor</label>
          <select
            id="filter-sponsor"
            value={sponsor}
            onChange={(e) => setSponsor(e.target.value)}
          >
            {SPONSORS.map((s) => (
              <option key={s.value} value={s.value}>{s.label}</option>
            ))}
          </select>
        </div>

        <div className="forms-filter-group">
          <label htmlFor="filter-role">Role</label>
          <select
            id="filter-role"
            value={role}
            onChange={(e) => setRole(e.target.value)}
          >
            {ROLES.map((r) => (
              <option key={r.value} value={r.value}>{r.label}</option>
            ))}
          </select>
        </div>

        <div className="forms-filter-group forms-search-group">
          <label htmlFor="filter-search">Search</label>
          <div className="forms-search-input">
            <FaSearch className="forms-search-icon" />
            <input
              id="filter-search"
              type="text"
              placeholder="Search by title or description..."
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
          </div>
        </div>
      </section>

      <div className="forms-result-meta" role="status">
        {loading
          ? "Loading..."
          : error
            ? error
            : `${visible.length} form${visible.length === 1 ? "" : "s"}`}
      </div>

      <ul className="forms-grid">
        {!loading && !error && visible.length === 0 && (
          <li className="forms-empty">
            No forms match these filters. Try widening the filters or clearing
            the search box.
          </li>
        )}
        {visible.map((f) => (
          <li key={f.doc_id} className="form-card">
            <a
              className="form-card-link"
              href={f.url}
              target="_blank"
              rel="noopener noreferrer"
            >
              <div className="form-card-icon-wrap">{fileIconFor(f.url)}</div>
              <div className="form-card-body">
                <div className="form-card-title">{f.title}</div>
                {f.summary && (
                  <div className="form-card-summary">{f.summary}</div>
                )}
                <div className="form-card-tags">
                  <span className="form-tag form-tag-category">
                    {CATEGORY_LABELS[f.category] || f.category}
                  </span>
                  {(f.sponsors || []).slice(0, 2).map((s) => (
                    <span key={s} className="form-tag form-tag-sponsor">{s}</span>
                  ))}
                  {(f.roles || []).slice(0, 2).map((r) => (
                    <span key={r} className="form-tag form-tag-role">{r}</span>
                  ))}
                </div>
              </div>
              <FaExternalLinkAlt className="form-card-open" />
            </a>
          </li>
        ))}
      </ul>
    </div>
  );
}
