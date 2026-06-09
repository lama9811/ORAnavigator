import React, { useState, useEffect, useRef } from "react";
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { ArrowUpCircle } from "lucide-react";
import "./GuestChatbox.css";

// Default suggestions — ORA faculty/PI/admin audience
const GUEST_SUGGESTIONS = [
  "What's Morgan's current F&A (indirect cost) rate?",
  "When is the next IRB meeting and what's the submission deadline?",
  "Who do I contact about a NIH grant submission?",
  "What forms do I need for a no-cost extension?",
  "How do I request a subaward on my sponsored project?",
  "Where can I find the PI Handbook on budget preparation?"
];

import { getApiBase } from "../lib/apiBase";
const API_BASE = getApiBase();

const MAX_INPUT_LENGTH = 500;

// Guest profile - no fake academic data to prevent false answers
const generateGuestProfile = () => {
  localStorage.removeItem("guest_profile"); // clean up old fake profiles
  return { name: "Guest User" };
};

// ORA Navigator is free for everyone. The guest chat is fully open — no trial
// timer, no message cap, no "create an account" nagging. (Signing in is still
// available, but only to SAVE chat history — never required to ask questions.)
// A light per-minute rate limit lives on the server (GUEST_RATE_LIMIT) purely
// to stop abuse/runaway cost; normal use never sees it.
export default function GuestChatbox() {
  // State
  const [messages, setMessages] = useState(() => {
    try {
      const saved = sessionStorage.getItem("guest_messages");
      return saved ? JSON.parse(saved) : [];
    } catch {
      return [];
    }
  });
  const [input, setInput] = useState("");
  const [isLoading, setIsLoading] = useState(false);
  const [guestProfile] = useState(generateGuestProfile);

  // Refs
  const messagesEndRef = useRef(null);
  const inputRef = useRef(null);

  // Focus input on load
  useEffect(() => {
    inputRef.current?.focus();
  }, []);

  // Auto-scroll to bottom as the conversation grows. Skip on the empty welcome
  // screen so the heading + question suggestions stay in view on landing.
  useEffect(() => {
    if (messages.length === 0) return;
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  // Persist guest messages so a page refresh doesn't wipe the conversation.
  // sessionStorage survives reload but clears when the tab is closed.
  useEffect(() => {
    try {
      sessionStorage.setItem("guest_messages", JSON.stringify(messages));
    } catch {
      // ignore storage errors (private mode / quota)
    }
  }, [messages]);

  // Helper to add message
  const addMessage = (text, sender) => {
    const time = new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
    setMessages((prev) => [...prev, { text, sender, time }]);
  };

  // Handle suggestion click
  const handleSuggestion = (text) => {
    if (isLoading) return;
    setInput('');

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
        if (res.status === 429) {
          setMessages(prev => [...prev, { text: "I'm getting a lot of questions right now — please wait a moment and try again.", sender: "bot", time: new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) }]);
          return;
        }
        const data = await res.json();
        const botReply = data.response || "I couldn't process that. Please try again.";
        setMessages(prev => [...prev, { text: botReply, sender: "bot", time: new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }), citations: data.citations || [] }]);
      } catch {
        setMessages(prev => [...prev, { text: "Something went wrong. Please try again.", sender: "bot", time: new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) }]);
      } finally {
        setIsLoading(false);
      }
    })();
  };

  // Main send handler
  const handleSend = async (e) => {
    e.preventDefault();
    if (!input.trim() || isLoading) return;

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
        addMessage("I'm getting a lot of questions right now — please wait a moment and try again.", "bot");
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
      <div className="guest-chat-messages">
        {messages.length === 0 ? (
          <div className="guest-welcome-container">
            <div className="ora-eyebrow">Office of Research Administration · Morgan State University</div>
            <h1 className="guest-welcome-title">ORA Navigator</h1>
            <p className="guest-welcome-subtitle">A research-administration assistant for faculty, PIs, and department admins — answers grounded in ORA policies, forms, IRB &amp; IACUC procedures, and funding sources.</p>
            <div className="ora-suggestions-label">Try asking</div>
            <div className="guest-suggestions">
              {GUEST_SUGGESTIONS.map((s, i) => (
                <button
                  key={i}
                  className="guest-suggestion-btn"
                  onClick={() => handleSuggestion(s)}
                  disabled={isLoading}
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
                  {msg.sender === "bot" && msg.citations && msg.citations.length > 0 && (
                    <div className="message-sources">
                      <span className="message-sources-label">Sources</span>
                      <ul className="message-sources-list">
                        {msg.citations.map((c, ci) => (
                          <li key={ci}>
                            <a href={c.url} target="_blank" rel="noopener noreferrer">{c.title}</a>
                          </li>
                        ))}
                      </ul>
                    </div>
                  )}
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
            placeholder="Type your message..."
            disabled={isLoading}
            maxLength={MAX_INPUT_LENGTH}
          />
          <button
            type="submit"
            className="guest-action-btn-icon guest-send-btn"
            title="Send message"
            disabled={isLoading || !input.trim()}
          >
            <ArrowUpCircle size={24} />
          </button>
        </form>
        {input.length > MAX_INPUT_LENGTH - 50 && (
          <div className="guest-char-counter">
            {input.length}/{MAX_INPUT_LENGTH}
          </div>
        )}
      </div>

      <div style={{ textAlign: 'center', padding: '6px 0 10px', fontSize: '0.7rem', color: 'var(--text-tertiary, #94a3b8)', letterSpacing: '0.04em', textTransform: 'uppercase' }}>
        ORA Navigator · {new Date().getFullYear()} · Morgan State University Office of Research Administration
      </div>
    </div>
  );
}
