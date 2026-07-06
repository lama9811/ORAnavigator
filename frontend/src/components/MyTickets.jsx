// MyTickets.jsx -- a read-only view of the support tickets the signed-in user
// has submitted, with their status and (when resolved) the admin's notes.
//
// Hits GET /api/tickets, which already scopes non-admins to their own tickets
// on the backend -- no new endpoint or DB work. No LLM in the loop; this is a
// plain read of the support_tickets table.
//
// Status is deliberately simplified for the user's eyes: the backend tracks
// open / in_progress / resolved / closed, but a submitter only needs to know
// "we're on it" vs "it's done." So resolved|closed -> "Resolved" (shown with
// the admin's notes), everything else -> "In Progress".

import React, { useState, useEffect } from "react";
import { useNavigate } from "react-router-dom";
import { CheckCircle2, Clock, Bug, Lightbulb, CircleHelp, Inbox } from "lucide-react";
import { getApiBase } from "../lib/apiBase";
import "./MyTickets.css";

const API_BASE = getApiBase();

// Read the role claim out of the JWT without a network round-trip -- the same
// approach App.jsx uses to gate the /admin route. Admins manage every ticket in
// the Admin dashboard, so this personal view isn't for them.
function roleFromToken() {
  try {
    const token = localStorage.getItem("token");
    if (!token) return null;
    const b64 = token.split(".")[1].replace(/-/g, "+").replace(/_/g, "/");
    const json = decodeURIComponent(
      atob(b64)
        .split("")
        .map((c) => "%" + ("00" + c.charCodeAt(0).toString(16)).slice(-2))
        .join("")
    );
    return JSON.parse(json).role || null;
  } catch {
    return null;
  }
}

const CATEGORY_META = {
  bug: { label: "Bug Report", icon: Bug },
  feature: { label: "Feature Request", icon: Lightbulb },
  question: { label: "Question", icon: CircleHelp },
  other: { label: "Other", icon: Inbox },
};

// Collapse the backend's four statuses into the two a submitter cares about.
function isResolved(status) {
  return status === "resolved" || status === "closed";
}

function formatDate(iso) {
  if (!iso) return "";
  try {
    return new Date(iso).toLocaleDateString(undefined, {
      year: "numeric",
      month: "short",
      day: "numeric",
    });
  } catch {
    return "";
  }
}

export default function MyTickets() {
  const navigate = useNavigate();
  const [tickets, setTickets] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  // Admins manage all tickets in the dashboard; if one reaches this URL
  // directly, send them there instead of showing the personal view.
  const isAdmin = roleFromToken() === "admin";

  useEffect(() => {
    if (isAdmin) navigate("/admin", { replace: true });
  }, [isAdmin, navigate]);

  useEffect(() => {
    if (isAdmin) return;
    const token = localStorage.getItem("token");
    if (!token) {
      setError("Please log in to view your support tickets.");
      setLoading(false);
      return;
    }
    setLoading(true);
    setError("");
    fetch(`${API_BASE}/api/tickets`, {
      headers: { Authorization: `Bearer ${token}` },
    })
      .then(async (r) => {
        if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
        return r.json();
      })
      .then((data) => setTickets(data.tickets || []))
      .catch((e) => setError("Couldn't load your tickets: " + e.message))
      .finally(() => setLoading(false));
  }, [isAdmin]);

  // Don't flash the personal ticket UI while the admin redirect is in flight.
  if (isAdmin) return null;

  return (
    <div className="mytickets">
      <header className="mytickets-header">
        <h1>My Support Tickets</h1>
        <p className="mytickets-subtitle">
          Issues and requests you've sent to the ORA Navigator team, and where
          they stand. Resolved tickets include a note from an administrator.
        </p>
      </header>

      <div className="mytickets-result-meta" role="status">
        {loading
          ? "Loading..."
          : error
            ? error
            : `${tickets.length} ticket${tickets.length === 1 ? "" : "s"}`}
      </div>

      {!loading && !error && tickets.length === 0 && (
        <div className="mytickets-empty">
          <Inbox size={32} aria-hidden="true" />
          <p>You haven't submitted any tickets yet.</p>
          <p className="mytickets-empty-hint">
            Use <strong>Contact Support</strong> in the sidebar to report a bug
            or request a feature.
          </p>
        </div>
      )}

      <ul className="mytickets-list">
        {tickets.map((t) => {
          const resolved = isResolved(t.status);
          const meta = CATEGORY_META[t.category] || CATEGORY_META.other;
          const CategoryIcon = meta.icon;
          return (
            <li key={t.id} className="ticket-card">
              <div className="ticket-card-top">
                <div className="ticket-card-heading">
                  <CategoryIcon size={16} className="ticket-category-icon" aria-hidden="true" />
                  <span className="ticket-subject">{t.subject}</span>
                </div>
                <span
                  className={`ticket-status-badge ${resolved ? "is-resolved" : "is-progress"}`}
                >
                  {resolved ? <CheckCircle2 size={13} /> : <Clock size={13} />}
                  {resolved ? "Resolved" : "In Progress"}
                </span>
              </div>

              <div className="ticket-card-meta">
                <span className="ticket-category-label">{meta.label}</span>
                <span className="ticket-meta-dot" aria-hidden="true">•</span>
                <span className="ticket-date">Submitted {formatDate(t.created_at)}</span>
              </div>

              {t.description && (
                <p className="ticket-description">{t.description}</p>
              )}

              {resolved && t.admin_notes && (
                <div className="ticket-resolution">
                  <div className="ticket-resolution-label">
                    <CheckCircle2 size={14} />
                    <span>Resolution note</span>
                  </div>
                  <p className="ticket-resolution-text">{t.admin_notes}</p>
                </div>
              )}
            </li>
          );
        })}
      </ul>
    </div>
  );
}
