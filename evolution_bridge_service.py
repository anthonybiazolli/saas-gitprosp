from __future__ import annotations

import asyncio
import json
import re
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.automation_engine import DispatchQueueStatus, OutboundDispatchQueue
from app.models.billing_commissions import BillingInvoice, InvoiceStatus
from app.models.evolution_bridge import EvolutionChannelConfig, OmnichannelMessageLog
from app.models.saas_gateway import InboundWebhookReceipt
from app.models.team_portal import CustomerPortalToken
from app.schemas.evolution_bridge import (
    EvolutionConfigOut,
    EvolutionConfigUpdate,
    EvolutionConnectionStatusOut,
    EvolutionQrCodeOut,
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def normalize_br_phone(raw_phone: str, default_cc: str = "55") -> str:
    digits = re.sub(r"\D+", "", raw_phone or "")
    if not digits:
        return ""
    if digits.startswith("00"):
        digits = digits[2:]
    if len(digits) in (10, 11) and not digits.startswith(default_cc):
        digits = f"{default_cc}{digits}"
    return digits


def _mask_api_key(key: str) -> str:
    if not key:
        return "(não configurada)"
    if len(key) <= 8:
        return "*" * len(key)
    return f"{key[:4]}...{key[-4:]}"


def serialize_config(cfg: EvolutionChannelConfig) -> EvolutionConfigOut:
    return EvolutionConfigOut(
        id=cfg.id,
        tenant_id=cfg.tenant_id,
        base_url=cfg.base_url,
        api_key_masked=_mask_api_key(cfg.api_key),
        instance_name=cfg.instance_name,
        default_country_code=cfg.default_country_code,
        is_enabled=cfg.is_enabled,
        send_pix_copy_paste_separately=cfg.send_pix_copy_paste_separately,
        auto_reply_enabled=cfg.auto_reply_enabled,
        last_connection_state=cfg.last_connection_state,
        last_checked_at=cfg.last_checked_at,
        webhook_configured_at=cfg.webhook_configured_at,
        updated_at=cfg.updated_at,
    )


async def _evolution_http_call(
    method: str,
    base_url: str,
    api_key: str,
    path: str,
    payload: dict[str, Any] | None = None,
    timeout: float = 15.0,
) -> tuple[int, dict[str, Any]]:
    url = f"{base_url.rstrip('/')}/{path.lstrip('/')}"

    def _sync_req() -> tuple[int, dict[str, Any]]:
        body_bytes = json.dumps(payload).encode("utf-8") if payload is not None else None
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "apikey": api_key or "",
        }
        req = urllib.request.Request(url=url, data=body_bytes, headers=headers, method=method.upper())
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
                try:
                    parsed = json.loads(raw) if raw else {}
                except Exception:
                    parsed = {"raw": raw}
                return int(resp.status), parsed if isinstance(parsed, dict) else {"data": parsed}
        except urllib.error.HTTPError as exc:
            err_raw = exc.read().decode("utf-8", errors="replace")
            try:
                err_json = json.loads(err_raw) if err_raw else {"error": str(exc)}
            except Exception:
                err_json = {"error": err_raw or str(exc)}
            return int(exc.code), err_json if isinstance(err_json, dict) else {"error": err_json}
        except Exception as exc:
            return 599, {"error": str(exc)}

    return await asyncio.to_thread(_sync_req)


async def ensure_evolution_config(
    session: AsyncSession, tenant_id: uuid.UUID
) -> EvolutionChannelConfig:
    stmt = select(EvolutionChannelConfig).where(EvolutionChannelConfig.tenant_id == tenant_id)
    cfg = (await session.execute(stmt)).scalars().first()
    if cfg is not None:
        return cfg

    cfg = EvolutionChannelConfig(
        tenant_id=tenant_id,
        base_url="http://192.168.0.9:8080",
        api_key="",
        instance_name="comercial-principal",
        default_country_code="55",
        is_enabled=False,
        send_pix_copy_paste_separately=True,
        auto_reply_enabled=True,
    )
    session.add(cfg)
    await session.flush()
    await session.refresh(cfg)
    return cfg


async def update_evolution_config(
    session: AsyncSession, tenant_id: uuid.UUID, req: EvolutionConfigUpdate
) -> EvolutionChannelConfig:
    cfg = await ensure_evolution_config(session, tenant_id)
    for field, value in req.model_dump(exclude_unset=True).items():
        if value is not None:
            if field == "base_url":
                value = value.strip().rstrip("/")
            elif field == "instance_name":
                value = value.strip()
            setattr(cfg, field, value)

    await session.flush()
    await session.refresh(cfg)
    return cfg


