"""Rotas FastAPI do Bloco 12: Motor de Cadências Ativas de Prospecção (D+0, D+2, D+5)."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import TenantContext, get_tenant_context
from app.db.session import get_db_session
from app.schemas.outbound_cadences import (
    CadenceBulkEnrollRequest,
    CadenceConvertDealRequest,
    CadenceCreate,
    CadenceEnrollmentOut,
    CadenceEnrollRequest,
    CadenceExecutionLogOut,
    CadenceMetricsOut,
    CadenceOut,
    CadenceProcessTickOut,
)
from app.services import outbound_cadence_service

router = APIRouter()


@router.get("", response_model=list[CadenceOut])
async def list_cadences(
    ctx: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(get_db_session),
) -> list[CadenceOut]:
    rows = await outbound_cadence_service.list_cadences(session, ctx.tenant_id)
    return [CadenceOut.model_validate(r) for r in rows]


@router.post("", response_model=CadenceOut, status_code=status.HTTP_201_CREATED)
async def create_cadence(
    payload: CadenceCreate,
    ctx: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(get_db_session),
) -> CadenceOut:
    row = await outbound_cadence_service.create_cadence(session, ctx.tenant_id, payload)
    await session.commit()
    return CadenceOut.model_validate(row)


@router.post("/bootstrap-default", response_model=CadenceOut, status_code=status.HTTP_201_CREATED)
async def bootstrap_default_cadence(
    ctx: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(get_db_session),
) -> CadenceOut:
    row = await outbound_cadence_service.bootstrap_default_d0_d2_d5_cadence(session, ctx.tenant_id)
    await session.commit()
    return CadenceOut.model_validate(row)


@router.patch("/{cadence_id}/toggle", response_model=CadenceOut)
async def toggle_cadence(
    cadence_id: uuid.UUID,
    is_active: bool = Query(...),
    ctx: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(get_db_session),
) -> CadenceOut:
    row = await outbound_cadence_service.toggle_cadence(session, ctx.tenant_id, cadence_id, is_active)
    await session.commit()
    return CadenceOut.model_validate(row)


@router.post("/{cadence_id}/enroll", response_model=CadenceEnrollmentOut, status_code=status.HTTP_201_CREATED)
async def enroll_prospect(
    cadence_id: uuid.UUID,
    payload: CadenceEnrollRequest,
    ctx: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(get_db_session),
) -> CadenceEnrollmentOut:
    enr = await outbound_cadence_service.enroll_prospect(session, ctx.tenant_id, cadence_id, payload)
    await session.commit()
    return CadenceEnrollmentOut.model_validate(enr)


@router.post("/{cadence_id}/enroll-bulk", response_model=list[CadenceEnrollmentOut], status_code=status.HTTP_201_CREATED)
async def enroll_prospects_bulk(
    cadence_id: uuid.UUID,
    payload: CadenceBulkEnrollRequest,
    ctx: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(get_db_session),
) -> list[CadenceEnrollmentOut]:
    results = []
    for item in payload.prospects:
        enr = await outbound_cadence_service.enroll_prospect(session, ctx.tenant_id, cadence_id, item)
        results.append(CadenceEnrollmentOut.model_validate(enr))
    await session.commit()
    return results


@router.get("/enrollments", response_model=list[CadenceEnrollmentOut])
async def list_enrollments(
    cadence_id: uuid.UUID | None = Query(default=None),
    limit: int = Query(default=80, ge=1, le=300),
    ctx: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(get_db_session),
) -> list[CadenceEnrollmentOut]:
    rows = await outbound_cadence_service.list_enrollments(session, ctx.tenant_id, cadence_id=cadence_id, limit=limit)
    return [CadenceEnrollmentOut.model_validate(r) for r in rows]


@router.post("/enrollments/{enrollment_id}/mark-replied", response_model=CadenceEnrollmentOut)
async def mark_enrollment_replied(
    enrollment_id: uuid.UUID,
    ctx: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(get_db_session),
) -> CadenceEnrollmentOut:
    enr = await outbound_cadence_service.mark_enrollment_replied(session, ctx.tenant_id, enrollment_id)
    await session.commit()
    return CadenceEnrollmentOut.model_validate(enr)


@router.post("/enrollments/{enrollment_id}/convert-deal")
async def convert_enrollment_to_deal(
    enrollment_id: uuid.UUID,
    payload: CadenceConvertDealRequest = CadenceConvertDealRequest(),
    ctx: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    res = await outbound_cadence_service.convert_enrollment_to_crm_deal(
        session, ctx.tenant_id, enrollment_id, payload
    )
    await session.commit()
    return res


@router.post("/process-due", response_model=CadenceProcessTickOut)
async def process_due_steps(
    batch_size: int = Query(default=50, ge=1, le=200),
    ctx: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(get_db_session),
) -> CadenceProcessTickOut:
    res = await outbound_cadence_service.process_due_cadence_steps(
        session, tenant_id=ctx.tenant_id, batch_size=batch_size
    )
    await session.commit()
    return res


@router.get("/logs", response_model=list[CadenceExecutionLogOut])
async def list_execution_logs(
    limit: int = Query(default=50, ge=1, le=200),
    ctx: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(get_db_session),
) -> list[CadenceExecutionLogOut]:
    rows = await outbound_cadence_service.list_execution_logs(session, ctx.tenant_id, limit=limit)
    return [CadenceExecutionLogOut.model_validate(r) for r in rows]


@router.get("/metrics", response_model=CadenceMetricsOut)
async def get_cadence_metrics(
    ctx: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(get_db_session),
) -> CadenceMetricsOut:
    return await outbound_cadence_service.get_cadence_metrics(session, ctx.tenant_id)
