"""Rotas FastAPI do Módulo 9: Planos SaaS, Metering & Gateway de Webhooks Inbound."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, Header, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import TenantContext, get_tenant_context
from app.db.session import get_db_session
from app.schemas.saas_gateway import (
    InboundWebhookReceiptOut,
    InboundWebhookResultOut,
    SubscriptionOut,
    SubscriptionUpdateRequest,
    TenantUsageOut,
)
from app.services import saas_gateway_service

router = APIRouter()


@router.get("/subscription", response_model=SubscriptionOut)
async def get_subscription(
    ctx: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(get_db_session),
) -> SubscriptionOut:
    sub = await saas_gateway_service.ensure_tenant_subscription(session, ctx.tenant_id)
    await session.commit()
    return SubscriptionOut.model_validate(sub)


@router.patch("/subscription", response_model=SubscriptionOut)
async def update_subscription(
    payload: SubscriptionUpdateRequest,
    ctx: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(get_db_session),
) -> SubscriptionOut:
    sub = await saas_gateway_service.update_tenant_subscription(session, ctx.tenant_id, payload)
    await session.commit()
    return SubscriptionOut.model_validate(sub)


@router.get("/usage", response_model=TenantUsageOut)
async def get_usage(
    ctx: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(get_db_session),
) -> TenantUsageOut:
    usage = await saas_gateway_service.get_tenant_usage(session, ctx.tenant_id)
    await session.commit()
    return usage


@router.get("/webhooks/receipts", response_model=list[InboundWebhookReceiptOut])
async def list_receipts(
    limit: int = Query(default=50, ge=1, le=200),
    ctx: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(get_db_session),
) -> list[InboundWebhookReceiptOut]:
    rows = await saas_gateway_service.list_inbound_receipts(session, ctx.tenant_id, limit=limit)
    return [InboundWebhookReceiptOut.model_validate(r) for r in rows]


@router.post("/webhooks/inbound/{provider}", response_model=InboundWebhookResultOut)
async def receive_authenticated_inbound_webhook(
    provider: str,
    payload: dict[str, Any] = Body(...),
    ctx: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(get_db_session),
) -> InboundWebhookResultOut:
    res = await saas_gateway_service.process_inbound_webhook(
        session, provider=provider, payload=payload, tenant_id=ctx.tenant_id
    )
    await session.commit()
    return res


@router.post("/webhooks/public/{provider}", response_model=InboundWebhookResultOut)
async def receive_public_gateway_webhook(
    provider: str,
    payload: dict[str, Any] = Body(...),
    x_webhook_secret: str | None = Header(default=None, alias="X-Webhook-Secret"),
    secret: str | None = Query(default=None),
    session: AsyncSession = Depends(get_db_session),
) -> InboundWebhookResultOut:
    res = await saas_gateway_service.process_inbound_webhook(
        session,
        provider=provider,
        payload=payload,
        tenant_id=None,
        provided_secret=x_webhook_secret or secret,
    )
    await session.commit()
    return res
