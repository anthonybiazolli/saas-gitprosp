from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status, Response
from sqlalchemy import select

from app.api.deps import TenantContext, get_tenant_context, write_audit_log
from app.models.core import CustomFieldDefinition, CustomFieldEntity
from app.schemas.crm import (
    CustomFieldDefinitionCreate,
    CustomFieldDefinitionResponse,
    CustomFieldDefinitionUpdate,
)
from app.services.custom_fields import CustomFieldValidationService

router = APIRouter(prefix="/custom-fields", tags=["Campos Personalizados"])


@router.post("", response_model=CustomFieldDefinitionResponse, status_code=status.HTTP_201_CREATED)
async def create_custom_field(
    payload: CustomFieldDefinitionCreate,
    ctx: TenantContext = Depends(get_tenant_context),
) -> CustomFieldDefinition:
    existing = await ctx.session.execute(
        select(CustomFieldDefinition.id).where(
            CustomFieldDefinition.tenant_id == ctx.tenant_id,
            CustomFieldDefinition.entity_type == payload.entity_type,
            CustomFieldDefinition.field_key == payload.field_key,
        )
    )
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Já existe um campo com field_key '{payload.field_key}' para '{payload.entity_type.value}'.",
        )

    defn = CustomFieldDefinition(
        tenant_id=ctx.tenant_id,
        entity_type=payload.entity_type,
        field_key=payload.field_key,
        label=payload.label,
        description=payload.description,
        field_type=payload.field_type,
        options=[opt.model_dump(exclude_none=True) for opt in payload.options],
        default_value=payload.default_value,
        validation_rules=payload.validation_rules,
        group_name=payload.group_name,
        display_order=payload.display_order,
        is_required=payload.is_required,
        is_searchable=payload.is_searchable,
        is_visible_in_kanban=payload.is_visible_in_kanban,
        is_active=payload.is_active,
    )
    ctx.session.add(defn)
    await ctx.session.flush()
    await write_audit_log(
        ctx,
        entity_type="custom_field_definition",
        record_id=defn.id,
        action="create",
        changes_after={"field_key": defn.field_key, "entity_type": defn.entity_type.value},
    )
    await ctx.session.commit()
    await ctx.session.refresh(defn)
    await CustomFieldValidationService.invalidate_cache(ctx.tenant_id, defn.entity_type)
    return defn


@router.get("", response_model=list[CustomFieldDefinitionResponse])
async def list_custom_fields(
    entity_type: CustomFieldEntity | None = Query(None),
    only_active: bool = Query(True),
    ctx: TenantContext = Depends(get_tenant_context),
) -> list[CustomFieldDefinition]:
    stmt = select(CustomFieldDefinition).where(CustomFieldDefinition.tenant_id == ctx.tenant_id)
    if entity_type is not None:
        stmt = stmt.where(CustomFieldDefinition.entity_type == entity_type)
    if only_active:
        stmt = stmt.where(CustomFieldDefinition.is_active.is_(True))
    stmt = stmt.order_by(
        CustomFieldDefinition.entity_type.asc(),
        CustomFieldDefinition.display_order.asc(),
    )
    result = await ctx.session.execute(stmt)
    return list(result.scalars().all())


@router.get("/{field_id}", response_model=CustomFieldDefinitionResponse)
async def get_custom_field(
    field_id: uuid.UUID,
    ctx: TenantContext = Depends(get_tenant_context),
) -> CustomFieldDefinition:
    result = await ctx.session.execute(
        select(CustomFieldDefinition).where(
            CustomFieldDefinition.id == field_id,
            CustomFieldDefinition.tenant_id == ctx.tenant_id,
        )
    )
    defn = result.scalar_one_or_none()
    if defn is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Campo personalizado não encontrado.")
    return defn


@router.patch("/{field_id}", response_model=CustomFieldDefinitionResponse)
async def update_custom_field(
    field_id: uuid.UUID,
    payload: CustomFieldDefinitionUpdate,
    ctx: TenantContext = Depends(get_tenant_context),
) -> CustomFieldDefinition:
    result = await ctx.session.execute(
        select(CustomFieldDefinition).where(
            CustomFieldDefinition.id == field_id,
            CustomFieldDefinition.tenant_id == ctx.tenant_id,
        )
    )
    defn = result.scalar_one_or_none()
    if defn is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Campo personalizado não encontrado.")

    update_data = payload.model_dump(exclude_unset=True)
    if "options" in update_data and update_data["options"] is not None:
        update_data["options"] = [
            opt.model_dump(exclude_none=True) if hasattr(opt, "model_dump") else opt
            for opt in update_data["options"]
        ]

    for key, val in update_data.items():
        setattr(defn, key, val)

    await write_audit_log(
        ctx,
        entity_type="custom_field_definition",
        record_id=defn.id,
        action="update",
        changes_after=update_data,
    )
    await ctx.session.commit()
    await ctx.session.refresh(defn)
    await CustomFieldValidationService.invalidate_cache(ctx.tenant_id, defn.entity_type)
    return defn


@router.delete("/{field_id}", status_code=status.HTTP_204_NO_CONTENT, response_class=Response, response_model=None)
async def delete_custom_field(
    field_id: uuid.UUID,
    ctx: TenantContext = Depends(get_tenant_context),
) -> None:
    result = await ctx.session.execute(
        select(CustomFieldDefinition).where(
            CustomFieldDefinition.id == field_id,
            CustomFieldDefinition.tenant_id == ctx.tenant_id,
        )
    )
    defn = result.scalar_one_or_none()
    if defn is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Campo personalizado não encontrado.")

    entity_type = defn.entity_type
    await ctx.session.delete(defn)
    await write_audit_log(
        ctx,
        entity_type="custom_field_definition",
        record_id=field_id,
        action="delete",
    )
    await ctx.session.commit()
    await CustomFieldValidationService.invalidate_cache(ctx.tenant_id, entity_type)


@router.post("/validate-preview/{entity_type}")
async def preview_custom_fields_validation(
    entity_type: CustomFieldEntity,
    custom_fields: dict[str, Any],
    ctx: TenantContext = Depends(get_tenant_context),
) -> dict[str, Any]:
    normalized = await CustomFieldValidationService.validate_and_normalize(
        session=ctx.session,
        tenant_id=ctx.tenant_id,
        entity_type=entity_type,
        incoming_custom_fields=custom_fields,
    )
    return {"valid": True, "normalized_custom_fields": normalized}
