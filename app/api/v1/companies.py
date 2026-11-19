from __future__ import annotations

import math
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status, Response
from sqlalchemy import func, or_, select

from app.api.deps import TenantContext, get_tenant_context, write_audit_log
from app.models.core import CustomFieldEntity
from app.models.crm import Company, CompanySize, LifecycleStage, TaxRegime
from app.schemas.crm import (
    CompanyCreate,
    CompanyResponse,
    CompanyUpdate,
    PaginatedResponse,
)
from app.services.custom_fields import CustomFieldValidationService

router = APIRouter(prefix="/companies", tags=["Empresas"])


@router.post("", response_model=CompanyResponse, status_code=status.HTTP_201_CREATED)
async def create_company(
    payload: CompanyCreate,
    ctx: TenantContext = Depends(get_tenant_context),
) -> Company:
    if payload.cnpj:
        dup = await ctx.session.execute(
            select(Company.id).where(
                Company.tenant_id == ctx.tenant_id,
                Company.cnpj == payload.cnpj,
                Company.deleted_at.is_(None),
            )
        )
        if dup.scalar_one_or_none() is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Já existe uma empresa ativa cadastrada com o CNPJ {payload.cnpj}.",
            )

    validated_custom_fields = await CustomFieldValidationService.validate_and_normalize(
        session=ctx.session,
        tenant_id=ctx.tenant_id,
        entity_type=CustomFieldEntity.COMPANY,
        incoming_custom_fields=payload.custom_fields,
    )

    data = payload.model_dump(exclude={"custom_fields"})
    company = Company(
        tenant_id=ctx.tenant_id,
        custom_fields=validated_custom_fields,
        **data,
    )
    ctx.session.add(company)
    await ctx.session.flush()

    await write_audit_log(
        ctx,
        entity_type="company",
        record_id=company.id,
        action="create",
        changes_after={"legal_name": company.legal_name, "cnpj": company.cnpj},
    )
    await ctx.session.commit()
    await ctx.session.refresh(company)
    return company


