from __future__ import annotations

import logging
import os
import re
import uuid
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.redis import redis_client
from app.db.base import set_tenant_rls_context
from app.models.ai_engine import AIConversation, AIConversationStatus, AIFollowupTask
from app.models.whatsapp import WhatsAppInstance
from app.schemas.ai_agents import SDRRespondRequest
from app.schemas.whatsapp import EvolutionWebhookAck
from app.services.evolution_client import EvolutionAPIClient

logger = logging.getLogger(__name__)


def extract_text_from_evolution_message(message_obj: dict[str, Any]) -> tuple[str | None, str]:
    """
    Extrai o conteúdo textual de payloads de mensagem da Evolution API v2.
    Retorna (texto_extraido, tipo_midia).
    """
    if not isinstance(message_obj, dict) or not message_obj:
        return None, "empty"

    # Desembrulha mensagens efêmeras ou viewOnce se existirem
    for wrapper_key in ("ephemeralMessage", "viewOnceMessage", "viewOnceMessageV2"):
        if wrapper_key in message_obj and isinstance(message_obj[wrapper_key], dict):
            inner = message_obj[wrapper_key].get("message")
            if isinstance(inner, dict):
                message_obj = inner

    if isinstance(message_obj.get("conversation"), str) and message_obj["conversation"].strip():
        return message_obj["conversation"].strip(), "text"

    ext = message_obj.get("extendedTextMessage")
    if isinstance(ext, dict) and isinstance(ext.get("text"), str) and ext["text"].strip():
        return ext["text"].strip(), "extended_text"

    img = message_obj.get("imageMessage")
    if isinstance(img, dict):
        caption = (img.get("caption") or "").strip()
        return (caption if caption else None), "image"

    vid = message_obj.get("videoMessage")
    if isinstance(vid, dict):
        caption = (vid.get("caption") or "").strip()
        return (caption if caption else None), "video"

    doc = message_obj.get("documentMessage")
    if isinstance(doc, dict):
        caption = (doc.get("caption") or doc.get("title") or "").strip()
        return (caption if caption else None), "document"

    btn = message_obj.get("buttonsResponseMessage")
    if isinstance(btn, dict):
        selected = (btn.get("selectedDisplayText") or btn.get("selectedButtonId") or "").strip()
        if selected:
            return selected, "button_reply"

    lst = message_obj.get("listResponseMessage")
    if isinstance(lst, dict):
        title = (lst.get("title") or "").strip()
        if title:
            return title, "list_reply"

    if "audioMessage" in message_obj:
        return None, "audio"

    return None, "unsupported"


def extract_lead_phone_and_thread(key_obj: dict[str, Any]) -> tuple[str, str | None]:
    """
    Resolve (external_thread_id, lead_identifier_e164) a partir de data.key da Evolution API.
    Suporta remoteJid (@s.whatsapp.net ou @lid) e remoteJidAlt.
    """
    remote_jid = str(key_obj.get("remoteJid") or "").strip()
    remote_jid_alt = str(key_obj.get("remoteJidAlt") or "").strip()

    candidate_phone_jid = remote_jid
    if remote_jid.endswith("@lid") and remote_jid_alt.endswith("@s.whatsapp.net"):
        candidate_phone_jid = remote_jid_alt

    digits = re.sub(r"\D", "", candidate_phone_jid.split("@")[0])
    e164 = f"+{digits}" if len(digits) >= 10 else None
    thread_id = digits if (candidate_phone_jid.endswith("@s.whatsapp.net") and digits) else remote_jid
    return thread_id, e164


