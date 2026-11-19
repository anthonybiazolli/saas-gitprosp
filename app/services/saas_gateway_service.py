"""Camada de serviço do Módulo 9: Assinaturas SaaS, Metering e Webhooks Inbound."""

from __future__ import annotations

import secrets
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.automation_engine import (
    AutomationTriggerType,
    OutboundDispatchQueue,
    WebhookEndpointConfig,
)
from app.models.billing_commissions import BillingInvoice
from app.models.crm_deals import ContractStatus, CrmContract, CrmDeal, DealStatus
from app.models.saas_gateway import (
    InboundWebhookReceipt,
    SaasPlanTier,
    SubscriptionBillingStatus,
    TenantSaasSubscription,
)
from app.models.team_portal import SalesTeamMember
from app.schemas.automation_engine import AutomationTriggerRequest
from app.schemas.billing_commissions import InvoicePayRequest
from app.schemas.saas_gateway import (
    InboundWebhookResultOut,
    SubscriptionUpdateRequest,
    TenantUsageOut,
)
from app.services import automation_service, billing_service

PLAN_DEFAULTS: dict[SaasPlanTier, dict[str, Any]] = {
    SaasPlanTier.STARTER: {
        "monthly_price": Decimal("147.00"),
        "max_team_members": 5,
        "max_monthly_dispatches": 1000,
        "max_active_webhooks": 3,
    },
    SaasPlanTier.PRO: {
        "monthly_price": Decimal("297.00"),
        "max_team_members": 15,
        "max_monthly_dispatches": 5000,
        "max_active_webhooks": 10,
    },
    SaasPlanTier.ENTERPRISE: {
        "monthly_price": Decimal("797.00"),
        "max_team_members": 100,
        "max_monthly_dispatches": 50000,
        "max_active_webhooks": 50,
    },
}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


async def ensure_tenant_subscription(
    session: AsyncSession, tenant_id: uuid.UUID
) -> TenantSaasSubscription:
    stmt = select(TenantSaasSubscription).where(TenantSaasSubscription.tenant_id == tenant_id)
    sub = (await session.execute(stmt)).scalars().first()
    if sub is not None:
        return sub

    defaults = PLAN_DEFAULTS[SaasPlanTier.PRO]
    sub = TenantSaasSubscription(
        tenant_id=tenant_id,
        plan_tier=SaasPlanTier.PRO,
        billing_status=SubscriptionBillingStatus.ACTIVE,
        monthly_price=defaults["monthly_price"],
        max_team_members=defaults["max_team_members"],
        max_monthly_dispatches=defaults["max_monthly_dispatches"],
        max_active_webhooks=defaults["max_active_webhooks"],
        inbound_webhook_secret=f"inwh_{secrets.token_urlsafe(24)}",
        current_period_end=_utcnow() + timedelta(days=30),
    )
    session.add(sub)
    await session.flush()
    await session.refresh(sub)
    return sub


async def update_tenant_subscription(
    session: AsyncSession, tenant_id: uuid.UUID, req: SubscriptionUpdateRequest
) -> TenantSaasSubscription:
    sub = await ensure_tenant_subscription(session, tenant_id)
    data = req.model_dump(exclude_unset=True)

    if "plan_tier" in data and data["plan_tier"] is not None:
        tier: SaasPlanTier = data["plan_tier"]
        sub.plan_tier = tier
        preset = PLAN_DEFAULTS[tier]
        if "monthly_price" not in data:
            sub.monthly_price = preset["monthly_price"]
        if "max_team_members" not in data:
            sub.max_team_members = preset["max_team_members"]
        if "max_monthly_dispatches" not in data:
            sub.max_monthly_dispatches = preset["max_monthly_dispatches"]
        if "max_active_webhooks" not in data:
            sub.max_active_webhooks = preset["max_active_webhooks"]

    for field, value in data.items():
        if value is not None:
            setattr(sub, field, value)

    await session.flush()
    await session.refresh(sub)
    return sub