async def check_instance_status(
    session: AsyncSession, tenant_id: uuid.UUID
) -> EvolutionConnectionStatusOut:
    cfg = await ensure_evolution_config(session, tenant_id)
    inst = urllib.parse.quote(cfg.instance_name, safe="")
    code, data = await _evolution_http_call(
        "GET", cfg.base_url, cfg.api_key, f"/instance/connectionState/{inst}"
    )

    state = "UNREACHABLE"
    reachable = code < 500
    if code == 200:
        inst_obj = data.get("instance") if isinstance(data.get("instance"), dict) else data
        state = str(inst_obj.get("state") or data.get("state") or "CONNECTED").upper()
    elif code < 500:
        state = f"HTTP_{code}"

    cfg.last_connection_state = state[:40]
    cfg.last_checked_at = _utcnow()
    await session.flush()

    return EvolutionConnectionStatusOut(
        instance_name=cfg.instance_name,
        base_url=cfg.base_url,
        is_enabled=cfg.is_enabled,
        connection_state=state,
        reachable=reachable,
        raw_response=data,
    )


async def fetch_instance_qrcode(
    session: AsyncSession, tenant_id: uuid.UUID
) -> EvolutionQrCodeOut:
    cfg = await ensure_evolution_config(session, tenant_id)
    inst = urllib.parse.quote(cfg.instance_name, safe="")
    code, data = await _evolution_http_call(
        "GET", cfg.base_url, cfg.api_key, f"/instance/connect/{inst}"
    )
    if code >= 500:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Não foi possível conectar à Evolution API em {cfg.base_url}: {data}",
        )

    qr_b64 = data.get("base64") or (data.get("qrcode") or {}).get("base64")
    pairing = data.get("pairingCode") or data.get("code")
    state = "PAIRING_REQUIRED" if (qr_b64 or pairing) else cfg.last_connection_state

    return EvolutionQrCodeOut(
        instance_name=cfg.instance_name,
        connection_state=state,
        pairing_code=str(pairing) if pairing else None,
        qr_code_base64=str(qr_b64) if qr_b64 else None,
        raw_response=data,
    )


async def register_webhook_on_evolution(
    session: AsyncSession, tenant_id: uuid.UUID, public_base_url: str
) -> dict[str, Any]:
    cfg = await ensure_evolution_config(session, tenant_id)
    inst = urllib.parse.quote(cfg.instance_name, safe="")
    webhook_url = f"{public_base_url.rstrip('/')}/api/v1/evolution/webhook/{tenant_id}"

    payload = {
        "webhook": {
            "enabled": True,
            "url": webhook_url,
            "webhookByEvents": False,
            "webhookBase64": False,
            "events": ["MESSAGES_UPSERT", "CONNECTION_UPDATE"],
        }
    }
    code, data = await _evolution_http_call(
        "POST", cfg.base_url, cfg.api_key, f"/webhook/set/{inst}", payload=payload
    )
    if code not in (200, 201):
        flat_payload = {
            "enabled": True,
            "url": webhook_url,
            "events": ["MESSAGES_UPSERT", "CONNECTION_UPDATE"],
        }
        code, data = await _evolution_http_call(
            "POST", cfg.base_url, cfg.api_key, f"/webhook/set/{inst}", payload=flat_payload
        )

    if code in (200, 201):
        cfg.webhook_configured_at = _utcnow()
        await session.flush()

    return {
        "http_status": code,
        "webhook_url": webhook_url,
        "instance_name": cfg.instance_name,
        "provider_response": data,
    }


async def send_whatsapp_text(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    raw_phone: str,
    text: str,
    related_invoice_number: str | None = None,
) -> OmnichannelMessageLog:
    cfg = await ensure_evolution_config(session, tenant_id)
    phone = normalize_br_phone(raw_phone, cfg.default_country_code)
    if not phone:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Número de telefone inválido.")

    remote_jid = f"{phone}@s.whatsapp.net"
    inst = urllib.parse.quote(cfg.instance_name, safe="")
    payload = {
        "number": phone,
        "text": text,
        "delay": 1200,
    }
    code, data = await _evolution_http_call(
        "POST", cfg.base_url, cfg.api_key, f"/message/sendText/{inst}", payload=payload
    )

    ok = code in (200, 201)
    msg_id = None
    if isinstance(data.get("key"), dict):
        msg_id = data["key"].get("id")
    elif data.get("id"):
        msg_id = str(data.get("id"))

    log_entry = OmnichannelMessageLog(
        tenant_id=tenant_id,
        direction="OUTBOUND",
        channel="WHATSAPP",
        instance_name=cfg.instance_name,
        phone_number=phone,
        remote_jid=remote_jid,
        message_text=text,
        provider_message_id=str(msg_id) if msg_id else None,
        delivery_status="SENT" if ok else "FAILED",
        error_detail=None if ok else json.dumps(data, ensure_ascii=False)[:1000],
        related_invoice_number=related_invoice_number,
        raw_payload=data,
    )
    session.add(log_entry)
    await session.flush()
    await session.refresh(log_entry)
    return log_entry


