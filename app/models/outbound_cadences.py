"""Modelos SQLAlchemy do Bloco 12: Motor de Cadências Ativas de Prospecção (D+0, D+2, D+5)."""

from __future__ import annotations

import enum
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum as SAEnum,
    ForeignKey,
    Index,
    Integer,
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


class CadenceChannel(str, enum.Enum):
    WHATSAPP = "WHATSAPP"
    EMAIL = "EMAIL"


class EnrollmentStatus(str, enum.Enum):
    ACTIVE = "ACTIVE"
    PAUSED = "PAUSED"
    COMPLETED = "COMPLETED"
    REPLIED = "REPLIED"
    CONVERTED = "CONVERTED"
    OPTED_OUT = "OPTED_OUT"


class ProspectCadence(Base):
    __tablename__ = "prospect_cadences"
    __table_args__ = (
        Index("ix_prospect_cadences_tenant_active", "tenant_id", "is_active"),
        {"extend_existing": True},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    target_persona: Mapped[str] = mapped_column(String(180), default="Decisor B2B / Diretor Comercial", nullable=False)
    value_proposition: Mapped[str] = mapped_column(
        Text,
        default="Automação comercial omnichannel com SDR IA, propostas e cobrança PIX integrada.",
        nullable=False,
    )
    tone_of_voice: Mapped[str] = mapped_column(String(80), default="consultivo, direto e executivo", nullable=False)

    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    ai_personalization_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    stop_on_reply: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    stop_on_deal_won: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=_utcnow, nullable=False
    )

    steps: Mapped[list["ProspectCadenceStep"]] = relationship(
        "ProspectCadenceStep",
        back_populates="cadence",
        cascade="all, delete-orphan",
        order_by="ProspectCadenceStep.step_order",
        lazy="selectin",
    )


class ProspectCadenceStep(Base):
    __tablename__ = "prospect_cadence_steps"
    __table_args__ = (
        UniqueConstraint("cadence_id", "step_order", name="uq_prospect_cadence_step_order"),
        Index("ix_prospect_cadence_steps_tenant_cadence", "tenant_id", "cadence_id"),
        {"extend_existing": True},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    cadence_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("prospect_cadences.id", ondelete="CASCADE"), nullable=False, index=True
    )
    step_order: Mapped[int] = mapped_column(Integer, nullable=False)
    day_offset: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    channel: Mapped[CadenceChannel] = mapped_column(
        SAEnum(CadenceChannel, name="prospect_cadence_channel_enum", native_enum=False),
        default=CadenceChannel.WHATSAPP,
        nullable=False,
    )
    subject_template: Mapped[str] = mapped_column(String(240), default="", nullable=False)
    body_template: Mapped[str] = mapped_column(Text, nullable=False)
    ai_prompt_instruction: Mapped[str] = mapped_column(Text, default="", nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    cadence: Mapped["ProspectCadence"] = relationship("ProspectCadence", back_populates="steps")


class CadenceEnrollment(Base):
    __tablename__ = "prospect_cadence_enrollments"
    __table_args__ = (
        Index("ix_prospect_cadence_enrollments_due", "status", "next_run_at"),
        Index("ix_prospect_cadence_enrollments_tenant_status", "tenant_id", "status"),
        {"extend_existing": True},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    cadence_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("prospect_cadences.id", ondelete="CASCADE"), nullable=False, index=True
    )

    lead_name: Mapped[str] = mapped_column(String(160), nullable=False)
    company_name: Mapped[str] = mapped_column(String(180), default="", nullable=False)
    email: Mapped[str] = mapped_column(String(180), default="", nullable=False)
    phone: Mapped[str] = mapped_column(String(40), default="", nullable=False)
    role_title: Mapped[str] = mapped_column(String(120), default="", nullable=False)
    segment: Mapped[str] = mapped_column(String(120), default="", nullable=False)
    context_notes: Mapped[str] = mapped_column(Text, default="", nullable=False)

    status: Mapped[EnrollmentStatus] = mapped_column(
        SAEnum(EnrollmentStatus, name="prospect_cadence_enrollment_status_enum", native_enum=False),
        default=EnrollmentStatus.ACTIVE,
        nullable=False,
    )
    current_step_order: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    enrolled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    last_dispatched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    replied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    converted_deal_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)


class CadenceExecutionLog(Base):
    __tablename__ = "prospect_cadence_execution_logs"
    __table_args__ = (
        Index("ix_prospect_cadence_exec_logs_tenant_exec", "tenant_id", "executed_at"),
        {"extend_existing": True},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    cadence_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("prospect_cadences.id", ondelete="CASCADE"), nullable=False, index=True
    )
    enrollment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("prospect_cadence_enrollments.id", ondelete="CASCADE"), nullable=False, index=True
    )
    step_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    step_order: Mapped[int] = mapped_column(Integer, nullable=False)
    day_offset: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    channel: Mapped[str] = mapped_column(String(24), nullable=False)
    destination: Mapped[str] = mapped_column(String(180), nullable=False)
    rendered_subject: Mapped[str] = mapped_column(String(240), default="", nullable=False)
    rendered_body: Mapped[str] = mapped_column(Text, nullable=False)
    ai_generated: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    dispatch_status: Mapped[str] = mapped_column(String(32), default="ENQUEUED", nullable=False)
    dispatch_queue_id: Mapped[str | None] = mapped_column(String(80), nullable=True)

    executed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
