from __future__ import annotations

import math
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status, Response
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from app.api.deps import TenantContext, get_tenant_context, write_audit_log
from app.models.core import CustomFieldEntity
from app.models.crm import (
    BillingFrequency,
    Company,
    Deal,
    DealItem,
    DealStageHistory,
    DealStatus,
    LifecycleStage,
    Pipeline,
    PipelineStage,
)
from app.schemas.crm import (
    DealCreate,
    DealItemInput,
    DealMoveStageRequest,
    DealResponse,
    DealStageHistoryResponse,
    DealUpdate,
    PaginatedResponse,
)
from app.services.custom_fields import CustomFieldValidationService

router = APIRouter(prefix="/deals", tags=["Negócios (Deals & Kanban)"])


def _validate_stage_governance(deal: Deal, target_stage: PipelineStage) -> None:
    """
    Garante que todos os campos configurados em `required_deal_fields` na etapa destino
    estejam preenchidos no Deal, em `qualification_data` ou em `custom_fields`.
    """
    if not target_stage.required_deal_fields:
        return

    missing: list[str] = []
    for req_field in target_stage.required_deal_fields:
        val = None
        if hasattr(deal, req_field):
            val = getattr(deal, req_field)
        elif deal.qualification_data and req_field in deal.qualification_data:
            val = deal.qualification_data.get(req_field)
        elif deal.custom_fields and req_field in deal.custom_fields:
            val = deal.custom_fields.get(req_field)

        if val is None or (isinstance(val, str) and val.strip() == "") or val == []:
            missing.append(req_field)

    if missing:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "message": f"Campos obrigatórios pendentes para avançar para a etapa '{target_stage.name}'.",
                "missing_fields": missing,
            },
        )


def _compute_items_totals(
    items_input: list[DealItemInput],
    contract_months: int,
    discount_amount: Decimal,
) -> tuple[list[dict[str, Any]], Decimal, Decimal, Decimal]:
    mrr = Decimal("0.00")
    one_time = Decimal("0.00")
    prepared_items: list[dict[str, Any]] = []

    for item in items_input:
        gross = item.quantity * item.unit_price
        discount_factor = Decimal("1.00") - (item.discount_percent / Decimal("100.00"))
        subtotal = (gross * discount_factor).quantize(Decimal("0.01"))

        if item.billing_frequency == BillingFrequency.ONE_TIME:
            one_time += subtotal
        elif item.billing_frequency == BillingFrequency.MONTHLY:
            mrr += subtotal
        elif item.billing_frequency == BillingFrequency.QUARTERLY:
            mrr += (subtotal / Decimal("3")).quantize(Decimal("0.01"))
        elif item.billing_frequency == BillingFrequency.SEMIANNUAL:
            mrr += (subtotal / Decimal("6")).quantize(Decimal("0.01"))
        elif item.billing_frequency == BillingFrequency.ANNUAL:
            mrr += (subtotal / Decimal("12")).quantize(Decimal("0.01"))

        prepared_items.append(
            {
                "product_id": item.product_id,
                "quantity": item.quantity,
                "unit_price": item.unit_price,
                "discount_percent": item.discount_percent,
                "subtotal": subtotal,
                "billing_frequency": item.billing_frequency,
            }
        )

    tcv = max(Decimal("0.00"), (mrr * Decimal(contract_months)) + one_time - discount_amount)
    return prepared_items, mrr, one_time, tcv


