// src/components/AdminDashboard.jsx
import React, { useState, useEffect, useRef, useCallback } from "react";
import { useNavigate } from "react-router-dom";
import { toast } from "sonner";
import { FaCog } from "@react-icons/all-files/fa/FaCog";
import { FaTicketAlt } from "@react-icons/all-files/fa/FaTicketAlt";
import { FaBug } from "@react-icons/all-files/fa/FaBug";
import { FaLightbulb } from "@react-icons/all-files/fa/FaLightbulb";
import { FaQuestionCircle } from "@react-icons/all-files/fa/FaQuestionCircle";
import { FaEye } from "@react-icons/all-files/fa/FaEye";
import { FaTimes } from "@react-icons/all-files/fa/FaTimes";
import { FaCheck } from "@react-icons/all-files/fa/FaCheck";
import { FaClock } from "@react-icons/all-files/fa/FaClock";
import { FaInbox } from "@react-icons/all-files/fa/FaInbox";
import { FaExclamationCircle } from "@react-icons/all-files/fa/FaExclamationCircle";
import { FaSpinner } from "@react-icons/all-files/fa/FaSpinner";
import { FaCheckCircle } from "@react-icons/all-files/fa/FaCheckCircle";
import { FaUser } from "@react-icons/all-files/fa/FaUser";
import { FaUsers } from "@react-icons/all-files/fa/FaUsers";
import { FaDatabase } from "@react-icons/all-files/fa/FaDatabase";
import { FaServer } from "@react-icons/all-files/fa/FaServer";
import { FaChartBar } from "@react-icons/all-files/fa/FaChartBar";
import { FaEdit } from "@react-icons/all-files/fa/FaEdit";
import { FaSearch } from "@react-icons/all-files/fa/FaSearch";
import { FaSync } from "@react-icons/all-files/fa/FaSync";
import { FaTrash } from "@react-icons/all-files/fa/FaTrash";
import { FaSave } from "@react-icons/all-files/fa/FaSave";
import { FaUserShield } from "@react-icons/all-files/fa/FaUserShield";
import { FaUserGraduate } from "@react-icons/all-files/fa/FaUserGraduate";
import { FaCalendarPlus } from "@react-icons/all-files/fa/FaCalendarPlus";
import { FaLink } from "@react-icons/all-files/fa/FaLink";
import { FaMicrophone } from "@react-icons/all-files/fa/FaMicrophone";
import { FaStop } from "@react-icons/all-files/fa/FaStop";
import { FaFileAlt } from "@react-icons/all-files/fa/FaFileAlt";
import { FaRoad } from "@react-icons/all-files/fa/FaRoad";
import { FaRobot } from "@react-icons/all-files/fa/FaRobot";
import { FaThumbsUp } from "@react-icons/all-files/fa/FaThumbsUp";
import { FaThumbsDown } from "@react-icons/all-files/fa/FaThumbsDown";
import { FaFlag } from "@react-icons/all-files/fa/FaFlag";
import { FaSmile } from "@react-icons/all-files/fa/FaSmile";
import { FaCloudUploadAlt } from "@react-icons/all-files/fa/FaCloudUploadAlt";
import { FaTachometerAlt } from "@react-icons/all-files/fa/FaTachometerAlt";
import DocumentationViewer from "./DocumentationViewer";
import "./AdminDashboard.css";

import { getApiBase } from "../lib/apiBase";
const API_BASE = getApiBase();

