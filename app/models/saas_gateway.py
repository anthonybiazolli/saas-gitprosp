"""Modelos SQLAlchemy do Módulo 9: Assinaturas SaaS, Metering e Webhooks Inbound."""

from __future__ import annotations

import enum
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    DateTime,
    Enum as SAEnum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class SaasPlanTier(str, enum.Enum):
    STARTER = "STARTER"
    PRO = "PRO"
    ENTERPRISE = "ENTERPRISE"


class SubscriptionBillingStatus(str, enum.Enum):
    TRIAL = "TRIAL"
    ACTIVE = "ACTIVE"
    PAST_DUE = "PAST_DUE"
    CANCELLED = "CANCELLED"


class TenantSaasSubscription(Base):
    __tablename__ = "tenant_saas_subscriptions"
    __table_args__ = (
        UniqueConstraint("tenant_id", name="uq_tenant_saas_subscription_tenant"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    plan_tier: Mapped[SaasPlanTier] = mapped_column(
        SAEnum(SaasPlanTier, native_enum=False, length=24),
        default=SaasPlanTier.PRO,
        nullable=False,
    )
    billing_status: Mapped[SubscriptionBillingStatus] = mapped_column(
        SAEnum(SubscriptionBillingStatus, native_enum=False, length=24),
        default=SubscriptionBillingStatus.ACTIVE,
        nullable=False,
    )
    monthly_price: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("297.00"), nullable=False)
    max_team_members: Mapped[int] = mapped_column(Integer, default=15, nullable=False)
    max_monthly_dispatches: Mapped[int] = mapped_column(Integer, default=5000, nullable=False)
    max_active_webhooks: Mapped[int] = mapped_column(Integer, default=10, nullable=False)
    inbound_webhook_secret: Mapped[str] = mapped_column(String(120), nullable=False)

    current_period_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=_utcnow, nullable=False
    )


class InboundWebhookReceipt(Base):
    __tablename__ = "inbound_webhook_receipts"
    __table_args__ = (
        Index("ix_inbound_webhooks_tenant_created", "tenant_id", "created_at"),
        Index("ix_inbound_webhooks_provider_ext", "provider", "external_event_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=True, index=True
    )
    provider: Mapped[str] = mapped_column(String(60), nullable=False)
    event_name: Mapped[str] = mapped_column(String(100), nullable=False)
    external_event_id: Mapped[str | None] = mapped_column(String(140), nullable=True)
    processing_status: Mapped[str] = mapped_column(String(30), default="PROCESSED", nullable=False)
    summary: Mapped[str] = mapped_column(String(300), nullable=False)
    raw_payload: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
