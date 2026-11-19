"""Rotas FastAPI do Módulo Pipeline CRM, Deals, Contratos e Analytics."""

from __future__ import annotations

import inspect
import uuid
from typing import Any

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import TenantContext, get_tenant_context, write_audit_log
from app.db.session import get_db_session
from app.models.crm_deals import ContractStatus, DealStatus
from app.schemas.crm_deals import (
    ContractCreate,
    ContractOut,
    ContractSignRequest,
    CrmAnalyticsOut,
    DealActivityCreate,
    DealActivityOut,
    DealCreate,
    DealMoveRequest,
    DealMoveResponse,
    DealOut,
    DealUpdate,
    KanbanBoardOut,
    PipelineOut,
)
from app.services import crm_deals_service

router = APIRouter()


async def _record_audit(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    action: str,
    entity_type: str,
    entity_id: str,
    details: dict[str, Any] | None = None,
) -> None:
    """Compatibiliza chamadas a write_audit_log independentemente da assinatura."""
    try:
        sig = inspect.signature(write_audit_log)
        kwargs: dict[str, Any] = {}
        for param_name in sig.parameters:
            if param_name in ("session", "db"):
                kwargs[param_name] = session
            elif param_name == "tenant_id":
                kwargs[param_name] = tenant_id
            elif param_name == "action":
                kwargs[param_name] = action
            elif param_name in ("entity_type", "resource_type", "entity"):
                kwargs[param_name] = entity_type
            elif param_name in ("entity_id", "resource_id"):
                kwargs[param_name] = entity_id
            elif param_name in ("details", "metadata", "payload"):
                kwargs[param_name] = details or {}
        res = write_audit_log(**kwargs)
        if inspect.isawaitable(res):
            await res
    except Exception:
        pass


@router.get("/pipeline", response_model=PipelineOut)
async def get_pipeline(
    ctx: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(get_db_session),
) -> PipelineOut:
    pipeline = await crm_deals_service.ensure_default_pipeline(session, ctx.tenant_id)
    await session.commit()
    return PipelineOut.model_validate(pipeline)


@router.get("/board", response_model=KanbanBoardOut)
async def get_kanban_board(
    ctx: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(get_db_session),
) -> KanbanBoardOut:
    board = await crm_deals_service.get_kanban_board(session, ctx.tenant_id)
    await session.commit()
    return board


@router.post("/deals", response_model=DealOut, status_code=status.HTTP_201_CREATED)
async def create_deal(
    payload: DealCreate,
    ctx: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(get_db_session),
) -> DealOut:
    deal = await crm_deals_service.create_deal(session, ctx.tenant_id, payload)
    await _record_audit(session, ctx.tenant_id, "crm.deal.created", "crm_deal", str(deal.id), {"title": deal.title})
    await session.commit()
    return DealOut.model_validate(deal)


@router.get("/deals", response_model=list[DealOut])
async def list_deals(
    status_filter: DealStatus | None = Query(default=None, alias="status"),
    stage_id: uuid.UUID | None = Query(default=None),
    search: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    ctx: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(get_db_session),
) -> list[DealOut]:
    deals = await crm_deals_service.list_deals(
        session, ctx.tenant_id, status_filter=status_filter, stage_id=stage_id, search=search, limit=limit, offset=offset
    )
    return [DealOut.model_validate(d) for d in deals]


@router.get("/deals/{deal_id}", response_model=DealOut)
async def get_deal(
    deal_id: uuid.UUID,
    ctx: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(get_db_session),
) -> DealOut:
    deal = await crm_deals_service.get_deal_or_404(session, ctx.tenant_id, deal_id)
    return DealOut.model_validate(deal)


@router.patch("/deals/{deal_id}", response_model=DealOut)
async def update_deal(
    deal_id: uuid.UUID,
    payload: DealUpdate,
    ctx: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(get_db_session),
) -> DealOut:
    deal = await crm_deals_service.update_deal(session, ctx.tenant_id, deal_id, payload)
    await _record_audit(session, ctx.tenant_id, "crm.deal.updated", "crm_deal", str(deal.id))
    await session.commit()
    return DealOut.model_validate(deal)


