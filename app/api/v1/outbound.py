from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.api.deps import TenantContext, get_tenant_context, write_audit_log
from app.models.outbound import (
    CadenceEnrollment,
    CadenceSequence,
    OutboundLead,
    OutboundLeadStatus,
    ProspectingCampaign,
)
from app.schemas.outbound import (
    CadenceEnrollmentResponse,
    CadenceEnrollRequest,
    CadenceSequenceCreate,
    CadenceSequenceResponse,
    CampaignCreate,
    CampaignResponse,
    OutboundLeadBatchImportRequest,
    OutboundLeadResponse,
    OutboundPromoteRequest,
)
from app.services.outbound_service import OutboundAndCadenceService

router = APIRouter(prefix="/outbound", tags=["Prospecção Outbound & Cadências"])


@router.post("/campaigns", response_model=CampaignResponse, status_code=status.HTTP_201_CREATED)
async def create_prospecting_campaign(
    payload: CampaignCreate,
    ctx: TenantContext = Depends(get_tenant_context),
) -> ProspectingCampaign:
    dup = (
        await ctx.session.execute(
            select(ProspectingCampaign.id).where(
                ProspectingCampaign.tenant_id == ctx.tenant_id,
                ProspectingCampaign.slug == payload.slug,
            )
        )
    ).scalar_one_or_none()
    if dup is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Já existe uma campanha com o slug '{payload.slug}' neste tenant.",
        )

    camp = ProspectingCampaign(tenant_id=ctx.tenant_id, **payload.model_dump())
    ctx.session.add(camp)
    await ctx.session.flush()
    await write_audit_log(
        ctx,
        entity_type="prospecting_campaign",
        record_id=camp.id,
        action="create",
        changes_after={"slug": camp.slug, "name": camp.name},
    )
    await ctx.session.commit()
    await ctx.session.refresh(camp)
    return camp


@router.get("/campaigns", response_model=list[CampaignResponse])
async def list_prospecting_campaigns(
    ctx: TenantContext = Depends(get_tenant_context),
) -> list[ProspectingCampaign]:
    stmt = (
        select(ProspectingCampaign)
        .where(ProspectingCampaign.tenant_id == ctx.tenant_id)
        .order_by(ProspectingCampaign.created_at.desc())
    )
    return list((await ctx.session.execute(stmt)).scalars().all())


@router.post("/leads/import", response_model=list[OutboundLeadResponse], status_code=status.HTTP_201_CREATED)
async def import_outbound_leads(
    payload: OutboundLeadBatchImportRequest,
    ctx: TenantContext = Depends(get_tenant_context),
) -> list[OutboundLead]:
    return await OutboundAndCadenceService.import_leads(
        session=ctx.session,
        tenant_id=ctx.tenant_id,
        leads_in=payload.leads,
        campaign_id=payload.campaign_id,
        auto_enrich=payload.auto_enrich,
    )


@router.get("/leads", response_model=list[OutboundLeadResponse])
async def list_outbound_leads(
    status_filter: OutboundLeadStatus | None = Query(None, alias="status"),
    ctx: TenantContext = Depends(get_tenant_context),
) -> list[OutboundLead]:
    stmt = select(OutboundLead).where(OutboundLead.tenant_id == ctx.tenant_id)
    if status_filter is not None:
        stmt = stmt.where(OutboundLead.status == status_filter)
    stmt = stmt.order_by(OutboundLead.icp_score.desc(), OutboundLead.created_at.desc()).limit(100)
    return list((await ctx.session.execute(stmt)).scalars().all())


@router.post("/leads/{lead_id}/enrich", response_model=OutboundLeadResponse)
async def enrich_single_outbound_lead(
    lead_id: uuid.UUID,
    ctx: TenantContext = Depends(get_tenant_context),
) -> OutboundLead:
    lead = (
        await ctx.session.execute(
            select(OutboundLead).where(
                OutboundLead.id == lead_id,
                OutboundLead.tenant_id == ctx.tenant_id,
            )
        )
    ).scalar_one_or_none()
    if lead is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Lead outbound não encontrado.")

    camp: ProspectingCampaign | None = None
    if lead.campaign_id:
        camp = (
            await ctx.session.execute(
                select(ProspectingCampaign).where(ProspectingCampaign.id == lead.campaign_id)
            )
        ).scalar_one_or_none()

    await OutboundAndCadenceService.enrich_outbound_lead(session=ctx.session, lead=lead, campaign=camp)
    await ctx.session.commit()
    await ctx.session.refresh(lead)
    return lead


@router.post("/leads/{lead_id}/promote", response_model=OutboundLeadResponse)
async def promote_outbound_lead(
    lead_id: uuid.UUID,
    payload: OutboundPromoteRequest,
    ctx: TenantContext = Depends(get_tenant_context),
) -> OutboundLead:
    return await OutboundAndCadenceService.promote_lead_to_crm(
        session=ctx.session,
        tenant_id=ctx.tenant_id,
        lead_id=lead_id,
        payload=payload,
    )


@router.post("/cadences", response_model=CadenceSequenceResponse, status_code=status.HTTP_201_CREATED)
async def create_cadence_sequence(
    payload: CadenceSequenceCreate,
    ctx: TenantContext = Depends(get_tenant_context),
) -> CadenceSequence:
    return await OutboundAndCadenceService.create_cadence(
        session=ctx.session,
        tenant_id=ctx.tenant_id,
        payload=payload,
    )


@router.get("/cadences", response_model=list[CadenceSequenceResponse])
async def list_cadence_sequences(
    ctx: TenantContext = Depends(get_tenant_context),
) -> list[CadenceSequence]:
    stmt = (
        select(CadenceSequence)
        .options(selectinload(CadenceSequence.steps))
        .where(CadenceSequence.tenant_id == ctx.tenant_id)
        .order_by(CadenceSequence.created_at.desc())
    )
    return list((await ctx.session.execute(stmt)).scalars().all())


@router.post("/cadences/enroll", response_model=CadenceEnrollmentResponse, status_code=status.HTTP_201_CREATED)
async def enroll_target_in_cadence(
    payload: CadenceEnrollRequest,
    ctx: TenantContext = Depends(get_tenant_context),
) -> CadenceEnrollment:
    return await OutboundAndCadenceService.enroll_in_cadence(
        session=ctx.session,
        tenant_id=ctx.tenant_id,
        payload=payload,
    )


@router.get("/cadences/enrollments", response_model=list[CadenceEnrollmentResponse])
async def list_cadence_enrollments(
    ctx: TenantContext = Depends(get_tenant_context),
) -> list[CadenceEnrollment]:
    stmt = (
        select(CadenceEnrollment)
        .where(CadenceEnrollment.tenant_id == ctx.tenant_id)
        .order_by(CadenceEnrollment.next_run_at.asc())
        .limit(100)
    )
    return list((await ctx.session.execute(stmt)).scalars().all())
