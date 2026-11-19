"""Camada de serviço do Módulo 10: Exportações CSV, Timeline Unificada e Saúde do Sistema."""

from __future__ import annotations

import csv
import io
import time
import uuid
from datetime import datetime, timezone

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.automation_engine import (
    AutomationExecutionLog,
    AutomationRule,
    OutboundDispatchQueue,
    WebhookEndpointConfig,
)
from app.models.billing_commissions import (
    BillingInvoice,
    BillingPaymentEvent,
    SalesCommission,
)
from app.models.crm_deals import CrmContract, CrmDeal
from app.models.saas_gateway import InboundWebhookReceipt
from app.models.team_portal import CustomerPortalToken, SalesTeamMember
from app.schemas.governance import ModuleHealthMatrixOut, UnifiedTimelineEventOut


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _to_csv_utf8_bom(headers: list[str], rows: list[list[object]]) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf, delimiter=";", quoting=csv.QUOTE_MINIMAL)
    writer.writerow(headers)
    for row in rows:
        writer.writerow(["" if v is None else str(v) for v in row])
    return "\ufeff" + buf.getvalue()


async def export_invoices_csv(session: AsyncSession, tenant_id: uuid.UUID) -> str:
    stmt = (
        select(BillingInvoice)
        .where(BillingInvoice.tenant_id == tenant_id)
        .order_by(BillingInvoice.created_at.desc())
    )
    invoices = list((await session.execute(stmt)).scalars().all())

    headers = [
        "numero_fatura",
        "cliente",
        "email_cliente",
        "documento_cliente",
        "descricao",
        "valor_bruto",
        "desconto",
        "valor_liquido",
        "moeda",
        "meio_pagamento",
        "status",
        "vencimento",
        "data_pagamento",
        "closer",
        "sdr",
        "criado_em",
    ]
    rows: list[list[object]] = []
    for inv in invoices:
        rows.append(
            [
                inv.invoice_number,
                inv.customer_name,
                inv.customer_email,
                inv.customer_document,
                inv.description,
                inv.amount,
                inv.discount_amount,
                inv.net_amount,
                inv.currency,
                inv.payment_method.value,
                inv.status.value,
                inv.due_date.isoformat() if inv.due_date else "",
                inv.paid_at.isoformat() if inv.paid_at else "",
                inv.closer_name,
                inv.sdr_name,
                inv.created_at.isoformat() if inv.created_at else "",
            ]
        )
    return _to_csv_utf8_bom(headers, rows)


async def export_commissions_csv(session: AsyncSession, tenant_id: uuid.UUID) -> str:
    stmt = (
        select(SalesCommission)
        .where(SalesCommission.tenant_id == tenant_id)
        .order_by(SalesCommission.created_at.desc())
    )
    comms = list((await session.execute(stmt)).scalars().all())

    headers = [
        "id_comissao",
        "beneficiario",
        "perfil",
        "valor_base",
        "valor_comissao",
        "status",
        "data_repasse",
        "observacoes",
        "criado_em",
    ]
    rows: list[list[object]] = []
    for c in comms:
        rows.append(
            [
                str(c.id),
                c.beneficiary_name,
                c.role_type.value,
                c.base_amount,
                c.commission_amount,
                c.status.value,
                c.paid_at.isoformat() if c.paid_at else "",
                c.notes,
                c.created_at.isoformat() if c.created_at else "",
            ]
        )
    return _to_csv_utf8_bom(headers, rows)


async def export_deals_csv(session: AsyncSession, tenant_id: uuid.UUID) -> str:
    stmt = (
        select(CrmDeal)
        .where(CrmDeal.tenant_id == tenant_id)
        .order_by(CrmDeal.created_at.desc())
    )
    deals = list((await session.execute(stmt)).scalars().all())

    headers = [
        "id_negocio",
        "titulo",
        "empresa_cliente",
        "contato_nome",
        "contato_email",
        "valor_estimado",
        "probabilidade_pct",
        "status",
        "responsavel",
        "fechado_em",
        "criado_em",
    ]
    rows: list[list[object]] = []
    for d in deals:
        rows.append(
            [
                str(d.id),
                d.title,
                d.company_name,
                d.contact_name,
                d.contact_email,
                d.amount,
                d.probability,
                d.status.value,
                d.owner_name,
                d.closed_at.isoformat() if d.closed_at else "",
                d.created_at.isoformat() if d.created_at else "",
            ]
        )
    return _to_csv_utf8_bom(headers, rows)


