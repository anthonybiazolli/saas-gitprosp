"""Schemas Pydantic v2 do Bloco 11: Evolution API v2 & Mensageria WhatsApp."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class EvolutionConfigUpdate(BaseModel):
    base_url: str | None = Field(default=None, min_length=8, max_length=255)
    api_key: str | None = Field(default=None, max_length=255)
    instance_name: str | None = Field(default=None, min_length=2, max_length=120)
    default_country_code: str | None = Field(default=None, min_length=1, max_length=6)
    is_enabled: bool | None = None
    send_pix_copy_paste_separately: bool | None = None
    auto_reply_enabled: bool | None = None


class EvolutionConfigOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    tenant_id: uuid.UUID
    base_url: str
    api_key_masked: str
    instance_name: str
    default_country_code: str
    is_enabled: bool
    send_pix_copy_paste_separately: bool
    auto_reply_enabled: bool
    last_connection_state: str
    last_checked_at: datetime | None = None
    webhook_configured_at: datetime | None = None
    updated_at: datetime


class EvolutionConnectionStatusOut(BaseModel):
    instance_name: str
    base_url: str
    is_enabled: bool
    connection_state: str
    reachable: bool
    raw_response: dict[str, Any] = Field(default_factory=dict)


class EvolutionQrCodeOut(BaseModel):
    instance_name: str
    connection_state: str
    pairing_code: str | None = None
    qr_code_base64: str | None = None
    raw_response: dict[str, Any] = Field(default_factory=dict)


class EvolutionWebhookSetupRequest(BaseModel):
    public_base_url: str = Field(
        default="https://comercial.3.143.227.21.sslip.io",
        min_length=10,
        max_length=255,
    )


class EvolutionTestMessageRequest(BaseModel):
    phone_number: str = Field(..., min_length=8, max_length=32)
    message: str = Field(
        default="Teste de integração Comercial SaaS via Evolution API v2 concluído com sucesso!",
        min_length=1,
        max_length=4000,
    )


class OmnichannelMessageLogOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    tenant_id: uuid.UUID
    direction: str
    channel: str
    instance_name: str
    phone_number: str
    remote_jid: str
    message_text: str
    provider_message_id: str | None = None
    delivery_status: str
    error_detail: str | None = None
    related_invoice_number: str | None = None
    created_at: datetime
