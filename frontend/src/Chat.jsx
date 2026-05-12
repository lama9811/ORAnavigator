import { useState, useEffect, useMemo } from "react";
import { useNavigate } from "react-router-dom";

export default function Chat() {
  const [messages, setMessages] = useState([]);
  const [input, setInput] = useState("");
  const token = localStorage.getItem("token");
  const navigate = useNavigate();

  // --- FORCE LOCAL BACKEND (Fixes the "Invalid Token" & 403 Error) ---
  const API_BASE = useMemo(() => {
    // If we are on Local Dev (5173), ALWAYS talk to Localhost:5000
    if (window.location.port === "5173") {
      return "http://localhost:8080";
    }
    // Default for Docker (3000)
    return "http://localhost:8080";
  }, []);

  useEffect(() => {
    if (!token) navigate("/login");
  }, [token, navigate]);

  async function sendMessage(e) {
    e.preventDefault();
    if (!input.trim()) return;
    
    setMessages(msgs => [...msgs, { from: "user", text: input }]);
    const query = input;
    setInput("");

    try {
      // DEBUG: This will show in your console so you know where it's going
      const targetUrl = `${API_BASE}/api/chat`; 
      console.log("Sending chat to:", targetUrl);

      const res = await fetch(targetUrl, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "Authorization": `Bearer ${token}`
        },
        body: JSON.stringify({ query })
      });

      if (res.ok) {
        const data = await res.json();
        const botText = data.response || data.message || JSON.stringify(data);
        setMessages(msgs => [...msgs, { from: "bot", text: botText }]);
      } else if (res.status === 403 || res.status === 401) {
        setMessages(msgs => [...msgs, { from: "bot", text: "Error: Session Invalid. Please Log In Again." }]);
        // Optional: logout automatically
        // localStorage.removeItem("token");
        // navigate("/login");
      } else {
        const err = await res.json().catch(() => ({}));
        setMessages(msgs => [...msgs, { from: "bot", text: `Error: ${err.detail || "Unknown error"}` }]);
      }
    } catch (error) {
      console.error(error);
      setMessages(msgs => [...msgs, { from: "bot", text: "Error: Could not connect to server." }]);
    }
  }

  return (
    <div style={{ padding: 20, maxWidth: 600, margin: "0 auto", fontFamily: "Segoe UI, sans-serif" }}>
      <h2>Chat with Morgan State Bot</h2>
      <div style={{ height: 400, overflowY: "auto", border: "1px solid #ccc", borderRadius: 8, padding: 20, marginBottom: 20 }}>
        {messages.map((m, i) => (
          <div key={i} style={{ textAlign: m.from === "user" ? "right" : "left", margin: "10px 0" }}>
            <span style={{ padding: "8px 12px", borderRadius: 12, background: m.from === "user" ? "#4A90E2" : "#E0E0E0", color: m.from === "user" ? "#fff" : "#000" }}>
              {m.text}
            </span>
          </div>
        ))}
      </div>
      <form onSubmit={sendMessage} style={{ display: "flex", gap: 10 }}>
        <input style={{ flex: 1, padding: 10 }} value={input} onChange={e => setInput(e.target.value)} placeholder="Type a message..." />
        <button type="submit" style={{ padding: "10px 20px", background: "#4A90E2", color: "#fff", border: "none" }}>Send</button>
      </form>
    </div>
  );
}