export default function AdminDashboard() {
  const navigate = useNavigate();
  const token = localStorage.getItem("token");

  // Tab state
  const [activeTab, setActiveTab] = useState("overview");

  // Overview State
  const [overviewData, setOverviewData] = useState(null);
  const [overviewLoading, setOverviewLoading] = useState(false);

  // Course state
  const [course, setCourse] = useState({
    course_code: "", course_name: "", credits: "", prerequisites: "", offered: "",
  });
  const [message, setMessage] = useState("");
  const [courses, setCourses] = useState([]);
  const [editingCourse, setEditingCourse] = useState(null);

  // Support Tickets State
  const [tickets, setTickets] = useState([]);
  const [ticketStats, setTicketStats] = useState({ total: 0, open: 0, in_progress: 0, resolved: 0 });
  const [ticketFilter, setTicketFilter] = useState("all");
  const [selectedTicket, setSelectedTicket] = useState(null);
  const [ticketLoading, setTicketLoading] = useState(false);

  // Users State
  const [users, setUsers] = useState([]);
  const [userStats, setUserStats] = useState({ total: 0, students: 0, admins: 0, new_this_week: 0, morgan_connected: 0 });
  const [userSearch, setUserSearch] = useState("");
  const [userRoleFilter, setUserRoleFilter] = useState("all");
  const [usersLoading, setUsersLoading] = useState(false);

  // System Health State
  const [healthStatus, setHealthStatus] = useState(null);
  const [healthLoading, setHealthLoading] = useState(false);

  // Knowledge Base State
  const [kbFiles, setKbFiles] = useState([]);
  const [selectedKbFile, setSelectedKbFile] = useState(null);
  const [kbContent, setKbContent] = useState("");
  const [kbLoading, setKbLoading] = useState(false);
  const [ingesting, setIngesting] = useState(false);
  const [kbSearch, setKbSearch] = useState("");
  const [kbSearchResults, setKbSearchResults] = useState([]);
  const [isListening, setIsListening] = useState(false);
  const [voiceSupported, setVoiceSupported] = useState(false);
  const [highlightTerm, setHighlightTerm] = useState("");

  // Format doc ID into a clean readable title
  // "academic_11_course_prerequisites" -> "Course Prerequisites"
  // "financial_aid_fafsa_requirements" -> "FAFSA Requirements"
  const formatDocName = (id) => {
    if (!id) return "";
    // Remove category prefix and numeric prefixes
    let clean = id
      .replace(/^(academic|career|financial|general)_/, "")
      .replace(/^\d+_/, "");
    // Convert underscores to spaces and title case
    return clean
      .split("_")
      .map(w => w.charAt(0).toUpperCase() + w.slice(1))
      .join(" ");
  };

  // Get category badge color
  const getCategoryColor = (id) => {
    if (id.startsWith("academic")) return { bg: "#e8f5e9", color: "#2e7d32", label: "Academic" };
    if (id.startsWith("career")) return { bg: "#e3f2fd", color: "#1565c0", label: "Career" };
    if (id.startsWith("financial")) return { bg: "#fff3e0", color: "#e65100", label: "Financial" };
    return { bg: "#f3e5f5", color: "#6a1b9a", label: "General" };
  };

  // Research Agent State
  const [researchStats, setResearchStats] = useState({ total_failed: 0, pending_suggestions: 0, approved: 0, pushed: 0 });
  const [suggestions, setSuggestions] = useState([]);
  const [researchRunning, setResearchRunning] = useState(false);
  const [suggestionFilter, setSuggestionFilter] = useState("pending");
  const [failedQueries, setFailedQueries] = useState([]);
  const [showFailedQueries, setShowFailedQueries] = useState(false);
  const [expandedSuggestion, setExpandedSuggestion] = useState(null);

  // Find & Replace State
  const [findText, setFindText] = useState("");
  const [replaceText, setReplaceText] = useState("");
  const [showFindReplace, setShowFindReplace] = useState(false);
  const [matchCount, setMatchCount] = useState(0);
  const [currentMatchIndex, setCurrentMatchIndex] = useState(0);
  const [matchedFiles, setMatchedFiles] = useState([]); // Files with matches
  const [showMatchedFiles, setShowMatchedFiles] = useState(false);
  const textareaRef = useRef(null);
  const highlightRef = useRef(null);

  // Analytics State
  const [analytics, setAnalytics] = useState(null);
  const [analyticsLoading, setAnalyticsLoading] = useState(false);

  // Cloud KB State
  const [cloudKbDocs, setCloudKbDocs] = useState([]);
  const [cloudKbLoading, setCloudKbLoading] = useState(false);
  const [cloudKbSelected, setCloudKbSelected] = useState(null);
  const [cloudKbContent, setCloudKbContent] = useState("");
  const [cloudKbEditing, setCloudKbEditing] = useState(false);
  const [cloudKbEditContent, setCloudKbEditContent] = useState("");
  const [cloudKbSyncing, setCloudKbSyncing] = useState(false);
  const [cloudKbUploading, setCloudKbUploading] = useState(false);
  const [cloudKbSearchResults, setCloudKbSearchResults] = useState(null); // null = no search, [] = no results
  const [cloudKbSearching, setCloudKbSearching] = useState(false);
  const [cloudKbStats, setCloudKbStats] = useState({ total_documents: 0, total_size: 0, last_modified: "" });
  const [cloudKbDragActive, setCloudKbDragActive] = useState(false);
  const cloudKbFileRef = useRef(null);
  const cloudKbSearchTimer = useRef(null);

  // Cache Management State
  const [cacheStats, setCacheStats] = useState(null);
  const [cacheLoading, setCacheLoading] = useState(false);
  const [cacheClearing, setCacheClearing] = useState(false);

  // Documentation Viewer State
  const [showDocViewer, setShowDocViewer] = useState(false);
  const [docViewerMode, setDocViewerMode] = useState("docs"); // "docs" or "roadmap"

  // Feedback State
  const [feedbackData, setFeedbackData] = useState([]);
  const [feedbackStats, setFeedbackStats] = useState({ total: 0, helpful: 0, not_helpful: 0, reports: 0, satisfaction_rate: 0 });
  const [feedbackFilter, setFeedbackFilter] = useState("all");
  const [feedbackLoading, setFeedbackLoading] = useState(false);
  const [selectedFeedback, setSelectedFeedback] = useState(null);

  // ===========================================
  // DATA LOADING FUNCTIONS
  // ===========================================

  const loadCourses = async () => {
    try {
      const res = await fetch(`${API_BASE}/api/curriculum`);
      if (res.ok) {
        const data = await res.json();
        // API returns {degree_info, courses, elective_requirements} - extract just courses array
        setCourses(data.courses || data || []);
      }
    } catch (err) { console.error("Failed to load courses:", err); }
  };

  const loadTickets = async (status = null) => {
    setTicketLoading(true);
    try {
      const url = status && status !== "all" ? `${API_BASE}/api/tickets?status=${status}` : `${API_BASE}/api/tickets`;
      const res = await fetch(url, { headers: { Authorization: `Bearer ${token}` } });
      if (res.ok) {
        const data = await res.json();
        setTickets(data.tickets || []);
      }
    } catch (err) { console.error("Failed to load tickets:", err); }
    finally { setTicketLoading(false); }
  };

  const loadTicketStats = async () => {
    try {
      const res = await fetch(`${API_BASE}/api/tickets/stats/summary`, { headers: { Authorization: `Bearer ${token}` } });
      if (res.ok) setTicketStats(await res.json());
    } catch (err) { console.error("Failed to load ticket stats:", err); }
  };

  const loadUsers = async () => {
    setUsersLoading(true);
    try {
      let url = `${API_BASE}/api/admin/users`;
      const params = new URLSearchParams();
      if (userSearch) params.append("search", userSearch);
      if (userRoleFilter !== "all") params.append("role", userRoleFilter);
      if (params.toString()) url += `?${params.toString()}`;

      const res = await fetch(url, { headers: { Authorization: `Bearer ${token}` } });
      if (res.ok) {
        const data = await res.json();
        setUsers(data.users || []);
      }
    } catch (err) { console.error("Failed to load users:", err); }
    finally { setUsersLoading(false); }
  };

  const loadUserStats = async () => {
    try {
      const res = await fetch(`${API_BASE}/api/admin/users/stats`, { headers: { Authorization: `Bearer ${token}` } });
      if (res.ok) setUserStats(await res.json());
    } catch (err) { console.error("Failed to load user stats:", err); }
  };

  const loadHealth = async () => {
    setHealthLoading(true);
    try {
      const res = await fetch(`${API_BASE}/api/admin/health`, { headers: { Authorization: `Bearer ${token}` } });
      if (res.ok) setHealthStatus(await res.json());
    } catch (err) { console.error("Failed to load health:", err); }
    finally { setHealthLoading(false); }
  };

  const loadKbFiles = async () => {
    try {
      const res = await fetch(`${API_BASE}/api/admin/knowledge-base/files`, { headers: { Authorization: `Bearer ${token}` } });
      if (res.ok) {
        const data = await res.json();
        setKbFiles(data.files || []);
      }
    } catch (err) { console.error("Failed to load KB files:", err); }
  };

  const loadKbFileContent = async (filename) => {
    setKbLoading(true);
    try {
      const res = await fetch(`${API_BASE}/api/admin/knowledge-base/${filename}`, { headers: { Authorization: `Bearer ${token}` } });
      if (res.ok) {
        const data = await res.json();
        setKbContent(JSON.stringify(data.content, null, 2));
      }
    } catch (err) { console.error("Failed to load KB file:", err); }
    finally { setKbLoading(false); }
  };

  const loadAnalytics = async () => {
    setAnalyticsLoading(true);
    try {
      const res = await fetch(`${API_BASE}/api/admin/analytics`, { headers: { Authorization: `Bearer ${token}` } });
      if (res.ok) setAnalytics(await res.json());
    } catch (err) { console.error("Failed to load analytics:", err); }
    finally { setAnalyticsLoading(false); }
  };

  // Overview data loader
  const loadOverview = async () => {
    setOverviewLoading(true);
    try {
      const [usersRes, healthRes, cacheRes, kbStatsRes] = await Promise.allSettled([
        fetch(`${API_BASE}/api/admin/users/stats`, { headers: { Authorization: `Bearer ${token}` } }),
        fetch(`${API_BASE}/api/admin/health`, { headers: { Authorization: `Bearer ${token}` } }),
        fetch(`${API_BASE}/api/admin/cache/stats`, { headers: { Authorization: `Bearer ${token}` } }),
        fetch(`${API_BASE}/api/admin/cloud-kb/stats`, { headers: { Authorization: `Bearer ${token}` } }),
      ]);
      const overview = {};
      if (usersRes.status === "fulfilled" && usersRes.value.ok) overview.users = await usersRes.value.json();
      if (healthRes.status === "fulfilled" && healthRes.value.ok) overview.health = await healthRes.value.json();
      if (cacheRes.status === "fulfilled" && cacheRes.value.ok) overview.cache = await cacheRes.value.json();
      if (kbStatsRes.status === "fulfilled" && kbStatsRes.value.ok) overview.kbStats = await kbStatsRes.value.json();
      setOverviewData(overview);
    } catch (err) { console.error("Failed to load overview:", err); }
    finally { setOverviewLoading(false); }
  };

  // Cloud KB Functions
  const loadCloudKbDocs = async () => {
    setCloudKbLoading(true);
    try {
      const res = await fetch(`${API_BASE}/api/admin/cloud-kb/documents`, { headers: { Authorization: `Bearer ${token}` } });
      if (res.ok) {
        const data = await res.json();
        setCloudKbDocs(data.documents || []);
      }
    } catch (err) { console.error("Failed to load cloud KB:", err); }
    finally { setCloudKbLoading(false); }
  };

  const loadCloudKbStats = async () => {
    try {
      const res = await fetch(`${API_BASE}/api/admin/cloud-kb/stats`, { headers: { Authorization: `Bearer ${token}` } });
      if (res.ok) setCloudKbStats(await res.json());
    } catch (err) { console.error("Failed to load cloud KB stats:", err); }
  };

  // Cache Management Functions
  const loadCacheStats = async () => {
    setCacheLoading(true);
    try {
      const res = await fetch(`${API_BASE}/api/admin/cache/stats`, { headers: { Authorization: `Bearer ${token}` } });
      if (res.ok) setCacheStats(await res.json());
    } catch (err) { console.error("Failed to load cache stats:", err); }
    finally { setCacheLoading(false); }
  };

  const handleClearCache = async () => {
    setCacheClearing(true);
    try {
      const res = await fetch(`${API_BASE}/api/admin/cache/clear`, {
        method: "POST",
        headers: { Authorization: `Bearer ${token}` }
      });
      if (res.ok) {
        const data = await res.json();
        toast.success("Cache cleared successfully", { description: `Cleared: L1=${data.message?.l1_cleared || 0}, L2=${data.message?.l2_cleared || 0}, Semantic=${data.message?.semantic_cleared || 0}` });
        loadCacheStats();
      } else {
        toast.error("Failed to clear cache");
      }
    } catch (err) { toast.error("Cache clear error: " + err.message); }
    finally { setCacheClearing(false); }
  };

  const loadCloudKbContent = async (doc) => {
    setCloudKbSelected(doc);
    setCloudKbEditing(false);
    setCloudKbContent("Loading...");
    try {
      const res = await fetch(`${API_BASE}/api/admin/cloud-kb/documents/${doc.id}/content`, {
        headers: { Authorization: `Bearer ${token}` }
      });
      if (res.ok) {
        const data = await res.json();
        setCloudKbContent(data.content || "");
      } else {
        setCloudKbContent("Failed to load content.");
      }
    } catch (err) { setCloudKbContent("Error loading content."); }
  };

  const searchCloudKb = async (query) => {
    if (!query || query.length < 2) {
      setCloudKbSearchResults(null);
      return;
    }
    setCloudKbSearching(true);
    try {
      const res = await fetch(`${API_BASE}/api/admin/cloud-kb/search?q=${encodeURIComponent(query)}`, {
        headers: { Authorization: `Bearer ${token}` }
      });
      if (res.ok) {
        const data = await res.json();
        setCloudKbSearchResults(data.results || []);
      }
    } catch (err) { console.error("Cloud KB search failed:", err); }
    finally { setCloudKbSearching(false); }
  };

  const handleCloudKbSearch = (value) => {
    setKbSearch(value);
    // Debounce both highlighting and API search to prevent cursor jumping
    if (cloudKbSearchTimer.current) clearTimeout(cloudKbSearchTimer.current);
    cloudKbSearchTimer.current = setTimeout(() => {
      if (value.length >= 2) {
        setFindText(value);
      } else {
        setFindText("");
      }
      searchCloudKb(value);
    }, 300);
  };

  const handleCloudKbUpload = async (e) => {
    const files = e.target.files || e.dataTransfer?.files;
    if (!files || files.length === 0) return;
    setCloudKbUploading(true);
    let successCount = 0;
    let failCount = 0;
    for (const file of files) {
      const formData = new FormData();
      formData.append("file", file);
      try {
        const res = await fetch(`${API_BASE}/api/admin/cloud-kb/upload`, {
          method: "POST",
          headers: { Authorization: `Bearer ${token}` },
          body: formData
        });
        if (res.ok) {
          successCount++;
        } else {
          failCount++;
          const data = await res.json();
          toast.error(`Failed: ${file.name}`, { description: data.detail || "Unknown error" });
        }
      } catch (err) {
        failCount++;
        toast.error(`Upload error: ${file.name}`);
      }
    }
    if (successCount > 0) {
      toast.success(`Uploaded ${successCount} file${successCount > 1 ? "s" : ""}`, {
        description: "Cache auto-cleared. Chatbot will use fresh data."
      });
      loadCloudKbDocs();
      loadCloudKbStats();
    }
    setCloudKbUploading(false);
    if (cloudKbFileRef.current) cloudKbFileRef.current.value = "";
  };

  // Drag and drop handlers
  const handleDragOver = useCallback((e) => { e.preventDefault(); e.stopPropagation(); setCloudKbDragActive(true); }, []);
  const handleDragLeave = useCallback((e) => { e.preventDefault(); e.stopPropagation(); setCloudKbDragActive(false); }, []);
  const handleDrop = useCallback((e) => {
    e.preventDefault();
    e.stopPropagation();
    setCloudKbDragActive(false);
    handleCloudKbUpload(e);
  }, [token]);

  const handleCloudKbSave = async () => {
    if (!cloudKbSelected) return;
    try {
      const res = await fetch(`${API_BASE}/api/admin/cloud-kb/documents/${cloudKbSelected.id}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
        body: JSON.stringify({ content: cloudKbEditContent })
      });
      if (res.ok) {
        toast.success("Document updated", { description: `${formatDocName(cloudKbSelected.filename)} saved. Instantly indexed.` });
        setCloudKbContent(cloudKbEditContent);
        setCloudKbEditing(false);
        loadCloudKbDocs();
        loadCloudKbStats();
      } else {
        const data = await res.json();
        toast.error("Save failed", { description: data.detail || "Unknown error" });
      }
    } catch (err) { toast.error("Save error: " + err.message); }
  };

  const handleCloudKbDelete = async (doc) => {
    if (!window.confirm(`Delete "${doc.filename}"? This cannot be undone.`)) return;
    try {
      const res = await fetch(`${API_BASE}/api/admin/cloud-kb/documents/${doc.id}?uri=${encodeURIComponent(doc.uri)}`, {
        method: "DELETE",
        headers: { Authorization: `Bearer ${token}` }
      });
      if (res.ok) {
        toast.success("Deleted", { description: `${doc.filename} removed. Cache auto-cleared.` });
        if (cloudKbSelected?.id === doc.id) {
          setCloudKbSelected(null);
          setCloudKbContent("");
        }
        loadCloudKbDocs();
        loadCloudKbStats();
      } else {
        const data = await res.json();
        toast.error("Delete failed", { description: data.detail || "Unknown error" });
      }
    } catch (err) { toast.error("Delete error: " + err.message); }
  };

  const handleCloudKbSync = async () => {
    if (!window.confirm("Re-sync all documents from GCS into the datastore?")) return;
    setCloudKbSyncing(true);
    toast.loading("Syncing datastore...", { id: "sync" });
    try {
      const res = await fetch(`${API_BASE}/api/admin/cloud-kb/sync`, {
        method: "POST",
        headers: { Authorization: `Bearer ${token}` }
      });
      const data = await res.json();
      if (res.ok) {
        toast.success("Sync started", { id: "sync", description: "Documents will be re-indexed shortly. Cache cleared." });
        loadCloudKbDocs();
        loadCloudKbStats();
      } else {
        toast.error("Sync failed", { id: "sync", description: data.detail || "Unknown error" });
      }
    } catch (err) { toast.error("Sync error", { id: "sync", description: err.message }); }
    finally { setCloudKbSyncing(false); }
  };

  const loadFeedbackStats = async () => {
    try {
      const res = await fetch(`${API_BASE}/api/feedback/stats`, { headers: { Authorization: `Bearer ${token}` } });
      if (res.ok) {
        const data = await res.json();
        setFeedbackStats(data);
        setFeedbackData(data.recent_reports || []);
      }
    } catch (err) { console.error("Failed to load feedback stats:", err); }
  };

  const loadAllFeedback = async (filterType = null) => {
    setFeedbackLoading(true);
    try {
      let url = `${API_BASE}/api/feedback/all`;
      if (filterType && filterType !== "all") {
        url += `?type=${filterType}`;
      }
      const res = await fetch(url, { headers: { Authorization: `Bearer ${token}` } });
      if (res.ok) {
        const data = await res.json();
        setFeedbackData(data.feedback || []);
      }
    } catch (err) { console.error("Failed to load feedback:", err); }
    finally { setFeedbackLoading(false); }
  };

  const searchKnowledgeBase = async (searchTerm) => {
    if (!searchTerm || searchTerm.length < 2) {
      setKbSearchResults([]);
      setHighlightTerm("");
      setMatchedFiles([]);
      setShowMatchedFiles(false);
      return;
    }
    setHighlightTerm(searchTerm);
    try {
      const res = await fetch(`${API_BASE}/api/admin/knowledge-base/search?q=${encodeURIComponent(searchTerm)}`, {
        headers: { Authorization: `Bearer ${token}` }
      });
      if (res.ok) {
        const data = await res.json();
        const results = data.results || [];
        setKbSearchResults(results);

        // Extract unique files with match counts
        const fileMap = {};
        results.forEach(r => {
          if (!fileMap[r.filename]) {
            fileMap[r.filename] = { filename: r.filename, matchCount: 0 };
          }
          fileMap[r.filename].matchCount++;
        });
        const files = Object.values(fileMap).sort((a, b) => b.matchCount - a.matchCount);
        setMatchedFiles(files);
        setShowMatchedFiles(files.length > 0);
      }
    } catch (err) { console.error("Failed to search KB:", err); }
  };

  // Voice Search Functions
  const startVoiceSearch = () => {
    const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!SpeechRecognition) {
      toast.error("Voice search is not supported in your browser. Please use Chrome or Edge.");
      return;
    }

    const recognition = new SpeechRecognition();
    recognition.lang = 'en-US';
    recognition.interimResults = false;
    recognition.maxAlternatives = 1;

    recognition.onstart = () => {
      setIsListening(true);
    };

    recognition.onresult = (event) => {
      const transcript = event.results[0][0].transcript;
      console.log("Voice input:", transcript);

      // Extract search keywords from natural language
      const keywords = extractKeywords(transcript);
      if (keywords) {
        setKbSearch(keywords);
        searchKnowledgeBase(keywords);
      }
    };

    recognition.onerror = (event) => {
      console.error("Voice recognition error:", event.error);
      setIsListening(false);
      if (event.error === 'not-allowed') {
        toast.error("Microphone access denied. Please allow microphone access.");
      }
    };

    recognition.onend = () => {
      setIsListening(false);
    };

    recognition.start();
  };

  const extractKeywords = (transcript) => {
    // Remove common phrases and extract the key search terms
    const lowerTranscript = transcript.toLowerCase();

    // Common patterns to remove
    const patternsToRemove = [
      /^(can you |please |i want to |i need to |help me )/i,
      /(search for |find |look for |look up |search |find me )/i,
      /('s | the | a | an | to | for | of | in | on | with | and | or )/gi,
      /(phone number|email|address|contact|information|info|details)/gi,
      /(so i can |so we can |that i can |change it|edit it|update it)/gi,
    ];

    let cleaned = lowerTranscript;

    // Extract names or specific terms (capitalized words or quoted text)
    const nameMatch = transcript.match(/(?:for |find |search )([A-Z][a-z]+ [A-Z][a-z]+)/);
    if (nameMatch) {
      return nameMatch[1];
    }

    // Clean up common phrases
    patternsToRemove.forEach(pattern => {
      cleaned = cleaned.replace(pattern, ' ');
    });

    // Clean up and return
    cleaned = cleaned.replace(/\s+/g, ' ').trim();

    // If we got something meaningful, return it
    if (cleaned.length >= 2) {
      return cleaned;
    }

    // Fallback: just use key words from original
    const words = transcript.split(' ').filter(w => w.length > 3);
    return words.slice(0, 3).join(' ');
  };

  const stopVoiceSearch = () => {
    setIsListening(false);
  };

  // Check voice support on mount
  useEffect(() => {
    const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
    setVoiceSupported(!!SpeechRecognition);
  }, []);

  // Research Agent Functions
  const loadResearchStats = async () => {
    try {
      const res = await fetch(`${API_BASE}/api/admin/research/stats`, { headers: { Authorization: `Bearer ${token}` } });
      if (res.ok) setResearchStats(await res.json());
    } catch (err) { console.error("Failed to load research stats:", err); }
  };

  const loadSuggestions = async () => {
    try {
      const res = await fetch(`${API_BASE}/api/admin/research/suggestions?status=${suggestionFilter}`, { headers: { Authorization: `Bearer ${token}` } });
      if (res.ok) { const data = await res.json(); setSuggestions(data.suggestions || []); }
    } catch (err) { console.error("Failed to load suggestions:", err); }
  };

  const loadFailedQueries = async () => {
    try {
      const res = await fetch(`${API_BASE}/api/admin/research/failed-queries`, { headers: { Authorization: `Bearer ${token}` } });
      if (res.ok) { const data = await res.json(); setFailedQueries(data.queries || []); }
    } catch (err) { console.error("Failed to load failed queries:", err); }
  };

  const handleRunResearch = async () => {
    setResearchRunning(true);
    try {
      const res = await fetch(`${API_BASE}/api/admin/research/run`, { method: "POST", headers: { Authorization: `Bearer ${token}` } });
      if (res.ok) {
        const data = await res.json();
        toast.success("Research complete", { description: `Clustered ${data.clustered} queries, researched ${data.researched} topics` });
        loadResearchStats(); loadSuggestions();
      } else { toast.error("Research failed"); }
    } catch (err) { toast.error("Research error: " + err.message); }
    finally { setResearchRunning(false); }
  };

  const handleSuggestionAction = async (id, action, extra = {}) => {
    try {
      const res = await fetch(`${API_BASE}/api/admin/research/suggestions/${id}`, {
        method: "PUT", headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
        body: JSON.stringify({ action, ...extra })
      });
      if (res.ok) { toast.success(`Suggestion ${action}ed`); loadSuggestions(); loadResearchStats(); }
    } catch (err) { toast.error("Action failed: " + err.message); }
  };

  const handlePushSuggestion = async (id) => {
    if (!window.confirm("Push this suggestion to the live knowledge base?")) return;
    try {
      const res = await fetch(`${API_BASE}/api/admin/research/suggestions/${id}/push`, {
        method: "POST", headers: { Authorization: `Bearer ${token}` }
      });
      if (res.ok) { toast.success("Pushed to KB!"); loadSuggestions(); loadResearchStats(); }
      else { const data = await res.json(); toast.error(data.detail || "Push failed"); }
    } catch (err) { toast.error("Push error: " + err.message); }
  };

  useEffect(() => {
    loadCourses();
    loadTickets();
    loadTicketStats();
    loadUsers();
    loadUserStats();
  }, []);

  useEffect(() => {
    if (activeTab === "overview") { loadOverview(); loadAnalytics(); }
    if (activeTab === "system") { loadHealth(); loadCacheStats(); }
    if (activeTab === "knowledge") loadKbFiles();
    if (activeTab === "feedback") loadFeedbackStats();
    if (activeTab === "research") { loadResearchStats(); loadSuggestions(); }
    if (activeTab === "cloud-kb") { loadCloudKbDocs(); loadCloudKbStats(); }
    // Preload cloud KB docs in background on first render so Datastore tab is instant
    if (activeTab !== "cloud-kb" && cloudKbDocs.length === 0) {
      fetch(`${API_BASE}/api/admin/cloud-kb/documents`, { headers: { Authorization: `Bearer ${token}` } }).catch(() => {});
    }
  }, [activeTab]);

  useEffect(() => {
    loadUsers();
  }, [userSearch, userRoleFilter]);

  // ===========================================
  // ACTION HANDLERS
  // ===========================================

  const updateTicketStatus = async (ticketId, newStatus) => {
    try {
      const res = await fetch(`${API_BASE}/api/tickets/${ticketId}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
        body: JSON.stringify({ status: newStatus })
      });
      if (res.ok) {
        loadTickets(ticketFilter === "all" ? null : ticketFilter);
        loadTicketStats();
        if (selectedTicket?.id === ticketId) setSelectedTicket(prev => ({ ...prev, status: newStatus }));
      }
    } catch (err) { console.error("Failed to update ticket:", err); }
  };

  const handleAddCourse = async (e) => {
    e.preventDefault();
    setMessage("Adding course...");
    const payload = {
      course_code: course.course_code,
      course_name: course.course_name,
      credits: Number(course.credits),
      prerequisites: course.prerequisites.split(",").map(s => s.trim()).filter(Boolean),
      offered: course.offered.split(",").map(s => s.trim()).filter(Boolean),
    };
    try {
      const res = await fetch(`${API_BASE}/api/curriculum/add`, {
        method: "POST",
        headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
        body: JSON.stringify(payload),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || res.statusText);
      setMessage(`Added ${data.course.course_code}`);
      setCourse({ course_code: "", course_name: "", credits: "", prerequisites: "", offered: "" });
      loadCourses();
    } catch (err) { setMessage(`Error: ${err.message}`); }
  };

  const handleEditCourse = async (e) => {
    e.preventDefault();
    setMessage("Updating course...");
    const payload = {
      course_code: editingCourse.course_code,
      course_name: editingCourse.course_name,
      credits: Number(editingCourse.credits),
      prerequisites: typeof editingCourse.prerequisites === 'string'
        ? editingCourse.prerequisites.split(",").map(s => s.trim()).filter(Boolean)
        : editingCourse.prerequisites || [],
      offered: typeof editingCourse.offered === 'string'
        ? editingCourse.offered.split(",").map(s => s.trim()).filter(Boolean)
        : editingCourse.offered || [],
    };
    try {
      const res = await fetch(`${API_BASE}/api/curriculum/${encodeURIComponent(editingCourse.course_code)}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
        body: JSON.stringify(payload),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || res.statusText);
      setMessage(`Updated ${editingCourse.course_code}`);
      setEditingCourse(null);
      loadCourses();
    } catch (err) { setMessage(`Error: ${err.message}`); }
  };

  const handleDeleteCourse = async (code) => {
    if (!window.confirm(`Delete ${code}?`)) return;
    try {
      const res = await fetch(`${API_BASE}/api/curriculum/delete/${encodeURIComponent(code)}`, {
        method: "DELETE",
        headers: { Authorization: `Bearer ${token}` }
      });
      if (res.ok) loadCourses();
    } catch (err) { console.error(err); }
  };

  const handleReingest = async () => {
    setMessage("Re-ingesting data...");
    try {
      const res = await fetch(`${API_BASE}/api/admin/knowledge-base/ingest`, {
        method: "POST",
        headers: { Authorization: `Bearer ${token}` }
      });
      const data = await res.json();
      if (res.ok) setMessage("Ingestion completed!");
      else throw new Error(data.detail);
    } catch (err) { setMessage(`Error: ${err.message}`); }
  };

  const handleClearIndex = async () => {
    if (!window.confirm("Clear all vectors from index? This cannot be undone.")) return;
    setMessage("Clearing index...");
    try {
      const res = await fetch(`${API_BASE}/clear-index`, {
        method: "DELETE",
        headers: { Authorization: `Bearer ${token}` }
      });
      const data = await res.json();
      if (res.ok) setMessage("Index cleared!");
      else throw new Error(data.detail);
    } catch (err) { setMessage(`Error: ${err.message}`); }
  };

  const handleUpdateUserRole = async (userId, newRole) => {
    if (!window.confirm(`Change user role to ${newRole}?`)) return;
    try {
      const res = await fetch(`${API_BASE}/api/admin/users/${userId}/role?new_role=${newRole}`, {
        method: "PUT",
        headers: { Authorization: `Bearer ${token}` }
      });
      if (res.ok) {
        loadUsers();
        loadUserStats();
      }
    } catch (err) { console.error(err); }
  };

  const handleSaveKbFile = async () => {
    if (!selectedKbFile) return;
    setKbLoading(true);
    try {
      const content = JSON.parse(kbContent);
      const res = await fetch(`${API_BASE}/api/admin/knowledge-base/${selectedKbFile}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
        body: JSON.stringify(content)
      });
      if (res.ok) {
        toast.success("File saved successfully!");
        loadKbFiles();
      } else {
        const data = await res.json();
        throw new Error(data.detail);
      }
    } catch (err) {
      toast.error(`Error: ${err.message}`);
    } finally { setKbLoading(false); }
  };

  const handleTriggerIngestion = async () => {
    setIngesting(true);
    try {
      const res = await fetch(`${API_BASE}/api/admin/knowledge-base/ingest`, {
        method: "POST",
        headers: { Authorization: `Bearer ${token}` }
      });
      const data = await res.json();
      if (res.ok) toast.success("Ingestion completed!");
      else throw new Error(data.detail);
    } catch (err) { toast.error(`Ingestion failed: ${err.message}`); }
    finally { setIngesting(false); }
  };

  // ===========================================
  // FIND & REPLACE FUNCTIONS
  // ===========================================

  // Count matches when findText changes (word-boundary matching to match backend)
  useEffect(() => {
    const content = cloudKbEditing ? cloudKbEditContent : cloudKbContent;
    if (findText && content && content !== "Loading...") {
      const escaped = findText.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
      const regex = new RegExp('\\b' + escaped + '\\b', 'gi');
      const matches = content.match(regex);
      setMatchCount(matches ? matches.length : 0);
      setCurrentMatchIndex(0);
    } else {
      setMatchCount(0);
      setCurrentMatchIndex(0);
    }
  }, [findText, cloudKbContent, cloudKbEditContent, cloudKbEditing]);

  // Auto-scroll to first match when opening Find & Replace from search
  // Only auto-scroll to first match when Find & Replace panel is first opened,
  // NOT on every keystroke in the search box
  useEffect(() => {
    const content = cloudKbEditing ? cloudKbEditContent : cloudKbContent;
    if (showFindReplace && findText && content && textareaRef.current) {
      setCurrentMatchIndex(1);
      // Don't auto-scroll - let user navigate with next/prev buttons
    }
  }, [showFindReplace]);

  // Get the active content for find/replace (cloud KB editing or viewing)
  const getActiveContent = () => cloudKbEditing ? cloudKbEditContent : cloudKbContent;

  // Scroll to a specific <mark> element by index inside the viewer/editor
  const scrollToMatch = (matchIdx) => {
    const container = textareaRef.current;
    if (!container) return;

    // For view mode: find <mark> elements in the rendered HTML
    if (!cloudKbEditing) {
      const marks = container.querySelectorAll("mark.highlight-match");
      if (marks.length > 0) {
        const idx = Math.max(0, Math.min(matchIdx, marks.length - 1));
        marks.forEach(m => m.classList.remove("active-match"));
        marks[idx].classList.add("active-match");
        // Scroll the container, not the viewport
        const containerRect = container.getBoundingClientRect();
        const markRect = marks[idx].getBoundingClientRect();
        const scrollTarget = container.scrollTop + (markRect.top - containerRect.top) - container.clientHeight / 3;
        container.scrollTo({ top: Math.max(0, scrollTarget), behavior: "smooth" });
      }
      return;
    }

    // For edit mode: scroll textarea to match without stealing focus
    const content = cloudKbEditContent;
    const text = content.toLowerCase();
    const searchTerm = findText.toLowerCase();
    let pos = -1;
    for (let i = 0; i <= matchIdx; i++) {
      pos = text.indexOf(searchTerm, pos + 1);
      if (pos === -1) break;
    }
    if (pos !== -1) {
      // Scroll textarea to the match position without focusing it
      const textBefore = content.substring(0, pos);
      const lines = textBefore.split("\n").length - 1;
      const lineH = parseFloat(getComputedStyle(container).lineHeight) || 20;
      container.scrollTop = Math.max(0, lines * lineH - container.clientHeight / 3);
    }
  };

  const findNextMatch = () => {
    if (!findText || matchCount === 0) return;
    const next = currentMatchIndex % matchCount; // 0-indexed
    setCurrentMatchIndex(next + 1); // 1-indexed display
    scrollToMatch(next);
  };

  const findPrevMatch = () => {
    if (!findText || matchCount === 0) return;
    const prev = currentMatchIndex - 2 < 0 ? matchCount - 1 : currentMatchIndex - 2;
    setCurrentMatchIndex(prev + 1);
    scrollToMatch(prev);
  };

  const replaceCurrentMatch = () => {
    if (!findText || !textareaRef.current || !cloudKbEditing) return;

    const textarea = textareaRef.current;
    const selStart = textarea.selectionStart;
    const selEnd = textarea.selectionEnd;
    const selectedText = cloudKbEditContent.substring(selStart, selEnd);

    if (selectedText.toLowerCase() === findText.toLowerCase()) {
      const newContent = cloudKbEditContent.substring(0, selStart) + replaceText + cloudKbEditContent.substring(selEnd);
      setCloudKbEditContent(newContent);
      setTimeout(() => {
        textarea.focus();
        textarea.setSelectionRange(selStart + replaceText.length, selStart + replaceText.length);
        findNextMatch();
      }, 10);
    } else {
      findNextMatch();
    }
  };

  const replaceAllMatches = () => {
    if (!findText || !cloudKbEditing) return;
    const escaped = findText.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    const regex = new RegExp('\\b' + escaped + '\\b', 'gi');
    const newContent = cloudKbEditContent.replace(regex, replaceText);
    const replacedCount = matchCount;
    setCloudKbEditContent(newContent);
    toast.success(`Replaced ${replacedCount} occurrence(s)`);
  };

  // ===========================================
  // HELPER FUNCTIONS
  // ===========================================

  const getCategoryIcon = (category) => {
    switch (category) {
      case "bug": return <FaBug size={14} />;
      case "feature": return <FaLightbulb size={14} />;
      case "question": return <FaQuestionCircle size={14} />;
      default: return <FaTicketAlt size={14} />;
    }
  };

  const getStatusClass = (status) => {
    switch (status) {
      case "open": return "status-open";
      case "in_progress": return "status-progress";
      case "resolved": return "status-resolved";
      case "closed": return "status-closed";
      default: return "";
    }
  };

  const formatDate = (dateStr) => {
    if (!dateStr) return "N/A";
    return new Date(dateStr).toLocaleDateString("en-US", {
      month: "short", day: "numeric", year: "numeric"
    });
  };

  const formatDateTime = (dateStr) => {
    if (!dateStr) return "N/A";
    return new Date(dateStr).toLocaleDateString("en-US", {
      month: "short", day: "numeric", year: "numeric", hour: "2-digit", minute: "2-digit"
    });
  };

  // Generate highlighted HTML content for preview
  const getHighlightedContent = () => {
    const content = getActiveContent();
    if (!findText || !content) return content;

    const escapedSearch = findText.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    const regex = new RegExp(`(\\b${escapedSearch}\\b)`, 'gi');

    const escaped = content
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;');

    return escaped.replace(regex, '<mark class="highlight-match">$1</mark>');
  };

  // Sync scroll between highlight preview and textarea
  const handleTextareaScroll = () => {
    if (highlightRef.current && textareaRef.current) {
      highlightRef.current.scrollTop = textareaRef.current.scrollTop;
      highlightRef.current.scrollLeft = textareaRef.current.scrollLeft;
    }
  };

  const formatBytes = (bytes) => {
    if (bytes < 1024) return bytes + " B";
    if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + " KB";
    return (bytes / (1024 * 1024)).toFixed(1) + " MB";
  };

  // ===========================================
  // RENDER
  // ===========================================

  return (
    <div className="card page-container AdminDashboard">
      <header className="page-header">
        <div className="header-left">
          <FaCog className="page-icon" />
          <h1 className="page-title">Admin Dashboard</h1>
        </div>
        <button className="back-home-btn" onClick={() => navigate("/chat")}>
          <span>Back to Home</span>
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
            <path d="M5 12h14M12 5l7 7-7 7"/>
          </svg>
        </button>
      </header>

      {/* Tab Navigation */}
      <div className="admin-tabs">
        <button className={`admin-tab ${activeTab === "overview" ? "active" : ""}`} onClick={() => setActiveTab("overview")}>
          <FaTachometerAlt size={14} /><span>Home</span>
        </button>
        <button className={`admin-tab ${activeTab === "cloud-kb" ? "active" : ""}`} onClick={() => setActiveTab("cloud-kb")}>
          <FaDatabase size={14} /><span>Database</span>
        </button>
        <button className={`admin-tab ${activeTab === "users" ? "active" : ""}`} onClick={() => setActiveTab("users")}>
          <FaUsers size={14} /><span>Users</span>
        </button>
        <button className={`admin-tab ${activeTab === "tickets" ? "active" : ""}`} onClick={() => setActiveTab("tickets")}>
          <FaTicketAlt size={14} /><span>Tickets</span>
          {ticketStats.open > 0 && <span className="ticket-badge">{ticketStats.open}</span>}
        </button>
        <button className={`admin-tab ${activeTab === "feedback" ? "active" : ""}`} onClick={() => setActiveTab("feedback")}>
          <FaSmile size={14} /><span>Reviews</span>
          {feedbackStats.reports > 0 && <span className="ticket-badge">{feedbackStats.reports}</span>}
        </button>
        <button className={`admin-tab ${activeTab === "research" ? "active" : ""}`} onClick={() => setActiveTab("research")}>
          <FaSearch size={14} /><span>Research</span>
          {researchStats.pending_suggestions > 0 && <span className="ticket-badge">{researchStats.pending_suggestions}</span>}
        </button>
        <button className={`admin-tab ${activeTab === "courses" ? "active" : ""}`} onClick={() => setActiveTab("courses")}>
          <FaCog size={14} /><span>Courses</span>
        </button>
        <button className={`admin-tab ${activeTab === "system" ? "active" : ""}`} onClick={() => setActiveTab("system")}>
          <FaServer size={14} /><span>System</span>
        </button>
      </div>

      {/* =================== OVERVIEW TAB =================== */}
      {activeTab === "overview" && (
        <div className="tab-content">
          {overviewLoading ? (
            <div className="loading-state">Loading dashboard...</div>
          ) : overviewData ? (
            <>
              {/* Quick Stats Row */}
              <div className="ticket-stats">
                <div className="stat-card total" onClick={() => setActiveTab("users")} style={{ cursor: "pointer" }}>
                  <FaUsers className="stat-icon" />
                  <span className="stat-number">{overviewData.users?.total || 0}</span>
                  <span className="stat-label">Total Users</span>
                </div>
                <div className="stat-card progress" onClick={() => setActiveTab("cloud-kb")} style={{ cursor: "pointer" }}>
                  <FaDatabase className="stat-icon" />
                  <span className="stat-number">{overviewData.kbStats?.total_documents || 0}</span>
                  <span className="stat-label">KB Documents</span>
                </div>
                <div className="stat-card open">
                  <FaRobot className="stat-icon" />
                  <span className="stat-number">
                    {overviewData.cache?.cache_stats?.overall?.hit_rate || "0%"}
                  </span>
                  <span className="stat-label">Cache Hit Rate</span>
                </div>
                <div className="stat-card resolved">
                  <FaCheckCircle className="stat-icon" />
                  <span className="stat-number">
                    {overviewData.health?.database?.status === "connected" &&
                     overviewData.health?.vertex_agent?.status === "connected"
                      ? "All Good" : "Check"}
                  </span>
                  <span className="stat-label">System Health</span>
                </div>
              </div>

              {/* System Health Cards */}
              <div className="overview-section">
                <h3>System Status</h3>
                <div className="health-cards">
                  <div className={`health-card ${overviewData.health?.database?.status === "connected" ? "healthy" : "error"}`}>
                    <FaDatabase className="health-icon" />
                    <div className="health-info">
                      <h4>Database</h4>
                      <span className="health-status">{overviewData.health?.database?.status || "unknown"}</span>
                    </div>
                  </div>
                  <div className={`health-card ${overviewData.health?.vertex_agent?.status === "connected" ? "healthy" : "warning"}`}>
                    <FaRobot className="health-icon" />
                    <div className="health-info">
                      <h4>AI Agent</h4>
                      <span className="health-status">{overviewData.health?.vertex_agent?.status || "unknown"}</span>
                    </div>
                  </div>
                  <div className={`health-card ${overviewData.cache?.cache_stats?.l2_redis?.connected ? "healthy" : "warning"}`}>
                    <FaServer className="health-icon" />
                    <div className="health-info">
                      <h4>Redis Cache</h4>
                      <span className="health-status">{overviewData.cache?.cache_stats?.l2_redis?.connected ? "connected" : "offline"}</span>
                    </div>
                  </div>
                </div>
              </div>

              {/* Quick Actions */}
              <div className="overview-section">
                <h3>Quick Actions</h3>
                <div className="overview-actions">
                  <button className="action-btn" onClick={() => setActiveTab("cloud-kb")}>
                    <FaDatabase size={14} /> Manage Datastore
                  </button>
                  <button className="action-btn secondary" onClick={() => { handleClearCache(); }}>
                    <FaTrash size={14} /> Clear Cache
                  </button>
                  <button className="action-btn" onClick={() => { setActiveTab("cloud-kb"); setTimeout(() => handleCloudKbSync(), 100); }}>
                    <FaSync size={14} /> Sync Datastore
                  </button>
                </div>
              </div>

              {/* Knowledge Base Summary */}
              {overviewData.kbStats && (
                <div className="overview-section">
                  <h3>Knowledge Base</h3>
                  <div className="kb-stats-row">
                    <div className="kb-stat-item">
                      <span className="kb-stat-value">{overviewData.kbStats.total_documents}</span>
                      <span className="kb-stat-label">Documents</span>
                    </div>
                    <div className="kb-stat-item">
                      <span className="kb-stat-value">{formatBytes(overviewData.kbStats.total_size)}</span>
                      <span className="kb-stat-label">Total Size</span>
                    </div>
                    <div className="kb-stat-item">
                      <span className="kb-stat-value">
                        {overviewData.kbStats.last_modified
                          ? formatDate(overviewData.kbStats.last_modified)
                          : "N/A"}
                      </span>
                      <span className="kb-stat-label">Last Updated</span>
                    </div>
                  </div>
                </div>
              )}

              {/* User Signups Chart (moved from Analytics) */}
              {analytics && analytics.signups_by_day && (
                <div className="overview-section">
                  <h3>User Signups (Last 7 Days)</h3>
                  <div className="chart-container">
                    {analytics.signups_by_day.map((day, i) => (
                      <div key={i} className="chart-bar-wrapper">
                        <div className="chart-bar" style={{ height: `${Math.max(day.count * 30, 5)}px` }}>
                          <span className="chart-value">{day.count}</span>
                        </div>
                        <span className="chart-label">{day.day}</span>
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </>
          ) : (
            <div className="empty-state">Unable to load overview data</div>
          )}
        </div>
      )}

      {/* =================== USERS TAB =================== */}
      {activeTab === "users" && (
        <div className="tab-content">
          <div className="ticket-stats">
            <div className="stat-card total">
              <FaUsers className="stat-icon" />
              <span className="stat-number">{userStats.total}</span>
              <span className="stat-label">Total Users</span>
            </div>
            <div className="stat-card open">
              <FaUserGraduate className="stat-icon" />
              <span className="stat-number">{userStats.students}</span>
              <span className="stat-label">Students</span>
            </div>
            <div className="stat-card progress">
              <FaUserShield className="stat-icon" />
              <span className="stat-number">{userStats.admins}</span>
              <span className="stat-label">Admins</span>
            </div>
            <div className="stat-card resolved">
              <FaCalendarPlus className="stat-icon" />
              <span className="stat-number">{userStats.new_this_week}</span>
              <span className="stat-label">New This Week</span>
            </div>
          </div>

          <div className="search-filter-bar">
            <div className="search-box">
              <FaSearch size={14} />
              <input
                type="text"
                placeholder="Search by email, name, or student ID..."
                value={userSearch}
                onChange={(e) => setUserSearch(e.target.value)}
              />
            </div>
            <div className="filter-buttons">
              {["all", "student", "admin"].map((role) => (
                <button
                  key={role}
                  className={`filter-btn ${userRoleFilter === role ? "active" : ""}`}
                  onClick={() => setUserRoleFilter(role)}
                >
                  {role === "all" ? "All" : role.charAt(0).toUpperCase() + role.slice(1)}s
                </button>
              ))}
            </div>
          </div>

          <div className="table-container">
            {usersLoading ? (
              <div className="loading-state">Loading users...</div>
            ) : (
              <table className="admin-table">
                <thead>
                  <tr>
                    <th>Email</th>
                    <th>Name</th>
                    <th>Role</th>
                    <th>Major</th>
                    <th>Morgan</th>
                    <th>Joined</th>
                    <th>Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {users.map((u) => (
                    <tr key={u.id}>
                      <td>{u.email}</td>
                      <td>{u.name || "-"}</td>
                      <td><span className={`role-badge ${u.role}`}>{u.role}</span></td>
                      <td>{u.major || "-"}</td>
                      <td>{u.morgan_connected ? <FaLink size={14} className="connected" /> : "-"}</td>
                      <td>{formatDate(u.created_at)}</td>
                      <td>
                        <select
                          value={u.role}
                          onChange={(e) => handleUpdateUserRole(u.id, e.target.value)}
                          className="role-select"
                        >
                          <option value="student">Student</option>
                          <option value="admin">Admin</option>
                        </select>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        </div>
      )}

      {/* =================== RESEARCH TAB =================== */}
      {activeTab === "research" && (
        <div className="tab-content">
          <h2 style={{ marginBottom: "8px" }}>Auto-Research Agent</h2>
          <p style={{ color: "var(--text-secondary)", marginBottom: "20px" }}>
            Tracks questions the chatbot can't answer, researches answers from morgan.edu, and suggests KB updates.
          </p>

          {/* Stats */}
          <div className="stats-grid" style={{ marginBottom: "20px" }}>
            <div className="stat-card"><div className="stat-number">{researchStats.total_failed || 0}</div><div className="stat-label">Failed Queries</div></div>
            <div className="stat-card"><div className="stat-number">{researchStats.pending_suggestions || 0}</div><div className="stat-label">Pending Review</div></div>
            <div className="stat-card"><div className="stat-number">{researchStats.approved || 0}</div><div className="stat-label">Approved</div></div>
            <div className="stat-card"><div className="stat-number">{researchStats.pushed || 0}</div><div className="stat-label">Pushed to KB</div></div>
          </div>

          {/* Actions */}
          <div style={{ display: "flex", gap: "10px", marginBottom: "20px", alignItems: "center", flexWrap: "wrap" }}>
            <button className="btn-primary" onClick={handleRunResearch} disabled={researchRunning} style={{ padding: "8px 20px" }}>
              {researchRunning ? "Researching..." : "Run Research Now"}
            </button>
            <select value={suggestionFilter} onChange={(e) => { setSuggestionFilter(e.target.value); setTimeout(() => loadSuggestions(), 50); }} style={{ padding: "8px 12px", borderRadius: "8px", border: "1px solid var(--border-color)", background: "var(--bg-body)", color: "var(--text-main)" }}>
              <option value="pending">Pending</option>
              <option value="approved">Approved</option>
              <option value="rejected">Rejected</option>
              <option value="pushed">Pushed to KB</option>
              <option value="all">All</option>
            </select>
            <button onClick={() => { loadSuggestions(); loadResearchStats(); }} style={{ padding: "8px 12px", borderRadius: "8px", border: "1px solid var(--border-color)", background: "transparent", color: "var(--text-main)", cursor: "pointer" }}>
              Refresh
            </button>
            <button onClick={() => { setShowFailedQueries(!showFailedQueries); if (!showFailedQueries) loadFailedQueries(); }} style={{ padding: "8px 12px", borderRadius: "8px", border: "1px solid var(--border-color)", background: "transparent", color: "var(--text-secondary)", cursor: "pointer", marginLeft: "auto" }}>
              {showFailedQueries ? "Hide" : "Show"} Failed Queries ({researchStats.total_failed || 0})
            </button>
          </div>

          {/* Failed Queries (collapsible) */}
          {showFailedQueries && (
            <div style={{ marginBottom: "20px", background: "var(--bg-elevated)", borderRadius: "12px", padding: "16px", maxHeight: "300px", overflow: "auto" }}>
              <h4 style={{ marginBottom: "10px" }}>Recent Failed Queries</h4>
              {failedQueries.length === 0 ? <p style={{ color: "var(--text-secondary)" }}>No failed queries yet. Students haven't asked anything the bot couldn't answer.</p> :
                failedQueries.map(q => (
                  <div key={q.id} style={{ padding: "8px 0", borderBottom: "1px solid var(--border-color)" }}>
                    <div style={{ fontWeight: 500 }}>{q.user_query}</div>
                    <div style={{ fontSize: "12px", color: "var(--text-secondary)", marginTop: "2px" }}>
                      {q.status} | {new Date(q.created_at).toLocaleDateString()}
                    </div>
                  </div>
                ))
              }
            </div>
          )}

          {/* Suggestions */}
          <div>
            <h3 style={{ marginBottom: "12px" }}>
              KB Suggestions ({suggestions.length})
            </h3>
            {suggestions.length === 0 ? (
              <div style={{ textAlign: "center", padding: "40px", color: "var(--text-secondary)" }}>
                {suggestionFilter === "pending" ? "No pending suggestions. Run research to generate some." : `No ${suggestionFilter} suggestions.`}
              </div>
            ) : (
              suggestions.map(s => (
                <div key={s.id} style={{ background: "var(--bg-elevated)", borderRadius: "12px", padding: "16px", marginBottom: "12px", border: "1px solid var(--border-color)" }}>
                  {/* Header */}
                  <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "8px" }}>
                    <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
                      <h4 style={{ margin: 0 }}>{s.topic}</h4>
                      <span style={{ fontSize: "11px", padding: "2px 8px", borderRadius: "10px", background: s.confidence === "high" ? "#e8f5e9" : s.confidence === "medium" ? "#fff3e0" : "#ffebee", color: s.confidence === "high" ? "#2e7d32" : s.confidence === "medium" ? "#e65100" : "#c62828", fontWeight: 600 }}>
                        {s.confidence} confidence
                      </span>
                      <span style={{ fontSize: "11px", padding: "2px 8px", borderRadius: "10px", background: "#e3f2fd", color: "#1565c0", fontWeight: 600 }}>
                        {s.query_count} student{s.query_count !== 1 ? "s" : ""} asked
                      </span>
                    </div>
                    <span style={{ fontSize: "12px", color: "var(--text-secondary)" }}>{new Date(s.created_at).toLocaleDateString()}</span>
                  </div>

                  {/* Representative query */}
                  <div style={{ fontSize: "13px", color: "var(--text-secondary)", marginBottom: "8px", fontStyle: "italic" }}>
                    "{s.representative_query}"
                  </div>

                  {/* Researched answer (collapsible) */}
                  <div style={{ marginBottom: "8px" }}>
                    <button onClick={() => setExpandedSuggestion(expandedSuggestion === s.id ? null : s.id)} style={{ fontSize: "13px", color: "var(--accent-blue)", background: "none", border: "none", cursor: "pointer", padding: 0 }}>
                      {expandedSuggestion === s.id ? "Hide" : "Show"} researched answer
                    </button>
                    {expandedSuggestion === s.id && (
                      <div style={{ marginTop: "8px", padding: "12px", background: "var(--bg-body)", borderRadius: "8px", fontSize: "13px", whiteSpace: "pre-wrap", maxHeight: "300px", overflow: "auto" }}>
                        {s.researched_answer}
                        {s.sources && s.sources.length > 0 && (
                          <div style={{ marginTop: "10px", paddingTop: "8px", borderTop: "1px solid var(--border-color)" }}>
                            <strong>Sources:</strong>
                            {s.sources.map((url, i) => <div key={i}><a href={url} target="_blank" rel="noopener noreferrer" style={{ fontSize: "12px", color: "var(--accent-blue)" }}>{url}</a></div>)}
                          </div>
                        )}
                      </div>
                    )}
                  </div>

                  {/* Target doc */}
                  <div style={{ fontSize: "12px", color: "var(--text-secondary)", marginBottom: "10px" }}>
                    Target KB doc: <strong>{s.suggested_doc_id || "new document"}</strong>
                  </div>

                  {/* Content Preview (editable before push) */}
                  {(s.status === "pending" || s.status === "approved") && (
                    <div style={{ marginBottom: "10px" }}>
                      <div style={{ fontSize: "12px", fontWeight: 600, marginBottom: "4px", color: "var(--text-secondary)" }}>
                        Content to be pushed to KB (editable):
                      </div>
                      <textarea
                        defaultValue={s.suggested_content}
                        id={`suggestion-content-${s.id}`}
                        style={{
                          width: "100%", minHeight: "120px", padding: "10px", borderRadius: "8px",
                          border: "1px solid var(--border-color)", background: "var(--bg-body)",
                          color: "var(--text-main)", fontSize: "13px", fontFamily: "monospace",
                          resize: "vertical"
                        }}
                      />
                    </div>
                  )}

                  {/* Actions */}
                  {s.status === "pending" && (
                    <div style={{ display: "flex", gap: "8px" }}>
                      <button onClick={() => {
                        const content = document.getElementById(`suggestion-content-${s.id}`)?.value;
                        if (content) handleSuggestionAction(s.id, "edit", { content });
                        handleSuggestionAction(s.id, "approve");
                      }} style={{ padding: "6px 16px", borderRadius: "8px", border: "none", background: "#4caf50", color: "white", cursor: "pointer", fontWeight: 500 }}>Approve</button>
                      <button onClick={() => handleSuggestionAction(s.id, "reject")} style={{ padding: "6px 16px", borderRadius: "8px", border: "1px solid #ef5350", background: "transparent", color: "#ef5350", cursor: "pointer" }}>Reject</button>
                    </div>
                  )}
                  {s.status === "approved" && (
                    <button onClick={() => {
                      const content = document.getElementById(`suggestion-content-${s.id}`)?.value;
                      if (content) handleSuggestionAction(s.id, "edit", { content });
                      setTimeout(() => handlePushSuggestion(s.id), 300);
                    }} style={{ padding: "6px 16px", borderRadius: "8px", border: "none", background: "var(--accent-blue)", color: "white", cursor: "pointer", fontWeight: 500 }}>Push to KB</button>
                  )}
                  {s.status === "pushed" && <span style={{ color: "#4caf50", fontWeight: 500 }}>Pushed to KB</span>}
                  {s.status === "rejected" && <span style={{ color: "#ef5350" }}>Rejected</span>}
                </div>
              ))
            )}
          </div>
        </div>
      )}

      {/* =================== COURSES TAB =================== */}
      {activeTab === "courses" && (
        <div className="tab-content">
          <section>
            <h2>Add New Course</h2>
            <form onSubmit={handleAddCourse} className="admin-form">
              <input name="course_code" placeholder="Course Code (e.g. COSC 101)" value={course.course_code} onChange={(e) => setCourse({...course, course_code: e.target.value})} required />
              <input name="course_name" placeholder="Course Name" value={course.course_name} onChange={(e) => setCourse({...course, course_name: e.target.value})} required />
              <input name="credits" type="number" placeholder="Credits" value={course.credits} onChange={(e) => setCourse({...course, credits: e.target.value})} required />
              <input name="prerequisites" placeholder="Prerequisites (comma-separated)" value={course.prerequisites} onChange={(e) => setCourse({...course, prerequisites: e.target.value})} />
              <input name="offered" placeholder="Offered Semesters (comma-separated)" value={course.offered} onChange={(e) => setCourse({...course, offered: e.target.value})} />
              <button type="submit" className="action-btn">Add Course</button>
            </form>
          </section>

          <section style={{ marginTop: 32 }}>
            <h2>Existing Courses ({courses.length})</h2>
            <table className="admin-table">
              <thead>
                <tr><th>Code</th><th>Name</th><th>Credits</th><th>Offered</th><th>Actions</th></tr>
              </thead>
              <tbody>
                {courses.map((c) => (
                  <tr key={c.course_code}>
                    <td>{c.course_code}</td>
                    <td>{c.course_name}</td>
                    <td>{c.credits}</td>
                    <td>{Array.isArray(c.offered) ? c.offered.join(", ") : c.offered}</td>
                    <td className="action-cell">
                      <button className="edit-btn-labeled" onClick={() => setEditingCourse({
                        ...c,
                        prerequisites: Array.isArray(c.prerequisites) ? c.prerequisites.join(", ") : c.prerequisites || "",
                        offered: Array.isArray(c.offered) ? c.offered.join(", ") : c.offered || ""
                      })}>
                        <FaEdit size={16} />
                        <span>Edit</span>
                      </button>
                      <button className="delete-btn-labeled" onClick={() => handleDeleteCourse(c.course_code)}>
                        <FaTrash size={16} />
                        <span>Delete</span>
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </section>

          {message && <p className="message">{message}</p>}
        </div>
      )}

      {/* =================== TICKETS TAB =================== */}
      {activeTab === "tickets" && (
        <div className="tickets-section">
          <div className="ticket-stats">
            <div className="stat-card total"><FaInbox className="stat-icon" /><span className="stat-number">{ticketStats.total}</span><span className="stat-label">Total</span></div>
            <div className="stat-card open"><FaExclamationCircle className="stat-icon" /><span className="stat-number">{ticketStats.open}</span><span className="stat-label">Open</span></div>
            <div className="stat-card progress"><FaSpinner className="stat-icon" /><span className="stat-number">{ticketStats.in_progress}</span><span className="stat-label">In Progress</span></div>
            <div className="stat-card resolved"><FaCheckCircle className="stat-icon" /><span className="stat-number">{ticketStats.resolved}</span><span className="stat-label">Resolved</span></div>
          </div>

          <div className="ticket-filters">
            {["all", "open", "in_progress", "resolved"].map((filter) => (
              <button key={filter} className={`filter-btn ${ticketFilter === filter ? "active" : ""}`}
                onClick={() => { setTicketFilter(filter); loadTickets(filter === "all" ? null : filter); }}>
                {filter === "all" ? "All" : filter.replace("_", " ")}
              </button>
            ))}
          </div>

          <div className="tickets-list">
            {ticketLoading ? <div className="tickets-loading">Loading tickets...</div> : tickets.length === 0 ? <div className="tickets-empty">No tickets found</div> : (
              tickets.map((ticket) => (
                <div key={ticket.id} className="ticket-card">
                  <div className="ticket-header-row">
                    <div className="ticket-category">{getCategoryIcon(ticket.category)}<span>{ticket.category}</span></div>
                    <span className={`ticket-status ${getStatusClass(ticket.status)}`}>{ticket.status.replace("_", " ")}</span>
                  </div>
                  <h3 className="ticket-subject">{ticket.subject}</h3>
                  <p className="ticket-preview">{ticket.description.length > 150 ? ticket.description.slice(0, 150) + "..." : ticket.description}</p>
                  <div className="ticket-footer">
                    <div className="ticket-meta">
                      <span className="ticket-user"><FaUser size={11} />{ticket.user_email || "Unknown"}</span>
                      <span className="ticket-date"><FaClock size={11} />{formatDateTime(ticket.created_at)}</span>
                    </div>
                    <div className="ticket-actions">
                      <button className="view-btn" onClick={() => setSelectedTicket(ticket)} title="View"><FaEye size={14} /></button>
                      {ticket.status === "open" && <button className="progress-btn" onClick={() => updateTicketStatus(ticket.id, "in_progress")} title="In Progress"><FaClock size={14} /></button>}
                      {ticket.status !== "resolved" && <button className="resolve-btn" onClick={() => updateTicketStatus(ticket.id, "resolved")} title="Resolve"><FaCheck size={14} /></button>}
                    </div>
                  </div>
                </div>
              ))
            )}
          </div>
        </div>
      )}

      {/* Analytics merged into Overview tab */}

      {/* =================== FEEDBACK TAB =================== */}
      {activeTab === "feedback" && (
        <div className="tab-content">
          {/* Feedback Stats */}
          <div className="ticket-stats">
            <div className="stat-card total">
              <FaSmile className="stat-icon" />
              <span className="stat-number">{feedbackStats.total}</span>
              <span className="stat-label">Total Feedback</span>
            </div>
            <div className="stat-card" style={{ background: 'linear-gradient(135deg, #dcfce7 0%, #bbf7d0 100%)', color: '#166534' }}>
              <FaThumbsUp className="stat-icon" style={{ color: '#22c55e' }} />
              <span className="stat-number" style={{ color: '#166534' }}>{feedbackStats.helpful}</span>
              <span className="stat-label" style={{ color: '#166534' }}>Helpful</span>
            </div>
            <div className="stat-card" style={{ background: 'linear-gradient(135deg, #fee2e2 0%, #fecaca 100%)', color: '#991b1b' }}>
              <FaThumbsDown className="stat-icon" style={{ color: '#ef4444' }} />
              <span className="stat-number" style={{ color: '#991b1b' }}>{feedbackStats.not_helpful}</span>
              <span className="stat-label" style={{ color: '#991b1b' }}>Not Helpful</span>
            </div>
            <div className="stat-card" style={{ background: 'linear-gradient(135deg, #fef3c7 0%, #fde68a 100%)', color: '#92400e' }}>
              <FaFlag className="stat-icon" style={{ color: '#f59e0b' }} />
              <span className="stat-number" style={{ color: '#92400e' }}>{feedbackStats.reports}</span>
              <span className="stat-label" style={{ color: '#92400e' }}>Reports</span>
            </div>
          </div>

          {/* Satisfaction Rate */}
          <div className="satisfaction-card">
            <h3>User Satisfaction Rate</h3>
            <div className="satisfaction-bar-container">
              <div
                className="satisfaction-bar"
                style={{ width: `${feedbackStats.satisfaction_rate || 0}%` }}
              />
            </div>
            <span className="satisfaction-percent">{feedbackStats.satisfaction_rate || 0}%</span>
          </div>

          {/* Recent Reports Section */}
          <div className="feedback-reports-section">
            <h3><FaFlag size={16} /> Recent Reports ({feedbackStats.reports})</h3>
            {feedbackStats.recent_reports && feedbackStats.recent_reports.length > 0 ? (
              <div className="feedback-list">
                {feedbackStats.recent_reports.map((report) => (
                  <div key={report.id} className="feedback-card report">
                    <div className="feedback-header">
                      <span className="feedback-type-badge report">
                        <FaFlag size={12} /> Report
                      </span>
                      <span className="feedback-date">
                        <FaClock size={11} /> {formatDateTime(report.timestamp)}
                      </span>
                    </div>
                    <div className="feedback-message">
                      <strong>Bot Response:</strong>
                      <p>{report.message_preview}</p>
                    </div>
                    {report.details && (
                      <div className="feedback-details">
                        <strong>User's Report:</strong>
                        <p>{report.details}</p>
                      </div>
                    )}
                  </div>
                ))}
              </div>
            ) : (
              <div className="empty-state">
                <FaCheckCircle size={32} style={{ color: '#22c55e', marginBottom: 12 }} />
                <p>No reports yet! Users are happy with the responses.</p>
              </div>
            )}
          </div>
        </div>
      )}

      {/* =================== SYSTEM TAB =================== */}
      {/* =================== CLOUD KB / DATASTORE TAB =================== */}
      {activeTab === "cloud-kb" && (
        <div className="tab-content">
          <div className="kb-header">
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start" }}>
              <div>
                <h2>Cloud Datastore</h2>
                <p>Manage documents in the Vertex AI Search datastore. Changes auto-clear cache for instant sync.</p>
              </div>
              <div className="kb-stats-row" style={{ marginBottom: 0 }}>
                <div className="kb-stat-item compact">
                  <span className="kb-stat-value">{cloudKbStats.total_documents}</span>
                  <span className="kb-stat-label">Docs</span>
                </div>
                <div className="kb-stat-item compact">
                  <span className="kb-stat-value">{formatBytes(cloudKbStats.total_size)}</span>
                  <span className="kb-stat-label">Size</span>
                </div>
                <div className="kb-stat-item compact">
                  <span className="kb-stat-value">{cloudKbStats.last_modified ? new Date(cloudKbStats.last_modified).toLocaleDateString("en-US", { month: "short", day: "numeric" }) : "N/A"}</span>
                  <span className="kb-stat-label">Updated</span>
                </div>
              </div>
            </div>
          </div>

          {/* Drag & Drop Zone */}
          <div
            className={`kb-drop-zone ${cloudKbDragActive ? "active" : ""}`}
            onDragOver={handleDragOver}
            onDragLeave={handleDragLeave}
            onDrop={handleDrop}
          >
            <FaCloudUploadAlt size={24} />
            <span>Drag & drop files here to upload to datastore</span>
            <span className="drop-hint">.txt, .pdf, .html, .csv, .json</span>
          </div>

          {/* Search Bar */}
          <div className="kb-search-bar">
            <div className="search-box-with-voice">
              <div className="search-box">
                <FaSearch size={14} />
                <input
                  type="text"
                  placeholder="Search across all cloud KB documents..."
                  value={kbSearch}
                  onChange={(e) => handleCloudKbSearch(e.target.value)}
                />
                {kbSearch && (
                  <button className="clear-search" onClick={() => { setKbSearch(""); setFindText(""); setShowFindReplace(false); setCloudKbSearchResults(null); }}>
                    <FaTimes size={12} />
                  </button>
                )}
              </div>
            </div>
          </div>

          {/* Search Results Summary */}
          {cloudKbSearchResults !== null && kbSearch && (
            <div className="kb-search-summary">
              <span className="search-summary-text">
                {cloudKbSearching ? "Searching..." : (
                  <>Found matches in <strong>{cloudKbSearchResults.length}</strong> files for "<strong>{kbSearch}</strong>"</>
                )}
              </span>
            </div>
          )}

          {message && <p className="message" style={{ margin: "8px 0" }}>{message}</p>}
          {cloudKbUploading && <div className="loading-state">Uploading document...</div>}

          <div className="kb-layout">
            {/* Document List Sidebar */}
            <div className="kb-sidebar">
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "8px" }}>
                <h3>{cloudKbSearchResults !== null && kbSearch
                  ? `Matches (${cloudKbSearchResults.length})`
                  : `Documents (${cloudKbDocs.length})`
                }</h3>
                <div style={{ display: "flex", gap: "4px" }}>
                  <button className="action-btn" onClick={loadCloudKbDocs} disabled={cloudKbLoading} title="Refresh" style={{ padding: "4px 8px" }}>
                    <FaSync size={12} className={cloudKbLoading ? "spinning" : ""} />
                  </button>
                  <label className="action-btn" style={{ cursor: "pointer", display: "inline-flex", alignItems: "center", gap: "4px", padding: "4px 8px" }} title="Upload new document">
                    <FaCalendarPlus size={12} />
                    <input type="file" ref={cloudKbFileRef} accept=".txt,.pdf,.html,.csv,.json" onChange={handleCloudKbUpload} style={{ display: "none" }} />
                  </label>
                </div>
              </div>
              {cloudKbLoading || cloudKbSearching ? (
                <div className="loading-state">{cloudKbSearching ? "Searching..." : "Loading..."}</div>
              ) : cloudKbSearchResults !== null && kbSearch ? (
                // Show only matched files when searching
                cloudKbSearchResults.length === 0 ? (
                  <div className="empty-state">No matches found</div>
                ) : (
                  cloudKbSearchResults.map((result) => {
                    // Find matching doc from full list to get the id
                    const doc = cloudKbDocs.find(d => d.filename === result.filename) || { id: result.filename, filename: result.filename, uri: result.uri, size: result.size };
                    return (
                      <div
                        key={result.filename}
                        className={`kb-file-item ${cloudKbSelected?.filename === result.filename ? "active" : ""}`}
                        onClick={() => {
                          loadCloudKbContent(doc);
                          setFindText(kbSearch);
                          setShowFindReplace(true);
                        }}
                      >
                        <span className="kb-filename">{formatDocName(result.filename)}</span>
                        <span className="match-badge" style={{ background: "#e8f0fe", color: "#1a73e8", borderRadius: "10px", padding: "1px 8px", fontSize: "11px", fontWeight: 600 }}>
                          {result.match_count} {result.match_count === 1 ? "match" : "matches"}
                        </span>
                      </div>
                    );
                  })
                )
              ) : (
                // Show all files when not searching
                cloudKbDocs.map((doc) => (
                  <div
                    key={doc.id}
                    className={`kb-file-item ${cloudKbSelected?.id === doc.id ? "active" : ""}`}
                    onClick={() => loadCloudKbContent(doc)}
                  >
                    <div style={{ display: "flex", flexDirection: "column", gap: "2px", flex: 1, minWidth: 0 }}>
                      <span className="kb-filename">{formatDocName(doc.filename)}</span>
                      <span style={{
                        fontSize: "10px",
                        padding: "1px 6px",
                        borderRadius: "8px",
                        width: "fit-content",
                        background: getCategoryColor(doc.id).bg,
                        color: getCategoryColor(doc.id).color,
                        fontWeight: 600
                      }}>{getCategoryColor(doc.id).label}</span>
                    </div>
                    <span className="kb-filesize">{doc.size > 0 ? formatBytes(doc.size) : ""}</span>
                  </div>
                ))
              )}
            </div>

            {/* Document Editor */}
            <div className="kb-editor">
              {cloudKbSelected ? (
                <>
                  <div className="kb-editor-header">
                    <div style={{ display: "flex", alignItems: "center", gap: "10px" }}>
                      <h3 style={{ margin: 0 }}>{formatDocName(cloudKbSelected.filename)}</h3>
                      <span style={{
                        fontSize: "11px",
                        padding: "2px 8px",
                        borderRadius: "10px",
                        background: getCategoryColor(cloudKbSelected.id).bg,
                        color: getCategoryColor(cloudKbSelected.id).color,
                        fontWeight: 600
                      }}>{getCategoryColor(cloudKbSelected.id).label}</span>
                    </div>
                    <div className="kb-editor-actions">
                      <button
                        className={`kb-icon-btn ${showFindReplace ? "active" : ""}`}
                        onClick={() => setShowFindReplace(!showFindReplace)}
                        title="Find & Replace"
                      >
                        <FaSearch size={13} />
                      </button>
                      {cloudKbEditing ? (
                        <>
                          <button className="kb-icon-btn save" onClick={handleCloudKbSave} title="Save to Cloud">
                            <FaSave size={13} />
                          </button>
                          <button className="kb-icon-btn" onClick={() => { setCloudKbEditing(false); }} title="Cancel editing">
                            <FaTimes size={13} />
                          </button>
                        </>
                      ) : (
                        <button className="kb-icon-btn" onClick={() => { setCloudKbEditing(true); setCloudKbEditContent(cloudKbContent); }} title="Edit document">
                          <FaEdit size={13} />
                        </button>
                      )}
                      <button className="kb-icon-btn" onClick={handleCloudKbSync} disabled={cloudKbSyncing} title="Re-sync datastore">
                        <FaSync size={13} className={cloudKbSyncing ? "spinning" : ""} />
                      </button>
                      <button className="kb-icon-btn danger" onClick={() => handleCloudKbDelete(cloudKbSelected)} title="Delete document">
                        <FaTrash size={13} />
                      </button>
                    </div>
                  </div>

                  {/* Find & Replace Toolbar */}
                  {showFindReplace && (
                    <div className="find-replace-toolbar">
                      <div className="find-replace-row">
                        <label>Find:</label>
                        <input
                          type="text"
                          value={findText}
                          onChange={(e) => setFindText(e.target.value)}
                          placeholder="Search text..."
                          onKeyDown={(e) => e.key === 'Enter' && findNextMatch()}
                        />
                        <span className="match-counter">
                          {matchCount > 0 ? `${currentMatchIndex || 1} of ${matchCount}` : "No matches"}
                        </span>
                        <button onClick={findPrevMatch} disabled={matchCount === 0} title="Previous match">
                          &#9650;
                        </button>
                        <button onClick={findNextMatch} disabled={matchCount === 0} title="Next match">
                          &#9660;
                        </button>
                      </div>
                      {cloudKbEditing && (
                        <div className="find-replace-row">
                          <label>Replace:</label>
                          <input
                            type="text"
                            value={replaceText}
                            onChange={(e) => setReplaceText(e.target.value)}
                            placeholder="Replace with..."
                            onKeyDown={(e) => e.key === 'Enter' && replaceCurrentMatch()}
                          />
                          <button onClick={replaceCurrentMatch} disabled={matchCount === 0} className="replace-btn">
                            Replace
                          </button>
                          <button onClick={replaceAllMatches} disabled={matchCount === 0} className="replace-all-btn">
                            Replace All
                          </button>
                        </div>
                      )}
                      <button className="close-find-replace" onClick={() => setShowFindReplace(false)}>
                        <FaTimes size={12} />
                      </button>
                    </div>
                  )}

                  {cloudKbContent === "Loading..." ? (
                    <div className="loading-state">Loading file...</div>
                  ) : cloudKbEditing ? (
                    <div className="kb-editor-container">
                      {findText && (
                        <div
                          ref={highlightRef}
                          className="kb-highlight-backdrop"
                          dangerouslySetInnerHTML={{ __html: getHighlightedContent() }}
                        />
                      )}
                      <textarea
                        ref={textareaRef}
                        className={`kb-textarea ${findText ? "with-highlights" : ""}`}
                        value={cloudKbEditContent}
                        onChange={(e) => setCloudKbEditContent(e.target.value)}
                        onScroll={handleTextareaScroll}
                        spellCheck={false}
                      />
                    </div>
                  ) : (
                    <div className="kb-editor-container">
                      {findText ? (
                        <div
                          ref={textareaRef}
                          className="kb-textarea"
                          style={{
                            whiteSpace: "pre-wrap", wordBreak: "break-word", fontFamily: "monospace",
                            fontSize: "13px", background: "#f8f9fa", padding: "16px", borderRadius: "6px",
                            border: "1px solid #e0e0e0", overflow: "auto", lineHeight: "1.5",
                            height: "100%", margin: 0
                          }}
                          dangerouslySetInnerHTML={{ __html: getHighlightedContent() }}
                        />
                      ) : (
                        <pre
                          ref={textareaRef}
                          className="kb-textarea"
                          style={{
                            whiteSpace: "pre-wrap", wordBreak: "break-word", fontFamily: "monospace",
                            fontSize: "13px", background: "#f8f9fa", padding: "16px", borderRadius: "6px",
                            border: "1px solid #e0e0e0", overflow: "auto", lineHeight: "1.5",
                            height: "100%", margin: 0
                          }}
                        >
                          {cloudKbContent}
                        </pre>
                      )}
                    </div>
                  )}
                </>
              ) : (
                <div className="kb-placeholder">Select a document to view or edit</div>
              )}
            </div>
          </div>
        </div>
      )}

      {activeTab === "system" && (
        <div className="tab-content">
          <div className="system-header">
            <h2>System Health</h2>
            <button className="action-btn" onClick={loadHealth}><FaSync size={14} /> Refresh</button>
          </div>

          {healthLoading ? (
            <div className="loading-state">Checking system health...</div>
          ) : healthStatus ? (
            <>
              <div className="health-cards">
                <div className={`health-card ${healthStatus.database?.status === "connected" ? "healthy" : "error"}`}>
                  <FaDatabase className="health-icon" />
                  <div className="health-info">
                    <h4>Database (RDS)</h4>
                    <span className="health-status">{healthStatus.database?.status}</span>
                    <p>{healthStatus.database?.message}</p>
                  </div>
                </div>

                <div className={`health-card ${healthStatus.vertex_agent?.status === "connected" ? "healthy" : healthStatus.vertex_agent?.status === "not_configured" ? "warning" : "error"}`}>
                  <FaRobot className="health-icon" />
                  <div className="health-info">
                    <h4>Vertex AI Agent (ADK)</h4>
                    <span className="health-status">{healthStatus.vertex_agent?.status}</span>
                    <p>{healthStatus.vertex_agent?.message}</p>
                  </div>
                </div>

                <div className={`health-card ${healthStatus.openai_tts?.status === "configured" ? "healthy" : "warning"}`}>
                  <FaMicrophone className="health-icon" />
                  <div className="health-info">
                    <h4>OpenAI TTS</h4>
                    <span className="health-status">{healthStatus.openai_tts?.status}</span>
                    <p>{healthStatus.openai_tts?.message}</p>
                  </div>
                </div>

                {healthStatus.mode && (
                  <div className="health-card healthy">
                    <FaServer className="health-icon" />
                    <div className="health-info">
                      <h4>AI Mode</h4>
                      <span className="health-status">{healthStatus.mode === "vertex_ai" ? "Google ADK" : "Legacy RAG"}</span>
                      <p>{healthStatus.mode === "vertex_ai" ? "Vertex AI Search + Gemini 2.0 Flash" : "Pinecone + OpenAI GPT-3.5"}</p>
                    </div>
                  </div>
                )}
              </div>

              {/* Cache Management Section */}
              <div className="cache-management">
                <div className="system-header" style={{ marginTop: 32 }}>
                  <h2>Cache Management</h2>
                  <div style={{ display: "flex", gap: 8 }}>
                    <button className="action-btn" onClick={loadCacheStats} disabled={cacheLoading}>
                      <FaSync size={14} className={cacheLoading ? "spinning" : ""} /> Refresh
                    </button>
                    <button className="action-btn danger" onClick={handleClearCache} disabled={cacheClearing}>
                      <FaTrash size={14} /> {cacheClearing ? "Clearing..." : "Clear All Cache"}
                    </button>
                  </div>
                </div>

                {cacheStats?.cache_stats ? (
                  <div className="cache-stats-grid">
                    <div className="cache-tier-card">
                      <h4>Overall</h4>
                      <div className="cache-stat-row">
                        <span>Hit Rate</span>
                        <strong>{cacheStats.cache_stats.overall?.hit_rate || "0%"}</strong>
                      </div>
                      <div className="cache-stat-row">
                        <span>Total Hits</span>
                        <strong>{cacheStats.cache_stats.overall?.total_hits || 0}</strong>
                      </div>
                      <div className="cache-stat-row">
                        <span>Total Misses</span>
                        <strong>{cacheStats.cache_stats.overall?.total_misses || 0}</strong>
                      </div>
                      <div className="cache-stat-row">
                        <span>Skipped (Personal)</span>
                        <strong>{cacheStats.cache_stats.overall?.skipped || 0}</strong>
                      </div>
                    </div>
                    <div className="cache-tier-card">
                      <h4>L1 (In-Memory)</h4>
                      <div className="cache-stat-row">
                        <span>Entries</span>
                        <strong>{cacheStats.cache_stats.l1_inmemory?.size || 0} / {cacheStats.cache_stats.l1_inmemory?.max_size || 500}</strong>
                      </div>
                      <div className="cache-stat-row">
                        <span>Hit Rate</span>
                        <strong>{cacheStats.cache_stats.l1_inmemory?.hit_rate || "0%"}</strong>
                      </div>
                    </div>
                    <div className="cache-tier-card">
                      <h4>L2 (Redis)</h4>
                      <div className="cache-stat-row">
                        <span>Connected</span>
                        <strong style={{ color: cacheStats.cache_stats.l2_redis?.connected ? "#22C55E" : "#EF4444" }}>
                          {cacheStats.cache_stats.l2_redis?.connected ? "Yes" : "No"}
                        </strong>
                      </div>
                      <div className="cache-stat-row">
                        <span>Entries</span>
                        <strong>{cacheStats.cache_stats.l2_redis?.size ?? "N/A"}</strong>
                      </div>
                      <div className="cache-stat-row">
                        <span>Hit Rate</span>
                        <strong>{cacheStats.cache_stats.l2_redis?.hit_rate || "0%"}</strong>
                      </div>
                    </div>
                    <div className="cache-tier-card">
                      <h4>Semantic</h4>
                      <div className="cache-stat-row">
                        <span>Available</span>
                        <strong style={{ color: cacheStats.cache_stats.semantic?.available ? "#22C55E" : "#EF4444" }}>
                          {cacheStats.cache_stats.semantic?.available ? "Yes" : "No"}
                        </strong>
                      </div>
                      <div className="cache-stat-row">
                        <span>Index</span>
                        <strong>{cacheStats.cache_stats.semantic?.index_size || 0} / {cacheStats.cache_stats.semantic?.max_entries || 200}</strong>
                      </div>
                      <div className="cache-stat-row">
                        <span>Hit Rate</span>
                        <strong>{cacheStats.cache_stats.semantic?.hit_rate || "0%"}</strong>
                      </div>
                      <div className="cache-stat-row">
                        <span>Threshold</span>
                        <strong>{cacheStats.cache_stats.semantic?.threshold || 0.78}</strong>
                      </div>
                    </div>
                  </div>
                ) : (
                  <div className="empty-state">No cache data. Click Refresh to load.</div>
                )}
              </div>

              <div className="system-actions">
                <h3>Quick Actions</h3>
                <div className="action-buttons">
                  <button className="action-btn" onClick={handleReingest}><FaSync size={14} /> Re-ingest KB Docs</button>
                </div>
              </div>
            </>
          ) : (
            <div className="empty-state">Unable to load health status</div>
          )}
        </div>
      )}

      {/* =================== EDIT COURSE MODAL =================== */}
      {editingCourse && (
        <div className="ticket-modal-overlay" onClick={() => setEditingCourse(null)}>
          <div className="ticket-detail-modal" onClick={(e) => e.stopPropagation()}>
            <div className="modal-header">
              <h2>Edit Course: {editingCourse.course_code}</h2>
              <button className="modal-close" onClick={() => setEditingCourse(null)}><FaTimes size={18} /></button>
            </div>
            <div className="modal-body">
              <form onSubmit={handleEditCourse} className="edit-form">
                <div className="form-group">
                  <label>Course Code</label>
                  <input value={editingCourse.course_code} disabled />
                </div>
                <div className="form-group">
                  <label>Course Name</label>
                  <input value={editingCourse.course_name} onChange={(e) => setEditingCourse({...editingCourse, course_name: e.target.value})} required />
                </div>
                <div className="form-group">
                  <label>Credits</label>
                  <input type="number" value={editingCourse.credits} onChange={(e) => setEditingCourse({...editingCourse, credits: e.target.value})} required />
                </div>
                <div className="form-group">
                  <label>Prerequisites (comma-separated)</label>
                  <input value={editingCourse.prerequisites} onChange={(e) => setEditingCourse({...editingCourse, prerequisites: e.target.value})} />
                </div>
                <div className="form-group">
                  <label>Offered Semesters (comma-separated)</label>
                  <input value={editingCourse.offered} onChange={(e) => setEditingCourse({...editingCourse, offered: e.target.value})} />
                </div>
                <div className="modal-actions">
                  <button type="button" className="action-btn secondary" onClick={() => setEditingCourse(null)}>Cancel</button>
                  <button type="submit" className="action-btn">Save Changes</button>
                </div>
              </form>
            </div>
          </div>
        </div>
      )}

      {/* =================== TICKET DETAIL MODAL =================== */}
      {selectedTicket && (
        <div className="ticket-modal-overlay" onClick={() => setSelectedTicket(null)}>
          <div className="ticket-detail-modal" onClick={(e) => e.stopPropagation()}>
            <div className="modal-header">
              <div className="modal-title-row">{getCategoryIcon(selectedTicket.category)}<h2>{selectedTicket.subject}</h2></div>
              <button className="modal-close" onClick={() => setSelectedTicket(null)}><FaTimes size={18} /></button>
            </div>
            <div className="modal-body">
              <div className="modal-meta">
                <span className={`ticket-status ${getStatusClass(selectedTicket.status)}`}>{selectedTicket.status.replace("_", " ")}</span>
                <span className="ticket-user"><FaUser size={12} />{selectedTicket.user_email || "Unknown"}</span>
                <span className="ticket-date"><FaClock size={12} />{formatDateTime(selectedTicket.created_at)}</span>
              </div>
              <div className="modal-description"><h4>Description</h4><p>{selectedTicket.description}</p></div>
              {selectedTicket.attachment_name && (
                <div className="modal-attachment">
                  <h4>Attachment</h4>
                  {selectedTicket.attachment_data ? (
                    <>
                      {/\.(png|jpg|jpeg|gif|webp)$/i.test(selectedTicket.attachment_name) ? (
                        <img
                          src={selectedTicket.attachment_data.startsWith('data:') ? selectedTicket.attachment_data : `data:image/png;base64,${selectedTicket.attachment_data}`}
                          alt={selectedTicket.attachment_name}
                          style={{ maxWidth: '100%', maxHeight: '400px', borderRadius: '8px', marginBottom: '8px', border: '1px solid var(--border-color)' }}
                        />
                      ) : null}
                      <a
                        href={selectedTicket.attachment_data.startsWith('data:') ? selectedTicket.attachment_data : `data:application/octet-stream;base64,${selectedTicket.attachment_data}`}
                        download={selectedTicket.attachment_name}
                        style={{ display: 'inline-block', padding: '6px 14px', background: 'var(--msu-blue)', color: '#fff', borderRadius: '6px', fontSize: '0.85rem', textDecoration: 'none' }}
                      >
                        Download {selectedTicket.attachment_name}
                      </a>
                    </>
                  ) : (
                    <span>{selectedTicket.attachment_name}</span>
                  )}
                </div>
              )}
              <div className="modal-actions">
                <h4>Update Status</h4>
                <div className="status-buttons">
                  <button className={`status-btn open ${selectedTicket.status === "open" ? "active" : ""}`} onClick={() => updateTicketStatus(selectedTicket.id, "open")}>Open</button>
                  <button className={`status-btn progress ${selectedTicket.status === "in_progress" ? "active" : ""}`} onClick={() => updateTicketStatus(selectedTicket.id, "in_progress")}>In Progress</button>
                  <button className={`status-btn resolved ${selectedTicket.status === "resolved" ? "active" : ""}`} onClick={() => updateTicketStatus(selectedTicket.id, "resolved")}>Resolved</button>
                </div>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Admin Footer with Documentation Icons */}
      <div className="admin-footer">
        <button
          className="footer-icon-btn"
          onClick={() => { setDocViewerMode("technical"); setShowDocViewer(true); }}
          title="View Technical Documentation"
        >
          <FaFileAlt size={20} />
          <span>Docs</span>
        </button>
        <button
          className="footer-icon-btn"
          onClick={() => { setDocViewerMode("roadmap"); setShowDocViewer(true); }}
          title="View Development Roadmap"
        >
          <FaRoad size={20} />
          <span>Roadmap</span>
        </button>
        <button
          className="footer-icon-btn agents"
          onClick={() => { setDocViewerMode("agents"); setShowDocViewer(true); }}
          title="View AI Agents Plan"
        >
          <FaRobot size={20} />
          <span>AI Agents</span>
        </button>
      </div>

      {/* Documentation Viewer Modal */}
      <DocumentationViewer
        isOpen={showDocViewer}
        onClose={() => setShowDocViewer(false)}
        darkMode={true}
        mode={docViewerMode}
      />
    </div>
  );
}
