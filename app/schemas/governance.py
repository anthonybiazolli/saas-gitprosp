"""Schemas Pydantic v2 do Módulo 10: Governança, Timeline Unificada & Diagnóstico."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class UnifiedTimelineEventOut(BaseModel):
    id: str
    category: str  # BILLING | AUTOMATION | WEBHOOK_INBOUND
    event_type: str
    status: str
    summary: str
    details: dict[str, Any] = Field(default_factory=dict)
    occurred_at: datetime


class ModuleHealthMatrixOut(BaseModel):
    tenant_id: uuid.UUID
    checked_at: datetime
    database_connected: bool
    database_latency_ms: float
    modules_status: dict[str, str]
    counters: dict[str, int]
