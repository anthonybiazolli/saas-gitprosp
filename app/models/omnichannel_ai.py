from __future__ import annotations

import enum
import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import (
    Base,
    TenantScopedMixin,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
)

if TYPE_CHECKING:
    from app.models.core import Tenant


class ChannelType(str, enum.Enum):
    WHATSAPP = "whatsapp"
    INSTAGRAM = "instagram"
    FACEBOOK_MESSENGER = "facebook_messenger"
    EMAIL = "email"
    VOIP_VOICE = "voip_voice"
    SMS = "sms"
    WEBCHAT = "webchat"


class ConversationStatus(str, enum.Enum):
    OPEN_AI = "open_ai"
    OPEN_HUMAN = "open_human"
    WAITING_LEAD = "waiting_lead"
    RESOLVED = "resolved"
    ARCHIVED = "archived"


class MessageDirection(str, enum.Enum):
    INBOUND = "inbound"
    OUTBOUND = "outbound"


class MessageContentType(str, enum.Enum):
    TEXT = "text"
    AUDIO = "audio"
    IMAGE = "image"
    VIDEO = "video"
    DOCUMENT = "document"
    TEMPLATE = "template"
    INTERACTIVE = "interactive"


class AiAgentRole(str, enum.Enum):
    ROUTER = "router"
    SDR_OUTBOUND = "sdr_outbound"
    SDR_INBOUND = "sdr_inbound"
    FOLLOWUP = "followup"
    SUPPORT_N1 = "support_n1"
    CS_ONBOARDING = "cs_onboarding"
    COLLECTIONS = "collections"


class EnrollmentStatus(str, enum.Enum):
    ACTIVE = "active"
    PAUSED_HUMAN_REPLY = "paused_human_reply"
    PAUSED_LEAD_REPLIED = "paused_lead_replied"
    COMPLETED = "completed"
    CONVERTED = "converted"
    OPTED_OUT = "opted_out"
    FAILED = "failed"


class ChannelAccount(Base, UUIDPrimaryKeyMixin, TenantScopedMixin, TimestampMixin):
    """Instâncias de comunicação conectadas (WhatsApp via Evolution/Connecty, SMTP/IMAP, Meta, VoIP)."""
    __tablename__ = "channel_accounts"

    name: Mapped[str] = mapped_column(String(120), nullable=False)
    channel_type: Mapped[ChannelType] = mapped_column(
        Enum(ChannelType, name="channel_type_enum"), nullable=False
    )
    identifier: Mapped[str] = mapped_column(String(200), nullable=False)
    provider: Mapped[str] = mapped_column(String(60), nullable=False)
    credentials: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    default_ai_agent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("ai_agent_configs.id", ondelete="SET NULL"), nullable=True
    )
    is_connected: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    daily_send_limit: Mapped[int] = mapped_column(Integer, nullable=False, default=500)

    tenant: Mapped[Tenant] = relationship("Tenant", back_populates="channel_accounts")


class Conversation(Base, UUIDPrimaryKeyMixin, TenantScopedMixin, TimestampMixin):
    """Thread da Inbox Omnichannel unificada."""
    __tablename__ = "conversations"
    __table_args__ = (
        Index("ix_conversations_tenant_status_last_msg", "tenant_id", "status", "last_message_at"),
    )

    channel_account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("channel_accounts.id", ondelete="CASCADE"), nullable=False
    )
    channel_type: Mapped[ChannelType] = mapped_column(
        Enum(ChannelType, name="channel_type_enum"), nullable=False
    )
    external_thread_id: Mapped[str] = mapped_column(String(200), nullable=False, index=True)

    company_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.id", ondelete="SET NULL"), nullable=True, index=True
    )
    contact_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("contacts.id", ondelete="SET NULL"), nullable=True, index=True
    )
    deal_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("deals.id", ondelete="SET NULL"), nullable=True
    )
    ticket_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tickets.id", ondelete="SET NULL"), nullable=True
    )

    status: Mapped[ConversationStatus] = mapped_column(
        Enum(ConversationStatus, name="conversation_status_enum"),
        nullable=False,
        default=ConversationStatus.OPEN_AI,
    )
    assigned_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    active_ai_agent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("ai_agent_configs.id", ondelete="SET NULL"), nullable=True
    )

    is_ai_paused: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    ai_pause_reason: Mapped[str | None] = mapped_column(String(150), nullable=True)

    unread_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    sentiment_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    context_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_message_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )

    messages: Mapped[list[Message]] = relationship(
        "Message", back_populates="conversation", cascade="all, delete-orphan"
    )


