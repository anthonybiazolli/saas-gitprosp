from __future__ import annotations

import math
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status, Response
from sqlalchemy import func, or_, select
from sqlalchemy.orm import selectinload

from app.api.deps import TenantContext, get_tenant_context, write_audit_log
from app.models.core import CustomFieldEntity
from app.models.crm import (
    BuyingRole,
    Company,
    CompanyContact,
    Contact,
    ContactSeniority,
)
from app.schemas.crm import (
    CompanyLinkInput,
    CompanyLinkResponse,
    ContactCreate,
    ContactResponse,
    ContactUpdate,
    PaginatedResponse,
)
from app.services.custom_fields import CustomFieldValidationService

router = APIRouter(prefix="/contacts", tags=["Contatos"])


@router.post("", response_model=ContactResponse, status_code=status.HTTP_201_CREATED)
async def create_contact(
    payload: ContactCreate,
    ctx: TenantContext = Depends(get_tenant_context),
) -> Contact:
    validated_cf = await CustomFieldValidationService.validate_and_normalize(
        session=ctx.session,
        tenant_id=ctx.tenant_id,
        entity_type=CustomFieldEntity.CONTACT,
        incoming_custom_fields=payload.custom_fields,
    )

    data = payload.model_dump(exclude={"custom_fields", "company_links"})
    contact = Contact(
        tenant_id=ctx.tenant_id,
        custom_fields=validated_cf,
        **data,
    )
    ctx.session.add(contact)
    await ctx.session.flush()

    for link in payload.company_links:
        comp_exists = await ctx.session.execute(
            select(Company.id).where(
                Company.id == link.company_id,
                Company.tenant_id == ctx.tenant_id,
                Company.deleted_at.is_(None),
            )
        )
        if comp_exists.scalar_one_or_none() is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Empresa {link.company_id} não encontrada para vinculação.",
            )
        ctx.session.add(
            CompanyContact(
                tenant_id=ctx.tenant_id,
                company_id=link.company_id,
                contact_id=contact.id,
                role_description=link.role_description,
                is_primary_decision_maker=link.is_primary_decision_maker,
                is_billing_contact=link.is_billing_contact,
                is_legal_signatory=link.is_legal_signatory,
            )
        )

    await write_audit_log(
        ctx,
        entity_type="contact",
        record_id=contact.id,
        action="create",
        changes_after={"full_name": contact.full_name, "email": contact.email},
    )
    await ctx.session.commit()

    stmt = (
        select(Contact)
        .options(selectinload(Contact.companies_assoc))
        .where(Contact.id == contact.id)
    )
    return (await ctx.session.execute(stmt)).scalar_one()


