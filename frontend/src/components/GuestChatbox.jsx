import React, { useState, useEffect, useRef } from "react";
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { useNavigate } from "react-router-dom";
import { BsArrowUpCircleFill, BsClock, BsCheckCircle, BsX } from "react-icons/bs";
import "./GuestChatbox.css";

// Default suggestions for guests
const GUEST_SUGGESTIONS = [
  "What degrees does the CS department offer?",
  "What are the prerequisites for COSC 220 Data Structures?",
  "Who are the faculty in the CS department?",
  "How many credits do I need to graduate with a B.S. in CS?",
  "What's the 4+1 accelerated B.S./M.S. program?",
  "Where can I find tutoring and academic support?"
];

import { getApiBase } from "../lib/apiBase";
const API_BASE = getApiBase();

// Session duration: 15 minutes in milliseconds
const GUEST_SESSION_DURATION = 15 * 60 * 1000;
const MAX_INPUT_LENGTH = 500;

// Guest profile - no fake academic data to prevent false answers
const generateGuestProfile = () => {
  localStorage.removeItem("guest_profile"); // clean up old fake profiles
  return { name: "Guest User" };
};

// Format time remaining as MM:SS
const formatTimeRemaining = (ms) => {
  if (ms <= 0) return "00:00";
  const totalSeconds = Math.ceil(ms / 1000);
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  return `${minutes.toString().padStart(2, '0')}:${seconds.toString().padStart(2, '0')}`;
};

// Get timer urgency state
const getTimerUrgency = (ms) => {
  if (ms <= 0) return "expired";
  if (ms <= 2 * 60 * 1000) return "critical"; // 2 minutes
  if (ms <= 5 * 60 * 1000) return "warning";  // 5 minutes
  return "normal";
};

