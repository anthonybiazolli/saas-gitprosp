"""Modelos SQLAlchemy do Módulo CRM Pipeline, Deals, Atividades e Contratos."""

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
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class DealStatus(str, enum.Enum):
    OPEN = "OPEN"
    WON = "WON"
    LOST = "LOST"


class DealActivityType(str, enum.Enum):
    CREATED = "CREATED"
    STAGE_CHANGED = "STAGE_CHANGED"
    NOTE = "NOTE"
    CALL = "CALL"
    MEETING = "MEETING"
    PROPOSAL_LINKED = "PROPOSAL_LINKED"
    CONTRACT_GENERATED = "CONTRACT_GENERATED"
    WON = "WON"
    LOST = "LOST"


class ContractStatus(str, enum.Enum):
    DRAFT = "DRAFT"
    SENT_FOR_SIGNATURE = "SENT_FOR_SIGNATURE"
    SIGNED = "SIGNED"
    ACTIVE = "ACTIVE"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"


class CrmPipeline(Base):
    __tablename__ = "crm_pipelines"
    __table_args__ = (
        Index("ix_crm_pipelines_tenant_default", "tenant_id", "is_default"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_default: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=_utcnow, nullable=False
    )

    stages: Mapped[list["app.models.crm_deals.CrmPipelineStage"]] = relationship(
        "app.models.crm_deals.CrmPipelineStage",
        back_populates="pipeline",
        cascade="all, delete-orphan",
        order_by="app.models.crm_deals.CrmPipelineStage.stage_order.asc()",
    )
    deals: Mapped[list["app.models.crm_deals.CrmDeal"]] = relationship(
        "app.models.crm_deals.CrmDeal",
        back_populates="pipeline",
    )


class CrmPipelineStage(Base):
    __tablename__ = "crm_pipeline_stages"
    __table_args__ = (
        UniqueConstraint("pipeline_id", "stage_order", name="uq_crm_stage_pipeline_order"),
        Index("ix_crm_stages_tenant_pipeline", "tenant_id", "pipeline_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    pipeline_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("crm_pipelines.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    stage_order: Mapped[int] = mapped_column(Integer, nullable=False)
    default_probability: Mapped[int] = mapped_column(Integer, default=10, nullable=False)
    color: Mapped[str] = mapped_column(String(20), default="#6366f1", nullable=False)
    is_won: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_lost: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    pipeline: Mapped["app.models.crm_deals.CrmPipeline"] = relationship(
        "app.models.crm_deals.CrmPipeline",
        back_populates="stages",
    )
    deals: Mapped[list["app.models.crm_deals.CrmDeal"]] = relationship(
        "app.models.crm_deals.CrmDeal",
        back_populates="stage",
    )


class CrmDeal(Base):
    __tablename__ = "crm_deals"
    __table_args__ = (
        Index("ix_crm_deals_tenant_status", "tenant_id", "status"),
        Index("ix_crm_deals_tenant_stage", "tenant_id", "stage_id"),
        Index("ix_crm_deals_tenant_created", "tenant_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    pipeline_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("crm_pipelines.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    stage_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("crm_pipeline_stages.id", ondelete="RESTRICT"), nullable=False, index=True
    )

    title: Mapped[str] = mapped_column(String(200), nullable=False)
    company_name: Mapped[str | None] = mapped_column(String(180), nullable=True)
    contact_name: Mapped[str] = mapped_column(String(150), nullable=False)
    contact_email: Mapped[str | None] = mapped_column(String(180), nullable=True)
    contact_phone: Mapped[str | None] = mapped_column(String(40), nullable=True)
    contact_document: Mapped[str | None] = mapped_column(String(32), nullable=True)

    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=Decimal("0.00"), nullable=False)
    currency: Mapped[str] = mapped_column(String(8), default="BRL", nullable=False)
    probability: Mapped[int] = mapped_column(Integer, default=10, nullable=False)
    status: Mapped[DealStatus] = mapped_column(
        SAEnum(DealStatus, native_enum=False, length=20), default=DealStatus.OPEN, nullable=False
    )

    source: Mapped[str] = mapped_column(String(50), default="manual", nullable=False)
    owner_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    expected_close_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    loss_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Referências desacopladas para os demais módulos do SaaS
    lead_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True, index=True)
    proposal_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True, index=True)
    conversation_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True, index=True)

    tags: Mapped[list[str]] = mapped_column(JSONB, default=list, nullable=False)
    custom_fields: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=_utcnow, nullable=False
    )

    pipeline: Mapped["app.models.crm_deals.CrmPipeline"] = relationship(
        "app.models.crm_deals.CrmPipeline",
        back_populates="deals",
    )
    stage: Mapped["app.models.crm_deals.CrmPipelineStage"] = relationship(
        "app.models.crm_deals.CrmPipelineStage",
        back_populates="deals",
    )
    activities: Mapped[list["app.models.crm_deals.CrmDealActivity"]] = relationship(
        "app.models.crm_deals.CrmDealActivity",
        back_populates="deal",
        cascade="all, delete-orphan",
        order_by="app.models.crm_deals.CrmDealActivity.created_at.desc()",
    )
    contracts: Mapped[list["app.models.crm_deals.CrmContract"]] = relationship(
        "app.models.crm_deals.CrmContract",
        back_populates="deal",
    )


class CrmDealActivity(Base):
    __tablename__ = "crm_deal_activities"
    __table_args__ = (
        Index("ix_crm_activities_tenant_deal", "tenant_id", "deal_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    deal_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("crm_deals.id", ondelete="CASCADE"), nullable=False, index=True
    )
    activity_type: Mapped[DealActivityType] = mapped_column(
        SAEnum(DealActivityType, native_enum=False, length=32),
        default=DealActivityType.NOTE,
        nullable=False,
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    actor_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    deal: Mapped["app.models.crm_deals.CrmDeal"] = relationship(
        "app.models.crm_deals.CrmDeal",
        back_populates="activities",
    )


class CrmContract(Base):
    __tablename__ = "crm_contracts"
    __table_args__ = (
        UniqueConstraint("tenant_id", "contract_number", name="uq_crm_contract_tenant_number"),
        Index("ix_crm_contracts_tenant_status", "tenant_id", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    deal_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("crm_deals.id", ondelete="SET NULL"), nullable=True, index=True
    )
    proposal_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True, index=True)

    contract_number: Mapped[str] = mapped_column(String(40), nullable=False)
    title: Mapped[str] = mapped_column(String(220), nullable=False)
    customer_name: Mapped[str] = mapped_column(String(180), nullable=False)
    customer_email: Mapped[str | None] = mapped_column(String(180), nullable=True)
    customer_document: Mapped[str | None] = mapped_column(String(32), nullable=True)

    total_value: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=Decimal("0.00"), nullable=False)
    recurring_monthly_value: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=Decimal("0.00"), nullable=False)
    billing_cycle: Mapped[str] = mapped_column(String(30), default="monthly", nullable=False)
    currency: Mapped[str] = mapped_column(String(8), default="BRL", nullable=False)

    status: Mapped[ContractStatus] = mapped_column(
        SAEnum(ContractStatus, native_enum=False, length=32),
        default=ContractStatus.DRAFT,
        nullable=False,
    )
    start_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    end_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    signed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    signed_by_name: Mapped[str | None] = mapped_column(String(150), nullable=True)
    signed_by_document: Mapped[str | None] = mapped_column(String(32), nullable=True)

    terms_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=_utcnow, nullable=False
    )

    deal: Mapped["app.models.crm_deals.CrmDeal | None"] = relationship(
        "app.models.crm_deals.CrmDeal",
        back_populates="contracts",
    )
