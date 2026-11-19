"""Modelos SQLAlchemy do Bloco 11: Conector Real Evolution API v2 & Log Omnichannel."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
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


class EvolutionChannelConfig(Base):
    __tablename__ = "evolution_channel_configs"
    __table_args__ = (
        UniqueConstraint("tenant_id", name="uq_evolution_channel_config_tenant"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    base_url: Mapped[str] = mapped_column(String(255), default="http://192.168.0.9:8080", nullable=False)
    api_key: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    instance_name: Mapped[str] = mapped_column(String(120), default="comercial-principal", nullable=False)
    default_country_code: Mapped[str] = mapped_column(String(6), default="55", nullable=False)

    is_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    send_pix_copy_paste_separately: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    auto_reply_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    last_connection_state: Mapped[str] = mapped_column(String(40), default="UNKNOWN", nullable=False)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    webhook_configured_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=_utcnow, nullable=False
    )


class OmnichannelMessageLog(Base):
    __tablename__ = "omnichannel_message_logs"
    __table_args__ = (
        Index("ix_omni_msg_tenant_created", "tenant_id", "created_at"),
        Index("ix_omni_msg_tenant_phone", "tenant_id", "phone_number"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    direction: Mapped[str] = mapped_column(String(16), nullable=False)  # OUTBOUND | INBOUND
    channel: Mapped[str] = mapped_column(String(24), default="WHATSAPP", nullable=False)
    instance_name: Mapped[str] = mapped_column(String(120), nullable=False)
    phone_number: Mapped[str] = mapped_column(String(40), nullable=False)
    remote_jid: Mapped[str] = mapped_column(String(120), nullable=False)
    message_text: Mapped[str] = mapped_column(Text, nullable=False)
    provider_message_id: Mapped[str | None] = mapped_column(String(140), nullable=True)
    delivery_status: Mapped[str] = mapped_column(String(32), default="SENT", nullable=False)
    error_detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    related_invoice_number: Mapped[str | None] = mapped_column(String(60), nullable=True)
    raw_payload: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
