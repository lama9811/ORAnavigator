import React from "react";
import { FaFileInvoiceDollar } from "@react-icons/all-files/fa/FaFileInvoiceDollar";
import { FaClipboardCheck } from "@react-icons/all-files/fa/FaClipboardCheck";
import { FaChartLine } from "@react-icons/all-files/fa/FaChartLine";
import { FaShieldAlt } from "@react-icons/all-files/fa/FaShieldAlt";
import "./auth.css";

const features = [
  {
    icon: FaFileInvoiceDollar,
    title: "Proposal & Pre-Award",
    desc: "F&A rates, budget templates, internal routing, NSF/NIH submission checklists — answers in seconds."
  },
  {
    icon: FaClipboardCheck,
    title: "Compliance Workflows",
    desc: "IRB meeting dates, IACUC forms, COI disclosure, RCR training — the right doc, the right contact."
  },
  {
    icon: FaChartLine,
    title: "Post-Award & Reporting",
    desc: "NCE 60-day rule, effort certification, subawards, financial reporting — grounded in PI Handbook 5."
  },
  {
    icon: FaShieldAlt,
    title: "Morgan-only access",
    desc: "Sign in with your @morgan.edu email. Your questions stay within the Morgan ORA workspace."
  }
];

export default function AuthLayout({ title, subtitle, children, footer }) {
  return (
    <div className="auth">
      {/* LEFT: Brand panel with features */}
      <aside className="auth__brand" aria-label="ORA Navigator">
        {/* Animated background elements */}
        <div className="auth__bgOrbs">
          <div className="auth__orb auth__orb--1"></div>
          <div className="auth__orb auth__orb--2"></div>
          <div className="auth__orb auth__orb--3"></div>
        </div>

        <div className="auth__brandInner">
          <img
            className="auth__logoMain"
            src="/main_logo.webp"
            alt="Morgan State University"
          />

          <h1 className="auth__brandTitle">ORA Navigator</h1>
          <p className="auth__brandSubtitle">{subtitle}</p>

          {/* Feature highlights */}
          <div className="auth__features">
            {features.map((feature, index) => (
              <div
                key={index}
                className="auth__feature"
                style={{ animationDelay: `${index * 0.1}s` }}
              >
                <div className="auth__featureIcon">
                  <feature.icon size={20} />
                </div>
                <div className="auth__featureText">
                  <strong>{feature.title}</strong>
                  <span>{feature.desc}</span>
                </div>
              </div>
            ))}
          </div>
        </div>

        {/* Footer on left panel */}
        <div className="auth__brandFooter">
          <span>Morgan State University</span>
          <span className="auth__dot">·</span>
          <span>Office of Research Administration</span>
        </div>
      </aside>

      {/* RIGHT: Form panel */}
      <main className="auth__main">
        <section className="auth__card" aria-label={title}>
          {/* Logo for mobile */}
          <div className="auth__mobileLogoWrap">
            <img src="/msu_logo.webp" alt="MSU" className="auth__mobileLogo" />
          </div>

          <header className="auth__header">
            <h2 className="auth__title">{title}</h2>
            <p className="auth__titleSub">
              {title === "Log in"
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
