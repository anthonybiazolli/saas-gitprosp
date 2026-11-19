"""Rotas FastAPI do Módulo 7: Automação Omnichannel, Fila, Webhooks e Relatório Executivo."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import TenantContext, get_tenant_context
from app.db.session import get_db_session
from app.models.automation_engine import DispatchQueueStatus
from app.schemas.automation_engine import (
    AutomationExecutionLogOut,
    AutomationRuleCreate,
    AutomationRuleOut,
    AutomationRuleUpdate,
    AutomationTriggerRequest,
    DispatchQueueCreate,
    DispatchQueueOut,
    ExecutiveReportOut,
    OverdueDunningScanOut,
    WebhookEndpointCreate,
    WebhookEndpointOut,
    WorkerCycleResultOut,
)
from app.services import automation_service

router = APIRouter()


@router.get("/rules", response_model=list[AutomationRuleOut])
async def list_rules(
    ctx: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(get_db_session),
) -> list[AutomationRuleOut]:
    rules = await automation_service.ensure_default_automation_rules(session, ctx.tenant_id)
    await session.commit()
    return [AutomationRuleOut.model_validate(r) for r in rules]


@router.post("/rules", response_model=AutomationRuleOut, status_code=status.HTTP_201_CREATED)
async def create_rule(
    payload: AutomationRuleCreate,
    ctx: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(get_db_session),
) -> AutomationRuleOut:
    rule = await automation_service.create_rule(session, ctx.tenant_id, payload)
    await session.commit()
    return AutomationRuleOut.model_validate(rule)


@router.patch("/rules/{rule_id}", response_model=AutomationRuleOut)
async def update_rule(
    rule_id: uuid.UUID,
    payload: AutomationRuleUpdate,
    ctx: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(get_db_session),
) -> AutomationRuleOut:
    rule = await automation_service.update_rule(session, ctx.tenant_id, rule_id, payload)
    await session.commit()
    return AutomationRuleOut.model_validate(rule)


@router.post("/events/trigger", response_model=dict)
async def trigger_automation_event(
    payload: AutomationTriggerRequest,
    ctx: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    enqueued, worker_res = await automation_service.trigger_event(session, ctx.tenant_id, payload)
    await session.commit()
    return {
        "enqueued_dispatches": enqueued,
        "worker_cycle": worker_res.model_dump() if worker_res else None,
    }


@router.get("/queue", response_model=list[DispatchQueueOut])
async def list_queue(
    status_filter: DispatchQueueStatus | None = Query(default=None, alias="status"),
    limit: int = Query(default=100, ge=1, le=500),
    ctx: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(get_db_session),
) -> list[DispatchQueueOut]:
    items = await automation_service.list_dispatch_queue(
        session, ctx.tenant_id, status_filter=status_filter, limit=limit
    )
    return [DispatchQueueOut.model_validate(i) for i in items]


@router.post("/queue", response_model=DispatchQueueOut, status_code=status.HTTP_201_CREATED)
async def enqueue_manual_message(
    payload: DispatchQueueCreate,
    ctx: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(get_db_session),
) -> DispatchQueueOut:
    item = await automation_service.enqueue_dispatch(session, ctx.tenant_id, payload)
    await session.commit()
    return DispatchQueueOut.model_validate(item)


@router.post("/worker/run-cycle", response_model=WorkerCycleResultOut)
async def run_worker_cycle(
    batch_size: int = Query(default=50, ge=1, le=500),
    ctx: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(get_db_session),
) -> WorkerCycleResultOut:
    res = await automation_service.process_dispatch_queue(session, tenant_id=ctx.tenant_id, batch_size=batch_size)
    await session.commit()
    return res


@router.post("/dunning/scan-overdue", response_model=OverdueDunningScanOut)
async def scan_overdue_invoices(
    ctx: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(get_db_session),
) -> OverdueDunningScanOut:
    res = await automation_service.scan_and_enqueue_overdue_invoices(session, ctx.tenant_id)
    await session.commit()
    return res


@router.get("/webhooks", response_model=list[WebhookEndpointOut])
async def list_webhooks(
    ctx: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(get_db_session),
) -> list[WebhookEndpointOut]:
    eps = await automation_service.list_webhook_endpoints(session, ctx.tenant_id)
    return [WebhookEndpointOut.model_validate(e) for e in eps]


@router.post("/webhooks", response_model=WebhookEndpointOut, status_code=status.HTTP_201_CREATED)
async def create_webhook(
    payload: WebhookEndpointCreate,
    ctx: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(get_db_session),
) -> WebhookEndpointOut:
    ep = await automation_service.create_webhook_endpoint(session, ctx.tenant_id, payload)
    await session.commit()
    return WebhookEndpointOut.model_validate(ep)


@router.get("/logs", response_model=list[AutomationExecutionLogOut])
async def list_logs(
    limit: int = Query(default=100, ge=1, le=500),
    ctx: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(get_db_session),
) -> list[AutomationExecutionLogOut]:
    logs = await automation_service.list_execution_logs(session, ctx.tenant_id, limit=limit)
    return [AutomationExecutionLogOut.model_validate(l) for l in logs]


@router.get("/executive-report", response_model=ExecutiveReportOut)
async def get_executive_report(
    ctx: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(get_db_session),
) -> ExecutiveReportOut:
    report = await automation_service.get_executive_report(session, ctx.tenant_id)
    await session.commit()
    return report
