from __future__ import annotations

import os
import uuid
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import TenantContext, get_tenant_context, write_audit_log
from app.db.session import get_db_session
from app.models.whatsapp import WhatsAppInstance
from app.schemas.whatsapp import (
    EvolutionWebhookAck,
    WhatsAppInstanceCreate,
    WhatsAppInstanceResponse,
    WhatsAppInstanceUpdate,
    WhatsAppSendTestRequest,
)
from app.services.evolution_client import EvolutionAPIClient
from app.services.whatsapp_service import WhatsAppService

router = APIRouter(prefix="/whatsapp", tags=["WhatsApp & Evolution API"])
webhook_router = APIRouter(prefix="/webhooks", tags=["Webhooks Externos"])


@router.post("/instances", response_model=WhatsAppInstanceResponse, status_code=status.HTTP_201_CREATED)
async def register_whatsapp_instance(
    payload: WhatsAppInstanceCreate,
    ctx: TenantContext = Depends(get_tenant_context),
) -> WhatsAppInstance:
    existing_tid = await WhatsAppService.resolve_tenant_for_instance(
        ctx.session, payload.instance_name
    )
    if existing_tid is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"A instância '{payload.instance_name}' já está registrada no sistema.",
        )

    data = payload.model_dump(exclude={"auto_configure_webhook", "webhook_public_base_url"})
    instance = WhatsAppInstance(tenant_id=ctx.tenant_id, **data)
    ctx.session.add(instance)
    await ctx.session.flush()

    if payload.auto_configure_webhook:
        public_base = (
            payload.webhook_public_base_url
            or os.getenv("EVOLUTION_WEBHOOK_PUBLIC_BASE_URL")
            or "http://192.168.0.9:8001"
        ).rstrip("/")
        webhook_url = f"{public_base}/api/v1/webhooks/evolution/{instance.instance_name}"
        if instance.webhook_secret:
            webhook_url = f"{webhook_url}?secret={instance.webhook_secret}"
        evo = EvolutionAPIClient(
            base_url=instance.evolution_api_url,
            api_key=instance.evolution_api_key,
        )
        try:
            await evo.configure_webhook(instance.instance_name, webhook_url)
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Instância salva no banco, mas falhou ao configurar webhook na Evolution API: {exc}",
            ) from exc

    await write_audit_log(
        ctx,
        entity_type="whatsapp_instance",
        record_id=instance.id,
        action="create",
        changes_after={"instance_name": instance.instance_name},
    )
    await ctx.session.commit()
    await ctx.session.refresh(instance)
    return instance


@router.get("/instances", response_model=list[WhatsAppInstanceResponse])
async def list_whatsapp_instances(
    ctx: TenantContext = Depends(get_tenant_context),
) -> list[WhatsAppInstance]:
    stmt = (
        select(WhatsAppInstance)
        .where(WhatsAppInstance.tenant_id == ctx.tenant_id)
        .order_by(WhatsAppInstance.is_primary.desc(), WhatsAppInstance.created_at.asc())
    )
    return list((await ctx.session.execute(stmt)).scalars().all())


@router.patch("/instances/{instance_id}", response_model=WhatsAppInstanceResponse)
async def update_whatsapp_instance(
    instance_id: uuid.UUID,
    payload: WhatsAppInstanceUpdate,
    ctx: TenantContext = Depends(get_tenant_context),
) -> WhatsAppInstance:
    instance = (
        await ctx.session.execute(
            select(WhatsAppInstance).where(
                WhatsAppInstance.id == instance_id,
                WhatsAppInstance.tenant_id == ctx.tenant_id,
            )
        )
    ).scalar_one_or_none()
    if instance is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Instância WhatsApp não encontrada.")

    for key, val in payload.model_dump(exclude_unset=True).items():
        setattr(instance, key, val)

    await ctx.session.commit()
    await ctx.session.refresh(instance)
    return instance


@router.get("/instances/{instance_id}/status")
async def get_whatsapp_instance_connection_status(
    instance_id: uuid.UUID,
    include_qr: bool = Query(default=False, description="Se true e desconectado, retorna base64 do QR Code"),
    ctx: TenantContext = Depends(get_tenant_context),
) -> dict[str, Any]:
    instance = (
        await ctx.session.execute(
            select(WhatsAppInstance).where(
                WhatsAppInstance.id == instance_id,
                WhatsAppInstance.tenant_id == ctx.tenant_id,
            )
        )
    ).scalar_one_or_none()
    if instance is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Instância WhatsApp não encontrada.")

    evo = EvolutionAPIClient(
        base_url=instance.evolution_api_url,
        api_key=instance.evolution_api_key,
    )
    try:
        state_data = await evo.get_connection_state(instance.instance_name)
        result: dict[str, Any] = {
            "instance_name": instance.instance_name,
            "connection_state": state_data,
        }
        state_str = str(state_data.get("instance", {}).get("state") or state_data.get("state") or "")
        if include_qr and state_str.lower() != "open":
            result["connect_qr"] = await evo.get_connect_qr(instance.instance_name)
        return result
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Erro ao consultar Evolution API: {exc}",
        ) from exc


@router.post("/send-test")
async def send_whatsapp_test_message(
    payload: WhatsAppSendTestRequest,
    ctx: TenantContext = Depends(get_tenant_context),
) -> dict[str, Any]:
    instance = await WhatsAppService.get_active_instance_by_name(
        ctx.session, ctx.tenant_id, payload.instance_name
    )
    if instance is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Instância '{payload.instance_name}' não encontrada ou inativa neste tenant.",
        )

    evo = EvolutionAPIClient(
        base_url=instance.evolution_api_url,
        api_key=instance.evolution_api_key,
    )
    try:
        resp = await evo.send_text(
            instance_name=instance.instance_name,
            number=payload.number,
            text=payload.text,
            delay_ms=500,
        )
        return {"status": "sent", "evolution_response": resp}
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(exc),
        ) from exc


@webhook_router.post("/evolution", response_model=EvolutionWebhookAck)
async def evolution_webhook_global(
    request: Request,
    secret: str | None = Query(default=None),
    x_webhook_secret: str | None = Header(default=None, alias="X-Webhook-Secret"),
    session: AsyncSession = Depends(get_db_session),
) -> EvolutionWebhookAck:
    payload = await request.json()
    return await WhatsAppService.process_evolution_webhook(
        session=session,
        payload=payload,
        path_instance_name=None,
        provided_secret=secret or x_webhook_secret,
    )


@webhook_router.post("/evolution/{instance_name}", response_model=EvolutionWebhookAck)
async def evolution_webhook_by_instance(
    instance_name: str,
    request: Request,
    secret: str | None = Query(default=None),
    x_webhook_secret: str | None = Header(default=None, alias="X-Webhook-Secret"),
    session: AsyncSession = Depends(get_db_session),
) -> EvolutionWebhookAck:
    payload = await request.json()
    return await WhatsAppService.process_evolution_webhook(
        session=session,
        payload=payload,
        path_instance_name=instance_name,
        provided_secret=secret or x_webhook_secret,
    )
