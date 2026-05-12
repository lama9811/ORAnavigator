import React, { useState, useEffect, useRef, useCallback } from "react";
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { toast } from 'sonner';
import { Prism as SyntaxHighlighter } from 'react-syntax-highlighter';
import { oneDark } from 'react-syntax-highlighter/dist/esm/styles/prism';
import { motion, AnimatePresence } from 'framer-motion';

import { FaMicrophone } from "@react-icons/all-files/fa/FaMicrophone";
import { FaPaperclip } from "@react-icons/all-files/fa/FaPaperclip";
import { FaVolumeUp } from "@react-icons/all-files/fa/FaVolumeUp";
import { FaTimes } from "@react-icons/all-files/fa/FaTimes";
import { FaStop } from "@react-icons/all-files/fa/FaStop";
import { FaEllipsisV } from "@react-icons/all-files/fa/FaEllipsisV";
import { FaThumbsUp } from "@react-icons/all-files/fa/FaThumbsUp";
import { FaThumbsDown } from "@react-icons/all-files/fa/FaThumbsDown";
import { FaFlag } from "@react-icons/all-files/fa/FaFlag";
import { BsSoundwave, BsArrowUpCircleFill } from "react-icons/bs";

// 🔥 Icons for File Cards
import { FaFile } from "@react-icons/all-files/fa/FaFile";
import { FaFilePdf } from "@react-icons/all-files/fa/FaFilePdf";
import { FaFileWord } from "@react-icons/all-files/fa/FaFileWord";
import { FaFileImage } from "@react-icons/all-files/fa/FaFileImage";

import "./Chatbox.css";

// Featured questions that showcase chatbot capabilities
const FEATURED_QUESTIONS = [
  "What's the difference between the B.S. in CS and Cloud Computing?",
  "What are the prerequisites for COSC 220 Data Structures?",
  "Who is the chair of the CS department and how do I contact them?",
  "How do I request a course override or substitute a requirement?",
  "What Group A and Group B electives should I take as a junior?",
  "Tell me about the 4+1 accelerated B.S./M.S. program",
  "Where can I get tutoring for intro CS courses like COSC 111?",
  "What scholarships are available for CS majors at Morgan State?",
];

import { getApiBase } from "../lib/apiBase";
const API_BASE = getApiBase();

// Helper for icons
const getFileIcon = (filename) => {
  if (!filename) return <FaFile className="file-icon generic" />;
  const ext = filename.split('.').pop().toLowerCase();
  
  if (ext === 'pdf') return <FaFilePdf className="file-icon pdf" />;
  if (['doc', 'docx'].includes(ext)) return <FaFileWord className="file-icon word" />;
  if (['jpg', 'jpeg', 'png', 'gif', 'webp'].includes(ext)) return <FaFileImage className="file-icon image" />;
  
  return <FaFile className="file-icon generic" />;
};

