import { useState, useEffect, useRef, useCallback } from "react";
import { Globe, Loader2, AlertTriangle, Check, RotateCcw, ExternalLink, X, ChevronRight, ChevronDown } from "lucide-react";

/**
 * Web scrape: trigger a crawl of morgan.edu/ora, watch it live, review what
 * changed.
 *
 * Progress is POLLED, not streamed. Cloud Run caps a request at 300s, so an SSE
 * stream would die partway through a crawl that legitimately runs longer — and
 * because the state lives in Cloud SQL rather than in a backend process, closing
 * this tab and reopening it picks the progress bar back up where it was.
 */

const POLL_MS = 2000;

const fmtDuration = (s) => {
  if (!s && s !== 0) return "";
  const m = Math.floor(s / 60);
  const sec = s % 60;
  return m ? `${m}m ${sec}s` : `${sec}s`;
};

// Relative age of a proposal, so the newest run is obvious at a glance without
// doing date arithmetic. The card carries the full local timestamp as a
// tooltip. Rows are returned newest-first, so this mainly answers "is this from
// the run I just did, or one from last week that I never cleared?".
const fmtWhen = (iso) => {
  if (!iso) return "";
  const t = new Date(iso.endsWith("Z") || iso.includes("+") ? iso : `${iso}Z`);
  if (Number.isNaN(t.getTime())) return "";
  const mins = Math.floor((Date.now() - t.getTime()) / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  const days = Math.floor(hrs / 24);
  if (days < 7) return `${days}d ago`;
  return t.toLocaleDateString(undefined, { month: "short", day: "numeric" });
};

const fmtExact = (iso) => {
  if (!iso) return "";
  const t = new Date(iso.endsWith("Z") || iso.includes("+") ? iso : `${iso}Z`);
  return Number.isNaN(t.getTime()) ? "" : t.toLocaleString();
};

const TYPE_LABEL = {
  modified: "changed",
  new: "new page",
  removed: "gone from site",
  unreadable: "couldn't read",
};

const STATUS_META = {
  pending:  { label: "Awaiting approval", cls: "review" },
  approved: { label: "Approved", cls: "applied" },
  rejected: { label: "Dismissed", cls: "skipped" },
  skipped:  { label: "Couldn't read", cls: "skipped" },
  cosmetic: { label: "Cosmetic", cls: "cosmetic" },
};

export default function KbScrapePanel({ apiBase, token, onDocsChanged }) {
  const [run, setRun] = useState(null);
  const [baseline, setBaseline] = useState(0);
  const [changes, setChanges] = useState([]);
  const [counts, setCounts] = useState({});
  const [starting, setStarting] = useState(false);
  const [diff, setDiff] = useState(null);
  const [busyId, setBusyId] = useState(null);
  const [bulkBusy, setBulkBusy] = useState(false);
  // Collapsed by default rather than removed. Neither group is actionable, but
  // both carry signal that matters once the browser engine is live: a change to
  // a page feeding 52 documents is reported ONLY in review-by-hand, and the
  // unreadable list is how a failed read stays visible instead of being
  // mistaken for deleted content. Hiding them outright would drop both.
  const [openGroups, setOpenGroups] = useState({ manual: false, settled: false });
  const toggleGroup = (key) =>
    setOpenGroups((g) => ({ ...g, [key]: !g[key] }));
  const timer = useRef(null);
  const wasRunning = useRef(false);

  const auth = { Authorization: `Bearer ${token}` };

  const loadChanges = useCallback(async () => {
    try {
      const res = await fetch(`${apiBase}/api/admin/kb-scrape/changes`, { headers: auth });
      if (res.ok) {
        const data = await res.json();
        setChanges(data.changes || []);
        setCounts(data.counts || {});
      }
    } catch { /* the status poll is what matters; a failed list retries next tick */ }
  }, [apiBase, token]);

  const poll = useCallback(async () => {
    try {
      const res = await fetch(`${apiBase}/api/admin/kb-scrape/status`, { headers: auth });
      if (!res.ok) return;
      const data = await res.json();
      setRun(data.run);
      setBaseline(data.baseline_pages || 0);

      const running = data.run?.status === "running" || data.run?.status === "queued";
      // Refresh the document list once, on the transition to finished — a scrape
      // may have rewritten content and flagged documents for review.
      if (wasRunning.current && !running) {
        loadChanges();
        onDocsChanged?.();
      }
      wasRunning.current = running;
    } catch { /* transient; next tick retries */ }
  }, [apiBase, token, loadChanges, onDocsChanged]);

  useEffect(() => {
    poll();
    loadChanges();
  }, [poll, loadChanges]);

  useEffect(() => {
    const running = run?.status === "running" || run?.status === "queued";
    clearInterval(timer.current);
    if (running) timer.current = setInterval(poll, POLL_MS);
    return () => clearInterval(timer.current);
  }, [run?.status, poll]);

  const start = async () => {
    setStarting(true);
    try {
      const res = await fetch(`${apiBase}/api/admin/kb-scrape/run`, { method: "POST", headers: auth });
      const data = await res.json();
      if (res.ok) {
        setRun(data);
        wasRunning.current = true;
      } else {
        alert(data.detail || "Could not start the scrape");
      }
    } catch (e) { alert("Could not start the scrape: " + e.message); }
    finally { setStarting(false); }
  };

  const cancel = async () => {
    await fetch(`${apiBase}/api/admin/kb-scrape/cancel`, { method: "POST", headers: auth });
    poll();
  };

  const act = async (change, what) => {
    setBusyId(change.id);
    try {
      const res = await fetch(
        `${apiBase}/api/admin/kb-scrape/changes/${change.id}/${what}`,
        { method: "POST", headers: auth }
      );
      if (res.ok) {
        await loadChanges();
        onDocsChanged?.();
      } else {
        const d = await res.json();
        alert(d.detail || "Action failed");
      }
    } finally { setBusyId(null); }
  };

  // Bulk dismiss for the review-by-hand pile, which routinely runs to 20+ rows
  // that carry no draft and cannot change anything. Deliberately NOT offered on
  // the approvable group: those are the only real change reports in the queue,
  // and clearing them in one click would throw away the actual signal.
  //
  // There is no bulk endpoint, so this walks the existing per-row reject. That
  // keeps it a frontend-only change — no API to deploy — at the cost of one
  // request per row, which is fine at this scale.
  const dismissAll = async (rows) => {
    if (!rows.length) return;
    const ok = window.confirm(
      `Dismiss all ${rows.length} items in "Review by hand"?\n\n` +
      `They will be cleared from this list. No document is changed either way — ` +
      `none of these carry a draft. This cannot be undone.`
    );
    if (!ok) return;

    setBulkBusy(true);
    let failed = 0;
    try {
      for (const c of rows) {
        try {
          const res = await fetch(
            `${apiBase}/api/admin/kb-scrape/changes/${c.id}/reject`,
            { method: "POST", headers: auth }
          );
          if (!res.ok) failed += 1;
        } catch { failed += 1; }
      }
      await loadChanges();
      onDocsChanged?.();
      if (failed) alert(`${failed} of ${rows.length} could not be dismissed. The rest were cleared.`);
    } finally { setBulkBusy(false); }
  };

  const showDiff = async (change) => {
    const res = await fetch(`${apiBase}/api/admin/kb-scrape/changes/${change.id}/diff`, { headers: auth });
    if (res.ok) setDiff(await res.json());
  };

  const running = run?.status === "running" || run?.status === "queued";
  const visible = changes.filter((c) => c.status !== "cosmetic");
  const pendingCount = changes.filter((c) => c.status === "pending").length;

  // Pending items split by whether there is actually anything to approve.
  // A page feeding many documents never gets a draft — re-splitting the IACUC
  // SOPs page back across its 52 documents unattended would corrupt all of
  // them — so it can only ever be a pointer to go and look. Mixed into one
  // list, a run with nothing approvable read as a broken Approve button rather
  // than a deliberate refusal, which is exactly how it was reported.
  const readyToApprove = visible.filter((c) => c.status === "pending" && c.has_diff);
  const reviewByHand = visible.filter((c) => c.status === "pending" && !c.has_diff);
  const settled = visible.filter((c) => c.status !== "pending");

  const renderChange = (c) => {
    const meta = STATUS_META[c.status] || STATUS_META.needs_review;
    return (
      <div key={c.id} className={`scrape-change ${meta.cls} ${c.reverted ? "reverted" : ""}`}>
        <div className="scrape-change-main">
          <div className="scrape-change-title">
            <span className={`scrape-tag ${meta.cls}`}>{TYPE_LABEL[c.change_type] || c.change_type}</span>
            <strong>{c.page_title || c.doc_id || c.url}</strong>
            {c.reverted && <span className="scrape-tag reverted">reverted</span>}
            {c.created_at && (
              <span className="scrape-change-when" title={fmtExact(c.created_at)}>
                {fmtWhen(c.created_at)}
              </span>
            )}
          </div>
          <div className="scrape-change-what">{c.what_changed}</div>
          {c.evidence_quote && (
            <div className="scrape-change-quote">“{c.evidence_quote}”</div>
          )}
          {c.affected_doc_ids?.length > 1 && (
            <div className="scrape-change-affected">
              {c.affected_doc_ids.length} documents derive from this page:{" "}
              {c.affected_doc_ids.slice(0, 6).join(", ")}
              {c.affected_doc_ids.length > 6 && ` +${c.affected_doc_ids.length - 6} more`}
            </div>
          )}
        </div>

        <div className="scrape-change-actions">
          <a href={c.url} target="_blank" rel="noreferrer" className="scrape-linkbtn" title="Open the live page">
            <ExternalLink size={12} />
          </a>
          {c.has_diff && (
            <button className="scrape-linkbtn" onClick={() => showDiff(c)}>diff</button>
          )}
          {c.status === "pending" && (
            <>
              {/* Only offered when there is an actual draft to apply. A
                  multi-document page has no single replacement, so it
                  gets Dismiss only. */}
              {c.has_diff && (
                <button
                  className="scrape-linkbtn ok"
                  disabled={busyId === c.id}
                  onClick={() => act(c, "approve")}
                  title="Save the proposed content as the new document"
                >
                  <Check size={12} /> Approve
                </button>
              )}
              <button
                className="scrape-linkbtn undo"
                disabled={busyId === c.id}
                onClick={() => act(c, "reject")}
                title="Dismiss — the document was never changed"
              >
                <X size={12} /> Dismiss
              </button>
            </>
          )}
          {c.status === "approved" && !c.reverted && (
            <button
              className="scrape-linkbtn undo"
              disabled={busyId === c.id}
              onClick={() => act(c, "revert")}
              title="Restore the content this replaced"
            >
              <RotateCcw size={12} /> Undo
            </button>
          )}
        </div>
      </div>
    );
  };

  return (
    <div className="scrape-panel">
      <div className="scrape-head">
        <div>
          <h3><Globe size={14} /> Web scrape</h3>
          <p>
            Crawls morgan.edu/office-of-research-administration and proposes updates
            for documents whose page changed. <strong>Nothing is written until you
            approve it.</strong>
            {baseline > 0
              ? ` ${baseline} pages tracked.`
              : " First run establishes the baseline, so it checks every page."}
          </p>
        </div>
        {!running && (
          <button className="action-btn primary" onClick={start} disabled={starting}>
            {starting ? "Starting…" : "Run scrape"}
          </button>
        )}
      </div>

      {running && (
        <div className="scrape-progress">
          <div className="scrape-progress-top">
            <Loader2 size={14} className="spinning" />
            <strong>
              {run.status === "queued" ? "Starting the scraper…" : "Crawling morgan.edu"}
            </strong>
            <button className="scrape-cancel" onClick={cancel}>Cancel</button>
          </div>

          <div className="scrape-bar">
            <div
              className="scrape-bar-fill"
              style={{ width: `${run.pages_found ? run.percent : 3}%` }}
            />
          </div>

          <div className="scrape-progress-meta">
            <span>
              <strong>{run.pages_done}</strong>
              {run.pages_found > 0 && <> / {run.pages_found} pages</>}
            </span>
            <span>{fmtDuration(run.elapsed_s)} elapsed</span>
            {run.eta_s != null && <span>~{fmtDuration(run.eta_s)} left</span>}
            {run.changes_found > 0 && (
              <span className="scrape-live-count">
                {run.changes_found} to review
              </span>
            )}
          </div>

          {run.current_url && (
            <div className="scrape-current" title={run.current_url}>
              {run.current_url.replace("https://www.morgan.edu", "")}
            </div>
          )}
          {/* The denominator grows during a breadth-first crawl, so the bar can
              move backwards early on. Say so rather than looking broken. */}
          <div className="scrape-note">
            Pages are discovered as it goes, so the total keeps rising until the
            crawl fans out. Safe to leave this page — it keeps running.
          </div>
        </div>
      )}

      {!running && run && run.status !== "idle" && (
        <div className={`scrape-summary ${run.status}`}>
          {run.status === "succeeded" && (
            <>
              <Check size={14} /> Checked <strong>{run.pages_done}</strong> pages in{" "}
              {fmtDuration(run.elapsed_s)} — <strong>{pendingCount}</strong>{" "}
              {pendingCount === 1 ? "change awaits" : "changes await"} your approval
              {run.pages_failed > 0 && <>, {run.pages_failed} unreadable</>}. Nothing has
              been changed yet.
            </>
          )}
          {run.status === "failed" && (
            <><AlertTriangle size={14} /> Scrape failed. {run.error}</>
          )}
          {run.status === "cancelled" && (
            <><X size={14} /> Cancelled after {run.pages_done} pages. Everything applied
            before the stop is still applied.</>
          )}
        </div>
      )}

      {visible.length > 0 && (
        <div className="scrape-changes">
          <div className="scrape-changes-head">
            <h4>What changed</h4>
            <span className="scrape-counts">
              {Object.entries(counts)
                .filter(([k]) => k !== "cosmetic")
                .map(([k, v]) => `${v} ${STATUS_META[k]?.label.toLowerCase() || k}`)
                .join(" · ")}
              {counts.cosmetic > 0 && (
                <em> · {counts.cosmetic} cosmetic, hidden</em>
              )}
            </span>
          </div>

          <div className="scrape-group">
            <div className="scrape-group-head">
              <h5>
                Ready to approve
                <span className="scrape-group-n">{readyToApprove.length}</span>
              </h5>
              <p>
                One page, one document — there is a drafted replacement, so Approve
                writes it and Undo puts the old version back.
              </p>
            </div>
            {readyToApprove.length > 0 ? (
              readyToApprove.map(renderChange)
            ) : (
              <div className="scrape-empty">
                {reviewByHand.length > 0
                  ? "Nothing from this run can be applied automatically. Every changed page below feeds more than one document, so there is no single replacement to write — which is why no Approve button appears."
                  : "Nothing is waiting on your approval."}
              </div>
            )}
          </div>

          {reviewByHand.length > 0 && (
            <div className="scrape-group">
              <div className="scrape-group-head">
                <h5>
                  <button
                    className="scrape-group-toggle"
                    onClick={() => toggleGroup("manual")}
                    aria-expanded={openGroups.manual}
                  >
                    {openGroups.manual ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
                    Review by hand
                    <span className="scrape-group-n">{reviewByHand.length}</span>
                  </button>
                  <button
                    className="scrape-linkbtn undo scrape-dismiss-all"
                    disabled={bulkBusy}
                    onClick={() => dismissAll(reviewByHand)}
                    title="Dismiss every item in this group. No document is changed."
                  >
                    <X size={12} /> {bulkBusy ? "Dismissing…" : `Dismiss all ${reviewByHand.length}`}
                  </button>
                </h5>
                {openGroups.manual && (
                  <p>
                    Each of these pages feeds several documents, so there is no single
                    replacement to approve. Open the page, check the documents it feeds,
                    then dismiss. Nothing here can change a document.
                  </p>
                )}
              </div>
              {openGroups.manual && reviewByHand.map(renderChange)}
            </div>
          )}

          {settled.length > 0 && (
            <div className="scrape-group">
              <div className="scrape-group-head">
                <h5>
                  <button
                    className="scrape-group-toggle"
                    onClick={() => toggleGroup("settled")}
                    aria-expanded={openGroups.settled}
                  >
                    {openGroups.settled ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
                    Already handled
                    <span className="scrape-group-n">{settled.length}</span>
                  </button>
                </h5>
                {openGroups.settled && (
                  <p>
                    Approved, dismissed, and pages that could not be read. A page that
                    could not be read is left completely alone — it is never treated as
                    deleted content.
                  </p>
                )}
              </div>
              {openGroups.settled && settled.map(renderChange)}
            </div>
          )}
        </div>
      )}

      {diff && (
        <div className="newdoc-backdrop" onClick={() => setDiff(null)}>
          <div className="newdoc-modal scrape-diff" onClick={(e) => e.stopPropagation()}>
            <div className="newdoc-head">
              <h3>{diff.doc_id || diff.url}</h3>
              <button className="newdoc-close" onClick={() => setDiff(null)}><X size={14} /></button>
            </div>
            <p className="scrape-diff-what">{diff.what_changed}</p>
            <div className="scrape-diff-cols">
              <div>
                <label className="newdoc-label">Before</label>
                <pre className="scrape-diff-pane old">{diff.previous_content || "(none)"}</pre>
              </div>
              <div>
                <label className="newdoc-label">After</label>
                <pre className="scrape-diff-pane new">{diff.new_content || "(none)"}</pre>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