class WhatsAppService:
    @staticmethod
    async def resolve_tenant_for_instance(
        session: AsyncSession,
        instance_name: str,
    ) -> uuid.UUID | None:
        result = await session.execute(
            text("SELECT resolve_whatsapp_instance_tenant(:inst)"),
            {"inst": instance_name.strip()},
        )
        raw_val = result.scalar_one_or_none()
        if raw_val is None:
            return None
        return raw_val if isinstance(raw_val, uuid.UUID) else uuid.UUID(str(raw_val))

    @staticmethod
    async def get_active_instance_by_name(
        session: AsyncSession,
        tenant_id: uuid.UUID,
        instance_name: str,
    ) -> WhatsAppInstance | None:
        return (
            await session.execute(
                select(WhatsAppInstance).where(
                    WhatsAppInstance.tenant_id == tenant_id,
                    WhatsAppInstance.instance_name == instance_name.strip(),
                    WhatsAppInstance.is_active.is_(True),
                )
            )
        ).scalar_one_or_none()

    @staticmethod
    async def get_primary_active_instance(
        session: AsyncSession,
        tenant_id: uuid.UUID,
        preferred_instance_name: str | None = None,
    ) -> WhatsAppInstance | None:
        if preferred_instance_name:
            inst = await WhatsAppService.get_active_instance_by_name(
                session, tenant_id, preferred_instance_name
            )
            if inst is not None:
                return inst

        return (
            await session.execute(
                select(WhatsAppInstance)
                .where(
                    WhatsAppInstance.tenant_id == tenant_id,
                    WhatsAppInstance.is_active.is_(True),
                )
                .order_by(WhatsAppInstance.is_primary.desc(), WhatsAppInstance.created_at.asc())
            )
        ).scalars().first()

    @classmethod
    async def process_evolution_webhook(
        cls,
        session: AsyncSession,
        payload: dict[str, Any],
        path_instance_name: str | None = None,
        provided_secret: str | None = None,
    ) -> EvolutionWebhookAck:
        from app.services.autonomous_agents import AutonomousAgentsOrchestrator

        raw_event = str(payload.get("event") or "").strip()
        normalized_event = raw_event.lower().replace("_", ".")
        instance_name = (path_instance_name or str(payload.get("instance") or "")).strip()

        if not instance_name:
            return EvolutionWebhookAck(
                status="ignored",
                event=raw_event,
                detail="Payload sem nome de instância Evolution.",
            )

        if normalized_event != "messages.upsert":
            return EvolutionWebhookAck(
                status="ignored",
                event=raw_event,
                instance=instance_name,
                detail=f"Evento '{raw_event}' não requer acionamento do SDR.",
            )

        data_obj = payload.get("data")
        if isinstance(data_obj, list):
            data_obj = data_obj[0] if data_obj else {}
        if not isinstance(data_obj, dict):
            return EvolutionWebhookAck(
                status="ignored",
                event=raw_event,
                instance=instance_name,
                detail="Campo data inválido no payload.",
            )

        key_obj = data_obj.get("key") or {}
        if not isinstance(key_obj, dict):
            return EvolutionWebhookAck(
                status="ignored",
                event=raw_event,
                instance=instance_name,
                detail="Campo data.key ausente.",
            )

        # Ignora mensagens enviadas pelo próprio número conectado
        if bool(key_obj.get("fromMe")):
            return EvolutionWebhookAck(
                status="ignored",
                event=raw_event,
                instance=instance_name,
                detail="Mensagem própria (fromMe=true) ignorada.",
            )

        remote_jid = str(key_obj.get("remoteJid") or "").strip()
        if not remote_jid or remote_jid.endswith("@g.us") or remote_jid == "status@broadcast":
            return EvolutionWebhookAck(
                status="ignored",
                event=raw_event,
                instance=instance_name,
                detail="Mensagem de grupo ou status broadcast ignorada.",
            )

        message_id = str(key_obj.get("id") or "").strip()
        if message_id:
            dedup_key = f"saas:wa:dedup:{instance_name}:{message_id}"
            is_new = await redis_client.set(dedup_key, "1", nx=True, ex=86400)
            if not is_new:
                return EvolutionWebhookAck(
                    status="duplicate_ignored",
                    event=raw_event,
                    instance=instance_name,
                    detail=f"Mensagem {message_id} já processada anteriormente.",
                )

        tenant_id = await cls.resolve_tenant_for_instance(session, instance_name)
        if tenant_id is None:
            logger.warning("Webhook Evolution recebido para instância não cadastrada: %s", instance_name)
            return EvolutionWebhookAck(
                status="unregistered_instance",
                event=raw_event,
                instance=instance_name,
                detail=f"Instância '{instance_name}' não vinculada a nenhum tenant ativo.",
            )

        await set_tenant_rls_context(session, tenant_id)

        wa_instance = await cls.get_active_instance_by_name(session, tenant_id, instance_name)
        if wa_instance is None:
            return EvolutionWebhookAck(
                status="inactive_instance",
                event=raw_event,
                instance=instance_name,
                detail="Instância inativa para este tenant.",
            )

        expected_secret = (wa_instance.webhook_secret or os.getenv("EVOLUTION_WEBHOOK_SECRET") or "").strip()
        if expected_secret and (provided_secret or "").strip() != expected_secret:
            return EvolutionWebhookAck(
                status="unauthorized_secret",
                event=raw_event,
                instance=instance_name,
                detail="Webhook secret inválido.",
            )

        message_obj = data_obj.get("message") or {}
        text_content, media_type = extract_text_from_evolution_message(message_obj)
        thread_id, lead_e164 = extract_lead_phone_and_thread(key_obj)
        reply_target = lead_e164.lstrip("+") if lead_e164 else remote_jid
        push_name = str(data_obj.get("pushName") or "").strip() or None

        evo_client = EvolutionAPIClient(
            base_url=wa_instance.evolution_api_url,
            api_key=wa_instance.evolution_api_key,
        )

        if not text_content:
            if media_type == "audio":
                await evo_client.send_text(
                    instance_name=instance_name,
                    number=reply_target,
                    text="Recebi seu áudio! Por aqui consigo te atender mais rápido por texto — pode me escrever sua dúvida ou o que precisa?",
                    delay_ms=900,
                )
            return EvolutionWebhookAck(
                status="ignored_non_text",
                event=raw_event,
                instance=instance_name,
                detail=f"Mensagem sem texto ({media_type}).",
            )

        await evo_client.send_composing_presence(
            instance_name=instance_name,
            number=reply_target,
            delay_ms=1500,
        )

        sdr_req = SDRRespondRequest(
            external_thread_id=thread_id,
            message=text_content,
            channel="whatsapp",
            lead_identifier=lead_e164 or remote_jid,
            lead_name_hint=push_name,
            agent_slug=wa_instance.default_agent_slug,
        )

        sdr_res = await AutonomousAgentsOrchestrator.handle_sdr_inbound(
            session=session,
            tenant_id=tenant_id,
            payload=sdr_req,
        )

        if sdr_res.ai_responded and sdr_res.reply_text:
            await evo_client.send_text(
                instance_name=instance_name,
                number=reply_target,
                text=sdr_res.reply_text,
                delay_ms=1200,
            )

        # Notifica o Closer humano no WhatsApp quando o SDR realizar transbordo
        if (
            sdr_res.status == AIConversationStatus.HUMAN_HANDOFF
            and wa_instance.handoff_notify_number
            and sdr_res.handoff_summary
        ):
            alert_key = f"saas:wa:handoff_notified:{sdr_res.conversation_id}"
            should_notify = await redis_client.set(alert_key, "1", nx=True, ex=43200)
            if should_notify:
                alert_msg = (
                    f"*[TRANSBORDO SDR -> CLOSER]*\n"
                    f"*Lead:* {push_name or 'Não identificado'} ({lead_e164 or thread_id})\n"
                    f"*Motivo:* {sdr_res.handoff_reason or 'Lead qualificado'}\n\n"
                    f"*Resumo Executivo:*\n{sdr_res.handoff_summary}"
                )
                try:
                    await evo_client.send_text(
                        instance_name=instance_name,
                        number=wa_instance.handoff_notify_number,
                        text=alert_msg,
                        delay_ms=500,
                    )
                except Exception as exc:
                    logger.warning("Falha ao enviar alerta de handoff ao Closer: %s", exc)

        return EvolutionWebhookAck(
            status="processed",
            event=raw_event,
            instance=instance_name,
            conversation_id=sdr_res.conversation_id,
            ai_responded=sdr_res.ai_responded,
            detail="Mensagem processada pelo SDR e enviada via Evolution API."
            if sdr_res.ai_responded
            else "Conversa em atendimento humano (IA silenciada).",
        )

    @classmethod
    async def dispatch_followup_message(
        cls,
        session: AsyncSession,
        conv: AIConversation,
        task: AIFollowupTask,
    ) -> dict[str, Any]:
        if not task.generated_message:
            raise RuntimeError("Tarefa de follow-up sem mensagem gerada.")

        target_number = (conv.lead_identifier or conv.external_thread_id or "").strip()
        if not target_number:
            raise RuntimeError("Conversa sem número/JID de destino para disparo no WhatsApp.")

        wa_instance = await cls.get_primary_active_instance(
            session=session,
            tenant_id=task.tenant_id,
        )
        if wa_instance is None:
            raise RuntimeError(
                "Nenhuma instância WhatsApp (Evolution API) ativa cadastrada para este tenant."
            )

        evo_client = EvolutionAPIClient(
            base_url=wa_instance.evolution_api_url,
            api_key=wa_instance.evolution_api_key,
        )
        return await evo_client.send_text(
            instance_name=wa_instance.instance_name,
            number=target_number,
            text=task.generated_message,
            delay_ms=1000,
        )
