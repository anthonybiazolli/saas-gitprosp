"""Rotas FastAPI do Bloco 11: Conector Real Evolution API v2 & WhatsApp Produção."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Body, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import TenantContext, get_tenant_context
from app.db.session import get_db_session
from app.schemas.evolution_bridge import (
    EvolutionConfigOut,
    EvolutionConfigUpdate,
    EvolutionConnectionStatusOut,
    EvolutionQrCodeOut,
    EvolutionTestMessageRequest,
    EvolutionWebhookSetupRequest,
    OmnichannelMessageLogOut,
)
from app.services import evolution_bridge_service

router = APIRouter()


@router.get("/config", response_model=EvolutionConfigOut)
async def get_evolution_config(
    ctx: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(get_db_session),
) -> EvolutionConfigOut:
    cfg = await evolution_bridge_service.ensure_evolution_config(session, ctx.tenant_id)
    await session.commit()
    return evolution_bridge_service.serialize_config(cfg)


@router.put("/config", response_model=EvolutionConfigOut)
async def update_evolution_config(
    payload: EvolutionConfigUpdate,
    ctx: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(get_db_session),
) -> EvolutionConfigOut:
    cfg = await evolution_bridge_service.update_evolution_config(session, ctx.tenant_id, payload)
    await session.commit()
    return evolution_bridge_service.serialize_config(cfg)


@router.get("/status", response_model=EvolutionConnectionStatusOut)
async def get_evolution_status(
    ctx: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(get_db_session),
) -> EvolutionConnectionStatusOut:
    res = await evolution_bridge_service.check_instance_status(session, ctx.tenant_id)
    await session.commit()
    return res


@router.get("/qrcode", response_model=EvolutionQrCodeOut)
async def get_evolution_qrcode(
    ctx: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(get_db_session),
) -> EvolutionQrCodeOut:
    return await evolution_bridge_service.fetch_instance_qrcode(session, ctx.tenant_id)


@router.post("/configure-webhook")
async def configure_evolution_webhook(
    payload: EvolutionWebhookSetupRequest,
    ctx: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    res = await evolution_bridge_service.register_webhook_on_evolution(
        session, ctx.tenant_id, payload.public_base_url
    )
    await session.commit()
    return res


@router.post("/send-test", response_model=OmnichannelMessageLogOut)
async def send_evolution_test_message(
    payload: EvolutionTestMessageRequest,
    ctx: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(get_db_session),
) -> OmnichannelMessageLogOut:
    row = await evolution_bridge_service.send_whatsapp_text(
        session, ctx.tenant_id, payload.phone_number, payload.message
    )
    await session.commit()
    return OmnichannelMessageLogOut.model_validate(row)


@router.get("/messages", response_model=list[OmnichannelMessageLogOut])
async def list_evolution_messages(
    limit: int = Query(default=40, ge=1, le=200),
    ctx: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(get_db_session),
) -> list[OmnichannelMessageLogOut]:
    rows = await evolution_bridge_service.list_message_logs(session, ctx.tenant_id, limit=limit)
    return [OmnichannelMessageLogOut.model_validate(r) for r in rows]


@router.post("/webhook/{tenant_id}")
async def receive_evolution_webhook(
    tenant_id: uuid.UUID,
    payload: dict[str, Any] = Body(...),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    res = await evolution_bridge_service.handle_evolution_inbound_webhook(session, tenant_id, payload)
    await session.commit()
    return res
