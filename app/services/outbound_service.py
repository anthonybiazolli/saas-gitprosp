from __future__ import annotations

import json
import logging
import re
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from fastapi import HTTPException, status
from google.genai import types
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.redis import redis_client
from app.db.base import set_tenant_rls_context
from app.db.session import AsyncSessionLocal
from app.models.ai_engine import AIConversation, AIConversationStatus, AIMessage
from app.models.crm import Company, CompanyContact, Contact, Deal, DealStageHistory, DealStatus, LifecycleStage, Pipeline
from app.models.outbound import (
    CadenceEnrollment,
    CadenceSequence,
    CadenceStep,
    EnrollmentStatus,
    OutboundLead,
    OutboundLeadStatus,
    ProspectingCampaign,
)
from app.schemas.outbound import (
    CadenceEnrollRequest,
    CadenceSequenceCreate,
    OutboundLeadInput,
    OutboundPromoteRequest,
)
from app.services.evolution_client import EvolutionAPIClient
from app.services.gemini_rag import generate_content_resilient, generate_structured_json, get_genai_client
from app.services.whatsapp_service import WhatsAppService

logger = logging.getLogger(__name__)

ENRICHMENT_SCHEMA: dict[str, Any] = {
    "type": "OBJECT",
    "properties": {
        "icp_score": {"type": "INTEGER"},
        "estimated_company_size": {"type": "STRING"},
        "likely_pain_points": {"type": "ARRAY", "items": {"type": "STRING"}},
        "recommended_hook": {"type": "STRING"},
        "opening_message": {"type": "STRING"},
    },
    "required": ["icp_score", "likely_pain_points", "recommended_hook", "opening_message"],
}