async def get_unified_timeline(
    session: AsyncSession, tenant_id: uuid.UUID, limit: int = 60
) -> list[UnifiedTimelineEventOut]:
    events: list[UnifiedTimelineEventOut] = []

    bill_events = list(
        (
            await session.execute(
                select(BillingPaymentEvent)
                .where(BillingPaymentEvent.tenant_id == tenant_id)
                .order_by(BillingPaymentEvent.created_at.desc())
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )
    for ev in bill_events:
        events.append(
            UnifiedTimelineEventOut(
                id=f"bill_{ev.id}",
                category="BILLING",
                event_type=ev.event_type.value,
                status="RECORDED",
                summary=ev.notes or f"Evento financeiro {ev.event_type.value} (R$ {ev.amount})",
                details={"amount": str(ev.amount), "gateway_reference": ev.gateway_reference},
                occurred_at=ev.created_at,
            )
        )

    auto_logs = list(
        (
            await session.execute(
                select(AutomationExecutionLog)
                .where(AutomationExecutionLog.tenant_id == tenant_id)
                .order_by(AutomationExecutionLog.created_at.desc())
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )
    for lg in auto_logs:
        events.append(
            UnifiedTimelineEventOut(
                id=f"auto_{lg.id}",
                category="AUTOMATION",
                event_type=lg.event_type,
                status=lg.status,
                summary=lg.summary,
                details=lg.details or {},
                occurred_at=lg.created_at,
            )
        )

    wh_receipts = list(
        (
            await session.execute(
                select(InboundWebhookReceipt)
                .where(InboundWebhookReceipt.tenant_id == tenant_id)
                .order_by(InboundWebhookReceipt.created_at.desc())
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )
    for wh in wh_receipts:
        events.append(
            UnifiedTimelineEventOut(
                id=f"wh_{wh.id}",
                category="WEBHOOK_INBOUND",
                event_type=f"{wh.provider}:{wh.event_name}",
                status=wh.processing_status,
                summary=wh.summary,
                details={"provider": wh.provider, "external_event_id": wh.external_event_id},
                occurred_at=wh.created_at,
            )
        )

    events.sort(key=lambda item: item.occurred_at, reverse=True)
    return events[:limit]


async def get_health_matrix(session: AsyncSession, tenant_id: uuid.UUID) -> ModuleHealthMatrixOut:
    t0 = time.perf_counter()
    await session.execute(text("SELECT 1"))
    latency_ms = round((time.perf_counter() - t0) * 1000.0, 2)

    async def _count(model_cls) -> int:
        res = await session.execute(select(func.count(model_cls.id)).where(model_cls.tenant_id == tenant_id))
        return int(res.scalar_one() or 0)

    counters = {
        "crm_deals": await _count(CrmDeal),
        "crm_contracts": await _count(CrmContract),
        "billing_invoices": await _count(BillingInvoice),
        "sales_commissions": await _count(SalesCommission),
        "automation_rules": await _count(AutomationRule),
        "dispatch_queue_items": await _count(OutboundDispatchQueue),
        "outbound_webhooks": await _count(WebhookEndpointConfig),
        "team_members": await _count(SalesTeamMember),
        "customer_portal_links": await _count(CustomerPortalToken),
        "inbound_webhook_receipts": await _count(InboundWebhookReceipt),
    }

    modules_status = {
        "mod_1_4_core_outbound_sdr": "ONLINE",
        "mod_5_crm_deals_contracts": "ONLINE",
        "mod_6_billing_commissions": "ONLINE",
        "mod_7_automation_dunning_worker": "ONLINE",
        "mod_8_team_quotas_portal": "ONLINE",
        "mod_9_saas_metering_inbound_gateway": "ONLINE",
        "mod_10_governance_csv_exports": "ONLINE",
    }

    return ModuleHealthMatrixOut(
        tenant_id=tenant_id,
        checked_at=_utcnow(),
        database_connected=True,
        database_latency_ms=latency_ms,
        modules_status=modules_status,
        counters=counters,
    )