async def get_tenant_usage(session: AsyncSession, tenant_id: uuid.UUID) -> TenantUsageOut:
    sub = await ensure_tenant_subscription(session, tenant_id)
    month_start = _utcnow().replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    members_cnt = int(
        (
            await session.execute(
                select(func.count(SalesTeamMember.id)).where(
                    SalesTeamMember.tenant_id == tenant_id,
                    SalesTeamMember.is_active.is_(True),
                )
            )
        ).scalar_one()
        or 0
    )
    dispatches_cnt = int(
        (
            await session.execute(
                select(func.count(OutboundDispatchQueue.id)).where(
                    OutboundDispatchQueue.tenant_id == tenant_id,
                    OutboundDispatchQueue.created_at >= month_start,
                )
            )
        ).scalar_one()
        or 0
    )
    webhooks_cnt = int(
        (
            await session.execute(
                select(func.count(WebhookEndpointConfig.id)).where(
                    WebhookEndpointConfig.tenant_id == tenant_id,
                    WebhookEndpointConfig.is_active.is_(True),
                )
            )
        ).scalar_one()
        or 0
    )
    open_deals_cnt = int(
        (
            await session.execute(
                select(func.count(CrmDeal.id)).where(
                    CrmDeal.tenant_id == tenant_id,
                    CrmDeal.status == DealStatus.OPEN,
                )
            )
        ).scalar_one()
        or 0
    )
    active_contracts_cnt = int(
        (
            await session.execute(
                select(func.count(CrmContract.id)).where(
                    CrmContract.tenant_id == tenant_id,
                    CrmContract.status.in_([ContractStatus.SIGNED, ContractStatus.ACTIVE]),
                )
            )
        ).scalar_one()
        or 0
    )
    invoices_cnt = int(
        (
            await session.execute(
                select(func.count(BillingInvoice.id)).where(BillingInvoice.tenant_id == tenant_id)
            )
        ).scalar_one()
        or 0
    )

    team_pct = round((members_cnt / sub.max_team_members) * 100.0, 2) if sub.max_team_members > 0 else 0.0
    disp_pct = (
        round((dispatches_cnt / sub.max_monthly_dispatches) * 100.0, 2)
        if sub.max_monthly_dispatches > 0
        else 0.0
    )

    return TenantUsageOut(
        tenant_id=tenant_id,
        plan_tier=sub.plan_tier,
        billing_status=sub.billing_status,
        active_team_members=members_cnt,
        max_team_members=sub.max_team_members,
        team_usage_percent=team_pct,
        dispatches_this_month=dispatches_cnt,
        max_monthly_dispatches=sub.max_monthly_dispatches,
        dispatches_usage_percent=disp_pct,
        active_webhooks=webhooks_cnt,
        max_active_webhooks=sub.max_active_webhooks,
        open_deals_count=open_deals_cnt,
        active_contracts_count=active_contracts_cnt,
        invoices_count=invoices_cnt,
    )


def _extract_invoice_lookup_keys(payload: dict[str, Any]) -> tuple[str | None, str | None]:
    """Extrai invoice_number e external_charge_id de payloads planos ou aninhados (Asaas/MP/Stripe/Genérico)."""
    nested = payload.get("payment") or payload.get("data") or {}
    if not isinstance(nested, dict):
        nested = {}

    inv_num = (
        payload.get("invoice_number")
        or payload.get("externalReference")
        or payload.get("external_reference")
        or nested.get("invoice_number")
        or nested.get("externalReference")
        or nested.get("external_reference")
    )
    charge_id = (
        payload.get("external_charge_id")
        or payload.get("charge_id")
        or nested.get("id")
        or payload.get("payment_id")
    )
    return (str(inv_num).strip() if inv_num else None, str(charge_id).strip() if charge_id else None)


def _is_payment_confirmed_event(event_name: str, payload: dict[str, Any]) -> bool:
    ev = event_name.strip().lower()
    status_val = str(
        payload.get("status")
        or (payload.get("payment") or {}).get("status")
        or (payload.get("data") or {}).get("status")
        or ""
    ).strip().lower()

    confirmed_keywords = {
        "payment_confirmed",
        "payment_received",
        "invoice.paid",
        "charge.succeeded",
        "pix.confirmed",
        "paid",
        "approved",
        "confirmed",
        "received",
    }
    return ev in confirmed_keywords or status_val in confirmed_keywords