def _set_queue_error(item: Any, err_msg: str | None) -> None:
    if hasattr(item, "last_error"):
        item.last_error = err_msg
    elif hasattr(item, "error_message"):
        item.error_message = err_msg


async def dispatch_pending_whatsapp_via_evolution(
    session: AsyncSession, tenant_id: uuid.UUID, limit: int = 50
) -> int:
    """Despacha itens PENDING da fila via Evolution API real se o tenant habilitou o canal."""
    cfg = await ensure_evolution_config(session, tenant_id)
    if not cfg.is_enabled:
        return 0

    stmt = (
        select(OutboundDispatchQueue)
        .where(
            OutboundDispatchQueue.tenant_id == tenant_id,
            OutboundDispatchQueue.status == DispatchQueueStatus.PENDING,
        )
        .order_by(OutboundDispatchQueue.created_at.asc())
        .limit(limit)
    )
    items = list((await session.execute(stmt)).scalars().all())
    processed = 0

    for item in items:
        ch = (item.channel.value if hasattr(item.channel, "value") else str(item.channel)).upper()
        if ch != "WHATSAPP":
            continue

        dest = (getattr(item, "destination", "") or "").strip()
        if not dest:
            item.status = DispatchQueueStatus.FAILED
            _set_queue_error(item, "Destino WhatsApp vazio na fila.")
            processed += 1
            continue

        ctx = item.context_json if isinstance(getattr(item, "context_json", None), dict) else {}
        inv_num = str(ctx.get("invoice_number") or "") or None
        log_row = await send_whatsapp_text(
            session,
            tenant_id,
            raw_phone=dest,
            text=item.rendered_body,
            related_invoice_number=inv_num,
        )

        if (
            log_row.delivery_status == "SENT"
            and cfg.send_pix_copy_paste_separately
            and inv_num
        ):
            inv_obj = (
                await session.execute(
                    select(BillingInvoice).where(
                        BillingInvoice.tenant_id == tenant_id,
                        BillingInvoice.invoice_number == inv_num,
                    )
                )
            ).scalars().first()
            if inv_obj and inv_obj.pix_copy_paste and inv_obj.status == InvoiceStatus.PENDING:
                await send_whatsapp_text(
                    session,
                    tenant_id,
                    raw_phone=dest,
                    text=inv_obj.pix_copy_paste,
                    related_invoice_number=inv_num,
                )

        current_attempts = int(getattr(item, "attempts", 0) or 0) + 1
        if hasattr(item, "attempts"):
            item.attempts = current_attempts

        if log_row.delivery_status == "SENT":
            item.status = DispatchQueueStatus.SENT
            if hasattr(item, "sent_at"):
                item.sent_at = _utcnow()
            _set_queue_error(item, None)
        else:
            _set_queue_error(item, log_row.error_detail)
            if current_attempts >= 3:
                item.status = DispatchQueueStatus.FAILED
        processed += 1

    await session.flush()
    return processed


