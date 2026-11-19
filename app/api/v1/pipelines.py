from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status, Response
from sqlalchemy import func, select, update
from sqlalchemy.orm import selectinload

from app.api.deps import TenantContext, get_tenant_context, write_audit_log
from app.models.crm import Deal, Pipeline, PipelineStage
from app.schemas.crm import (
    PipelineCreate,
    PipelineResponse,
    PipelineStageCreate,
    PipelineStageResponse,
    PipelineStageUpdate,
    PipelineUpdate,
)

router = APIRouter(prefix="/pipelines", tags=["Funis e Etapas (Kanban)"])


@router.post("", response_model=PipelineResponse, status_code=status.HTTP_201_CREATED)
async def create_pipeline(
    payload: PipelineCreate,
    ctx: TenantContext = Depends(get_tenant_context),
) -> Pipeline:
    if payload.is_default:
        await ctx.session.execute(
            update(Pipeline)
            .where(Pipeline.tenant_id == ctx.tenant_id)
            .values(is_default=False)
        )

    pipeline = Pipeline(
        tenant_id=ctx.tenant_id,
        name=payload.name,
        description=payload.description,
        pipeline_type=payload.pipeline_type,
        is_default=payload.is_default,
        is_active=payload.is_active,
    )
    ctx.session.add(pipeline)
    await ctx.session.flush()

    for idx, st in enumerate(payload.stages, start=1):
        ctx.session.add(
            PipelineStage(
                tenant_id=ctx.tenant_id,
                pipeline_id=pipeline.id,
                name=st.name,
                display_order=st.display_order or idx,
                win_probability=st.win_probability,
                rotting_days_limit=st.rotting_days_limit,
                color_hex=st.color_hex,
                required_deal_fields=st.required_deal_fields,
                is_won_stage=st.is_won_stage,
                is_lost_stage=st.is_lost_stage,
            )
        )

    await write_audit_log(
        ctx,
        entity_type="pipeline",
        record_id=pipeline.id,
        action="create",
        changes_after={"name": pipeline.name},
    )
    await ctx.session.commit()

    stmt = (
        select(Pipeline)
        .options(selectinload(Pipeline.stages))
        .where(Pipeline.id == pipeline.id)
    )
    return (await ctx.session.execute(stmt)).scalar_one()


@router.get("", response_model=list[PipelineResponse])
async def list_pipelines(
    ctx: TenantContext = Depends(get_tenant_context),
) -> list[Pipeline]:
    stmt = (
        select(Pipeline)
        .options(selectinload(Pipeline.stages))
        .where(Pipeline.tenant_id == ctx.tenant_id)
        .order_by(Pipeline.is_default.desc(), Pipeline.created_at.asc())
    )
    return list((await ctx.session.execute(stmt)).scalars().all())


@router.get("/{pipeline_id}", response_model=PipelineResponse)
async def get_pipeline(
    pipeline_id: uuid.UUID,
    ctx: TenantContext = Depends(get_tenant_context),
) -> Pipeline:
    stmt = (
        select(Pipeline)
        .options(selectinload(Pipeline.stages))
        .where(Pipeline.id == pipeline_id, Pipeline.tenant_id == ctx.tenant_id)
    )
    pipeline = (await ctx.session.execute(stmt)).scalar_one_or_none()
    if pipeline is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Funil não encontrado.")
    return pipeline


@router.patch("/{pipeline_id}", response_model=PipelineResponse)
async def update_pipeline(
    pipeline_id: uuid.UUID,
    payload: PipelineUpdate,
    ctx: TenantContext = Depends(get_tenant_context),
) -> Pipeline:
    stmt = (
        select(Pipeline)
        .options(selectinload(Pipeline.stages))
        .where(Pipeline.id == pipeline_id, Pipeline.tenant_id == ctx.tenant_id)
    )
    pipeline = (await ctx.session.execute(stmt)).scalar_one_or_none()
    if pipeline is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Funil não encontrado.")

    update_data = payload.model_dump(exclude_unset=True)
    if update_data.get("is_default"):
        await ctx.session.execute(
            update(Pipeline)
            .where(Pipeline.tenant_id == ctx.tenant_id, Pipeline.id != pipeline.id)
            .values(is_default=False)
        )

    for key, val in update_data.items():
        setattr(pipeline, key, val)

    await ctx.session.commit()
    await ctx.session.refresh(pipeline)
    return pipeline


@router.post(
    "/{pipeline_id}/stages",
    response_model=PipelineStageResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_pipeline_stage(
    pipeline_id: uuid.UUID,
    payload: PipelineStageCreate,
    ctx: TenantContext = Depends(get_tenant_context),
) -> PipelineStage:
    pipeline_exists = (
        await ctx.session.execute(
            select(Pipeline.id).where(Pipeline.id == pipeline_id, Pipeline.tenant_id == ctx.tenant_id)
        )
    ).scalar_one_or_none()
    if pipeline_exists is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Funil não encontrado.")

    stage = PipelineStage(
        tenant_id=ctx.tenant_id,
        pipeline_id=pipeline_id,
        **payload.model_dump(),
    )
    ctx.session.add(stage)
    await ctx.session.commit()
    await ctx.session.refresh(stage)
    return stage


@router.patch("/{pipeline_id}/stages/{stage_id}", response_model=PipelineStageResponse)
async def update_pipeline_stage(
    pipeline_id: uuid.UUID,
    stage_id: uuid.UUID,
    payload: PipelineStageUpdate,
    ctx: TenantContext = Depends(get_tenant_context),
) -> PipelineStage:
    stage = (
        await ctx.session.execute(
            select(PipelineStage).where(
                PipelineStage.id == stage_id,
                PipelineStage.pipeline_id == pipeline_id,
                PipelineStage.tenant_id == ctx.tenant_id,
            )
        )
    ).scalar_one_or_none()
    if stage is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Etapa não encontrada.")

    for key, val in payload.model_dump(exclude_unset=True).items():
        setattr(stage, key, val)

    await ctx.session.commit()
    await ctx.session.refresh(stage)
    return stage


@router.delete("/{pipeline_id}/stages/{stage_id}", status_code=status.HTTP_204_NO_CONTENT, response_class=Response, response_model=None)
async def delete_pipeline_stage(
    pipeline_id: uuid.UUID,
    stage_id: uuid.UUID,
    ctx: TenantContext = Depends(get_tenant_context),
) -> None:
    stage = (
        await ctx.session.execute(
            select(PipelineStage).where(
                PipelineStage.id == stage_id,
                PipelineStage.pipeline_id == pipeline_id,
                PipelineStage.tenant_id == ctx.tenant_id,
            )
        )
    ).scalar_one_or_none()
    if stage is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Etapa não encontrada.")

    deals_count = (
        await ctx.session.execute(
            select(func.count(Deal.id)).where(
                Deal.stage_id == stage_id,
                Deal.tenant_id == ctx.tenant_id,
                Deal.deleted_at.is_(None),
            )
        )
    ).scalar_one()
    if deals_count > 0:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Não é possível excluir a etapa pois existem {deals_count} negócio(s) vinculados a ela.",
        )

    await ctx.session.delete(stage)
    await ctx.session.commit()