@router.post("/deals/{deal_id}/move", response_model=DealMoveResponse)
async def move_deal(
    deal_id: uuid.UUID,
    payload: DealMoveRequest,
    ctx: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(get_db_session),
) -> DealMoveResponse:
    result = await crm_deals_service.move_deal_stage(session, ctx.tenant_id, deal_id, payload)
    await _record_audit(
        session,
        ctx.tenant_id,
        "crm.deal.moved",
        "crm_deal",
        str(deal_id),
        {"stage_id": str(payload.stage_id), "status": result.deal.status.value},
    )
    await session.commit()
    return result


@router.get("/deals/{deal_id}/activities", response_model=list[DealActivityOut])
async def list_deal_activities(
    deal_id: uuid.UUID,
    ctx: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(get_db_session),
) -> list[DealActivityOut]:
    items = await crm_deals_service.list_deal_activities(session, ctx.tenant_id, deal_id)
    return [DealActivityOut.model_validate(a) for a in items]


@router.post("/deals/{deal_id}/activities", response_model=DealActivityOut, status_code=status.HTTP_201_CREATED)
async def create_deal_activity(
    deal_id: uuid.UUID,
    payload: DealActivityCreate,
    ctx: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(get_db_session),
) -> DealActivityOut:
    item = await crm_deals_service.add_deal_activity(session, ctx.tenant_id, deal_id, payload)
    await session.commit()
    return DealActivityOut.model_validate(item)


@router.post("/deals/{deal_id}/generate-contract", response_model=ContractOut, status_code=status.HTTP_201_CREATED)
async def generate_contract_from_deal(
    deal_id: uuid.UUID,
    ctx: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(get_db_session),
) -> ContractOut:
    deal = await crm_deals_service.get_deal_or_404(session, ctx.tenant_id, deal_id)
    contract = await crm_deals_service.generate_contract_for_deal(session, ctx.tenant_id, deal)
    await _record_audit(
        session, ctx.tenant_id, "crm.contract.generated", "crm_contract", str(contract.id), {"deal_id": str(deal.id)}
    )
    await session.commit()
    return ContractOut.model_validate(contract)


@router.get("/contracts", response_model=list[ContractOut])
async def list_contracts(
    status_filter: ContractStatus | None = Query(default=None, alias="status"),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    ctx: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(get_db_session),
) -> list[ContractOut]:
    contracts = await crm_deals_service.list_contracts(
        session, ctx.tenant_id, status_filter=status_filter, limit=limit, offset=offset
    )
    return [ContractOut.model_validate(c) for c in contracts]


@router.post("/contracts", response_model=ContractOut, status_code=status.HTTP_201_CREATED)
async def create_contract(
    payload: ContractCreate,
    ctx: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(get_db_session),
) -> ContractOut:
    contract = await crm_deals_service.create_contract(session, ctx.tenant_id, payload)
    await _record_audit(
        session, ctx.tenant_id, "crm.contract.created", "crm_contract", str(contract.id)
    )
    await session.commit()
    return ContractOut.model_validate(contract)


@router.post("/contracts/{contract_id}/sign", response_model=ContractOut)
async def sign_contract(
    contract_id: uuid.UUID,
    payload: ContractSignRequest,
    ctx: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(get_db_session),
) -> ContractOut:
    contract = await crm_deals_service.sign_contract(session, ctx.tenant_id, contract_id, payload)
    await _record_audit(
        session,
        ctx.tenant_id,
        "crm.contract.signed",
        "crm_contract",
        str(contract.id),
        {"signed_by_name": payload.signed_by_name},
    )
    await session.commit()
    return ContractOut.model_validate(contract)


@router.get("/analytics", response_model=CrmAnalyticsOut)
async def get_crm_analytics(
    ctx: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(get_db_session),
) -> CrmAnalyticsOut:
    analytics = await crm_deals_service.get_crm_analytics(session, ctx.tenant_id)
    await session.commit()
    return analytics