async def handle_evolution_inbound_webhook(
    session: AsyncSession, tenant_id: uuid.UUID, payload: dict[str, Any]
) -> dict[str, Any]:
    cfg = await ensure_evolution_config(session, tenant_id)
    event_name = str(payload.get("event") or payload.get("type") or "unknown").upper()
    data = payload.get("data") if isinstance(payload.get("data"), dict) else payload

    if "CONNECTION_UPDATE" in event_name:
        new_state = str(data.get("state") or data.get("status") or "UPDATED").upper()
        cfg.last_connection_state = new_state[:40]
        cfg.last_checked_at = _utcnow()
        session.add(
            InboundWebhookReceipt(
                tenant_id=tenant_id,
                provider="evolution",
                event_name=event_name,
                processing_status="PROCESSED",
                summary=f"Estado da instância {cfg.instance_name} atualizado para {new_state}.",
                raw_payload=payload,
            )
        )
        await session.flush()
        return {"status": "ok", "event": event_name, "connection_state": new_state}

    key = data.get("key") if isinstance(data.get("key"), dict) else {}
    if key.get("fromMe") is True:
        return {"status": "ignored", "reason": "fromMe"}

    remote_jid = str(key.get("remoteJid") or data.get("remoteJid") or "")
    phone = normalize_br_phone(remote_jid.split("@")[0], cfg.default_country_code)

    msg_obj = data.get("message") if isinstance(data.get("message"), dict) else {}
    text_body = (
        msg_obj.get("conversation")
        or (msg_obj.get("extendedTextMessage") or {}).get("text")
        or data.get("text")
        or ""
    ).strip()

    if not phone or not text_body:
        return {"status": "ignored", "reason": "non_text_or_empty"}

    in_log = OmnichannelMessageLog(
        tenant_id=tenant_id,
        direction="INBOUND",
        channel="WHATSAPP",
        instance_name=cfg.instance_name,
        phone_number=phone,
        remote_jid=remote_jid or f"{phone}@s.whatsapp.net",
        message_text=text_body,
        provider_message_id=str(key.get("id")) if key.get("id") else None,
        delivery_status="RECEIVED",
        raw_payload=payload,
    )
    session.add(in_log)
    session.add(
        InboundWebhookReceipt(
            tenant_id=tenant_id,
            provider="evolution",
            event_name=event_name,
            external_event_id=str(key.get("id")) if key.get("id") else None,
            processing_status="PROCESSED",
            summary=f"Mensagem WhatsApp recebida de {phone}: {text_body[:90]}",
            raw_payload=payload,
        )
    )

    auto_replied = False
    upper_txt = text_body.upper()
    if cfg.is_enabled and cfg.auto_reply_enabled and any(
        kw in upper_txt for kw in ("PIX", "FATURA", "2 VIA", "SEGUNDA VIA", "BOLETO", "PAGAR")
    ):
        inv_stmt = (
            select(BillingInvoice)
            .where(
                BillingInvoice.tenant_id == tenant_id,
                BillingInvoice.status == InvoiceStatus.PENDING,
            )
            .order_by(BillingInvoice.created_at.desc())
            .limit(10)
        )
        pending_invs = list((await session.execute(inv_stmt)).scalars().all())
        matched_inv = next(
            (
                inv
                for inv in pending_invs
                if normalize_br_phone(
                    str((inv.metadata_json or {}).get("customer_phone") or ""),
                    cfg.default_country_code,
                ).endswith(phone[-8:])
            ),
            None,
        )
        if matched_inv and matched_inv.pix_copy_paste:
            await send_whatsapp_text(
                session,
                tenant_id,
                raw_phone=phone,
                text=(
                    f"Olá {matched_inv.customer_name}! Segue o código PIX Copia e Cola da fatura "
                    f"*{matched_inv.invoice_number}* (R$ {matched_inv.net_amount}):"
                ),
                related_invoice_number=matched_inv.invoice_number,
            )
            await send_whatsapp_text(
                session,
                tenant_id,
                raw_phone=phone,
                text=matched_inv.pix_copy_paste,
                related_invoice_number=matched_inv.invoice_number,
            )
            auto_replied = True

    elif cfg.is_enabled and cfg.auto_reply_enabled and any(
        kw in upper_txt for kw in ("CONTRATO", "PORTAL", "ASSINAR", "LINK")
    ):
        prt_filters = [CustomerPortalToken.tenant_id == tenant_id]
        if hasattr(CustomerPortalToken, "is_revoked"):
            prt_filters.append(CustomerPortalToken.is_revoked.is_(False))
        elif hasattr(CustomerPortalToken, "is_active"):
            prt_filters.append(CustomerPortalToken.is_active.is_(True))

        prt_stmt = (
            select(CustomerPortalToken)
            .where(*prt_filters)
            .order_by(CustomerPortalToken.created_at.desc())
            .limit(1)
        )
        latest_prt = (await session.execute(prt_stmt)).scalars().first()
        if latest_prt:
            await send_whatsapp_text(
                session,
                tenant_id,
                raw_phone=phone,
                text=(
                    f"Olá {latest_prt.customer_name}! Acesse seu Portal Self-Service para assinatura "
                    f"e consulta: https://comercial.3.143.227.21.sslip.io/api/v1/team/public/{latest_prt.token}/view"
                ),
            )
            auto_replied = True

    await session.flush()
    return {"status": "processed", "phone": phone, "auto_replied": auto_replied}


async def list_message_logs(
    session: AsyncSession, tenant_id: uuid.UUID, limit: int = 40
) -> list[OmnichannelMessageLog]:
    stmt = (
        select(OmnichannelMessageLog)
        .where(OmnichannelMessageLog.tenant_id == tenant_id)
        .order_by(OmnichannelMessageLog.created_at.desc())
        .limit(limit)
    )
    return list((await session.execute(stmt)).scalars().all())