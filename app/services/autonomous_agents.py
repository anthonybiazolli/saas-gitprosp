from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import datetime, timedelta, timezone

from google.genai import types
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.redis import redis_client
from app.db.base import set_tenant_rls_context
from app.db.session import AsyncSessionLocal
from app.models.ai_engine import (
    AIAgentConfig,
    AIAgentRole,
    AIConversation,
    AIConversationStatus,
    AIFollowupStatus,
    AIFollowupTask,
    AIMessage,
)
from app.models.crm import Company, Contact, Deal
from app.schemas.ai_agents import (
    SDRRespondRequest,
    SDRRespondResponse,
    ToolExecutionRecord,
)
from app.services.agent_tools import AgentToolExecutor, build_sdr_tool_declarations
from app.services.gemini_rag import (
    generate_content_resilient,
    get_default_gemini_model,
    get_genai_client,
)
from app.services.outbound_service import process_due_cadences_once

logger = logging.getLogger(__name__)

DEFAULT_SDR_PERSONA = """Você é um SDR (Sales Development Representative) Consultivo Sênior B2B.
Seu objetivo é atender leads com cordialidade, agilidade e precisão técnica, diagnosticar suas dores reais,
consultar a base de conhecimento oficial (RAG) para responder sobre produtos, preços e SLAs sem inventar dados,
cadastrar/atualizar os dados da Empresa e do Contato no CRM, qualificar segundo o método BANT (Budget, Authority, Need, Timeline)
e conduzir leads qualificados para agendamento ou fechamento com o Executivo de Contas humano.

Diretrizes operacionais:
1. Nunca invente preços, prazos ou especificações técnicas — use sempre `search_knowledge_base`.
2. Logo no início do atendimento, use `lookup_crm_context` para verificar se o lead já existe no CRM.
3. Assim que o lead informar nome, empresa, cargo ou necessidade, chame `upsert_lead_and_company` e `update_bant_qualification`.
4. Quando identificar oportunidade comercial concreta, crie ou avance o negócio com `create_or_advance_deal`.
5. Se o lead solicitar proposta comercial formal ou tiver escopo definido para fechamento, gere com `create_commercial_proposal`.
6. Se o lead pedir para falar depois ou precisar validar internamente, agende um retorno com `schedule_autonomous_followup`.
7. Se o lead estiver pronto para proposta formal/fechamento ou pedir falar com um humano, execute `escalate_to_human_closer`.
8. Mantenha mensagens objetivas, naturais e adequadas ao canal (WhatsApp/Chat), fazendo no máximo 1 ou 2 perguntas por turno."""


