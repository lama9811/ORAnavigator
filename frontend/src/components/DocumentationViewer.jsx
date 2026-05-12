import React, { useState, useEffect, useRef } from 'react';
import { FaTimes } from '@react-icons/all-files/fa/FaTimes';
import { FaBook } from '@react-icons/all-files/fa/FaBook';
import { FaProjectDiagram } from '@react-icons/all-files/fa/FaProjectDiagram';
import { FaServer } from '@react-icons/all-files/fa/FaServer';
import { FaCode } from '@react-icons/all-files/fa/FaCode';
import { FaRoad } from '@react-icons/all-files/fa/FaRoad';
import { FaCheckCircle } from '@react-icons/all-files/fa/FaCheckCircle';
import { FaClock } from '@react-icons/all-files/fa/FaClock';
import { FaRocket } from '@react-icons/all-files/fa/FaRocket';
import { FaRobot } from '@react-icons/all-files/fa/FaRobot';
import { FaDatabase } from '@react-icons/all-files/fa/FaDatabase';
import { FaGraduationCap } from '@react-icons/all-files/fa/FaGraduationCap';
import { FaBriefcase } from '@react-icons/all-files/fa/FaBriefcase';
import { FaGamepad } from '@react-icons/all-files/fa/FaGamepad';
import { FaHeart } from '@react-icons/all-files/fa/FaHeart';
import { FaChartLine } from '@react-icons/all-files/fa/FaChartLine';
import { FaMagic } from '@react-icons/all-files/fa/FaMagic';
import { FaEnvelope } from '@react-icons/all-files/fa/FaEnvelope';
import { FaCalendarAlt } from '@react-icons/all-files/fa/FaCalendarAlt';
import { FaBell } from '@react-icons/all-files/fa/FaBell';
import { FaFileAlt } from '@react-icons/all-files/fa/FaFileAlt';
import { FaUsers } from '@react-icons/all-files/fa/FaUsers';
import { FaLightbulb } from '@react-icons/all-files/fa/FaLightbulb';
import { FaBolt } from '@react-icons/all-files/fa/FaBolt';
import { FaBullseye } from '@react-icons/all-files/fa/FaBullseye';
import { FaCog } from '@react-icons/all-files/fa/FaCog';
import { FaEye } from '@react-icons/all-files/fa/FaEye';
import { FaCheck } from '@react-icons/all-files/fa/FaCheck';
import { FaAward } from '@react-icons/all-files/fa/FaAward';
import { FaFire } from '@react-icons/all-files/fa/FaFire';
import { FaStar } from '@react-icons/all-files/fa/FaStar';
import { FaTrophy } from '@react-icons/all-files/fa/FaTrophy';
import { FaExclamationTriangle } from '@react-icons/all-files/fa/FaExclamationTriangle';
import { FaHandshake } from '@react-icons/all-files/fa/FaHandshake';
import { FaComments } from '@react-icons/all-files/fa/FaComments';
import { FaLock } from '@react-icons/all-files/fa/FaLock';
import { FaMapMarkerAlt } from '@react-icons/all-files/fa/FaMapMarkerAlt';
import { FaClipboardList } from '@react-icons/all-files/fa/FaClipboardList';
import { FaBookOpen } from '@react-icons/all-files/fa/FaBookOpen';
import { FaClock as FaClockIcon } from '@react-icons/all-files/fa/FaClock';
import { FaSearch } from '@react-icons/all-files/fa/FaSearch';
import { FaUserFriends } from '@react-icons/all-files/fa/FaUserFriends';
import { FaStickyNote } from '@react-icons/all-files/fa/FaStickyNote';
import { FaExchangeAlt } from '@react-icons/all-files/fa/FaExchangeAlt';
import { FaMoneyBillWave } from '@react-icons/all-files/fa/FaMoneyBillWave';
import { FaBuilding } from '@react-icons/all-files/fa/FaBuilding';
import { FaQuestionCircle } from '@react-icons/all-files/fa/FaQuestionCircle';
import './DocumentationViewer.css';