@router.get("", response_model=PaginatedResponse)
async def list_companies(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    q: str | None = Query(None, description="Busca por Razão Social, Fantasia, CNPJ, E-mail ou Domínio"),
    lifecycle_stage: LifecycleStage | None = Query(None),
    company_size: CompanySize | None = Query(None),
    tax_regime: TaxRegime | None = Query(None),
    state_uf: str | None = Query(None, min_length=2, max_length=2),
    city: str | None = Query(None),
    primary_cnae_code: str | None = Query(None),
    owner_id: uuid.UUID | None = Query(None),
    icp_tier: str | None = Query(None),
    min_icp_score: int | None = Query(None, ge=0, le=100),
    tag: str | None = Query(None),
    cf_key: str | None = Query(None, description="Chave dentro de custom_fields JSONB"),
    cf_value: str | None = Query(None, description="Valor exato dentro de custom_fields JSONB"),
    ctx: TenantContext = Depends(get_tenant_context),
) -> PaginatedResponse:
    base_filter = [
        Company.tenant_id == ctx.tenant_id,
        Company.deleted_at.is_(None),
    ]

    if q:
        pattern = f"%{q.strip()}%"
        base_filter.append(
            or_(
                Company.legal_name.ilike(pattern),
                Company.trade_name.ilike(pattern),
                Company.cnpj.ilike(pattern),
                Company.primary_email.ilike(pattern),
                Company.domain.ilike(pattern),
            )
        )
    if lifecycle_stage is not None:
        base_filter.append(Company.lifecycle_stage == lifecycle_stage)
    if company_size is not None:
        base_filter.append(Company.company_size == company_size)
    if tax_regime is not None:
        base_filter.append(Company.tax_regime == tax_regime)
    if state_uf is not None:
        base_filter.append(Company.state_uf == state_uf.upper())
    if city is not None:
        base_filter.append(Company.city.ilike(f"%{city.strip()}%"))
    if primary_cnae_code is not None:
        base_filter.append(Company.primary_cnae_code == primary_cnae_code.strip())
    if owner_id is not None:
        base_filter.append(Company.owner_id == owner_id)
    if icp_tier is not None:
        base_filter.append(Company.icp_tier == icp_tier)
    if min_icp_score is not None:
        base_filter.append(Company.icp_score >= min_icp_score)
    if tag is not None:
        base_filter.append(Company.tags.contains([tag]))
    if cf_key and cf_value is not None:
        base_filter.append(Company.custom_fields[cf_key].astext == cf_value)

    total_stmt = select(func.count(Company.id)).where(*base_filter)
    total = (await ctx.session.execute(total_stmt)).scalar_one()

    stmt = (
        select(Company)
        .where(*base_filter)
        .order_by(Company.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    rows = (await ctx.session.execute(stmt)).scalars().all()
    items = [CompanyResponse.model_validate(r) for r in rows]

    return PaginatedResponse(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
        pages=max(1, math.ceil(total / page_size)),
    )


@router.get("/{company_id}", response_model=CompanyResponse)
async def get_company(
    company_id: uuid.UUID,
    ctx: TenantContext = Depends(get_tenant_context),
) -> Company:
    result = await ctx.session.execute(
        select(Company).where(
            Company.id == company_id,
            Company.tenant_id == ctx.tenant_id,
            Company.deleted_at.is_(None),
        )
    )
    company = result.scalar_one_or_none()
    if company is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Empresa não encontrada.")
    return company


@router.patch("/{company_id}", response_model=CompanyResponse)
async def update_company(
    company_id: uuid.UUID,
    payload: CompanyUpdate,
    ctx: TenantContext = Depends(get_tenant_context),
) -> Company:
    result = await ctx.session.execute(
        select(Company).where(
            Company.id == company_id,
            Company.tenant_id == ctx.tenant_id,
            Company.deleted_at.is_(None),
        )
    )
    company = result.scalar_one_or_none()
    if company is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Empresa não encontrada.")

    update_data = payload.model_dump(exclude_unset=True)

    if "cnpj" in update_data and update_data["cnpj"] and update_data["cnpj"] != company.cnpj:
        dup = await ctx.session.execute(
            select(Company.id).where(
                Company.tenant_id == ctx.tenant_id,
                Company.cnpj == update_data["cnpj"],
                Company.id != company.id,
                Company.deleted_at.is_(None),
            )
        )
        if dup.scalar_one_or_none() is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Já existe outra empresa cadastrada com o CNPJ {update_data['cnpj']}.",
            )

    if "custom_fields" in update_data:
        incoming_cf = update_data.pop("custom_fields")
        company.custom_fields = await CustomFieldValidationService.validate_and_normalize(
            session=ctx.session,
            tenant_id=ctx.tenant_id,
            entity_type=CustomFieldEntity.COMPANY,
            incoming_custom_fields=incoming_cf,
            existing_custom_fields=company.custom_fields,
        )

    before_snapshot: dict[str, Any] = {}
    after_snapshot: dict[str, Any] = {}
    for key, val in update_data.items():
        old_val = getattr(company, key)
        if old_val != val:
            before_snapshot[key] = str(old_val) if old_val is not None else None
            after_snapshot[key] = str(val) if val is not None else None
            setattr(company, key, val)

    await write_audit_log(
        ctx,
        entity_type="company",
        record_id=company.id,
        action="update",
        changes_before=before_snapshot,
        changes_after=after_snapshot,
    )
    await ctx.session.commit()
    await ctx.session.refresh(company)
    return company


@router.delete("/{company_id}", status_code=status.HTTP_204_NO_CONTENT, response_class=Response, response_model=None)
async def delete_company(
    company_id: uuid.UUID,
    ctx: TenantContext = Depends(get_tenant_context),
) -> None:
    result = await ctx.session.execute(
        select(Company).where(
            Company.id == company_id,
            Company.tenant_id == ctx.tenant_id,
            Company.deleted_at.is_(None),
        )
    )
    company = result.scalar_one_or_none()
    if company is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Empresa não encontrada.")

    company.deleted_at = datetime.now(timezone.utc)
    await write_audit_log(
        ctx,
        entity_type="company",
        record_id=company.id,
        action="delete",
    )
    await ctx.session.commit()