async def process_inbound_webhook(
    session: AsyncSession,
    provider: str,
    payload: dict[str, Any],
    tenant_id: uuid.UUID | None = None,
    provided_secret: str | None = None,
) -> InboundWebhookResultOut:
    clean_provider = provider.strip().lower()[:60]
    event_name = str(
        payload.get("event")
        or payload.get("type")
        or payload.get("action")
        or "generic.event"
    )[:100]
    ext_event_id = str(payload.get("event_id") or payload.get("id") or "")[:140] or None

    # Idempotência por (provider, external_event_id)
    if ext_event_id:
        dup_stmt = select(InboundWebhookReceipt).where(
            InboundWebhookReceipt.provider == clean_provider,
            InboundWebhookReceipt.external_event_id == ext_event_id,
        )
        existing = (await session.execute(dup_stmt)).scalars().first()
        if existing is not None:
            return InboundWebhookResultOut(
                receipt_id=existing.id,
                provider=clean_provider,
                event_name=event_name,
                processing_status="DUPLICATE_IGNORED",
                summary=f"Evento {ext_event_id} já processado anteriormente.",
            )

    inv_num, charge_id = _extract_invoice_lookup_keys(payload)
    target_invoice: BillingInvoice | None = None

    if inv_num:
        q = select(BillingInvoice).where(BillingInvoice.invoice_number == inv_num)
        if tenant_id is not None:
            q = q.where(BillingInvoice.tenant_id == tenant_id)
        target_invoice = (await session.execute(q)).scalars().first()

    if target_invoice is None and charge_id:
        q = select(BillingInvoice).where(BillingInvoice.external_charge_id == charge_id)
        if tenant_id is not None:
            q = q.where(BillingInvoice.tenant_id == tenant_id)
        target_invoice = (await session.execute(q)).scalars().first()

    resolved_tenant_id = tenant_id or (target_invoice.tenant_id if target_invoice else None)

    if resolved_tenant_id is not None and provided_secret:
        sub = await ensure_tenant_subscription(session, resolved_tenant_id)
        if provided_secret != sub.inbound_webhook_secret:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Assinatura/segredo do webhook inbound inválido.",
            )

    settled_inv_number: str | None = None
    comms_count = 0
    automations_count = 0
    proc_status = "IGNORED"
    summary = f"Webhook '{event_name}' recebido de {clean_provider}."

    if target_invoice is not None and _is_payment_confirmed_event(event_name, payload):
        pay_res = await billing_service.pay_invoice(
            session,
            target_invoice.tenant_id,
            target_invoice.id,
            InvoicePayRequest(
                gateway_reference=charge_id or ext_event_id or f"wh_{clean_provider}",
                notes=f"Liquidação automática via webhook inbound ({clean_provider} / {event_name}).",
            ),
        )
        settled_inv_number = pay_res.invoice.invoice_number
        comms_count = len(pay_res.generated_commissions)

        phone = str((target_invoice.metadata_json or {}).get("customer_phone") or payload.get("customer_phone") or "")
        enq_cnt, _ = await automation_service.trigger_event(
            session,
            target_invoice.tenant_id,
            AutomationTriggerRequest(
                event_type=AutomationTriggerType.INVOICE_PAID,
                destination_phone=phone or None,
                destination_email=target_invoice.customer_email,
                context={
                    "invoice_id": str(target_invoice.id),
                    "invoice_number": target_invoice.invoice_number,
                    "customer_name": target_invoice.customer_name,
                    "currency": target_invoice.currency,
                    "net_amount": str(target_invoice.net_amount),
                },
                process_immediately=True,
            ),
        )
        automations_count = enq_cnt
        proc_status = "PROCESSED"
        summary = (
            f"Fatura {settled_inv_number} liquidada via {clean_provider}; "
            f"{comms_count} comissão(ões) gerada(s) e {automations_count} automação(ões) acionada(s)."
        )
    elif clean_provider == "evolution" and resolved_tenant_id is not None:
        proc_status = "PROCESSED"
        summary = f"Evento WhatsApp Evolution '{event_name}' registrado com sucesso."

    receipt = InboundWebhookReceipt(
        tenant_id=resolved_tenant_id,
        provider=clean_provider,
        event_name=event_name,
        external_event_id=ext_event_id,
        processing_status=proc_status,
        summary=summary,
        raw_payload=payload,
    )
    session.add(receipt)
    await session.flush()
    await session.refresh(receipt)

    return InboundWebhookResultOut(
        receipt_id=receipt.id,
        provider=clean_provider,
        event_name=event_name,
        processing_status=proc_status,
        summary=summary,
        settled_invoice_number=settled_inv_number,
        generated_commissions_count=comms_count,
        enqueued_automations_count=automations_count,
    )


async def list_inbound_receipts(
    session: AsyncSession, tenant_id: uuid.UUID, limit: int = 50
) -> list[InboundWebhookReceipt]:
    stmt = (
        select(InboundWebhookReceipt)
        .where(InboundWebhookReceipt.tenant_id == tenant_id)
        .order_by(InboundWebhookReceipt.created_at.desc())
        .limit(limit)
    )
    return list((await session.execute(stmt)).scalars().all())
