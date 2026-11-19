"""Rotas FastAPI do Módulo 8: Equipe Comercial, Metas & Portal Self-Service do Cliente."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query, status
from fastapi.responses import HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import TenantContext, get_tenant_context
from app.db.session import get_db_session
from app.schemas.team_portal import (
    PortalLinkCreateRequest,
    PortalLinkOut,
    PublicContractSignRequest,
    PublicPortalSummaryOut,
    QuotaPeriodOut,
    QuotaUpsertRequest,
    TeamMemberCreate,
    TeamMemberOut,
    TeamMemberUpdate,
    TeamPerformanceOut,
)
from app.services import team_portal_service

router = APIRouter()


@router.get("/members", response_model=list[TeamMemberOut])
async def list_members(
    ctx: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(get_db_session),
) -> list[TeamMemberOut]:
    members = await team_portal_service.list_team_members(session, ctx.tenant_id)
    return [TeamMemberOut.model_validate(m) for m in members]


@router.post("/members", response_model=TeamMemberOut, status_code=status.HTTP_201_CREATED)
async def create_member(
    payload: TeamMemberCreate,
    ctx: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(get_db_session),
) -> TeamMemberOut:
    member = await team_portal_service.create_team_member(session, ctx.tenant_id, payload)
    await session.commit()
    return TeamMemberOut.model_validate(member)


@router.patch("/members/{member_id}", response_model=TeamMemberOut)
async def update_member(
    member_id: uuid.UUID,
    payload: TeamMemberUpdate,
    ctx: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(get_db_session),
) -> TeamMemberOut:
    member = await team_portal_service.update_team_member(session, ctx.tenant_id, member_id, payload)
    await session.commit()
    return TeamMemberOut.model_validate(member)


@router.post("/quotas", response_model=QuotaPeriodOut, status_code=status.HTTP_201_CREATED)
async def upsert_quota(
    payload: QuotaUpsertRequest,
    ctx: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(get_db_session),
) -> QuotaPeriodOut:
    quota = await team_portal_service.upsert_quota_period(session, ctx.tenant_id, payload)
    await session.commit()
    return QuotaPeriodOut.model_validate(quota)


@router.get("/quotas", response_model=list[QuotaPeriodOut])
async def list_quotas(
    period_ym: str | None = Query(default=None),
    ctx: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(get_db_session),
) -> list[QuotaPeriodOut]:
    quotas = await team_portal_service.list_quotas(session, ctx.tenant_id, period_ym=period_ym)
    return [QuotaPeriodOut.model_validate(q) for q in quotas]


@router.get("/leaderboard", response_model=TeamPerformanceOut)
async def get_leaderboard(
    period_ym: str | None = Query(default=None),
    ctx: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(get_db_session),
) -> TeamPerformanceOut:
    return await team_portal_service.get_team_leaderboard(session, ctx.tenant_id, period_ym=period_ym)


@router.post("/portal-links", response_model=PortalLinkOut, status_code=status.HTTP_201_CREATED)
async def create_portal_link(
    payload: PortalLinkCreateRequest,
    ctx: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(get_db_session),
) -> PortalLinkOut:
    link = await team_portal_service.create_portal_link(session, ctx.tenant_id, payload)
    await session.commit()
    return link


@router.get("/portal-links", response_model=list[PortalLinkOut])
async def list_portal_links(
    ctx: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(get_db_session),
) -> list[PortalLinkOut]:
    return await team_portal_service.list_portal_links(session, ctx.tenant_id)


@router.post("/portal-links/{link_id}/revoke", response_model=PortalLinkOut)
async def revoke_portal_link(
    link_id: uuid.UUID,
    ctx: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(get_db_session),
) -> PortalLinkOut:
    link = await team_portal_service.revoke_portal_link(session, ctx.tenant_id, link_id)
    await session.commit()
    return link


# --- Endpoints Públicos Self-Service do Cliente (autenticados via token único) ---


@router.get("/public/{token}", response_model=PublicPortalSummaryOut)
async def public_portal_summary(
    token: str,
    session: AsyncSession = Depends(get_db_session),
) -> PublicPortalSummaryOut:
    summary = await team_portal_service.get_public_portal_summary(session, token)
    await session.commit()
    return summary


@router.post("/public/{token}/sign-contract", response_model=PublicPortalSummaryOut)
async def public_portal_sign_contract(
    token: str,
    payload: PublicContractSignRequest,
    session: AsyncSession = Depends(get_db_session),
) -> PublicPortalSummaryOut:
    summary = await team_portal_service.sign_contract_via_portal(session, token, payload)
    await session.commit()
    return summary


@router.get("/public/{token}/view", response_class=HTMLResponse)
async def public_portal_html_view(
    token: str,
    session: AsyncSession = Depends(get_db_session),
) -> HTMLResponse:
    summary = await team_portal_service.get_public_portal_summary(session, token)
    await session.commit()
    return HTMLResponse(content=team_portal_service.render_public_portal_html(summary))
