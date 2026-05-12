import React from "react";
import { FaGraduationCap } from "@react-icons/all-files/fa/FaGraduationCap";
import { FaBook } from "@react-icons/all-files/fa/FaBook";
import { FaComments } from "@react-icons/all-files/fa/FaComments";
import { FaShieldAlt } from "@react-icons/all-files/fa/FaShieldAlt";
import "./auth.css";

const features = [
  {
    icon: FaGraduationCap,
    title: "Academic Guidance",
    desc: "Get personalized advice on courses, majors, and degree requirements"
  },
  {
    icon: FaBook,
    title: "Curriculum Explorer",
    desc: "Browse the full CS curriculum with prerequisites and offerings"
  },
  {
    icon: FaComments,
    title: "AI-Powered Chat",
    desc: "Ask questions and get instant answers about your academic journey"
  },
  {
    icon: FaShieldAlt,
    title: "Secure & Private",
    desc: "Your conversations and data are protected and confidential"
  }
];

export default function AuthLayout({ title, subtitle, children, footer }) {
  return (
    <div className="auth">
      {/* LEFT: Brand panel with features */}
      <aside className="auth__brand" aria-label="Morgan State CS Navigator">
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

          <h1 className="auth__brandTitle">CS Navigator</h1>
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
          <span className="auth__dot">•</span>
          <span>Department of Computer Science</span>
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
                ? "Welcome back! Enter your credentials to continue."
                : "Create your account to get started."}
            </p>
          </header>

          {children}

          {footer ? <div className="auth__footer">{footer}</div> : null}
        </section>

      </main>
    </div>
  );
}