class AutonomousAgentsOrchestrator:
    @staticmethod
    async def _resolve_agent_config(
        session: AsyncSession,
        tenant_id: uuid.UUID,
        role: AIAgentRole,
        agent_slug: str | None = None,
    ) -> AIAgentConfig | None:
        stmt = select(AIAgentConfig).where(
            AIAgentConfig.tenant_id == tenant_id,
            AIAgentConfig.role == role,
            AIAgentConfig.is_active.is_(True),
        )
        if agent_slug:
            stmt = stmt.where(AIAgentConfig.slug == agent_slug)
        stmt = stmt.order_by(AIAgentConfig.created_at.asc())
        return (await session.execute(stmt)).scalars().first()

    @classmethod
    async def handle_sdr_inbound(
        cls,
        session: AsyncSession,
        tenant_id: uuid.UUID,
        payload: SDRRespondRequest,
    ) -> SDRRespondResponse:
        now = datetime.now(timezone.utc)

        agent_cfg = await cls._resolve_agent_config(
            session=session,
            tenant_id=tenant_id,
            role=AIAgentRole.SDR,
            agent_slug=payload.agent_slug,
        )

        conv = (
            await session.execute(
                select(AIConversation).where(
                    AIConversation.tenant_id == tenant_id,
                    AIConversation.channel == payload.channel,
                    AIConversation.external_thread_id == payload.external_thread_id,
                )
            )
        ).scalar_one_or_none()

        if conv is None:
            conv = AIConversation(
                tenant_id=tenant_id,
                agent_id=agent_cfg.id if agent_cfg else None,
                company_id=payload.company_id,
                contact_id=payload.contact_id,
                deal_id=payload.deal_id,
                channel=payload.channel,
                external_thread_id=payload.external_thread_id,
                lead_identifier=payload.lead_identifier or payload.external_thread_id,
                status=AIConversationStatus.ACTIVE_AI,
                turn_count=0,
            )
            session.add(conv)
            await session.flush()
        else:
            if payload.company_id and not conv.company_id:
                conv.company_id = payload.company_id
            if payload.contact_id and not conv.contact_id:
                conv.contact_id = payload.contact_id
            if payload.deal_id and not conv.deal_id:
                conv.deal_id = payload.deal_id

        conv.last_inbound_at = now

        pending_fups = (
            await session.execute(
                select(AIFollowupTask).where(
                    AIFollowupTask.tenant_id == tenant_id,
                    AIFollowupTask.conversation_id == conv.id,
                    AIFollowupTask.status == AIFollowupStatus.PENDING,
                )
            )
        ).scalars().all()
        for fup in pending_fups:
            fup.status = AIFollowupStatus.CANCELLED_REPLIED

        session.add(
            AIMessage(
                tenant_id=tenant_id,
                conversation_id=conv.id,
                role="user",
                sender_type="lead",
                content=payload.message,
            )
        )
        await session.flush()

        if conv.status == AIConversationStatus.HUMAN_HANDOFF:
            await session.commit()
            return SDRRespondResponse(
                conversation_id=conv.id,
                status=conv.status,
                reply_text=None,
                ai_responded=False,
                turn_count=conv.turn_count,
                company_id=conv.company_id,
                contact_id=conv.contact_id,
                deal_id=conv.deal_id,
                qualification_state=conv.qualification_state,
                handoff_reason=conv.handoff_reason,
                handoff_summary=conv.handoff_summary,
            )

        max_turns = agent_cfg.max_autonomous_turns if agent_cfg else 15
        if conv.turn_count >= max_turns:
            conv.status = AIConversationStatus.HUMAN_HANDOFF
            conv.handoff_reason = f"Limite máximo de {max_turns} turnos autônomos atingido."
            conv.handoff_summary = "Conversa atingiu o limite de interações autônomas do SDR e requer acompanhamento humano."
            await session.commit()
            return SDRRespondResponse(
                conversation_id=conv.id,
                status=conv.status,
                reply_text=None,
                ai_responded=False,
                turn_count=conv.turn_count,
                company_id=conv.company_id,
                contact_id=conv.contact_id,
                deal_id=conv.deal_id,
                qualification_state=conv.qualification_state,
                handoff_reason=conv.handoff_reason,
                handoff_summary=conv.handoff_summary,
            )

        history_rows = (
            await session.execute(
                select(AIMessage)
                .where(AIMessage.conversation_id == conv.id)
                .order_by(AIMessage.created_at.desc())
                .limit(20)
            )
        ).scalars().all()
        history_rows = list(reversed(history_rows))

        contents: list[types.Content] = []
        for msg in history_rows:
            gemini_role = "model" if msg.role == "assistant" else "user"
            contents.append(
                types.Content(
                    role=gemini_role,
                    parts=[types.Part.from_text(text=msg.content)],
                )
            )

        persona = agent_cfg.persona_prompt if agent_cfg else DEFAULT_SDR_PERSONA
        extra_rules = []
        if agent_cfg and agent_cfg.qualification_guidelines:
            extra_rules.append(f"Critérios de Qualificação:\n{agent_cfg.qualification_guidelines}")
        if agent_cfg and agent_cfg.handoff_rules:
            extra_rules.append(f"Regras de Transbordo Humano:\n{agent_cfg.handoff_rules}")

        context_banner = (
            f"\n\n[Contexto da Sessão Atual]\n"
            f"- Canal: {conv.channel}\n"
            f"- Thread ID: {conv.external_thread_id}\n"
            f"- Identificador do Lead: {conv.lead_identifier or 'não informado'}\n"
            f"- Nome sugerido no perfil: {payload.lead_name_hint or 'desconhecido'}\n"
            f"- IDs já vinculados -> company_id: {conv.company_id}, contact_id: {conv.contact_id}, deal_id: {conv.deal_id}\n"
            f"- Qualificação BANT atual: {json.dumps(conv.qualification_state, ensure_ascii=False)}"
        )

        system_instruction = persona + ("\n\n" + "\n\n".join(extra_rules) if extra_rules else "") + context_banner
        model_name = agent_cfg.gemini_model if agent_cfg else get_default_gemini_model()
        temperature = agent_cfg.temperature if agent_cfg else 0.35

        client = get_genai_client()
        tool_declaration = build_sdr_tool_declarations()
        tool_executor = AgentToolExecutor(
            session=session,
            tenant_id=tenant_id,
            conversation=conv,
        )

        tools_executed: list[ToolExecutionRecord] = []
        final_reply_text = ""

        for _step in range(6):
            response = await generate_content_resilient(
                client=client,
                contents=contents,
                config=types.GenerateContentConfig(
                    system_instruction=system_instruction,
                    temperature=temperature,
                    tools=[tool_declaration],
                ),
                preferred_model=model_name,
            )

            if response.function_calls:
                if response.candidates and response.candidates[0].content:
                    contents.append(response.candidates[0].content)

                function_response_parts: list[types.Part] = []
                for fc in response.function_calls:
                    fn_name = fc.name
                    fn_args = dict(fc.args) if fc.args else {}
                    fn_result = await tool_executor.execute(fn_name, fn_args)
                    tools_executed.append(
                        ToolExecutionRecord(
                            tool_name=fn_name,
                            arguments=fn_args,
                            result=fn_result,
                        )
                    )
                    function_response_parts.append(
                        types.Part.from_function_response(
                            name=fn_name,
                            response={"result": fn_result},
                        )
                    )

                contents.append(types.Content(role="tool", parts=function_response_parts))
                continue

            final_reply_text = (response.text or "").strip()
            break

        if not final_reply_text:
            final_reply_text = (
                "Perfeito, já registrei suas informações aqui. Um de nossos especialistas vai dar sequência ao seu atendimento."
            )

        out_now = datetime.now(timezone.utc)
        conv.turn_count += 1
        conv.last_outbound_at = out_now

        auto_fup_enabled = agent_cfg.auto_followup_enabled if agent_cfg else True
        if auto_fup_enabled and conv.status == AIConversationStatus.ACTIVE_AI:
            has_pending = (
                await session.execute(
                    select(AIFollowupTask.id).where(
                        AIFollowupTask.conversation_id == conv.id,
                        AIFollowupTask.status == AIFollowupStatus.PENDING,
                    )
                )
            ).scalar_one_or_none()
            if has_pending is None:
                delay_min = agent_cfg.default_followup_delay_minutes if agent_cfg else 240
                session.add(
                    AIFollowupTask(
                        tenant_id=tenant_id,
                        conversation_id=conv.id,
                        company_id=conv.company_id,
                        contact_id=conv.contact_id,
                        deal_id=conv.deal_id,
                        attempt_number=1,
                        channel=conv.channel,
                        objective="Retomar conversa caso o lead não responda à última interação do SDR.",
                        scheduled_for=out_now + timedelta(minutes=delay_min),
                        status=AIFollowupStatus.PENDING,
                    )
                )

        session.add(
            AIMessage(
                tenant_id=tenant_id,
                conversation_id=conv.id,
                role="assistant",
                sender_type="ai_sdr",
                content=final_reply_text,
                tool_calls_log=[t.model_dump() for t in tools_executed],
                rag_sources_used=tool_executor.rag_sources_collected,
                model_used=model_name,
            )
        )

        await session.commit()
        await session.refresh(conv)

        return SDRRespondResponse(
            conversation_id=conv.id,
            status=conv.status,
            reply_text=final_reply_text,
            ai_responded=True,
            turn_count=conv.turn_count,
            company_id=conv.company_id,
            contact_id=conv.contact_id,
            deal_id=conv.deal_id,
            qualification_state=conv.qualification_state,
            tools_executed=tools_executed,
            rag_sources_used=tool_executor.rag_sources_collected,
            handoff_reason=conv.handoff_reason,
            handoff_summary=conv.handoff_summary,
        )

    @classmethod
    async def execute_followup_task(
        cls,
        session: AsyncSession,
        task: AIFollowupTask,
    ) -> AIFollowupTask:
        from app.services.whatsapp_service import WhatsAppService

        conv = (
            await session.execute(
                select(AIConversation).where(AIConversation.id == task.conversation_id)
            )
        ).scalar_one_or_none()

        if conv is None or conv.status != AIConversationStatus.ACTIVE_AI:
            task.status = AIFollowupStatus.CANCELLED_MANUAL
            task.error_message = "Conversa encerrada ou sob atendimento humano."
            await session.commit()
            return task

        if conv.last_inbound_at and conv.last_inbound_at > task.created_at:
            task.status = AIFollowupStatus.CANCELLED_REPLIED
            await session.commit()
            return task

        agent_cfg = await cls._resolve_agent_config(
            session=session,
            tenant_id=task.tenant_id,
            role=AIAgentRole.FOLLOWUP,
        )
        if agent_cfg is None:
            agent_cfg = await cls._resolve_agent_config(
                session=session,
                tenant_id=task.tenant_id,
                role=AIAgentRole.SDR,
            )

        recent_msgs = (
            await session.execute(
                select(AIMessage)
                .where(AIMessage.conversation_id == conv.id)
                .order_by(AIMessage.created_at.desc())
                .limit(10)
            )
        ).scalars().all()
        recent_msgs = list(reversed(recent_msgs))
        transcript = "\n".join(f"[{m.sender_type}]: {m.content}" for m in recent_msgs)

        crm_notes = []
        if conv.company_id:
            comp = (await session.execute(select(Company).where(Company.id == conv.company_id))).scalar_one_or_none()
            if comp:
                crm_notes.append(f"Empresa: {comp.trade_name or comp.legal_name} (ICP {comp.icp_score}/100)")
        if conv.contact_id:
            cont = (await session.execute(select(Contact).where(Contact.id == conv.contact_id))).scalar_one_or_none()
            if cont:
                crm_notes.append(f"Contato: {cont.full_name} ({cont.job_title or 'Cargo não informado'})")
        if conv.deal_id:
            deal = (await session.execute(select(Deal).where(Deal.id == conv.deal_id))).scalar_one_or_none()
            if deal:
                crm_notes.append(f"Negócio: {deal.title} (MRR R$ {deal.mrr_value})")

        prompt = (
            f"Você é o Agente Autônomo de Follow-up Comercial B2B.\n"
            f"Esta é a tentativa de follow-up #{task.attempt_number} no canal {task.channel}.\n"
            f"Objetivo específico: {task.objective}\n"
            f"Dados CRM: {' | '.join(crm_notes) if crm_notes else 'Lead em prospecção inicial'}\n"
            f"Qualificação BANT atual: {json.dumps(conv.qualification_state, ensure_ascii=False)}\n\n"
            f"Histórico recente da conversa:\n{transcript}\n\n"
            f"Escreva uma mensagem curta, consultiva, natural e personalizada (sem parecer robô ou cobrança insistente) "
            f"para reengajar o lead com base no contexto exato acima. Retorne APENAS o texto da mensagem a ser enviada."
        )

        model_name = agent_cfg.gemini_model if agent_cfg else get_default_gemini_model()
        client = get_genai_client()

        try:
            response = await generate_content_resilient(
                client=client,
                contents=prompt,
                config=types.GenerateContentConfig(temperature=0.45),
                preferred_model=model_name,
            )
            followup_text = (response.text or "").strip()
            if not followup_text:
                raise RuntimeError("Resposta vazia do modelo Gemini ao gerar follow-up.")

            task.generated_message = followup_text

            if task.channel == "whatsapp":
                await WhatsAppService.dispatch_followup_message(
                    session=session,
                    conv=conv,
                    task=task,
                )

            now = datetime.now(timezone.utc)
            task.status = AIFollowupStatus.SENT
            task.executed_at = now
            task.error_message = None

            conv.last_outbound_at = now
            session.add(
                AIMessage(
                    tenant_id=task.tenant_id,
                    conversation_id=conv.id,
                    role="assistant",
                    sender_type="ai_followup",
                    content=followup_text,
                    model_used=model_name,
                )
            )
            await session.commit()
            await session.refresh(task)
            return task
        except Exception as exc:
            task.status = AIFollowupStatus.FAILED
            task.error_message = str(exc)[:500]
            await session.commit()
            return task


