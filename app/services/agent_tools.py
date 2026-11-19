from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from google.genai import types
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.ai_engine import (
    AIConversation,
    AIConversationStatus,
    AIFollowupStatus,
    AIFollowupTask,
)
from app.models.core import AuditLog
from app.models.crm import (
    Company,
    CompanyContact,
    Contact,
    Deal,
    DealStageHistory,
    DealStatus,
    LifecycleStage,
    Pipeline,
    PipelineStage,
)
from app.models.proposals import BillingType
from app.schemas.proposals import ProposalCreate, ProposalItemInput
from app.services.gemini_rag import GeminiRAGService
from app.services.proposal_service import ProposalService, build_public_proposal_pdf_url, build_public_proposal_url


def build_sdr_tool_declarations() -> types.Tool:
    return types.Tool(
        function_declarations=[
            types.FunctionDeclaration(
                name="search_knowledge_base",
                description=(
                    "Busca informações oficiais da empresa na base de conhecimento vetorial (RAG): "
                    "planos, preços, SLAs, especificações técnicas, políticas comerciais, cases e contorno de objeções. "
                    "Sempre consulte esta ferramenta antes de responder dúvidas sobre produto, preço ou condições."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "Pergunta ou termos-chave para busca semântica na base de conhecimento.",
                        },
                        "category": {
                            "type": "string",
                            "description": "Filtro opcional de categoria (ex: precos, tecnico, objecoes, institucional).",
                        },
                    },
                    "required": ["query"],
                },
            ),
            types.FunctionDeclaration(
                name="lookup_crm_context",
                description=(
                    "Consulta o banco de dados do CRM para verificar se o lead, empresa ou negócio já possui cadastro, "
                    "histórico de compras, estágio atual no funil ou campos personalizados."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "phone_or_email": {
                            "type": "string",
                            "description": "Telefone, WhatsApp ou e-mail do contato.",
                        },
                        "cnpj_or_company_name": {
                            "type": "string",
                            "description": "CNPJ ou nome da empresa para busca.",
                        },
                    },
                },
            ),
            types.FunctionDeclaration(
                name="upsert_lead_and_company",
                description=(
                    "Cria ou atualiza os dados cadastrais do Contato e da Empresa B2B no CRM à medida que o lead "
                    "informa nome, empresa, cargo, cidade/UF, e-mail ou dores operacionais."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "contact_name": {"type": "string", "description": "Nome completo ou nome preferido do contato."},
                        "company_name": {"type": "string", "description": "Nome fantasia ou razão social da empresa."},
                        "email": {"type": "string", "description": "E-mail corporativo do contato."},
                        "whatsapp": {"type": "string", "description": "Número WhatsApp em formato internacional (ex: +5541999999999)."},
                        "cnpj": {"type": "string", "description": "CNPJ da empresa se informado."},
                        "job_title": {"type": "string", "description": "Cargo do contato (ex: Diretor de TI, Sócio, Gerente)."},
                        "city": {"type": "string", "description": "Cidade da empresa."},
                        "state_uf": {"type": "string", "description": "Sigla do estado com 2 letras (ex: PR, SP, SC)."},
                        "pain_points": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Lista de dores ou necessidades identificadas na conversa.",
                        },
                        "icp_score": {
                            "type": "integer",
                            "description": "Score de aderência ao Perfil de Cliente Ideal de 0 a 100.",
                        },
                    },
                    "required": ["contact_name", "company_name"],
                },
            ),
            types.FunctionDeclaration(
                name="update_bant_qualification",
                description=(
                    "Registra e atualiza os critérios de qualificação BANT (Budget, Authority, Need, Timeline) "
                    "e a pontuação do lead conforme descobertos no diálogo."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "budget": {"type": "string", "description": "Orçamento disponível ou faixa de investimento."},
                        "authority": {"type": "string", "description": "Papel na decisão (decisor, influenciador, avaliador)."},
                        "need": {"type": "string", "description": "Problema principal e escopo técnico/comercial desejado."},
                        "timeline": {"type": "string", "description": "Prazo previsto para contratação ou implantação."},
                        "lead_score": {"type": "integer", "description": "Score atualizado de qualificação (0 a 100)."},
                    },
                },
            ),
            types.FunctionDeclaration(
                name="create_or_advance_deal",
                description=(
                    "Cria uma nova oportunidade comercial (Deal) no funil de vendas ou movimenta o negócio existente "
                    "para uma etapa mais avançada (ex: Qualificado, Proposta, Reunião Agendada)."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "title": {"type": "string", "description": "Título claro da oportunidade comercial."},
                        "estimated_mrr": {"type": "number", "description": "Valor mensal recorrente estimado (MRR) em BRL."},
                        "contract_months": {"type": "integer", "description": "Duração estimada do contrato em meses (padrão 12)."},
                        "target_stage_name": {
                            "type": "string",
                            "description": "Nome aproximado da etapa destino no funil (ex: Qualificação, Diagnóstico, Proposta).",
                        },
                    },
                    "required": ["title"],
                },
            ),
            types.FunctionDeclaration(
                name="create_commercial_proposal",
                description=(
                    "Gera uma proposta comercial formal com cálculo de MRR, Setup e Valor Global do Contrato (TCV), "
                    "sincroniza o Deal para Proposta Enviada e retorna o link público de aceite digital e download do PDF."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "title": {"type": "string", "description": "Título da proposta comercial."},
                        "contract_months": {"type": "integer", "description": "Prazo do contrato em meses (ex: 12, 24)."},
                        "discount_percent": {"type": "number", "description": "Desconto comercial percentual (0 a 20)."},
                        "executive_summary": {"type": "string", "description": "Resumo técnico-comercial da solução proposta."},
                        "items": {
                            "type": "array",
                            "description": "Lista de itens da proposta.",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "name": {"type": "string"},
                                    "description": {"type": "string"},
                                    "billing_type": {"type": "string", "description": "recurring ou one_time"},
                                    "quantity": {"type": "number"},
                                    "unit_price": {"type": "number"},
                                },
                                "required": ["name", "quantity", "unit_price"],
                            },
                        },
                    },
                    "required": ["title", "items"],
                },
            ),
            types.FunctionDeclaration(
                name="schedule_autonomous_followup",
                description=(
                    "Agenda um follow-up autônomo futuro quando o lead pedir para retornar mais tarde, "
                    "precisar validar internamente ou deixar a conversa em aberto."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "delay_minutes": {
                            "type": "integer",
                            "description": "Minutos até o disparo do próximo contato (ex: 60, 240, 1440).",
                        },
                        "objective": {
                            "type": "string",
                            "description": "Objetivo específico do recontato (ex: Retomar proposta de 4 racks após reunião com sócio).",
                        },
                        "channel": {
                            "type": "string",
                            "description": "Canal preferencial (whatsapp ou email).",
                        },
                    },
                    "required": ["delay_minutes", "objective"],
                },
            ),
            types.FunctionDeclaration(
                name="escalate_to_human_closer",
                description=(
                    "Transfere imediatamente o atendimento para um Executivo de Contas / Closer humano quando o lead "
                    "estiver qualificado para fechamento, solicitar proposta customizada complexa, pedir atendimento humano "
                    "ou apresentar objeção crítica."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "reason": {"type": "string", "description": "Motivo objetivo do transbordo."},
                        "summary_for_closer": {
                            "type": "string",
                            "description": "Resumo executivo completo (empresa, contato, dores, BANT e próximos passos) para o vendedor humano.",
                        },
                    },
                    "required": ["reason", "summary_for_closer"],
                },
            ),
        ]
    )