export default function Chatbox({ initialMessages = [], onSessionChange, sessionId }) {
  // --- STATE ---
  const [messages, setMessages] = useState(initialMessages);
  const [input, setInput] = useState("");
  const [isLoading, setIsLoading] = useState(false);
  const [isListening, setIsListening] = useState(false);
  const [userProfilePicture, setUserProfilePicture] = useState("/user_icon.webp");

  // 🔥 Staging State for File Uploads
  const [pendingFile, setPendingFile] = useState(null);

  // 🔥 Dynamic Suggestions State
  const [suggestions, setSuggestions] = useState(FEATURED_QUESTIONS);
  const [suggestionsLoading, setSuggestionsLoading] = useState(true);

  // 🔥 Voice Mode State
  const [isVoiceMode, setIsVoiceMode] = useState(false);
  const [isSpeaking, setIsSpeaking] = useState(false);
  const [voiceStatus, setVoiceStatus] = useState("idle"); // idle, listening, processing, speaking

  // Model selector state
  const [selectedModel, setSelectedModel] = useState("inav-1.1");
  const [modelDropdownOpen, setModelDropdownOpen] = useState(false);
  const modelDropdownRef = useRef(null);

  const MODEL_OPTIONS = [
    { id: "inav-1.1", name: "iNav", desc: "Fast & accurate" },
    { id: "inav-2.0", name: "iNav Pro", desc: "Deeper thinking, may take longer" },
  ];

  // Close dropdown when clicking outside
  useEffect(() => {
    const handleClickOutside = (e) => {
      if (modelDropdownRef.current && !modelDropdownRef.current.contains(e.target)) {
        setModelDropdownOpen(false);
      }
    };
    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, []);

  // 🔥 Feedback State
  const [feedbackMenuOpen, setFeedbackMenuOpen] = useState(null); // index of message with open menu
  const [feedbackGiven, setFeedbackGiven] = useState({}); // {messageIndex: 'helpful' | 'not_helpful' | 'reported'}
  const [reportModal, setReportModal] = useState(null); // index of message being reported
  const [reportText, setReportText] = useState("");

  // 🔥 Drag-and-drop state
  const [isDragging, setIsDragging] = useState(false);

  // Thinking status - step index drives everything
  const [thinkingStepIndex, setThinkingStepIndex] = useState(0);
  const [thinkingTimer, setThinkingTimer] = useState(0);
  const thinkingMessages = [
    "Understanding your question",
    "Searching knowledge base",
    "Analyzing results",
    "Preparing response"
  ];
  // Derived: completed steps are all before current index, active is current
  const thinkingStatus = thinkingMessages[thinkingStepIndex] || thinkingMessages[0];

  // Map status text to contextual SVG icon
  const getStatusIcon = (status) => {
    const s = (status || "").toLowerCase();
    if (s.includes("search") || s.includes("knowledge"))
      return ( // magnifying glass
        <svg viewBox="0 0 20 20" fill="none" className="status-icon icon-search"><circle cx="8.5" cy="8.5" r="5.5" stroke="currentColor" strokeWidth="1.8"/><path d="M12.5 12.5L17 17" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round"/></svg>
      );
    if (s.includes("understand") || s.includes("analyz") || s.includes("question"))
      return ( // brain / lightbulb
        <svg viewBox="0 0 20 20" fill="none" className="status-icon icon-think"><path d="M10 2a5.5 5.5 0 00-2 10.63V15a1 1 0 001 1h2a1 1 0 001-1v-2.37A5.5 5.5 0 0010 2z" stroke="currentColor" strokeWidth="1.6" strokeLinejoin="round"/><path d="M8 17h4M9 19h2" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round"/></svg>
      );
    if (s.includes("consult") || s.includes("specialist") || s.includes("agent"))
      return ( // people / transfer
        <svg viewBox="0 0 20 20" fill="none" className="status-icon icon-consult"><circle cx="7" cy="6" r="3" stroke="currentColor" strokeWidth="1.5"/><path d="M1 17c0-3.3 2.7-6 6-6s6 2.7 6 6" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round"/><circle cx="15" cy="6" r="2" stroke="currentColor" strokeWidth="1.3"/><path d="M19 15c0-2.2-1.8-4-4-4" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round"/></svg>
      );
    if (s.includes("process") || s.includes("compil") || s.includes("generat"))
      return ( // gear
        <svg viewBox="0 0 20 20" fill="none" className="status-icon icon-process"><path d="M10 13a3 3 0 100-6 3 3 0 000 6z" stroke="currentColor" strokeWidth="1.5"/><path d="M10 1v2M10 17v2M1 10h2M17 10h2M3.93 3.93l1.41 1.41M14.66 14.66l1.41 1.41M16.07 3.93l-1.41 1.41M5.34 14.66l-1.41 1.41" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round"/></svg>
      );
    if (s.includes("prepar") || s.includes("writing") || s.includes("response"))
      return ( // pen / writing
        <svg viewBox="0 0 20 20" fill="none" className="status-icon icon-write"><path d="M13.586 3.586a2 2 0 012.828 2.828l-9.5 9.5-3.5 1 1-3.5 9.172-9.828z" stroke="currentColor" strokeWidth="1.5" strokeLinejoin="round"/><path d="M12 5l3 3" stroke="currentColor" strokeWidth="1.3"/></svg>
      );
    if (s.includes("review") || s.includes("catalog") || s.includes("course"))
      return ( // book
        <svg viewBox="0 0 20 20" fill="none" className="status-icon icon-book"><path d="M3 4a1 1 0 011-1h4a3 3 0 013 3v11a2 2 0 00-2-2H4a1 1 0 01-1-1V4zM17 4a1 1 0 00-1-1h-4a3 3 0 00-3 3v11a2 2 0 012-2h4a1 1 0 001-1V4z" stroke="currentColor" strokeWidth="1.5" strokeLinejoin="round"/></svg>
      );
    if (s.includes("department") || s.includes("info") || s.includes("check"))
      return ( // info/clipboard
        <svg viewBox="0 0 20 20" fill="none" className="status-icon icon-info"><rect x="4" y="2" width="12" height="16" rx="2" stroke="currentColor" strokeWidth="1.5"/><path d="M8 6h4M8 10h4M8 14h2" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round"/></svg>
      );
    // Default: sparkle
    return (
      <svg viewBox="0 0 20 20" fill="none" className="status-icon icon-default"><path d="M10 2l1.5 5L17 8.5l-5 2L10 16l-2-5.5L3 8.5l5-1L10 2z" stroke="currentColor" strokeWidth="1.4" strokeLinejoin="round"/></svg>
    );
  };

  // --- REFS ---
  const messagesEndRef = useRef(null);
  const inputRef = useRef(null);
  const fileInputRef = useRef(null);
  const isRemoteUpdate = useRef(false);
  const audioRef = useRef(null);
  const recognitionRef = useRef(null);
  const isVoiceModeRef = useRef(false); // 🔥 Ref to track voice mode for callbacks

  // --- EFFECTS ---

  // 1. Focus input on load
  useEffect(() => { 
    const focusInput = () => inputRef.current?.focus();
    focusInput();
    window.addEventListener('focus', focusInput);
    return () => window.removeEventListener('focus', focusInput);
  }, []);

  // 2. Sync Messages FROM Parent (Database Load)
  useEffect(() => {
    if (JSON.stringify(initialMessages) !== JSON.stringify(messages)) {
      isRemoteUpdate.current = true;
      setMessages(initialMessages);
    }
  }, [initialMessages]);

  // 3. Sync Messages TO Parent (User typed something)
  useEffect(() => {
    if (!onSessionChange) return;
    if (isRemoteUpdate.current) { 
        isRemoteUpdate.current = false; 
        return; 
    }
    onSessionChange(messages);
  }, [messages, onSessionChange]);

  // 4. Auto-Scroll to bottom
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  // 5. Fetch User Profile Picture
  useEffect(() => {
    const fetchUserProfile = async () => {
      const token = localStorage.getItem("token");
      if (!token) return;
      try {
        const response = await fetch(`${API_BASE}/api/profile`, {
          headers: { Authorization: `Bearer ${token}` }
        });
        if (response.ok) {
          const data = await response.json();
          if (data.profilePicture) {
             // Handle base64 data URLs, full URLs, and relative paths
             let picUrl = data.profilePicture;
             if (picUrl.startsWith("data:")) {
                // Base64 data URL - use directly
                setUserProfilePicture(picUrl);
             } else if (picUrl.startsWith("http")) {
                // Full URL - use directly
                setUserProfilePicture(picUrl);
             } else {
                // Relative path - prepend API base
                setUserProfilePicture(`${API_BASE}${picUrl}`);
             }
          }
        }
      } catch (error) {
        console.error("❌ Profile Error:", error);
      }
    };
    fetchUserProfile();
  }, []);

  // 6. Fetch randomized featured questions from backend
  useEffect(() => {
    const fetchSuggestions = async () => {
      if (messages.length > 0) {
        setSuggestionsLoading(false);
        return;
      }
      try {
        const response = await fetch(`${API_BASE}/api/popular-questions`);
        if (response.ok) {
          const data = await response.json();
          if (data.questions && data.questions.length > 0) {
            setSuggestions(data.questions.slice(0, 8));
          }
        }
      } catch (error) {
        console.error("Failed to fetch suggestions:", error);
      } finally {
        setSuggestionsLoading(false);
      }
    };
    fetchSuggestions();
  }, []);

  // 7. Cleanup voice mode on unmount
  useEffect(() => {
    return () => {
      if (recognitionRef.current) {
        recognitionRef.current.abort();
      }
      if (audioRef.current) {
        audioRef.current.pause();
      }
      window.speechSynthesis?.cancel();
    };
  }, []);

  // 8. Cycle through thinking steps while waiting for response
  const streamingNoText = messages.some(m => m.isStreaming && !m.text);
  const showThinking = isLoading || streamingNoText;

  useEffect(() => {
    if (!showThinking) {
      setThinkingTimer(0);
      return;
    }

    setThinkingTimer(0);

    // Advance to next step every 1.8s
    const statusInterval = setInterval(() => {
      setThinkingStepIndex(prev => {
        if (prev < thinkingMessages.length - 1) return prev + 1;
        return prev; // Stay on last step until text arrives
      });
    }, 1800);

    // Timer
    const timerInterval = setInterval(() => {
      setThinkingTimer(prev => prev + 1);
    }, 1000);

    return () => {
      clearInterval(statusInterval);
      clearInterval(timerInterval);
    };
  }, [showThinking]);

  // --- HANDLERS ---

  // Helper to add message to local state
  const addMessage = (text, sender) => {
    const time = new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
    setMessages((prev) => [...prev, { text, sender, time }]);
  };

  // 🔥 Enhanced TTS using OpenAI API
  const speakWithTTS = async (text) => {
    if (isSpeaking) return;

    setIsSpeaking(true);
    setVoiceStatus("speaking");

    try {
      const token = localStorage.getItem("token");
      const response = await fetch(`${API_BASE}/api/tts`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "Authorization": `Bearer ${token}`
        },
        body: JSON.stringify({ text: text.substring(0, 4000), voice: "alloy" })
      });

      if (!response.ok) throw new Error("TTS request failed");

      const audioBlob = await response.blob();
      const audioUrl = URL.createObjectURL(audioBlob);

      if (audioRef.current) {
        audioRef.current.src = audioUrl;
        audioRef.current.onended = () => {
          setIsSpeaking(false);
          URL.revokeObjectURL(audioUrl);
          // 🔥 Use ref to check voice mode (avoids closure issues)
          if (isVoiceModeRef.current) {
            setVoiceStatus("listening");
            setTimeout(() => startListening(), 500);
          } else {
            setVoiceStatus("idle");
          }
        };
        audioRef.current.onerror = () => {
          setIsSpeaking(false);
          setVoiceStatus("idle");
          fallbackSpeak(text);
        };
        await audioRef.current.play();
      }
    } catch (error) {
      console.error("TTS Error:", error);
      fallbackSpeak(text);
    }
  };

  // Browser TTS fallback
  const fallbackSpeak = (text) => {
    if (!window.speechSynthesis) {
      setIsSpeaking(false);
      setVoiceStatus("idle");
      return;
    }
    window.speechSynthesis.cancel();
    const utterance = new SpeechSynthesisUtterance(text);
    utterance.rate = 1.05;
    utterance.onend = () => {
      setIsSpeaking(false);
      // 🔥 Use ref to check voice mode (avoids closure issues)
      if (isVoiceModeRef.current) {
        setVoiceStatus("listening");
        setTimeout(() => startListening(), 500);
      } else {
        setVoiceStatus("idle");
      }
    };
    window.speechSynthesis.speak(utterance);
  };

  // Simple TTS for manual speaker button (uses browser TTS)
  // Click once to play, click again to stop
  const speak = (text) => {
    if (!window.speechSynthesis) return toast.warning("Text-to-speech not supported in this browser.");
    if (window.speechSynthesis.speaking) {
      window.speechSynthesis.cancel();
      if (audioRef.current) audioRef.current.pause();
      setIsSpeaking(false);
      return;
    }
    window.speechSynthesis.cancel();
    const utterance = new SpeechSynthesisUtterance(text);
    utterance.rate = 1.05;
    utterance.onend = () => setIsSpeaking(false);
    setIsSpeaking(true);
    window.speechSynthesis.speak(utterance);
  };

  // Handle File Selection (Staging)
  const MAX_FILE_SIZE = 10 * 1024 * 1024; // 10MB
  const ALLOWED_FILE_TYPES = ['image/png', 'image/jpeg', 'image/gif', 'application/pdf', 'text/plain', 'application/msword', 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'];
  const handleFileSelect = (e) => {
    if (e.target.files && e.target.files[0]) {
      const file = e.target.files[0];
      if (file.size > MAX_FILE_SIZE) {
        toast.error("File too large. Maximum size is 10MB.");
        return;
      }
      if (!ALLOWED_FILE_TYPES.includes(file.type)) {
        toast.error("Unsupported file type.");
        return;
      }
      setPendingFile(file);
    }
    // Reset value so onChange triggers again if same file selected
    if (fileInputRef.current) fileInputRef.current.value = "";
  };

  // Clear Staged File
  const clearFile = () => {
    setPendingFile(null);
  };

  // 🔥 Enhanced Voice Input with Voice Mode Support - CONTINUOUS
  const startListening = (forceVoiceMode = false) => {
    // Don't start if already listening or speaking
    if (isListening || isSpeaking) return;

    // Extra safety check - if not in voice mode and not forced, don't start
    if (!forceVoiceMode && !isVoiceModeRef.current) return;

    const SpeechAPI = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!SpeechAPI) {
      toast.warning("Speech recognition not supported. Try Chrome or Edge.");
      return;
    }

    const rec = new SpeechAPI();
    rec.lang = "en-US";
    rec.continuous = false;
    rec.interimResults = false;
    recognitionRef.current = rec;

    // Track if we got a result (to handle silence timeouts)
    let gotResult = false;

    rec.onstart = () => {
      setIsListening(true);
      setVoiceStatus("listening");
      console.log("🎤 Voice mode: Started listening...");
    };

    rec.onresult = async (e) => {
      gotResult = true;
      const transcript = e.results[0][0].transcript;
      console.log("🎤 Voice mode: Got transcript:", transcript);
      setInput(transcript);
      setIsListening(false);

      // 🔥 Check ref for current voice mode state (not stale closure)
      if (isVoiceModeRef.current) {
        setVoiceStatus("processing");
        await handleVoiceSend(transcript);
      }
    };

    rec.onerror = (e) => {
      console.error("🎤 Speech error:", e.error);
      setIsListening(false);

      // 🔥 FIXED: For certain errors, retry listening if still in voice mode
      if (isVoiceModeRef.current) {
        // "no-speech" means user was silent - just restart listening
        // "aborted" means we stopped it intentionally - don't restart
        // "network" - network issue, try again
        if (e.error === "no-speech" || e.error === "network") {
          console.log("🎤 Voice mode: Restarting after", e.error);
          setVoiceStatus("listening");
          setTimeout(() => startListening(), 300);
        } else if (e.error !== "aborted") {
          // Other errors - still try to restart after a delay
          setVoiceStatus("listening");
          setTimeout(() => startListening(), 1000);
        }
      } else {
        setVoiceStatus("idle");
      }
    };

    rec.onend = () => {
      console.log("🎤 Voice mode: Recognition ended, gotResult:", gotResult);
      setIsListening(false);

      // 🔥 FIXED: If voice mode is active and we didn't get a result, restart
      // This handles the case where recognition ends without triggering onresult or onerror
      if (isVoiceModeRef.current && !gotResult && !isSpeaking) {
        console.log("🎤 Voice mode: Restarting (no result received)");
        setVoiceStatus("listening");
        setTimeout(() => startListening(), 300);
      }
    };

    rec.start();
  };

  // Voice mode send handler - sends and speaks response
  const handleVoiceSend = async (transcript) => {
    if (!transcript.trim()) {
      // Empty transcript - restart listening if in voice mode
      if (isVoiceModeRef.current) {
        setVoiceStatus("listening");
        setTimeout(() => startListening(), 300);
      }
      return;
    }

    const token = localStorage.getItem("token");
    addMessage(transcript, "user");
    setInput("");

    try {
      const res = await fetch(`${API_BASE}/chat`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "Authorization": `Bearer ${token}`
        },
        body: JSON.stringify({
          query: transcript,
          session_id: sessionId || "default",
          model: selectedModel
        })
      });

      if (!res.ok) throw new Error(res.statusText);

      const data = await res.json();
      const botResponse = data.response || data.message || "No response.";

      const isOutage = botResponse.includes("temporarily") && botResponse.includes("knowledge base");
      if (isOutage) {
        toast("Warming up! Try your question again.", {
          duration: 6000,
          style: {
            background: "linear-gradient(135deg, #1e293b 0%, #0f172a 100%)",
            color: "#f1f5f9",
            border: "1px solid rgba(99, 102, 241, 0.3)",
            borderRadius: "14px",
            padding: "14px 18px",
            boxShadow: "0 8px 32px rgba(0, 0, 0, 0.25), 0 0 0 1px rgba(99, 102, 241, 0.1)",
            backdropFilter: "blur(12px)",
            fontSize: "0.88rem",
            fontWeight: 500,
            letterSpacing: "0.01em",
          },
          icon: <svg width="18" height="18" viewBox="0 0 24 24" fill="none"><circle cx="12" cy="12" r="9" stroke="url(#tg2)" strokeWidth="2" strokeLinecap="round"/><path d="M12 7v5l3 3" stroke="url(#tg2)" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/><defs><linearGradient id="tg2" x1="3" y1="3" x2="21" y2="21"><stop stopColor="#818cf8"/><stop offset="1" stopColor="#6366f1"/></linearGradient></defs></svg>,
        });
      } else {
        addMessage(botResponse, "bot");
        await speakWithTTS(botResponse);
      }

    } catch (err) {
      console.error("🎤 Voice send error:", err);
      addMessage("Sorry, I had trouble processing that. Please try again.", "bot");

      // 🔥 FIXED: Restart listening even on error if still in voice mode
      if (isVoiceModeRef.current) {
        setVoiceStatus("listening");
        setTimeout(() => startListening(), 1000);
      } else {
        setVoiceStatus("idle");
      }
    }
  };

  // Toggle voice mode on/off
  const toggleVoiceMode = () => {
    if (isVoiceMode) {
      // Stop voice mode
      setIsVoiceMode(false);
      isVoiceModeRef.current = false; // 🔥 Sync ref with state
      setVoiceStatus("idle");
      setIsListening(false);
      if (audioRef.current) {
        audioRef.current.pause();
      }
      if (recognitionRef.current) {
        recognitionRef.current.abort();
      }
      window.speechSynthesis?.cancel();
    } else {
      // Start voice mode
      setIsVoiceMode(true);
      isVoiceModeRef.current = true; // 🔥 Sync ref with state
      startListening(true); // 🔥 Pass true to force voice mode for first listen
    }
  };

  // Simple voice input (tap mic without entering voice mode) - FIXED
  const handleVoiceInput = () => {
    // If already listening, stop it
    if (isListening) {
      if (recognitionRef.current) {
        recognitionRef.current.stop();
      }
      setIsListening(false);
      return;
    }

    const SpeechAPI = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!SpeechAPI) {
      toast.warning("Speech recognition not supported. Try Chrome or Edge.");
      return;
    }

    const rec = new SpeechAPI();
    rec.lang = "en-US";
    rec.continuous = false;
    rec.interimResults = false;
    recognitionRef.current = rec;

    rec.onstart = () => {
      setIsListening(true);
      console.log("🎤 Simple mic: Started listening...");
    };

    rec.onresult = (e) => {
      const transcript = e.results[0][0].transcript;
      console.log("🎤 Simple mic: Got transcript:", transcript);
      setInput(transcript);
      setIsListening(false);
      // Auto-focus input so user can edit or send
      inputRef.current?.focus();
    };

    rec.onerror = (e) => {
      console.error("🎤 Simple mic error:", e.error);
      setIsListening(false);
      if (e.error === "no-speech") {
        // User was silent, just stop quietly
      } else if (e.error !== "aborted") {
        toast.error("Voice input error: " + e.error);
      }
    };

    rec.onend = () => {
      setIsListening(false);
    };

    rec.start();
  };

  const handleSuggestion = (text) => {
      if (!isLoading) {
          setInput(text);
          // Auto-send the suggestion instead of just filling the input
          setTimeout(() => {
              const form = document.querySelector('.chat-input-wrapper');
              if (form) form.requestSubmit();
          }, 50);
      }
  };

  // 🔥 FEEDBACK HANDLERS
  const handleFeedback = async (messageIndex, feedbackType, messageText) => {
    const token = localStorage.getItem("token");

    try {
      await fetch(`${API_BASE}/api/feedback`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "Authorization": `Bearer ${token}`
        },
        body: JSON.stringify({
          message_text: messageText,
          feedback_type: feedbackType, // 'helpful', 'not_helpful', 'report'
          report_details: feedbackType === 'report' ? reportText : null,
          session_id: sessionId || "default"
        })
      });

      // Update local state to show feedback was given
      setFeedbackGiven(prev => ({ ...prev, [messageIndex]: feedbackType }));
      setFeedbackMenuOpen(null);

      if (feedbackType === 'report') {
        setReportModal(null);
        setReportText("");
      }
    } catch (error) {
      console.error("Failed to submit feedback:", error);
    }
  };

  const openReportModal = (messageIndex) => {
    setReportModal(messageIndex);
    setFeedbackMenuOpen(null);
  };

  const closeReportModal = () => {
    setReportModal(null);
    setReportText("");
  };

  // Close feedback menu when clicking outside
  useEffect(() => {
    const handleClickOutside = (e) => {
      if (feedbackMenuOpen !== null && !e.target.closest('.feedback-menu-container')) {
        setFeedbackMenuOpen(null);
      }
    };
    document.addEventListener('click', handleClickOutside);
    return () => document.removeEventListener('click', handleClickOutside);
  }, [feedbackMenuOpen]);

  // Auto-resize textarea
  const resizeTextarea = useCallback(() => {
    const el = inputRef.current;
    if (!el) return;
    el.style.height = 'auto';
    el.style.height = Math.min(el.scrollHeight, 150) + 'px';
  }, []);

  // 🔥 MAIN SEND LOGIC - With Streaming Support
  const handleSend = async (e, overrideText = null, skipCache = false) => {
    if (e) e.preventDefault();
    const sendText = overrideText || input.trim();
    if ((!sendText && !pendingFile) || isLoading) return;

    setIsLoading(true);
    setInput("");  // Clear input immediately to prevent concatenation with next typed message
    let finalMessage = sendText;

    try {
        const token = localStorage.getItem("token");

        // 1. Upload File (if exists, only for non-override sends)
        if (pendingFile && !overrideText) {
            const formData = new FormData();
            formData.append("file", pendingFile);

            const uploadRes = await fetch(`${API_BASE}/api/upload-file`, {
                method: "POST",
                headers: { "Authorization": `Bearer ${token}` },
                body: formData
            });

            if (uploadRes.ok) {
                const data = await uploadRes.json();
                const fullUrl = data.url.startsWith("http") ? data.url : `${API_BASE}${data.url}`;

                const fileMarkdown = `[${data.filename}](${fullUrl})`;

                if (finalMessage) {
                    finalMessage = `${fileMarkdown}\n${finalMessage}`;
                } else {
                    finalMessage = fileMarkdown;
                }
            } else {
                toast.error("File upload failed. Sending text only.");
            }
        }

        // 2. Optimistic UI Update
        addMessage(finalMessage, "user");
        if (!overrideText) {
            setInput("");
            setPendingFile(null);
            // Reset textarea height
            if (inputRef.current) inputRef.current.style.height = 'auto';
        }

        // 3. Add placeholder bot message for streaming
        const botMessageIndex = messages.length + 1; // Index after user message
        const time = new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
        setThinkingStepIndex(0);
        setMessages((prev) => [...prev, { text: "", sender: "bot", time, isStreaming: true }]);

        // 4. Stream from Chat API using fetch with ReadableStream
        const res = await fetch(`${API_BASE}/chat/stream`, {
            method: "POST",
            headers: {
                "Content-Type": "application/json",
                "Authorization": `Bearer ${token}`
            },
            body: JSON.stringify({
                query: finalMessage,
                session_id: sessionId || "default",
                skip_cache: skipCache,
                model: selectedModel
            }),
        });

        if (res.status === 401 || res.status === 403) {
            setMessages((prev) => {
                const newMessages = [...prev];
                newMessages[newMessages.length - 1] = {
                    ...newMessages[newMessages.length - 1],
                    text: "Session expired. Please log in again.",
                    isStreaming: false
                };
                return newMessages;
            });
            setIsLoading(false);
            return;
        }

        if (!res.ok) throw new Error(res.statusText);

        // 5. Read SSE stream
        const reader = res.body.getReader();
        const decoder = new TextDecoder();
        let buffer = "";
        let fullText = "";

        while (true) {
            const { done, value } = await reader.read();
            if (done) break;

            buffer += decoder.decode(value, { stream: true });
            const lines = buffer.split("\n");
            buffer = lines.pop() || ""; // Keep incomplete line in buffer

            for (const line of lines) {
                if (line.startsWith("data: ")) {
                    try {
                        const event = JSON.parse(line.slice(6));

                        if (event.type === "status") {
                            // Real-time status from ADK tool calls - advance step
                            setThinkingStepIndex(prev => Math.min(prev + 1, thinkingMessages.length - 1));
                        } else if (event.type === "chunk") {
                            fullText += event.content;
                            // Update the streaming message
                            setMessages((prev) => {
                                const newMessages = [...prev];
                                newMessages[newMessages.length - 1] = {
                                    ...newMessages[newMessages.length - 1],
                                    text: fullText
                                };
                                return newMessages;
                            });
                        } else if (event.type === "done") {
                            // Finalize the message
                            fullText = event.content || fullText;
                            setMessages((prev) => {
                                const newMessages = [...prev];
                                newMessages[newMessages.length - 1] = {
                                    ...newMessages[newMessages.length - 1],
                                    text: fullText,
                                    isStreaming: false
                                };
                                return newMessages;
                            });
                        } else if (event.type === "error") {
                            const errMsg = event.content || "An error occurred.";
                            const isOutage = errMsg.includes("temporarily") || errMsg.includes("knowledge base") || errMsg.includes("system issue");

                            if (isOutage) {
                                // Silent retry once before showing toast (ADK cold-connect)
                                if (!skipCache && !window._lastRetried) {
                                    window._lastRetried = true;
                                    setMessages((prev) => prev.slice(0, -1)); // remove placeholder
                                    setIsLoading(false);
                                    setTimeout(() => {
                                        handleSend(null, finalMessage, false);
                                        setTimeout(() => { window._lastRetried = false; }, 10000);
                                    }, 2000);
                                    return;
                                }
                                window._lastRetried = false;
                                toast("Warming up! Try your question again.", {
                                    duration: 6000,
                                    style: {
                                      background: "linear-gradient(135deg, #1e293b 0%, #0f172a 100%)",
                                      color: "#f1f5f9",
                                      border: "1px solid rgba(99, 102, 241, 0.3)",
                                      borderRadius: "14px",
                                      padding: "14px 18px",
                                      boxShadow: "0 8px 32px rgba(0, 0, 0, 0.25), 0 0 0 1px rgba(99, 102, 241, 0.1)",
                                      backdropFilter: "blur(12px)",
                                      fontSize: "0.88rem",
                                      fontWeight: 500,
                                      letterSpacing: "0.01em",
                                    },
                                    icon: <svg width="18" height="18" viewBox="0 0 24 24" fill="none"><circle cx="12" cy="12" r="9" stroke="url(#tg)" strokeWidth="2" strokeLinecap="round"/><path d="M12 7v5l3 3" stroke="url(#tg)" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/><defs><linearGradient id="tg" x1="3" y1="3" x2="21" y2="21"><stop stopColor="#818cf8"/><stop offset="1" stopColor="#6366f1"/></linearGradient></defs></svg>,
                                });
                                // Remove the placeholder bot message
                                setMessages((prev) => prev.slice(0, -1));
                            } else {
                                setMessages((prev) => {
                                    const newMessages = [...prev];
                                    newMessages[newMessages.length - 1] = {
                                        ...newMessages[newMessages.length - 1],
                                        text: errMsg,
                                        isStreaming: false
                                    };
                                    return newMessages;
                                });
                            }
                        }
                    } catch (parseErr) {
                        console.warn("SSE parse error:", parseErr);
                    }
                }
            }
        }

        // Finalize if stream ended without explicit done
        setMessages((prev) => {
            const newMessages = [...prev];
            const lastMsg = newMessages[newMessages.length - 1];
            if (lastMsg.isStreaming) {
                const cleanText = (lastMsg.text || "").replace(/[\x00-\x09\x0B-\x1F\x7F-\x9F]/g, "").trim();
                newMessages[newMessages.length - 1] = {
                    ...lastMsg,
                    text: cleanText || "I'm sorry, I couldn't generate a response. Please try rephrasing your question.",
                    isStreaming: false
                };
            }
            return newMessages;
        });

    } catch (err) {
        console.error("Send error:", err);
        const isNetworkDown = err.message?.includes("Failed to fetch") || err.message?.includes("NetworkError") || err.message?.includes("network");

        if (isNetworkDown) {
            // Silent retry once before showing toast (backend cold-connect)
            if (!window._lastRetried) {
                window._lastRetried = true;
                setMessages((prev) => {
                    const last = prev[prev.length - 1];
                    if (last && last.sender === "bot" && last.isStreaming) return prev.slice(0, -1);
                    return prev;
                });
                setIsLoading(false);
                setTimeout(() => {
                    handleSend(null, finalMessage, false);
                    setTimeout(() => { window._lastRetried = false; }, 10000);
                }, 2000);
                return;
            }
            window._lastRetried = false;
            toast("Warming up! Try your question again.", {
                duration: 6000,
                style: {
                    background: "linear-gradient(135deg, #1e293b 0%, #0f172a 100%)",
                    color: "#f1f5f9",
                    border: "1px solid rgba(99, 102, 241, 0.3)",
                    borderRadius: "14px",
                    padding: "14px 18px",
                    boxShadow: "0 8px 32px rgba(0, 0, 0, 0.25), 0 0 0 1px rgba(99, 102, 241, 0.1)",
                    backdropFilter: "blur(12px)",
                    fontSize: "0.88rem",
                    fontWeight: 500,
                },
                icon: <svg width="18" height="18" viewBox="0 0 24 24" fill="none"><path d="M21 12a9 9 0 11-6.22-8.56" stroke="url(#dg)" strokeWidth="2" strokeLinecap="round"/><path d="M21 3v5h-5" stroke="url(#dg)" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/><defs><linearGradient id="dg" x1="3" y1="3" x2="21" y2="21"><stop stopColor="#818cf8"/><stop offset="1" stopColor="#6366f1"/></linearGradient></defs></svg>,
            });
            // Remove the placeholder bot message
            setMessages((prev) => {
                const last = prev[prev.length - 1];
                if (last && last.sender === "bot" && last.isStreaming) {
                    return prev.slice(0, -1);
                }
                return prev;
            });
        } else {
            setMessages((prev) => {
                const newMessages = [...prev];
                if (newMessages.length > 0 && newMessages[newMessages.length - 1].sender === "bot") {
                    newMessages[newMessages.length - 1] = {
                        ...newMessages[newMessages.length - 1],
                        text: "Something went wrong. Please try again.",
                        isStreaming: false
                    };
                } else {
                    newMessages.push({ text: "Something went wrong. Please try again.", sender: "bot", time: new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) });
                }
                return newMessages;
            });
        }
    } finally {
        setIsLoading(false);
        // Regain focus
        setTimeout(() => inputRef.current?.focus(), 100);
    }
  };

  // Regenerate last response
  const handleRegenerate = () => {
    const lastUserMsg = [...messages].reverse().find(m => m.sender === "user");
    if (!lastUserMsg) return;
    // Remove last bot message
    setMessages(prev => {
      const copy = [...prev];
      if (copy.length > 0 && copy[copy.length - 1].sender === "bot") {
        copy.pop();
      }
      return copy;
    });
    setTimeout(() => handleSend(null, lastUserMsg.text, true), 50);
  };

  // Drag and drop handlers
  const handleDragOver = (e) => {
    e.preventDefault();
    e.stopPropagation();
    setIsDragging(true);
  };
  const handleDragLeave = (e) => {
    e.preventDefault();
    e.stopPropagation();
    if (!e.currentTarget.contains(e.relatedTarget)) {
      setIsDragging(false);
    }
  };
  const handleDrop = (e) => {
    e.preventDefault();
    e.stopPropagation();
    setIsDragging(false);
    if (e.dataTransfer.files && e.dataTransfer.files[0]) {
      setPendingFile(e.dataTransfer.files[0]);
    }
  };

  // Message animation variants
  const messageVariants = {
    hidden: { opacity: 0, y: 20 },
    visible: { opacity: 1, y: 0, transition: { duration: 0.35, ease: [0.34, 1.56, 0.64, 1] } },
  };

  // Code block renderer for ReactMarkdown
  const codeRenderer = ({ node, className, children, ...props }) => {
    const match = /language-(\w+)/.exec(className || '');
    const codeString = String(children).replace(/\n$/, '');
    const isBlock = match || codeString.includes('\n');

    if (isBlock) {
      const language = match ? match[1] : 'text';
      return (
        <div className="code-block-wrapper">
          <div className="code-block-header">
            <span className="code-lang">{language}</span>
            <button
              className="code-copy-btn"
              onClick={() => {
                navigator.clipboard.writeText(codeString);
                toast.success("Copied to clipboard");
              }}
            >
              Copy
            </button>
          </div>
          <SyntaxHighlighter
            style={oneDark}
            language={language}
            PreTag="div"
            customStyle={{ margin: 0, borderRadius: '0 0 8px 8px', fontSize: '0.85rem' }}
          >
            {codeString}
          </SyntaxHighlighter>
        </div>
      );
    }
    return <code className={className} {...props}>{children}</code>;
  };

  return (
    <div
      className={`chat-main ${isDragging ? 'drag-active' : ''}`}
      onDragOver={handleDragOver}
      onDragLeave={handleDragLeave}
      onDrop={handleDrop}
    >
      {/* Model selector dropdown */}
      <div className="model-selector-header" ref={modelDropdownRef}>
        <button
          className="model-selector-trigger"
          onClick={() => setModelDropdownOpen(prev => !prev)}
          disabled={isLoading}
        >
          <span className="model-selector-name">
            {MODEL_OPTIONS.find(m => m.id === selectedModel)?.name || "iNav"}
          </span>
          <svg width="16" height="16" viewBox="0 0 16 16" fill="none" style={{ opacity: 0.5, marginLeft: 4 }}>
            <path d="M4 6L8 10L12 6" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/>
          </svg>
        </button>
        {modelDropdownOpen && (
          <div className="model-dropdown">
            {MODEL_OPTIONS.map(model => (
              <button
                key={model.id}
                className={`model-dropdown-item ${selectedModel === model.id ? 'active' : ''}`}
                onClick={() => { setSelectedModel(model.id); setModelDropdownOpen(false); }}
              >
                <div className="model-dropdown-info">
                  <span className="model-dropdown-name">{model.name}</span>
                  <span className="model-dropdown-desc">{model.desc}</span>
                </div>
                {selectedModel === model.id && (
                  <svg width="16" height="16" viewBox="0 0 16 16" fill="currentColor" className="model-dropdown-check">
                    <path d="M6.5 12.5L2 8l1.5-1.5L6.5 9.5 12.5 3.5 14 5z"/>
                  </svg>
                )}
              </button>
            ))}
          </div>
        )}
      </div>

      {/* Hidden audio element for TTS playback */}
      <audio ref={audioRef} style={{ display: 'none' }} />

      {/* Drag overlay */}
      {isDragging && (
        <div className="drag-overlay">
          <div className="drag-overlay-content">
            <FaPaperclip size={32} />
            <span>Drop file here</span>
          </div>
        </div>
      )}

      <div className="chat-messages">
        <AnimatePresence initial={false}>
        {messages.length === 0 ? (
          <motion.div
            className="welcome-container"
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.35, ease: [0.34, 1.56, 0.64, 1] }}
          >
            <img src="/msu_logo.webp" alt="MSU Logo" className="welcome-logo" />
            <h1 className="welcome-title">Morgan State CS Navigator</h1>
            <p className="welcome-subtitle">How can I assist with your academic journey today?</p>
            <div className="suggestions">
              {suggestionsLoading ? (
                <>
                  <div className="suggestion-skeleton"></div>
                  <div className="suggestion-skeleton"></div>
                  <div className="suggestion-skeleton"></div>
                </>
              ) : (
                suggestions.map((s, i) => (
                  <button key={i} className="suggestion-btn" onClick={() => handleSuggestion(s)} disabled={isLoading}>
                    {s}
                  </button>
                ))
              )}
            </div>
          </motion.div>
        ) : (
          messages.map((msg, i) => (
            <motion.div
              key={i}
              className={`message ${msg.sender}`}
              variants={messageVariants}
              initial="hidden"
              animate="visible"
            >
              <img
                src={msg.sender === "user" ? userProfilePicture : "/bot_avatar.webp"}
                alt={msg.sender}
                className="avatar-img"
                onError={(e) => { if (msg.sender === "user") e.target.src = "/user_icon.webp"; }}
              />
              <div className="message-content">
                <div className="message-bubble-wrapper">
                  <div className="message-bubble">

                    <ReactMarkdown
                      remarkPlugins={[remarkGfm]}
                      components={{
                          code: codeRenderer,
                          a: ({node, href, children, ...props}) => {
                              const isFile = href && (href.includes("uploads/chat_files") || href.includes("uploads/profile_pictures"));

                              if (isFile) {
                                  return (
                                      <a href={href} target="_blank" rel="noopener noreferrer" className="file-card">
                                          <div className="file-icon-wrapper">
                                              {getFileIcon(children[0])}
                                          </div>
                                          <div className="file-info">
                                              <span className="file-name">{children}</span>
                                              <span className="file-action">Click to view file</span>
                                          </div>
                                      </a>
                                  );
                              }
                              return <a href={href} target="_blank" rel="noopener noreferrer" className="message-link" {...props}>{children}</a>;
                          }
                      }}
                    >
                      {msg.text}
                    </ReactMarkdown>

                    {/* Streaming indicator - show steps when no text, cursor when text is streaming */}
                    {msg.isStreaming && !msg.text && (
                      <div className="stream-status-container">
                        {thinkingMessages.slice(0, thinkingStepIndex).map((step, si) => (
                          <div key={si} className="stream-step completed">
                            <div className="step-icon-wrap done">
                              <svg className="step-check" viewBox="0 0 16 16" fill="none"><path d="M4 8.5l3 3 5-6" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/></svg>
                            </div>
                            <span>{step}</span>
                          </div>
                        ))}
                        <div className="stream-step active">
                          <div className="step-icon-wrap active-icon">
                            {getStatusIcon(thinkingMessages[thinkingStepIndex])}
                          </div>
                          <span className="thinking-text-shimmer">{thinkingMessages[thinkingStepIndex]}</span>
                        </div>
                      </div>
                    )}
                    {msg.isStreaming && msg.text && (
                      <span className="streaming-cursor" aria-hidden="true">
                        <span className="cursor-bar"></span>
                      </span>
                    )}

                    {msg.sender === "bot" && !msg.isStreaming && (
                      <div className="bot-action-row">
                        <button
                          className={`tts-btn${isSpeaking ? ' tts-active' : ''}`}
                          onClick={() => speak(msg.text)}
                          title={isSpeaking ? "Stop speaking" : "Read response aloud"}
                        >
                          {isSpeaking ? <FaStop size={14}/> : <FaVolumeUp size={14}/>}
                        </button>
                        {i === messages.length - 1 && !isLoading && (
                          <button
                            className="regen-icon-btn"
                            onClick={handleRegenerate}
                            title="Regenerate response"
                          >
                            <svg viewBox="0 0 16 16" fill="none" width="14" height="14"><path d="M13.5 8a5.5 5.5 0 11-1.3-3.56" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round"/><path d="M13.5 2.5v2.5H11" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round"/></svg>
                          </button>
                        )}
                      </div>
                    )}
                  </div>

                  {/* 🔥 FEEDBACK MENU - Right side of bot messages */}
                  {msg.sender === "bot" && (
                    <div className="feedback-menu-container">
                      {/* Show feedback status if already given */}
                      {feedbackGiven[i] ? (
                        <div className={`feedback-status feedback-status--${feedbackGiven[i]}`}>
                          {feedbackGiven[i] === 'helpful' && <FaThumbsUp size={12} />}
                          {feedbackGiven[i] === 'not_helpful' && <FaThumbsDown size={12} />}
                          {feedbackGiven[i] === 'report' && <FaFlag size={12} />}
                        </div>
                      ) : (
                        <>
                          {/* Three-dot menu button - visible on hover */}
                          <button
                            className="feedback-menu-btn"
                            onClick={(e) => {
                              e.stopPropagation();
                              setFeedbackMenuOpen(feedbackMenuOpen === i ? null : i);
                            }}
                            title="Rate this response"
                          >
                            <FaEllipsisV size={14} />
                          </button>

                          {/* Dropdown menu */}
                          {feedbackMenuOpen === i && (
                            <div className="feedback-dropdown">
                              <button
                                className="feedback-option feedback-option--helpful"
                                onClick={() => handleFeedback(i, 'helpful', msg.text)}
                              >
                                <FaThumbsUp size={14} />
                                <span>Helpful</span>
                              </button>
                              <button
                                className="feedback-option feedback-option--not-helpful"
                                onClick={() => handleFeedback(i, 'not_helpful', msg.text)}
                              >
                                <FaThumbsDown size={14} />
                                <span>Not Helpful</span>
                              </button>
                              <button
                                className="feedback-option feedback-option--report"
                                onClick={() => openReportModal(i)}
                              >
                                <FaFlag size={14} />
                                <span>Report Issue</span>
                              </button>
                            </div>
                          )}
                        </>
                      )}
                    </div>
                  )}
                </div>
                <div className="timestamp">{msg.time}</div>
              </div>
            </motion.div>
          ))
        )}
        </AnimatePresence>

        {/* Old regenerate button removed - now inline with bot message actions */}

        {/* Thinking Indicator - shown before streaming starts */}
        {isLoading && !messages.some(m => m.isStreaming) && (
          <div className="message bot">
            <img src="/bot_avatar.webp" alt="Bot" className="avatar-img" />
            <div className="message-content">
              <div className="message-bubble thinking-bubble">
                <div className="stream-status-container">
                  {thinkingMessages.slice(0, thinkingStepIndex).map((step, si) => (
                    <div key={si} className="stream-step completed">
                      <div className="step-icon-wrap done">
                        <svg className="step-check" viewBox="0 0 16 16" fill="none"><path d="M4 8.5l3 3 5-6" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/></svg>
                      </div>
                      <span>{step}</span>
                    </div>
                  ))}
                  <div className="stream-step active">
                    <div className="step-icon-wrap active-icon">
                      {getStatusIcon(thinkingMessages[thinkingStepIndex])}
                    </div>
                    <span className="thinking-text-shimmer">{thinkingMessages[thinkingStepIndex]}</span>
                    <span className="thinking-timer">{thinkingTimer}s</span>
                  </div>
                </div>
              </div>
            </div>
          </div>
        )}

        <div ref={messagesEndRef} />

        {/* 🔥 Voice Mode Overlay - Seamless ChatGPT-style */}
        {isVoiceMode && (
          <div className="voice-overlay">
            <div className="voice-orb-container">
              <div className={`voice-orb ${voiceStatus}`}>
                <div className="orb-ring ring-1"></div>
                <div className="orb-ring ring-2"></div>
                <div className="orb-ring ring-3"></div>
                <div className="orb-core">
                  {voiceStatus === "listening" && <FaMicrophone size={32} />}
                  {voiceStatus === "processing" && <div className="orb-spinner" />}
                  {voiceStatus === "speaking" && <FaVolumeUp size={32} />}
                  {voiceStatus === "idle" && <FaMicrophone size={32} />}
                </div>
              </div>
              <p className="voice-label">
                {voiceStatus === "listening" && "Listening..."}
                {voiceStatus === "processing" && "Thinking..."}
                {voiceStatus === "speaking" && "Speaking..."}
                {voiceStatus === "idle" && "Ready"}
              </p>
              <button className="voice-end-btn" onClick={toggleVoiceMode}>
                End
              </button>
            </div>
          </div>
        )}

        {/* 🔥 REPORT MODAL */}
        {reportModal !== null && (
          <div className="report-modal-overlay" onClick={closeReportModal}>
            <div className="report-modal" onClick={(e) => e.stopPropagation()}>
              <div className="report-modal-header">
                <h3>Report an Issue</h3>
                <button className="report-modal-close" onClick={closeReportModal}>
                  <FaTimes size={16} />
                </button>
              </div>
              <div className="report-modal-body">
                <p>Help us improve! What was wrong with this response?</p>
                <textarea
                  className="report-textarea"
                  placeholder="Describe the issue (e.g., incorrect information, unhelpful response, inappropriate content...)"
                  value={reportText}
                  onChange={(e) => setReportText(e.target.value)}
                  rows={4}
                />
              </div>
              <div className="report-modal-footer">
                <button className="report-cancel-btn" onClick={closeReportModal}>
                  Cancel
                </button>
                <button
                  className="report-submit-btn"
                  onClick={() => handleFeedback(reportModal, 'report', messages[reportModal]?.text)}
                  disabled={!reportText.trim()}
                >
                  Submit Report
                </button>
              </div>
            </div>
          </div>
        )}
      </div>

      <div className="chat-input-container">

        <form onSubmit={handleSend} className="chat-input-wrapper">

          {/* 🔥 STAGING AREA: Shows file before sending */}
          {pendingFile && (
            <div className="attachment-preview">
              {getFileIcon(pendingFile.name)}
              <span className="file-name-preview">{pendingFile.name}</span>
              <button
                type="button"
                className="remove-attachment-btn"
                onClick={clearFile}
                title="Remove file"
              >
                <FaTimes />
              </button>
            </div>
          )}

          <div className="input-row">
            <button
                type="button"
                className="action-btn-icon"
                onClick={() => fileInputRef.current.click()}
                title="Attach a file"
                disabled={isLoading || isVoiceMode}
            >
                <FaPaperclip size={18} />
            </button>

            <input
                type="file"
                ref={fileInputRef}
                style={{ display: 'none' }}
                accept=".png,.jpg,.jpeg,.gif,.pdf,.txt,.doc,.docx"
                onChange={handleFileSelect}
            />

            <button
                type="button"
                className={`action-btn-icon voice-btn ${isListening ? 'listening' : ''}`}
                onClick={handleVoiceInput}
                title="Voice input"
                disabled={isLoading || isSpeaking || isVoiceMode}
            >
                <FaMicrophone size={18} />
            </button>

            <textarea
                rows={1}
                ref={inputRef}
                className="chat-input-field"
                value={input}
                maxLength={2000}
                onChange={(e) => { setInput(e.target.value.slice(0, 2000)); resizeTextarea(); }}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' && !e.shiftKey) {
                    e.preventDefault();
                    handleSend(e);
                  }
                }}
                placeholder={isVoiceMode ? (voiceStatus === "listening" ? "Listening..." : voiceStatus === "speaking" ? "Speaking..." : "Speak now...") : pendingFile ? "Add a message..." : "Type your message..."}
                disabled={isLoading || isVoiceMode}
            />

            <button
                type="submit"
                className="action-btn-icon send-btn"
                title="Send message"
                disabled={isLoading || (!input.trim() && !pendingFile) || isVoiceMode}
            >
                <BsArrowUpCircleFill size={24} />
            </button>

            {/* Model toggle removed - now in header dropdown */}

            {/* Live Voice Mode Button */}
            <button
                type="button"
                className={`live-mode-btn ${isVoiceMode ? 'active' : ''}`}
                onClick={toggleVoiceMode}
                title={isVoiceMode ? "Exit Live Mode" : "Enter Live Mode"}
                disabled={isLoading}
            >
                <BsSoundwave size={18} />
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}