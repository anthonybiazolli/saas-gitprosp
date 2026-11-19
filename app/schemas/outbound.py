from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.models.outbound import (
    CampaignStatus,
    EnrollmentStatus,
    OutboundLeadStatus,
)


class CampaignCreate(BaseModel):
    name: str = Field(..., min_length=2, max_length=160)
    slug: str = Field(..., min_length=2, max_length=100, pattern=r"^[a-z0-9-]+$")
    icp_description: str = Field(..., min_length=10)
    value_proposition: str | None = None
    default_channel: str = Field(default="whatsapp", max_length=30)
    status: CampaignStatus = CampaignStatus.ACTIVE
    auto_send_first_touch: bool = False
    metadata_json: dict[str, Any] = Field(default_factory=dict)


class CampaignResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    tenant_id: uuid.UUID
    name: str
    slug: str
    icp_description: str
    value_proposition: str | None
    default_channel: str
    status: CampaignStatus
    auto_send_first_touch: bool
    metadata_json: dict[str, Any]
    created_at: datetime
    updated_at: datetime


class OutboundLeadInput(BaseModel):
    company_name: str = Field(..., min_length=2, max_length=200)
    cnpj: str | None = Field(None, max_length=32)
    website: str | None = Field(None, max_length=255)
    sector: str | None = Field(None, max_length=120)
    city: str | None = Field(None, max_length=120)
    state_uf: str | None = Field(None, max_length=4)
    contact_name: str | None = Field(None, max_length=160)
    job_title: str | None = Field(None, max_length=120)
    email: str | None = Field(None, max_length=160)
    whatsapp_e164: str | None = Field(None, max_length=40)
    source_channel: str = Field(default="outbound_import", max_length=50)
    notes: str | None = None


class OutboundLeadBatchImportRequest(BaseModel):
    campaign_id: uuid.UUID | None = None
    leads: list[OutboundLeadInput] = Field(..., min_length=1, max_length=200)
    auto_enrich: bool = True


class OutboundLeadResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    tenant_id: uuid.UUID
    campaign_id: uuid.UUID | None
    company_name: str
    cnpj: str | None
    website: str | None
    sector: str | None
    city: str | None
    state_uf: str | None
    contact_name: str | None
    job_title: str | None
    email: str | None
    whatsapp_e164: str | None
    source_channel: str
    status: OutboundLeadStatus
    icp_score: int
    enrichment_json: dict[str, Any]
    generated_opening_message: str | None
    promoted_company_id: uuid.UUID | None
    promoted_contact_id: uuid.UUID | None
    promoted_deal_id: uuid.UUID | None
    conversation_id: uuid.UUID | None
    created_at: datetime
    updated_at: datetime


class OutboundPromoteRequest(BaseModel):
    create_deal: bool = True
    deal_title: str | None = None
    estimated_mrr: float = Field(default=0.0, ge=0.0)
    send_opening_whatsapp: bool = False
    instance_name: str | None = None


class CadenceStepInput(BaseModel):
    step_order: int = Field(..., ge=1, le=50)
    delay_minutes_from_prev: int = Field(default=60, ge=0, le=129600)
    channel: str = Field(default="whatsapp", max_length=30)
    objective: str = Field(..., min_length=4, max_length=1000)
    message_template: str | None = Field(
        None,
        description="Template opcional com variáveis {{company_name}}, {{contact_name}}, {{objective}}; se omitido, a IA redige na hora.",
    )


class CadenceStepResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    step_order: int
    delay_minutes_from_prev: int
    channel: str
    objective: str
    message_template: str | None


class CadenceSequenceCreate(BaseModel):
    name: str = Field(..., min_length=2, max_length=160)
    slug: str = Field(..., min_length=2, max_length=100, pattern=r"^[a-z0-9-]+$")
    channel: str = Field(default="whatsapp", max_length=30)
    stop_on_reply: bool = True
    is_active: bool = True
    steps: list[CadenceStepInput] = Field(..., min_length=1, max_length=20)


class CadenceSequenceResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    tenant_id: uuid.UUID
    name: str
    slug: str
    channel: str
    stop_on_reply: bool
    is_active: bool
    created_at: datetime
    updated_at: datetime
    steps: list[CadenceStepResponse] = Field(default_factory=list)


class CadenceEnrollRequest(BaseModel):
    sequence_id: uuid.UUID
    target_identifier: str = Field(..., min_length=4, max_length=160, description="Telefone/WhatsApp ou identificador")
    channel: str | None = None
    company_id: uuid.UUID | None = None
    contact_id: uuid.UUID | None = None
    deal_id: uuid.UUID | None = None
    conversation_id: uuid.UUID | None = None
    start_immediately: bool = False


class CadenceEnrollmentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    tenant_id: uuid.UUID
    sequence_id: uuid.UUID
    conversation_id: uuid.UUID | None
    company_id: uuid.UUID | None
    contact_id: uuid.UUID | None
    deal_id: uuid.UUID | None
    target_identifier: str
    channel: str
    current_step_order: int
    status: EnrollmentStatus
    next_run_at: datetime
    enrolled_at: datetime
    last_executed_at: datetime | None
    last_message_sent: str | None
    last_error: str | None
