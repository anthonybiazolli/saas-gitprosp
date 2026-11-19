"""Camada de serviço do Módulo Pipeline CRM, Deals, Contratos e Analytics."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.crm_deals import (
    ContractStatus,
    CrmContract,
    CrmDeal,
    CrmDealActivity,
    CrmPipeline,
    CrmPipelineStage,
    DealActivityType,
    DealStatus,
)
from app.schemas.crm_deals import (
    ContractCreate,
    ContractSignRequest,
    CrmAnalyticsOut,
    DealActivityCreate,
    DealCreate,
    DealMoveRequest,
    DealMoveResponse,
    DealOut,
    DealUpdate,
    KanbanBoardOut,
    KanbanColumnOut,
    PipelineOut,
    PipelineStageOut,
    SourceMetricOut,
    StageMetricOut,
)

DEFAULT_STAGES = [
    ("Novo Lead", 1, 10, "#64748b", False, False),
    ("Qualificado SDR", 2, 25, "#0ea5e9", False, False),
    ("Diagnóstico / Reunião", 3, 45, "#6366f1", False, False),
    ("Proposta Enviada", 4, 65, "#f59e0b", False, False),
    ("Negociação", 5, 80, "#ec4899", False, False),
    ("Fechado - Ganho", 6, 100, "#10b981", True, False),
    ("Fechado - Perdido", 7, 0, "#ef4444", False, True),
]


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


async def ensure_default_pipeline(session: AsyncSession, tenant_id: uuid.UUID) -> CrmPipeline:
    stmt = (
        select(CrmPipeline)
        .options(selectinload(CrmPipeline.stages))
        .where(CrmPipeline.tenant_id == tenant_id, CrmPipeline.is_active.is_(True))
        .order_by(CrmPipeline.is_default.desc(), CrmPipeline.created_at.asc())
    )
    result = await session.execute(stmt)
    pipeline = result.scalars().first()
    if pipeline is not None:
        return pipeline

    pipeline = CrmPipeline(
        tenant_id=tenant_id,
        name="Funil Comercial Padrão",
        description="Pipeline comercial B2B integrado com Outbound, SDR IA e Propostas.",
        is_default=True,
        is_active=True,
    )
    session.add(pipeline)
    await session.flush()

    for name, order, prob, color, is_won, is_lost in DEFAULT_STAGES:
        stage = CrmPipelineStage(
            tenant_id=tenant_id,
            pipeline_id=pipeline.id,
            name=name,
            stage_order=order,
            default_probability=prob,
            color=color,
            is_won=is_won,
            is_lost=is_lost,
        )
        session.add(stage)

    await session.flush()

    reload_stmt = (
        select(CrmPipeline)
        .options(selectinload(CrmPipeline.stages))
        .where(CrmPipeline.id == pipeline.id)
    )
    reloaded = await session.execute(reload_stmt)
    return reloaded.scalars().one()


async def _next_contract_number(session: AsyncSession, tenant_id: uuid.UUID) -> str:
    year = _utcnow().year
    count_stmt = select(func.count(CrmContract.id)).where(CrmContract.tenant_id == tenant_id)
    res = await session.execute(count_stmt)
    seq = int(res.scalar_one() or 0) + 1
    return f"CTR-{year}-{seq:04d}"


async def create_deal(session: AsyncSession, tenant_id: uuid.UUID, data: DealCreate) -> CrmDeal:
    pipeline = await ensure_default_pipeline(session, tenant_id)
    stages = sorted(pipeline.stages, key=lambda s: s.stage_order)
    if not stages:
        raise HTTPException(status_code=500, detail="Pipeline sem etapas configuradas.")

    target_stage: CrmPipelineStage | None = None
    if data.stage_id is not None:
        target_stage = next((s for s in stages if s.id == data.stage_id), None)
        if target_stage is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Etapa do funil não encontrada.")
    else:
        target_stage = stages[0]

    probability = data.probability if data.probability is not None else target_stage.default_probability
    deal_status = DealStatus.OPEN
    closed_at: datetime | None = None
    if target_stage.is_won:
        deal_status = DealStatus.WON
        probability = 100
        closed_at = _utcnow()
    elif target_stage.is_lost:
        deal_status = DealStatus.LOST
        probability = 0
        closed_at = _utcnow()

    deal = CrmDeal(
        tenant_id=tenant_id,
        pipeline_id=pipeline.id,
        stage_id=target_stage.id,
        title=data.title,
        company_name=data.company_name,
        contact_name=data.contact_name,
        contact_email=data.contact_email,
        contact_phone=data.contact_phone,
        contact_document=data.contact_document,
        amount=data.amount,
        currency=data.currency,
        probability=probability,
        status=deal_status,
        source=data.source,
        owner_name=data.owner_name,
        expected_close_date=data.expected_close_date,
        closed_at=closed_at,
        notes=data.notes,
        lead_id=data.lead_id,
        proposal_id=data.proposal_id,
        conversation_id=data.conversation_id,
        tags=data.tags,
        custom_fields=data.custom_fields,
    )
    session.add(deal)
    await session.flush()

    activity = CrmDealActivity(
        tenant_id=tenant_id,
        deal_id=deal.id,
        activity_type=DealActivityType.CREATED,
        title=f"Oportunidade criada na etapa '{target_stage.name}'",
        description=f"Origem: {data.source} | Valor inicial: {data.currency} {data.amount}",
        actor_name=data.owner_name or "Sistema",
        payload={"stage_id": str(target_stage.id), "stage_name": target_stage.name, "amount": str(data.amount)},
    )
    session.add(activity)
    await session.flush()
    await session.refresh(deal)
    return deal


async def list_deals(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    status_filter: DealStatus | None = None,
    stage_id: uuid.UUID | None = None,
    search: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[CrmDeal]:
    stmt = select(CrmDeal).where(CrmDeal.tenant_id == tenant_id)
    if status_filter is not None:
        stmt = stmt.where(CrmDeal.status == status_filter)
    if stage_id is not None:
        stmt = stmt.where(CrmDeal.stage_id == stage_id)
    if search:
        pattern = f"%{search.strip()}%"
        stmt = stmt.where(
            (CrmDeal.title.ilike(pattern))
            | (CrmDeal.contact_name.ilike(pattern))
            | (CrmDeal.company_name.ilike(pattern))
        )
    stmt = stmt.order_by(CrmDeal.updated_at.desc()).limit(limit).offset(offset)
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def get_deal_or_404(session: AsyncSession, tenant_id: uuid.UUID, deal_id: uuid.UUID) -> CrmDeal:
    stmt = select(CrmDeal).where(CrmDeal.tenant_id == tenant_id, CrmDeal.id == deal_id)
    result = await session.execute(stmt)
    deal = result.scalars().first()
    if deal is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Oportunidade (Deal) não encontrada.")
    return deal


async def update_deal(
    session: AsyncSession, tenant_id: uuid.UUID, deal_id: uuid.UUID, data: DealUpdate
) -> CrmDeal:
    deal = await get_deal_or_404(session, tenant_id, deal_id)
    updates = data.model_dump(exclude_unset=True)
    for field, value in updates.items():
        setattr(deal, field, value)

    if updates:
        session.add(
            CrmDealActivity(
                tenant_id=tenant_id,
                deal_id=deal.id,
                activity_type=DealActivityType.NOTE,
                title="Dados da oportunidade atualizados",
                description=f"Campos atualizados: {', '.join(updates.keys())}",
                actor_name=deal.owner_name or "Operador",
                payload={"updated_fields": list(updates.keys())},
            )
        )

    await session.flush()
    await session.refresh(deal)
    return deal


async def generate_contract_for_deal(
    session: AsyncSession, tenant_id: uuid.UUID, deal: CrmDeal
) -> CrmContract:
    existing_stmt = select(CrmContract).where(
        CrmContract.tenant_id == tenant_id,
        CrmContract.deal_id == deal.id,
        CrmContract.status != ContractStatus.CANCELLED,
    )
    existing_res = await session.execute(existing_stmt)
    existing = existing_res.scalars().first()
    if existing is not None:
        return existing

    contract_number = await _next_contract_number(session, tenant_id)
    customer = deal.company_name or deal.contact_name
    contract = CrmContract(
        tenant_id=tenant_id,
        deal_id=deal.id,
        proposal_id=deal.proposal_id,
        contract_number=contract_number,
        title=f"Contrato Comercial - {deal.title}",
        customer_name=customer,
        customer_email=deal.contact_email,
        customer_document=deal.contact_document,
        total_value=deal.amount,
        recurring_monthly_value=deal.amount,
        billing_cycle="monthly",
        currency=deal.currency,
        status=ContractStatus.DRAFT,
        start_date=_utcnow(),
        terms_summary=deal.notes or f"Contrato gerado automaticamente a partir da oportunidade '{deal.title}'.",
        metadata_json={"source_deal_id": str(deal.id), "contact_name": deal.contact_name},
    )
    session.add(contract)
    await session.flush()

    session.add(
        CrmDealActivity(
            tenant_id=tenant_id,
            deal_id=deal.id,
            activity_type=DealActivityType.CONTRACT_GENERATED,
            title=f"Contrato {contract_number} gerado automaticamente",
            description=f"Valor total: {deal.currency} {deal.amount}",
            actor_name="Sistema",
            payload={"contract_id": str(contract.id), "contract_number": contract_number},
        )
    )
    await session.flush()
    await session.refresh(contract)
    return contract


async def move_deal_stage(
    session: AsyncSession, tenant_id: uuid.UUID, deal_id: uuid.UUID, req: DealMoveRequest
) -> DealMoveResponse:
    deal = await get_deal_or_404(session, tenant_id, deal_id)

    stage_stmt = select(CrmPipelineStage).where(
        CrmPipelineStage.tenant_id == tenant_id,
        CrmPipelineStage.id == req.stage_id,
    )
    stage_res = await session.execute(stage_stmt)
    target_stage = stage_res.scalars().first()
    if target_stage is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Etapa de destino não encontrada.")

    prev_stage_stmt = select(CrmPipelineStage).where(CrmPipelineStage.id == deal.stage_id)
    prev_stage = (await session.execute(prev_stage_stmt)).scalars().first()
    prev_stage_name = prev_stage.name if prev_stage else "Anterior"

    deal.pipeline_id = target_stage.pipeline_id
    deal.stage_id = target_stage.id
    deal.probability = req.probability if req.probability is not None else target_stage.default_probability

    generated_contract: CrmContract | None = None
    act_type = DealActivityType.STAGE_CHANGED

    if target_stage.is_won:
        deal.status = DealStatus.WON
        deal.probability = 100
        deal.closed_at = _utcnow()
        act_type = DealActivityType.WON
        if req.auto_generate_contract:
            generated_contract = await generate_contract_for_deal(session, tenant_id, deal)
    elif target_stage.is_lost:
        deal.status = DealStatus.LOST
        deal.probability = 0
        deal.closed_at = _utcnow()
        if req.loss_reason:
            deal.loss_reason = req.loss_reason
        act_type = DealActivityType.LOST
    else:
        deal.status = DealStatus.OPEN
        deal.closed_at = None

    session.add(
        CrmDealActivity(
            tenant_id=tenant_id,
            deal_id=deal.id,
            activity_type=act_type,
            title=f"Movido de '{prev_stage_name}' para '{target_stage.name}'",
            description=req.note or (f"Motivo da perda: {req.loss_reason}" if req.loss_reason else None),
            actor_name=req.actor_name or "Sistema",
            payload={
                "from_stage": prev_stage_name,
                "to_stage": target_stage.name,
                "probability": deal.probability,
                "status": deal.status.value,
            },
        )
    )

    await session.flush()
    await session.refresh(deal)
    return DealMoveResponse(deal=DealOut.model_validate(deal), generated_contract=generated_contract)


async def add_deal_activity(
    session: AsyncSession, tenant_id: uuid.UUID, deal_id: uuid.UUID, data: DealActivityCreate
) -> CrmDealActivity:
    deal = await get_deal_or_404(session, tenant_id, deal_id)
    activity = CrmDealActivity(
        tenant_id=tenant_id,
        deal_id=deal.id,
        activity_type=data.activity_type,
        title=data.title,
        description=data.description,
        actor_name=data.actor_name,
        payload=data.payload,
    )
    session.add(activity)
    await session.flush()
    await session.refresh(activity)
    return activity


async def list_deal_activities(
    session: AsyncSession, tenant_id: uuid.UUID, deal_id: uuid.UUID
) -> list[CrmDealActivity]:
    await get_deal_or_404(session, tenant_id, deal_id)
    stmt = (
        select(CrmDealActivity)
        .where(CrmDealActivity.tenant_id == tenant_id, CrmDealActivity.deal_id == deal_id)
        .order_by(CrmDealActivity.created_at.desc())
    )
    res = await session.execute(stmt)
    return list(res.scalars().all())


async def get_kanban_board(session: AsyncSession, tenant_id: uuid.UUID) -> KanbanBoardOut:
    pipeline = await ensure_default_pipeline(session, tenant_id)
    stages = sorted(pipeline.stages, key=lambda s: s.stage_order)

    deals_stmt = (
        select(CrmDeal)
        .where(CrmDeal.tenant_id == tenant_id, CrmDeal.pipeline_id == pipeline.id)
        .order_by(CrmDeal.updated_at.desc())
    )
    deals = list((await session.execute(deals_stmt)).scalars().all())

    columns: list[KanbanColumnOut] = []
    total_open = Decimal("0.00")
    total_weighted = Decimal("0.00")

    for stage in stages:
        stage_deals = [d for d in deals if d.stage_id == stage.id]
        col_total = sum((Decimal(d.amount) for d in stage_deals), Decimal("0.00"))
        col_weighted = sum(
            ((Decimal(d.amount) * Decimal(d.probability)) / Decimal("100") for d in stage_deals),
            Decimal("0.00"),
        ).quantize(Decimal("0.01"))

        if not stage.is_won and not stage.is_lost:
            total_open += col_total
            total_weighted += col_weighted

        columns.append(
            KanbanColumnOut(
                stage=PipelineStageOut.model_validate(stage),
                deal_count=len(stage_deals),
                total_amount=col_total,
                weighted_amount=col_weighted,
                deals=[DealOut.model_validate(d) for d in stage_deals],
            )
        )

    return KanbanBoardOut(
        pipeline=PipelineOut.model_validate(pipeline),
        total_open_amount=total_open,
        total_weighted_amount=total_weighted.quantize(Decimal("0.01")),
        columns=columns,
    )


async def create_contract(
    session: AsyncSession, tenant_id: uuid.UUID, data: ContractCreate
) -> CrmContract:
    if data.deal_id is not None:
        await get_deal_or_404(session, tenant_id, data.deal_id)

    contract_number = await _next_contract_number(session, tenant_id)
    contract = CrmContract(
        tenant_id=tenant_id,
        deal_id=data.deal_id,
        proposal_id=data.proposal_id,
        contract_number=contract_number,
        title=data.title,
        customer_name=data.customer_name,
        customer_email=data.customer_email,
        customer_document=data.customer_document,
        total_value=data.total_value,
        recurring_monthly_value=data.recurring_monthly_value,
        billing_cycle=data.billing_cycle,
        currency=data.currency,
        status=ContractStatus.DRAFT,
        start_date=data.start_date or _utcnow(),
        end_date=data.end_date,
        terms_summary=data.terms_summary,
        metadata_json=data.metadata_json,
    )
    session.add(contract)
    await session.flush()
    await session.refresh(contract)
    return contract


async def list_contracts(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    status_filter: ContractStatus | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[CrmContract]:
    stmt = select(CrmContract).where(CrmContract.tenant_id == tenant_id)
    if status_filter is not None:
        stmt = stmt.where(CrmContract.status == status_filter)
    stmt = stmt.order_by(CrmContract.created_at.desc()).limit(limit).offset(offset)
    res = await session.execute(stmt)
    return list(res.scalars().all())


async def sign_contract(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    contract_id: uuid.UUID,
    req: ContractSignRequest,
) -> CrmContract:
    stmt = select(CrmContract).where(
        CrmContract.tenant_id == tenant_id,
        CrmContract.id == contract_id,
    )
    contract = (await session.execute(stmt)).scalars().first()
    if contract is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Contrato não encontrado.")

    contract.signed_at = _utcnow()
    contract.signed_by_name = req.signed_by_name
    contract.signed_by_document = req.signed_by_document
    contract.status = ContractStatus.ACTIVE if req.activate_immediately else ContractStatus.SIGNED
    if contract.start_date is None:
        contract.start_date = contract.signed_at

    await session.flush()
    await session.refresh(contract)
    return contract


async def get_crm_analytics(session: AsyncSession, tenant_id: uuid.UUID) -> CrmAnalyticsOut:
    pipeline = await ensure_default_pipeline(session, tenant_id)
    stages = sorted(pipeline.stages, key=lambda s: s.stage_order)

    deals = list(
        (await session.execute(select(CrmDeal).where(CrmDeal.tenant_id == tenant_id))).scalars().all()
    )
    contracts = list(
        (await session.execute(select(CrmContract).where(CrmContract.tenant_id == tenant_id))).scalars().all()
    )

    open_deals = [d for d in deals if d.status == DealStatus.OPEN]
    won_deals = [d for d in deals if d.status == DealStatus.WON]
    lost_deals = [d for d in deals if d.status == DealStatus.LOST]

    open_val = sum((Decimal(d.amount) for d in open_deals), Decimal("0.00"))
    weighted_val = sum(
        ((Decimal(d.amount) * Decimal(d.probability)) / Decimal("100") for d in open_deals),
        Decimal("0.00"),
    ).quantize(Decimal("0.01"))
    won_val = sum((Decimal(d.amount) for d in won_deals), Decimal("0.00"))

    avg_ticket = (won_val / Decimal(len(won_deals))).quantize(Decimal("0.01")) if won_deals else Decimal("0.00")
    closed_total = len(won_deals) + len(lost_deals)
    win_rate = round((len(won_deals) / closed_total) * 100.0, 2) if closed_total > 0 else 0.0

    active_contracts = [
        c for c in contracts if c.status in (ContractStatus.SIGNED, ContractStatus.ACTIVE)
    ]
    mrr_total = sum((Decimal(c.recurring_monthly_value) for c in active_contracts), Decimal("0.00"))

    by_stage: list[StageMetricOut] = []
    for st in stages:
        st_deals = [d for d in deals if d.stage_id == st.id]
        st_total = sum((Decimal(d.amount) for d in st_deals), Decimal("0.00"))
        st_weighted = sum(
            ((Decimal(d.amount) * Decimal(d.probability)) / Decimal("100") for d in st_deals),
            Decimal("0.00"),
        ).quantize(Decimal("0.01"))
        by_stage.append(
            StageMetricOut(
                stage_id=st.id,
                stage_name=st.name,
                stage_order=st.stage_order,
                deal_count=len(st_deals),
                total_amount=st_total,
                weighted_amount=st_weighted,
            )
        )

    sources_map: dict[str, list[CrmDeal]] = {}
    for d in deals:
        sources_map.setdefault(d.source or "manual", []).append(d)

    by_source: list[SourceMetricOut] = []
    for src, src_deals in sorted(sources_map.items()):
        src_won = [d for d in src_deals if d.status == DealStatus.WON]
        by_source.append(
            SourceMetricOut(
                source=src,
                deal_count=len(src_deals),
                won_count=len(src_won),
                total_amount=sum((Decimal(d.amount) for d in src_deals), Decimal("0.00")),
                won_amount=sum((Decimal(d.amount) for d in src_won), Decimal("0.00")),
            )
        )

    return CrmAnalyticsOut(
        open_deals_count=len(open_deals),
        open_pipeline_value=open_val,
        weighted_forecast_value=weighted_val,
        won_deals_count=len(won_deals),
        won_revenue_total=won_val,
        lost_deals_count=len(lost_deals),
        average_ticket=avg_ticket,
        win_rate_percent=win_rate,
        contracts_total_count=len(contracts),
        contracts_signed_or_active_count=len(active_contracts),
        contracts_mrr_total=mrr_total,
        by_stage=by_stage,
        by_source=by_source,
    )
