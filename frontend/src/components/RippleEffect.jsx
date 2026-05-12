import { useState, useEffect, useCallback } from "react";
import { useNavigate } from "react-router-dom";
import {
  ReactFlow,
  Background,
  Controls,
  Handle,
  Position,
  useNodesState,
  useEdgesState,
  BezierEdge,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import dagre from "dagre";
import { FaSync } from "@react-icons/all-files/fa/FaSync";
import { FaExclamationTriangle } from "@react-icons/all-files/fa/FaExclamationTriangle";
import { FaCheckCircle } from "@react-icons/all-files/fa/FaCheckCircle";
import { FaTimes } from "@react-icons/all-files/fa/FaTimes";
import { FaLock } from "@react-icons/all-files/fa/FaLock";
import { FaUnlock } from "@react-icons/all-files/fa/FaUnlock";
import { FaArrowRight } from "@react-icons/all-files/fa/FaArrowRight";
import { getApiBase } from "../lib/apiBase";
import "./RippleEffect.css";

const API = getApiBase();

const STATUS_COLORS = {
  completed: { bg: "#e6f4ea", border: "#34a853", text: "#1e7e34", glow: "rgba(52,168,83,0.25)" },
  in_progress: { bg: "#e8f0fe", border: "#4285f4", text: "#1a73e8", glow: "rgba(66,133,244,0.25)" },
  at_risk: { bg: "#fce8e6", border: "#ea4335", text: "#d93025", glow: "rgba(234,67,53,0.3)" },
  future: { bg: "#f8f9fa", border: "#dadce0", text: "#5f6368", glow: "rgba(0,0,0,0.05)" },
};

function CourseNode({ data }) {
  const colors = STATUS_COLORS[data.status] || STATUS_COLORS.future;
  const isSelected = data.isSelected;
  return (
    <>
      <Handle type="target" position={Position.Top} style={{ opacity: 0, width: 6, height: 6 }} />
      <div
        className={`re-node ${data.status} ${data.highlighted ? "highlighted" : ""} ${isSelected ? "selected" : ""}`}
        style={{
          borderColor: isSelected ? colors.text : colors.border,
          background: colors.bg,
          boxShadow: isSelected ? `0 0 0 3px ${colors.glow}, 0 4px 14px ${colors.glow}` : undefined,
        }}
        onClick={() => data.onSelect?.(data)}
      >
        <div className="re-node-code" style={{ color: colors.text }}>{data.id}</div>
        {data.current_score != null && (
          <div className="re-node-score" style={{ color: colors.text }}>{data.current_score.toFixed(0)}%</div>
        )}
        {data.grade && <div className="re-node-grade">{data.grade}</div>}
        {data.status === "at_risk" && <div className="re-node-risk">!</div>}
      </div>
      <Handle type="source" position={Position.Bottom} style={{ opacity: 0, width: 6, height: 6 }} />
    </>
  );
}

const nodeTypes = { course: CourseNode };

function layoutGraph(nodes, edges) {
  const g = new dagre.graphlib.Graph();
  g.setDefaultEdgeLabel(() => ({}));
  g.setGraph({ rankdir: "TB", nodesep: 40, ranksep: 70, marginx: 20, marginy: 20 });

  nodes.forEach((n) => g.setNode(n.id, { width: 110, height: 58 }));
  edges.forEach((e) => g.setEdge(e.source, e.target));
  dagre.layout(g);

  return nodes.map((n) => {
    const pos = g.node(n.id);
    return { ...n, position: { x: pos.x - 55, y: pos.y - 29 } };
  });
}

export default function RippleEffect() {
  const navigate = useNavigate();
  const [graphData, setGraphData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [selected, setSelected] = useState(null);
  const [highlightedPath, setHighlightedPath] = useState(null);
  const [nodes, setNodes, onNodesChange] = useNodesState([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState([]);

  const token = localStorage.getItem("token");

  useEffect(() => {
    if (!token) { setLoading(false); return; }
    fetch(`${API}/api/ripple-effect`, { headers: { Authorization: `Bearer ${token}` } })
      .then(r => r.ok ? r.json() : null)
      .then(d => { if (d) setGraphData(d); })
      .catch(() => {})
      .finally(() => setLoading(false));
  }, []);

  const handleSelect = useCallback((nodeData) => {
    setSelected(prev => prev?.id === nodeData.id ? null : nodeData);
    setHighlightedPath(null);
  }, []);

  useEffect(() => {
    if (!graphData) return;

    const dangerCodes = new Set();
    if (highlightedPath) {
      dangerCodes.add(highlightedPath.root_course);
      highlightedPath.cascade.forEach(c => dangerCodes.add(c));
    }

    // Highlight edges connected to selected node
    const selectedId = selected?.id;
    const connectedEdges = new Set();
    if (selectedId) {
      graphData.edges.forEach(e => {
        if (e.from === selectedId || e.to === selectedId) {
          connectedEdges.add(`${e.from}->${e.to}`);
        }
      });
    }

    const flowNodes = graphData.nodes.map(n => ({
      id: n.id,
      type: "course",
      data: {
        ...n,
        highlighted: dangerCodes.has(n.id),
        isSelected: n.id === selectedId,
        onSelect: handleSelect,
      },
      position: { x: 0, y: 0 },
    }));

    const flowEdges = graphData.edges.map((e, i) => {
      const isDanger = dangerCodes.has(e.from) && dangerCodes.has(e.to);
      const isConnected = connectedEdges.has(`${e.from}->${e.to}`);
      const srcNode = graphData.nodes.find(n => n.id === e.from);
      const srcColors = STATUS_COLORS[srcNode?.status] || STATUS_COLORS.future;

      return {
        id: `e-${i}`,
        source: e.from,
        target: e.to,
        type: "default",
        animated: isDanger || isConnected,
        markerEnd: {
          type: "arrowclosed",
          color: isDanger ? "#ea4335" : isConnected ? srcColors.border : "#c4c9ce",
          width: 14,
          height: 14,
        },
        style: {
          stroke: isDanger ? "#ea4335" : isConnected ? srcColors.border : "#c4c9ce",
          strokeWidth: isDanger ? 3 : isConnected ? 2.5 : 1.2,
          opacity: (selectedId && !isConnected && !isDanger) ? 0.2 : 1,
          transition: "opacity 0.3s, stroke 0.3s, stroke-width 0.3s",
        },
      };
    });

    const laid = layoutGraph(flowNodes, flowEdges);
    setNodes(laid);
    setEdges(flowEdges);
  }, [graphData, highlightedPath, selected, handleSelect]);

  if (loading) return (
    <div className="re"><div className="re-center"><FaSync className="re-spin" size={20} /><p>Loading prerequisite graph...</p></div></div>
  );

  if (!graphData) return (
    <div className="re">
      <div className="re-center">
        <h2>Connect Your Data</h2>
        <p className="re-muted">Sync DegreeWorks and Canvas to see your prerequisite map.</p>
        <button className="re-primary-btn" onClick={() => navigate("/profile")}><FaSync size={13} /> Go to Profile</button>
      </div>
    </div>
  );

  const stats = graphData.stats;
  const pct = stats.total > 0 ? Math.round((stats.completed / stats.total) * 100) : 0;

  return (
    <div className="re">
      <header className="re-head">
        <div>
          <h1>Ripple Effect</h1>
          <span className="re-muted">See how each course connects to your degree</span>
        </div>
        <div className="re-progress-ring">
          <svg viewBox="0 0 36 36" className="re-ring-svg">
            <path className="re-ring-bg" d="M18 2.0845 a 15.9155 15.9155 0 0 1 0 31.831 a 15.9155 15.9155 0 0 1 0 -31.831" />
            <path className="re-ring-fill" strokeDasharray={`${pct}, 100`} d="M18 2.0845 a 15.9155 15.9155 0 0 1 0 31.831 a 15.9155 15.9155 0 0 1 0 -31.831" />
          </svg>
          <span className="re-ring-text">{pct}%</span>
        </div>
      </header>

      {/* Stats Bar */}
      <div className="re-stats">
        <div className="re-stat"><span className="re-stat-dot completed" /><span>{stats.completed} Completed</span></div>
        <div className="re-stat"><span className="re-stat-dot in_progress" /><span>{stats.in_progress} In Progress</span></div>
        {stats.at_risk > 0 && <div className="re-stat danger"><span className="re-stat-dot at_risk" /><span>{stats.at_risk} At Risk</span></div>}
        <div className="re-stat"><span className="re-stat-dot future" /><span>{stats.future} Remaining</span></div>
      </div>

      {/* Danger Paths */}
      {graphData.danger_paths.length > 0 && (
        <div className="re-danger-section">
          <h3><FaExclamationTriangle size={14} /> Danger Paths</h3>
          {graphData.danger_paths.map((dp, i) => (
            <button
              key={i}
              className={`re-danger-card ${highlightedPath === dp ? "active" : ""}`}
              onClick={() => setHighlightedPath(highlightedPath === dp ? null : dp)}
            >
              <div className="re-danger-title">{dp.root_course} ({dp.current_score?.toFixed(0)}%)</div>
              <div className="re-danger-detail">
                Failing cascades to {dp.cascade_count} courses, ~{dp.graduation_delay_semesters} semester delay
              </div>
            </button>
          ))}
        </div>
      )}

      {/* Graph */}
      <div className="re-graph-container">
        <ReactFlow
          nodes={nodes}
          edges={edges}
          onNodesChange={onNodesChange}
          onEdgesChange={onEdgesChange}
          nodeTypes={nodeTypes}
          fitView
          minZoom={0.25}
          maxZoom={2}
          proOptions={{ hideAttribution: true }}
        >
          <Background color="#e8eaed" gap={24} size={1} />
          <Controls showInteractive={false} />
        </ReactFlow>
      </div>

      {/* Selected Course Detail */}
      {selected && (
        <div className="re-detail" key={selected.id}>
          <button className="re-detail-close" onClick={() => setSelected(null)}><FaTimes size={14} /></button>

          {/* Top row: status + course info */}
          <div className="re-detail-top">
            <span className={`re-status-badge ${selected.status}`}>
              {selected.status === "completed" && <FaCheckCircle size={11} />}
              {selected.status === "at_risk" && <FaExclamationTriangle size={11} />}
              {selected.status.replace("_", " ")}
            </span>
            <div className="re-detail-title">
              <h3>{selected.id}</h3>
              <span>{selected.name}</span>
            </div>
            <div className="re-detail-badges">
              {selected.current_score != null && (
                <div className="re-badge-score">{selected.current_score.toFixed(1)}%</div>
              )}
              {selected.grade && <div className="re-badge-grade">{selected.grade}</div>}
            </div>
          </div>

          {/* Meta chips */}
          <div className="re-detail-meta">
            <span>{selected.credits} credits</span>
            <span>{selected.category}</span>
            {selected.offered?.length > 0 && <span>{selected.offered.join(" / ")}</span>}
          </div>

          {/* Unlocks + Requires in 2-column grid */}
          <div className="re-detail-grid">
            {selected.blocked_by.length > 0 && (
              <div className="re-detail-col requires">
                <div className="re-col-header">
                  <FaLock size={11} />
                  <span>Prerequisites ({selected.blocked_by.length})</span>
                </div>
                <div className="re-col-chips">
                  {selected.blocked_by.map(c => {
                    const node = graphData.nodes.find(n => n.id === c);
                    const st = node?.status || "future";
                    return (
                      <button key={c} className={`re-chip-btn ${st}`} onClick={() => {
                        const nd = graphData.nodes.find(n => n.id === c);
                        if (nd) handleSelect(nd);
                      }}>
                        <span className="re-chip-dot" />
                        {c}
                        {node?.grade && <span className="re-chip-grade">{node.grade}</span>}
                      </button>
                    );
                  })}
                </div>
              </div>
            )}

            {selected.unlocks.length > 0 && (
              <div className="re-detail-col unlocks">
                <div className="re-col-header">
                  <FaUnlock size={11} />
                  <span>Unlocks ({selected.unlocks.length})</span>
                </div>
                <div className="re-col-chips">
                  {selected.unlocks.map(c => {
                    const node = graphData.nodes.find(n => n.id === c);
                    const st = node?.status || "future";
                    return (
                      <button key={c} className={`re-chip-btn ${st}`} onClick={() => {
                        const nd = graphData.nodes.find(n => n.id === c);
                        if (nd) handleSelect(nd);
                      }}>
                        <span className="re-chip-dot" />
                        {c}
                      </button>
                    );
                  })}
                </div>
              </div>
            )}

            {selected.blocked_by.length === 0 && selected.unlocks.length === 0 && (
              <div className="re-detail-col"><p className="re-muted">No prerequisite connections</p></div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
