// src/components/AIChatbot.jsx
import React from "react";

function AIChatbot() {
  return (
    <div className="welcome-container">
      <img
        src="/msu_logo.png"
        alt="Morgan State University Logo"
        style={{
          width: "120px",        // fixed width
          height: "auto",        // preserve aspect ratio
          marginBottom: "15px",
          filter: "drop-shadow(0 2px 4px rgba(0,0,0,0.2))"
        }}
      />
      <h1 className="welcome-title">
        Morgan State Computer Science Department
      </h1>
      <p className="welcome-subtitle">
        How can I help you today?
      </p>
    </div>
  );
}

export default AIChatbot;
