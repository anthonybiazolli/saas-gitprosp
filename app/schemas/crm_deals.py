"""Schemas Pydantic v2 para Pipeline CRM, Deals, Atividades e Contratos."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.models.crm_deals import ContractStatus, DealActivityType, DealStatus


class PipelineStageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    pipeline_id: uuid.UUID
    name: str
    stage_order: int
    default_probability: int
    color: str
    is_won: bool
    is_lost: bool


class PipelineOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    tenant_id: uuid.UUID
    name: str
    description: str | None = None
    is_default: bool
    is_active: bool
    stages: list[PipelineStageOut] = Field(default_factory=list)


class DealCreate(BaseModel):
    title: str = Field(..., min_length=2, max_length=200)
    contact_name: str = Field(..., min_length=2, max_length=150)
    company_name: str | None = Field(default=None, max_length=180)
    contact_email: str | None = Field(default=None, max_length=180)
    contact_phone: str | None = Field(default=None, max_length=40)
    contact_document: str | None = Field(default=None, max_length=32)

    stage_id: uuid.UUID | None = None
    amount: Decimal = Field(default=Decimal("0.00"), ge=0)
    currency: str = Field(default="BRL", max_length=8)
    probability: int | None = Field(default=None, ge=0, le=100)

    source: str = Field(default="manual", max_length=50)
    owner_name: str | None = Field(default=None, max_length=120)
    expected_close_date: datetime | None = None
    notes: str | None = None

    lead_id: uuid.UUID | None = None
    proposal_id: uuid.UUID | None = None
    conversation_id: uuid.UUID | None = None

    tags: list[str] = Field(default_factory=list)
    custom_fields: dict[str, Any] = Field(default_factory=dict)


class DealUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=2, max_length=200)
    company_name: str | None = Field(default=None, max_length=180)
    contact_name: str | None = Field(default=None, min_length=2, max_length=150)
    contact_email: str | None = Field(default=None, max_length=180)
    contact_phone: str | None = Field(default=None, max_length=40)
    contact_document: str | None = Field(default=None, max_length=32)

    amount: Decimal | None = Field(default=None, ge=0)
    probability: int | None = Field(default=None, ge=0, le=100)
    owner_name: str | None = Field(default=None, max_length=120)
    expected_close_date: datetime | None = None
    loss_reason: str | None = Field(default=None, max_length=255)
    notes: str | None = None
    proposal_id: uuid.UUID | None = None
    tags: list[str] | None = None
    custom_fields: dict[str, Any] | None = None


class DealMoveRequest(BaseModel):
    stage_id: uuid.UUID
    probability: int | None = Field(default=None, ge=0, le=100)
    loss_reason: str | None = Field(default=None, max_length=255)
    note: str | None = None
    actor_name: str | None = Field(default="Sistema", max_length=120)
    auto_generate_contract: bool = True


class DealActivityCreate(BaseModel):
    activity_type: DealActivityType = DealActivityType.NOTE
    title: str = Field(..., min_length=2, max_length=200)
    description: str | None = None
    actor_name: str | None = Field(default="Operador", max_length=120)
    payload: dict[str, Any] = Field(default_factory=dict)


class DealActivityOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    deal_id: uuid.UUID
    activity_type: DealActivityType
    title: str
    description: str | None = None
    actor_name: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class DealOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    tenant_id: uuid.UUID
    pipeline_id: uuid.UUID
    stage_id: uuid.UUID
    title: str
    company_name: str | None = None
    contact_name: str
    contact_email: str | None = None
    contact_phone: str | None = None
    contact_document: str | None = None
    amount: Decimal
    currency: str
    probability: int
    status: DealStatus
    source: str
    owner_name: str | None = None
    expected_close_date: datetime | None = None
    closed_at: datetime | None = None
    loss_reason: str | None = None
    notes: str | None = None
    lead_id: uuid.UUID | None = None
    proposal_id: uuid.UUID | None = None
    conversation_id: uuid.UUID | None = None
    tags: list[str] = Field(default_factory=list)
    custom_fields: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime


class KanbanColumnOut(BaseModel):
    stage: PipelineStageOut
    deal_count: int
    total_amount: Decimal
    weighted_amount: Decimal
    deals: list[DealOut] = Field(default_factory=list)


class KanbanBoardOut(BaseModel):
    pipeline: PipelineOut
    total_open_amount: Decimal
    total_weighted_amount: Decimal
    columns: list[KanbanColumnOut] = Field(default_factory=list)


class ContractCreate(BaseModel):
    deal_id: uuid.UUID | None = None
    proposal_id: uuid.UUID | None = None
    title: str = Field(..., min_length=2, max_length=220)
    customer_name: str = Field(..., min_length=2, max_length=180)
    customer_email: str | None = Field(default=None, max_length=180)
    customer_document: str | None = Field(default=None, max_length=32)
    total_value: Decimal = Field(default=Decimal("0.00"), ge=0)
    recurring_monthly_value: Decimal = Field(default=Decimal("0.00"), ge=0)
    billing_cycle: str = Field(default="monthly", max_length=30)
    currency: str = Field(default="BRL", max_length=8)
    start_date: datetime | None = None
    end_date: datetime | None = None
    terms_summary: str | None = None
    metadata_json: dict[str, Any] = Field(default_factory=dict)


class ContractSignRequest(BaseModel):
    signed_by_name: str = Field(..., min_length=2, max_length=150)
    signed_by_document: str | None = Field(default=None, max_length=32)
    activate_immediately: bool = True


class ContractOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    tenant_id: uuid.UUID
    deal_id: uuid.UUID | None = None
    proposal_id: uuid.UUID | None = None
    contract_number: str
    title: str
    customer_name: str
    customer_email: str | None = None
    customer_document: str | None = None
    total_value: Decimal
    recurring_monthly_value: Decimal
    billing_cycle: str
    currency: str
    status: ContractStatus
    start_date: datetime | None = None
    end_date: datetime | None = None
    signed_at: datetime | None = None
    signed_by_name: str | None = None
    signed_by_document: str | None = None
    terms_summary: str | None = None
    metadata_json: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime


class DealMoveResponse(BaseModel):
    deal: DealOut
    generated_contract: ContractOut | None = None


class StageMetricOut(BaseModel):
    stage_id: uuid.UUID
    stage_name: str
    stage_order: int
    deal_count: int
    total_amount: Decimal
    weighted_amount: Decimal


class SourceMetricOut(BaseModel):
    source: str
    deal_count: int
    won_count: int
    total_amount: Decimal
    won_amount: Decimal


class CrmAnalyticsOut(BaseModel):
    open_deals_count: int
    open_pipeline_value: Decimal
    weighted_forecast_value: Decimal
    won_deals_count: int
    won_revenue_total: Decimal
    lost_deals_count: int
    average_ticket: Decimal
    win_rate_percent: float
    contracts_total_count: int
    contracts_signed_or_active_count: int
    contracts_mrr_total: Decimal
    by_stage: list[StageMetricOut] = Field(default_factory=list)
    by_source: list[SourceMetricOut] = Field(default_factory=list)
