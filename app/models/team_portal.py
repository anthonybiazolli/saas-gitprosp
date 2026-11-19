"""Modelos SQLAlchemy do Módulo 8: Equipe Comercial, Metas & Portal Self-Service."""

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
    Integer,
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


class SalesRoleType(str, enum.Enum):
    ADMIN = "ADMIN"
    MANAGER = "MANAGER"
    CLOSER = "CLOSER"
    SDR = "SDR"


class SalesTeamMember(Base):
    __tablename__ = "sales_team_members"
    __table_args__ = (
        UniqueConstraint("tenant_id", "email", name="uq_sales_member_tenant_email"),
        Index("ix_sales_members_tenant_active", "tenant_id", "is_active"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    full_name: Mapped[str] = mapped_column(String(150), nullable=False)
    email: Mapped[str] = mapped_column(String(180), nullable=False)
    phone: Mapped[str | None] = mapped_column(String(40), nullable=True)
    role: Mapped[SalesRoleType] = mapped_column(
        SAEnum(SalesRoleType, native_enum=False, length=24),
        default=SalesRoleType.CLOSER,
        nullable=False,
    )
    monthly_revenue_quota: Mapped[Decimal] = mapped_column(
        Numeric(14, 2), default=Decimal("15000.00"), nullable=False
    )
    monthly_deals_quota: Mapped[int] = mapped_column(Integer, default=5, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=_utcnow, nullable=False
    )


class SalesQuotaPeriod(Base):
    __tablename__ = "sales_quota_periods"
    __table_args__ = (
        UniqueConstraint("tenant_id", "member_id", "period_ym", name="uq_sales_quota_member_period"),
        Index("ix_sales_quotas_tenant_period", "tenant_id", "period_ym"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    member_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sales_team_members.id", ondelete="CASCADE"), nullable=False, index=True
    )
    period_ym: Mapped[str] = mapped_column(String(7), nullable=False)  # Ex: 2026-11
    revenue_target: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=Decimal("0.00"), nullable=False)
    deals_target: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class CustomerPortalToken(Base):
    __tablename__ = "customer_portal_tokens"
    __table_args__ = (
        UniqueConstraint("token", name="uq_customer_portal_token"),
        Index("ix_portal_tokens_tenant_active", "tenant_id", "is_revoked"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    token: Mapped[str] = mapped_column(String(96), nullable=False)

    customer_name: Mapped[str] = mapped_column(String(180), nullable=False)
    customer_email: Mapped[str | None] = mapped_column(String(180), nullable=True)
    customer_document: Mapped[str | None] = mapped_column(String(32), nullable=True)

    contract_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True, index=True)
    invoice_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True, index=True)

    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_accessed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    access_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    is_revoked: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
