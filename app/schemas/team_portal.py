"""Schemas Pydantic v2 para Equipe Comercial, Metas & Portal Self-Service do Cliente."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.models.team_portal import SalesRoleType


class TeamMemberCreate(BaseModel):
    full_name: str = Field(..., min_length=2, max_length=150)
    email: str = Field(..., min_length=5, max_length=180)
    phone: str | None = Field(default=None, max_length=40)
    role: SalesRoleType = SalesRoleType.CLOSER
    monthly_revenue_quota: Decimal = Field(default=Decimal("15000.00"), ge=0)
    monthly_deals_quota: int = Field(default=5, ge=0)
    is_active: bool = True


class TeamMemberUpdate(BaseModel):
    full_name: str | None = Field(default=None, min_length=2, max_length=150)
    phone: str | None = Field(default=None, max_length=40)
    role: SalesRoleType | None = None
    monthly_revenue_quota: Decimal | None = Field(default=None, ge=0)
    monthly_deals_quota: int | None = Field(default=None, ge=0)
    is_active: bool | None = None


class TeamMemberOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    tenant_id: uuid.UUID
    full_name: str
    email: str
    phone: str | None = None
    role: SalesRoleType
    monthly_revenue_quota: Decimal
    monthly_deals_quota: int
    is_active: bool
    created_at: datetime
    updated_at: datetime


class QuotaUpsertRequest(BaseModel):
    member_id: uuid.UUID
    period_ym: str = Field(..., pattern=r"^\d{4}-(0[1-9]|1[0-2])$")
    revenue_target: Decimal = Field(..., ge=0)
    deals_target: int = Field(default=0, ge=0)
    notes: str | None = None


class QuotaPeriodOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    tenant_id: uuid.UUID
    member_id: uuid.UUID
    period_ym: str
    revenue_target: Decimal
    deals_target: int
    notes: str | None = None
    created_at: datetime


class LeaderboardEntryOut(BaseModel):
    member_id: uuid.UUID
    full_name: str
    role: SalesRoleType
    revenue_target: Decimal
    revenue_achieved: Decimal
    revenue_attainment_percent: float
    deals_target: int
    deals_won: int
    open_pipeline_value: Decimal
    commissions_earned: Decimal


class TeamPerformanceOut(BaseModel):
    period_ym: str
    total_revenue_target: Decimal
    total_revenue_achieved: Decimal
    team_attainment_percent: float
    leaderboard: list[LeaderboardEntryOut] = Field(default_factory=list)


class PortalLinkCreateRequest(BaseModel):
    customer_name: str = Field(..., min_length=2, max_length=180)
    customer_email: str | None = Field(default=None, max_length=180)
    customer_document: str | None = Field(default=None, max_length=32)
    contract_id: uuid.UUID | None = None
    invoice_id: uuid.UUID | None = None
    expires_in_days: int = Field(default=15, ge=1, le=365)


class PortalLinkOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    tenant_id: uuid.UUID
    token: str
    public_json_url: str
    public_page_url: str
    customer_name: str
    customer_email: str | None = None
    customer_document: str | None = None
    contract_id: uuid.UUID | None = None
    invoice_id: uuid.UUID | None = None
    expires_at: datetime
    last_accessed_at: datetime | None = None
    access_count: int
    is_revoked: bool
    created_at: datetime


class PublicPortalSummaryOut(BaseModel):
    token: str
    customer_name: str
    customer_email: str | None = None
    customer_document: str | None = None
    expires_at: datetime
    contract: dict[str, Any] | None = None
    invoice: dict[str, Any] | None = None


class PublicContractSignRequest(BaseModel):
    signed_by_name: str = Field(..., min_length=2, max_length=150)
    signed_by_document: str = Field(..., min_length=5, max_length=32)
    accepted_terms: bool = True