async def autonomous_followup_worker_loop(stop_event: asyncio.Event) -> None:
    """
    Worker assíncrono em background que verifica a cada 30s tarefas de follow-up vencidas
    e etapas de cadências multi-etapa ativas usando lock distribuído no Redis.
    """
    while not stop_event.is_set():
        try:
            await process_due_cadences_once()

            lock_acquired = await redis_client.set("saas:lock:followup_worker", "1", nx=True, ex=25)
            if lock_acquired:
                now = datetime.now(timezone.utc)
                async with AsyncSessionLocal() as session:
                    due_ids = (
                        await session.execute(
                            select(AIFollowupTask.id, AIFollowupTask.tenant_id)
                            .where(
                                AIFollowupTask.status == AIFollowupStatus.PENDING,
                                AIFollowupTask.scheduled_for <= now,
                            )
                            .order_by(AIFollowupTask.scheduled_for.asc())
                            .limit(10)
                        )
                    ).all()

                for task_id, tenant_id in due_ids:
                    async with AsyncSessionLocal() as task_session:
                        await set_tenant_rls_context(task_session, tenant_id)
                        task = (
                            await task_session.execute(
                                select(AIFollowupTask).where(AIFollowupTask.id == task_id)
                            )
                        ).scalar_one_or_none()
                        if task is not None and task.status == AIFollowupStatus.PENDING:
                            await AutonomousAgentsOrchestrator.execute_followup_task(task_session, task)
        except Exception as exc:
            logger.warning("Erro no ciclo do worker autônomo: %s", exc)

        try:
            await asyncio.wait_for(stop_event.wait(), timeout=30.0)
        except asyncio.TimeoutError:
            continue
