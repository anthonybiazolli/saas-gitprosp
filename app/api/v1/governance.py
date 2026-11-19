"""Rotas FastAPI do Módulo 10: Governança, Timeline Unificada & Exportações CSV."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import TenantContext, get_tenant_context
from app.db.session import get_db_session
from app.schemas.governance import ModuleHealthMatrixOut, UnifiedTimelineEventOut
from app.services import governance_service

router = APIRouter()


@router.get("/exports/invoices.csv")
async def download_invoices_csv(
    ctx: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(get_db_session),
) -> Response:
    csv_content = await governance_service.export_invoices_csv(session, ctx.tenant_id)
    return Response(
        content=csv_content.encode("utf-8"),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="faturas_comercial_saas.csv"'},
    )


@router.get("/exports/commissions.csv")
async def download_commissions_csv(
    ctx: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(get_db_session),
) -> Response:
    csv_content = await governance_service.export_commissions_csv(session, ctx.tenant_id)
    return Response(
        content=csv_content.encode("utf-8"),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="comissoes_comercial_saas.csv"'},
    )


@router.get("/exports/deals.csv")
async def download_deals_csv(
    ctx: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(get_db_session),
) -> Response:
    csv_content = await governance_service.export_deals_csv(session, ctx.tenant_id)
    return Response(
        content=csv_content.encode("utf-8"),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="pipeline_crm_comercial_saas.csv"'},
    )


@router.get("/timeline", response_model=list[UnifiedTimelineEventOut])
async def get_unified_timeline(
    limit: int = Query(default=60, ge=1, le=300),
    ctx: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(get_db_session),
) -> list[UnifiedTimelineEventOut]:
    return await governance_service.get_unified_timeline(session, ctx.tenant_id, limit=limit)


@router.get("/health-matrix", response_model=ModuleHealthMatrixOut)
async def get_health_matrix(
    ctx: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(get_db_session),
) -> ModuleHealthMatrixOut:
    return await governance_service.get_health_matrix(session, ctx.tenant_id)
