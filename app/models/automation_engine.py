"""Modelos SQLAlchemy do Módulo 7: Automação Omnichannel, Fila de Disparos e Webhooks."""

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
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class AutomationTriggerType(str, enum.Enum):
    DEAL_STAGE_CHANGED = "DEAL_STAGE_CHANGED"
    CONTRACT_GENERATED = "CONTRACT_GENERATED"
    INVOICE_CREATED = "INVOICE_CREATED"
    INVOICE_OVERDUE = "INVOICE_OVERDUE"
    INVOICE_PAID = "INVOICE_PAID"
    CADENCE_STEP = "CADENCE_STEP"


class DispatchChannelType(str, enum.Enum):
    WHATSAPP = "WHATSAPP"
    EMAIL = "EMAIL"
    WEBHOOK = "WEBHOOK"


class DispatchQueueStatus(str, enum.Enum):
    QUEUED = "QUEUED"
    PROCESSING = "PROCESSING"
    SENT = "SENT"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class AutomationRule(Base):
    __tablename__ = "automation_rules"
    __table_args__ = (
        Index("ix_automation_rules_tenant_trigger", "tenant_id", "trigger_type", "is_active"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(150), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    trigger_type: Mapped[AutomationTriggerType] = mapped_column(
        SAEnum(AutomationTriggerType, native_enum=False, length=40),
        nullable=False,
    )
    channel: Mapped[DispatchChannelType] = mapped_column(
        SAEnum(DispatchChannelType, native_enum=False, length=24),
        default=DispatchChannelType.WHATSAPP,
        nullable=False,
    )
    delay_minutes: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    message_template: Mapped[str] = mapped_column(Text, nullable=False)
    conditions_json: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=_utcnow, nullable=False
    )


class OutboundDispatchQueue(Base):
    __tablename__ = "outbound_dispatch_queue"
    __table_args__ = (
        Index("ix_dispatch_queue_status_sched", "status", "scheduled_for"),
        Index("ix_dispatch_queue_tenant_status", "tenant_id", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    rule_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("automation_rules.id", ondelete="SET NULL"), nullable=True
    )

    channel: Mapped[DispatchChannelType] = mapped_column(
        SAEnum(DispatchChannelType, native_enum=False, length=24),
        default=DispatchChannelType.WHATSAPP,
        nullable=False,
    )
    destination: Mapped[str] = mapped_column(String(255), nullable=False)
    subject: Mapped[str | None] = mapped_column(String(220), nullable=True)
    rendered_message: Mapped[str] = mapped_column(Text, nullable=False)

    status: Mapped[DispatchQueueStatus] = mapped_column(
        SAEnum(DispatchQueueStatus, native_enum=False, length=24),
        default=DispatchQueueStatus.QUEUED,
        nullable=False,
    )
    scheduled_for: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3, nullable=False)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    provider_receipt: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    context_json: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=_utcnow, nullable=False
    )


class WebhookEndpointConfig(Base):
    __tablename__ = "webhook_endpoint_configs"
    __table_args__ = (
        Index("ix_webhook_endpoints_tenant_active", "tenant_id", "is_active"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    target_url: Mapped[str] = mapped_column(String(500), nullable=False)
    secret_token: Mapped[str] = mapped_column(String(120), nullable=False)
    subscribed_events: Mapped[list[str]] = mapped_column(JSONB, default=list, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    last_status_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_triggered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class AutomationExecutionLog(Base):
    __tablename__ = "automation_execution_logs"
    __table_args__ = (
        Index("ix_automation_logs_tenant_created", "tenant_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    rule_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    event_type: Mapped[str] = mapped_column(String(60), nullable=False)
    status: Mapped[str] = mapped_column(String(30), default="SUCCESS", nullable=False)
    summary: Mapped[str] = mapped_column(String(300), nullable=False)
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
