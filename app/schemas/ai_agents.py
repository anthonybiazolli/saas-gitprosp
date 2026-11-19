from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.models.ai_engine import AIAgentRole, AIConversationStatus, AIFollowupStatus


class AIAgentConfigCreate(BaseModel):
    name: str = Field(..., min_length=2, max_length=120)
    slug: str = Field(..., min_length=2, max_length=80, pattern=r"^[a-z0-9-]+$")
    role: AIAgentRole = AIAgentRole.SDR
    gemini_model: str = Field(default="gemini-2.5-flash", max_length=80)
    temperature: float = Field(default=0.35, ge=0.0, le=1.5)
    persona_prompt: str = Field(..., min_length=10)
    qualification_guidelines: str | None = None
    handoff_rules: str | None = None
    max_autonomous_turns: int = Field(default=15, ge=1, le=100)
    auto_followup_enabled: bool = True
    default_followup_delay_minutes: int = Field(default=240, ge=1, le=43200)
    is_active: bool = True
    metadata_json: dict[str, Any] = Field(default_factory=dict)


class AIAgentConfigUpdate(BaseModel):
    name: str | None = Field(None, min_length=2, max_length=120)
    gemini_model: str | None = Field(None, max_length=80)
    temperature: float | None = Field(None, ge=0.0, le=1.5)
    persona_prompt: str | None = Field(None, min_length=10)
    qualification_guidelines: str | None = None
    handoff_rules: str | None = None
    max_autonomous_turns: int | None = Field(None, ge=1, le=100)
    auto_followup_enabled: bool | None = None
    default_followup_delay_minutes: int | None = Field(None, ge=1, le=43200)
    is_active: bool | None = None
    metadata_json: dict[str, Any] | None = None


class AIAgentConfigResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    tenant_id: uuid.UUID
    name: str
    slug: str
    role: AIAgentRole
    gemini_model: str
    temperature: float
    persona_prompt: str
    qualification_guidelines: str | None
    handoff_rules: str | None
    max_autonomous_turns: int
    auto_followup_enabled: bool
    default_followup_delay_minutes: int
    is_active: bool
    metadata_json: dict[str, Any]
    created_at: datetime
    updated_at: datetime


class AIKnowledgeDocumentCreate(BaseModel):
    title: str = Field(..., min_length=2, max_length=220)
    category: str = Field(default="geral", max_length=80)
    source_type: str = Field(default="manual_text", max_length=40)
    content: str = Field(..., min_length=20)
    tags: list[str] = Field(default_factory=list)
    chunk_size: int = Field(default=900, ge=200, le=3000)
    chunk_overlap: int = Field(default=150, ge=0, le=600)


class AIKnowledgeDocumentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    tenant_id: uuid.UUID
    title: str
    category: str
    source_type: str
    tags: list[str]
    chunk_count: int
    is_active: bool
    created_at: datetime
    updated_at: datetime


class AIKnowledgeSearchRequest(BaseModel):
    query: str = Field(..., min_length=2, max_length=1000)
    category: str | None = None
    top_k: int = Field(default=4, ge=1, le=15)
    min_similarity: float = Field(default=0.45, ge=0.0, le=1.0)


class AIKnowledgeSearchHit(BaseModel):
    chunk_id: uuid.UUID
    document_id: uuid.UUID
    document_title: str
    category: str
    chunk_index: int
    similarity: float
    content: str


class SDRRespondRequest(BaseModel):
    external_thread_id: str = Field(
        ...,
        min_length=2,
        max_length=160,
        description="Identificador único da thread (ex: número WhatsApp E.164, e-mail ou session_id)",
    )
    message: str = Field(..., min_length=1, max_length=8000)
    channel: str = Field(default="whatsapp", max_length=30)
    lead_identifier: str | None = Field(
        None,
        description="Telefone, WhatsApp E.164 ou e-mail do lead para auto-identificação no CRM",
    )
    lead_name_hint: str | None = Field(None, description="Nome do perfil no WhatsApp/Canal se disponível")
    agent_slug: str | None = Field(None, description="Slug do agente SDR específico (opcional)")
    company_id: uuid.UUID | None = None
    contact_id: uuid.UUID | None = None
    deal_id: uuid.UUID | None = None


class ToolExecutionRecord(BaseModel):
    tool_name: str
    arguments: dict[str, Any]
    result: dict[str, Any]


class SDRRespondResponse(BaseModel):
    conversation_id: uuid.UUID
    status: AIConversationStatus
    reply_text: str | None
    ai_responded: bool
    turn_count: int
    company_id: uuid.UUID | None
    contact_id: uuid.UUID | None
    deal_id: uuid.UUID | None
    qualification_state: dict[str, Any]
    tools_executed: list[ToolExecutionRecord] = Field(default_factory=list)
    rag_sources_used: list[dict[str, Any]] = Field(default_factory=list)
    handoff_reason: str | None = None
    handoff_summary: str | None = None


class AIMessageResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    role: str
    sender_type: str
    content: str
    tool_calls_log: list[dict[str, Any]]
    rag_sources_used: list[dict[str, Any]]
    model_used: str | None
    created_at: datetime


class AIConversationSummaryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    tenant_id: uuid.UUID
    agent_id: uuid.UUID | None
    company_id: uuid.UUID | None
    contact_id: uuid.UUID | None
    deal_id: uuid.UUID | None
    channel: str
    external_thread_id: str
    lead_identifier: str | None
    status: AIConversationStatus
    turn_count: int
    qualification_state: dict[str, Any]
    handoff_reason: str | None
    handoff_summary: str | None
    last_inbound_at: datetime | None
    last_outbound_at: datetime | None
    created_at: datetime
    updated_at: datetime


class AIConversationDetailResponse(AIConversationSummaryResponse):
    messages: list[AIMessageResponse] = Field(default_factory=list)


class FollowupScheduleRequest(BaseModel):
    conversation_id: uuid.UUID
    delay_minutes: int = Field(default=60, ge=0, le=43200)
    objective: str = Field(..., min_length=5, max_length=1000)
    channel: str | None = None


class FollowupTaskResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    tenant_id: uuid.UUID
    conversation_id: uuid.UUID
    company_id: uuid.UUID | None
    contact_id: uuid.UUID | None
    deal_id: uuid.UUID | None
    attempt_number: int
    channel: str
    objective: str
    scheduled_for: datetime
    status: AIFollowupStatus
    generated_message: str | None
    executed_at: datetime | None
    error_message: str | None
    created_at: datetime
