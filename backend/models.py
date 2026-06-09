# backend/models.py
from sqlalchemy import Column, Integer, String, Boolean, DateTime, Text, Float, ForeignKey, func
from sqlalchemy.orm import relationship
from datetime import datetime, timezone
from db import Base


class ChatHistory(Base):
    """Stores chat history in Cloud SQL.
    Linked to the User table via user_id."""
    __tablename__ = "chat_history"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    session_id = Column(String(255), default="default")
    user_query = Column(Text)
    bot_response = Column(Text)
    # Sources/citations for this answer: JSON-encoded list[{title, url}].
    # TEXT + json.dumps/loads (house style), so the Sources block survives
    # cache hits, page refreshes, and history reloads. NULL on legacy rows.
    citations = Column(Text, nullable=True)
    timestamp = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    # Phase 1: rolling session summary. Populated on the row whose post-commit
    # task fired the summarization. On read, latest non-null wins.
    session_summary = Column(Text, nullable=True)
    summary_through_id = Column(Integer, nullable=True)
    # Phase 4: verbatim turn embedding for cross-session semantic recall.
    # Embedded as f"User: {q}\nAssistant: {a[:1500]}" on post-commit hook.
    embedding = Column(Text, nullable=True)
    embedding_model = Column(String(64), nullable=True)
    topic_label = Column(String(128), nullable=True)


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
    role = Column(String(50), nullable=False, default="user")  # "admin" or "user"

    #  Profile fields
    name = Column(String(255), nullable=True)
    profile_picture = Column(String(500), nullable=True, default="/user_icon.jpg")
    profile_picture_data = Column(Text, nullable=True)  # Store base64 image data
    email_verified = Column(Boolean, nullable=False, default=False)
    verification_token = Column(String(255), nullable=True)
    reset_token = Column(String(255), nullable=True)
    reset_token_expires = Column(DateTime, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    # Phase 3: idle-sweep needs to know when the user last chatted so the cron
    # can run extraction 5-10 min after they go quiet.
    last_chat_at = Column(DateTime, nullable=True)
    # Phase 5: global memory pause. When True, the chat path skips both
    # semantic recall and per-turn extraction; per-row pause on UserMemory
    # still works independently.
    memory_paused = Column(Boolean, nullable=False, default=False)
    # Research-admin profile fields. Optional; surfaced to the agent via
    # profile_parts in /chat endpoints so it can tailor answers (a PI vs.
    # research staff vs. dept admin needs different guidance). The mirror
    # in services/memory_service.mirror_profile_to_memories() also writes
    # department / primary_role into user_memories so Sponsor Fit Finder
    # picks them up without code changes.
    department = Column(String(128), nullable=True)
    title = Column(String(128), nullable=True)
    # Allowed values (validated at the API layer, see deps.PROFILE_ROLE_ENUM):
    # PI, Co-PI, Research Staff, Department Admin, Faculty, Postdoc, Student.
    primary_role = Column(String(32), nullable=True)


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
    """Long-term user memory for ORA Navigator personalization.
    Consolidated from daily conversations via Cloud Scheduler cron job.
    Stored on the project's Cloud SQL instance, not Vertex AI.

    Memory types: role, department, active_grant, irb_protocol, iacuc_protocol,
    sponsor, interest, preference, goal, context."""
    __tablename__ = "user_memories"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    memory_type = Column(String(50), nullable=False)  # role, department, active_grant, irb_protocol, iacuc_protocol, sponsor, interest, preference, goal, context
    content = Column(Text, nullable=False)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)
    # Phase 2: JSON-encoded 256-float embedding for semantic recall. Stored as
    # TEXT (codebase convention is TEXT + json.dumps/loads, not native JSON).
    embedding = Column(Text, nullable=True)
    embedding_model = Column(String(64), nullable=True)
    # Per-row pause: when True, this row is skipped during semantic retrieval.
    # Cooperates with Phase 5's global users.memory_paused.
    paused = Column(Boolean, nullable=False, default=False)

    user = relationship("User", backref="memories")


