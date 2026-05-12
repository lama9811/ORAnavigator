import { useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import AuthLayout from "./components/auth/AuthLayout";
import { getApiBase } from "./lib/apiBase";

const API_BASE = getApiBase();

const LockIcon = (props) => (
  <svg {...props} width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <rect x="3" y="11" width="18" height="11" rx="2" ry="2"/>
    <path d="M7 11V7a5 5 0 0110 0v4"/>
  </svg>
);

export default function ResetPassword() {
  const [searchParams] = useSearchParams();
  const token = searchParams.get("token") || "";
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [done, setDone] = useState(false);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  const handleSubmit = async (e) => {
    e.preventDefault();
    setError("");
    if (password.length < 8) { setError("Password must be at least 8 characters"); return; }
    if (password !== confirm) { setError("Passwords don't match"); return; }

    setLoading(true);
    try {
      const res = await fetch(`${API_BASE}/api/reset-password`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ token, password }),
      });
      const data = await res.json();
      if (res.ok) {
        setDone(true);
      } else {
        setError(data.detail || "Reset failed");
      }
    } catch (err) {
      setError("Failed to connect to server");
    } finally {
      setLoading(false);
    }
  };

  if (!token) {
    return (
      <AuthLayout
        title="Invalid Link"
        subtitle="This reset link is invalid or has expired."
        footer={<Link className="auth__link" to="/forgot-password">Request a new reset link</Link>}
      >
        <div />
      </AuthLayout>
    );
  }

  return (
    <AuthLayout
      title={done ? "Password Reset!" : "New Password"}
      subtitle={done ? "Your password has been changed successfully." : "Choose a new password for your account."}
      footer={
        <>
          <Link className="auth__link" to="/login">Back to Login</Link>
        </>
      }
    >
      {done ? (
        <div style={{ textAlign: "center", padding: "20px 0" }}>
          <svg width="48" height="48" viewBox="0 0 48 48" fill="none" style={{ marginBottom: "12px" }}>
            <circle cx="24" cy="24" r="24" fill="rgba(52,168,83,0.1)"/>
            <path d="M15 24l6 6 12-12" stroke="#34A853" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round"/>
          </svg>
          <Link to="/login" className="auth__submit" style={{ display: "inline-block", textDecoration: "none", textAlign: "center", marginTop: "12px" }}>
            Log In
          </Link>
        </div>
      ) : (
        <form onSubmit={handleSubmit}>
          {error && <div className="auth__error">{error}</div>}

          <div className="field">
            <label htmlFor="new-pw">New Password</label>
            <div className="field__control">
              <LockIcon className="field__icon" aria-hidden="true" />
              <input
                id="new-pw"
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                placeholder="Minimum 8 characters"
                required
                minLength={8}
              />
            </div>
          </div>

          <div className="field">
            <label htmlFor="confirm-pw">Confirm Password</label>
            <div className="field__control">
              <LockIcon className="field__icon" aria-hidden="true" />
              <input
                id="confirm-pw"
                type="password"
                value={confirm}
                onChange={(e) => setConfirm(e.target.value)}
                placeholder="Re-enter password"
                required
              />
            </div>
          </div>

          <button type="submit" className="auth__submit" disabled={loading}>
            {loading ? "Resetting..." : "Reset Password"}
          </button>
        </form>
      )}
    </AuthLayout>
  );
}