export default function DocumentationViewer({ isOpen, onClose, darkMode, mode = 'technical' }) {
  const [activeTab, setActiveTab] = useState('overview');
  const mermaidRef = useRef(null);

  const technicalTabs = [
    { id: 'overview', label: 'Overview', icon: FaBook },
    { id: 'architecture', label: 'Architecture', icon: FaProjectDiagram },
    { id: 'workflow', label: 'Workflow', icon: FaServer },
    { id: 'api', label: 'API Docs', icon: FaCode },
  ];

  const roadmapTabs = [
    { id: 'completed', label: 'Completed', icon: FaCheckCircle },
    { id: 'current', label: 'In Progress', icon: FaClock },
    { id: 'planned', label: 'Planned', icon: FaRocket },
  ];

  const agentsTabs = [
    { id: 'agents-overview', label: 'Overview', icon: FaRobot },
    { id: 'agents-automation', label: 'Automation', icon: FaMagic },
    { id: 'agents-knowledge', label: 'Knowledge', icon: FaDatabase },
    { id: 'agents-academic', label: 'Academic', icon: FaGraduationCap },
    { id: 'agents-career', label: 'Career', icon: FaBriefcase },
    { id: 'agents-engagement', label: 'Engagement', icon: FaGamepad },
    { id: 'agents-wellness', label: 'Wellness', icon: FaHeart },
    { id: 'agents-analytics', label: 'Analytics', icon: FaChartLine },
    { id: 'agents-social', label: 'Social', icon: FaUsers },
    { id: 'agents-future', label: 'Future Dev', icon: FaRocket },
  ];

  const tabs = mode === 'roadmap' ? roadmapTabs : mode === 'agents' ? agentsTabs : technicalTabs;

  useEffect(() => {
    if (isOpen) {
      if (mode === 'roadmap') setActiveTab('completed');
      else if (mode === 'agents') setActiveTab('agents-overview');
      else setActiveTab('overview');
    }
  }, [isOpen, mode]);

  // Render mermaid diagrams
  useEffect(() => {
    if (isOpen && (activeTab === 'architecture' || activeTab === 'workflow' || activeTab.startsWith('agents-'))) {
      const renderMermaid = async () => {
        try {
          const mermaid = (await import('mermaid')).default;
          mermaid.initialize({
            startOnLoad: false,
            theme: darkMode ? 'dark' : 'default',
            securityLevel: 'loose',
          });

          const elements = document.querySelectorAll('.mermaid-diagram');
          elements.forEach(async (el, index) => {
            const code = el.getAttribute('data-diagram');
            if (code) {
              try {
                const { svg } = await mermaid.render(`mermaid-${activeTab}-${index}`, code);
                el.innerHTML = svg;
              } catch (e) {
                console.error('Mermaid render error:', e);
              }
            }
          });
        } catch (e) {
          console.error('Mermaid import error:', e);
        }
      };

      setTimeout(renderMermaid, 100);
    }
  }, [isOpen, activeTab, darkMode]);

  if (!isOpen) return null;

  const renderOverview = () => (
    <div className="doc-section">
      <h2>CS Navigator - Technical Documentation</h2>
      <p className="doc-intro">
        CS Navigator is an AI-powered academic assistant that uses RAG (Retrieval-Augmented Generation)
        to help Computer Science students at Morgan State University.
      </p>

      <h3>Technology Stack</h3>
      <table className="doc-table">
        <thead>
          <tr>
            <th>Layer</th>
            <th>Technology</th>
            <th>Purpose</th>
          </tr>
        </thead>
        <tbody>
          <tr><td>Frontend</td><td>React + Vite</td><td>Modern SPA with fast HMR</td></tr>
          <tr><td>Backend</td><td>FastAPI (Python)</td><td>High-performance REST API</td></tr>
          <tr><td>Database</td><td>AWS RDS MySQL</td><td>User data, chat history</td></tr>
          <tr><td>Vector DB</td><td>Pinecone</td><td>Semantic search for RAG</td></tr>
          <tr><td>AI Model</td><td>OpenAI GPT-3.5</td><td>Response generation</td></tr>
          <tr><td>Deployment</td><td>Docker + EC2</td><td>Containerized cloud hosting</td></tr>
        </tbody>
      </table>

      <h3>Key Features</h3>
      <ul className="doc-features">
        <li><strong>AI Chat</strong> - Context-aware responses grounded in university data</li>
        <li><strong>Voice Mode</strong> - Speech-to-text and text-to-speech</li>
        <li><strong>Curriculum Tracker</strong> - Visual degree progress</li>
        <li><strong>DegreeWorks Parser</strong> - Upload PDF transcripts</li>
        <li><strong>Multi-Session</strong> - Multiple conversation threads</li>
        <li><strong>Admin Dashboard</strong> - Knowledge base management</li>
      </ul>

      <h3>Knowledge Sources</h3>
      <p>The chatbot is trained on <strong>11 curated knowledge sources</strong> including:</p>
      <div className="doc-grid">
        <span>CS Course Catalog</span>
        <span>Degree Requirements</span>
        <span>Faculty Directory</span>
        <span>Career Resources</span>
        <span>Campus Facilities</span>
        <span>Academic Policies</span>
        <span>Student Organizations</span>
        <span>Research Opportunities</span>
        <span>Graduate Programs</span>
        <span>FAQ</span>
        <span>Contact Info</span>
      </div>
    </div>
  );

  const renderArchitecture = () => (
    <div className="doc-section">
      <h2>System Architecture</h2>
      <p>The system follows a modern microservices architecture with clear separation of concerns.</p>

      <div
        className="mermaid-diagram"
        data-diagram={`flowchart TB
    subgraph Client["Client Layer"]
        Browser["Web Browser"]
        React["React SPA"]
    end

    subgraph Server["Application Layer"]
        Nginx["Nginx Proxy"]
        FastAPI["FastAPI Backend"]
    end

    subgraph AI["AI/ML Layer"]
        LangChain["LangChain"]
        OpenAI["OpenAI GPT-3.5"]
    end

    subgraph Data["Data Layer"]
        MySQL[("AWS RDS MySQL")]
        Pinecone[("Pinecone Vector DB")]
    end

    Browser --> React
    React --> Nginx
    Nginx --> FastAPI
    FastAPI --> LangChain
    LangChain --> OpenAI
    LangChain --> Pinecone
    FastAPI --> MySQL`}
      />

      <h3>Layer Descriptions</h3>
      <table className="doc-table">
        <thead>
          <tr><th>Layer</th><th>Components</th><th>Responsibility</th></tr>
        </thead>
        <tbody>
          <tr>
            <td><strong>Client</strong></td>
            <td>React, Vite, Tailwind</td>
            <td>User interface, state management, API calls</td>
          </tr>
          <tr>
            <td><strong>Application</strong></td>
            <td>Nginx, FastAPI, JWT</td>
            <td>Request routing, authentication, business logic</td>
          </tr>
          <tr>
            <td><strong>AI/ML</strong></td>
            <td>LangChain, OpenAI</td>
            <td>RAG pipeline, embeddings, response generation</td>
          </tr>
          <tr>
            <td><strong>Data</strong></td>
            <td>MySQL, Pinecone</td>
            <td>Persistent storage, vector similarity search</td>
          </tr>
        </tbody>
      </table>
    </div>
  );

  const renderWorkflow = () => (
    <div className="doc-section">
      <h2>RAG Pipeline Workflow</h2>
      <p>How questions are processed and answered using Retrieval-Augmented Generation.</p>

      <div
        className="mermaid-diagram"
        data-diagram={`sequenceDiagram
    participant U as User
    participant F as Frontend
    participant B as Backend
    participant P as Pinecone
    participant O as OpenAI

    U->>F: Ask question
    F->>B: POST /chat
    B->>B: Embed query (1536 dims)
    B->>P: Vector similarity search
    P-->>B: Top 5 relevant docs
    B->>O: Query + Context
    O-->>B: Generated answer
    B-->>F: JSON response
    F-->>U: Display answer`}
      />

      <h3>RAG Process Steps</h3>
      <div className="workflow-steps">
        <div className="step">
          <div className="step-number">1</div>
          <div className="step-content">
            <h4>EMBED</h4>
            <p>Convert user question to a 1536-dimensional vector using OpenAI embeddings</p>
          </div>
        </div>
        <div className="step">
          <div className="step-number">2</div>
          <div className="step-content">
            <h4>RETRIEVE</h4>
            <p>Search Pinecone for semantically similar documents from knowledge base</p>
          </div>
        </div>
        <div className="step">
          <div className="step-number">3</div>
          <div className="step-content">
            <h4>AUGMENT</h4>
            <p>Combine retrieved context with the original question into a prompt</p>
          </div>
        </div>
        <div className="step">
          <div className="step-number">4</div>
          <div className="step-content">
            <h4>GENERATE</h4>
            <p>GPT-3.5 produces a grounded response based on actual university data</p>
          </div>
        </div>
      </div>
    </div>
  );

  const renderAPI = () => (
    <div className="doc-section">
      <h2>API Documentation</h2>
      <p>REST API endpoints available in the FastAPI backend.</p>

      <h3>Authentication</h3>
      <table className="doc-table">
        <thead>
          <tr><th>Endpoint</th><th>Method</th><th>Description</th></tr>
        </thead>
        <tbody>
          <tr><td><code>/api/register</code></td><td>POST</td><td>Create new user account</td></tr>
          <tr><td><code>/api/login</code></td><td>POST</td><td>Authenticate, receive JWT token</td></tr>
          <tr><td><code>/api/profile</code></td><td>GET/PUT</td><td>View or update user profile</td></tr>
        </tbody>
      </table>

      <h3>Chat</h3>
      <table className="doc-table">
        <thead>
          <tr><th>Endpoint</th><th>Method</th><th>Description</th></tr>
        </thead>
        <tbody>
          <tr><td><code>/chat</code></td><td>POST</td><td>Send message, receive AI response</td></tr>
          <tr><td><code>/chat-history</code></td><td>GET</td><td>Retrieve conversation history</td></tr>
          <tr><td><code>/sessions</code></td><td>GET/POST/DELETE</td><td>Manage chat sessions</td></tr>
          <tr><td><code>/reset-history</code></td><td>POST</td><td>Clear chat history</td></tr>
        </tbody>
      </table>

      <h3>Academic</h3>
      <table className="doc-table">
        <thead>
          <tr><th>Endpoint</th><th>Method</th><th>Description</th></tr>
        </thead>
        <tbody>
          <tr><td><code>/api/curriculum</code></td><td>GET</td><td>Get CS curriculum data</td></tr>
          <tr><td><code>/api/degreeworks</code></td><td>GET</td><td>View degree audit data</td></tr>
          <tr><td><code>/api/degreeworks/upload-pdf</code></td><td>POST</td><td>Upload transcript PDF</td></tr>
        </tbody>
      </table>

      <h3>Admin</h3>
      <table className="doc-table">
        <thead>
          <tr><th>Endpoint</th><th>Method</th><th>Description</th></tr>
        </thead>
        <tbody>
          <tr><td><code>/api/admin/users</code></td><td>GET</td><td>List all users</td></tr>
          <tr><td><code>/api/admin/analytics</code></td><td>GET</td><td>View usage analytics</td></tr>
          <tr><td><code>/api/admin/tickets</code></td><td>GET</td><td>View support tickets</td></tr>
          <tr><td><code>/api/admin/knowledge-base</code></td><td>GET</td><td>Search knowledge base</td></tr>
        </tbody>
      </table>

      <h3>Request Example</h3>
      <pre className="code-block">
{`POST /chat
Authorization: Bearer <jwt_token>
Content-Type: application/json

{
  "query": "What are the prerequisites for COSC 311?",
  "session_id": "default"
}`}
      </pre>

      <h3>Response Example</h3>
      <pre className="code-block">
{`{
  "response": "COSC 311 (Data Structures) requires COSC 211
              (Object-Oriented Programming) and MATH 241
              (Calculus I) as prerequisites."
}`}
      </pre>
    </div>
  );

  // ========== ROADMAP CONTENT ==========
  const renderCompleted = () => (
    <div className="doc-section">
      <h2>Phase 1: Core Platform (Completed)</h2>
      <p className="phase-desc">Foundation and essential features</p>

      <div className="roadmap-list">
        <div className="roadmap-item done">
          <FaCheckCircle className="item-icon" />
          <div><strong>RAG Chat Engine</strong> - Vector search + GPT responses grounded in MSU data</div>
        </div>
        <div className="roadmap-item done">
          <FaCheckCircle className="item-icon" />
          <div><strong>Auth & Sessions</strong> - JWT authentication, multi-conversation support</div>
        </div>
        <div className="roadmap-item done">
          <FaCheckCircle className="item-icon" />
          <div><strong>Curriculum Tracker</strong> - Visual degree progress with DegreeWorks parsing</div>
        </div>
        <div className="roadmap-item done">
          <FaCheckCircle className="item-icon" />
          <div><strong>Voice Mode</strong> - OpenAI TTS/STT for hands-free interaction</div>
        </div>
        <div className="roadmap-item done">
          <FaCheckCircle className="item-icon" />
          <div><strong>Admin Dashboard</strong> - User management, tickets, knowledge base editor</div>
        </div>
        <div className="roadmap-item done">
          <FaCheckCircle className="item-icon" />
          <div><strong>11 Knowledge Sources</strong> - Courses, faculty, careers, policies, FAQs</div>
        </div>
      </div>

      <h3>Proven Impact (Industry Research)</h3>
      <table className="doc-table">
        <thead>
          <tr><th>University</th><th>Chatbot</th><th>Result</th></tr>
        </thead>
        <tbody>
          <tr><td>Georgia State</td><td>Pounce</td><td>21.4% reduction in summer melt</td></tr>
          <tr><td>U. of Galway</td><td>Cara</td><td>80%+ inquiries handled autonomously</td></tr>
          <tr><td>Staffordshire</td><td>Beacon</td><td>Improved retention rates</td></tr>
        </tbody>
      </table>
    </div>
  );

  const renderCurrent = () => (
    <div className="doc-section">
      <h2>Phase 2: Optimization (In Progress)</h2>
      <p className="phase-desc">Performance improvements and quality assurance</p>

      <div className="metrics-grid">
        <div className="metric-card">
          <span className="metric-label">Response Time</span>
          <span className="metric-current">2.5s</span>
          <span className="metric-arrow">→</span>
          <span className="metric-target">&lt;1.5s</span>
        </div>
        <div className="metric-card">
          <span className="metric-label">Accuracy</span>
          <span className="metric-current">88%</span>
          <span className="metric-arrow">→</span>
          <span className="metric-target">92%</span>
        </div>
        <div className="metric-card">
          <span className="metric-label">Test Coverage</span>
          <span className="metric-current">40%</span>
          <span className="metric-arrow">→</span>
          <span className="metric-target">80%</span>
        </div>
      </div>

      <div className="roadmap-list">
        <div className="roadmap-item progress">
          <FaClock className="item-icon" />
          <div><strong>Hybrid Search</strong> - Combine vector + BM25 keyword search (+15-20% accuracy)</div>
        </div>
        <div className="roadmap-item progress">
          <FaClock className="item-icon" />
          <div><strong>Redis Caching</strong> - 40-60% faster responses for common queries</div>
        </div>
        <div className="roadmap-item progress">
          <FaClock className="item-icon" />
          <div><strong>CI/CD Pipeline</strong> - Automated testing and deployment</div>
        </div>
        <div className="roadmap-item progress">
          <FaClock className="item-icon" />
          <div><strong>Security Hardening</strong> - Audit and penetration testing</div>
        </div>
      </div>
    </div>
  );

  const renderPlanned = () => (
    <div className="doc-section">
      <h2>Phase 3-4: Scale & Commercialize</h2>
      <p className="phase-desc">University-wide expansion and revenue generation</p>

      <h3>Feature Expansion</h3>
      <div className="roadmap-list">
        <div className="roadmap-item planned">
          <FaRocket className="item-icon" />
          <div><strong>Multilingual Support</strong> - Spanish, Mandarin for diverse student body</div>
        </div>
        <div className="roadmap-item planned">
          <FaRocket className="item-icon" />
          <div><strong>Proactive Notifications</strong> - Deadline reminders, registration alerts</div>
        </div>
        <div className="roadmap-item planned">
          <FaRocket className="item-icon" />
          <div><strong>LMS Integration</strong> - Connect with Canvas and Banner SIS</div>
        </div>
        <div className="roadmap-item planned">
          <FaRocket className="item-icon" />
          <div><strong>Mobile App</strong> - React Native for iOS/Android</div>
        </div>
        <div className="roadmap-item planned">
          <FaRocket className="item-icon" />
          <div><strong>GraphRAG</strong> - Complex multi-hop queries across knowledge graph</div>
        </div>
      </div>

      <h3>Scale Targets</h3>
      <table className="doc-table">
        <thead>
          <tr><th>Metric</th><th>Current</th><th>12 Months</th></tr>
        </thead>
        <tbody>
          <tr><td>Active Users</td><td>500</td><td>10,000+</td></tr>
          <tr><td>Daily Messages</td><td>500</td><td>20,000+</td></tr>
          <tr><td>Knowledge Sources</td><td>11</td><td>200+</td></tr>
        </tbody>
      </table>

      <h3>Revenue Model</h3>
      <p>White-label SaaS for other universities: $1,500 - $7,500/month tiered pricing</p>
      <table className="doc-table">
        <thead>
          <tr><th>Year</th><th>Clients</th><th>Revenue</th></tr>
        </thead>
        <tbody>
          <tr><td>Year 1</td><td>5</td><td>$150K</td></tr>
          <tr><td>Year 2</td><td>15</td><td>$540K</td></tr>
          <tr><td>Year 3</td><td>40</td><td>$1.68M</td></tr>
        </tbody>
      </table>
    </div>
  );

  // ========== AI AGENTS CONTENT ==========
  const renderAgentsOverview = () => (
    <div className="doc-section agents-section">
      <h2>AI Agents Plan</h2>
      <p className="doc-intro">
        Transform CS Navigator with autonomous AI agents that can reason, act, and complete complex tasks
        to better serve students and faculty. Think of agents as smart assistants that work 24/7.
      </p>

      {/* Hero Stats */}
      <div className="agents-hero-stats">
        <div className="hero-stat">
          <span className="hero-stat-icon"><FaRobot /></span>
          <span className="hero-stat-number">50+</span>
          <span className="hero-stat-label">Features Planned</span>
        </div>
        <div className="hero-stat">
          <span className="hero-stat-icon"><FaBolt /></span>
          <span className="hero-stat-number">8</span>
          <span className="hero-stat-label">Agent Categories</span>
        </div>
        <div className="hero-stat">
          <span className="hero-stat-icon"><FaBullseye /></span>
          <span className="hero-stat-number">90%</span>
          <span className="hero-stat-label">Task Automation</span>
        </div>
        <div className="hero-stat">
          <span className="hero-stat-icon"><FaLightbulb /></span>
          <span className="hero-stat-number">24/7</span>
          <span className="hero-stat-label">Always Available</span>
        </div>
      </div>

      <h3>What Are AI Agents? (Simple Version)</h3>
      <div className="simple-explainer">
        <div className="explainer-card">
          <span className="explainer-emoji"><FaLightbulb /></span>
          <h4>They Think</h4>
          <p>Understand what you need and figure out the best way to help</p>
        </div>
        <div className="explainer-arrow">→</div>
        <div className="explainer-card">
          <span className="explainer-emoji"><FaCog /></span>
          <h4>They Act</h4>
          <p>Use tools like email, calendar, search to complete tasks</p>
        </div>
        <div className="explainer-arrow">→</div>
        <div className="explainer-card">
          <span className="explainer-emoji"><FaEye /></span>
          <h4>They Check</h4>
          <p>Make sure the task was done correctly before finishing</p>
        </div>
        <div className="explainer-arrow">→</div>
        <div className="explainer-card">
          <span className="explainer-emoji"><FaCheck /></span>
          <h4>They Deliver</h4>
          <p>Give you exactly what you asked for, no extra steps needed</p>
        </div>
      </div>

      <h3>Before vs After AI Agents</h3>
      <div className="comparison-cards">
        <div className="comparison-card before">
          <div className="comparison-header"><FaExclamationTriangle style={{marginRight: '8px'}} /> Without Agents</div>
          <ul>
            <li>Manually check registration dates</li>
            <li>Search for advisor office hours</li>
            <li>Calculate GPA by hand</li>
            <li>Miss deadlines and reminders</li>
            <li>No career guidance</li>
            <li>One-size-fits-all responses</li>
          </ul>
        </div>
        <div className="comparison-card after">
          <div className="comparison-header"><FaRocket style={{marginRight: '8px'}} /> With Agents</div>
          <ul>
            <li>Auto-reminder before registration opens</li>
            <li>Book meetings with one message</li>
            <li>Instant GPA predictions</li>
            <li>Smart deadline tracking</li>
            <li>Personalized career paths</li>
            <li>Tailored advice for YOUR situation</li>
          </ul>
        </div>
      </div>

      <h3>Agent Categories at a Glance</h3>
      <div className="agent-category-grid">
        <div className="agent-cat-card" style={{borderColor: '#3b82f6'}}>
          <span className="cat-icon"><FaEnvelope /></span>
          <h4>Automation</h4>
          <p>Email, calendar, notifications, forms</p>
        </div>
        <div className="agent-cat-card" style={{borderColor: '#10b981'}}>
          <span className="cat-icon"><FaDatabase /></span>
          <h4>Knowledge</h4>
          <p>Auto-update course info, faculty data</p>
        </div>
        <div className="agent-cat-card" style={{borderColor: '#8b5cf6'}}>
          <span className="cat-icon"><FaGraduationCap /></span>
          <h4>Academic</h4>
          <p>GPA calc, prereqs, degree planning</p>
        </div>
        <div className="agent-cat-card" style={{borderColor: '#f59e0b'}}>
          <span className="cat-icon"><FaBriefcase /></span>
          <h4>Career</h4>
          <p>Resume review, interviews, jobs</p>
        </div>
        <div className="agent-cat-card" style={{borderColor: '#ec4899'}}>
          <span className="cat-icon"><FaGamepad /></span>
          <h4>Engagement</h4>
          <p>Streaks, badges, challenges</p>
        </div>
        <div className="agent-cat-card" style={{borderColor: '#ef4444'}}>
          <span className="cat-icon"><FaHeart /></span>
          <h4>Wellness</h4>
          <p>Check-ins, resources, support</p>
        </div>
        <div className="agent-cat-card" style={{borderColor: '#06b6d4'}}>
          <span className="cat-icon"><FaChartLine /></span>
          <h4>Analytics</h4>
          <p>Trends, insights, reports</p>
        </div>
        <div className="agent-cat-card" style={{borderColor: '#84cc16'}}>
          <span className="cat-icon"><FaHandshake /></span>
          <h4>Social</h4>
          <p>Study buddies, mentors, groups</p>
        </div>
      </div>

      <h3>Implementation Timeline</h3>
      <div className="timeline-visual">
        <div className="timeline-phase-card phase-1-card">
          <div className="phase-badge">Phase 1</div>
          <h4>Foundation</h4>
          <span className="phase-time">Months 1-2</span>
          <div className="phase-features">
            <span>✓ Auto Emails</span>
            <span>✓ Calendar Booking</span>
            <span>✓ Smart Reminders</span>
            <span>✓ GPA Calculator</span>
          </div>
        </div>
        <div className="timeline-connector"></div>
        <div className="timeline-phase-card phase-2-card">
          <div className="phase-badge">Phase 2</div>
          <h4>Engagement</h4>
          <span className="phase-time">Months 3-4</span>
          <div className="phase-features">
            <span>✓ Daily Streaks</span>
            <span>✓ Badges & XP</span>
            <span>✓ Study Buddies</span>
            <span>✓ Progress Tracking</span>
          </div>
        </div>
        <div className="timeline-connector"></div>
        <div className="timeline-phase-card phase-3-card">
          <div className="phase-badge">Phase 3</div>
          <h4>Career</h4>
          <span className="phase-time">Months 5-6</span>
          <div className="phase-features">
            <span>✓ Resume Review</span>
            <span>✓ Mock Interviews</span>
            <span>✓ Job Matching</span>
            <span>✓ Scholarship Finder</span>
          </div>
        </div>
        <div className="timeline-connector"></div>
        <div className="timeline-phase-card phase-4-card">
          <div className="phase-badge">Phase 4</div>
          <h4>Advanced</h4>
          <span className="phase-time">Months 7-8</span>
          <div className="phase-features">
            <span>✓ Faculty Dashboard</span>
            <span>✓ Intervention Alerts</span>
            <span>✓ Smart Recommender</span>
            <span>✓ Full Analytics</span>
          </div>
        </div>
      </div>
    </div>
  );

  // ========== NEW AUTOMATION TAB ==========
  const renderAgentsAutomation = () => (
    <div className="doc-section agents-section">
      <h2>Workflow Automation</h2>
      <p className="doc-intro">
        Let AI handle repetitive tasks so students and staff can focus on what matters.
        These agents work in the background to save everyone time.
      </p>

      {/* Automation Hero */}
      <div className="automation-hero">
        <div className="automation-stat">
          <FaEnvelope className="auto-icon" />
          <span className="auto-value">Auto Email</span>
          <span className="auto-desc">Send reminders & follow-ups</span>
        </div>
        <div className="automation-stat">
          <FaCalendarAlt className="auto-icon" />
          <span className="auto-value">Calendar</span>
          <span className="auto-desc">Book meetings instantly</span>
        </div>
        <div className="automation-stat">
          <FaBell className="auto-icon" />
          <span className="auto-value">Notifications</span>
          <span className="auto-desc">Never miss a deadline</span>
        </div>
        <div className="automation-stat">
          <FaFileAlt className="auto-icon" />
          <span className="auto-value">Forms</span>
          <span className="auto-desc">Auto-fill applications</span>
        </div>
      </div>

      <h3><FaEnvelope style={{marginRight: '8px', color: '#0052CC'}} /> Auto Email Agent</h3>
      <div className="feature-showcase">
        <div className="feature-desc">
          <p><strong>What it does:</strong> Automatically sends emails on behalf of students with their permission.</p>
          <div className="use-cases">
            <div className="use-case">
              <span className="use-icon"><FaEnvelope /></span>
              <div>
                <strong>Advisor Introduction</strong>
                <p>"Email my advisor introducing myself" → Drafts professional email, shows preview, sends on approval</p>
              </div>
            </div>
            <div className="use-case">
              <span className="use-icon"><FaClockIcon /></span>
              <div>
                <strong>Deadline Reminders</strong>
                <p>Auto-sends reminder emails 3 days before registration, graduation apps, etc.</p>
              </div>
            </div>
            <div className="use-case">
              <span className="use-icon"><FaFileAlt /></span>
              <div>
                <strong>Follow-up Emails</strong>
                <p>"Follow up on my internship application to Google" → Writes polite follow-up</p>
              </div>
            </div>
            <div className="use-case">
              <span className="use-icon"><FaAward /></span>
              <div>
                <strong>Milestone Celebrations</strong>
                <p>Auto-sends congrats when student reaches 100 credits, makes Dean's List, etc.</p>
              </div>
            </div>
          </div>
        </div>
        <div className="feature-preview email-preview">
          <div className="preview-header"><FaEnvelope style={{marginRight: '8px'}} /> Email Preview</div>
          <div className="email-mock">
            <div className="email-to">To: dr.johnson@morgan.edu</div>
            <div className="email-subject">Subject: Introduction - Junior CS Student</div>
            <div className="email-body">
              Dear Dr. Johnson,<br/><br/>
              My name is Marcus and I'm a junior in the Computer Science program. I'm interested in your research on machine learning...<br/><br/>
              Best regards,<br/>Marcus Williams
            </div>
            <div className="email-actions">
              <button className="email-btn send"><FaCheck /> Send</button>
              <button className="email-btn edit"><FaCog /> Edit</button>
              <button className="email-btn cancel"><FaTimes /> Cancel</button>
            </div>
          </div>
        </div>
      </div>

      <h3><FaCalendarAlt style={{marginRight: '8px', color: '#0052CC'}} /> Calendar Scheduler Agent</h3>
      <div className="feature-showcase">
        <div className="feature-desc">
          <p><strong>What it does:</strong> Books meetings, finds open times, and manages academic calendar events.</p>
          <div className="use-cases">
            <div className="use-case">
              <span className="use-icon"><FaUsers /></span>
              <div>
                <strong>Advisor Meetings</strong>
                <p>"Schedule a meeting with my advisor next week" → Shows available slots, books automatically</p>
              </div>
            </div>
            <div className="use-case">
              <span className="use-icon"><FaBuilding /></span>
              <div>
                <strong>Office Hours</strong>
                <p>"When are Dr. Smith's office hours?" → Shows times, offers to add to calendar</p>
              </div>
            </div>
            <div className="use-case">
              <span className="use-icon"><FaBookOpen /></span>
              <div>
                <strong>Study Sessions</strong>
                <p>"Find time for a study group this weekend" → Suggests slots, sends invites to group</p>
              </div>
            </div>
            <div className="use-case">
              <span className="use-icon"><FaClipboardList /></span>
              <div>
                <strong>Important Dates</strong>
                <p>Auto-adds registration dates, finals schedule, graduation deadlines to calendar</p>
              </div>
            </div>
          </div>
        </div>
        <div className="feature-preview calendar-preview">
          <div className="preview-header"><FaCalendarAlt style={{marginRight: '8px'}} /> Available Slots</div>
          <div className="calendar-mock">
            <div className="cal-slot available">
              <span className="slot-time">Mon 2:00 PM</span>
              <span className="slot-status">Available</span>
            </div>
            <div className="cal-slot available">
              <span className="slot-time">Tue 10:00 AM</span>
              <span className="slot-status">Available</span>
            </div>
            <div className="cal-slot booked">
              <span className="slot-time">Wed 3:00 PM</span>
              <span className="slot-status">Booked</span>
            </div>
            <div className="cal-slot available preferred">
              <span className="slot-time">Thu 1:00 PM</span>
              <span className="slot-status">✓ Recommended</span>
            </div>
          </div>
        </div>
      </div>

      <h3><FaBell style={{marginRight: '8px', color: '#0052CC'}} /> Smart Notification Agent</h3>
      <div className="feature-showcase">
        <div className="feature-desc">
          <p><strong>What it does:</strong> Sends timely alerts so students never miss important deadlines.</p>
          <div className="notification-types">
            <div className="notif-type urgent">
              <span className="notif-icon"><FaExclamationTriangle /></span>
              <span className="notif-label">Urgent</span>
              <p>Registration closes tomorrow!</p>
            </div>
            <div className="notif-type important">
              <span className="notif-icon"><FaBell /></span>
              <span className="notif-label">Important</span>
              <p>Graduation application due in 1 week</p>
            </div>
            <div className="notif-type info">
              <span className="notif-icon"><FaLightbulb /></span>
              <span className="notif-label">Helpful</span>
              <p>New internship matches your profile!</p>
            </div>
            <div className="notif-type reminder">
              <span className="notif-icon"><FaMapMarkerAlt /></span>
              <span className="notif-label">Reminder</span>
              <p>Advisor meeting in 30 minutes</p>
            </div>
          </div>
        </div>
      </div>

      <h3><FaFileAlt style={{marginRight: '8px', color: '#0052CC'}} /> Auto Form Agent</h3>
      <div className="feature-showcase">
        <div className="feature-desc">
          <p><strong>What it does:</strong> Pre-fills forms with student data to save time and reduce errors.</p>
          <div className="form-examples">
            <div className="form-example">
              <span className="form-icon"><FaGraduationCap /></span>
              <strong>Graduation Application</strong>
              <p>Auto-fills name, ID, credits, expected date from DegreeWorks</p>
            </div>
            <div className="form-example">
              <span className="form-icon"><FaBookOpen /></span>
              <strong>Course Override Request</strong>
              <p>Pre-fills course info, prerequisites met, reason template</p>
            </div>
            <div className="form-example">
              <span className="form-icon"><FaBriefcase /></span>
              <strong>Internship Applications</strong>
              <p>Auto-fills resume info, generates cover letter draft</p>
            </div>
            <div className="form-example">
              <span className="form-icon"><FaFileAlt /></span>
              <strong>Transcript Requests</strong>
              <p>One-click official transcript orders</p>
            </div>
          </div>
        </div>
      </div>

      <h3><FaSearch style={{marginRight: '8px', color: '#0052CC'}} /> More Smart Agents</h3>
      <div className="mini-agent-grid">
        <div className="mini-agent">
          <span className="mini-icon"><FaBookOpen /></span>
          <h4>Textbook Finder</h4>
          <p>Finds cheapest textbook options - new, used, rental, PDF across all platforms</p>
        </div>
        <div className="mini-agent">
          <span className="mini-icon"><FaMoneyBillWave /></span>
          <h4>Scholarship Scout</h4>
          <p>Searches & alerts for scholarships you qualify for, tracks deadlines</p>
        </div>
        <div className="mini-agent">
          <span className="mini-icon"><FaEye /></span>
          <h4>Seat Watcher</h4>
          <p>Monitors full classes, alerts when seat opens, can auto-register</p>
        </div>
        <div className="mini-agent">
          <span className="mini-icon"><FaChartLine /></span>
          <h4>Grade Tracker</h4>
          <p>Calculates current grade, predicts final, suggests needed scores</p>
        </div>
        <div className="mini-agent">
          <span className="mini-icon"><FaBuilding /></span>
          <h4>Lab Finder</h4>
          <p>Shows which computer labs are open right now with available seats</p>
        </div>
        <div className="mini-agent">
          <span className="mini-icon"><FaStickyNote /></span>
          <h4>Notes Summarizer</h4>
          <p>Summarizes meeting/lecture notes, extracts action items</p>
        </div>
        <div className="mini-agent">
          <span className="mini-icon"><FaExchangeAlt /></span>
          <h4>Transfer Evaluator</h4>
          <p>Shows how credits from other schools would transfer</p>
        </div>
        <div className="mini-agent">
          <span className="mini-icon"><FaCalendarAlt /></span>
          <h4>Deadline Watchdog</h4>
          <p>Tracks ALL your deadlines in one place, smart reminders</p>
        </div>
      </div>

      <h3>How Automation Saves Time</h3>
      <div className="time-savings">
        <div className="saving-item">
          <span className="task-name">Book advisor meeting</span>
          <div className="time-bar">
            <div className="time-before" style={{width: '80%'}}>Before: 15 min</div>
            <div className="time-after" style={{width: '20%'}}>After: 30 sec</div>
          </div>
        </div>
        <div className="saving-item">
          <span className="task-name">Find cheapest textbook</span>
          <div className="time-bar">
            <div className="time-before" style={{width: '90%'}}>Before: 30 min</div>
            <div className="time-after" style={{width: '10%'}}>After: 10 sec</div>
          </div>
        </div>
        <div className="saving-item">
          <span className="task-name">Check degree progress</span>
          <div className="time-bar">
            <div className="time-before" style={{width: '70%'}}>Before: 10 min</div>
            <div className="time-after" style={{width: '15%'}}>After: Instant</div>
          </div>
        </div>
        <div className="saving-item">
          <span className="task-name">Fill graduation app</span>
          <div className="time-bar">
            <div className="time-before" style={{width: '85%'}}>Before: 20 min</div>
            <div className="time-after" style={{width: '15%'}}>After: 2 min</div>
          </div>
        </div>
      </div>
    </div>
  );

  const renderAgentsKnowledge = () => (
    <div className="doc-section agents-section">
      <h2>Knowledge & Content Agents</h2>
      <p className="doc-intro">
        Keep the knowledge base fresh and accurate with minimal manual effort through automated scraping
        and human-in-the-loop review.
      </p>

      <h3>How It Works</h3>
      <div
        className="mermaid-diagram"
        data-diagram={`flowchart LR
    subgraph Trigger["Triggers"]
        Manual[Manual]
        Cron[Scheduled]
        Webhook[Webhook]
    end

    subgraph Pipeline["Agent Pipeline"]
        Scraper[Scraper]
        Parser[Parser]
        Validator[Validator]
    end

    subgraph Review["Human Review"]
        JSON[JSON Output]
        Admin[Admin Panel]
    end

    subgraph Final["Final"]
        KB[(Knowledge Base)]
        Reject[Discard]
    end

    Trigger --> Scraper
    Scraper --> Parser
    Parser --> Validator
    Validator --> JSON
    JSON --> Admin
    Admin -->|Approve| KB
    Admin -->|Reject| Reject

    style KB fill:#10b981,color:#fff
    style Reject fill:#ef4444,color:#fff`}
      />

      <h3>Feature List</h3>
      <table className="doc-table agents-table">
        <thead>
          <tr><th>Feature</th><th>Description</th><th>Priority</th></tr>
        </thead>
        <tbody>
          <tr className="priority-critical">
            <td><strong>Web Scraper Agent</strong></td>
            <td>Scrapes Morgan State CS website, outputs JSON for admin review before updating knowledge base</td>
            <td><span className="priority-badge critical">Critical</span></td>
          </tr>
          <tr className="priority-high">
            <td><strong>Course Catalog Sync</strong></td>
            <td>Automatically detects course changes each semester, generates diff report</td>
            <td><span className="priority-badge high">High</span></td>
          </tr>
          <tr className="priority-high">
            <td><strong>Faculty Profile Monitor</strong></td>
            <td>Tracks new hires, departures, research area updates</td>
            <td><span className="priority-badge high">High</span></td>
          </tr>
          <tr className="priority-high">
            <td><strong>Content Freshness Checker</strong></td>
            <td>Flags documents older than 6 months for review</td>
            <td><span className="priority-badge high">High</span></td>
          </tr>
          <tr className="priority-medium">
            <td><strong>FAQ Auto-Generator</strong></td>
            <td>Analyzes most common questions and generates FAQ content</td>
            <td><span className="priority-badge medium">Medium</span></td>
          </tr>
        </tbody>
      </table>

      <h3>JSON Output Example</h3>
      <pre className="code-block">
{`{
  "scrape_id": "scrape_20260119_001",
  "source_url": "https://morgan.edu/cs/courses",
  "scraped_at": "2026-01-19T15:30:00Z",
  "confidence": 0.92,
  "items": [
    {
      "type": "course",
      "data": {
        "code": "COSC 455",
        "title": "Computer Networks",
        "credits": 3,
        "prerequisites": ["COSC 311"]
      },
      "confidence": 0.95,
      "needs_review": false
    }
  ]
}`}
      </pre>
    </div>
  );

  const renderAgentsAcademic = () => (
    <div className="doc-section agents-section">
      <h2>Academic Success Agents</h2>
      <p className="doc-intro">
        Help students make better academic decisions with personalized tools that understand their
        DegreeWorks data and academic standing.
      </p>

      <h3>System Flow</h3>
      <div
        className="mermaid-diagram"
        data-diagram={`flowchart TB
    subgraph Input["Student Data"]
        Profile[Profile]
        DW[DegreeWorks]
        Question[Question]
    end

    subgraph Agent["Academic Agent"]
        Analyze[Analyze Request]

        subgraph Tools["Tools"]
            Prereq[Check Prereqs]
            GPA[Calculate GPA]
            Schedule[Build Schedule]
        end
    end

    subgraph Output["Output"]
        Rec[Recommendations]
        Visual[Visual Progress]
        Alert[Warnings]
    end

    Input --> Analyze
    Analyze --> Tools
    Tools --> Output

    style Rec fill:#4285F4,color:#fff
    style Visual fill:#4285F4,color:#fff
    style Alert fill:#f59e0b,color:#fff`}
      />

      <h3>Feature List</h3>
      <table className="doc-table agents-table">
        <thead>
          <tr><th>Feature</th><th>Description</th><th>Priority</th></tr>
        </thead>
        <tbody>
          <tr className="priority-high">
            <td><strong>Prerequisite Checker</strong></td>
            <td>"Can I take COSC 455?" → Shows missing prerequisites based on courses taken</td>
            <td><span className="priority-badge high">High</span></td>
          </tr>
          <tr className="priority-high">
            <td><strong>Degree Progress Tracker</strong></td>
            <td>Visual progress bar for each requirement category (Core, Electives, etc.)</td>
            <td><span className="priority-badge high">High</span></td>
          </tr>
          <tr className="priority-high">
            <td><strong>GPA Calculator & Predictor</strong></td>
            <td>"What grade do I need to reach 3.5 GPA?" with current standing</td>
            <td><span className="priority-badge high">High</span></td>
          </tr>
          <tr className="priority-high">
            <td><strong>Smart Course Recommender</strong></td>
            <td>Suggests courses based on GPA, interests, and remaining requirements</td>
            <td><span className="priority-badge high">High</span></td>
          </tr>
          <tr className="priority-medium">
            <td><strong>What-If Scenario Planner</strong></td>
            <td>"What if I minor in Math?" → Shows impact on graduation timeline</td>
            <td><span className="priority-badge medium">Medium</span></td>
          </tr>
          <tr className="priority-medium">
            <td><strong>Course Comparison Tool</strong></td>
            <td>Side-by-side comparison: difficulty, topics, career relevance</td>
            <td><span className="priority-badge medium">Medium</span></td>
          </tr>
          <tr className="priority-medium">
            <td><strong>Socratic Tutoring Mode</strong></td>
            <td>Guides students to answers with questions instead of giving direct answers</td>
            <td><span className="priority-badge medium">Medium</span></td>
          </tr>
          <tr className="priority-medium">
            <td><strong>Professor Match Finder</strong></td>
            <td>Matches student interests to faculty research areas</td>
            <td><span className="priority-badge medium">Medium</span></td>
          </tr>
        </tbody>
      </table>

      <h3>User Interaction Example</h3>
      <div className="chat-example">
        <div className="chat-user">Can I take COSC 455 next semester?</div>
        <div className="chat-bot">
          <p>Let me check your prerequisites for <strong>COSC 455 (Computer Networks)</strong>...</p>
          <p><span className="status-complete">COSC 311</span> - Data Structures (Completed: A)</p>
          <p><span className="status-missing">COSC 350</span> - Computer Organization (Missing)</p>
          <p><strong>Recommendation:</strong> Take COSC 350 first, then COSC 455 the following semester.</p>
        </div>
      </div>
    </div>
  );

  const renderAgentsCareer = () => (
    <div className="doc-section agents-section">
      <h2>Career & Professional Agents</h2>
      <p className="doc-intro">
        Prepare students for life after graduation with AI-powered career tools that provide
        personalized feedback and job matching.
      </p>

      <h3>Career Agent Suite</h3>
      <div
        className="mermaid-diagram"
        data-diagram={`flowchart LR
    subgraph Student["Student Input"]
        Resume[Resume]
        Profile[Profile]
        Goals[Career Goals]
    end

    subgraph Agents["Career Agents"]
        RR[Resume Reviewer]
        IC[Interview Coach]
        IM[Internship Matcher]
        CP[Career Pathfinder]
    end

    subgraph External["External Data"]
        Jobs[(Job Boards)]
        Salary[(Salary Data)]
    end

    subgraph Output["Output"]
        Feedback[Feedback]
        Matches[Job Matches]
        Path[Career Map]
    end

    Student --> Agents
    External --> Agents
    Agents --> Output

    style RR fill:#4285F4,color:#fff
    style IC fill:#4285F4,color:#fff
    style IM fill:#4285F4,color:#fff
    style CP fill:#4285F4,color:#fff`}
      />

      <h3>Feature List</h3>
      <table className="doc-table agents-table">
        <thead>
          <tr><th>Feature</th><th>Description</th><th>Priority</th></tr>
        </thead>
        <tbody>
          <tr className="priority-high">
            <td><strong>Resume Review Agent</strong></td>
            <td>AI feedback on format, keywords, ATS compatibility with specific suggestions</td>
            <td><span className="priority-badge high">High</span></td>
          </tr>
          <tr className="priority-high">
            <td><strong>Mock Interview Bot</strong></td>
            <td>Practice behavioral and technical interviews with real-time feedback</td>
            <td><span className="priority-badge high">High</span></td>
          </tr>
          <tr className="priority-high">
            <td><strong>Internship Matcher</strong></td>
            <td>Scrapes job boards, matches opportunities to student profile and interests</td>
            <td><span className="priority-badge high">High</span></td>
          </tr>
          <tr className="priority-medium">
            <td><strong>Career Path Visualizer</strong></td>
            <td>Interactive visualization: CS degree → roles → growth paths → salaries</td>
            <td><span className="priority-badge medium">Medium</span></td>
          </tr>
          <tr className="priority-medium">
            <td><strong>LinkedIn Profile Tips</strong></td>
            <td>Personalized optimization suggestions based on career goals</td>
            <td><span className="priority-badge medium">Medium</span></td>
          </tr>
          <tr className="priority-medium">
            <td><strong>Portfolio Project Suggester</strong></td>
            <td>"Build these 3 projects to stand out for Software Engineer roles"</td>
            <td><span className="priority-badge medium">Medium</span></td>
          </tr>
        </tbody>
      </table>

      <h3>Resume Review Example</h3>
      <div className="review-card">
        <div className="review-header">Resume Analysis Results</div>
        <div className="review-score">
          <span className="score-number">72</span>
          <span className="score-label">/ 100</span>
        </div>
        <div className="review-items">
          <div className="review-item good">Add quantifiable achievements (e.g., "Improved load time by 40%")</div>
          <div className="review-item good">Include relevant keywords: REST API, Git, Agile</div>
          <div className="review-item warning">Skills section missing: Add programming languages prominently</div>
          <div className="review-item warning">Consider adding a Projects section</div>
        </div>
      </div>
    </div>
  );

  const renderAgentsEngagement = () => (
    <div className="doc-section agents-section">
      <h2>Gamification & Engagement</h2>
      <p className="doc-intro">
        Keep students coming back and engaged with game-like mechanics that reward consistent
        usage and academic progress.
      </p>

      <h3>Gamification Engine</h3>
      <div
        className="mermaid-diagram"
        data-diagram={`flowchart TB
    subgraph Actions["User Actions"]
        Ask[Ask Question]
        Return[Daily Return]
        Complete[Complete Task]
    end

    subgraph Engine["Gamification Engine"]
        XP[XP Calculator]
        Streak[Streak Tracker]
        Badge[Badge Awarder]
    end

    subgraph Rewards["Rewards"]
        Points[XP Points]
        Badges[Badges]
        Level[Level Up]
    end

    Actions --> Engine
    Engine --> Rewards

    style XP fill:#4285F4,color:#fff
    style Streak fill:#f59e0b,color:#fff
    style Badge fill:#10b981,color:#fff`}
      />

      <h3>Feature List</h3>
      <table className="doc-table agents-table">
        <thead>
          <tr><th>Feature</th><th>Description</th><th>Priority</th></tr>
        </thead>
        <tbody>
          <tr className="priority-high">
            <td><strong>Daily Streak System</strong></td>
            <td>Track consecutive days using the chatbot with rewards for consistency</td>
            <td><span className="priority-badge high">High</span></td>
          </tr>
          <tr className="priority-high">
            <td><strong>Progress Milestones</strong></td>
            <td>Celebrate achievements: "50% of degree planned!" with animations</td>
            <td><span className="priority-badge high">High</span></td>
          </tr>
          <tr className="priority-medium">
            <td><strong>XP & Levels</strong></td>
            <td>Earn XP for engagement activities, level up over time</td>
            <td><span className="priority-badge medium">Medium</span></td>
          </tr>
          <tr className="priority-medium">
            <td><strong>Achievement Badges</strong></td>
            <td>Unlock badges for milestones and accomplishments</td>
            <td><span className="priority-badge medium">Medium</span></td>
          </tr>
          <tr className="priority-medium">
            <td><strong>Weekly Challenges</strong></td>
            <td>"Ask 5 questions this week" or "Explore 3 new courses"</td>
            <td><span className="priority-badge medium">Medium</span></td>
          </tr>
        </tbody>
      </table>

      <h3>Badge Examples</h3>
      <div className="badge-grid">
        <div className="badge-item">
          <span className="badge-icon"><FaBullseye /></span>
          <span className="badge-name">First Question</span>
          <span className="badge-desc">Asked your first question</span>
        </div>
        <div className="badge-item">
          <span className="badge-icon"><FaBookOpen /></span>
          <span className="badge-name">Course Explorer</span>
          <span className="badge-desc">Viewed 10 different courses</span>
        </div>
        <div className="badge-item">
          <span className="badge-icon"><FaGraduationCap /></span>
          <span className="badge-name">Degree Planner</span>
          <span className="badge-desc">Created a 4-year plan</span>
        </div>
        <div className="badge-item">
          <span className="badge-icon"><FaBriefcase /></span>
          <span className="badge-name">Career Ready</span>
          <span className="badge-desc">Completed resume review</span>
        </div>
        <div className="badge-item">
          <span className="badge-icon"><FaFire /></span>
          <span className="badge-name">Week Warrior</span>
          <span className="badge-desc">7-day streak achieved</span>
        </div>
        <div className="badge-item">
          <span className="badge-icon"><FaStar /></span>
          <span className="badge-name">Power User</span>
          <span className="badge-desc">Reached Level 10</span>
        </div>
      </div>
    </div>
  );

  const renderAgentsWellness = () => (
    <div className="doc-section agents-section">
      <h2>Wellness & Support</h2>
      <p className="doc-intro">
        Support student mental health and wellbeing with periodic check-ins, resource connections,
        and crisis escalation protocols.
      </p>

      <h3>Wellness Detection Flow</h3>
      <div
        className="mermaid-diagram"
        data-diagram={`flowchart TB
    subgraph Detection["Detection"]
        Checkin[Check-ins]
        Keywords[Keywords]
        Patterns[Patterns]
    end

    subgraph Assessment["Assessment"]
        Score[Wellness Score]
        Risk{Risk Level}
    end

    subgraph Response["Response"]
        Normal[Encouragement]
        Moderate[Resources]
        High[Counseling Info]
        Crisis[Crisis Protocol]
    end

    Detection --> Score
    Score --> Risk
    Risk -->|Low| Normal
    Risk -->|Medium| Moderate
    Risk -->|High| High
    Risk -->|Critical| Crisis

    style Normal fill:#10b981,color:#fff
    style Moderate fill:#f59e0b,color:#fff
    style High fill:#ef4444,color:#fff
    style Crisis fill:#7f1d1d,color:#fff`}
      />

      <h3>Feature List</h3>
      <table className="doc-table agents-table">
        <thead>
          <tr><th>Feature</th><th>Description</th><th>Priority</th></tr>
        </thead>
        <tbody>
          <tr className="priority-critical">
            <td><strong>Crisis Escalation Protocol</strong></td>
            <td>Detects crisis keywords and immediately shows counseling center info + hotlines</td>
            <td><span className="priority-badge critical">Critical</span></td>
          </tr>
          <tr className="priority-high">
            <td><strong>Wellness Check-In</strong></td>
            <td>Periodic prompt: "How are you feeling about your courses?" (1-5 scale)</td>
            <td><span className="priority-badge high">High</span></td>
          </tr>
          <tr className="priority-high">
            <td><strong>Stress Resource Connector</strong></td>
            <td>Detects stressed language and surfaces relevant campus resources</td>
            <td><span className="priority-badge high">High</span></td>
          </tr>
          <tr className="priority-medium">
            <td><strong>Study Break Reminders</strong></td>
            <td>"You've been studying for 2 hours. Consider taking a 10-minute break!"</td>
            <td><span className="priority-badge medium">Medium</span></td>
          </tr>
          <tr className="priority-medium">
            <td><strong>Motivation Booster</strong></td>
            <td>Encouraging messages during stressful periods (finals, midterms)</td>
            <td><span className="priority-badge medium">Medium</span></td>
          </tr>
        </tbody>
      </table>

      <h3>Crisis Response Example</h3>
      <div className="crisis-card">
        <div className="crisis-header">
          <span className="crisis-icon"><FaQuestionCircle /></span>
          <span>We're Here to Help</span>
        </div>
        <div className="crisis-content">
          <p>If you're in crisis or experiencing difficult thoughts, please reach out:</p>
          <div className="crisis-resources">
            <div className="resource-item">
              <strong>MSU Counseling Center</strong>
              <span>(443) 885-3130</span>
            </div>
            <div className="resource-item">
              <strong>National Crisis Hotline</strong>
              <span>988 (24/7)</span>
            </div>
            <div className="resource-item">
              <strong>Crisis Text Line</strong>
              <span>Text HOME to 741741</span>
            </div>
          </div>
        </div>
      </div>
    </div>
  );

  const renderAgentsAnalytics = () => (
    <div className="doc-section agents-section">
      <h2>Analytics & Faculty Tools</h2>
      <p className="doc-intro">
        Give faculty and advisors insights into student engagement, query trends, and at-risk
        indicators to enable proactive intervention.
      </p>

      <h3>Analytics Pipeline</h3>
      <div
        className="mermaid-diagram"
        data-diagram={`flowchart LR
    subgraph Data["Data Sources"]
        Chats[(Chat Logs)]
        Users[(User Data)]
        Queries[(Queries)]
    end

    subgraph Analytics["Analytics Agent"]
        Trends[Trend Detection]
        Risk[Risk Identification]
        Insights[Generate Insights]
    end

    subgraph Dashboard["Dashboard"]
        Charts[Visual Charts]
        Alerts[Alert Feed]
        Reports[Weekly Reports]
    end

    Data --> Analytics
    Analytics --> Dashboard

    style Trends fill:#4285F4,color:#fff
    style Risk fill:#f59e0b,color:#fff
    style Insights fill:#10b981,color:#fff`}
      />

      <h3>Feature List</h3>
      <table className="doc-table agents-table">
        <thead>
          <tr><th>Feature</th><th>Description</th><th>Priority</th></tr>
        </thead>
        <tbody>
          <tr className="priority-high">
            <td><strong>Student Analytics Dashboard</strong></td>
            <td>Engagement patterns, usage trends, at-risk indicators for faculty view</td>
            <td><span className="priority-badge high">High</span></td>
          </tr>
          <tr className="priority-high">
            <td><strong>Query Trend Analysis</strong></td>
            <td>"Top 10 questions this week" + "Emerging topics" for curriculum feedback</td>
            <td><span className="priority-badge high">High</span></td>
          </tr>
          <tr className="priority-high">
            <td><strong>Intervention Alerts</strong></td>
            <td>Notify advisor when student asks about withdrawal, struggling, etc.</td>
            <td><span className="priority-badge high">High</span></td>
          </tr>
          <tr className="priority-medium">
            <td><strong>Knowledge Gap Finder</strong></td>
            <td>Identifies questions the system can't answer well → add to KB</td>
            <td><span className="priority-badge medium">Medium</span></td>
          </tr>
          <tr className="priority-medium">
            <td><strong>Batch Communication</strong></td>
            <td>"Send registration reminder to all Juniors" through the chatbot</td>
            <td><span className="priority-badge medium">Medium</span></td>
          </tr>
          <tr className="priority-medium">
            <td><strong>Office Hours Scheduler</strong></td>
            <td>Students can book advisor time directly through the chatbot</td>
            <td><span className="priority-badge medium">Medium</span></td>
          </tr>
        </tbody>
      </table>

      <h3>Dashboard Preview</h3>
      <div className="dashboard-preview">
        <div className="dash-stat">
          <span className="dash-value">1,247</span>
          <span className="dash-label">Questions This Week</span>
          <span className="dash-change positive">+18%</span>
        </div>
        <div className="dash-stat">
          <span className="dash-value">94%</span>
          <span className="dash-label">Answer Rate</span>
          <span className="dash-change positive">+2%</span>
        </div>
        <div className="dash-stat">
          <span className="dash-value">12</span>
          <span className="dash-label">At-Risk Alerts</span>
          <span className="dash-change negative">+5</span>
        </div>
        <div className="dash-stat">
          <span className="dash-value">4.6</span>
          <span className="dash-label">Satisfaction Score</span>
          <span className="dash-change positive">+0.3</span>
        </div>
      </div>

      <h3>Top Questions This Week</h3>
      <div className="top-questions">
        <div className="question-item">
          <span className="question-rank">1</span>
          <span className="question-text">What are the prerequisites for COSC 311?</span>
          <span className="question-count">87 asks</span>
        </div>
        <div className="question-item">
          <span className="question-rank">2</span>
          <span className="question-text">When is the registration deadline?</span>
          <span className="question-count">64 asks</span>
        </div>
        <div className="question-item">
          <span className="question-rank">3</span>
          <span className="question-text">How do I change my major to CS?</span>
          <span className="question-count">52 asks</span>
        </div>
      </div>
    </div>
  );

  // ========== SOCIAL TAB ==========
  const renderAgentsSocial = () => (
    <div className="doc-section agents-section">
      <h2>Social & Collaboration</h2>
      <p className="doc-intro">
        Learning is better together. These agents help students connect with study partners,
        mentors, and form study groups based on courses and learning styles.
      </p>

      {/* Social Hero */}
      <div className="social-hero">
        <div className="social-stat">
          <span className="social-icon"><FaHandshake /></span>
          <span className="social-label">Study Buddies</span>
          <p>Find partners in your classes</p>
        </div>
        <div className="social-stat">
          <span className="social-icon"><FaUserFriends /></span>
          <span className="social-label">Study Groups</span>
          <p>Join or create study sessions</p>
        </div>
        <div className="social-stat">
          <span className="social-icon"><FaGraduationCap /></span>
          <span className="social-label">Peer Mentors</span>
          <p>Connect with upperclassmen</p>
        </div>
        <div className="social-stat">
          <span className="social-icon"><FaComments /></span>
          <span className="social-label">Class Forums</span>
          <p>Discuss with classmates</p>
        </div>
      </div>

      <h3><FaHandshake style={{marginRight: '8px', color: '#0052CC'}} /> Study Buddy Matcher</h3>
      <div className="feature-showcase">
        <div className="feature-desc">
          <p><strong>What it does:</strong> Matches students with compatible study partners based on courses, schedules, and learning preferences.</p>
          <div className="matching-criteria">
            <h4>Matching Based On:</h4>
            <div className="criteria-grid">
              <div className="criteria-item">
                <span className="criteria-icon"><FaBookOpen /></span>
                <span>Same courses enrolled</span>
              </div>
              <div className="criteria-item">
                <span className="criteria-icon"><FaClockIcon /></span>
                <span>Compatible schedules</span>
              </div>
              <div className="criteria-item">
                <span className="criteria-icon"><FaMapMarkerAlt /></span>
                <span>Study location preference</span>
              </div>
              <div className="criteria-item">
                <span className="criteria-icon"><FaBullseye /></span>
                <span>Learning style (visual, hands-on)</span>
              </div>
              <div className="criteria-item">
                <span className="criteria-icon"><FaStar /></span>
                <span>Strength areas (can help)</span>
              </div>
              <div className="criteria-item">
                <span className="criteria-icon"><FaQuestionCircle /></span>
                <span>Need help with areas</span>
              </div>
            </div>
          </div>
        </div>
        <div className="feature-preview buddy-preview">
          <div className="preview-header"><FaHandshake style={{marginRight: '8px'}} /> Your Matches</div>
          <div className="buddy-list">
            <div className="buddy-card">
              <div className="buddy-avatar">JT</div>
              <div className="buddy-info">
                <span className="buddy-name">Jordan Taylor</span>
                <span className="buddy-match">92% Match</span>
                <div className="buddy-courses">
                  <span className="course-tag">COSC 311</span>
                  <span className="course-tag">COSC 350</span>
                </div>
              </div>
              <button className="connect-btn">Connect</button>
            </div>
            <div className="buddy-card">
              <div className="buddy-avatar">AS</div>
              <div className="buddy-info">
                <span className="buddy-name">Alex Smith</span>
                <span className="buddy-match">87% Match</span>
                <div className="buddy-courses">
                  <span className="course-tag">COSC 311</span>
                </div>
              </div>
              <button className="connect-btn">Connect</button>
            </div>
          </div>
        </div>
      </div>

      <h3><FaUserFriends style={{marginRight: '8px', color: '#0052CC'}} /> Study Group Creator</h3>
      <div className="feature-showcase">
        <div className="feature-desc">
          <p><strong>What it does:</strong> Helps students create and join study groups with scheduling and location coordination.</p>
          <div className="use-cases">
            <div className="use-case">
              <span className="use-icon"><FaFileAlt /></span>
              <div>
                <strong>Create a Group</strong>
                <p>"Create a study group for COSC 311 exam" → Sets up group, finds members, schedules session</p>
              </div>
            </div>
            <div className="use-case">
              <span className="use-icon"><FaSearch /></span>
              <div>
                <strong>Find Groups</strong>
                <p>"Find study groups for Data Structures" → Shows active groups you can join</p>
              </div>
            </div>
            <div className="use-case">
              <span className="use-icon"><FaCalendarAlt /></span>
              <div>
                <strong>Schedule Sessions</strong>
                <p>Auto-finds time that works for all members, books study room</p>
              </div>
            </div>
          </div>
        </div>
        <div className="feature-preview group-preview">
          <div className="preview-header"><FaUserFriends style={{marginRight: '8px'}} /> Active Study Groups</div>
          <div className="group-list">
            <div className="group-card">
              <div className="group-name">COSC 311 Exam Prep</div>
              <div className="group-meta">
                <span><FaUsers style={{marginRight: '4px'}} /> 6 members</span>
                <span><FaMapMarkerAlt style={{marginRight: '4px'}} /> Library 3rd Floor</span>
              </div>
              <div className="group-time">Next: Tomorrow 3:00 PM</div>
              <button className="join-btn">Join Group</button>
            </div>
            <div className="group-card">
              <div className="group-name">Algorithms Weekly</div>
              <div className="group-meta">
                <span><FaUsers style={{marginRight: '4px'}} /> 4 members</span>
                <span><FaMapMarkerAlt style={{marginRight: '4px'}} /> Virtual (Zoom)</span>
              </div>
              <div className="group-time">Every Wed 7:00 PM</div>
              <button className="join-btn">Join Group</button>
            </div>
          </div>
        </div>
      </div>

      <h3><FaGraduationCap style={{marginRight: '8px', color: '#0052CC'}} /> Peer Mentor Matching</h3>
      <div className="mentor-section">
        <p>Connect new students with experienced upperclassmen who've been through the same courses.</p>
        <div className="mentor-grid">
          <div className="mentor-card">
            <div className="mentor-badge">Senior</div>
            <div className="mentor-avatar">MJ</div>
            <h4>Maria Johnson</h4>
            <p className="mentor-major">CS Major, 3.8 GPA</p>
            <div className="mentor-expertise">
              <span>Data Structures</span>
              <span>Algorithms</span>
              <span>Python</span>
            </div>
            <p className="mentor-quote">"Happy to help with COSC 311 - survived it last semester!"</p>
            <button className="request-mentor-btn">Request Mentor</button>
          </div>
          <div className="mentor-card">
            <div className="mentor-badge">Junior</div>
            <div className="mentor-avatar">DK</div>
            <h4>David Kim</h4>
            <p className="mentor-major">CS Major, 3.6 GPA</p>
            <div className="mentor-expertise">
              <span>Web Dev</span>
              <span>JavaScript</span>
              <span>React</span>
            </div>
            <p className="mentor-quote">"Love helping people get into frontend development!"</p>
            <button className="request-mentor-btn">Request Mentor</button>
          </div>
        </div>
      </div>

      <h3>More Social Features</h3>
      <div className="mini-agent-grid">
        <div className="mini-agent">
          <span className="mini-icon"><FaComments /></span>
          <h4>Class Discussion</h4>
          <p>Anonymous Q&A forums for each course, moderated for quality</p>
        </div>
        <div className="mini-agent">
          <span className="mini-icon"><FaTrophy /></span>
          <h4>Study Leaderboards</h4>
          <p>Friendly competition: most questions answered, streak leaders</p>
        </div>
        <div className="mini-agent">
          <span className="mini-icon"><FaBell /></span>
          <h4>Event Finder</h4>
          <p>CS club meetings, hackathons, career fairs, tech talks</p>
        </div>
        <div className="mini-agent">
          <span className="mini-icon"><FaUsers /></span>
          <h4>Project Teams</h4>
          <p>Find teammates for capstone projects or hackathons</p>
        </div>
      </div>

      <h3>Privacy & Safety</h3>
      <div className="privacy-note">
        <span className="privacy-icon"><FaLock /></span>
        <div>
          <strong>Student privacy is protected</strong>
          <p>All matching is opt-in. Students control what info is shared. No real names shown until both parties agree to connect. Report/block features available.</p>
        </div>
      </div>
    </div>
  );

  // ========== FUTURE DEVELOPMENT SECTION ==========
  const renderAgentsFuture = () => (
    <div className="doc-section agents-section">
      <h2>Future Development & API Requirements</h2>
      <p className="doc-intro">
        This section outlines our current AI infrastructure, potential upgrades, and API requirements
        to enhance CS Navigator's capabilities. Use this as a reference when discussing budget with your professor.
      </p>

      {/* Current Stack Summary */}
      <h3><FaDatabase style={{marginRight: '8px', color: '#0052CC'}} /> Current AI Stack</h3>
      <div className="current-stack-grid">
        <div className="stack-card current">
          <div className="stack-header">
            <span className="stack-icon"><FaRobot /></span>
            <span className="stack-badge current">Currently Using</span>
          </div>
          <h4>OpenAI GPT-3.5 Turbo</h4>
          <p>Our primary LLM for generating responses</p>
          <div className="stack-details">
            <div className="detail-row">
              <span className="detail-label">Model:</span>
              <span className="detail-value">gpt-3.5-turbo</span>
            </div>
            <div className="detail-row">
              <span className="detail-label">Cost:</span>
              <span className="detail-value">$0.50 / 1M input tokens</span>
            </div>
            <div className="detail-row">
              <span className="detail-label">Speed:</span>
              <span className="detail-value">Fast (1-3 seconds)</span>
            </div>
            <div className="detail-row">
              <span className="detail-label">Quality:</span>
              <span className="detail-value good">Good for basic Q&A</span>
            </div>
          </div>
        </div>

        <div className="stack-card current">
          <div className="stack-header">
            <span className="stack-icon"><FaDatabase /></span>
            <span className="stack-badge current">Currently Using</span>
          </div>
          <h4>Pinecone Vector DB</h4>
          <p>Stores embeddings for semantic search (RAG)</p>
          <div className="stack-details">
            <div className="detail-row">
              <span className="detail-label">Plan:</span>
              <span className="detail-value">Starter (Free)</span>
            </div>
            <div className="detail-row">
              <span className="detail-label">Vectors:</span>
              <span className="detail-value">Up to 100K</span>
            </div>
            <div className="detail-row">
              <span className="detail-label">Dimensions:</span>
              <span className="detail-value">1536</span>
            </div>
          </div>
        </div>

        <div className="stack-card current">
          <div className="stack-header">
            <span className="stack-icon"><FaCog /></span>
            <span className="stack-badge current">Currently Using</span>
          </div>
          <h4>OpenAI Embeddings</h4>
          <p>text-embedding-3-small for document vectors</p>
          <div className="stack-details">
            <div className="detail-row">
              <span className="detail-label">Model:</span>
              <span className="detail-value">text-embedding-3-small</span>
            </div>
            <div className="detail-row">
              <span className="detail-label">Cost:</span>
              <span className="detail-value">$0.02 / 1M tokens</span>
            </div>
          </div>
        </div>
      </div>

      {/* LLM Upgrade Options */}
      <h3><FaRocket style={{marginRight: '8px', color: '#0052CC'}} /> LLM Upgrade Options</h3>
      <p>Here are better LLM models we could upgrade to for improved response quality:</p>

      <div className="upgrade-options">
        <div className="upgrade-card recommended">
          <div className="upgrade-header">
            <h4>GPT-4o (Recommended)</h4>
            <span className="upgrade-badge best">Best Value</span>
          </div>
          <p className="upgrade-desc">Latest OpenAI model with excellent reasoning, faster than GPT-4</p>
          <div className="upgrade-pricing">
            <div className="price-row">
              <span>Input:</span>
              <strong>$2.50 / 1M tokens</strong>
            </div>
            <div className="price-row">
              <span>Output:</span>
              <strong>$10.00 / 1M tokens</strong>
            </div>
          </div>
          <div className="upgrade-pros">
            <span className="pro"><FaCheck /> Much smarter responses</span>
            <span className="pro"><FaCheck /> Better at complex questions</span>
            <span className="pro"><FaCheck /> Understands context better</span>
            <span className="pro"><FaCheck /> Easy integration (same API)</span>
          </div>
          <div className="monthly-estimate">
            <span className="label">Est. Monthly Cost:</span>
            <span className="cost">$50-150</span>
            <span className="usage">(based on 1000 users/month)</span>
          </div>
        </div>

        <div className="upgrade-card">
          <div className="upgrade-header">
            <h4>Claude 3.5 Sonnet</h4>
            <span className="upgrade-badge">Anthropic</span>
          </div>
          <p className="upgrade-desc">Excellent at academic content, very detailed explanations</p>
          <div className="upgrade-pricing">
            <div className="price-row">
              <span>Input:</span>
              <strong>$3.00 / 1M tokens</strong>
            </div>
            <div className="price-row">
              <span>Output:</span>
              <strong>$15.00 / 1M tokens</strong>
            </div>
          </div>
          <div className="upgrade-pros">
            <span className="pro"><FaCheck /> Great for academic content</span>
            <span className="pro"><FaCheck /> Very detailed explanations</span>
            <span className="pro"><FaCheck /> 200K context window</span>
          </div>
          <div className="monthly-estimate">
            <span className="label">Est. Monthly Cost:</span>
            <span className="cost">$75-200</span>
          </div>
        </div>

        <div className="upgrade-card">
          <div className="upgrade-header">
            <h4>Google Gemini 1.5 Pro</h4>
            <span className="upgrade-badge google">Google</span>
          </div>
          <p className="upgrade-desc">Google's flagship model, great multimodal capabilities</p>
          <div className="upgrade-pricing">
            <div className="price-row">
              <span>Input:</span>
              <strong>$1.25 / 1M tokens</strong>
            </div>
            <div className="price-row">
              <span>Output:</span>
              <strong>$5.00 / 1M tokens</strong>
            </div>
          </div>
          <div className="upgrade-pros">
            <span className="pro"><FaCheck /> Cheapest premium option</span>
            <span className="pro"><FaCheck /> 1M token context window</span>
            <span className="pro"><FaCheck /> Good reasoning abilities</span>
          </div>
          <div className="monthly-estimate">
            <span className="label">Est. Monthly Cost:</span>
            <span className="cost">$30-100</span>
          </div>
        </div>
      </div>

      {/* Google Agents Explanation */}
      <h3><FaLightbulb style={{marginRight: '8px', color: '#f59e0b'}} /> About AI Agents</h3>
      <div className="info-box">
        <span className="info-box-icon"><FaLightbulb /></span>
        <div className="info-box-content">
          <strong>Important Clarification:</strong> The "AI Agents" described in this documentation are <strong>features we plan to BUILD ourselves</strong>,
          not pre-built agents from Google or other providers. We use LLM APIs (like OpenAI) as the "brain" and build
          custom logic around them to create agent-like behavior.
        </div>
      </div>

      <div className="agent-explanation">
        <div className="explanation-card">
          <h4><FaCog style={{marginRight: '8px'}} /> What We're Building</h4>
          <ul>
            <li><strong>Custom Agents:</strong> We write code that uses LLMs to perform specific tasks (email drafting, scheduling, etc.)</li>
            <li><strong>Tool Integration:</strong> We connect the LLM to university systems (calendar, email, student records)</li>
            <li><strong>Workflow Automation:</strong> We design the logic for how agents decide what to do</li>
          </ul>
        </div>

        <div className="explanation-card">
          <h4><FaRobot style={{marginRight: '8px'}} /> Google Vertex AI Agents (Alternative)</h4>
          <p>Google offers pre-built agent frameworks through Vertex AI, but these require:</p>
          <ul>
            <li>Google Cloud Platform account ($300 free credits available)</li>
            <li>Different architecture (Google's Dialogflow CX or Agent Builder)</li>
            <li>Migration of our current RAG system</li>
            <li>Learning curve for new tools</li>
          </ul>
          <p className="recommendation"><strong>Recommendation:</strong> Stick with our current approach + better LLM model for now.</p>
        </div>
      </div>

      {/* API Requirements Table */}
      <h3><FaClipboardList style={{marginRight: '8px', color: '#0052CC'}} /> API Requirements Summary</h3>
      <p>Here's what you need to discuss with your professor:</p>

      <table className="doc-table api-table">
        <thead>
          <tr>
            <th>API/Service</th>
            <th>Current Status</th>
            <th>Upgrade Needed?</th>
            <th>Est. Monthly Cost</th>
            <th>Priority</th>
          </tr>
        </thead>
        <tbody>
          <tr>
            <td><strong>OpenAI API</strong></td>
            <td><span className="status-badge active">Active (GPT-3.5)</span></td>
            <td><span className="status-badge recommended">Upgrade to GPT-4o</span></td>
            <td>$50-150/month</td>
            <td><span className="priority-badge priority-high">High</span></td>
          </tr>
          <tr>
            <td><strong>Pinecone</strong></td>
            <td><span className="status-badge active">Active (Free tier)</span></td>
            <td><span className="status-badge ok">Keep current</span></td>
            <td>$0 (free tier sufficient)</td>
            <td><span className="priority-badge priority-low">Low</span></td>
          </tr>
          <tr>
            <td><strong>OpenAI Embeddings</strong></td>
            <td><span className="status-badge active">Active</span></td>
            <td><span className="status-badge ok">Keep current</span></td>
            <td>~$5/month</td>
            <td><span className="priority-badge priority-low">Low</span></td>
          </tr>
          <tr>
            <td><strong>AWS RDS (MySQL)</strong></td>
            <td><span className="status-badge active">Active</span></td>
            <td><span className="status-badge ok">Keep current</span></td>
            <td>~$15-30/month</td>
            <td><span className="priority-badge priority-low">Low</span></td>
          </tr>
          <tr>
            <td><strong>AWS EC2</strong></td>
            <td><span className="status-badge active">Active</span></td>
            <td><span className="status-badge ok">Keep current</span></td>
            <td>~$20-50/month</td>
            <td><span className="priority-badge priority-low">Low</span></td>
          </tr>
        </tbody>
      </table>

      {/* Recommended Actions */}
      <h3><FaBullseye style={{marginRight: '8px', color: '#22c55e'}} /> Recommended Actions for Professor</h3>
      <div className="action-cards">
        <div className="action-card priority-1">
          <div className="action-number">1</div>
          <div className="action-content">
            <h4>Upgrade to GPT-4o</h4>
            <p>This single change will dramatically improve response quality. Students will get much better answers about courses, requirements, and career advice.</p>
            <div className="action-cost">
              <strong>Budget Request:</strong> $100-150/month for OpenAI API
            </div>
          </div>
        </div>

        <div className="action-card priority-2">
          <div className="action-number">2</div>
          <div className="action-content">
            <h4>Add Function Calling</h4>
            <p>Enable the chatbot to take actions like checking real-time course availability, sending emails, and booking appointments.</p>
            <div className="action-cost">
              <strong>Budget Request:</strong> No additional cost (included in GPT-4o)
            </div>
          </div>
        </div>

        <div className="action-card priority-3">
          <div className="action-number">3</div>
          <div className="action-content">
            <h4>Integrate with University Systems</h4>
            <p>Connect to D2L, Banner, or other MSU systems for real-time student data. Requires IT department coordination.</p>
            <div className="action-cost">
              <strong>Budget Request:</strong> Developer time + possible API fees
            </div>
          </div>
        </div>
      </div>

      {/* Cost Summary */}
      <h3><FaMoneyBillWave style={{marginRight: '8px', color: '#22c55e'}} /> Total Budget Summary</h3>
      <div className="budget-summary">
        <div className="budget-current">
          <h4>Current Monthly Costs</h4>
          <div className="budget-row">
            <span>OpenAI API (GPT-3.5)</span>
            <span>~$10-30</span>
          </div>
          <div className="budget-row">
            <span>AWS Hosting (EC2 + RDS)</span>
            <span>~$50-80</span>
          </div>
          <div className="budget-row">
            <span>Pinecone</span>
            <span>$0 (free)</span>
          </div>
          <div className="budget-total">
            <span>Total Current</span>
            <span>$60-110/month</span>
          </div>
        </div>

        <div className="budget-arrow">→</div>

        <div className="budget-upgraded">
          <h4>After GPT-4o Upgrade</h4>
          <div className="budget-row highlight">
            <span>OpenAI API (GPT-4o)</span>
            <span>~$100-150</span>
          </div>
          <div className="budget-row">
            <span>AWS Hosting (EC2 + RDS)</span>
            <span>~$50-80</span>
          </div>
          <div className="budget-row">
            <span>Pinecone</span>
            <span>$0 (free)</span>
          </div>
          <div className="budget-total">
            <span>Total Upgraded</span>
            <span>$150-230/month</span>
          </div>
        </div>
      </div>

    </div>
  );

  const renderContent = () => {
    if (mode === 'roadmap') {
      switch (activeTab) {
        case 'completed': return renderCompleted();
        case 'current': return renderCurrent();
        case 'planned': return renderPlanned();
        default: return renderCompleted();
      }
    }
    if (mode === 'agents') {
      switch (activeTab) {
        case 'agents-overview': return renderAgentsOverview();
        case 'agents-automation': return renderAgentsAutomation();
        case 'agents-knowledge': return renderAgentsKnowledge();
        case 'agents-academic': return renderAgentsAcademic();
        case 'agents-career': return renderAgentsCareer();
        case 'agents-engagement': return renderAgentsEngagement();
        case 'agents-wellness': return renderAgentsWellness();
        case 'agents-analytics': return renderAgentsAnalytics();
        case 'agents-social': return renderAgentsSocial();
        case 'agents-future': return renderAgentsFuture();
        default: return renderAgentsOverview();
      }
    }
    switch (activeTab) {
      case 'overview': return renderOverview();
      case 'architecture': return renderArchitecture();
      case 'workflow': return renderWorkflow();
      case 'api': return renderAPI();
      default: return renderOverview();
    }
  };

  const getTitle = () => {
    if (mode === 'roadmap') return 'Development Roadmap';
    if (mode === 'agents') return 'AI Agents Plan';
    return 'Technical Documentation';
  };

  return (
    <div className="doc-viewer-overlay" onClick={onClose}>
      <div
        className={`doc-viewer ${darkMode ? 'dark' : ''} ${mode === 'agents' ? 'agents-mode' : ''}`}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="doc-viewer-header">
          <h2>{getTitle()}</h2>
          <button className="doc-close-btn" onClick={onClose} title="Close">
            <FaTimes size={20} />
          </button>
        </div>

        <div className="doc-viewer-tabs">
          {tabs.map((tab) => {
            const IconComponent = tab.icon;
            return (
              <button
                key={tab.id}
                className={`doc-tab ${activeTab === tab.id ? 'active' : ''}`}
                onClick={() => setActiveTab(tab.id)}
              >
                <IconComponent size={16} />
                <span>{tab.label}</span>
              </button>
            );
          })}
        </div>

        <div className="doc-viewer-content" ref={mermaidRef}>
          {renderContent()}
        </div>
      </div>
    </div>
  );
}
