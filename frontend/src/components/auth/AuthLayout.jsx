import React from "react";
import "./auth.css";

// Gov-grade redesign: navy brand panel (blueprint grid) on the left with the
// Morgan State vertical lockup, white form card on the right. Stacks on mobile.
// Form logic stays in Login.jsx / SignUp.jsx — this is layout + chrome only.

const ShieldCheck = () => (
  <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor"
    strokeWidth="2.1" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
    <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z" /><polyline points="9 12 11 14 15 10" />
  </svg>
);
const MessageSquare = () => (
  <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor"
    strokeWidth="2.1" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
    <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />
  </svg>
);
const ClipboardCheck = () => (
  <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor"
    strokeWidth="2.1" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
    <rect x="8" y="2" width="8" height="4" rx="1" ry="1" />
    <path d="M16 4h2a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h2" />
    <path d="m9 14 2 2 4-4" />
  </svg>
);

const features = [
  { Icon: MessageSquare, label: "Grounded answers on grants, compliance & forms" },
  { Icon: ClipboardCheck, label: "Proposal tracker with deadline countdowns" },
  { Icon: ShieldCheck, label: "Every answer cites its morgan.edu source" },
];

export default function AuthLayout({ title, subtitle, children, footer }) {
  return (
    <div className="auth">
      {/* LEFT: navy brand panel */}
      <aside className="auth__brand" aria-label="ORA Navigator">
        <div className="auth__grid" aria-hidden="true" />
        <div className="auth__brandInner">
          <img
            className="auth__logoMain"
            src="/morgan-state-vertical.png"
            alt="Morgan State University logo"
          />
          <h1 className="auth__brandTitle">ORA Navigator</h1>
          <p className="auth__brandSubtitle">
            A research-administration assistant for Morgan State faculty, PIs, and
            ORA staff. Sign in with your <strong>@morgan.edu</strong> address to get started.
          </p>
          <ul className="auth__features">
            {features.map(({ Icon, label }, i) => (
              <li key={i} className="auth__feature">
                <span className="auth__featIcon"><Icon /></span>
                {label}
              </li>
            ))}
          </ul>
        </div>
        <div className="auth__brandFooter">
          <span>Morgan State University</span>
          <span className="auth__dot">·</span>
          <span>Office of Research Administration</span>
        </div>
      </aside>

      {/* RIGHT: form card */}
      <main className="auth__main">
        <section className="auth__card" aria-label={title}>
          <div className="auth__mobileLogoWrap">
            <img src="/morgan-tower-mark.png" alt="Morgan State University" className="auth__mobileLogo" />
          </div>
          <header className="auth__header">
            <span className="auth__eyebrow">ORA Navigator</span>
            <h2 className="auth__title">{title}</h2>
            <p className="auth__titleSub">
              {subtitle
                ? subtitle
                : title === "Log in"
                ? "Welcome back. Sign in with your Morgan State email to continue."
                : "Use your @morgan.edu email to create your ORA Navigator account."}
            </p>
          </header>

          {children}

          {footer ? <div className="auth__footer">{footer}</div> : null}
        </section>
      </main>
    </div>
  );
}
