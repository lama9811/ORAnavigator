import React, { useEffect } from "react";
import { useNavigate } from "react-router-dom";
import GuestChatbox from "./GuestChatbox";
import "./LandingPage.css";

export default function LandingPage() {
  const navigate = useNavigate();

  // Redirect if already authenticated
  useEffect(() => {
    const token = localStorage.getItem("token");
    if (token) {
      navigate("/chat", { replace: true });
    }
  }, [navigate]);

  // Collapse sidebar for landing page
  useEffect(() => {
    document.body.classList.add('sidebar-collapsed');
    return () => {
      document.body.classList.remove('sidebar-collapsed');
    };
  }, []);

  return (
    <div className="landing-page">
      <div className="landing-chat-wrapper">
        <GuestChatbox />
      </div>
    </div>
  );
}