@router.get("", response_model=PaginatedResponse)
async def list_contacts(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    q: str | None = Query(None, description="Busca por Nome, E-mail, WhatsApp, CPF ou Cargo"),
    company_id: uuid.UUID | None = Query(None),
    seniority: ContactSeniority | None = Query(None),
    buying_role: BuyingRole | None = Query(None),
    opt_in_whatsapp: bool | None = Query(None),
    opt_in_email: bool | None = Query(None),
    tag: str | None = Query(None),
    ctx: TenantContext = Depends(get_tenant_context),
) -> PaginatedResponse:
    base_filter = [
        Contact.tenant_id == ctx.tenant_id,
        Contact.deleted_at.is_(None),
    ]

    if q:
        pattern = f"%{q.strip()}%"
        base_filter.append(
            or_(
                Contact.full_name.ilike(pattern),
                Contact.preferred_name.ilike(pattern),
                Contact.email.ilike(pattern),
                Contact.whatsapp_e164.ilike(pattern),
                Contact.cpf.ilike(pattern),
                Contact.job_title.ilike(pattern),
            )
        )
    if company_id is not None:
        base_filter.append(
            Contact.id.in_(
                select(CompanyContact.contact_id).where(
                    CompanyContact.tenant_id == ctx.tenant_id,
                    CompanyContact.company_id == company_id,
                    CompanyContact.is_active.is_(True),
                )
            )
        )
    if seniority is not None:
        base_filter.append(Contact.seniority == seniority)
    if buying_role is not None:
        base_filter.append(Contact.buying_role == buying_role)
    if opt_in_whatsapp is not None:
        base_filter.append(Contact.opt_in_whatsapp == opt_in_whatsapp)
    if opt_in_email is not None:
        base_filter.append(Contact.opt_in_email == opt_in_email)
    if tag is not None:
        base_filter.append(Contact.tags.contains([tag]))

    total = (
        await ctx.session.execute(select(func.count(Contact.id)).where(*base_filter))
    ).scalar_one()

    stmt = (
        select(Contact)
        .options(selectinload(Contact.companies_assoc))
        .where(*base_filter)
        .order_by(Contact.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    rows = (await ctx.session.execute(stmt)).scalars().all()
    items = [ContactResponse.model_validate(r) for r in rows]

    return PaginatedResponse(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
        pages=max(1, math.ceil(total / page_size)),
    )


@router.get("/{contact_id}", response_model=ContactResponse)
async def get_contact(
    contact_id: uuid.UUID,
    ctx: TenantContext = Depends(get_tenant_context),
) -> Contact:
    stmt = (
        select(Contact)
        .options(selectinload(Contact.companies_assoc))
        .where(
            Contact.id == contact_id,
            Contact.tenant_id == ctx.tenant_id,
            Contact.deleted_at.is_(None),
        )
    )
    contact = (await ctx.session.execute(stmt)).scalar_one_or_none()
    if contact is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Contato não encontrado.")
    return contact


@router.patch("/{contact_id}", response_model=ContactResponse)
async def update_contact(
    contact_id: uuid.UUID,
    payload: ContactUpdate,
    ctx: TenantContext = Depends(get_tenant_context),
) -> Contact:
    stmt = (
        select(Contact)
        .options(selectinload(Contact.companies_assoc))
        .where(
            Contact.id == contact_id,
            Contact.tenant_id == ctx.tenant_id,
            Contact.deleted_at.is_(None),
        )
    )
    contact = (await ctx.session.execute(stmt)).scalar_one_or_none()
    if contact is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Contato não encontrado.")

    update_data = payload.model_dump(exclude_unset=True)
    if "custom_fields" in update_data:
        incoming_cf = update_data.pop("custom_fields")
        contact.custom_fields = await CustomFieldValidationService.validate_and_normalize(
            session=ctx.session,
            tenant_id=ctx.tenant_id,
            entity_type=CustomFieldEntity.CONTACT,
            incoming_custom_fields=incoming_cf,
            existing_custom_fields=contact.custom_fields,
        )

    for key, val in update_data.items():
        setattr(contact, key, val)

    await write_audit_log(
        ctx,
        entity_type="contact",
        record_id=contact.id,
        action="update",
        changes_after={k: str(v) for k, v in update_data.items()},
    )
    await ctx.session.commit()
    await ctx.session.refresh(contact)
    return contact


@router.post("/{contact_id}/companies", response_model=CompanyLinkResponse, status_code=status.HTTP_201_CREATED)
async def link_contact_to_company(
    contact_id: uuid.UUID,
    payload: CompanyLinkInput,
    ctx: TenantContext = Depends(get_tenant_context),
) -> CompanyContact:
    contact_exists = await ctx.session.execute(
        select(Contact.id).where(
            Contact.id == contact_id,
            Contact.tenant_id == ctx.tenant_id,
            Contact.deleted_at.is_(None),
        )
    )
    if contact_exists.scalar_one_or_none() is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Contato não encontrado.")

    company_exists = await ctx.session.execute(
        select(Company.id).where(
            Company.id == payload.company_id,
            Company.tenant_id == ctx.tenant_id,
            Company.deleted_at.is_(None),
        )
    )
    if company_exists.scalar_one_or_none() is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Empresa não encontrada.")

    existing_link = (
        await ctx.session.execute(
            select(CompanyContact).where(
                CompanyContact.tenant_id == ctx.tenant_id,
                CompanyContact.company_id == payload.company_id,
                CompanyContact.contact_id == contact_id,
            )
        )
    ).scalar_one_or_none()

    if existing_link is not None:
        existing_link.role_description = payload.role_description
        existing_link.is_primary_decision_maker = payload.is_primary_decision_maker
        existing_link.is_billing_contact = payload.is_billing_contact
        existing_link.is_legal_signatory = payload.is_legal_signatory
        existing_link.is_active = True
        link_obj = existing_link
    else:
        link_obj = CompanyContact(
            tenant_id=ctx.tenant_id,
            company_id=payload.company_id,
            contact_id=contact_id,
            role_description=payload.role_description,
            is_primary_decision_maker=payload.is_primary_decision_maker,
            is_billing_contact=payload.is_billing_contact,
            is_legal_signatory=payload.is_legal_signatory,
        )
        ctx.session.add(link_obj)

    await ctx.session.commit()
    await ctx.session.refresh(link_obj)
    return link_obj


@router.delete("/{contact_id}/companies/{company_id}", status_code=status.HTTP_204_NO_CONTENT, response_class=Response, response_model=None)
async def unlink_contact_from_company(
    contact_id: uuid.UUID,
    company_id: uuid.UUID,
    ctx: TenantContext = Depends(get_tenant_context),
) -> None:
    link = (
        await ctx.session.execute(
            select(CompanyContact).where(
                CompanyContact.tenant_id == ctx.tenant_id,
                CompanyContact.contact_id == contact_id,
                CompanyContact.company_id == company_id,
            )
        )
    ).scalar_one_or_none()
    if link is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Vínculo não encontrado.")

    await ctx.session.delete(link)
    await ctx.session.commit()


@router.delete("/{contact_id}", status_code=status.HTTP_204_NO_CONTENT, response_class=Response, response_model=None)
async def delete_contact(
    contact_id: uuid.UUID,
    ctx: TenantContext = Depends(get_tenant_context),
) -> None:
    contact = (
        await ctx.session.execute(
            select(Contact).where(
                Contact.id == contact_id,
                Contact.tenant_id == ctx.tenant_id,
                Contact.deleted_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if contact is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Contato não encontrado.")

    contact.deleted_at = datetime.now(timezone.utc)
    await write_audit_log(
        ctx,
        entity_type="contact",
        record_id=contact.id,
        action="delete",
    )
    await ctx.session.commit()
