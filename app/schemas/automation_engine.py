"""Schemas Pydantic v2 para Automação Omnichannel, Fila, Webhooks e Relatório Executivo."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.models.automation_engine import (
    AutomationTriggerType,
    DispatchChannelType,
    DispatchQueueStatus,
)


class AutomationRuleCreate(BaseModel):
    name: str = Field(..., min_length=2, max_length=150)
    description: str | None = None
    trigger_type: AutomationTriggerType
    channel: DispatchChannelType = DispatchChannelType.WHATSAPP
    delay_minutes: int = Field(default=0, ge=0, le=43200)
    message_template: str = Field(..., min_length=3)
    conditions_json: dict[str, Any] = Field(default_factory=dict)
    is_active: bool = True


class AutomationRuleUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=150)
    description: str | None = None
    channel: DispatchChannelType | None = None
    delay_minutes: int | None = Field(default=None, ge=0, le=43200)
    message_template: str | None = Field(default=None, min_length=3)
    conditions_json: dict[str, Any] | None = None
    is_active: bool | None = None


class AutomationRuleOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    tenant_id: uuid.UUID
    name: str
    description: str | None = None
    trigger_type: AutomationTriggerType
    channel: DispatchChannelType
    delay_minutes: int
    message_template: str
    conditions_json: dict[str, Any] = Field(default_factory=dict)
    is_active: bool
    created_at: datetime
    updated_at: datetime


class DispatchQueueCreate(BaseModel):
    channel: DispatchChannelType = DispatchChannelType.WHATSAPP
    destination: str = Field(..., min_length=3, max_length=255)
    subject: str | None = Field(default=None, max_length=220)
    rendered_message: str = Field(..., min_length=1)
    scheduled_for: datetime | None = None
    context_json: dict[str, Any] = Field(default_factory=dict)


class DispatchQueueOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    tenant_id: uuid.UUID
    rule_id: uuid.UUID | None = None
    channel: DispatchChannelType
    destination: str
    subject: str | None = None
    rendered_message: str
    status: DispatchQueueStatus
    scheduled_for: datetime
    sent_at: datetime | None = None
    attempts: int
    max_attempts: int
    last_error: str | None = None
    provider_receipt: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class WebhookEndpointCreate(BaseModel):
    name: str = Field(..., min_length=2, max_length=120)
    target_url: str = Field(..., min_length=8, max_length=500)
    secret_token: str | None = Field(default=None, max_length=120)
    subscribed_events: list[str] = Field(default_factory=lambda: ["*"])
    is_active: bool = True


class WebhookEndpointOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    tenant_id: uuid.UUID
    name: str
    target_url: str
    secret_token: str
    subscribed_events: list[str] = Field(default_factory=list)
    is_active: bool
    last_status_code: int | None = None
    last_triggered_at: datetime | None = None
    created_at: datetime


class AutomationTriggerRequest(BaseModel):
    event_type: AutomationTriggerType
    destination_phone: str | None = None
    destination_email: str | None = None
    context: dict[str, Any] = Field(default_factory=dict)
    process_immediately: bool = True


class WorkerCycleResultOut(BaseModel):
    processed_count: int
    sent_count: int
    failed_count: int
    requeued_count: int


class OverdueDunningScanOut(BaseModel):
    newly_marked_overdue: int
    dispatches_enqueued: int
    worker_result: WorkerCycleResultOut


class AutomationExecutionLogOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    tenant_id: uuid.UUID
    rule_id: uuid.UUID | None = None
    event_type: str
    status: str
    summary: str
    details: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class ExecutiveReportOut(BaseModel):
    generated_at: datetime
    open_deals_count: int
    open_pipeline_value: Decimal
    weighted_forecast_value: Decimal
    won_deals_count: int
    won_revenue_total: Decimal
    win_rate_percent: float
    active_contracts_count: int
    contracts_mrr_total: Decimal
    invoices_paid_total: Decimal
    invoices_pending_total: Decimal
    invoices_overdue_total: Decimal
    commissions_pending_total: Decimal
    commissions_paid_total: Decimal
    net_cash_after_commissions: Decimal
    dispatches_sent_count: int
    dispatches_queued_count: int
    dispatches_failed_count: int
