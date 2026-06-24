import React, { useState, useEffect, lazy, Suspense } from "react";
import { Routes, Route, Navigate, useNavigate } from "react-router-dom";
import { Toaster } from "sonner";

// Eager: always-mounted layout chrome (light, shown on every screen).
import NavBar         from "./components/NavBar";
import ChatSidebar    from "./components/ChatSidebar";
import CommandPalette from "./components/CommandPalette";
import Forbidden      from "./components/Forbidden";

// Lazy: each page loads only when its route is opened, so the first paint
// no longer ships every screen (Admin, chat, markdown/highlighter, etc.).
const Chatbox        = lazy(() => import("./components/Chatbox"));
const ProfilePage    = lazy(() => import("./components/ProfilePage"));
const FormsCatalog   = lazy(() => import("./components/FormsCatalog"));
const SampleProposalsLibrary = lazy(() => import("./components/SampleProposalsLibrary"));
const OpportunityFinder = lazy(() => import("./components/OpportunityFinder"));
const MyProposals    = lazy(() => import("./components/MyProposals"));
const AdminDashboard = lazy(() => import("./components/AdminDashboard"));
const LandingPage    = lazy(() => import("./components/LandingPage"));

const SignUp         = lazy(() => import("./SignUp"));
const Login          = lazy(() => import("./Login"));
const ForgotPassword = lazy(() => import("./ForgotPassword"));
const ResetPassword  = lazy(() => import("./ResetPassword"));

import "./index.css";

import { getApiBase } from "./lib/apiBase";
const API_BASE = getApiBase();
function parseJwt(token) {
  try {
    const b64 = token.split(".")[1].replace(/-/g, "+").replace(/_/g, "/");
    const json = decodeURIComponent(
      atob(b64)
        .split("")
        .map((c) =>
          "%" + ("00" + c.charCodeAt(0).toString(16)).slice(-2)
        )
        .join("")
    );
    return JSON.parse(json);
  } catch {
    return {};
  }
}

function RequireAuth({ children }) {
  return localStorage.getItem("token")
    ? children
    : <Navigate to="/login" replace />;
}

// A lazy page chunk can fail to download when a returning (PWA-cached) visitor
// still has a stale service-worker bundle whose hashed filenames 404 after a
// redeploy. Without a boundary that left a silent blank/dark screen (the Suspense
// fallback) on mobile. This catches that failure and grabs a fresh copy ONCE
// (a 10s timestamp guard prevents a reload loop if the chunk is genuinely gone),
// falling back to a visible "Reload" card instead of a dead dark screen.
class ChunkErrorBoundary extends React.Component {
  constructor(props) {
    super(props);
    this.state = { failed: false };
  }
  static getDerivedStateFromError(error) {
    const msg = (error && (error.message || String(error))) || "";
    const isChunkError =
      /dynamically imported module|importing a module script|loading chunk|chunkloaderror|failed to fetch|unable to preload/i.test(msg);
    if (isChunkError) {
      const last = Number(sessionStorage.getItem("ora_chunk_reload_at") || 0);
      if (Date.now() - last > 10000) {
        sessionStorage.setItem("ora_chunk_reload_at", String(Date.now()));
        window.location.reload();
        return { failed: false };
      }
    }
    return { failed: true };
  }
  render() {
    if (this.state.failed) {
      return (
        <div className="route-error" role="alert">
          <h2>Something went wrong</h2>
          <p>The app couldn't finish loading. A refresh usually clears it.</p>
          <button
            type="button"
            onClick={() => {
              sessionStorage.removeItem("ora_chunk_reload_at");
              window.location.reload();
            }}
          >
            Reload
          </button>
        </div>
      );
    }
    return this.props.children;
  }
}

