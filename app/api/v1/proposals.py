from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.deps import TenantContext, get_tenant_context, write_audit_log
from app.db.base import set_tenant_rls_context
from app.db.session import get_db_session
from app.models.core import Tenant
from app.models.crm import Company, Contact
from app.models.proposals import CatalogItem, CommercialProposal, ProposalStatus
from app.schemas.proposals import (
    CatalogItemCreate,
    CatalogItemResponse,
    CatalogItemUpdate,
    ProposalCreate,
    ProposalDetailResponse,
    ProposalItemResponse,
    ProposalResponse,
    ProposalSendWhatsAppRequest,
    PublicProposalAcceptRequest,
    PublicProposalAcceptResponse,
    PublicProposalViewResponse,
)
from app.services.proposal_service import (
    ProposalService,
    build_public_proposal_pdf_url,
    build_public_proposal_url,
)

router = APIRouter(prefix="/proposals", tags=["Catálogo CPQ & Propostas Comerciais"])
public_router = APIRouter(prefix="/public/proposals", tags=["Portal Público de Aceite Digital"])


def _to_detail_response(prop: CommercialProposal) -> ProposalDetailResponse:
    base = ProposalResponse.model_validate(prop)
    return ProposalDetailResponse(
        **base.model_dump(),
        items=[ProposalItemResponse.model_validate(it) for it in prop.items],
        public_url=build_public_proposal_url(prop.public_token),
        pdf_download_url=build_public_proposal_pdf_url(prop.public_token),
    )


@router.post("/catalog", response_model=CatalogItemResponse, status_code=status.HTTP_201_CREATED)
async def create_catalog_item(
    payload: CatalogItemCreate,
    ctx: TenantContext = Depends(get_tenant_context),
) -> CatalogItem:
    dup = (
        await ctx.session.execute(
            select(CatalogItem.id).where(
                CatalogItem.tenant_id == ctx.tenant_id,
                CatalogItem.sku == payload.sku,
            )
        )
    ).scalar_one_or_none()
    if dup is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Já existe um item de catálogo com SKU '{payload.sku}' neste tenant.",
        )

    item = CatalogItem(tenant_id=ctx.tenant_id, **payload.model_dump())
    ctx.session.add(item)
    await ctx.session.flush()
    await write_audit_log(
        ctx,
        entity_type="catalog_item",
        record_id=item.id,
        action="create",
        changes_after={"sku": item.sku, "name": item.name, "unit_price": float(item.unit_price)},
    )
    await ctx.session.commit()
    await ctx.session.refresh(item)
    return item


@router.get("/catalog", response_model=list[CatalogItemResponse])
async def list_catalog_items(
    category: str | None = Query(None),
    active_only: bool = Query(True),
    ctx: TenantContext = Depends(get_tenant_context),
) -> list[CatalogItem]:
    stmt = select(CatalogItem).where(CatalogItem.tenant_id == ctx.tenant_id)
    if category:
        stmt = stmt.where(CatalogItem.category == category.strip().lower())
    if active_only:
        stmt = stmt.where(CatalogItem.is_active.is_(True))
    stmt = stmt.order_by(CatalogItem.category.asc(), CatalogItem.name.asc())
    return list((await ctx.session.execute(stmt)).scalars().all())


@router.patch("/catalog/{item_id}", response_model=CatalogItemResponse)
async def update_catalog_item(
    item_id: uuid.UUID,
    payload: CatalogItemUpdate,
    ctx: TenantContext = Depends(get_tenant_context),
) -> CatalogItem:
    item = (
        await ctx.session.execute(
            select(CatalogItem).where(
                CatalogItem.id == item_id,
                CatalogItem.tenant_id == ctx.tenant_id,
            )
        )
    ).scalar_one_or_none()
    if item is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Item de catálogo não encontrado.")

    for key, val in payload.model_dump(exclude_unset=True).items():
        setattr(item, key, val)

    await ctx.session.commit()
    await ctx.session.refresh(item)
    return item


@router.post("", response_model=ProposalDetailResponse, status_code=status.HTTP_201_CREATED)
async def create_commercial_proposal(
    payload: ProposalCreate,
    ctx: TenantContext = Depends(get_tenant_context),
) -> ProposalDetailResponse:
    prop = await ProposalService.create_proposal(
        session=ctx.session,
        tenant_id=ctx.tenant_id,
        payload=payload,
    )
    return _to_detail_response(prop)


@router.get("", response_model=list[ProposalResponse])
async def list_commercial_proposals(
    status_filter: ProposalStatus | None = Query(None, alias="status"),
    company_id: uuid.UUID | None = Query(None),
    ctx: TenantContext = Depends(get_tenant_context),
) -> list[CommercialProposal]:
    stmt = select(CommercialProposal).where(CommercialProposal.tenant_id == ctx.tenant_id)
    if status_filter is not None:
        stmt = stmt.where(CommercialProposal.status == status_filter)
    if company_id is not None:
        stmt = stmt.where(CommercialProposal.company_id == company_id)
    stmt = stmt.order_by(CommercialProposal.created_at.desc()).limit(100)
    return list((await ctx.session.execute(stmt)).scalars().all())


