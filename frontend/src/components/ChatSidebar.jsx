import React, { useState, useEffect, useRef } from "react";
import { useNavigate } from "react-router-dom";
import { toast } from 'sonner';
import { FaPlus } from "@react-icons/all-files/fa/FaPlus";
import { FaSearch } from "@react-icons/all-files/fa/FaSearch";
import { FaBook } from "@react-icons/all-files/fa/FaBook";
import { FaChalkboardTeacher } from "@react-icons/all-files/fa/FaChalkboardTeacher";
import { FaChartLine } from "@react-icons/all-files/fa/FaChartLine";
import { FaProjectDiagram } from "@react-icons/all-files/fa/FaProjectDiagram";
import { FaTrash } from "@react-icons/all-files/fa/FaTrash";
import { FaUser } from "@react-icons/all-files/fa/FaUser";
import { FaSignOutAlt } from "@react-icons/all-files/fa/FaSignOutAlt";
import { FaEllipsisV } from "@react-icons/all-files/fa/FaEllipsisV";
import { FaThumbtack } from "@react-icons/all-files/fa/FaThumbtack";
import { FaArchive } from "@react-icons/all-files/fa/FaArchive";
import { FaPencilAlt } from "@react-icons/all-files/fa/FaPencilAlt";
import { FaMoon } from "@react-icons/all-files/fa/FaMoon";
import { FaSun } from "@react-icons/all-files/fa/FaSun";
import { FaDownload } from "@react-icons/all-files/fa/FaDownload";
import { FaChevronRight } from "@react-icons/all-files/fa/FaChevronRight";
import { FaHeadset } from "@react-icons/all-files/fa/FaHeadset";
import { FaTimes } from "@react-icons/all-files/fa/FaTimes";
import { FaBug } from "@react-icons/all-files/fa/FaBug";
import { FaLightbulb } from "@react-icons/all-files/fa/FaLightbulb";
import { FaQuestionCircle } from "@react-icons/all-files/fa/FaQuestionCircle";
import { FaPaperclip } from "@react-icons/all-files/fa/FaPaperclip";
import { FaCheckCircle } from "@react-icons/all-files/fa/FaCheckCircle";
import { FaGithub } from "@react-icons/all-files/fa/FaGithub";
import { getApiBase } from "../lib/apiBase";
import "./ChatSidebar.css";