class Message(Base, UUIDPrimaryKeyMixin, TenantScopedMixin, TimestampMixin):
    __tablename__ = "messages"
    __table_args__ = (
        Index("ix_messages_conversation_created", "conversation_id", "created_at"),
    )

    conversation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False
    )
    direction: Mapped[MessageDirection] = mapped_column(
        Enum(MessageDirection, name="message_direction_enum"), nullable=False
    )
    sender_type: Mapped[str] = mapped_column(String(30), nullable=False)
    sender_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    sender_ai_agent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("ai_agent_configs.id", ondelete="SET NULL"), nullable=True
    )

    content_type: Mapped[MessageContentType] = mapped_column(
        Enum(MessageContentType, name="message_content_type_enum"),
        nullable=False,
        default=MessageContentType.TEXT,
    )
    body: Mapped[str | None] = mapped_column(Text, nullable=True)
    media_url: Mapped[str | None] = mapped_column(String(600), nullable=True)
    media_mime_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    audio_transcription: Mapped[str | None] = mapped_column(Text, nullable=True)

    external_message_id: Mapped[str | None] = mapped_column(String(200), nullable=True, index=True)
    delivery_status: Mapped[str] = mapped_column(String(30), nullable=False, default="sent")
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    provider_metadata: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    conversation: Mapped[Conversation] = relationship("Conversation", back_populates="messages")


class AiAgentConfig(Base, UUIDPrimaryKeyMixin, TenantScopedMixin, TimestampMixin):
    """Configuração de agentes autônomos (SDR, Follow-up, Suporte N1, Cobrança)."""
    __tablename__ = "ai_agent_configs"

    name: Mapped[str] = mapped_column(String(120), nullable=False)
    role: Mapped[AiAgentRole] = mapped_column(
        Enum(AiAgentRole, name="ai_agent_role_enum"), nullable=False
    )
    llm_model: Mapped[str] = mapped_column(String(80), nullable=False, default="gemini-2.5-flash")
    temperature: Mapped[float] = mapped_column(Float, nullable=False, default=0.3)

    system_prompt: Mapped[str] = mapped_column(Text, nullable=False)
    persona_guidelines: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    qualification_criteria: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    allowed_tools: Mapped[list[str]] = mapped_column(
        ARRAY(String),
        nullable=False,
        default=lambda: [
            "search_knowledge_base",
            "update_lead_fields",
            "move_deal_stage",
            "check_calendar_slots",
            "book_meeting",
            "handoff_to_human",
        ],
    )
    handoff_rules: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    max_autonomous_turns: Mapped[int] = mapped_column(Integer, nullable=False, default=15)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    tenant: Mapped[Tenant] = relationship("Tenant", back_populates="ai_agents")
    knowledge_documents: Mapped[list[KnowledgeBaseDocument]] = relationship(
        "KnowledgeBaseDocument", back_populates="agent", cascade="all, delete-orphan"
    )


