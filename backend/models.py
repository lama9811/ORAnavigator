# backend/models.py
from sqlalchemy import Column, Integer, String, Boolean, DateTime, Text, Float, ForeignKey, func
from sqlalchemy.orm import relationship
from datetime import datetime, timezone
from db import Base


class ChatHistory(Base):
    """Stores chat history in AWS RDS (or local DB).
    Linked to the User table via user_id."""
    __tablename__ = "chat_history"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    session_id = Column(String(255), default="default")
    user_query = Column(Text)
    bot_response = Column(Text)
    timestamp = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class Feedback(Base):
    """Stores user feedback on bot responses for improving the chatbot."""
    __tablename__ = "feedback"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    session_id = Column(String(255), default="default")
    message_text = Column(Text)
    feedback_type = Column(String(50))  # 'helpful', 'not_helpful', 'report'
    report_details = Column(Text, nullable=True)
    timestamp = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String(255), unique=True, index=True, nullable=False)
    password_hash = Column(String(255), nullable=False)
    role = Column(String(50), nullable=False, default="student")  # "admin" or "student"

    #  Profile fields
    name = Column(String(255), nullable=True)
    student_id = Column(String(50), nullable=True)
    major = Column(String(100), nullable=True, default="Computer Science")
    profile_picture = Column(String(500), nullable=True, default="/user_icon.jpg")
    profile_picture_data = Column(Text, nullable=True)  # Store base64 image data
    morgan_connected = Column(Boolean, nullable=False, default=False)
    morgan_connected_at = Column(DateTime, nullable=True)  # When DegreeWorks was synced
    email_verified = Column(Boolean, nullable=False, default=False)
    verification_token = Column(String(255), nullable=True)
    reset_token = Column(String(255), nullable=True)
    reset_token_expires = Column(DateTime, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    # Relationship to DegreeWorks data
    degreeworks = relationship("DegreeWorksData", back_populates="user", uselist=False)


class DegreeWorksData(Base):
    """Stores parsed DegreeWorks academic data for personalized chatbot responses"""
    __tablename__ = "degreeworks_data"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), unique=True, nullable=False)

    # Student Info
    student_name = Column(String(255), nullable=True)
    student_id = Column(String(50), nullable=True)
    degree_program = Column(String(255), nullable=True)  # e.g., "Bachelor of Science in Computer Science"
    catalog_year = Column(String(20), nullable=True)  # e.g., "2022-2023"
    classification = Column(String(50), nullable=True)  # e.g., "Senior", "Junior"
    advisor = Column(String(255), nullable=True)

    # Academic Progress
    overall_gpa = Column(Float, nullable=True)
    major_gpa = Column(Float, nullable=True)
    total_credits_earned = Column(Float, nullable=True)
    credits_required = Column(Float, nullable=True)
    credits_remaining = Column(Float, nullable=True)

    # Course Data (stored as JSON strings)
    courses_completed = Column(Text, nullable=True)  # JSON: [{code, name, credits, grade, semester}]
    courses_in_progress = Column(Text, nullable=True)  # JSON: [{code, name, credits, semester}]
    courses_remaining = Column(Text, nullable=True)  # JSON: [{code, name, credits, category}]
    requirements_status = Column(Text, nullable=True)  # JSON: [{category, status, details}]

    # Raw data backup
    raw_data = Column(Text, nullable=True)  # Full JSON dump for reference

    # Data source tracking
    data_source = Column(String(50), nullable=True, default="manual_entry")  # "pdf_parse", "banner_scrape", "html_scrape", "manual_entry"

    # Metadata
    synced_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationship
    user = relationship("User", back_populates="degreeworks")


class BannerStudentData(Base):
    """All Banner-synced data beyond DegreeWorks, stored as JSON fields.
    One row per student. Populated by Banner SSB REST API sync."""
    __tablename__ = "banner_student_data"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), unique=True, nullable=False)

    # Student Profile (from Banner, supplements DegreeWorks)
    student_phone = Column(String(20), nullable=True)
    student_address = Column(Text, nullable=True)       # JSON

    # Current Registration
    current_term = Column(String(50), nullable=True)
    registered_courses = Column(Text, nullable=True)     # JSON: [{crn, subject, number, title, credits, instructor, times, location}]
    total_registered_credits = Column(Float, nullable=True)

    # Registration History
    registration_history = Column(Text, nullable=True)   # JSON: [{term, courses, term_gpa, credits_attempted, credits_earned}]

    # Grade History
    grade_history = Column(Text, nullable=True)          # JSON: [{term, courses: [{code, title, grade, credits}], term_gpa}]
    cumulative_gpa = Column(Float, nullable=True)
    total_credits_earned = Column(Float, nullable=True)
    total_credits_attempted = Column(Float, nullable=True)
    deans_list_terms = Column(Text, nullable=True)       # JSON: ["Fall 2025", "Spring 2026"]

    # Metadata
    synced_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationship
    user = relationship("User", backref="banner_data")


