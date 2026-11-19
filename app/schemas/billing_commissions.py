"""Schemas Pydantic v2 para Faturamento, Cobranças e Comissões."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.models.billing_commissions import (
    BillingEventType,
    CommissionRoleType,
    CommissionStatus,
    InvoiceStatus,
    PaymentMethodType,
)


class InvoiceCreate(BaseModel):
    contract_id: uuid.UUID | None = None
    deal_id: uuid.UUID | None = None
    customer_name: str = Field(..., min_length=2, max_length=180)
    customer_email: str | None = Field(default=None, max_length=180)
    customer_document: str | None = Field(default=None, max_length=32)
    description: str = Field(..., min_length=2, max_length=255)
    amount: Decimal = Field(..., gt=0)
    discount_amount: Decimal = Field(default=Decimal("0.00"), ge=0)
    currency: str = Field(default="BRL", max_length=8)
    payment_method: PaymentMethodType = PaymentMethodType.PIX
    due_in_days: int = Field(default=5, ge=0, le=365)
    due_date: datetime | None = None
    closer_name: str | None = Field(default=None, max_length=120)
    sdr_name: str | None = Field(default=None, max_length=120)
    metadata_json: dict[str, Any] = Field(default_factory=dict)


class ContractInvoiceGenerateRequest(BaseModel):
    description: str | None = Field(default=None, max_length=255)
    discount_amount: Decimal = Field(default=Decimal("0.00"), ge=0)
    payment_method: PaymentMethodType = PaymentMethodType.PIX
    due_in_days: int = Field(default=5, ge=0, le=365)
    closer_name: str | None = Field(default=None, max_length=120)
    sdr_name: str | None = Field(default=None, max_length=120)


class InvoicePayRequest(BaseModel):
    gateway_reference: str | None = Field(default=None, max_length=120)
    notes: str | None = None
    closer_name: str | None = Field(default=None, max_length=120)
    sdr_name: str | None = Field(default=None, max_length=120)


class InvoiceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    tenant_id: uuid.UUID
    contract_id: uuid.UUID | None = None
    deal_id: uuid.UUID | None = None
    invoice_number: str
    customer_name: str
    customer_email: str | None = None
    customer_document: str | None = None
    description: str
    amount: Decimal
    discount_amount: Decimal
    net_amount: Decimal
    currency: str
    payment_method: PaymentMethodType
    status: InvoiceStatus
    due_date: datetime
    paid_at: datetime | None = None
    external_charge_id: str | None = None
    pix_copy_paste: str | None = None
    payment_link: str | None = None
    closer_name: str | None = None
    sdr_name: str | None = None
    metadata_json: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime


class PaymentEventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    invoice_id: uuid.UUID
    event_type: BillingEventType
    amount: Decimal
    gateway_reference: str | None = None
    notes: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class CommissionRuleCreate(BaseModel):
    name: str = Field(..., min_length=2, max_length=120)
    role_type: CommissionRoleType = CommissionRoleType.CLOSER
    percentage: Decimal = Field(..., ge=0, le=100)
    fixed_bonus: Decimal = Field(default=Decimal("0.00"), ge=0)
    is_active: bool = True


class CommissionRuleOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    tenant_id: uuid.UUID
    name: str
    role_type: CommissionRoleType
    percentage: Decimal
    fixed_bonus: Decimal
    is_active: bool
    created_at: datetime


class SalesCommissionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    tenant_id: uuid.UUID
    rule_id: uuid.UUID | None = None
    invoice_id: uuid.UUID
    contract_id: uuid.UUID | None = None
    deal_id: uuid.UUID | None = None
    beneficiary_name: str
    role_type: CommissionRoleType
    base_amount: Decimal
    commission_amount: Decimal
    status: CommissionStatus
    paid_at: datetime | None = None
    notes: str | None = None
    created_at: datetime


class CommissionStatusUpdateRequest(BaseModel):
    status: CommissionStatus
    notes: str | None = None


class InvoicePayResponse(BaseModel):
    invoice: InvoiceOut
    generated_commissions: list[SalesCommissionOut] = Field(default_factory=list)


class FinancialSummaryOut(BaseModel):
    invoices_total_count: int
    total_invoiced_amount: Decimal
    total_paid_amount: Decimal
    total_pending_amount: Decimal
    total_overdue_amount: Decimal
    paid_invoices_count: int
    pending_invoices_count: int
    overdue_invoices_count: int
    commissions_pending_amount: Decimal
    commissions_approved_amount: Decimal
    commissions_paid_amount: Decimal
    net_revenue_after_commissions: Decimal