class KnowledgeBaseDocument(Base, UUIDPrimaryKeyMixin, TenantScopedMixin, TimestampMixin):
    """Playbooks comerciais, tabelas de preços e manuais técnicos indexados para RAG."""
    __tablename__ = "knowledge_base_documents"

    agent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("ai_agent_configs.id", ondelete="CASCADE"), nullable=True, index=True
    )
    title: Mapped[str] = mapped_column(String(220), nullable=False)
    category: Mapped[str] = mapped_column(String(80), nullable=False, default="playbook")
    source_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    raw_content: Mapped[str] = mapped_column(Text, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    agent: Mapped[AiAgentConfig | None] = relationship("AiAgentConfig", back_populates="knowledge_documents")
    chunks: Mapped[list[KnowledgeBaseChunk]] = relationship(
        "KnowledgeBaseChunk", back_populates="document", cascade="all, delete-orphan"
    )


class KnowledgeBaseChunk(Base, UUIDPrimaryKeyMixin, TenantScopedMixin, TimestampMixin):
    """Chunks vetorizados via pgvector para busca semântica de baixa latência."""
    __tablename__ = "knowledge_base_chunks"

    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("knowledge_base_documents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    embedding: Mapped[list[float]] = mapped_column(Vector(768), nullable=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)

    document: Mapped[KnowledgeBaseDocument] = relationship("KnowledgeBaseDocument", back_populates="chunks")


class Cadence(Base, UUIDPrimaryKeyMixin, TenantScopedMixin, TimestampMixin):
    """Cadências multicanal automatizadas de prospecção e follow-up com IA."""
    __tablename__ = "cadences"

    name: Mapped[str] = mapped_column(String(150), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    target_segment: Mapped[str | None] = mapped_column(String(100), nullable=True)
    ai_agent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("ai_agent_configs.id", ondelete="SET NULL"), nullable=True
    )
    stop_on_reply: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    respect_business_hours: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    tenant: Mapped[Tenant] = relationship("Tenant", back_populates="cadences")
    steps: Mapped[list[CadenceStep]] = relationship(
        "app.models.omnichannel_ai.CadenceStep",
        back_populates="cadence",
        order_by="app.models.omnichannel_ai.CadenceStep.step_order",
        cascade="all, delete-orphan",
    )
    enrollments: Mapped[list[CadenceEnrollment]] = relationship(
        "app.models.omnichannel_ai.CadenceEnrollment", back_populates="cadence", cascade="all, delete-orphan"
    )


class CadenceStep(Base, UUIDPrimaryKeyMixin, TenantScopedMixin, TimestampMixin):
    __tablename__ = "cadence_steps"
    __table_args__ = (UniqueConstraint("cadence_id", "step_order", name="uq_cadence_step_order"),)

    cadence_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cadences.id", ondelete="CASCADE"), nullable=False
    )
    step_order: Mapped[int] = mapped_column(Integer, nullable=False)
    delay_minutes_from_previous: Mapped[int] = mapped_column(Integer, nullable=False, default=1440)
    channel_type: Mapped[ChannelType] = mapped_column(
        Enum(ChannelType, name="channel_type_enum"), nullable=False
    )
    use_ai_dynamic_generation: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    ai_step_objective: Mapped[str | None] = mapped_column(Text, nullable=True)
    fallback_template: Mapped[str | None] = mapped_column(Text, nullable=True)

    cadence: Mapped[Cadence] = relationship("Cadence", back_populates="steps")


class CadenceEnrollment(Base, UUIDPrimaryKeyMixin, TenantScopedMixin, TimestampMixin):
    """Máquina de estados que controla cada lead dentro de uma cadência de follow-up."""
    __tablename__ = "cadence_enrollments"
    __table_args__ = (
        Index("ix_cadence_enrollments_worker_poll", "status", "next_execution_at"),
    )

    cadence_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cadences.id", ondelete="CASCADE"), nullable=False
    )
    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True
    )
    contact_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("contacts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    deal_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("deals.id", ondelete="SET NULL"), nullable=True
    )

    current_step_order: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    status: Mapped[EnrollmentStatus] = mapped_column(
        Enum(EnrollmentStatus, name="enrollment_status_enum"),
        nullable=False,
        default=EnrollmentStatus.ACTIVE,
    )
    next_execution_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ai_state_memory: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    stop_reason: Mapped[str | None] = mapped_column(String(150), nullable=True)

    cadence: Mapped[Cadence] = relationship("Cadence", back_populates="enrollments")


class AiExecutionLog(Base, UUIDPrimaryKeyMixin, TenantScopedMixin):
    """Telemetria de custos, tokens e chamadas de ferramentas dos agentes de IA."""
    __tablename__ = "ai_execution_logs"

    agent_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("ai_agent_configs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    conversation_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("conversations.id", ondelete="SET NULL"), nullable=True
    )
    llm_model: Mapped[str] = mapped_column(String(80), nullable=False)
    prompt_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    tools_called: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )
