from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class WhatsAppInstanceCreate(BaseModel):
    instance_name: str = Field(..., min_length=2, max_length=120, pattern=r"^[a-zA-Z0-9_-]+$")
    display_name: str = Field(..., min_length=2, max_length=160)
    evolution_api_url: str | None = Field(
        None,
        description="URL base da Evolution API (opcional; usa EVOLUTION_API_URL do .env se omitido)",
    )
    evolution_api_key: str | None = Field(
        None,
        description="API Key da Evolution API (opcional; usa EVOLUTION_API_KEY do .env se omitido)",
    )
    webhook_secret: str | None = Field(
        None,
        description="Token opcional validado via query ?secret=... ou header x-webhook-secret",
    )
    default_agent_slug: str | None = Field(None, max_length=80)
    handoff_notify_number: str | None = Field(
        None,
        max_length=40,
        description="WhatsApp do vendedor/closer humano (ex: 5541999999999) para receber alertas de handoff",
    )
    is_primary: bool = True
    is_active: bool = True
    auto_configure_webhook: bool = Field(
        default=False,
        description="Se true, configura automaticamente o webhook na Evolution API ao salvar",
    )
    webhook_public_base_url: str | None = Field(
        None,
        description="URL base acessível pela Evolution API (ex: http://192.168.0.9:8001)",
    )
    metadata_json: dict[str, Any] = Field(default_factory=dict)


class WhatsAppInstanceUpdate(BaseModel):
    display_name: str | None = Field(None, min_length=2, max_length=160)
    evolution_api_url: str | None = None
    evolution_api_key: str | None = None
    webhook_secret: str | None = None
    default_agent_slug: str | None = None
    handoff_notify_number: str | None = None
    is_primary: bool | None = None
    is_active: bool | None = None
    metadata_json: dict[str, Any] | None = None


class WhatsAppInstanceResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    tenant_id: uuid.UUID
    instance_name: str
    display_name: str
    evolution_api_url: str | None
    default_agent_slug: str | None
    handoff_notify_number: str | None
    is_primary: bool
    is_active: bool
    metadata_json: dict[str, Any]
    created_at: datetime
    updated_at: datetime


class WhatsAppSendTestRequest(BaseModel):
    instance_name: str = Field(..., min_length=2, max_length=120)
    number: str = Field(..., min_length=8, max_length=40, description="Número destino (ex: 5541999999999)")
    text: str = Field(..., min_length=1, max_length=4096)


class EvolutionWebhookAck(BaseModel):
    status: str
    event: str | None = None
    instance: str | None = None
    detail: str | None = None
    conversation_id: uuid.UUID | None = None
    ai_responded: bool = False