function ChatLayout({
  sessions,
  activeId,
  onNew,
  onSelect,
  onDelete,
  onSessionChange,
  onLogout,
  userEmail,
  onPin,
  onArchive,
  onRename,
  darkMode,
  onToggleTheme,
  onCollapse
}) {
  const activeSession = sessions.find((s) => s.id === activeId) || { messages: [] };
  return (
    <div className="app-layout">
      <ChatSidebar
        sessions={sessions}
        activeId={activeId}
        onNew={onNew}
        onSelect={onSelect}
        onDelete={onDelete}
        onLogout={onLogout}
        userEmail={userEmail}
        onPin={onPin}
        onArchive={onArchive}
        onRename={onRename}
        darkMode={darkMode}
        onToggleTheme={onToggleTheme}
        onCollapse={onCollapse}
      />
      {/* 🔥 UPDATE: Passing sessionId to Chatbox so it knows where to save */}
      <Chatbox
        key={activeId}
        sessionId={activeId} 
        initialMessages={activeSession.messages}
        onSessionChange={onSessionChange}
      />
    </div>
  );
}

function SidebarLayout({
  sessions,
  activeId,
  onNew,
  onSelect,
  onDelete,
  onLogout,
  userEmail,
  onPin,
  onArchive,
  onRename,
  darkMode,
  onToggleTheme,
  onCollapse,
  children
}) {
  return (
    <div className="app-layout">
      <ChatSidebar
        sessions={sessions}
        activeId={activeId}
        onNew={onNew}
        onSelect={onSelect}
        onDelete={onDelete}
        onLogout={onLogout}
        userEmail={userEmail}
        onPin={onPin}
        onArchive={onArchive}
        onRename={onRename}
        darkMode={darkMode}
        onToggleTheme={onToggleTheme}
        onCollapse={onCollapse}
      />
      <div className="page-content">
        {children}
      </div>
    </div>
  );
}

