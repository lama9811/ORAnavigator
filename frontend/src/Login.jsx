import React, { useEffect, useState } from "react";
import { Link, useNavigate, useLocation } from "react-router-dom";
import AuthLayout from "./components/auth/AuthLayout";
import { getApiBase } from "./lib/apiBase";

// Modern line icons - with explicit dimensions for proper rendering
const EnvelopeIcon = (props) => (
  <svg {...props} width="18" height="18" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <path d="M4 4h16c1.1 0 2 .9 2 2v12c0 1.1-.9 2-2 2H4c-1.1 0-2-.9-2-2V6c0-1.1.9-2 2-2z"/>
    <polyline points="22,6 12,13 2,6"/>
  </svg>
);

const LockIcon = (props) => (
  <svg {...props} width="18" height="18" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <rect x="3" y="11" width="18" height="11" rx="2" ry="2"/>
    <path d="M7 11V7a5 5 0 0 1 10 0v4"/>
  </svg>
);

async function parseResponseError(res) {
  let message = `Error ${res.status}`;
  const ct = res.headers.get("content-type") || "";
  if (ct.includes("application/json")) {
    const errData = await res.json().catch(() => null);
    if (errData) {
      if (typeof errData?.detail === "string") message = errData.detail;
      else if (typeof errData?.message === "string") message = errData.message;
      else if (typeof errData === "string") message = errData;
    }
  } else {
    const txt = await res.text().catch(() => "");
    if (txt) message = txt;
  }
  return message;
}

export default function Login({ onLoggedIn }) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [showPw, setShowPw] = useState(false);
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const navigate = useNavigate();
  const location = useLocation();

  const API_BASE = getApiBase();

  useEffect(() => {
    if (localStorage.getItem("token")) navigate("/", { replace: true });
    // Show success message from signup redirect
    if (location.state?.message) {
      setSuccess(location.state.message);
      window.history.replaceState({}, document.title);
    }
    // Show verified message from email verification redirect
    const params = new URLSearchParams(location.search);
    if (params.get("verified") === "true") {
      setSuccess("Email verified! You can now log in.");
      window.history.replaceState({}, document.title, "/login");
    }
  }, [navigate, location]);

  const handleSubmit = async (e) => {
    e.preventDefault();
    setError("");
    setSuccess("");
    setSubmitting(true);

    try {
      const res = await fetch(`${API_BASE}/api/login`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email: email.trim(), password }),
      });

      if (!res.ok) throw new Error(await parseResponseError(res));

      const data = await res.json();
      const jwt = data.access_token || data.token;
      if (!jwt) throw new Error("No token returned from server");

      localStorage.setItem("token", jwt);
      onLoggedIn?.(jwt);
      navigate("/", { replace: true });
    } catch (err) {
      const msg = err?.message || "Login failed";
      setError(msg);
      setNeedsVerification(msg.toLowerCase().includes("verify"));
    } finally {
      setSubmitting(false);
    }
  };

  const [needsVerification, setNeedsVerification] = useState(false);
  const [resending, setResending] = useState(false);

  const handleResendVerification = async () => {
    if (!email.trim()) return;
    setResending(true);
    try {
      const res = await fetch(`${API_BASE}/api/resend-verification`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email: email.trim() }),
      });
      const data = await res.json();
      setSuccess(data.message || "Verification email sent! Check your inbox.");
      setError("");
      setNeedsVerification(false);
    } catch {
      setError("Failed to resend verification email. Try again.");
    } finally {
      setResending(false);
    }
  };

  return (
    <AuthLayout
      title="Log in"
      subtitle="Welcome back, Bear. Ask questions about courses, requirements, and resources."
      footer={
        <>
          Don't have an account? <Link className="auth__link" to="/signup">Sign up</Link>
          <br />
          <Link className="auth__link" to="/forgot-password" style={{ fontSize: "0.85rem" }}>Forgot password?</Link>
        </>
      }
    >
      {success && (
        <div style={{
          background: 'linear-gradient(135deg, rgba(34, 197, 94, 0.1), rgba(34, 197, 94, 0.05))',
          border: '1px solid rgba(34, 197, 94, 0.3)',
          color: '#16a34a',
          padding: '12px 16px',
          borderRadius: '12px',
          marginBottom: '20px',
          fontSize: '0.9rem'
        }} role="status">
          {success}
        </div>
      )}

      {error && (
        <div className="auth__error" role="alert">
          {error}
          {needsVerification && (
            <button
              type="button"
              onClick={handleResendVerification}
              disabled={resending}
              style={{
                display: "block", marginTop: "10px", background: "none", border: "1px solid currentColor",
                color: "inherit", padding: "6px 14px", borderRadius: "8px", cursor: "pointer",
                fontSize: "0.85rem", width: "100%",
              }}
            >
              {resending ? "Sending..." : "Resend verification email"}
            </button>
          )}
        </div>
      )}

      <form onSubmit={handleSubmit}>
        <div className="field">
          <label htmlFor="email">Email Address</label>
          <div className="field__control">
            <EnvelopeIcon className="field__icon" aria-hidden="true" />
            <input
              id="email"
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder="name@morgan.edu"
              autoComplete="email"
              required
            />
          </div>
        </div>

        <div className="field">
          <label htmlFor="password">Password</label>
          <div className="field__control">
            <LockIcon className="field__icon" aria-hidden="true" />
            <input
              id="password"
              type={showPw ? "text" : "password"}
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder="Enter your password"
              autoComplete="current-password"
              required
            />
            <button
              type="button"
              className="field__action"
              onClick={() => setShowPw((v) => !v)}
              aria-label={showPw ? "Hide password" : "Show password"}
            >
              {showPw ? "Hide" : "Show"}
            </button>
          </div>
        </div>

        <button
          className="btn-primary auth__submit"
          type="submit"
          disabled={submitting || !email.trim() || !password}
        >
          {submitting ? "Logging in..." : "Log in"}
        </button>
      </form>
    </AuthLayout>
  );
}
