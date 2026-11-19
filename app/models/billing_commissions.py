"""Modelos SQLAlchemy do Módulo de Faturamento, Cobranças e Comissões."""

from __future__ import annotations

import enum
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum as SAEnum,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class PaymentMethodType(str, enum.Enum):
    PIX = "PIX"
    BOLETO = "BOLETO"
    CREDIT_CARD = "CREDIT_CARD"
    TRANSFER = "TRANSFER"


class InvoiceStatus(str, enum.Enum):
    DRAFT = "DRAFT"
    PENDING = "PENDING"
    PAID = "PAID"
    OVERDUE = "OVERDUE"
    CANCELLED = "CANCELLED"
    REFUNDED = "REFUNDED"


class BillingEventType(str, enum.Enum):
    CREATED = "CREATED"
    REMINDER_SENT = "REMINDER_SENT"
    PAID = "PAID"
    OVERDUE_MARKED = "OVERDUE_MARKED"
    CANCELLED = "CANCELLED"
    WEBHOOK_RECEIVED = "WEBHOOK_RECEIVED"


class CommissionRoleType(str, enum.Enum):
    CLOSER = "CLOSER"
    SDR = "SDR"
    PARTNER = "PARTNER"


class CommissionStatus(str, enum.Enum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    PAID = "PAID"
    CANCELLED = "CANCELLED"


class BillingInvoice(Base):
    __tablename__ = "billing_invoices"
    __table_args__ = (
        UniqueConstraint("tenant_id", "invoice_number", name="uq_billing_invoice_tenant_number"),
        Index("ix_billing_invoices_tenant_status", "tenant_id", "status"),
        Index("ix_billing_invoices_tenant_due", "tenant_id", "due_date"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    contract_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("crm_contracts.id", ondelete="SET NULL"), nullable=True, index=True
    )
    deal_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True, index=True)

    invoice_number: Mapped[str] = mapped_column(String(40), nullable=False)
    customer_name: Mapped[str] = mapped_column(String(180), nullable=False)
    customer_email: Mapped[str | None] = mapped_column(String(180), nullable=True)
    customer_document: Mapped[str | None] = mapped_column(String(32), nullable=True)
    description: Mapped[str] = mapped_column(String(255), nullable=False)

    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=Decimal("0.00"), nullable=False)
    discount_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=Decimal("0.00"), nullable=False)
    net_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=Decimal("0.00"), nullable=False)
    currency: Mapped[str] = mapped_column(String(8), default="BRL", nullable=False)

    payment_method: Mapped[PaymentMethodType] = mapped_column(
        SAEnum(PaymentMethodType, native_enum=False, length=24),
        default=PaymentMethodType.PIX,
        nullable=False,
    )
    status: Mapped[InvoiceStatus] = mapped_column(
        SAEnum(InvoiceStatus, native_enum=False, length=24),
        default=InvoiceStatus.PENDING,
        nullable=False,
    )

    due_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    external_charge_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    pix_copy_paste: Mapped[str | None] = mapped_column(Text, nullable=True)
    payment_link: Mapped[str | None] = mapped_column(String(300), nullable=True)

    closer_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    sdr_name: Mapped[str | None] = mapped_column(String(120), nullable=True)

    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=_utcnow, nullable=False
    )


class BillingPaymentEvent(Base):
    __tablename__ = "billing_payment_events"
    __table_args__ = (
        Index("ix_billing_events_tenant_invoice", "tenant_id", "invoice_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    invoice_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("billing_invoices.id", ondelete="CASCADE"), nullable=False, index=True
    )
    event_type: Mapped[BillingEventType] = mapped_column(
        SAEnum(BillingEventType, native_enum=False, length=32),
        default=BillingEventType.CREATED,
        nullable=False,
    )
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=Decimal("0.00"), nullable=False)
    gateway_reference: Mapped[str | None] = mapped_column(String(120), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class CommissionRule(Base):
    __tablename__ = "billing_commission_rules"
    __table_args__ = (
        Index("ix_billing_comm_rules_tenant_active", "tenant_id", "is_active"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    role_type: Mapped[CommissionRoleType] = mapped_column(
        SAEnum(CommissionRoleType, native_enum=False, length=24),
        default=CommissionRoleType.CLOSER,
        nullable=False,
    )
    percentage: Mapped[Decimal] = mapped_column(Numeric(6, 2), default=Decimal("10.00"), nullable=False)
    fixed_bonus: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=Decimal("0.00"), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class SalesCommission(Base):
    __tablename__ = "billing_sales_commissions"
    __table_args__ = (
        Index("ix_billing_commissions_tenant_status", "tenant_id", "status"),
        Index("ix_billing_commissions_invoice", "tenant_id", "invoice_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    rule_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("billing_commission_rules.id", ondelete="SET NULL"), nullable=True
    )
    invoice_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("billing_invoices.id", ondelete="CASCADE"), nullable=False, index=True
    )
    contract_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    deal_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)

    beneficiary_name: Mapped[str] = mapped_column(String(140), nullable=False)
    role_type: Mapped[CommissionRoleType] = mapped_column(
        SAEnum(CommissionRoleType, native_enum=False, length=24),
        default=CommissionRoleType.CLOSER,
        nullable=False,
    )
    base_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=Decimal("0.00"), nullable=False)
    commission_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=Decimal("0.00"), nullable=False)
    status: Mapped[CommissionStatus] = mapped_column(
        SAEnum(CommissionStatus, native_enum=False, length=24),
        default=CommissionStatus.PENDING,
        nullable=False,
    )
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
