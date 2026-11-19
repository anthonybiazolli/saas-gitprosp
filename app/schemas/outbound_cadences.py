"""Schemas Pydantic v2 do Bloco 12: Motor de Cadências Ativas de Prospecção."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.models.outbound_cadences import CadenceChannel, EnrollmentStatus


class CadenceStepCreate(BaseModel):
    step_order: int = Field(..., ge=1, le=30)
    day_offset: int = Field(..., ge=0, le=90)
    channel: CadenceChannel = CadenceChannel.WHATSAPP
    subject_template: str = Field(default="", max_length=240)
    body_template: str = Field(..., min_length=3, max_length=5000)
    ai_prompt_instruction: str = Field(default="", max_length=1500)
    is_active: bool = True


class CadenceStepOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    step_order: int
    day_offset: int
    channel: CadenceChannel
    subject_template: str
    body_template: str
    ai_prompt_instruction: str
    is_active: bool


class CadenceCreate(BaseModel):
    name: str = Field(..., min_length=3, max_length=160)
    description: str = Field(default="", max_length=1500)
    target_persona: str = Field(default="Decisor B2B / Diretor Comercial", max_length=180)
    value_proposition: str = Field(
        default="Automação comercial omnichannel com SDR IA, propostas e cobrança PIX integrada.",
        max_length=2000,
    )
    tone_of_voice: str = Field(default="consultivo, direto e executivo", max_length=80)
    ai_personalization_enabled: bool = True
    stop_on_reply: bool = True
    stop_on_deal_won: bool = True
    steps: list[CadenceStepCreate] = Field(default_factory=list)


class CadenceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    tenant_id: uuid.UUID
    name: str
    description: str
    target_persona: str
    value_proposition: str
    tone_of_voice: str
    is_active: bool
    ai_personalization_enabled: bool
    stop_on_reply: bool
    stop_on_deal_won: bool
    created_at: datetime
    updated_at: datetime
    steps: list[CadenceStepOut] = Field(default_factory=list)


class CadenceEnrollRequest(BaseModel):
    lead_name: str = Field(..., min_length=2, max_length=160)
    company_name: str = Field(default="", max_length=180)
    email: str = Field(default="", max_length=180)
    phone: str = Field(default="", max_length=40)
    role_title: str = Field(default="", max_length=120)
    segment: str = Field(default="", max_length=120)
    context_notes: str = Field(default="", max_length=2000)
    start_immediately: bool = True
    metadata_json: dict[str, Any] = Field(default_factory=dict)


class CadenceBulkEnrollRequest(BaseModel):
    prospects: list[CadenceEnrollRequest] = Field(..., min_length=1, max_length=200)


class CadenceEnrollmentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    tenant_id: uuid.UUID
    cadence_id: uuid.UUID
    lead_name: str
    company_name: str
    email: str
    phone: str
    role_title: str
    segment: str
    context_notes: str
    status: EnrollmentStatus
    current_step_order: int
    next_run_at: datetime | None = None
    enrolled_at: datetime
    last_dispatched_at: datetime | None = None
    replied_at: datetime | None = None
    converted_deal_id: uuid.UUID | None = None


class CadenceConvertDealRequest(BaseModel):
    deal_title: str | None = Field(default=None, max_length=200)
    estimated_amount: float = Field(default=3500.0, ge=0)
    owner_name: str = Field(default="SDR IA", max_length=120)


class CadenceExecutionLogOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    cadence_id: uuid.UUID
    enrollment_id: uuid.UUID
    step_order: int
    day_offset: int
    channel: str
    destination: str
    rendered_subject: str
    rendered_body: str
    ai_generated: bool
    dispatch_status: str
    dispatch_queue_id: str | None = None
    executed_at: datetime


class CadenceProcessTickOut(BaseModel):
    checked_enrollments: int
    dispatched_steps: int
    auto_stopped_by_reply: int
    completed_enrollments: int
    skipped_missing_contact: int


class CadenceMetricsOut(BaseModel):
    total_cadences: int
    active_cadences: int
    total_enrollments: int
    active_enrollments: int
    replied_enrollments: int
    converted_enrollments: int
    completed_enrollments: int
    total_touches_executed: int
    ai_personalized_touches: int
    reply_rate_pct: float
    conversion_rate_pct: float
