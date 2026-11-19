from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Boolean,
    DateTime,
    Enum as SAEnum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class AIAgentRole(str, enum.Enum):
    SDR = "sdr"
    FOLLOWUP = "followup"
    CONCIERGE = "concierge"


class AIConversationStatus(str, enum.Enum):
    ACTIVE_AI = "active_ai"
    HUMAN_HANDOFF = "human_handoff"
    QUALIFIED = "qualified"
    CLOSED = "closed"


class AIFollowupStatus(str, enum.Enum):
    PENDING = "pending"
    SENT = "sent"
    CANCELLED_REPLIED = "cancelled_replied"
    CANCELLED_MANUAL = "cancelled_manual"
    FAILED = "failed"


class AIAgentConfig(Base):
    __tablename__ = "ai_agent_configs"
    __table_args__ = (
        UniqueConstraint("tenant_id", "slug", name="uq_ai_agent_configs_tenant_slug"),
        {"extend_existing": True},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    slug: Mapped[str] = mapped_column(String(80), nullable=False)
    role: Mapped[AIAgentRole] = mapped_column(
        SAEnum(AIAgentRole, name="ai_agent_role_enum", create_type=True),
        nullable=False,
        default=AIAgentRole.SDR,
    )
    gemini_model: Mapped[str] = mapped_column(String(80), nullable=False, default="gemini-2.5-flash")
    temperature: Mapped[float] = mapped_column(Float, nullable=False, default=0.35)
    persona_prompt: Mapped[str] = mapped_column(Text, nullable=False)
    qualification_guidelines: Mapped[str | None] = mapped_column(Text, nullable=True)
    handoff_rules: Mapped[str | None] = mapped_column(Text, nullable=True)
    max_autonomous_turns: Mapped[int] = mapped_column(Integer, nullable=False, default=15)
    auto_followup_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    default_followup_delay_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=240)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class AIKnowledgeDocument(Base):
    __tablename__ = "ai_knowledge_documents"
    __table_args__ = ({"extend_existing": True},)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(String(220), nullable=False)
    category: Mapped[str] = mapped_column(String(80), nullable=False, default="geral", index=True)
    source_type: Mapped[str] = mapped_column(String(40), nullable=False, default="manual_text")
    raw_content: Mapped[str] = mapped_column(Text, nullable=False)
    tags: Mapped[list[str]] = mapped_column(ARRAY(String(60)), nullable=False, default=list)
    chunk_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    chunks: Mapped[list[AIKnowledgeChunk]] = relationship(
        "AIKnowledgeChunk", back_populates="document", cascade="all, delete-orphan"
    )


class AIKnowledgeChunk(Base):
    __tablename__ = "ai_knowledge_chunks"
    __table_args__ = (
        Index("ix_ai_knowledge_chunks_tenant_doc", "tenant_id", "document_id"),
        {"extend_existing": True},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("ai_knowledge_documents.id", ondelete="CASCADE"), nullable=False
    )
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    token_estimate: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    embedding: Mapped[list[float]] = mapped_column(Vector(768), nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    document: Mapped[AIKnowledgeDocument] = relationship("AIKnowledgeDocument", back_populates="chunks")


class AIConversation(Base):
    __tablename__ = "ai_conversations"
    __table_args__ = (
        Index("ix_ai_conversations_tenant_external", "tenant_id", "channel", "external_thread_id"),
        {"extend_existing": True},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    agent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("ai_agent_configs.id", ondelete="SET NULL"), nullable=True
    )
    company_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.id", ondelete="SET NULL"), nullable=True, index=True
    )
    contact_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("contacts.id", ondelete="SET NULL"), nullable=True, index=True
    )
    deal_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("deals.id", ondelete="SET NULL"), nullable=True, index=True
    )
    channel: Mapped[str] = mapped_column(String(30), nullable=False, default="whatsapp")
    external_thread_id: Mapped[str] = mapped_column(String(160), nullable=False)
    lead_identifier: Mapped[str | None] = mapped_column(String(160), nullable=True)

    status: Mapped[AIConversationStatus] = mapped_column(
        SAEnum(AIConversationStatus, name="ai_conversation_status_enum", create_type=True),
        nullable=False,
        default=AIConversationStatus.ACTIVE_AI,
    )
    turn_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    qualification_state: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=lambda: {
            "budget": None,
            "authority": None,
            "need": None,
            "timeline": None,
            "lead_score": 0,
        },
    )
    handoff_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    handoff_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_inbound_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_outbound_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    messages: Mapped[list[AIMessage]] = relationship(
        "AIMessage",
        back_populates="conversation",
        cascade="all, delete-orphan",
        order_by="AIMessage.created_at.asc()",
    )


class AIMessage(Base):
    __tablename__ = "ai_messages"
    __table_args__ = (
        Index("ix_ai_messages_conv_created", "conversation_id", "created_at"),
        {"extend_existing": True},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("ai_conversations.id", ondelete="CASCADE"), nullable=False
    )
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    sender_type: Mapped[str] = mapped_column(String(30), nullable=False, default="lead")
    content: Mapped[str] = mapped_column(Text, nullable=False)
    tool_calls_log: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    rag_sources_used: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    model_used: Mapped[str | None] = mapped_column(String(80), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    conversation: Mapped[AIConversation] = relationship("AIConversation", back_populates="messages")


class AIFollowupTask(Base):
    __tablename__ = "ai_followup_tasks"
    __table_args__ = (
        Index("ix_ai_followup_due", "status", "scheduled_for"),
        {"extend_existing": True},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("ai_conversations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    company_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.id", ondelete="SET NULL"), nullable=True
    )
    contact_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("contacts.id", ondelete="SET NULL"), nullable=True
    )
    deal_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("deals.id", ondelete="SET NULL"), nullable=True
    )
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    channel: Mapped[str] = mapped_column(String(30), nullable=False, default="whatsapp")
    objective: Mapped[str] = mapped_column(Text, nullable=False)
    scheduled_for: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[AIFollowupStatus] = mapped_column(
        SAEnum(AIFollowupStatus, name="ai_followup_status_enum", create_type=True),
        nullable=False,
        default=AIFollowupStatus.PENDING,
    )
    generated_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    executed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())