"""Schemas Pydantic v2 para Planos SaaS, Metering e Gateway de Webhooks Inbound."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.models.saas_gateway import SaasPlanTier, SubscriptionBillingStatus


class SubscriptionUpdateRequest(BaseModel):
    plan_tier: SaasPlanTier | None = None
    billing_status: SubscriptionBillingStatus | None = None
    monthly_price: Decimal | None = Field(default=None, ge=0)
    max_team_members: int | None = Field(default=None, ge=1)
    max_monthly_dispatches: int | None = Field(default=None, ge=1)
    max_active_webhooks: int | None = Field(default=None, ge=1)


class SubscriptionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    tenant_id: uuid.UUID
    plan_tier: SaasPlanTier
    billing_status: SubscriptionBillingStatus
    monthly_price: Decimal
    max_team_members: int
    max_monthly_dispatches: int
    max_active_webhooks: int
    inbound_webhook_secret: str
    current_period_end: datetime
    created_at: datetime
    updated_at: datetime


class TenantUsageOut(BaseModel):
    tenant_id: uuid.UUID
    plan_tier: SaasPlanTier
    billing_status: SubscriptionBillingStatus
    active_team_members: int
    max_team_members: int
    team_usage_percent: float
    dispatches_this_month: int
    max_monthly_dispatches: int
    dispatches_usage_percent: float
    active_webhooks: int
    max_active_webhooks: int
    open_deals_count: int
    active_contracts_count: int
    invoices_count: int


class InboundWebhookResultOut(BaseModel):
    receipt_id: uuid.UUID
    provider: str
    event_name: str
    processing_status: str
    summary: str
    settled_invoice_number: str | None = None
    generated_commissions_count: int = 0
    enqueued_automations_count: int = 0


class InboundWebhookReceiptOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    tenant_id: uuid.UUID | None = None
    provider: str
    event_name: str
    external_event_id: str | None = None
    processing_status: str
    summary: str
    raw_payload: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