class UserSuggestedQuestions(Base):
    """Precomputed home-screen suggestions for each user.
    Refreshed in the post-commit hook after every chat turn — the GET
    endpoint is a pure ~5ms read. Throttled by source_signature so unchanged
    history doesn't trigger needless LLM calls."""
    __tablename__ = "user_suggested_questions"

    user_id = Column(Integer, ForeignKey("users.id"), primary_key=True)
    # JSON-encoded list[str] (codebase convention: TEXT + json.dumps/loads).
    questions = Column(Text, nullable=False)
    generated_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    # "{max_chat_id}:{max_memory_updated_epoch}" — used to skip regen when
    # neither history nor memory facts have changed since last run.
    source_signature = Column(String(64), nullable=False, default="")
    # "personalized" (LLM + template) or "default" (cold-start pool sample).
    source = Column(String(32), nullable=False, default="default")

    user = relationship("User", backref="suggested_questions")


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


class Submission(Base):
    """A user's in-flight grant proposal / submission. Owns an ordered
    list of SubmissionTask rows that the user ticks off as they go.

    Status values: 'active' (default), 'submitted', 'withdrawn'. Hard
    delete via DELETE /api/me/submissions/{id} cascades through
    SubmissionTask via the relationship below."""
    __tablename__ = "submissions"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    title = Column(String(255), nullable=False)
    sponsor = Column(String(64), nullable=False, default="Internal")  # NSF, NIH, DoD, Internal, ...
    deadline = Column(DateTime, nullable=True)
    status = Column(String(32), nullable=False, default="active")  # active, submitted, withdrawn
    notes = Column(Text, nullable=True)
    # Budget Helper: JSON string of the saved budget inputs (line items + F&A
    # selection + cap). Recomputed deterministically on load. Nullable — most
    # submissions have no budget yet.
    budget_json = Column(Text, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    user = relationship("User", backref="submissions")
    tasks = relationship(
        "SubmissionTask",
        backref="submission",
        order_by="SubmissionTask.sort_order, SubmissionTask.id",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class SubmissionTask(Base):
    """A single checklist item under a Submission. Seeded from a template
    (generic / NSF / NIH) at submission-create time, then editable by
    the user (add custom tasks, toggle done, edit, delete)."""
    __tablename__ = "submission_tasks"

    id = Column(Integer, primary_key=True, index=True)
    submission_id = Column(
        Integer,
        ForeignKey("submissions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    title = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    # Optional pointer back to the KB form/template this task corresponds to.
    # The frontend uses this to render a "Open form" link inline on the task.
    kb_doc_id = Column(String(128), nullable=True)
    # Days BEFORE the submission deadline this task is recommended to be
    # done by. NULL means no recommended due date.
    due_offset_days = Column(Integer, nullable=True)
    status = Column(String(16), nullable=False, default="pending")  # pending, done
    notes = Column(Text, nullable=True)
    sort_order = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)


class DeadlineReminderLog(Base):
    """Idempotency log for the Deadline Watcher cron.

    Every time the watcher sends a "deadline in N days" email for a
    Submission, we insert one row here. On the next cron run we skip any
    (submission_id, threshold_days) pair we've already sent, so a faculty
    member never gets the same 14-days-out warning twice for the same
    proposal.

    Cascade-deletes when the parent submission is deleted; that way a
    user who withdraws and recreates a proposal can legitimately get the
    full reminder schedule again."""
    __tablename__ = "deadline_reminder_log"

    id = Column(Integer, primary_key=True, index=True)
    submission_id = Column(
        Integer,
        ForeignKey("submissions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # The "days from deadline" bucket this reminder is for: 14, 7, 3, 1, or 0.
    threshold_days = Column(Integer, nullable=False)
    sent_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    # Captured for observability / audit -- which user email the SMTP send
    # was directed at. Doesn't affect idempotency.
    sent_to = Column(String(255), nullable=True)


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