export default function ChatSidebar({
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
  onCollapse
}) {
  const [searchQuery, setSearchQuery] = useState("");
  const [contextMenu, setContextMenu] = useState({ visible: false, x: 0, y: 0, sessionId: null });
  const [renamingId, setRenamingId] = useState(null);
  const [renameValue, setRenameValue] = useState("");
  const [showArchived, setShowArchived] = useState(false);
  const [userProfile, setUserProfile] = useState(null);
  const [profileImageUrl, setProfileImageUrl] = useState(null);
  const navigate = useNavigate();

  // Support Ticket Modal State
  const [showTicketModal, setShowTicketModal] = useState(false);
  const [ticketForm, setTicketForm] = useState({
    subject: "",
    category: "bug",
    description: "",
    attachment: null,
    attachmentName: ""
  });
  const [ticketSubmitting, setTicketSubmitting] = useState(false);
  const [ticketSuccess, setTicketSuccess] = useState(false);

  const API_BASE = getApiBase();

  // PWA install prompt
  const deferredPromptRef = useRef(null);
  const [canInstall, setCanInstall] = useState(false);

  useEffect(() => {
    const handler = (e) => {
      e.preventDefault();
      deferredPromptRef.current = e;
      setCanInstall(true);
    };
    window.addEventListener('beforeinstallprompt', handler);
    return () => window.removeEventListener('beforeinstallprompt', handler);
  }, []);

  // 🔥 Fetch user profile on mount - PRESERVED
  useEffect(() => {
    fetchUserProfile();
  }, []);

  const fetchUserProfile = async () => {
    const token = localStorage.getItem("token");
    if (!token) return;

    try {
      const response = await fetch(`${API_BASE}/api/profile`, {
        headers: {
          Authorization: `Bearer ${token}`,
        },
      });

      if (response.ok) {
        const data = await response.json();
        setUserProfile(data);

        // 🔥 FIXED: Handle base64 data URLs, full URLs, and relative paths
        if (data.profilePicture) {
          let imageUrl = data.profilePicture;
          if (imageUrl.startsWith('data:')) {
            // Base64 data URL - use directly
            setProfileImageUrl(imageUrl);
          } else if (imageUrl.startsWith('http')) {
            // Full URL - use directly
            setProfileImageUrl(imageUrl);
          } else if (!imageUrl.startsWith('/user_icon.webp')) {
            // Relative path - prepend API base
            setProfileImageUrl(`${API_BASE}${imageUrl}`);
          }
        }
      }
    } catch (error) {
      console.error("❌ Error fetching profile:", error);
    }
  };

  // Filter logic - PRESERVED
  const filteredSessions = sessions.filter(s =>
    !s.archived && s.title.toLowerCase().includes(searchQuery.toLowerCase())
  );
  const archivedSessions = sessions.filter(s => s.archived);
  const pinnedSessions = filteredSessions.filter(s => s.pinned);
  const regularSessions = filteredSessions.filter(s => !s.pinned);

  // Date grouping for chat history
  const groupSessionsByDate = (sessions) => {
    const now = new Date();
    const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
    const yesterday = new Date(today); yesterday.setDate(today.getDate() - 1);
    const weekAgo = new Date(today); weekAgo.setDate(today.getDate() - 7);

    const groups = { "Today": [], "Yesterday": [], "Previous 7 Days": [], "Older": [] };

    sessions.forEach(s => {
      const ts = parseInt(s.id);
      if (isNaN(ts)) { groups["Older"].push(s); return; }
      const date = new Date(ts);
      if (date >= today) groups["Today"].push(s);
      else if (date >= yesterday) groups["Yesterday"].push(s);
      else if (date >= weekAgo) groups["Previous 7 Days"].push(s);
      else groups["Older"].push(s);
    });

    return groups;
  };

  const dateGroups = groupSessionsByDate(regularSessions);

  const handleContextMenu = (e, sessionId) => {
    e.preventDefault();
    e.stopPropagation();
    setContextMenu({ visible: true, x: e.clientX, y: e.clientY, sessionId });
  };

  const closeContextMenu = () => {
    setContextMenu({ visible: false, x: 0, y: 0, sessionId: null });
  };

  const handleRename = (sessionId, currentTitle) => {
    setRenamingId(sessionId);
    setRenameValue(currentTitle);
    closeContextMenu();
  };

  const submitRename = (sessionId) => {
    if (renameValue.trim()) onRename(sessionId, renameValue.trim());
    setRenamingId(null);
    setRenameValue("");
  };

  const handleInstallApp = async () => {
    if (deferredPromptRef.current) {
      deferredPromptRef.current.prompt();
      const { outcome } = await deferredPromptRef.current.userChoice;
      if (outcome === 'accepted') {
        toast.success("App installed!");
        setCanInstall(false);
      }
      deferredPromptRef.current = null;
    } else {
      const isSafari = /^((?!chrome|android).)*safari/i.test(navigator.userAgent);
      const isIOS = /iPad|iPhone|iPod/.test(navigator.userAgent);
      if (isIOS || isSafari) {
        toast("Tap the Share button in Safari, then tap 'Add to Home Screen'.", { duration: 6000 });
      } else {
        toast("Click the install icon in your browser's address bar, or use the menu to 'Install App'.", { duration: 5000 });
      }
    }
  };

  // 🎫 Support Ticket Handlers
  const compressImage = (file, maxWidth = 1200, quality = 0.7) => {
    return new Promise((resolve) => {
      // Non-image files pass through as-is
      if (!file.type.startsWith("image/")) {
        const reader = new FileReader();
        reader.onloadend = () => resolve(reader.result);
        reader.readAsDataURL(file);
        return;
      }
      const img = new Image();
      img.onload = () => {
        const canvas = document.createElement("canvas");
        let w = img.width, h = img.height;
        if (w > maxWidth) { h = (h * maxWidth) / w; w = maxWidth; }
        canvas.width = w;
        canvas.height = h;
        canvas.getContext("2d").drawImage(img, 0, 0, w, h);
        resolve(canvas.toDataURL("image/jpeg", quality));
      };
      img.src = URL.createObjectURL(file);
    });
  };

  const handleTicketAttachment = async (e) => {
    const file = e.target.files[0];
    if (file) {
      if (file.size > 10 * 1024 * 1024) {
        toast.warning("File size must be under 10MB");
        return;
      }
      const compressed = await compressImage(file);
      setTicketForm(prev => ({
        ...prev,
        attachment: compressed,
        attachmentName: file.name
      }));
    }
  };

  const handleTicketSubmit = async (e) => {
    e.preventDefault();
    if (!ticketForm.subject.trim() || !ticketForm.description.trim()) {
      toast.warning("Please fill in subject and description");
      return;
    }

    setTicketSubmitting(true);
    const token = localStorage.getItem("token");

    try {
      const response = await fetch(`${API_BASE}/api/tickets`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${token}`,
        },
        body: JSON.stringify({
          subject: ticketForm.subject,
          category: ticketForm.category,
          description: ticketForm.description,
          attachment_data: ticketForm.attachment,
          attachment_name: ticketForm.attachmentName
        }),
      });

      if (response.ok) {
        setTicketSuccess(true);
        setTimeout(() => {
          setShowTicketModal(false);
          setTicketSuccess(false);
          setTicketForm({
            subject: "",
            category: "bug",
            description: "",
            attachment: null,
            attachmentName: ""
          });
        }, 2000);
      } else {
        const data = await response.json();
        toast.error(data.detail || "Failed to submit ticket");
      }
    } catch (error) {
      console.error("Error submitting ticket:", error);
      toast.error("Failed to submit ticket. Please try again.");
    } finally {
      setTicketSubmitting(false);
    }
  };

  const closeTicketModal = () => {
    setShowTicketModal(false);
    setTicketSuccess(false);
    setTicketForm({
      subject: "",
      category: "bug",
      description: "",
      attachment: null,
      attachmentName: ""
    });
  };
  
  const handleCurriculumClick = (e) => {
    e.preventDefault();
    e.stopPropagation();
    closeContextMenu();
    navigate("/curriculum");
  };

  const handleMyClassesClick = (e) => {
    e.preventDefault();
    e.stopPropagation();
    closeContextMenu();
    navigate("/my-classes");
  };

  const handleProfileClick = (e) => {
    e.preventDefault();
    e.stopPropagation();
    closeContextMenu();
    navigate("/profile");
  };

  useEffect(() => {
    if (contextMenu.visible) {
      const handler = (e) => {
        if (!e.target.closest('.context-menu')) closeContextMenu();
      };
      window.addEventListener('click', handler);
      return () => window.removeEventListener('click', handler);
    }
  }, [contextMenu.visible]);

  const renderChatItem = (s, isArchived = false) => {
    if (renamingId === s.id) {
      return (
        <div key={s.id} className="chat-history-item">
          <input
            type="text"
            className="rename-input"
            value={renameValue}
            onChange={(e) => setRenameValue(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') submitRename(s.id);
              if (e.key === 'Escape') setRenamingId(null);
            }}
            onBlur={() => submitRename(s.id)}
            autoFocus
          />
        </div>
      );
    }

    return (
      <div
        key={s.id}
        className={`chat-history-item ${s.id === activeId ? "active" : ""} ${isArchived ? "archived-item" : ""}`}
        onClick={() => onSelect(s.id)}
        onContextMenu={(e) => handleContextMenu(e, s.id)}
        title={`Select conversation: ${s.title}`} // 🔥 NEW: Hover Text
      >
        {s.pinned && <FaThumbtack className="pin-icon" size={10} />}
        <span className="chat-title">{s.title}</span>
        <button
          className="chat-menu-btn"
          onClick={(e) => {
            e.stopPropagation();
            handleContextMenu(e, s.id);
          }}
          title="Chat options" // 🔥 NEW: Hover Text
          aria-label="More options"
        >
          <FaEllipsisV size={12} />
        </button>
      </div>
    );
  };

  return (
    <div className="chat-sidebar">
      <div className="sidebar-top">
        <div className="sidebar-top-row">
          <button
            className="sidebar-action-btn new-chat"
            onClick={onNew}
            title="Start a new chat session"
            style={{ flex: 1 }}
          >
            <FaPlus size={16} />
            <span>New Chat</span>
          </button>
          {onCollapse && (
            <button className="sidebar-toggle-btn" onClick={onCollapse} title="Close sidebar">
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <rect x="3" y="3" width="18" height="18" rx="2" ry="2"/>
                <line x1="9" y1="3" x2="9" y2="21"/>
              </svg>
            </button>
          )}
        </div>

        <div className="search-container" title="Search through your conversations"> {/* 🔥 NEW: Hover Text */}
          <FaSearch className="search-icon" size={14} />
          <input
            type="text"
            className="search-input"
            placeholder="Search chats..."
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
          />
        </div>

        <button
          className="sidebar-action-btn curriculum-link"
          onClick={handleMyClassesClick}
          title="View your Canvas courses, assignments, and grades"
        >
          <FaChalkboardTeacher size={16} />
          <span>My Classes</span>
        </button>
        <button
          className="sidebar-action-btn curriculum-link"
          onClick={handleCurriculumClick}
          title="View Computer Science curriculum"
        >
          <FaBook size={16} />
          <span>Curriculum</span>
        </button>
        <button
          className="sidebar-action-btn curriculum-link"
          onClick={(e) => { e.preventDefault(); navigate("/grade-analysis"); }}
          title="Grade analysis and strategy"
        >
          <FaChartLine size={16} />
          <span>Grade Surgeon</span>
        </button>
        <button
          className="sidebar-action-btn curriculum-link"
          onClick={(e) => { e.preventDefault(); navigate("/ripple-effect"); }}
          title="Prerequisite dependency map"
        >
          <FaProjectDiagram size={16} />
          <span>Ripple Effect</span>
        </button>
      </div>

      <div className="sidebar-middle">
        {pinnedSessions.length > 0 && (
          <>
            <div className="section-header">Pinned</div>
            <div className="chat-history-section">
              {pinnedSessions.map(s => renderChatItem(s))}
            </div>
          </>
        )}

        <div className="chat-history-list">
          {regularSessions.length === 0 ? (
            <div className="empty-state">No chats found</div>
          ) : (
            Object.entries(dateGroups).map(([label, items]) =>
              items.length > 0 && (
                <React.Fragment key={label}>
                  <div className="date-group-header">{label}</div>
                  {items.map(s => renderChatItem(s))}
                </React.Fragment>
              )
            )
          )}
        </div>

        {archivedSessions.length > 0 && (
          <div className="archived-section-container">
            <button 
              className="archived-header"
              onClick={() => setShowArchived(!showArchived)}
              title="Toggle archived conversations" // 🔥 NEW: Hover Text
            >
              <FaArchive size={14} />
              <span>Archived ({archivedSessions.length})</span>
              <FaChevronRight 
                size={12} 
                className={`chevron-icon ${showArchived ? 'rotated' : ''}`}
              />
            </button>
            {showArchived && (
              <div className="archived-list">
                {archivedSessions.map(s => renderChatItem(s, true))}
              </div>
            )}
          </div>
        )}
      </div>

      {contextMenu.visible && (
        <div className="context-menu" style={{ top: contextMenu.y, left: contextMenu.x }} onClick={(e) => e.stopPropagation()}>
          <button className="context-menu-item" onClick={() => { onPin(contextMenu.sessionId); closeContextMenu(); }}>
            <FaThumbtack size={14} />
            <span>{sessions.find(s => s.id === contextMenu.sessionId)?.pinned ? 'Unpin' : 'Pin'} chat</span>
          </button>
          <button className="context-menu-item" onClick={() => {
              const session = sessions.find(s => s.id === contextMenu.sessionId);
              handleRename(contextMenu.sessionId, session?.title || '');
          }}>
            <FaPencilAlt size={14} />
            <span>Rename</span>
          </button>
          <button className="context-menu-item" onClick={() => { onArchive(contextMenu.sessionId); closeContextMenu(); }}>
            <FaArchive size={14} />
            <span>{sessions.find(s => s.id === contextMenu.sessionId)?.archived ? 'Unarchive' : 'Archive'}</span>
          </button>
          <div className="context-menu-divider" />
          <button className="context-menu-item danger" onClick={() => { onDelete(contextMenu.sessionId); closeContextMenu(); }}>
            <FaTrash size={14} />
            <span>Delete</span>
          </button>
        </div>
      )}

      <div className="sidebar-bottom">
        <div className="sidebar-settings-wrapper">
          <button
            className="setting-btn support-btn full-width"
            onClick={() => setShowTicketModal(true)}
            title="Report a bug or request a feature"
          >
            <FaHeadset size={18} />
            <span>Contact Support</span>
          </button>

          <div className="sidebar-settings-row">
            <button
              className="setting-btn"
              onClick={onToggleTheme}
              title={darkMode ? "Switch to light mode" : "Switch to dark mode"}
            >
              {darkMode ? <FaSun size={18} /> : <FaMoon size={18} />}
              <span>{darkMode ? "Light" : "Dark"} Mode</span>
            </button>

            <button
              className="setting-btn install-app-btn"
              onClick={handleInstallApp}
              title="Download desktop application"
            >
              <FaDownload size={18} />
              <span>Install App</span>
            </button>
          </div>
        </div>

        <div 
          className="user-profile" 
          onClick={handleProfileClick}
          title="Open your profile and account settings" // 🔥 NEW: Hover Text
        >
          <div className="user-avatar">
            {profileImageUrl ? (
              <>
                <img 
                  src={profileImageUrl} 
                  alt="Profile" 
                  className="profile-picture"
                  onError={(e) => {
                    e.target.style.display = 'none';
                    const fallback = e.target.parentElement.querySelector('.fallback-user-icon');
                    if (fallback) fallback.style.display = 'flex';
                  }}
                />
                <div className="fallback-user-icon" style={{ display: 'none' }}><FaUser size={18} /></div>
              </>
            ) : (
              <div className="fallback-user-icon"><FaUser size={18} /></div>
            )}
          </div>
          <div className="user-info">
            <div className="user-email">{userProfile?.email || userEmail || "User"}</div>
            <div className="user-status">Free Plan</div>
          </div>
          <button
            className="logout-icon-btn"
            onClick={(e) => {
              e.stopPropagation();
              onLogout();
            }}
            title="Sign out of CS Navigator"
          >
            <FaSignOutAlt size={16} />
          </button>
        </div>
        <a className="sidebar-opensource-badge" href="https://github.com/theaayushstha1/cs-navigator" target="_blank" rel="noopener noreferrer">
          <FaGithub size={10} />
          <span>Open Source</span>
        </a>
      </div>

      {/* 🎫 Support Ticket Modal */}
      {showTicketModal && (
        <div className="ticket-modal-overlay" onClick={closeTicketModal}>
          <div className="ticket-modal" onClick={(e) => e.stopPropagation()}>
            {ticketSuccess ? (
              <div className="ticket-success">
                <FaCheckCircle size={48} className="success-icon" />
                <h3>Ticket Submitted!</h3>
                <p>We'll review your feedback and get back to you soon.</p>
              </div>
            ) : (
              <>
                <div className="ticket-header">
                  <h2>Contact Support</h2>
                  <button className="ticket-close-btn" onClick={closeTicketModal}>
                    <FaTimes size={18} />
                  </button>
                </div>

                <form onSubmit={handleTicketSubmit} className="ticket-form">
                  <div className="ticket-field">
                    <label>Category</label>
                    <div className="category-options">
                      <button
                        type="button"
                        className={`category-btn ${ticketForm.category === 'bug' ? 'active' : ''}`}
                        onClick={() => setTicketForm(prev => ({ ...prev, category: 'bug' }))}
                      >
                        <FaBug size={16} />
                        <span>Bug Report</span>
                      </button>
                      <button
                        type="button"
                        className={`category-btn ${ticketForm.category === 'feature' ? 'active' : ''}`}
                        onClick={() => setTicketForm(prev => ({ ...prev, category: 'feature' }))}
                      >
                        <FaLightbulb size={16} />
                        <span>Feature Request</span>
                      </button>
                      <button
                        type="button"
                        className={`category-btn ${ticketForm.category === 'question' ? 'active' : ''}`}
                        onClick={() => setTicketForm(prev => ({ ...prev, category: 'question' }))}
                      >
                        <FaQuestionCircle size={16} />
                        <span>Question</span>
                      </button>
                    </div>
                  </div>

                  <div className="ticket-field">
                    <label htmlFor="ticket-subject">Subject</label>
                    <input
                      id="ticket-subject"
                      type="text"
                      placeholder="Brief description of your issue..."
                      value={ticketForm.subject}
                      onChange={(e) => setTicketForm(prev => ({ ...prev, subject: e.target.value }))}
                      required
                    />
                  </div>

                  <div className="ticket-field">
                    <label htmlFor="ticket-description">Description</label>
                    <textarea
                      id="ticket-description"
                      placeholder="Please provide details about your issue or suggestion..."
                      value={ticketForm.description}
                      onChange={(e) => setTicketForm(prev => ({ ...prev, description: e.target.value }))}
                      rows={4}
                      required
                    />
                  </div>

                  <div className="ticket-field">
                    <label>Attachment (Optional)</label>
                    <div className="attachment-area">
                      <input
                        type="file"
                        id="ticket-attachment"
                        accept="image/*,.pdf,.txt,.doc,.docx"
                        onChange={handleTicketAttachment}
                        style={{ display: 'none' }}
                      />
                      <label htmlFor="ticket-attachment" className="attachment-btn">
                        <FaPaperclip size={16} />
                        <span>{ticketForm.attachmentName || "Attach file (max 5MB)"}</span>
                      </label>
                      {ticketForm.attachmentName && (
                        <button
                          type="button"
                          className="remove-attachment"
                          onClick={() => setTicketForm(prev => ({ ...prev, attachment: null, attachmentName: "" }))}
                        >
                          <FaTimes size={12} />
                        </button>
                      )}
                    </div>
                  </div>

                  <button
                    type="submit"
                    className="ticket-submit-btn"
                    disabled={ticketSubmitting}
                  >
                    {ticketSubmitting ? "Submitting..." : "Submit Ticket"}
                  </button>
                </form>
              </>
            )}
          </div>
        </div>
      )}
    </div>
  );
}