import { useState, useMemo } from "react";
import { ChevronRight, ChevronDown, AlertTriangle, FolderOpen } from "lucide-react";

/**
 * Browsable KB hierarchy for the admin Cloud Datastore panel.
 *
 * Mirrors morgan.edu/ora's left nav: nine sections, their subsections, and the
 * nested IACUC / Research Security branches. Counts are live.
 *
 * Unplaced documents are pinned at the top under "Unfiled" instead of being
 * hidden — a document you can't see is the problem this panel exists to solve.
 */

const formatBytes = (b) => {
  if (!b) return "";
  if (b < 1024) return `${b} B`;
  if (b < 1024 * 1024) return `${(b / 1024).toFixed(1)} KB`;
  return `${(b / 1024 / 1024).toFixed(1)} MB`;
};

function DocRow({ doc, selected, onSelect }) {
  return (
    <div
      className={`kb-tree-doc ${selected ? "active" : ""} ${doc.pending_change ? "review" : ""}`}
      onClick={() => onSelect(doc)}
      title={
        doc.pending_change
          ? `Proposed update awaiting approval — ${doc.pending_change.what_changed}`
          : doc.id
      }
    >
      {/* The document itself is unchanged; this badge is a join against pending
          scrape proposals, cleared the moment one is approved or dismissed. */}
      {doc.pending_change && <AlertTriangle size={11} className="kb-tree-doc-warn" />}
      <span className="kb-tree-doc-title">{doc.title || doc.id}</span>
      <span className="kb-tree-doc-size">{formatBytes(doc.size)}</span>
    </div>
  );
}

function TreeNode({ node, depth, expanded, toggle, selected, onSelect }) {
  const isOpen = expanded.has(node.path);
  const hasContents = node.count > 0;

  return (
    <div className="kb-tree-node">
      <div
        className={`kb-tree-branch depth-${Math.min(depth, 2)} ${hasContents ? "" : "empty"}`}
        onClick={() => toggle(node.path)}
      >
        {isOpen ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
        <span className="kb-tree-branch-title">{node.title}</span>
        {/* Recursive, so a collapsed section still shows there's something to
            look at inside it. */}
        {node.pending_count > 0 && (
          <span
            className="kb-tree-count review"
            title={`${node.pending_count} proposed update(s) awaiting approval`}
          >
            ⚠ {node.pending_count}
          </span>
        )}
        <span className="kb-tree-count">{node.count}</span>
      </div>

      {isOpen && (
        <div className="kb-tree-children">
          {node.docs.map((d) => (
            <DocRow
              key={d.id}
              doc={d}
              selected={selected?.id === d.id}
              onSelect={onSelect}
            />
          ))}
          {node.children.map((c) => (
            <TreeNode
              key={c.path}
              node={c}
              depth={depth + 1}
              expanded={expanded}
              toggle={toggle}
              selected={selected}
              onSelect={onSelect}
            />
          ))}
          {!hasContents && <div className="kb-tree-empty-note">No documents</div>}
        </div>
      )}
    </div>
  );
}

export default function KbTree({
  tree = [],
  unfiled = [],
  paths = [],
  selected,
  onSelect,
  onPlace,
}) {
  const [expanded, setExpanded] = useState(() => new Set(["__unfiled__"]));

  const toggle = (path) =>
    setExpanded((prev) => {
      const next = new Set(prev);
      next.has(path) ? next.delete(path) : next.add(path);
      return next;
    });

  const allPaths = useMemo(
    () => ["__unfiled__", ...paths.map((p) => p.path)],
    [paths]
  );

  const expandAll = () => setExpanded(new Set(allPaths));
  const collapseAll = () => setExpanded(new Set(unfiled.length ? ["__unfiled__"] : []));

  const unfiledOpen = expanded.has("__unfiled__");

  return (
    <div className="kb-tree">
      <div className="kb-tree-toolbar">
        <button className="kb-tree-toolbtn" onClick={expandAll}>Expand all</button>
        <button className="kb-tree-toolbtn" onClick={collapseAll}>Collapse all</button>
      </div>

      {unfiled.length > 0 && (
        <div className="kb-tree-node">
          <div
            className="kb-tree-branch unfiled"
            onClick={() => toggle("__unfiled__")}
          >
            {unfiledOpen ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
            <AlertTriangle size={13} />
            <span className="kb-tree-branch-title">Unfiled</span>
            <span className="kb-tree-count warn">{unfiled.length}</span>
          </div>

          {unfiledOpen && (
            <div className="kb-tree-children">
              <div className="kb-tree-unfiled-note">
                Not placed in the tree. Pick a section to file each one.
              </div>
              {unfiled.map((doc) => (
                <div key={doc.id} className="kb-tree-unfiled-row">
                  <DocRow
                    doc={doc}
                    selected={selected?.id === doc.id}
                    onSelect={onSelect}
                  />
                  <select
                    className="kb-tree-place"
                    defaultValue=""
                    onClick={(e) => e.stopPropagation()}
                    onChange={(e) => {
                      if (e.target.value) onPlace(doc, e.target.value);
                    }}
                  >
                    <option value="">Place in…</option>
                    {paths.map((p) => (
                      <option key={p.path} value={p.path}>
                        {p.label}
                      </option>
                    ))}
                  </select>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {tree.map((node) => (
        <TreeNode
          key={node.path}
          node={node}
          depth={0}
          expanded={expanded}
          toggle={toggle}
          selected={selected}
          onSelect={onSelect}
        />
      ))}

      {tree.length === 0 && unfiled.length === 0 && (
        <div className="empty-state">
          <FolderOpen size={16} /> No documents
        </div>
      )}
    </div>
  );
}