@router.get("/{proposal_id}", response_model=ProposalDetailResponse)
async def get_commercial_proposal(
    proposal_id: uuid.UUID,
    ctx: TenantContext = Depends(get_tenant_context),
) -> ProposalDetailResponse:
    prop = await ProposalService.get_proposal_with_items(ctx.session, ctx.tenant_id, proposal_id)
    return _to_detail_response(prop)


@router.get("/{proposal_id}/pdf")
async def download_commercial_proposal_pdf(
    proposal_id: uuid.UUID,
    ctx: TenantContext = Depends(get_tenant_context),
) -> Response:
    prop, pdf_bytes = await ProposalService.render_proposal_pdf(ctx.session, ctx.tenant_id, proposal_id)
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'inline; filename="{prop.proposal_code}.pdf"',
        },
    )


@router.post("/{proposal_id}/send-whatsapp")
async def send_commercial_proposal_whatsapp(
    proposal_id: uuid.UUID,
    payload: ProposalSendWhatsAppRequest,
    ctx: TenantContext = Depends(get_tenant_context),
) -> dict[str, Any]:
    return await ProposalService.send_proposal_via_whatsapp(
        session=ctx.session,
        tenant_id=ctx.tenant_id,
        proposal_id=proposal_id,
        recipient_number=payload.recipient_number,
        instance_name=payload.instance_name,
        custom_message=payload.custom_message,
        attach_pdf=payload.attach_pdf,
    )


@public_router.get("/{public_token}", response_model=PublicProposalViewResponse)
async def view_public_proposal(
    public_token: str,
    session: AsyncSession = Depends(get_db_session),
) -> PublicProposalViewResponse:
    from datetime import datetime, timezone

    tenant_id = await ProposalService.resolve_tenant_by_token(session, public_token)
    if tenant_id is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Proposta não encontrada.")

    await set_tenant_rls_context(session, tenant_id)

    prop = (
        await session.execute(
            select(CommercialProposal)
            .options(selectinload(CommercialProposal.items))
            .where(
                CommercialProposal.tenant_id == tenant_id,
                CommercialProposal.public_token == public_token.strip(),
            )
        )
    ).scalar_one_or_none()
    if prop is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Proposta não encontrada.")

    if prop.status == ProposalStatus.SENT:
        prop.status = ProposalStatus.VIEWED
        prop.viewed_at = datetime.now(timezone.utc)
        await session.commit()

    tenant = (await session.execute(select(Tenant).where(Tenant.id == tenant_id))).scalar_one_or_none()
    company = (
        (await session.execute(select(Company).where(Company.id == prop.company_id))).scalar_one_or_none()
        if prop.company_id
        else None
    )
    contact = (
        (await session.execute(select(Contact).where(Contact.id == prop.contact_id))).scalar_one_or_none()
        if prop.contact_id
        else None
    )

    return PublicProposalViewResponse(
        proposal_code=prop.proposal_code,
        title=prop.title,
        status=prop.status,
        issuer_name=tenant.name if tenant else "TBTC Comercial",
        client_company_name=(company.trade_name or company.legal_name) if company else None,
        client_cnpj=company.cnpj if company else None,
        client_contact_name=contact.full_name if contact else None,
        contract_months=prop.contract_months,
        discount_percent=prop.discount_percent,
        subtotal_mrr=prop.subtotal_mrr,
        subtotal_one_time=prop.subtotal_one_time,
        discount_amount=prop.discount_amount,
        total_mrr=prop.total_mrr,
        total_one_time=prop.total_one_time,
        total_contract_value=prop.total_contract_value,
        valid_until=prop.valid_until,
        executive_summary=prop.executive_summary,
        terms_and_conditions=prop.terms_and_conditions,
        accepted_at=prop.accepted_at,
        accepted_by_name=prop.accepted_by_name,
        items=[ProposalItemResponse.model_validate(it) for it in prop.items],
    )


@public_router.get("/{public_token}/pdf")
async def download_public_proposal_pdf(
    public_token: str,
    session: AsyncSession = Depends(get_db_session),
) -> Response:
    tenant_id = await ProposalService.resolve_tenant_by_token(session, public_token)
    if tenant_id is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Proposta não encontrada.")

    await set_tenant_rls_context(session, tenant_id)

    prop = (
        await session.execute(
            select(CommercialProposal).where(
                CommercialProposal.tenant_id == tenant_id,
                CommercialProposal.public_token == public_token.strip(),
            )
        )
    ).scalar_one_or_none()
    if prop is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Proposta não encontrada.")

    _, pdf_bytes = await ProposalService.render_proposal_pdf(session, tenant_id, prop.id)
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'inline; filename="{prop.proposal_code}.pdf"',
        },
    )


@public_router.post("/{public_token}/accept", response_model=PublicProposalAcceptResponse)
async def accept_public_proposal_endpoint(
    public_token: str,
    payload: PublicProposalAcceptRequest,
    request: Request,
    session: AsyncSession = Depends(get_db_session),
) -> PublicProposalAcceptResponse:
    forwarded_for = request.headers.get("x-forwarded-for")
    client_ip = (
        forwarded_for.split(",")[0].strip()
        if forwarded_for
        else (request.client.host if request.client else None)
    )
    return await ProposalService.accept_public_proposal(
        session=session,
        public_token=public_token,
        payload=payload,
        client_ip=client_ip,
    )
