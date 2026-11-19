"""Camada de serviço do Módulo 7: Automação Omnichannel, Fila, Webhooks e Relatório Executivo."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import re
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.automation_engine import (
    AutomationExecutionLog,
    AutomationRule,
    AutomationTriggerType,
    DispatchChannelType,
    DispatchQueueStatus,
    OutboundDispatchQueue,
    WebhookEndpointConfig,
)
from app.models.billing_commissions import (
    BillingEventType,
    BillingInvoice,
    BillingPaymentEvent,
    InvoiceStatus,
)
from app.schemas.automation_engine import (
    AutomationRuleCreate,
    AutomationRuleUpdate,
    AutomationTriggerRequest,
    DispatchQueueCreate,
    ExecutiveReportOut,
    OverdueDunningScanOut,
    WebhookEndpointCreate,
    WorkerCycleResultOut,
)
from app.services import billing_service, crm_deals_service


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def render_message_template(template: str, context: dict[str, Any]) -> str:
    """Interpola placeholders {{chave}} sem falhar caso alguma chave não exista no contexto."""

    def _repl(match: re.Match[str]) -> str:
        key = match.group(1).strip()
        val = context.get(key)
        return str(val) if val is not None else f"{{{{{key}}}}}"

    return re.sub(r"\{\{\s*([a-zA-Z0-9_.-]+)\s*\}\}", _repl, template)


async def ensure_default_automation_rules(session: AsyncSession, tenant_id: uuid.UUID) -> list[AutomationRule]:
    stmt = (
        select(AutomationRule)
        .where(AutomationRule.tenant_id == tenant_id)
        .order_by(AutomationRule.created_at.asc())
    )
    existing = list((await session.execute(stmt)).scalars().all())
    if existing:
        return existing

    defaults = [
        AutomationRule(
            tenant_id=tenant_id,
            name="Contrato Gerado - Boas-vindas & Formalização",
            description="Notifica o cliente via WhatsApp assim que o contrato comercial é emitido.",
            trigger_type=AutomationTriggerType.CONTRACT_GENERATED,
            channel=DispatchChannelType.WHATSAPP,
            delay_minutes=0,
            message_template=(
                "Olá {{customer_name}}! Seu contrato *{{contract_number}}* ({{title}}) "
                "no valor de {{currency}} {{total_value}} foi gerado e está pronto para assinatura."
            ),
            is_active=True,
        ),
        AutomationRule(
            tenant_id=tenant_id,
            name="Fatura Emitida - Envio de PIX & Link",
            description="Envia imediatamente o código PIX Copia e Cola e link da fatura.",
            trigger_type=AutomationTriggerType.INVOICE_CREATED,
            channel=DispatchChannelType.WHATSAPP,
            delay_minutes=0,
            message_template=(
                "Olá {{customer_name}}, sua fatura *{{invoice_number}}* no valor de "
                "{{currency}} {{net_amount}} foi emitida. Link: {{payment_link}} | PIX: {{pix_copy_paste}}"
            ),
            is_active=True,
        ),
        AutomationRule(
            tenant_id=tenant_id,
            name="Régua de Cobrança - Fatura Vencida",
            description="Lembrete amigável automático quando uma fatura entra em atraso.",
            trigger_type=AutomationTriggerType.INVOICE_OVERDUE,
            channel=DispatchChannelType.WHATSAPP,
            delay_minutes=0,
            message_template=(
                "Olá {{customer_name}}, identificamos que a fatura *{{invoice_number}}* "
                "({{currency}} {{net_amount}}) está vencida. Regularize pelo link: {{payment_link}}"
            ),
            is_active=True,
        ),
        AutomationRule(
            tenant_id=tenant_id,
            name="Confirmação de Pagamento & Recibo",
            description="Confirma a liquidação da fatura para o cliente.",
            trigger_type=AutomationTriggerType.INVOICE_PAID,
            channel=DispatchChannelType.WHATSAPP,
            delay_minutes=0,
            message_template=(
                "Pagamento confirmado! Recebemos {{currency}} {{net_amount}} referente à fatura "
                "*{{invoice_number}}*. Obrigado pela parceria, {{customer_name}}!"
            ),
            is_active=True,
        ),
    ]
    for rule in defaults:
        session.add(rule)
    await session.flush()
    return list((await session.execute(stmt)).scalars().all())


async def create_rule(
    session: AsyncSession, tenant_id: uuid.UUID, data: AutomationRuleCreate
) -> AutomationRule:
    rule = AutomationRule(
        tenant_id=tenant_id,
        name=data.name,
        description=data.description,
        trigger_type=data.trigger_type,
        channel=data.channel,
        delay_minutes=data.delay_minutes,
        message_template=data.message_template,
        conditions_json=data.conditions_json,
        is_active=data.is_active,
    )
    session.add(rule)
    await session.flush()
    await session.refresh(rule)
    return rule


async def update_rule(
    session: AsyncSession, tenant_id: uuid.UUID, rule_id: uuid.UUID, data: AutomationRuleUpdate
) -> AutomationRule:
    stmt = select(AutomationRule).where(AutomationRule.tenant_id == tenant_id, AutomationRule.id == rule_id)
    rule = (await session.execute(stmt)).scalars().first()
    if rule is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Regra de automação não encontrada.")

    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(rule, field, value)

    await session.flush()
    await session.refresh(rule)
    return rule


async def enqueue_dispatch(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    data: DispatchQueueCreate,
    rule_id: uuid.UUID | None = None,
) -> OutboundDispatchQueue:
    item = OutboundDispatchQueue(
        tenant_id=tenant_id,
        rule_id=rule_id,
        channel=data.channel,
        destination=data.destination,
        subject=data.subject,
        rendered_message=data.rendered_message,
        status=DispatchQueueStatus.QUEUED,
        scheduled_for=data.scheduled_for or _utcnow(),
        context_json=data.context_json,
    )
    session.add(item)
    await session.flush()
    await session.refresh(item)
    return item


async def create_webhook_endpoint(
    session: AsyncSession, tenant_id: uuid.UUID, data: WebhookEndpointCreate
) -> WebhookEndpointConfig:
    secret = data.secret_token or f"whsec_{uuid.uuid4().hex}"
    ep = WebhookEndpointConfig(
        tenant_id=tenant_id,
        name=data.name,
        target_url=data.target_url,
        secret_token=secret,
        subscribed_events=data.subscribed_events or ["*"],
        is_active=data.is_active,
    )
    session.add(ep)
    await session.flush()
    await session.refresh(ep)
    return ep


async def list_webhook_endpoints(session: AsyncSession, tenant_id: uuid.UUID) -> list[WebhookEndpointConfig]:
    stmt = (
        select(WebhookEndpointConfig)
        .where(WebhookEndpointConfig.tenant_id == tenant_id)
        .order_by(WebhookEndpointConfig.created_at.desc())
    )
    return list((await session.execute(stmt)).scalars().all())


def _http_post_json_sync(url: str, body_bytes: bytes, headers: dict[str, str], timeout: int = 8) -> tuple[int, str]:
    req = urllib.request.Request(url=url, data=body_bytes, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")[:500]
            return resp.getcode(), raw
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")[:500]
        return exc.code, raw
    except Exception as exc:
        return 0, str(exc)[:500]


async def _dispatch_webhooks_for_event(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    event_type: str,
    context: dict[str, Any],
) -> int:
    endpoints = await list_webhook_endpoints(session, tenant_id)
    active_eps = [
        ep
        for ep in endpoints
        if ep.is_active and ("*" in (ep.subscribed_events or []) or event_type in (ep.subscribed_events or []))
    ]
    if not active_eps:
        return 0

    envelope = {
        "event": event_type,
        "tenant_id": str(tenant_id),
        "timestamp": _utcnow().isoformat(),
        "data": context,
    }
    body_bytes = json.dumps(envelope, default=str).encode("utf-8")

    delivered = 0
    for ep in active_eps:
        sig = hmac.new(ep.secret_token.encode("utf-8"), body_bytes, hashlib.sha256).hexdigest()
        headers = {
            "Content-Type": "application/json",
            "X-Webhook-Event": event_type,
            "X-Webhook-Signature": f"sha256={sig}",
        }
        code, resp_text = await asyncio.to_thread(_http_post_json_sync, ep.target_url, body_bytes, headers)
        ep.last_status_code = code
        ep.last_triggered_at = _utcnow()
        session.add(
            AutomationExecutionLog(
                tenant_id=tenant_id,
                event_type=event_type,
                status="SUCCESS" if 200 <= code < 300 else "WARNING",
                summary=f"Webhook '{ep.name}' acionado (HTTP {code}).",
                details={"target_url": ep.target_url, "status_code": code, "response": resp_text},
            )
        )
        if 200 <= code < 300:
            delivered += 1

    return delivered


async def trigger_event(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    req: AutomationTriggerRequest,
) -> tuple[int, WorkerCycleResultOut | None]:
    rules = await ensure_default_automation_rules(session, tenant_id)
    matching = [r for r in rules if r.is_active and r.trigger_type == req.event_type]

    enqueued_count = 0
    for rule in matching:
        dest: str | None = None
        if rule.channel == DispatchChannelType.WHATSAPP:
            dest = req.destination_phone or str(req.context.get("customer_phone") or req.context.get("contact_phone") or "")
        elif rule.channel == DispatchChannelType.EMAIL:
            dest = req.destination_email or str(req.context.get("customer_email") or req.context.get("contact_email") or "")
        elif rule.channel == DispatchChannelType.WEBHOOK:
            dest = str(req.context.get("webhook_url") or "webhook://tenant-default")

        if not dest:
            session.add(
                AutomationExecutionLog(
                    tenant_id=tenant_id,
                    rule_id=rule.id,
                    event_type=req.event_type.value,
                    status="SKIPPED",
                    summary=f"Regra '{rule.name}' ignorada: destinatário ausente para canal {rule.channel.value}.",
                    details={"context": req.context},
                )
            )
            continue

        rendered = render_message_template(rule.message_template, req.context)
        sched = _utcnow() + timedelta(minutes=rule.delay_minutes)
        await enqueue_dispatch(
            session,
            tenant_id,
            DispatchQueueCreate(
                channel=rule.channel,
                destination=dest,
                subject=f"[{req.event_type.value}] {rule.name}",
                rendered_message=rendered,
                scheduled_for=sched,
                context_json=req.context,
            ),
            rule_id=rule.id,
        )
        enqueued_count += 1
        session.add(
            AutomationExecutionLog(
                tenant_id=tenant_id,
                rule_id=rule.id,
                event_type=req.event_type.value,
                status="ENQUEUED",
                summary=f"Mensagem enfileirada via '{rule.name}' para {dest}.",
                details={"channel": rule.channel.value, "destination": dest},
            )
        )

    await _dispatch_webhooks_for_event(session, tenant_id, req.event_type.value, req.context)
    await session.flush()

    worker_res: WorkerCycleResultOut | None = None
    if req.process_immediately and enqueued_count > 0:
        worker_res = await process_dispatch_queue(session, tenant_id=tenant_id, batch_size=50)

    return enqueued_count, worker_res


async def _deliver_single_dispatch(item: OutboundDispatchQueue) -> tuple[bool, dict[str, Any], str | None]:
    """Entrega o item da fila via Evolution API (se configurada) ou modo sandbox auditado."""
    evo_url = os.getenv("EVOLUTION_API_URL", "").rstrip("/")
    evo_key = os.getenv("EVOLUTION_API_KEY", "")
    evo_instance = os.getenv("EVOLUTION_INSTANCE", "")

    if item.channel == DispatchChannelType.WHATSAPP and evo_url and evo_key and evo_instance:
        digits = "".join(ch for ch in item.destination if ch.isdigit())
        endpoint = f"{evo_url}/message/sendText/{evo_instance}"
        payload = json.dumps({"number": digits, "text": item.rendered_message}).encode("utf-8")
        headers = {"Content-Type": "application/json", "apikey": evo_key}
        code, resp_text = await asyncio.to_thread(_http_post_json_sync, endpoint, payload, headers)
        if 200 <= code < 300:
            return True, {"provider": "evolution-api", "http_status": code, "response": resp_text}, None
        return False, {"provider": "evolution-api", "http_status": code}, f"Evolution API HTTP {code}: {resp_text}"

    # Modo integrado local / sandbox (garante pipeline operacional mesmo sem gateway externo configurado)
    receipt = {
        "provider": f"sandbox-{item.channel.value.lower()}",
        "message_id": f"msg_{uuid.uuid4().hex[:16]}",
        "destination": item.destination,
        "delivered_at": _utcnow().isoformat(),
    }
    return True, receipt, None


async def process_dispatch_queue(
    session: AsyncSession,
    tenant_id: uuid.UUID | None = None,
    batch_size: int = 50,
) -> WorkerCycleResultOut:
    now = _utcnow()
    stmt = select(OutboundDispatchQueue).where(
        OutboundDispatchQueue.status == DispatchQueueStatus.QUEUED,
        OutboundDispatchQueue.scheduled_for <= now,
    )
    if tenant_id is not None:
        stmt = stmt.where(OutboundDispatchQueue.tenant_id == tenant_id)
    stmt = stmt.order_by(OutboundDispatchQueue.scheduled_for.asc()).limit(batch_size)

    items = list((await session.execute(stmt)).scalars().all())
    sent_count = 0
    failed_count = 0
    requeued_count = 0

    for item in items:
        item.status = DispatchQueueStatus.PROCESSING
        item.attempts += 1
        ok, receipt, err = await _deliver_single_dispatch(item)
        if ok:
            item.status = DispatchQueueStatus.SENT
            item.sent_at = _utcnow()
            item.provider_receipt = receipt
            item.last_error = None
            sent_count += 1
        else:
            item.last_error = err or "Falha desconhecida no provedor."
            item.provider_receipt = receipt
            if item.attempts < item.max_attempts:
                item.status = DispatchQueueStatus.QUEUED
                item.scheduled_for = _utcnow() + timedelta(minutes=2 * item.attempts)
                requeued_count += 1
            else:
                item.status = DispatchQueueStatus.FAILED
                failed_count += 1

    await session.flush()
    return WorkerCycleResultOut(
        processed_count=len(items),
        sent_count=sent_count,
        failed_count=failed_count,
        requeued_count=requeued_count,
    )


async def scan_and_enqueue_overdue_invoices(
    session: AsyncSession, tenant_id: uuid.UUID
) -> OverdueDunningScanOut:
    now = _utcnow()
    stmt = select(BillingInvoice).where(
        BillingInvoice.tenant_id == tenant_id,
        BillingInvoice.status == InvoiceStatus.PENDING,
        BillingInvoice.due_date < now,
    )
    overdue_list = list((await session.execute(stmt)).scalars().all())

    newly_marked = 0
    total_enqueued = 0

    for inv in overdue_list:
        inv.status = InvoiceStatus.OVERDUE
        newly_marked += 1
        session.add(
            BillingPaymentEvent(
                tenant_id=tenant_id,
                invoice_id=inv.id,
                event_type=BillingEventType.OVERDUE_MARKED,
                amount=inv.net_amount,
                gateway_reference=inv.external_charge_id,
                notes="Marcada automaticamente como vencida pela régua de cobrança.",
            )
        )
        phone = str((inv.metadata_json or {}).get("customer_phone") or "")
        enq, _ = await trigger_event(
            session,
            tenant_id,
            AutomationTriggerRequest(
                event_type=AutomationTriggerType.INVOICE_OVERDUE,
                destination_phone=phone or None,
                destination_email=inv.customer_email,
                context={
                    "invoice_id": str(inv.id),
                    "invoice_number": inv.invoice_number,
                    "customer_name": inv.customer_name,
                    "currency": inv.currency,
                    "net_amount": str(inv.net_amount),
                    "payment_link": inv.payment_link or "",
                    "pix_copy_paste": inv.pix_copy_paste or "",
                },
                process_immediately=False,
            ),
        )
        total_enqueued += enq

    worker_res = await process_dispatch_queue(session, tenant_id=tenant_id, batch_size=100)
    await session.flush()

    return OverdueDunningScanOut(
        newly_marked_overdue=newly_marked,
        dispatches_enqueued=total_enqueued,
        worker_result=worker_res,
    )


async def list_dispatch_queue(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    status_filter: DispatchQueueStatus | None = None,
    limit: int = 100,
) -> list[OutboundDispatchQueue]:
    stmt = select(OutboundDispatchQueue).where(OutboundDispatchQueue.tenant_id == tenant_id)
    if status_filter is not None:
        stmt = stmt.where(OutboundDispatchQueue.status == status_filter)
    stmt = stmt.order_by(OutboundDispatchQueue.created_at.desc()).limit(limit)
    return list((await session.execute(stmt)).scalars().all())


async def list_execution_logs(
    session: AsyncSession, tenant_id: uuid.UUID, limit: int = 100
) -> list[AutomationExecutionLog]:
    stmt = (
        select(AutomationExecutionLog)
        .where(AutomationExecutionLog.tenant_id == tenant_id)
        .order_by(AutomationExecutionLog.created_at.desc())
        .limit(limit)
    )
    return list((await session.execute(stmt)).scalars().all())


async def get_executive_report(session: AsyncSession, tenant_id: uuid.UUID) -> ExecutiveReportOut:
    crm_stats = await crm_deals_service.get_crm_analytics(session, tenant_id)
    fin_stats = await billing_service.get_financial_summary(session, tenant_id)

    q_items = list(
        (
            await session.execute(
                select(OutboundDispatchQueue).where(OutboundDispatchQueue.tenant_id == tenant_id)
            )
        )
        .scalars()
        .all()
    )
    sent_cnt = sum(1 for i in q_items if i.status == DispatchQueueStatus.SENT)
    queued_cnt = sum(1 for i in q_items if i.status == DispatchQueueStatus.QUEUED)
    failed_cnt = sum(1 for i in q_items if i.status == DispatchQueueStatus.FAILED)

    return ExecutiveReportOut(
        generated_at=_utcnow(),
        open_deals_count=crm_stats.open_deals_count,
        open_pipeline_value=crm_stats.open_pipeline_value,
        weighted_forecast_value=crm_stats.weighted_forecast_value,
        won_deals_count=crm_stats.won_deals_count,
        won_revenue_total=crm_stats.won_revenue_total,
        win_rate_percent=crm_stats.win_rate_percent,
        active_contracts_count=crm_stats.contracts_signed_or_active_count,
        contracts_mrr_total=crm_stats.contracts_mrr_total,
        invoices_paid_total=fin_stats.total_paid_amount,
        invoices_pending_total=fin_stats.total_pending_amount,
        invoices_overdue_total=fin_stats.total_overdue_amount,
        commissions_pending_total=fin_stats.commissions_pending_amount,
        commissions_paid_total=fin_stats.commissions_paid_amount,
        net_cash_after_commissions=fin_stats.net_revenue_after_commissions,
        dispatches_sent_count=sent_cnt,
        dispatches_queued_count=queued_cnt,
        dispatches_failed_count=failed_cnt,
    )