export default function GuestChatbox() {
  const navigate = useNavigate();

  // State
  const [messages, setMessages] = useState([]);
  const [input, setInput] = useState("");
  const [isLoading, setIsLoading] = useState(false);
  const [guestProfile] = useState(generateGuestProfile);

  // Timer state
  const [sessionStartTime, setSessionStartTime] = useState(() => {
    const saved = localStorage.getItem("guest_session_start");
    return saved ? parseInt(saved, 10) : null;
  });
  const [timeRemaining, setTimeRemaining] = useState(GUEST_SESSION_DURATION);
  const [showSignUpModal, setShowSignUpModal] = useState(false);
  const [bonusUsed, setBonusUsed] = useState(() => localStorage.getItem("guest_bonus_used") === "true");

  // Refs
  const messagesEndRef = useRef(null);
  const inputRef = useRef(null);

  // Computed state
  const isSessionExpired = timeRemaining <= 0;
  const timerUrgency = getTimerUrgency(timeRemaining);
  const hasSessionStarted = sessionStartTime !== null;

  // Focus input on load
  useEffect(() => {
    inputRef.current?.focus();
  }, []);

  // Auto-scroll to bottom
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  // Timer countdown effect
  useEffect(() => {
    if (!sessionStartTime) return;

    const updateTimer = () => {
      const elapsed = Date.now() - sessionStartTime;
      const remaining = Math.max(0, GUEST_SESSION_DURATION - elapsed);
      setTimeRemaining(remaining);

      // Auto-show modal when expired
      if (remaining <= 0 && !showSignUpModal) {
        setShowSignUpModal(true);
      }
    };

    updateTimer(); // Initial update
    const interval = setInterval(updateTimer, 1000);

    return () => clearInterval(interval);
  }, [sessionStartTime, showSignUpModal]);

  // Start session (called on first message)
  const startSession = () => {
    if (!sessionStartTime) {
      const now = Date.now();
      setSessionStartTime(now);
      localStorage.setItem("guest_session_start", now.toString());
    }
  };

  // Helper to add message
  const addMessage = (text, sender) => {
    const time = new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
    setMessages((prev) => [...prev, { text, sender, time }]);
  };

  // Handle suggestion click
  const handleSuggestion = (text) => {
    if (!isLoading && !isSessionExpired) {
      // Directly send instead of just filling input
      setInput('');
      startSession();

      const userMessage = text.trim();
      setMessages(prev => [...prev, { text: userMessage, sender: "user", time: new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) }]);
      setIsLoading(true);

      (async () => {
        try {
          const res = await fetch(`${API_BASE}/chat/guest`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ query: userMessage, guestProfile: guestProfile || {} }),
          });
          const data = await res.json();
          const botReply = data.response || "I couldn't process that. Please try again.";
          setMessages(prev => [...prev, { text: botReply, sender: "bot", time: new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) }]);
        } catch {
          setMessages(prev => [...prev, { text: "Something went wrong. Please try again.", sender: "bot", time: new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) }]);
        } finally {
          setIsLoading(false);
        }
      })();
    }
  };

  // Main send handler
  const handleSend = async (e) => {
    e.preventDefault();
    if (!input.trim() || isLoading || isSessionExpired) return;

    // Start session on first message
    startSession();

    const userMessage = input.trim();
    setIsLoading(true);

    addMessage(userMessage, "user");
    setInput("");

    try {
      const res = await fetch(`${API_BASE}/chat/guest`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          query: userMessage,
          guestProfile: guestProfile
        }),
      });

      if (res.status === 429) {
        addMessage("You've reached the rate limit. Please wait a moment or sign up for unlimited access!", "bot");
        return;
      }

      if (!res.ok) throw new Error(res.statusText);

      const data = await res.json();
      const botResponse = data.response || "No response.";
      addMessage(botResponse, "bot");

    } catch (err) {
      console.error("Guest chat error:", err);
      addMessage("Sorry, I had trouble processing that. Please try again.", "bot");
    } finally {
      setIsLoading(false);
      setTimeout(() => inputRef.current?.focus(), 100);
    }
  };

  return (
    <div className="guest-chat-main">
      {/* Trial timer bar - always visible at top */}
      <div className={`guest-trial-bar ${timerUrgency} ${isSessionExpired ? 'expired' : ''}`}>
        <div className="trial-bar-content">
          {isSessionExpired ? (
            <>
              <span className="trial-text">Your free trial has ended</span>
              <button onClick={() => setShowSignUpModal(true)} className="trial-cta">
                Create Free Account for Unlimited
              </button>
            </>
          ) : hasSessionStarted ? (
            <>
              <div className="timer-display">
                <BsClock className="timer-icon" />
                <span className="timer-countdown">{formatTimeRemaining(timeRemaining)}</span>
                <span className="timer-label">remaining</span>
              </div>
              <span className="trial-divider">|</span>
              <button onClick={() => navigate("/signup")} className="trial-link">
                Create an account for unlimited access
              </button>
            </>
          ) : (
            <>
              <span className="trial-text">
                <strong>15:00</strong> free trial • Timer starts on your first message
              </span>
              <span className="trial-divider">|</span>
              <button onClick={() => navigate("/signup")} className="trial-link">
                Create an account for unlimited access
              </button>
            </>
          )}
        </div>
      </div>

      <div className="guest-chat-messages">
        {messages.length === 0 ? (
          <div className="guest-welcome-container">
            <img src="/msu_logo.webp" alt="MSU Logo" className="guest-welcome-logo" />
            <h1 className="guest-welcome-title">Morgan State CS Navigator</h1>
            <p className="guest-welcome-subtitle">How can I assist with your academic journey today?</p>
            <div className="guest-suggestions">
              {GUEST_SUGGESTIONS.map((s, i) => (
                <button
                  key={i}
                  className="guest-suggestion-btn"
                  onClick={() => handleSuggestion(s)}
                  disabled={isLoading || isSessionExpired}
                >
                  {s}
                </button>
              ))}
            </div>
          </div>
        ) : (
          messages.map((msg, i) => (
            <div key={i} className={`guest-message ${msg.sender}`}>
              <img
                src={msg.sender === "user" ? "/user_icon.webp" : "/bot_avatar.webp"}
                alt={msg.sender}
                className="guest-avatar-img"
                onError={(e) => {
                  e.target.onerror = null;
                  e.target.src = msg.sender === "user"
                    ? "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='%23666'%3E%3Cpath d='M12 12c2.21 0 4-1.79 4-4s-1.79-4-4-4-4 1.79-4 4 1.79 4 4 4zm0 2c-2.67 0-8 1.34-8 4v2h16v-2c0-2.66-5.33-4-8-4z'/%3E%3C/svg%3E"
                    : "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='%23002D72'%3E%3Cpath d='M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm-1 17.93c-3.95-.49-7-3.85-7-7.93 0-.62.08-1.21.21-1.79L9 15v1c0 1.1.9 2 2 2v1.93zm6.9-2.54c-.26-.81-1-1.39-1.9-1.39h-1v-3c0-.55-.45-1-1-1H8v-2h2c.55 0 1-.45 1-1V7h2c1.1 0 2-.9 2-2v-.41c2.93 1.19 5 4.06 5 7.41 0 2.08-.8 3.97-2.1 5.39z'/%3E%3C/svg%3E";
                }}
              />
              <div className="guest-message-content">
                <div className="guest-message-bubble">
                  <ReactMarkdown remarkPlugins={[remarkGfm]}>
                    {msg.text}
                  </ReactMarkdown>
                </div>
                <div className="guest-timestamp">{msg.time}</div>
              </div>
            </div>
          ))
        )}

        {/* Typing indicator */}
        {isLoading && (
          <div className="guest-message bot">
            <img
              src="/bot_avatar.webp"
              alt="Bot"
              className="guest-avatar-img"
              onError={(e) => {
                e.target.onerror = null;
                e.target.src = "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='%23002D72'%3E%3Cpath d='M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm-1 17.93c-3.95-.49-7-3.85-7-7.93 0-.62.08-1.21.21-1.79L9 15v1c0 1.1.9 2 2 2v1.93zm6.9-2.54c-.26-.81-1-1.39-1.9-1.39h-1v-3c0-.55-.45-1-1-1H8v-2h2c.55 0 1-.45 1-1V7h2c1.1 0 2-.9 2-2v-.41c2.93 1.19 5 4.06 5 7.41 0 2.08-.8 3.97-2.1 5.39z'/%3E%3C/svg%3E";
              }}
            />
            <div className="guest-message-content">
              <div className="guest-message-bubble guest-typing-bubble">
                <div className="guest-dot"></div>
                <div className="guest-dot"></div>
                <div className="guest-dot"></div>
              </div>
            </div>
          </div>
        )}

        <div ref={messagesEndRef} />
      </div>

      {/* Input area */}
      <div className="guest-chat-input-container">
        <form onSubmit={handleSend} className="guest-chat-input-wrapper">
          <input
            type="text"
            ref={inputRef}
            className="guest-chat-input-field"
            value={input}
            onChange={(e) => setInput(e.target.value.slice(0, MAX_INPUT_LENGTH))}
            placeholder={isSessionExpired ? "Sign up for unlimited messages..." : "Type your message..."}
            disabled={isLoading || isSessionExpired}
            maxLength={MAX_INPUT_LENGTH}
          />
          <button
            type="submit"
            className="guest-action-btn-icon guest-send-btn"
            title="Send message"
            disabled={isLoading || !input.trim() || isSessionExpired}
          >
            <BsArrowUpCircleFill size={24} />
          </button>
        </form>
        {input.length > MAX_INPUT_LENGTH - 50 && (
          <div className="guest-char-counter">
            {input.length}/{MAX_INPUT_LENGTH}
          </div>
        )}
      </div>

      <div style={{ textAlign: 'center', padding: '6px 0 10px', fontSize: '0.7rem', color: 'var(--text-tertiary, #94a3b8)', letterSpacing: '0.02em' }}>
        CS Navigator {new Date().getFullYear()} | Morgan State University Department of Computer Science
      </div>

      {/* Sign-up Modal */}
      {showSignUpModal && (
        <div className="signup-modal-overlay" onClick={() => setShowSignUpModal(false)}>
          <div className="signup-modal" onClick={(e) => e.stopPropagation()}>
            <button className="signup-modal-close" onClick={() => setShowSignUpModal(false)}>
              <BsX size={24} />
            </button>

            <div className="signup-modal-header">
              <div className="signup-modal-icon">
                <BsClock size={32} />
              </div>
              <h2 className="signup-modal-title">Your Free Trial Has Ended</h2>
              <p className="signup-modal-subtitle">
                Create a free account to continue using CS Navigator
              </p>
            </div>

            <div className="signup-modal-benefits">
              <h3 className="benefits-title">With a free account, you get:</h3>
              <ul className="benefits-list">
                <li>
                  <BsCheckCircle className="benefit-icon" />
                  <span>Unlimited chat messages</span>
                </li>
                <li>
                  <BsCheckCircle className="benefit-icon" />
                  <span>Personalized course recommendations</span>
                </li>
                <li>
                  <BsCheckCircle className="benefit-icon" />
                  <span>Save your chat history</span>
                </li>
                <li>
                  <BsCheckCircle className="benefit-icon" />
                  <span>Connect your DegreeWorks for tailored advice</span>
                </li>
              </ul>
            </div>

            <div className="signup-modal-footer">
              <button
                className="signup-modal-btn secondary"
                onClick={() => {
                  if (!bonusUsed) {
                    // Grant 5 bonus minutes by pushing session start forward
                    const bonus = 5 * 60 * 1000;
                    const newStart = Date.now() - (GUEST_SESSION_DURATION - bonus);
                    setSessionStartTime(newStart);
                    localStorage.setItem("guest_session_start", newStart.toString());
                    setTimeRemaining(bonus);
                    setBonusUsed(true);
                    localStorage.setItem("guest_bonus_used", "true");
                  }
                  setShowSignUpModal(false);
                }}
              >
                {bonusUsed ? "Close" : "5 More Minutes"}
              </button>
              <button
                className="signup-modal-btn primary"
                onClick={() => navigate("/signup")}
              >
                Create Free Account
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
