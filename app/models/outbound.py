from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum as SAEnum,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class CampaignStatus(str, enum.Enum):
    DRAFT = "draft"
    ACTIVE = "active"
    PAUSED = "paused"
    COMPLETED = "completed"


class OutboundLeadStatus(str, enum.Enum):
    NEW = "new"
    ENRICHED = "enriched"
    CONTACTED = "contacted"
    REPLIED = "replied"
    CONVERTED = "converted"
    DISQUALIFIED = "disqualified"


class EnrollmentStatus(str, enum.Enum):
    ACTIVE = "active"
    PAUSED = "paused"
    COMPLETED = "completed"
    STOPPED_REPLIED = "stopped_replied"
    STOPPED_HANDOFF = "stopped_handoff"


class ProspectingCampaign(Base):
    __tablename__ = "prospecting_campaigns"
    __table_args__ = (
        UniqueConstraint("tenant_id", "slug", name="uq_prospecting_campaigns_tenant_slug"),
        {"extend_existing": True},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    slug: Mapped[str] = mapped_column(String(100), nullable=False)
    icp_description: Mapped[str] = mapped_column(Text, nullable=False)
    value_proposition: Mapped[str | None] = mapped_column(Text, nullable=True)
    default_channel: Mapped[str] = mapped_column(String(30), nullable=False, default="whatsapp")
    status: Mapped[CampaignStatus] = mapped_column(
        SAEnum(CampaignStatus, name="prospecting_campaign_status_enum", create_type=True),
        nullable=False,
        default=CampaignStatus.ACTIVE,
    )
    auto_send_first_touch: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class OutboundLead(Base):
    __tablename__ = "outbound_leads"
    __table_args__ = ({"extend_existing": True},)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    campaign_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("prospecting_campaigns.id", ondelete="SET NULL"), nullable=True, index=True
    )
    company_name: Mapped[str] = mapped_column(String(200), nullable=False)
    cnpj: Mapped[str | None] = mapped_column(String(32), nullable=True)
    website: Mapped[str | None] = mapped_column(String(255), nullable=True)
    sector: Mapped[str | None] = mapped_column(String(120), nullable=True)
    city: Mapped[str | None] = mapped_column(String(120), nullable=True)
    state_uf: Mapped[str | None] = mapped_column(String(4), nullable=True)

    contact_name: Mapped[str | None] = mapped_column(String(160), nullable=True)
    job_title: Mapped[str | None] = mapped_column(String(120), nullable=True)
    email: Mapped[str | None] = mapped_column(String(160), nullable=True)
    whatsapp_e164: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    source_channel: Mapped[str] = mapped_column(String(50), nullable=False, default="outbound_import")

    status: Mapped[OutboundLeadStatus] = mapped_column(
        SAEnum(OutboundLeadStatus, name="outbound_lead_status_enum", create_type=True),
        nullable=False,
        default=OutboundLeadStatus.NEW,
    )
    icp_score: Mapped[int] = mapped_column(Integer, nullable=False, default=50)
    enrichment_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    generated_opening_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    promoted_company_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.id", ondelete="SET NULL"), nullable=True
    )
    promoted_contact_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("contacts.id", ondelete="SET NULL"), nullable=True
    )
    promoted_deal_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("deals.id", ondelete="SET NULL"), nullable=True
    )
    conversation_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("ai_conversations.id", ondelete="SET NULL"), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class CadenceSequence(Base):
    __tablename__ = "cadence_sequences"
    __table_args__ = (
        UniqueConstraint("tenant_id", "slug", name="uq_cadence_sequences_tenant_slug"),
        {"extend_existing": True},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    slug: Mapped[str] = mapped_column(String(100), nullable=False)
    channel: Mapped[str] = mapped_column(String(30), nullable=False, default="whatsapp")
    stop_on_reply: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    steps: Mapped[list[CadenceStep]] = relationship(
        "app.models.outbound.CadenceStep",
        back_populates="sequence",
        cascade="all, delete-orphan",
        order_by="app.models.outbound.CadenceStep.step_order.asc()",
    )


class CadenceStep(Base):
    __tablename__ = "cadence_steps"
    __table_args__ = ({"extend_existing": True},)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    sequence_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cadence_sequences.id", ondelete="CASCADE"), nullable=False, index=True
    )
    step_order: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    delay_minutes_from_prev: Mapped[int] = mapped_column(Integer, nullable=False, default=60)
    channel: Mapped[str] = mapped_column(String(30), nullable=False, default="whatsapp")
    objective: Mapped[str] = mapped_column(Text, nullable=False)
    message_template: Mapped[str | None] = mapped_column(Text, nullable=True)

    sequence: Mapped[CadenceSequence] = relationship("CadenceSequence", back_populates="steps")


class CadenceEnrollment(Base):
    __tablename__ = "cadence_enrollments"
    __table_args__ = ({"extend_existing": True},)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    sequence_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cadence_sequences.id", ondelete="CASCADE"), nullable=False, index=True
    )
    conversation_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("ai_conversations.id", ondelete="SET NULL"), nullable=True
    )
    company_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.id", ondelete="SET NULL"), nullable=True
    )
    contact_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("contacts.id", ondelete="SET NULL"), nullable=True
    )
    deal_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("deals.id", ondelete="SET NULL"), nullable=True
    )
    target_identifier: Mapped[str] = mapped_column(String(160), nullable=False)
    channel: Mapped[str] = mapped_column(String(30), nullable=False, default="whatsapp")

    current_step_order: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    status: Mapped[EnrollmentStatus] = mapped_column(
        SAEnum(EnrollmentStatus, name="cadence_enrollment_status_enum", create_type=True),
        nullable=False,
        default=EnrollmentStatus.ACTIVE,
    )
    next_run_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    enrolled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_executed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_message_sent: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