@dataclass
class AgentToolExecutor:
    session: AsyncSession
    tenant_id: uuid.UUID
    conversation: AIConversation
    rag_sources_collected: list[dict[str, Any]] = field(default_factory=list)

    async def execute(self, tool_name: str, args: dict[str, Any]) -> dict[str, Any]:
        handlers = {
            "search_knowledge_base": self._search_knowledge_base,
            "lookup_crm_context": self._lookup_crm_context,
            "upsert_lead_and_company": self._upsert_lead_and_company,
            "update_bant_qualification": self._update_bant_qualification,
            "create_or_advance_deal": self._create_or_advance_deal,
            "create_commercial_proposal": self._create_commercial_proposal,
            "schedule_autonomous_followup": self._schedule_autonomous_followup,
            "escalate_to_human_closer": self._escalate_to_human_closer,
        }
        handler = handlers.get(tool_name)
        if handler is None:
            return {"status": "error", "message": f"Ferramenta desconhecida: {tool_name}"}
        try:
            return await handler(**args)
        except Exception as exc:
            return {"status": "error", "message": f"Erro ao executar {tool_name}: {exc}"}

    async def _search_knowledge_base(
        self,
        query: str,
        category: str | None = None,
    ) -> dict[str, Any]:
        hits = await GeminiRAGService.semantic_search(
            session=self.session,
            tenant_id=self.tenant_id,
            query=query,
            category=category,
            top_k=4,
            min_similarity=0.40,
        )
        for h in hits:
            self.rag_sources_collected.append(
                {
                    "document_title": h["document_title"],
                    "category": h["category"],
                    "similarity": h["similarity"],
                }
            )
        if not hits:
            return {
                "found": False,
                "message": "Nenhum trecho específico encontrado na base de conhecimento para esta consulta.",
            }
        return {
            "found": True,
            "results": [
                {
                    "fonte": h["document_title"],
                    "categoria": h["category"],
                    "relevancia": h["similarity"],
                    "conteudo": h["content"],
                }
                for h in hits
            ],
        }

    async def _lookup_crm_context(
        self,
        phone_or_email: str | None = None,
        cnpj_or_company_name: str | None = None,
    ) -> dict[str, Any]:
        contact: Contact | None = None
        company: Company | None = None

        if self.conversation.contact_id:
            contact = (
                await self.session.execute(
                    select(Contact).where(
                        Contact.id == self.conversation.contact_id,
                        Contact.tenant_id == self.tenant_id,
                    )
                )
            ).scalar_one_or_none()

        if contact is None and phone_or_email:
            term = phone_or_email.strip()
            digits = re.sub(r"\D", "", term)
            filters = [Contact.email.ilike(term)]
            if digits:
                filters.append(Contact.whatsapp_e164.ilike(f"%{digits}%"))
                filters.append(Contact.phone.ilike(f"%{digits}%"))
            contact = (
                await self.session.execute(
                    select(Contact).where(
                        Contact.tenant_id == self.tenant_id,
                        Contact.deleted_at.is_(None),
                        or_(*filters),
                    )
                )
            ).scalars().first()
            if contact is not None:
                self.conversation.contact_id = contact.id

        if self.conversation.company_id:
            company = (
                await self.session.execute(
                    select(Company).where(
                        Company.id == self.conversation.company_id,
                        Company.tenant_id == self.tenant_id,
                    )
                )
            ).scalar_one_or_none()

        if company is None and cnpj_or_company_name:
            term = cnpj_or_company_name.strip()
            company = (
                await self.session.execute(
                    select(Company).where(
                        Company.tenant_id == self.tenant_id,
                        Company.deleted_at.is_(None),
                        or_(
                            Company.cnpj.ilike(f"%{term}%"),
                            Company.legal_name.ilike(f"%{term}%"),
                            Company.trade_name.ilike(f"%{term}%"),
                        ),
                    )
                )
            ).scalars().first()
            if company is not None:
                self.conversation.company_id = company.id

        if company is None and contact is not None:
            link = (
                await self.session.execute(
                    select(CompanyContact).where(
                        CompanyContact.tenant_id == self.tenant_id,
                        CompanyContact.contact_id == contact.id,
                        CompanyContact.is_active.is_(True),
                    )
                )
            ).scalars().first()
            if link is not None:
                company = (
                    await self.session.execute(select(Company).where(Company.id == link.company_id))
                ).scalar_one_or_none()
                if company is not None:
                    self.conversation.company_id = company.id

        deals_summary: list[dict[str, Any]] = []
        if company is not None:
            deals = (
                await self.session.execute(
                    select(Deal).where(
                        Deal.tenant_id == self.tenant_id,
                        Deal.company_id == company.id,
                        Deal.deleted_at.is_(None),
                    )
                )
            ).scalars().all()
            for d in deals:
                if self.conversation.deal_id is None and d.status == DealStatus.OPEN:
                    self.conversation.deal_id = d.id
                deals_summary.append(
                    {
                        "deal_id": str(d.id),
                        "title": d.title,
                        "status": d.status.value,
                        "mrr_value": float(d.mrr_value),
                        "total_contract_value": float(d.total_contract_value),
                        "qualification_data": d.qualification_data,
                    }
                )

        return {
            "contact": {
                "id": str(contact.id),
                "full_name": contact.full_name,
                "job_title": contact.job_title,
                "email": contact.email,
                "whatsapp": contact.whatsapp_e164,
                "lead_score": contact.lead_score,
            }
            if contact
            else None,
            "company": {
                "id": str(company.id),
                "legal_name": company.legal_name,
                "trade_name": company.trade_name,
                "cnpj": company.cnpj,
                "city": company.city,
                "state_uf": company.state_uf,
                "lifecycle_stage": company.lifecycle_stage.value,
                "icp_score": company.icp_score,
                "pain_points": company.identified_pain_points,
                "custom_fields": company.custom_fields,
            }
            if company
            else None,
            "deals": deals_summary,
        }

    async def _upsert_lead_and_company(
        self,
        contact_name: str,
        company_name: str,
        email: str | None = None,
        whatsapp: str | None = None,
        cnpj: str | None = None,
        job_title: str | None = None,
        city: str | None = None,
        state_uf: str | None = None,
        pain_points: list[str] | None = None,
        icp_score: int | None = None,
    ) -> dict[str, Any]:
        company: Company | None = None
        if self.conversation.company_id:
            company = (
                await self.session.execute(
                    select(Company).where(Company.id == self.conversation.company_id)
                )
            ).scalar_one_or_none()

        if company is None:
            filters = [Company.legal_name.ilike(company_name.strip()), Company.trade_name.ilike(company_name.strip())]
            if cnpj:
                filters.append(Company.cnpj == cnpj.strip())
            company = (
                await self.session.execute(
                    select(Company).where(
                        Company.tenant_id == self.tenant_id,
                        Company.deleted_at.is_(None),
                        or_(*filters),
                    )
                )
            ).scalars().first()

        if company is None:
            company = Company(
                tenant_id=self.tenant_id,
                legal_name=company_name.strip(),
                trade_name=company_name.strip(),
                cnpj=cnpj.strip() if cnpj else None,
                primary_email=email.strip().lower() if email else None,
                whatsapp_number=whatsapp.strip() if whatsapp else self.conversation.lead_identifier,
                city=city.strip() if city else None,
                state_uf=state_uf.strip().upper()[:2] if state_uf else None,
                lifecycle_stage=LifecycleStage.SQL,
                lead_source=f"ai_sdr_{self.conversation.channel}",
                icp_score=max(0, min(100, icp_score)) if icp_score is not None else 50,
                identified_pain_points=pain_points or [],
            )
            self.session.add(company)
            await self.session.flush()
        else:
            if cnpj and not company.cnpj:
                company.cnpj = cnpj.strip()
            if email and not company.primary_email:
                company.primary_email = email.strip().lower()
            if city:
                company.city = city.strip()
            if state_uf:
                company.state_uf = state_uf.strip().upper()[:2]
            if icp_score is not None:
                company.icp_score = max(0, min(100, icp_score))
            if pain_points:
                existing_pains = list(company.identified_pain_points or [])
                for p in pain_points:
                    if p not in existing_pains:
                        existing_pains.append(p)
                company.identified_pain_points = existing_pains
            if company.lifecycle_stage in (LifecycleStage.PROSPECT, LifecycleStage.LEAD, LifecycleStage.MQL):
                company.lifecycle_stage = LifecycleStage.SQL

        self.conversation.company_id = company.id

        contact: Contact | None = None
        if self.conversation.contact_id:
            contact = (
                await self.session.execute(
                    select(Contact).where(Contact.id == self.conversation.contact_id)
                )
            ).scalar_one_or_none()

        if contact is None:
            contact = Contact(
                tenant_id=self.tenant_id,
                full_name=contact_name.strip(),
                preferred_name=contact_name.strip().split()[0],
                job_title=job_title.strip() if job_title else None,
                email=email.strip().lower() if email else None,
                whatsapp_e164=whatsapp.strip() if whatsapp else self.conversation.lead_identifier,
                preferred_channel=self.conversation.channel,
                lead_score=max(0, min(100, icp_score)) if icp_score is not None else 50,
            )
            self.session.add(contact)
            await self.session.flush()
        else:
            if contact_name:
                contact.full_name = contact_name.strip()
            if job_title:
                contact.job_title = job_title.strip()
            if email and not contact.email:
                contact.email = email.strip().lower()
            if whatsapp and not contact.whatsapp_e164:
                contact.whatsapp_e164 = whatsapp.strip()

        self.conversation.contact_id = contact.id

        existing_link = (
            await self.session.execute(
                select(CompanyContact).where(
                    CompanyContact.tenant_id == self.tenant_id,
                    CompanyContact.company_id == company.id,
                    CompanyContact.contact_id == contact.id,
                )
            )
        ).scalar_one_or_none()
        if existing_link is None:
            self.session.add(
                CompanyContact(
                    tenant_id=self.tenant_id,
                    company_id=company.id,
                    contact_id=contact.id,
                    role_description=job_title,
                    is_primary_decision_maker=True,
                )
            )

        await self.session.flush()
        return {
            "status": "ok",
            "company_id": str(company.id),
            "contact_id": str(contact.id),
            "lifecycle_stage": company.lifecycle_stage.value,
        }

    async def _update_bant_qualification(
        self,
        budget: str | None = None,
        authority: str | None = None,
        need: str | None = None,
        timeline: str | None = None,
        lead_score: int | None = None,
    ) -> dict[str, Any]:
        qual = dict(self.conversation.qualification_state or {})
        if budget is not None:
            qual["budget"] = budget
        if authority is not None:
            qual["authority"] = authority
        if need is not None:
            qual["need"] = need
        if timeline is not None:
            qual["timeline"] = timeline
        if lead_score is not None:
            qual["lead_score"] = max(0, min(100, int(lead_score)))

        self.conversation.qualification_state = qual

        if self.conversation.contact_id and lead_score is not None:
            contact = (
                await self.session.execute(select(Contact).where(Contact.id == self.conversation.contact_id))
            ).scalar_one_or_none()
            if contact is not None:
                contact.lead_score = qual["lead_score"]

        if self.conversation.deal_id:
            deal = (
                await self.session.execute(select(Deal).where(Deal.id == self.conversation.deal_id))
            ).scalar_one_or_none()
            if deal is not None:
                deal_qual = dict(deal.qualification_data or {})
                for k in ("budget", "authority", "need", "timeline"):
                    if qual.get(k) is not None:
                        deal_qual[k] = qual[k]
                deal.qualification_data = deal_qual

        await self.session.flush()
        return {"status": "ok", "qualification_state": qual}

    async def _create_or_advance_deal(
        self,
        title: str,
        estimated_mrr: float = 0.0,
        contract_months: int = 12,
        target_stage_name: str | None = None,
    ) -> dict[str, Any]:
        if self.conversation.company_id is None:
            return {
                "status": "error",
                "message": "Cadastre a empresa com upsert_lead_and_company antes de criar o Deal.",
            }

        pipeline = (
            await self.session.execute(
                select(Pipeline)
                .options(selectinload(Pipeline.stages))
                .where(Pipeline.tenant_id == self.tenant_id, Pipeline.is_active.is_(True))
                .order_by(Pipeline.is_default.desc(), Pipeline.created_at.asc())
            )
        ).scalars().first()

        if pipeline is None or not pipeline.stages:
            pipeline = Pipeline(
                tenant_id=self.tenant_id,
                name="Funil Comercial B2B (Padrão)",
                pipeline_type="sales",
                is_default=True,
                is_active=True,
            )
            self.session.add(pipeline)
            await self.session.flush()
            default_stages = [
                ("Novo Lead", 1, 10, "#64748B", False, False),
                ("Qualificação SDR", 2, 25, "#3B82F6", False, False),
                ("Diagnóstico / Demo", 3, 50, "#8B5CF6", False, False),
                ("Proposta Enviada", 4, 75, "#F59E0B", False, False),
                ("Fechado Ganho", 5, 100, "#10B981", True, False),
                ("Perdido", 6, 0, "#EF4444", False, True),
            ]
            created_stages: list[PipelineStage] = []
            for st_name, order, prob, color, won, lost in default_stages:
                st = PipelineStage(
                    tenant_id=self.tenant_id,
                    pipeline_id=pipeline.id,
                    name=st_name,
                    display_order=order,
                    win_probability=prob,
                    color_hex=color,
                    is_won_stage=won,
                    is_lost_stage=lost,
                )
                self.session.add(st)
                created_stages.append(st)
            await self.session.flush()
            stages_sorted = created_stages
        else:
            stages_sorted = sorted(pipeline.stages, key=lambda s: s.display_order)

        chosen_stage = stages_sorted[0]
        if target_stage_name:
            needle = target_stage_name.strip().lower()
            for st in stages_sorted:
                if needle in st.name.lower():
                    chosen_stage = st
                    break

        now = datetime.now(timezone.utc)
        mrr_dec = Decimal(str(max(0.0, estimated_mrr))).quantize(Decimal("0.01"))
        months = max(1, int(contract_months))
        tcv_dec = (mrr_dec * Decimal(months)).quantize(Decimal("0.01"))

        deal: Deal | None = None
        if self.conversation.deal_id:
            deal = (
                await self.session.execute(select(Deal).where(Deal.id == self.conversation.deal_id))
            ).scalar_one_or_none()

        if deal is None:
            deal = Deal(
                tenant_id=self.tenant_id,
                title=title.strip(),
                company_id=self.conversation.company_id,
                primary_contact_id=self.conversation.contact_id,
                pipeline_id=pipeline.id,
                stage_id=chosen_stage.id,
                status=DealStatus.OPEN,
                mrr_value=mrr_dec,
                one_time_value=Decimal("0.00"),
                total_contract_value=tcv_dec,
                contract_months=months,
                win_probability=chosen_stage.win_probability,
                stage_entered_at=now,
                qualification_data=self.conversation.qualification_state,
            )
            self.session.add(deal)
            await self.session.flush()
            self.conversation.deal_id = deal.id

            self.session.add(
                DealStageHistory(
                    tenant_id=self.tenant_id,
                    deal_id=deal.id,
                    from_stage_id=None,
                    to_stage_id=chosen_stage.id,
                    changed_by_ai=True,
                    entered_at=now,
                )
            )
        else:
            prev_stage = deal.stage_id
            if mrr_dec > 0:
                deal.mrr_value = mrr_dec
                deal.contract_months = months
                deal.total_contract_value = tcv_dec
            if target_stage_name and prev_stage != chosen_stage.id:
                duration_sec = max(0, int((now - deal.stage_entered_at).total_seconds()))
                deal.stage_id = chosen_stage.id
                deal.stage_entered_at = now
                deal.win_probability = chosen_stage.win_probability
                self.session.add(
                    DealStageHistory(
                        tenant_id=self.tenant_id,
                        deal_id=deal.id,
                        from_stage_id=prev_stage,
                        to_stage_id=chosen_stage.id,
                        changed_by_ai=True,
                        duration_in_previous_stage_seconds=duration_sec,
                        entered_at=now,
                    )
                )

        await self.session.flush()
        return {
            "status": "ok",
            "deal_id": str(deal.id),
            "title": deal.title,
            "stage_name": chosen_stage.name,
            "mrr_value": float(deal.mrr_value),
            "total_contract_value": float(deal.total_contract_value),
        }

    async def _create_commercial_proposal(
        self,
        title: str,
        items: list[dict[str, Any]],
        contract_months: int = 12,
        discount_percent: float = 0.0,
        executive_summary: str | None = None,
    ) -> dict[str, Any]:
        parsed_items: list[ProposalItemInput] = []
        for raw in items:
            b_type = (
                BillingType.ONE_TIME
                if str(raw.get("billing_type") or "").lower() == "one_time"
                else BillingType.RECURRING
            )
            parsed_items.append(
                ProposalItemInput(
                    name=str(raw.get("name") or "Solução Comercial"),
                    description=raw.get("description"),
                    billing_type=b_type,
                    quantity=Decimal(str(raw.get("quantity") or 1)),
                    unit_price=Decimal(str(raw.get("unit_price") or 0)),
                )
            )

        prop_payload = ProposalCreate(
            title=title,
            company_id=self.conversation.company_id,
            contact_id=self.conversation.contact_id,
            deal_id=self.conversation.deal_id,
            conversation_id=self.conversation.id,
            contract_months=max(1, int(contract_months)),
            discount_percent=Decimal(str(max(0.0, min(30.0, discount_percent)))),
            executive_summary=executive_summary,
            auto_sync_deal=True,
            items=parsed_items,
        )
        prop = await ProposalService.create_proposal(
            session=self.session,
            tenant_id=self.tenant_id,
            payload=prop_payload,
        )
        if prop.deal_id and not self.conversation.deal_id:
            self.conversation.deal_id = prop.deal_id

        return {
            "status": "created",
            "proposal_id": str(prop.id),
            "proposal_code": prop.proposal_code,
            "total_mrr": float(prop.total_mrr),
            "total_one_time": float(prop.total_one_time),
            "total_contract_value": float(prop.total_contract_value),
            "public_acceptance_url": build_public_proposal_url(prop.public_token),
            "pdf_download_url": build_public_proposal_pdf_url(prop.public_token),
        }

    async def _schedule_autonomous_followup(
        self,
        delay_minutes: int,
        objective: str,
        channel: str = "whatsapp",
    ) -> dict[str, Any]:
        existing_pending = (
            await self.session.execute(
                select(AIFollowupTask).where(
                    AIFollowupTask.tenant_id == self.tenant_id,
                    AIFollowupTask.conversation_id == self.conversation.id,
                    AIFollowupTask.status == AIFollowupStatus.PENDING,
                )
            )
        ).scalars().all()
        for old in existing_pending:
            old.status = AIFollowupStatus.CANCELLED_MANUAL

        past_attempts = (
            await self.session.execute(
                select(func.count(AIFollowupTask.id)).where(
                    AIFollowupTask.tenant_id == self.tenant_id,
                    AIFollowupTask.conversation_id == self.conversation.id,
                    AIFollowupTask.status == AIFollowupStatus.SENT,
                )
            )
        ).scalar_one()

        scheduled_at = datetime.now(timezone.utc) + timedelta(minutes=max(1, int(delay_minutes)))
        task = AIFollowupTask(
            tenant_id=self.tenant_id,
            conversation_id=self.conversation.id,
            company_id=self.conversation.company_id,
            contact_id=self.conversation.contact_id,
            deal_id=self.conversation.deal_id,
            attempt_number=int(past_attempts) + 1,
            channel=channel or self.conversation.channel,
            objective=objective.strip(),
            scheduled_for=scheduled_at,
            status=AIFollowupStatus.PENDING,
        )
        self.session.add(task)
        await self.session.flush()
        return {
            "status": "scheduled",
            "followup_task_id": str(task.id),
            "attempt_number": task.attempt_number,
            "scheduled_for": scheduled_at.isoformat(),
        }

    async def _escalate_to_human_closer(
        self,
        reason: str,
        summary_for_closer: str,
    ) -> dict[str, Any]:
        self.conversation.status = AIConversationStatus.HUMAN_HANDOFF
        self.conversation.handoff_reason = reason.strip()
        self.conversation.handoff_summary = summary_for_closer.strip()

        self.session.add(
            AuditLog(
                tenant_id=self.tenant_id,
                actor_type="ai_agent",
                entity_type="ai_conversation",
                record_id=self.conversation.id,
                action="human_handoff",
                changes_after={
                    "reason": self.conversation.handoff_reason,
                    "summary": self.conversation.handoff_summary,
                },
            )
        )
        await self.session.flush()
        return {
            "status": "escalated",
            "conversation_status": "human_handoff",
            "message": "Conversa transferida para o Executivo de Contas humano. Informe ao cliente que um especialista dará continuidade.",
        }
