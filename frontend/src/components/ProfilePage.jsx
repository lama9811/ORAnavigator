// src/components/ProfilePage.jsx
import React, { useState, useEffect } from "react";
import { useNavigate } from "react-router-dom";
import { FaArrowLeft } from "@react-icons/all-files/fa/FaArrowLeft";
import { FaUser } from "@react-icons/all-files/fa/FaUser";
import { FaEnvelope } from "@react-icons/all-files/fa/FaEnvelope";
import { FaLock } from "@react-icons/all-files/fa/FaLock";
import { FaCamera } from "@react-icons/all-files/fa/FaCamera";
import { FaCog } from "@react-icons/all-files/fa/FaCog";
import { FaShieldAlt } from "@react-icons/all-files/fa/FaShieldAlt";
import "./ProfilePage.css";

import { getApiBase } from "../lib/apiBase";
const API_BASE = getApiBase();

export default function ProfilePage({ userEmail, onLogout }) {
  const navigate = useNavigate();
  const [isEditing, setIsEditing] = useState(false);
  const [loading, setLoading] = useState(false);
  const [pendingResearch, setPendingResearch] = useState(0);
  const [message, setMessage] = useState({ type: "", text: "" });
  
  const [profile, setProfile] = useState({
    name: "",
    email: userEmail || "",
    studentId: "",
    major: "",
    profilePicture: "/user_icon.webp",
    morganConnected: false,
    role: "student"
  });

  const [passwords, setPasswords] = useState({
    currentPassword: "",
    newPassword: "",
    confirmPassword: ""
  });

  const [showPasswordForm, setShowPasswordForm] = useState(false);
  const [showPwFields, setShowPwFields] = useState({ current: false, new: false, confirm: false });

  // Fetch profile data on mount
  useEffect(() => {
    fetchProfile();
    const token = localStorage.getItem("token");
    // Fetch pending research suggestions count for admin badge
    if (token) {
      fetch(`${API_BASE}/api/admin/research/stats`, { headers: { Authorization: `Bearer ${token}` } })
        .then(r => r.ok ? r.json() : null)
        .then(d => { if (d) setPendingResearch(d.pending_suggestions || 0); })
        .catch(() => {});
    }
  }, []);

  const fetchProfile = async () => {
  try {
    const token = localStorage.getItem("token");
    const response = await fetch(`${API_BASE}/api/profile`, {
      headers: {
        "Authorization": `Bearer ${token}`
      }
    });

    if (response.ok) {
      const data = await response.json();

      // 🔥 FIX: Handle base64 data URLs, full URLs, and relative paths
      if (data.profilePicture) {
        if (data.profilePicture.startsWith('data:')) {
          // Base64 data URL - use directly
        } else if (data.profilePicture.startsWith('http')) {
          // Full URL - use directly
        } else {
          // Relative path - prepend API base
          data.profilePicture = `${API_BASE}${data.profilePicture}`;
        }
      }

      console.log("Profile loaded:", data);
      setProfile(data);
    }
  } catch (error) {
    console.error("Error fetching profile:", error);
  }
};


  const handleUpdateProfile = async (e) => {
    e.preventDefault();
    setLoading(true);
    setMessage({ type: "", text: "" });

    try {
      const token = localStorage.getItem("token");
      const response = await fetch(`${API_BASE}/api/profile`, {
        method: "PUT",
        headers: {
          "Content-Type": "application/json",
          "Authorization": `Bearer ${token}`
        },
        body: JSON.stringify({
          name: profile.name,
          studentId: profile.studentId,
          major: profile.major
        })
      });

      if (response.ok) {
        setMessage({ type: "success", text: "Profile updated successfully!" });
        setIsEditing(false);
        fetchProfile();
      } else {
        const error = await response.json();
        setMessage({ type: "error", text: error.detail || "Failed to update profile" });
      }
    } catch (error) {
      setMessage({ type: "error", text: "Network error. Please try again." });
    } finally {
      setLoading(false);
    }
  };

  const handleChangePassword = async (e) => {
    e.preventDefault();
    
    if (passwords.newPassword !== passwords.confirmPassword) {
      setMessage({ type: "error", text: "New passwords don't match!" });
      return;
    }

    if (passwords.newPassword.length < 6) {
      setMessage({ type: "error", text: "Password must be at least 6 characters" });
      return;
    }

    setLoading(true);
    setMessage({ type: "", text: "" });

    try {
      const token = localStorage.getItem("token");
      const response = await fetch(`${API_BASE}/api/change-password`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "Authorization": `Bearer ${token}`
        },
        body: JSON.stringify({
          currentPassword: passwords.currentPassword,
          newPassword: passwords.newPassword
        })
      });

      if (response.ok) {
        setMessage({ type: "success", text: "Password changed successfully!" });
        setPasswords({ currentPassword: "", newPassword: "", confirmPassword: "" });
        setShowPasswordForm(false);
      } else {
        const error = await response.json();
        setMessage({ type: "error", text: error.detail || "Failed to change password" });
      }
    } catch (error) {
      setMessage({ type: "error", text: "Network error. Please try again." });
    } finally {
      setLoading(false);
    }
  };

  const handleProfilePictureUpload = async (e) => {
    const file = e.target.files[0];
    if (!file) return;

    const formData = new FormData();
    formData.append("profilePicture", file);

    setLoading(true);
    setMessage({ type: "", text: "" });

    try {
      const token = localStorage.getItem("token");
      const response = await fetch(`${API_BASE}/api/upload-profile-picture`, {
        method: "POST",
        headers: {
          "Authorization": `Bearer ${token}`
        },
        body: formData
      });

      if (response.ok) {
        const data = await response.json();
        setProfile({ ...profile, profilePicture: data.url });
        setMessage({ type: "success", text: "Profile picture updated! Refreshing..." });
        
        // 🔥 AUTO-REFRESH to update navbar
        setTimeout(() => window.location.reload(), 1000);
      } else {
        const error = await response.json();
        setMessage({ type: "error", text: error.detail || "Failed to upload picture" });
      }
    } catch (error) {
      setMessage({ type: "error", text: "Network error. Please try again." });
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="profile-page">
      <div className="profile-header">
        <button className="back-button" onClick={() => navigate("/")}>
          <FaArrowLeft /> Back to Chat
        </button>
        <h1>Profile Settings</h1>
      </div>

      {message.text && (
        <div className={`message ${message.type}`}>
          {message.text}
        </div>
      )}

      <div className="profile-container">
        {/* Profile Picture Section */}
        <div className="profile-picture-section">
          <div className="profile-picture-wrapper">
            <img 
              src={profile.profilePicture} 
              alt="Profile" 
              className="profile-picture"
              onError={(e) => e.target.src = "/user_icon.webp"}
            />
            <label className="upload-overlay">
              <FaCamera size={24} />
              <input 
                type="file" 
                accept="image/*" 
                onChange={handleProfilePictureUpload}
                style={{ display: "none" }}
              />
            </label>
          </div>
          <h2>{profile.name || profile.email}</h2>
          <p className="profile-email">{profile.email}</p>
        </div>

        {/* Profile Information */}
        <div className="profile-section">
          <div className="section-header">
            <h3>Personal Information</h3>
            {!isEditing && (
              <button className="edit-btn" onClick={() => setIsEditing(true)}>
                Edit
              </button>
            )}
          </div>

          <form onSubmit={handleUpdateProfile}>
            <div className="form-group">
              <label>
                <FaUser /> Full Name
              </label>
              <input
                type="text"
                value={profile.name || ""}
                onChange={(e) => setProfile({ ...profile, name: e.target.value })}
                disabled={!isEditing}
                placeholder="Enter your full name"
              />
            </div>

            <div className="form-group">
              <label>
                <FaEnvelope /> Email
              </label>
              <input
                type="email"
                value={profile.email}
                disabled
                className="disabled-input"
              />
            </div>

            {isEditing && (
              <div className="form-actions">
                <button type="submit" className="save-btn" disabled={loading}>
                  {loading ? "Saving..." : "Save Changes"}
                </button>
                <button 
                  type="button" 
                  className="cancel-btn" 
                  onClick={() => {
                    setIsEditing(false);
                    fetchProfile();
                  }}
                >
                  Cancel
                </button>
              </div>
            )}
          </form>
        </div>

        {/* Password Section */}
        <div className="profile-section">
          <div className="section-header">
            <h3>Security</h3>
            {!showPasswordForm && (
              <button className="edit-btn" onClick={() => setShowPasswordForm(true)}>
                Change Password
              </button>
            )}
          </div>

          {showPasswordForm && (
            <form onSubmit={handleChangePassword}>
              <div className="form-group">
                <label>
                  <FaLock /> Current Password
                </label>
                <div style={{ position: 'relative' }}>
                  <input
                    type={showPwFields.current ? "text" : "password"}
                    value={passwords.currentPassword}
                    onChange={(e) => setPasswords({ ...passwords, currentPassword: e.target.value })}
                    required
                    style={{ paddingRight: '60px' }}
                  />
                  <button type="button" onClick={() => setShowPwFields(s => ({ ...s, current: !s.current }))}
                    style={{ position: 'absolute', right: '10px', top: '50%', transform: 'translateY(-50%)', background: 'none', border: 'none', color: 'var(--msu-blue)', cursor: 'pointer', fontSize: '0.8rem', fontWeight: 600 }}>
                    {showPwFields.current ? "Hide" : "Show"}
                  </button>
                </div>
              </div>

              <div className="form-group">
                <label>
                  <FaLock /> New Password
                </label>
                <div style={{ position: 'relative' }}>
                  <input
                    type={showPwFields.new ? "text" : "password"}
                    value={passwords.newPassword}
                    onChange={(e) => setPasswords({ ...passwords, newPassword: e.target.value })}
                    required
                    style={{ paddingRight: '60px' }}
                  />
                  <button type="button" onClick={() => setShowPwFields(s => ({ ...s, new: !s.new }))}
                    style={{ position: 'absolute', right: '10px', top: '50%', transform: 'translateY(-50%)', background: 'none', border: 'none', color: 'var(--msu-blue)', cursor: 'pointer', fontSize: '0.8rem', fontWeight: 600 }}>
                    {showPwFields.new ? "Hide" : "Show"}
                  </button>
                </div>
              </div>

              <div className="form-group">
                <label>
                  <FaLock /> Confirm New Password
                </label>
                <div style={{ position: 'relative' }}>
                  <input
                    type={showPwFields.confirm ? "text" : "password"}
                    value={passwords.confirmPassword}
                    onChange={(e) => setPasswords({ ...passwords, confirmPassword: e.target.value })}
                    required
                    style={{ paddingRight: '60px' }}
                  />
                  <button type="button" onClick={() => setShowPwFields(s => ({ ...s, confirm: !s.confirm }))}
                    style={{ position: 'absolute', right: '10px', top: '50%', transform: 'translateY(-50%)', background: 'none', border: 'none', color: 'var(--msu-blue)', cursor: 'pointer', fontSize: '0.8rem', fontWeight: 600 }}>
                    {showPwFields.confirm ? "Hide" : "Show"}
                  </button>
                </div>
              </div>

              <div className="form-actions">
                <button type="submit" className="save-btn" disabled={loading}>
                  {loading ? "Changing..." : "Change Password"}
                </button>
                <button 
                  type="button" 
                  className="cancel-btn" 
                  onClick={() => {
                    setShowPasswordForm(false);
                    setPasswords({ currentPassword: "", newPassword: "", confirmPassword: "" });
                  }}
                >
                  Cancel
                </button>
              </div>
            </form>
          )}
        </div>

        {/* Admin Access - Only show for admins */}
        {profile.role === "admin" && (
          <div className="profile-section admin-section">
            <div className="section-header">
              <h3><FaShieldAlt /> Admin Access</h3>
            </div>
            <div className="admin-access-content">
              <p>You have administrator privileges. Access the admin dashboard to manage tickets and curriculum.</p>
              <div style={{ position: "relative", display: "inline-block", width: "100%" }}>
                <button className="admin-access-btn" onClick={() => navigate("/admin")}>
                  <FaCog /> Open Admin Dashboard
                </button>
                {pendingResearch > 0 && (
                  <span style={{
                    position: "absolute", top: "-8px", right: "-4px",
                    background: "#ef4444", color: "white", borderRadius: "50%",
                    width: "22px", height: "22px", display: "flex", alignItems: "center",
                    justifyContent: "center", fontSize: "11px", fontWeight: 700,
                    boxShadow: "0 2px 6px rgba(239,68,68,0.4)", border: "2px solid var(--bg-card)"
                  }}>
                    {pendingResearch}
                  </span>
                )}
              </div>
            </div>
          </div>
        )}

        {/* Logout */}
        <div className="profile-section">
          <button className="logout-btn" onClick={onLogout}>
            Sign Out
          </button>
        </div>
      </div>

    </div>
  );
}