export default function App() {
  const navigate = useNavigate();

  const [token, setToken] = useState(() => localStorage.getItem("token"));
  const [role, setRole]   = useState(null);
  // On phones the sidebar is a slide-over panel, so start it CLOSED (login
  // lands on the welcome screen). On desktop it's a persistent column → open.
  const [sidebarCollapsed, setSidebarCollapsed] = useState(
    () => typeof window !== "undefined" && window.matchMedia("(max-width: 768px)").matches
  );
  const [cmdkOpen, setCmdkOpen] = useState(false);
  const [showWelcome, setShowWelcome] = useState(false); // disabled

  // Dark mode removed — the app is light-only.
  const [darkMode] = useState(false);

  // sync token ↔ localStorage & extract role
  useEffect(() => {
    if (token) {
      localStorage.setItem("token", token);
      const { role: r } = parseJwt(token);
      setRole(r || null);
    } else {
      localStorage.removeItem("token");
      setRole(null);
    }
  }, [token]);

  // Dark mode removed — force light and clear any saved dark preference.
  useEffect(() => {
    document.body.classList.remove("dark");
    localStorage.setItem("theme", "light");
  }, []);

  // Toggle sidebar CSS class on body
  // IMPORTANT: Also collapse sidebar when not authenticated to prevent overlay on login page
  useEffect(() => {
    const shouldCollapse = sidebarCollapsed || !token;
    document.body.classList.toggle('sidebar-collapsed', shouldCollapse);
  }, [sidebarCollapsed, token]);

  // Welcome modal disabled
  const dismissWelcome = () => setShowWelcome(false);

  // Cmd+K listener
  useEffect(() => {
    const handler = (e) => {
      if ((e.metaKey || e.ctrlKey) && e.key === 'k') {
        e.preventDefault();
        setCmdkOpen(prev => !prev);
      }
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, []);

  // chat‐session state. Past chats stay in the sidebar, but the app always
  // opens on a fresh "New Chat" (the welcome screen) — never an old conversation.
  const [sessions, setSessions] = useState(() => {
    const saved = JSON.parse(localStorage.getItem("chat_sessions") || "[]");
    const restored = saved.map(s => ({
      ...s,
      pinned: s.pinned || false,
      archived: s.archived || false
    }));
    const freshId = Date.now().toString();
    return [...restored, { id: freshId, title: "New Chat", messages: [], pinned: false, archived: false }];
  });

  const [activeId, setActiveId] = useState(sessions[sessions.length - 1]?.id || "");
  
  useEffect(() => {
    localStorage.setItem("chat_sessions", JSON.stringify(sessions));
  }, [sessions]);

  // Fetch chat history (from Cloud SQL) and group by session ID
  useEffect(() => {
    async function loadHistory() {
      if (!token) return;
      try {
        const res = await fetch(`${API_BASE}/chat-history`, {
          headers: { Authorization: `Bearer ${token}` }
        });
        if (res.ok) {
          const data = await res.json();
          
          if (data.history && data.history.length > 0) {
              const grouped = {};

              // Group the flat list of messages by their session_id
              data.history.forEach(item => {
                  const sid = item.session_id || "default";
                  if (!grouped[sid]) grouped[sid] = [];

                  // Add User Message
                  grouped[sid].push({
                    text: item.user,
                    sender: "user",
                    time: new Date(item.time).toLocaleTimeString([], {hour:'2-digit', minute:'2-digit'})
                  });

                  // Add Bot Message (with its saved Sources, so the Sources
                  // block reappears on refresh / history reload)
                  grouped[sid].push({
                    text: item.bot,
                    sender: "bot",
                    citations: item.citations || [],
                    time: new Date(item.time).toLocaleTimeString([], {hour:'2-digit', minute:'2-digit'})
                  });
              });

              // Convert the groups into your Session Objects
              const dbSessions = Object.keys(grouped).map(sid => ({
                  id: sid,
                  // Use the first message as the title (capped at 30 chars)
                  title: grouped[sid][0]?.text.slice(0, 30) || "Chat",
                  messages: grouped[sid],
                  pinned: false,
                  archived: false
              }));

              // Keep past conversations in the sidebar, but always open on a
              // fresh New Chat so the user lands on the welcome screen.
              const freshId = Date.now().toString();
              setSessions([
                ...dbSessions,
                { id: freshId, title: "New Chat", messages: [], pinned: false, archived: false }
              ]);
              setActiveId(freshId);
          } else {
              // New account or no history - reset to a fresh session
              // This clears any stale sessions from a previous account
              const freshId = Date.now().toString();
              setSessions([{ id: freshId, title: "New Chat", messages: [], pinned: false, archived: false }]);
              setActiveId(freshId);
          }
        }
      } catch (err) {
        console.error("Failed to load persistent chat history:", err);
      }
    }
    loadHistory();
  }, [token]); // Run once when token changes (login)

  // On phones the sidebar is an overlay covering the chat — close it after a
  // navigation action so the user actually SEES the fresh/selected chat.
  // (No-op on desktop, where the sidebar is a persistent column.)
  const collapseSidebarOnMobile = () => {
    if (typeof window !== "undefined" && window.matchMedia("(max-width: 768px)").matches) {
      setSidebarCollapsed(true);
    }
  };

  // FIXED: session handlers
  const handleNew = () => {
    const id = Date.now().toString();
    const newChat = { id, title: "New Chat", messages: [], pinned: false, archived: false };
    setSessions((prev) => [...prev, newChat]); // Append to end
    setActiveId(id);
    navigate("/chat");
    collapseSidebarOnMobile();
  };

  const handleSelect = (id) => {
    setActiveId(id);
    navigate("/chat");
    collapseSidebarOnMobile();
  };
  
  const handleDelete = async (id) => {
    if (!window.confirm("Delete this chat permanently?")) return;
    const next = sessions.filter((s) => s.id !== id);
    setSessions(next);
    if (activeId === id) setActiveId(next[0]?.id || "");
    try {
      await fetch(`${API_BASE}/api/sessions/${id}`, {
        method: "DELETE",
        headers: { Authorization: `Bearer ${localStorage.getItem("token")}` },
      });
    } catch (err) {
      console.error("Failed to delete session from server:", err);
    }
  };
  
  // 🔥 FIXED: Prevent infinite re-renders by checking if messages actually changed
  const handleUpdateSession = (msgs) => {
    setSessions((prev) => {
      const currentSession = prev.find((s) => s.id === activeId);
      
      // Only update if messages actually changed
      if (currentSession && JSON.stringify(currentSession.messages) === JSON.stringify(msgs)) {
        return prev; // No change needed, return same reference
      }
      
      return prev.map((s) =>
        s.id === activeId
          ? {
              ...s,
              messages: msgs,
              title: msgs.length > 0 ? (msgs[0]?.text.slice(0, 30) || "New Chat") : "New Chat",
            }
          : s
      );
    });
  };

  // Pin/Unpin handler
  const handlePin = (id) => {
    setSessions((prev) =>
      prev.map((s) =>
        s.id === id ? { ...s, pinned: !s.pinned } : s
      )
    );
  };

  // Archive handler
  const handleArchive = (id) => {
    setSessions((prev) =>
      prev.map((s) =>
        s.id === id ? { ...s, archived: !s.archived } : s
      )
    );
    if (id === activeId) {
      const remaining = sessions.filter(s => s.id !== id && !s.archived);
      setActiveId(remaining[0]?.id || "");
    }
  };

  // Rename handler
  const handleRename = (id, newTitle) => {
    setSessions((prev) =>
      prev.map((s) =>
        s.id === id ? { ...s, title: newTitle } : s
      )
    );
  };

  // Sidebar toggle function
  const toggleSidebar = () => {
    setSidebarCollapsed(prev => !prev);
  };

  // Dark mode removed — no-op kept so existing props don't break.
  const toggleTheme = () => {};

  // logout
  const handleLogout = () => {
    setToken(null);
    localStorage.removeItem("token");
    localStorage.removeItem("chat_sessions");
    // Clear UI and reset to a fresh chat
    const freshId = Date.now().toString();
    setSessions([{ id: freshId, title: "New Chat", messages: [], pinned: false, archived: false }]);
    setActiveId(freshId);
    navigate("/login", { replace: true });
  };

  // Extract user email from token
  const userEmail = token ? (parseJwt(token).email || parseJwt(token).sub || "User") : "";

  return (
    <>
      <Toaster position="top-center" richColors />
      {/* WelcomeModal removed */}
      <CommandPalette
        open={cmdkOpen}
        onOpenChange={setCmdkOpen}
        onNewChat={handleNew}
        onToggleTheme={toggleTheme}
        onNavigate={navigate}
        role={role}
        darkMode={darkMode}
      />
      <NavBar
        role={role}
        onLogout={handleLogout}
        onToggleSidebar={toggleSidebar}
      />

      <ChunkErrorBoundary>
      <Suspense fallback={<div className="route-loading" aria-busy="true" aria-label="Loading…" />}>
      <Routes>
        {/* public */}
        <Route
          path="/signup"
          element={
            <SignUp onRegistered={() => navigate("/login", { replace: true })} />
          }
        />
        <Route
          path="/login"
          element={
            <Login
              onLoggedIn={(tk) => {
                setToken(tk);
                navigate("/chat", { replace: true });
              }}
            />
          }
        />

        <Route path="/forgot-password" element={<ForgotPassword />} />
        <Route path="/reset-password" element={<ResetPassword />} />

        {/* public: open guest chat (free, no trial/limit) */}
        <Route
          path="/trychat"
          element={<LandingPage />}
        />

        {/* root redirects to /trychat or /chat based on auth */}
        <Route
          path="/"
          element={<Navigate to={token ? "/chat" : "/trychat"} replace />}
        />

        {/* protected: chat */}
        <Route
          path="/chat"
          element={
            <RequireAuth>
              <ChatLayout
                sessions={sessions}
                activeId={activeId}
                onNew={handleNew}
                onSelect={handleSelect}
                onDelete={handleDelete}
                onSessionChange={handleUpdateSession}
                onLogout={handleLogout}
                userEmail={userEmail}
                onPin={handlePin}
                onArchive={handleArchive}
                onRename={handleRename}
                darkMode={darkMode}
                onToggleTheme={toggleTheme}
                onCollapse={toggleSidebar}
              />
            </RequireAuth>
          }
        />

        {/* protected: my classes with sidebar */}
        {/* protected: profile page with sidebar */}
        <Route
          path="/profile"
          element={
            <RequireAuth>
              <SidebarLayout
                sessions={sessions}
                activeId={activeId}
                onNew={handleNew}
                onSelect={handleSelect}
                onDelete={handleDelete}
                onLogout={handleLogout}
                userEmail={userEmail}
                onPin={handlePin}
                onArchive={handleArchive}
                onRename={handleRename}
                darkMode={darkMode}
                onToggleTheme={toggleTheme}
                onCollapse={toggleSidebar}
              >
                <ProfilePage userEmail={userEmail} onLogout={handleLogout} />
              </SidebarLayout>
            </RequireAuth>
          }
        />

        {/* protected: forms catalog */}
        <Route
          path="/forms"
          element={
            <RequireAuth>
              <SidebarLayout
                sessions={sessions}
                activeId={activeId}
                onNew={handleNew}
                onSelect={handleSelect}
                onDelete={handleDelete}
                onLogout={handleLogout}
                userEmail={userEmail}
                onPin={handlePin}
                onArchive={handleArchive}
                onRename={handleRename}
                darkMode={darkMode}
                onToggleTheme={toggleTheme}
                onCollapse={toggleSidebar}
              >
                <FormsCatalog />
              </SidebarLayout>
            </RequireAuth>
          }
        />

        {/* protected: sample proposals library */}
        <Route
          path="/sample-proposals"
          element={
            <RequireAuth>
              <SidebarLayout
                sessions={sessions}
                activeId={activeId}
                onNew={handleNew}
                onSelect={handleSelect}
                onDelete={handleDelete}
                onLogout={handleLogout}
                userEmail={userEmail}
                onPin={handlePin}
                onArchive={handleArchive}
                onRename={handleRename}
                darkMode={darkMode}
                onToggleTheme={toggleTheme}
                onCollapse={toggleSidebar}
              >
                <SampleProposalsLibrary />
              </SidebarLayout>
            </RequireAuth>
          }
        />

        {/* protected: opportunity finder */}
        <Route
          path="/opportunities"
          element={
            <RequireAuth>
              <SidebarLayout
                sessions={sessions}
                activeId={activeId}
                onNew={handleNew}
                onSelect={handleSelect}
                onDelete={handleDelete}
                onLogout={handleLogout}
                userEmail={userEmail}
                onPin={handlePin}
                onArchive={handleArchive}
                onRename={handleRename}
                darkMode={darkMode}
                onToggleTheme={toggleTheme}
                onCollapse={toggleSidebar}
              >
                <OpportunityFinder />
              </SidebarLayout>
            </RequireAuth>
          }
        />

        {/* protected: my proposals tracker */}
        <Route
          path="/my-proposals"
          element={
            <RequireAuth>
              <SidebarLayout
                sessions={sessions}
                activeId={activeId}
                onNew={handleNew}
                onSelect={handleSelect}
                onDelete={handleDelete}
                onLogout={handleLogout}
                userEmail={userEmail}
                onPin={handlePin}
                onArchive={handleArchive}
                onRename={handleRename}
                darkMode={darkMode}
                onToggleTheme={toggleTheme}
                onCollapse={toggleSidebar}
              >
                <MyProposals />
              </SidebarLayout>
            </RequireAuth>
          }
        />

        {/* protected: admin */}
        <Route
          path="/admin"
          element={
            <RequireAuth>
              {role === "admin" ? <AdminDashboard /> : <Forbidden />}
            </RequireAuth>
          }
        />

        {/* fallback */}
        <Route
          path="*"
          element={<Navigate to={token ? "/chat" : "/trychat"} replace />}
        />
      </Routes>
      </Suspense>
      </ChunkErrorBoundary>
    </>
  );
}