class OutboundAndCadenceService:
    @classmethod
    async def enrich_outbound_lead(
        cls,
        session: AsyncSession,
        lead: OutboundLead,
        campaign: ProspectingCampaign | None = None,
    ) -> OutboundLead:
        icp_ctx = campaign.icp_description if campaign else "Empresas B2B com necessidade de infraestrutura, conectividade, software ou automação."
        prop_ctx = campaign.value_proposition if (campaign and campaign.value_proposition) else "Soluções escaláveis com SLA transparente e atendimento consultivo."

        prompt = (
            f"Analise o lead B2B abaixo para prospecção outbound consultiva.\n"
            f"ICP da Campanha: {icp_ctx}\n"
            f"Proposta de Valor: {prop_ctx}\n\n"
            f"Dados do Lead:\n"
            f"- Empresa: {lead.company_name}\n"
            f"- CNPJ: {lead.cnpj or 'N/I'} | Website: {lead.website or 'N/I'}\n"
            f"- Setor: {lead.sector or 'N/I'} | Cidade/UF: {lead.city or 'N/I'}/{lead.state_uf or 'N/I'}\n"
            f"- Contato: {lead.contact_name or 'Responsável'} | Cargo: {lead.job_title or 'N/I'}\n"
            f"- Observações: {json.dumps(lead.enrichment_json, ensure_ascii=False)}\n\n"
            f"Retorne JSON com:\n"
            f"- icp_score (0 a 100)\n"
            f"- estimated_company_size\n"
            f"- likely_pain_points (2 a 4 dores prováveis)\n"
            f"- recommended_hook (ângulo de abordagem)\n"
            f"- opening_message (mensagem curta, natural e consultiva para iniciar conversa no WhatsApp)"
        )

        data = await generate_structured_json(prompt=prompt, schema=ENRICHMENT_SCHEMA, temperature=0.3)
        score = max(0, min(100, int(data.get("icp_score", 55))))

        merged_meta = dict(lead.enrichment_json or {})
        merged_meta.update(
            {
                "estimated_company_size": data.get("estimated_company_size", "Média/Grande"),
                "likely_pain_points": data.get("likely_pain_points", []),
                "recommended_hook": data.get("recommended_hook", ""),
                "enriched_at": datetime.now(timezone.utc).isoformat(),
            }
        )

        lead.icp_score = score
        lead.enrichment_json = merged_meta
        if data.get("opening_message"):
            lead.generated_opening_message = str(data["opening_message"]).strip()
        if lead.status == OutboundLeadStatus.NEW:
            lead.status = OutboundLeadStatus.ENRICHED

        await session.flush()
        return lead

    @classmethod
    async def import_leads(
        cls,
        session: AsyncSession,
        tenant_id: uuid.UUID,
        leads_in: list[OutboundLeadInput],
        campaign_id: uuid.UUID | None = None,
        auto_enrich: bool = True,
    ) -> list[OutboundLead]:
        campaign: ProspectingCampaign | None = None
        if campaign_id:
            campaign = (
                await session.execute(
                    select(ProspectingCampaign).where(
                        ProspectingCampaign.id == campaign_id,
                        ProspectingCampaign.tenant_id == tenant_id,
                    )
                )
            ).scalar_one_or_none()
            if campaign is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Campanha de prospecção não encontrada.")

        created_leads: list[OutboundLead] = []
        for item in leads_in:
            clean_wa: str | None = None
            if item.whatsapp_e164:
                digits = re.sub(r"\D", "", item.whatsapp_e164)
                clean_wa = f"+{digits}" if digits else item.whatsapp_e164.strip()

            lead = OutboundLead(
                tenant_id=tenant_id,
                campaign_id=campaign.id if campaign else None,
                company_name=item.company_name.strip(),
                cnpj=item.cnpj.strip() if item.cnpj else None,
                website=item.website.strip() if item.website else None,
                sector=item.sector.strip() if item.sector else None,
                city=item.city.strip() if item.city else None,
                state_uf=item.state_uf.strip().upper()[:2] if item.state_uf else None,
                contact_name=item.contact_name.strip() if item.contact_name else None,
                job_title=item.job_title.strip() if item.job_title else None,
                email=item.email.strip().lower() if item.email else None,
                whatsapp_e164=clean_wa,
                source_channel=item.source_channel,
                enrichment_json={"notes": item.notes} if item.notes else {},
            )
            session.add(lead)
            await session.flush()

            if auto_enrich:
                try:
                    await cls.enrich_outbound_lead(session=session, lead=lead, campaign=campaign)
                except Exception as exc:
                    logger.warning("Falha ao enriquecer lead %s com Gemini: %s", lead.company_name, exc)

            created_leads.append(lead)

        await session.commit()
        for ld in created_leads:
            await session.refresh(ld)
        return created_leads

    @classmethod
    async def promote_lead_to_crm(
        cls,
        session: AsyncSession,
        tenant_id: uuid.UUID,
        lead_id: uuid.UUID,
        payload: OutboundPromoteRequest,
    ) -> OutboundLead:
        lead = (
            await session.execute(
                select(OutboundLead).where(
                    OutboundLead.id == lead_id,
                    OutboundLead.tenant_id == tenant_id,
                )
            )
        ).scalar_one_or_none()
        if lead is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Lead outbound não encontrado.")

        now = datetime.now(timezone.utc)
        pains = list((lead.enrichment_json or {}).get("likely_pain_points") or [])

        company = Company(
            tenant_id=tenant_id,
            legal_name=lead.company_name,
            trade_name=lead.company_name,
            cnpj=lead.cnpj,
            website=lead.website,
            sector=lead.sector,
            city=lead.city,
            state_uf=lead.state_uf,
            primary_email=lead.email,
            whatsapp_number=lead.whatsapp_e164,
            lifecycle_stage=LifecycleStage.SQL,
            lead_source="outbound_prospector",
            icp_score=lead.icp_score,
            identified_pain_points=pains,
        )
        session.add(company)
        await session.flush()
        lead.promoted_company_id = company.id

        contact: Contact | None = None
        if lead.contact_name or lead.email or lead.whatsapp_e164:
            full_name = lead.contact_name or f"Contato {lead.company_name}"
            contact = Contact(
                tenant_id=tenant_id,
                full_name=full_name,
                preferred_name=full_name.split()[0],
                job_title=lead.job_title,
                email=lead.email,
                whatsapp_e164=lead.whatsapp_e164,
                preferred_channel="whatsapp",
                lead_score=lead.icp_score,
            )
            session.add(contact)
            await session.flush()
            lead.promoted_contact_id = contact.id
            session.add(
                CompanyContact(
                    tenant_id=tenant_id,
                    company_id=company.id,
                    contact_id=contact.id,
                    role_description=lead.job_title,
                    is_primary_decision_maker=True,
                )
            )

        if payload.create_deal:
            pipeline = (
                await session.execute(
                    select(Pipeline)
                    .options(selectinload(Pipeline.stages))
                    .where(Pipeline.tenant_id == tenant_id, Pipeline.is_active.is_(True))
                    .order_by(Pipeline.is_default.desc(), Pipeline.created_at.asc())
                )
            ).scalars().first()
            if pipeline and pipeline.stages:
                first_stage = sorted(pipeline.stages, key=lambda s: s.display_order)[0]
                mrr_dec = Decimal(str(payload.estimated_mrr)).quantize(Decimal("0.01"))
                deal = Deal(
                    tenant_id=tenant_id,
                    title=payload.deal_title or f"Prospecção Outbound - {lead.company_name}",
                    company_id=company.id,
                    primary_contact_id=contact.id if contact else None,
                    pipeline_id=pipeline.id,
                    stage_id=first_stage.id,
                    status=DealStatus.OPEN,
                    mrr_value=mrr_dec,
                    one_time_value=Decimal("0.00"),
                    total_contract_value=(mrr_dec * Decimal(12)).quantize(Decimal("0.01")),
                    contract_months=12,
                    win_probability=first_stage.win_probability,
                    stage_entered_at=now,
                )
                session.add(deal)
                await session.flush()
                lead.promoted_deal_id = deal.id
                session.add(
                    DealStageHistory(
                        tenant_id=tenant_id,
                        deal_id=deal.id,
                        from_stage_id=None,
                        to_stage_id=first_stage.id,
                        changed_by_ai=True,
                        entered_at=now,
                    )
                )

        if payload.send_opening_whatsapp and lead.whatsapp_e164:
            if not lead.generated_opening_message:
                await cls.enrich_outbound_lead(session, lead)
            msg_text = lead.generated_opening_message or (
                f"Olá {lead.contact_name or ''}, tudo bem? Identificamos a {lead.company_name} "
                f"e gostaria de trocar uma ideia rápida sobre como podemos apoiar a operação de vocês."
            )
            wa_inst = await WhatsAppService.get_primary_active_instance(
                session=session,
                tenant_id=tenant_id,
                preferred_instance_name=payload.instance_name,
            )
            if wa_inst is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Nenhuma instância WhatsApp ativa para envio da mensagem inicial.",
                )
            evo = EvolutionAPIClient(base_url=wa_inst.evolution_api_url, api_key=wa_inst.evolution_api_key)
            await evo.send_text(instance_name=wa_inst.instance_name, number=lead.whatsapp_e164, text=msg_text, delay_ms=800)

            thread_digits = re.sub(r"\D", "", lead.whatsapp_e164)
            conv = AIConversation(
                tenant_id=tenant_id,
                company_id=company.id,
                contact_id=contact.id if contact else None,
                deal_id=lead.promoted_deal_id,
                channel="whatsapp",
                external_thread_id=thread_digits or lead.whatsapp_e164,
                lead_identifier=lead.whatsapp_e164,
                status=AIConversationStatus.ACTIVE_AI,
                turn_count=1,
                last_outbound_at=now,
            )
            session.add(conv)
            await session.flush()
            lead.conversation_id = conv.id
            session.add(
                AIMessage(
                    tenant_id=tenant_id,
                    conversation_id=conv.id,
                    role="assistant",
                    sender_type="ai_outbound",
                    content=msg_text,
                )
            )
            lead.status = OutboundLeadStatus.CONTACTED
        else:
            lead.status = OutboundLeadStatus.CONVERTED

        await session.commit()
        await session.refresh(lead)
        return lead

    @classmethod
    async def create_cadence(
        cls,
        session: AsyncSession,
        tenant_id: uuid.UUID,
        payload: CadenceSequenceCreate,
    ) -> CadenceSequence:
        dup = (
            await session.execute(
                select(CadenceSequence.id).where(
                    CadenceSequence.tenant_id == tenant_id,
                    CadenceSequence.slug == payload.slug,
                )
            )
        ).scalar_one_or_none()
        if dup is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Já existe uma cadência com o slug '{payload.slug}' neste tenant.",
            )

        seq = CadenceSequence(
            tenant_id=tenant_id,
            name=payload.name.strip(),
            slug=payload.slug.strip(),
            channel=payload.channel,
            stop_on_reply=payload.stop_on_reply,
            is_active=payload.is_active,
        )
        session.add(seq)
        await session.flush()

        for st in sorted(payload.steps, key=lambda s: s.step_order):
            session.add(
                CadenceStep(
                    tenant_id=tenant_id,
                    sequence_id=seq.id,
                    step_order=st.step_order,
                    delay_minutes_from_prev=st.delay_minutes_from_prev,
                    channel=st.channel or payload.channel,
                    objective=st.objective.strip(),
                    message_template=st.message_template.strip() if st.message_template else None,
                )
            )

        await session.commit()
        stmt = (
            select(CadenceSequence)
            .options(selectinload(CadenceSequence.steps))
            .where(CadenceSequence.id == seq.id)
        )
        return (await session.execute(stmt)).scalar_one()

    @classmethod
    async def enroll_in_cadence(
        cls,
        session: AsyncSession,
        tenant_id: uuid.UUID,
        payload: CadenceEnrollRequest,
    ) -> CadenceEnrollment:
        seq = (
            await session.execute(
                select(CadenceSequence)
                .options(selectinload(CadenceSequence.steps))
                .where(
                    CadenceSequence.id == payload.sequence_id,
                    CadenceSequence.tenant_id == tenant_id,
                    CadenceSequence.is_active.is_(True),
                )
            )
        ).scalar_one_or_none()
        if seq is None or not seq.steps:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Cadência ativa sem etapas encontrada.")

        first_step = sorted(seq.steps, key=lambda s: s.step_order)[0]
        delay = 0 if payload.start_immediately else first_step.delay_minutes_from_prev
        now = datetime.now(timezone.utc)

        enrollment = CadenceEnrollment(
            tenant_id=tenant_id,
            sequence_id=seq.id,
            conversation_id=payload.conversation_id,
            company_id=payload.company_id,
            contact_id=payload.contact_id,
            deal_id=payload.deal_id,
            target_identifier=payload.target_identifier.strip(),
            channel=payload.channel or first_step.channel or seq.channel,
            current_step_order=first_step.step_order,
            status=EnrollmentStatus.ACTIVE,
            next_run_at=now + timedelta(minutes=delay),
        )
        session.add(enrollment)
        await session.commit()
        await session.refresh(enrollment)
        return enrollment

    @classmethod
    async def execute_due_cadence_step(
        cls,
        session: AsyncSession,
        enrollment: CadenceEnrollment,
    ) -> CadenceEnrollment:
        seq = (
            await session.execute(
                select(CadenceSequence)
                .options(selectinload(CadenceSequence.steps))
                .where(CadenceSequence.id == enrollment.sequence_id)
            )
        ).scalar_one_or_none()
        if seq is None or not seq.is_active:
            enrollment.status = EnrollmentStatus.PAUSED
            await session.commit()
            return enrollment

        conv: AIConversation | None = None
        if enrollment.conversation_id:
            conv = (
                await session.execute(
                    select(AIConversation).where(AIConversation.id == enrollment.conversation_id)
                )
            ).scalar_one_or_none()
            if conv is not None:
                if conv.status == AIConversationStatus.HUMAN_HANDOFF:
                    enrollment.status = EnrollmentStatus.STOPPED_HANDOFF
                    await session.commit()
                    return enrollment
                if seq.stop_on_reply and conv.last_inbound_at and conv.last_inbound_at > enrollment.enrolled_at:
                    enrollment.status = EnrollmentStatus.STOPPED_REPLIED
                    await session.commit()
                    return enrollment

        ordered_steps = sorted(seq.steps, key=lambda s: s.step_order)
        current_step: CadenceStep | None = None
        next_step: CadenceStep | None = None
        for idx, st in enumerate(ordered_steps):
            if st.step_order == enrollment.current_step_order:
                current_step = st
                if idx + 1 < len(ordered_steps):
                    next_step = ordered_steps[idx + 1]
                break

        if current_step is None:
            enrollment.status = EnrollmentStatus.COMPLETED
            await session.commit()
            return enrollment

        company_name = "sua empresa"
        contact_name = "cliente"
        if enrollment.company_id:
            comp = (await session.execute(select(Company).where(Company.id == enrollment.company_id))).scalar_one_or_none()
            if comp:
                company_name = comp.trade_name or comp.legal_name
        if enrollment.contact_id:
            cont = (await session.execute(select(Contact).where(Contact.id == enrollment.contact_id))).scalar_one_or_none()
            if cont:
                contact_name = cont.preferred_name or cont.full_name

        if current_step.message_template:
            msg_text = (
                current_step.message_template.replace("{{company_name}}", company_name)
                .replace("{{contact_name}}", contact_name)
                .replace("{{objective}}", current_step.objective)
            )
        else:
            prompt = (
                f"Você é um executivo SDR B2B executando a etapa #{current_step.step_order} de uma cadência comercial.\n"
                f"Contato: {contact_name} | Empresa: {company_name}\n"
                f"Objetivo desta etapa: {current_step.objective}\n"
                f"Escreva uma mensagem curta, objetiva, humana e consultiva para o canal {current_step.channel}. "
                f"Retorne APENAS o texto da mensagem."
            )
            client = get_genai_client()
            resp = await generate_content_resilient(
                client=client,
                contents=prompt,
                config=types.GenerateContentConfig(temperature=0.45),
            )
            msg_text = (resp.text or current_step.objective).strip()

        try:
            if enrollment.channel == "whatsapp":
                wa_inst = await WhatsAppService.get_primary_active_instance(
                    session=session,
                    tenant_id=enrollment.tenant_id,
                )
                if wa_inst is None:
                    raise RuntimeError("Nenhuma instância WhatsApp ativa para o tenant.")
                evo = EvolutionAPIClient(base_url=wa_inst.evolution_api_url, api_key=wa_inst.evolution_api_key)
                await evo.send_text(
                    instance_name=wa_inst.instance_name,
                    number=enrollment.target_identifier,
                    text=msg_text,
                    delay_ms=900,
                )

            now = datetime.now(timezone.utc)
            enrollment.last_executed_at = now
            enrollment.last_message_sent = msg_text
            enrollment.last_error = None

            if conv is not None:
                conv.last_outbound_at = now
                session.add(
                    AIMessage(
                        tenant_id=enrollment.tenant_id,
                        conversation_id=conv.id,
                        role="assistant",
                        sender_type="ai_cadence",
                        content=msg_text,
                    )
                )

            if next_step is not None:
                enrollment.current_step_order = next_step.step_order
                enrollment.next_run_at = now + timedelta(minutes=next_step.delay_minutes_from_prev)
            else:
                enrollment.status = EnrollmentStatus.COMPLETED

            await session.commit()
            await session.refresh(enrollment)
            return enrollment
        except Exception as exc:
            enrollment.last_error = str(exc)[:500]
            await session.commit()
            return enrollment


async def process_due_cadences_once() -> None:
    lock_acquired = await redis_client.set("saas:lock:cadence_worker", "1", nx=True, ex=25)
    if not lock_acquired:
        return
    now = datetime.now(timezone.utc)
    async with AsyncSessionLocal() as session:
        due_rows = (
            await session.execute(
                select(CadenceEnrollment.id, CadenceEnrollment.tenant_id)
                .where(
                    CadenceEnrollment.status == EnrollmentStatus.ACTIVE,
                    CadenceEnrollment.next_run_at <= now,
                )
                .order_by(CadenceEnrollment.next_run_at.asc())
                .limit(10)
            )
        ).all()

    for enroll_id, tenant_id in due_rows:
        async with AsyncSessionLocal() as e_session:
            await set_tenant_rls_context(e_session, tenant_id)
            enroll = (
                await e_session.execute(
                    select(CadenceEnrollment).where(CadenceEnrollment.id == enroll_id)
                )
            ).scalar_one_or_none()
            if enroll is not None and enroll.status == EnrollmentStatus.ACTIVE:
                await OutboundAndCadenceService.execute_due_cadence_step(e_session, enroll)
