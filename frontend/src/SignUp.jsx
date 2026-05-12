import React, { useState, useMemo } from "react";
import { Link, useNavigate } from "react-router-dom";
import AuthLayout from "./components/auth/AuthLayout";
import { getApiBase } from "./lib/apiBase";

// Icons with explicit dimensions for proper rendering
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

const UserIcon = (props) => (
  <svg {...props} width="18" height="18" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/>
    <circle cx="12" cy="7" r="4"/>
  </svg>
);

const IdCardIcon = (props) => (
  <svg {...props} width="18" height="18" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <rect x="2" y="5" width="20" height="14" rx="2"/>
    <line x1="2" y1="10" x2="22" y2="10"/>
    <line x1="6" y1="14" x2="6" y2="14.01"/>
    <line x1="10" y1="14" x2="14" y2="14"/>
  </svg>
);

const CheckIcon = (props) => (
  <svg {...props} width="16" height="16" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <polyline points="20 6 9 17 4 12"/>
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

// Password strength checker
function getPasswordStrength(password) {
  if (!password) return { level: 0, label: "", class: "" };

  let score = 0;
  if (password.length >= 6) score++;
  if (password.length >= 10) score++;
  if (/[A-Z]/.test(password)) score++;
  if (/[0-9]/.test(password)) score++;
  if (/[^A-Za-z0-9]/.test(password)) score++;

  if (score <= 2) return { level: 1, label: "Weak", class: "weak" };
  if (score <= 3) return { level: 2, label: "Medium", class: "medium" };
  return { level: 3, label: "Strong", class: "strong" };
}

export default function Signup({ onRegistered }) {
  const [email, setEmail] = useState("");
  const [name, setName] = useState("");
  const [studentId, setStudentId] = useState("");
  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [showPw, setShowPw] = useState(false);
  const [showConfirmPw, setShowConfirmPw] = useState(false);
  const [error, setError] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const navigate = useNavigate();

  const API_BASE = getApiBase();

  const passwordStrength = useMemo(() => getPasswordStrength(password), [password]);
  const passwordsMatch = password && confirmPassword && password === confirmPassword;
  const passwordsMismatch = confirmPassword && password !== confirmPassword;

  const canSubmit = email.trim() && password.length >= 8 && passwordsMatch && !submitting;

  const handleSubmit = async (e) => {
    e.preventDefault();
    setError("");

    if (password !== confirmPassword) {
      setError("Passwords do not match");
      return;
    }

    if (password.length < 8) {
      setError("Password must be at least 8 characters");
      return;
    }

    setSubmitting(true);

    try {
      const res = await fetch(`${API_BASE}/api/register`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email: email.trim(), password, name: name.trim() || undefined, student_id: studentId.trim() || undefined }),
      });

      if (!res.ok) throw new Error(await parseResponseError(res));

      // Success - redirect to login
      navigate("/login", {
        state: { message: "Account created successfully! Please log in." }
      });
    } catch (err) {
      setError(err?.message || "Registration failed");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <AuthLayout
      title="Sign Up"
      subtitle="Join the community. Create your account to explore courses, requirements, and resources."
      footer={
        <>
          Already have an account? <Link className="auth__link" to="/login">Log in</Link>
        </>
      }
    >
      {error ? (
        <div className="auth__error" role="alert">{error}</div>
      ) : null}

      <form onSubmit={handleSubmit}>
        <div className="field">
          <label htmlFor="signup-email">Morgan State Email <span style={{ fontSize: "0.75rem", color: "var(--text-tertiary)", fontWeight: 400 }}>(@morgan.edu only)</span></label>
          <div className="field__control">
            <EnvelopeIcon className="field__icon" aria-hidden="true" />
            <input
              id="signup-email"
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder="you@morgan.edu"
              autoComplete="email"
              required
            />
          </div>
        </div>

        <div className="field">
          <label htmlFor="signup-name">Full Name <span style={{ fontSize: "0.75rem", color: "var(--text-tertiary)", fontWeight: 400 }}>(optional)</span></label>
          <div className="field__control">
            <UserIcon className="field__icon" aria-hidden="true" />
            <input
              id="signup-name"
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="e.g. Rohan Saini"
              autoComplete="name"
            />
          </div>
        </div>

        <div className="field">
          <label htmlFor="signup-student-id">Student ID <span style={{ fontSize: "0.75rem", color: "var(--text-tertiary)", fontWeight: 400 }}>(optional)</span></label>
          <div className="field__control">
            <IdCardIcon className="field__icon" aria-hidden="true" />
            <input
              id="signup-student-id"
              type="text"
              value={studentId}
              onChange={(e) => setStudentId(e.target.value)}
              placeholder="e.g. 12345678"
              autoComplete="off"
            />
          </div>
        </div>

        <div className="field">
          <label htmlFor="signup-password">Password</label>
          <div className="field__control">
            <LockIcon className="field__icon" aria-hidden="true" />
            <input
              id="signup-password"
              type={showPw ? "text" : "password"}
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder="Create a strong password"
              autoComplete="new-password"
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

          {/* Password Requirements + Strength Indicator */}
          {password && (
            <>
              <div className="password-strength">
                <div className="password-strength__bar">
                  <div className={`password-strength__fill password-strength__fill--${passwordStrength.class}`} />
                </div>
                <span className={`password-strength__text password-strength__text--${passwordStrength.class}`}>
                  {passwordStrength.label} password
                </span>
              </div>
              <div style={{ fontSize: '0.75rem', color: 'var(--text-tertiary)', marginTop: '4px', lineHeight: '1.6' }}>
                <span style={{ color: password.length >= 8 ? '#22c55e' : '#94a3b8' }}>8+ characters</span>
                {' \u00B7 '}
                <span style={{ color: /[A-Z]/.test(password) ? '#22c55e' : '#94a3b8' }}>uppercase</span>
                {' \u00B7 '}
                <span style={{ color: /[0-9]/.test(password) ? '#22c55e' : '#94a3b8' }}>number</span>
                {' \u00B7 '}
                <span style={{ color: /[^A-Za-z0-9]/.test(password) ? '#22c55e' : '#94a3b8' }}>symbol</span>
              </div>
            </>
          )}
        </div>

        <div className="field">
          <label htmlFor="signup-confirm">Confirm Password</label>
          <div className="field__control">
            <LockIcon className="field__icon" aria-hidden="true" />
            <input
              id="signup-confirm"
              type={showConfirmPw ? "text" : "password"}
              value={confirmPassword}
              onChange={(e) => setConfirmPassword(e.target.value)}
              placeholder="Confirm your password"
              autoComplete="new-password"
              required
              style={{
                borderColor: passwordsMismatch ? '#ef4444' : passwordsMatch ? '#22c55e' : undefined
              }}
            />
            {passwordsMatch && (
              <CheckIcon
                style={{
                  position: 'absolute',
                  left: 'auto',
                  right: '70px',
                  top: '50%',
                  transform: 'translateY(-50%)',
                  color: '#22c55e',
                  width: '18px',
                  height: '18px',
                  pointerEvents: 'none',
                }}
              />
            )}
            <button
              type="button"
              className="field__action"
              onClick={() => setShowConfirmPw((v) => !v)}
              aria-label={showConfirmPw ? "Hide password" : "Show password"}
            >
              {showConfirmPw ? "Hide" : "Show"}
            </button>
          </div>
          {passwordsMismatch && (
            <span style={{ color: '#ef4444', fontSize: '0.8rem', marginTop: '6px', display: 'block' }}>
              Passwords do not match
            </span>
          )}
        </div>

        <button
          className="btn-primary auth__submit"
          type="submit"
          disabled={!canSubmit}
        >
          {submitting ? "Creating Account..." : "Create Account"}
        </button>

        <p style={{
          marginTop: '16px',
          fontSize: '0.8rem',
          color: '#64748b',
          textAlign: 'center',
          lineHeight: '1.5'
        }}>
          By creating an account, you agree to the University's terms of service and privacy policy.
        </p>
      </form>
    </AuthLayout>
  );
}
