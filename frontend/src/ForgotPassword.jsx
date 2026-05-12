import { useState } from "react";
import { Link } from "react-router-dom";
import AuthLayout from "./components/auth/AuthLayout";
import { getApiBase } from "./lib/apiBase";

const API_BASE = getApiBase();

const EnvelopeIcon = (props) => (
  <svg {...props} width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <path d="M4 4h16c1.1 0 2 .9 2 2v12c0 1.1-.9 2-2 2H4c-1.1 0-2-.9-2-2V6c0-1.1.9-2 2-2z"/>
    <polyline points="22,6 12,13 2,6"/>
  </svg>
);

export default function ForgotPassword() {
  const [email, setEmail] = useState("");
  const [sent, setSent] = useState(false);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  const handleSubmit = async (e) => {
    e.preventDefault();
    setError("");
    setLoading(true);
    try {
      const res = await fetch(`${API_BASE}/api/forgot-password`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email }),
      });
      if (res.ok) {
        setSent(true);
      } else {
        const data = await res.json();
        setError(data.detail || "Something went wrong");
      }
    } catch (err) {
      setError("Failed to connect to server");
    } finally {
      setLoading(false);
    }
  };

  return (
    <AuthLayout
      title="Reset Password"
      subtitle="Enter your Morgan State email to receive a password reset link."
      footer={
        <>
          Remember your password? <Link className="auth__link" to="/login">Log in</Link>
        </>
      }
    >
      {sent ? (
        <div style={{ textAlign: "center", padding: "20px 0" }}>
          <svg width="48" height="48" viewBox="0 0 48 48" fill="none" style={{ marginBottom: "12px" }}>
            <circle cx="24" cy="24" r="24" fill="rgba(52,168,83,0.1)"/>
            <path d="M15 24l6 6 12-12" stroke="#34A853" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round"/>
          </svg>
          <p style={{ color: "var(--text-secondary)", fontSize: "0.9rem", lineHeight: 1.6 }}>
            If an account exists for <strong>{email}</strong>, we've sent a password reset link.
          </p>
          <p style={{ color: "var(--text-secondary)", fontSize: "0.75rem", lineHeight: 1.5, marginTop: "12px", background: "var(--bg-secondary, #f5f5f5)", padding: "10px 14px", borderRadius: "8px" }}>
            Morgan State emails may take up to 10 minutes due to institutional email security. Check your spam folder if you don't see it. Gmail and other providers should arrive instantly.
          </p>
          <Link className="auth__link" to="/login" style={{ marginTop: "12px", display: "inline-block" }}>
            Back to Login
          </Link>
        </div>
      ) : (
        <form onSubmit={handleSubmit}>
          {error && <div className="auth__error">{error}</div>}

          <div className="field">
            <label htmlFor="reset-email">Email Address</label>
            <div className="field__control">
              <EnvelopeIcon className="field__icon" aria-hidden="true" />
              <input
                id="reset-email"
                type="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder="you@morgan.edu"
                autoComplete="email"
                required
              />
            </div>
          </div>

          <button type="submit" className="auth__submit" disabled={loading}>
            {loading ? "Sending..." : "Send Reset Link"}
          </button>
        </form>
      )}
    </AuthLayout>
  );
}