class CanvasStudentData(Base):
    """Stores Canvas LMS data: courses, assignments, grades, deadlines.
    Synced via Canvas REST API using Morgan State LDAP credentials."""
    __tablename__ = "canvas_student_data"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), unique=True, nullable=False)

    # Canvas Profile
    canvas_user_id = Column(Integer, nullable=True)
    canvas_login_id = Column(String(100), nullable=True)

    # Courses (JSON array)
    courses = Column(Text, nullable=True)  # [{id, name, code, grade, score}]

    # Assignments (JSON array)
    upcoming_assignments = Column(Text, nullable=True)  # [{title, type, due_at, points, course_name, submitted}]

    # Missing/overdue (JSON array)
    missing_assignments = Column(Text, nullable=True)  # [{title, course_id, due_at, points}]

    # Grades per course (JSON dict)
    grades = Column(Text, nullable=True)  # {course_id: {current_score, current_grade}}

    # Full gradebook (JSON dict keyed by course_id)
    gradebook = Column(Text, nullable=True)  # {course_id: {grading_type, assignment_groups, assignments}}

    # Metadata
    synced_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationship
    user = relationship("User", backref="canvas_data")


class SupportTicket(Base):
    """Support tickets submitted by users for bug reports and feedback"""
    __tablename__ = "support_tickets"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)

    # Ticket Details
    subject = Column(String(255), nullable=False)
    category = Column(String(50), nullable=False)  # "bug", "feature", "question", "other"
    description = Column(Text, nullable=False)
    attachment_data = Column(Text(16777215), nullable=True)  # MEDIUMTEXT: Base64 encoded file (up to ~12MB)
    attachment_name = Column(String(255), nullable=True)

    # Status tracking
    status = Column(String(50), nullable=False, default="open")  # "open", "in_progress", "resolved", "closed"
    priority = Column(String(20), nullable=False, default="normal")  # "low", "normal", "high", "urgent"

    # Admin response
    admin_notes = Column(Text, nullable=True)
    resolved_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    resolved_at = Column(DateTime, nullable=True)

    # Metadata
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    user = relationship("User", foreign_keys=[user_id], backref="tickets")


class UserMemory(Base):
    """Long-term user memory for chatbot personalization.
    Consolidated from daily conversations via cron job.
    Stored on our RDS (FERPA-safe), not Vertex AI."""
    __tablename__ = "user_memories"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    memory_type = Column(String(50), nullable=False)  # interest, preference, goal, context
    content = Column(Text, nullable=False)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    user = relationship("User", backref="memories")


class FailedQuery(Base):
    """Tracks questions the chatbot couldn't answer (KB misses).
    Used by the auto-research agent to find and fill knowledge gaps."""
    __tablename__ = "failed_queries"

    id = Column(Integer, primary_key=True, index=True)
    user_query = Column(Text, nullable=False)
    bot_response = Column(Text, nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    cluster_id = Column(Integer, nullable=True, index=True)
    status = Column(String(50), default="new")  # new, clustered, researched, dismissed
    created_at = Column(DateTime, nullable=False, server_default=func.now())


class KBSuggestion(Base):
    """KB update suggestions generated by the auto-research agent.
    Admin reviews and approves before pushing to the live datastore."""
    __tablename__ = "kb_suggestions"

    id = Column(Integer, primary_key=True, index=True)
    cluster_id = Column(Integer, nullable=True)
    topic = Column(String(500), nullable=False)
    representative_query = Column(Text, nullable=False)
    query_count = Column(Integer, default=1)
    researched_answer = Column(Text, nullable=False)
    sources = Column(Text, nullable=True)  # JSON array of URLs
    confidence = Column(String(20), default="medium")  # high, medium, low
    suggested_doc_id = Column(String(255), nullable=True)
    suggested_content = Column(Text, nullable=True)
    status = Column(String(50), default="pending")  # pending, approved, rejected, pushed
    admin_notes = Column(Text, nullable=True)
    reviewed_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    reviewed_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, nullable=False, server_default=func.now())
    updated_at = Column(DateTime, nullable=False, server_default=func.now(), onupdate=func.now())