@router.post("", response_model=DealResponse, status_code=status.HTTP_201_CREATED)
async def create_deal(
    payload: DealCreate,
    ctx: TenantContext = Depends(get_tenant_context),
) -> Deal:
    company = (
        await ctx.session.execute(
            select(Company).where(
                Company.id == payload.company_id,
                Company.tenant_id == ctx.tenant_id,
                Company.deleted_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if company is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Empresa não encontrada.")

    # Resolve Pipeline e Stage padrão caso não informados
    pipeline_id = payload.pipeline_id
    if pipeline_id is None:
        default_pipeline = (
            await ctx.session.execute(
                select(Pipeline)
                .options(selectinload(Pipeline.stages))
                .where(Pipeline.tenant_id == ctx.tenant_id, Pipeline.is_active.is_(True))
                .order_by(Pipeline.is_default.desc(), Pipeline.created_at.asc())
            )
        ).scalars().first()
        if default_pipeline is None or not default_pipeline.stages:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Nenhum funil com etapas configurado para este tenant.",
            )
        pipeline_id = default_pipeline.id
        target_stage = (
            next((s for s in default_pipeline.stages if s.id == payload.stage_id), None)
            if payload.stage_id
            else sorted(default_pipeline.stages, key=lambda s: s.display_order)[0]
        )
    else:
        stmt_stage = select(PipelineStage).where(
            PipelineStage.tenant_id == ctx.tenant_id,
            PipelineStage.pipeline_id == pipeline_id,
        )
        if payload.stage_id:
            stmt_stage = stmt_stage.where(PipelineStage.id == payload.stage_id)
        else:
            stmt_stage = stmt_stage.order_by(PipelineStage.display_order.asc())
        target_stage = (await ctx.session.execute(stmt_stage)).scalars().first()

    if target_stage is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Etapa do funil não encontrada.")

    validated_cf = await CustomFieldValidationService.validate_and_normalize(
        session=ctx.session,
        tenant_id=ctx.tenant_id,
        entity_type=CustomFieldEntity.DEAL,
        incoming_custom_fields=payload.custom_fields,
    )

    if payload.items:
        prepared_items, mrr_val, one_time_val, tcv_val = _compute_items_totals(
            payload.items, payload.contract_months, payload.discount_amount
        )
    else:
        prepared_items = []
        mrr_val = payload.mrr_value
        one_time_val = payload.one_time_value
        tcv_val = (
            payload.total_contract_value
            if payload.total_contract_value is not None
            else max(Decimal("0.00"), (mrr_val * Decimal(payload.contract_months)) + one_time_val - payload.discount_amount)
        )

    now = datetime.now(timezone.utc)
    deal = Deal(
        tenant_id=ctx.tenant_id,
        title=payload.title,
        company_id=payload.company_id,
        primary_contact_id=payload.primary_contact_id,
        pipeline_id=pipeline_id,
        stage_id=target_stage.id,
        owner_id=payload.owner_id,
        sdr_id=payload.sdr_id,
        status=DealStatus.WON if target_stage.is_won_stage else DealStatus.OPEN,
        mrr_value=mrr_val,
        one_time_value=one_time_val,
        total_contract_value=tcv_val,
        discount_amount=payload.discount_amount,
        contract_months=payload.contract_months,
        win_probability=target_stage.win_probability,
        expected_close_date=payload.expected_close_date,
        stage_entered_at=now,
        qualification_data=payload.qualification_data,
        tags=payload.tags,
        custom_fields=validated_cf,
    )

    _validate_stage_governance(deal, target_stage)

    ctx.session.add(deal)
    await ctx.session.flush()

    for item_dict in prepared_items:
        ctx.session.add(
            DealItem(
                tenant_id=ctx.tenant_id,
                deal_id=deal.id,
                **item_dict,
            )
        )

    ctx.session.add(
        DealStageHistory(
            tenant_id=ctx.tenant_id,
            deal_id=deal.id,
            from_stage_id=None,
            to_stage_id=target_stage.id,
            changed_by_user_id=ctx.user_id,
            changed_by_ai=False,
            duration_in_previous_stage_seconds=None,
            entered_at=now,
        )
    )

    if company.lifecycle_stage in (LifecycleStage.PROSPECT, LifecycleStage.LEAD, LifecycleStage.MQL, LifecycleStage.SQL):
        company.lifecycle_stage = LifecycleStage.OPPORTUNITY

    await write_audit_log(
        ctx,
        entity_type="deal",
        record_id=deal.id,
        action="create",
        changes_after={"title": deal.title, "tcv": str(deal.total_contract_value)},
    )
    await ctx.session.commit()

    stmt = select(Deal).options(selectinload(Deal.items)).where(Deal.id == deal.id)
    return (await ctx.session.execute(stmt)).scalar_one()


@router.get("", response_model=PaginatedResponse)
async def list_deals(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    pipeline_id: uuid.UUID | None = Query(None),
    stage_id: uuid.UUID | None = Query(None),
    status_filter: DealStatus | None = Query(None, alias="status"),
    owner_id: uuid.UUID | None = Query(None),
    company_id: uuid.UUID | None = Query(None),
    q: str | None = Query(None),
    ctx: TenantContext = Depends(get_tenant_context),
) -> PaginatedResponse:
    base_filter = [
        Deal.tenant_id == ctx.tenant_id,
        Deal.deleted_at.is_(None),
    ]
    if pipeline_id is not None:
        base_filter.append(Deal.pipeline_id == pipeline_id)
    if stage_id is not None:
        base_filter.append(Deal.stage_id == stage_id)
    if status_filter is not None:
        base_filter.append(Deal.status == status_filter)
    if owner_id is not None:
        base_filter.append(Deal.owner_id == owner_id)
    if company_id is not None:
        base_filter.append(Deal.company_id == company_id)
    if q:
        base_filter.append(Deal.title.ilike(f"%{q.strip()}%"))

    total = (
        await ctx.session.execute(select(func.count(Deal.id)).where(*base_filter))
    ).scalar_one()

    stmt = (
        select(Deal)
        .options(selectinload(Deal.items))
        .where(*base_filter)
        .order_by(Deal.updated_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    rows = (await ctx.session.execute(stmt)).scalars().all()
    items = [DealResponse.model_validate(r) for r in rows]

    return PaginatedResponse(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
        pages=max(1, math.ceil(total / page_size)),
    )


@router.get("/kanban/{pipeline_id}")
async def get_kanban_board(
    pipeline_id: uuid.UUID,
    owner_id: uuid.UUID | None = Query(None),
    ctx: TenantContext = Depends(get_tenant_context),
) -> dict[str, Any]:
    pipeline = (
        await ctx.session.execute(
            select(Pipeline)
            .options(selectinload(Pipeline.stages))
            .where(Pipeline.id == pipeline_id, Pipeline.tenant_id == ctx.tenant_id)
        )
    ).scalar_one_or_none()
    if pipeline is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Funil não encontrado.")

    deal_filters = [
        Deal.tenant_id == ctx.tenant_id,
        Deal.pipeline_id == pipeline_id,
        Deal.deleted_at.is_(None),
    ]
    if owner_id is not None:
        deal_filters.append(Deal.owner_id == owner_id)

    deals = (
        await ctx.session.execute(
            select(Deal)
            .options(selectinload(Deal.items))
            .where(*deal_filters)
            .order_by(Deal.stage_entered_at.desc())
        )
    ).scalars().all()

    now = datetime.now(timezone.utc)
    deals_by_stage: dict[uuid.UUID, list[Deal]] = {s.id: [] for s in pipeline.stages}
    for d in deals:
        if d.stage_id in deals_by_stage:
            deals_by_stage[d.stage_id].append(d)

    columns = []
    for stage in sorted(pipeline.stages, key=lambda s: s.display_order):
        stage_deals = deals_by_stage.get(stage.id, [])
        total_mrr = sum((d.mrr_value for d in stage_deals), Decimal("0.00"))
        total_tcv = sum((d.total_contract_value for d in stage_deals), Decimal("0.00"))
        weighted_tcv = (total_tcv * Decimal(stage.win_probability) / Decimal("100")).quantize(Decimal("0.01"))

        cards = []
        for d in stage_deals:
            days_in_stage = max(0, (now - d.stage_entered_at).days)
            is_rotting = bool(
                stage.rotting_days_limit is not None
                and d.status == DealStatus.OPEN
                and days_in_stage > stage.rotting_days_limit
            )
            card_data = DealResponse.model_validate(d).model_dump(mode="json")
            card_data["days_in_stage"] = days_in_stage
            card_data["is_rotting"] = is_rotting
            cards.append(card_data)

        columns.append(
            {
                "stage_id": str(stage.id),
                "name": stage.name,
                "display_order": stage.display_order,
                "color_hex": stage.color_hex,
                "win_probability": stage.win_probability,
                "rotting_days_limit": stage.rotting_days_limit,
                "required_deal_fields": stage.required_deal_fields,
                "deals_count": len(cards),
                "total_mrr": float(total_mrr),
                "total_tcv": float(total_tcv),
                "weighted_forecast_tcv": float(weighted_tcv),
                "deals": cards,
            }
        )

    return {
        "pipeline_id": str(pipeline.id),
        "pipeline_name": pipeline.name,
        "columns": columns,
    }


@router.get("/{deal_id}", response_model=DealResponse)
async def get_deal(
    deal_id: uuid.UUID,
    ctx: TenantContext = Depends(get_tenant_context),
) -> Deal:
    stmt = (
        select(Deal)
        .options(selectinload(Deal.items))
        .where(
            Deal.id == deal_id,
            Deal.tenant_id == ctx.tenant_id,
            Deal.deleted_at.is_(None),
        )
    )
    deal = (await ctx.session.execute(stmt)).scalar_one_or_none()
    if deal is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Negócio não encontrado.")
    return deal


@router.get("/{deal_id}/stage-history", response_model=list[DealStageHistoryResponse])
async def get_deal_stage_history(
    deal_id: uuid.UUID,
    ctx: TenantContext = Depends(get_tenant_context),
) -> list[DealStageHistory]:
    stmt = (
        select(DealStageHistory)
        .where(
            DealStageHistory.deal_id == deal_id,
            DealStageHistory.tenant_id == ctx.tenant_id,
        )
        .order_by(DealStageHistory.entered_at.asc())
    )
    return list((await ctx.session.execute(stmt)).scalars().all())


@router.patch("/{deal_id}", response_model=DealResponse)
async def update_deal(
    deal_id: uuid.UUID,
    payload: DealUpdate,
    ctx: TenantContext = Depends(get_tenant_context),
) -> Deal:
    stmt = (
        select(Deal)
        .options(selectinload(Deal.items))
        .where(
            Deal.id == deal_id,
            Deal.tenant_id == ctx.tenant_id,
            Deal.deleted_at.is_(None),
        )
    )
    deal = (await ctx.session.execute(stmt)).scalar_one_or_none()
    if deal is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Negócio não encontrado.")

    update_data = payload.model_dump(exclude_unset=True)

    if "custom_fields" in update_data:
        incoming_cf = update_data.pop("custom_fields")
        deal.custom_fields = await CustomFieldValidationService.validate_and_normalize(
            session=ctx.session,
            tenant_id=ctx.tenant_id,
            entity_type=CustomFieldEntity.DEAL,
            incoming_custom_fields=incoming_cf,
            existing_custom_fields=deal.custom_fields,
        )

    if "qualification_data" in update_data and update_data["qualification_data"] is not None:
        merged_qual = dict(deal.qualification_data or {})
        merged_qual.update(update_data.pop("qualification_data"))
        deal.qualification_data = merged_qual

    for key, val in update_data.items():
        setattr(deal, key, val)

    await write_audit_log(
        ctx,
        entity_type="deal",
        record_id=deal.id,
        action="update",
        changes_after={k: str(v) for k, v in update_data.items()},
    )
    await ctx.session.commit()
    await ctx.session.refresh(deal)
    return deal


@router.post("/{deal_id}/move-stage", response_model=DealResponse)
async def move_deal_stage(
    deal_id: uuid.UUID,
    payload: DealMoveStageRequest,
    ctx: TenantContext = Depends(get_tenant_context),
) -> Deal:
    stmt = (
        select(Deal)
        .options(selectinload(Deal.items))
        .where(
            Deal.id == deal_id,
            Deal.tenant_id == ctx.tenant_id,
            Deal.deleted_at.is_(None),
        )
    )
    deal = (await ctx.session.execute(stmt)).scalar_one_or_none()
    if deal is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Negócio não encontrado.")

    target_stage = (
        await ctx.session.execute(
            select(PipelineStage).where(
                PipelineStage.id == payload.target_stage_id,
                PipelineStage.tenant_id == ctx.tenant_id,
            )
        )
    ).scalar_one_or_none()
    if target_stage is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Etapa destino não encontrada.")

    if deal.stage_id == target_stage.id:
        return deal

    _validate_stage_governance(deal, target_stage)

    if target_stage.is_lost_stage and not payload.loss_reason:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="O motivo de perda (loss_reason) é obrigatório ao mover para uma etapa de perda.",
        )

    now = datetime.now(timezone.utc)
    previous_stage_id = deal.stage_id
    duration_seconds = max(0, int((now - deal.stage_entered_at).total_seconds()))

    deal.pipeline_id = target_stage.pipeline_id
    deal.stage_id = target_stage.id
    deal.stage_entered_at = now
    deal.win_probability = target_stage.win_probability

    if target_stage.is_won_stage:
        deal.status = DealStatus.WON
        deal.closed_at = now
        company = (
            await ctx.session.execute(select(Company).where(Company.id == deal.company_id))
        ).scalar_one_or_none()
        if company is not None:
            company.lifecycle_stage = LifecycleStage.ACTIVE_CLIENT
    elif target_stage.is_lost_stage:
        deal.status = DealStatus.LOST
        deal.closed_at = now
        deal.loss_reason = payload.loss_reason
        deal.loss_notes = payload.loss_notes
        deal.loss_competitor = payload.loss_competitor
    else:
        deal.status = DealStatus.OPEN
        deal.closed_at = None

    ctx.session.add(
        DealStageHistory(
            tenant_id=ctx.tenant_id,
            deal_id=deal.id,
            from_stage_id=previous_stage_id,
            to_stage_id=target_stage.id,
            changed_by_user_id=ctx.user_id,
            changed_by_ai=payload.changed_by_ai,
            duration_in_previous_stage_seconds=duration_seconds,
            entered_at=now,
        )
    )

    await write_audit_log(
        ctx,
        entity_type="deal",
        record_id=deal.id,
        action="stage_move",
        changes_before={"stage_id": str(previous_stage_id)},
        changes_after={"stage_id": str(target_stage.id), "status": deal.status.value},
    )
    await ctx.session.commit()
    await ctx.session.refresh(deal)
    return deal


@router.put("/{deal_id}/items", response_model=DealResponse)
async def replace_deal_items(
    deal_id: uuid.UUID,
    items: list[DealItemInput],
    ctx: TenantContext = Depends(get_tenant_context),
) -> Deal:
    stmt = (
        select(Deal)
        .options(selectinload(Deal.items))
        .where(
            Deal.id == deal_id,
            Deal.tenant_id == ctx.tenant_id,
            Deal.deleted_at.is_(None),
        )
    )
    deal = (await ctx.session.execute(stmt)).scalar_one_or_none()
    if deal is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Negócio não encontrado.")

    for old_item in list(deal.items):
        await ctx.session.delete(old_item)
    await ctx.session.flush()

    prepared_items, mrr_val, one_time_val, tcv_val = _compute_items_totals(
        items, deal.contract_months, deal.discount_amount
    )
    for item_dict in prepared_items:
        ctx.session.add(
            DealItem(
                tenant_id=ctx.tenant_id,
                deal_id=deal.id,
                **item_dict,
            )
        )

    deal.mrr_value = mrr_val
    deal.one_time_value = one_time_val
    deal.total_contract_value = tcv_val

    await ctx.session.commit()

    refreshed = (
        await ctx.session.execute(
            select(Deal).options(selectinload(Deal.items)).where(Deal.id == deal.id)
        )
    ).scalar_one()
    return refreshed


@router.delete("/{deal_id}", status_code=status.HTTP_204_NO_CONTENT, response_class=Response, response_model=None)
async def delete_deal(
    deal_id: uuid.UUID,
    ctx: TenantContext = Depends(get_tenant_context),
) -> None:
    deal = (
        await ctx.session.execute(
            select(Deal).where(
                Deal.id == deal_id,
                Deal.tenant_id == ctx.tenant_id,
                Deal.deleted_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if deal is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Negócio não encontrado.")

    deal.deleted_at = datetime.now(timezone.utc)
    await write_audit_log(
        ctx,
        entity_type="deal",
        record_id=deal.id,
        action="delete",
    )
    await ctx.session.commit()
