from __future__ import annotations

import base64
import logging
import os
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.base import set_tenant_rls_context
from app.models.core import AuditLog, Tenant
from app.models.crm import (
    Company,
    Contact,
    Deal,
    DealStageHistory,
    DealStatus,
    LifecycleStage,
    Pipeline,
    PipelineStage,
)
from app.models.proposals import (
    BillingType,
    CatalogItem,
    CommercialProposal,
    ProposalItem,
    ProposalStatus,
)
from app.schemas.proposals import (
    ProposalCreate,
    PublicProposalAcceptRequest,
    PublicProposalAcceptResponse,
)
from app.services.evolution_client import EvolutionAPIClient
from app.services.proposal_pdf import format_brl, generate_proposal_pdf_bytes
from app.services.whatsapp_service import WhatsAppService

logger = logging.getLogger(__name__)


def get_public_base_url() -> str:
    return (
        os.getenv("EVOLUTION_WEBHOOK_PUBLIC_BASE_URL")
        or os.getenv("PUBLIC_APP_BASE_URL")
        or "http://192.168.0.9:8001"
    ).rstrip("/")


def build_public_proposal_url(public_token: str) -> str:
    return f"{get_public_base_url()}/api/v1/public/proposals/{public_token}"


def build_public_proposal_pdf_url(public_token: str) -> str:
    return f"{get_public_base_url()}/api/v1/public/proposals/{public_token}/pdf"


class ProposalService:
    @staticmethod
    async def resolve_tenant_by_token(
        session: AsyncSession,
        public_token: str,
    ) -> uuid.UUID | None:
        res = await session.execute(
            text("SELECT resolve_proposal_token_tenant(:tok)"),
            {"tok": public_token.strip()},
        )
        raw_val = res.scalar_one_or_none()
        if raw_val is None:
            return None
        return raw_val if isinstance(raw_val, uuid.UUID) else uuid.UUID(str(raw_val))

    @staticmethod
    async def _next_proposal_code(session: AsyncSession, tenant_id: uuid.UUID) -> str:
        year = datetime.now(timezone.utc).year
        count_res = await session.execute(
            select(func.count(CommercialProposal.id)).where(
                CommercialProposal.tenant_id == tenant_id
            )
        )
        seq = int(count_res.scalar_one() or 0) + 1
        return f"PROP-{year}-{seq:04d}"

    @classmethod
    async def create_proposal(
        cls,
        session: AsyncSession,
        tenant_id: uuid.UUID,
        payload: ProposalCreate,
    ) -> CommercialProposal:
        now = datetime.now(timezone.utc)
        proposal_code = await cls._next_proposal_code(session, tenant_id)
        public_token = secrets.token_urlsafe(24)

        resolved_items: list[dict[str, Any]] = []
        subtotal_mrr = Decimal("0.00")
        subtotal_one_time = Decimal("0.00")

        for idx, item_in in enumerate(payload.items, start=1):
            cat_item: CatalogItem | None = None
            if item_in.catalog_item_id:
                cat_item = (
                    await session.execute(
                        select(CatalogItem).where(
                            CatalogItem.id == item_in.catalog_item_id,
                            CatalogItem.tenant_id == tenant_id,
                        )
                    )
                ).scalar_one_or_none()
                if cat_item is None:
                    raise HTTPException(
                        status_code=status.HTTP_404_NOT_FOUND,
                        detail=f"Item de catálogo {item_in.catalog_item_id} não encontrado.",
                    )

            item_name = (item_in.name or (cat_item.name if cat_item else None) or "").strip()
            if not item_name:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=f"Item #{idx} deve informar 'name' ou 'catalog_item_id'.",
                )

            item_desc = item_in.description if item_in.description is not None else (cat_item.description if cat_item else None)
            billing_type = item_in.billing_type or (cat_item.billing_type if cat_item else BillingType.RECURRING)
            unit_price = (
                item_in.unit_price
                if item_in.unit_price is not None
                else (cat_item.unit_price if cat_item else Decimal("0.00"))
            )
            qty = Decimal(str(item_in.quantity)).quantize(Decimal("0.01"))
            u_price = Decimal(str(unit_price)).quantize(Decimal("0.01"))
            line_total = (qty * u_price).quantize(Decimal("0.01"))

            if billing_type == BillingType.RECURRING:
                subtotal_mrr += line_total
            else:
                subtotal_one_time += line_total

            resolved_items.append(
                {
                    "catalog_item_id": cat_item.id if cat_item else None,
                    "display_order": idx,
                    "name": item_name,
                    "description": item_desc,
                    "billing_type": billing_type,
                    "quantity": qty,
                    "unit_price": u_price,
                    "line_total": line_total,
                }
            )

        discount_pct = Decimal(str(payload.discount_percent)).quantize(Decimal("0.01"))
        factor = (Decimal("100.00") - discount_pct) / Decimal("100.00")

        total_mrr = (subtotal_mrr * factor).quantize(Decimal("0.01"))
        total_one_time = (subtotal_one_time * factor).quantize(Decimal("0.01"))
        months = int(payload.contract_months)

        gross_contract = (subtotal_mrr * Decimal(months)) + subtotal_one_time
        total_contract_value = ((total_mrr * Decimal(months)) + total_one_time).quantize(Decimal("0.01"))
        discount_amount = (gross_contract - total_contract_value).quantize(Decimal("0.01"))

        deal_id = payload.deal_id
        if payload.auto_sync_deal:
            deal: Deal | None = None
            if deal_id:
                deal = (
                    await session.execute(
                        select(Deal).where(Deal.id == deal_id, Deal.tenant_id == tenant_id)
                    )
                ).scalar_one_or_none()

            pipeline = (
                await session.execute(
                    select(Pipeline)
                    .options(selectinload(Pipeline.stages))
                    .where(Pipeline.tenant_id == tenant_id, Pipeline.is_active.is_(True))
                    .order_by(Pipeline.is_default.desc(), Pipeline.created_at.asc())
                )
            ).scalars().first()

            proposal_stage: PipelineStage | None = None
            if pipeline and pipeline.stages:
                stages_sorted = sorted(pipeline.stages, key=lambda s: s.display_order)
                proposal_stage = stages_sorted[0]
                for st in stages_sorted:
                    if "proposta" in st.name.lower():
                        proposal_stage = st
                        break

            if deal is None and payload.company_id and pipeline and proposal_stage:
                deal = Deal(
                    tenant_id=tenant_id,
                    title=payload.title.strip(),
                    company_id=payload.company_id,
                    primary_contact_id=payload.contact_id,
                    pipeline_id=pipeline.id,
                    stage_id=proposal_stage.id,
                    status=DealStatus.OPEN,
                    mrr_value=total_mrr,
                    one_time_value=total_one_time,
                    total_contract_value=total_contract_value,
                    contract_months=months,
                    win_probability=proposal_stage.win_probability,
                    stage_entered_at=now,
                )
                session.add(deal)
                await session.flush()
                deal_id = deal.id
                session.add(
                    DealStageHistory(
                        tenant_id=tenant_id,
                        deal_id=deal.id,
                        from_stage_id=None,
                        to_stage_id=proposal_stage.id,
                        changed_by_ai=False,
                        entered_at=now,
                    )
                )
            elif deal is not None:
                deal.mrr_value = total_mrr
                deal.one_time_value = total_one_time
                deal.contract_months = months
                deal.total_contract_value = total_contract_value
                if proposal_stage and deal.stage_id != proposal_stage.id and deal.status == DealStatus.OPEN:
                    prev_stage = deal.stage_id
                    dur = max(0, int((now - deal.stage_entered_at).total_seconds()))
                    deal.stage_id = proposal_stage.id
                    deal.stage_entered_at = now
                    deal.win_probability = proposal_stage.win_probability
                    session.add(
                        DealStageHistory(
                            tenant_id=tenant_id,
                            deal_id=deal.id,
                            from_stage_id=prev_stage,
                            to_stage_id=proposal_stage.id,
                            changed_by_ai=False,
                            duration_in_previous_stage_seconds=dur,
                            entered_at=now,
                        )
                    )

        proposal = CommercialProposal(
            tenant_id=tenant_id,
            proposal_code=proposal_code,
            public_token=public_token,
            title=payload.title.strip(),
            company_id=payload.company_id,
            contact_id=payload.contact_id,
            deal_id=deal_id,
            conversation_id=payload.conversation_id,
            status=ProposalStatus.DRAFT,
            contract_months=months,
            discount_percent=discount_pct,
            subtotal_mrr=subtotal_mrr,
            subtotal_one_time=subtotal_one_time,
            discount_amount=discount_amount,
            total_mrr=total_mrr,
            total_one_time=total_one_time,
            total_contract_value=total_contract_value,
            valid_until=now + timedelta(days=payload.valid_days),
            executive_summary=payload.executive_summary,
            terms_and_conditions=payload.terms_and_conditions,
            metadata_json=payload.metadata_json,
        )
        session.add(proposal)
        await session.flush()

        for item_data in resolved_items:
            session.add(
                ProposalItem(
                    tenant_id=tenant_id,
                    proposal_id=proposal.id,
                    **item_data,
                )
            )

        session.add(
            AuditLog(
                tenant_id=tenant_id,
                actor_type="user_or_ai",
                entity_type="commercial_proposal",
                record_id=proposal.id,
                action="create",
                changes_after={
                    "proposal_code": proposal.proposal_code,
                    "total_mrr": float(proposal.total_mrr),
                    "total_contract_value": float(proposal.total_contract_value),
                },
            )
        )

        await session.commit()
        return await cls.get_proposal_with_items(session, tenant_id, proposal.id)

    @staticmethod
    async def get_proposal_with_items(
        session: AsyncSession,
        tenant_id: uuid.UUID,
        proposal_id: uuid.UUID,
    ) -> CommercialProposal:
        stmt = (
            select(CommercialProposal)
            .options(selectinload(CommercialProposal.items))
            .where(
                CommercialProposal.id == proposal_id,
                CommercialProposal.tenant_id == tenant_id,
            )
        )
        prop = (await session.execute(stmt)).scalar_one_or_none()
        if prop is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Proposta comercial não encontrada.")
        return prop

    @classmethod
    async def render_proposal_pdf(
        cls,
        session: AsyncSession,
        tenant_id: uuid.UUID,
        proposal_id: uuid.UUID,
    ) -> tuple[CommercialProposal, bytes]:
        prop = await cls.get_proposal_with_items(session, tenant_id, proposal_id)
        tenant = (await session.execute(select(Tenant).where(Tenant.id == tenant_id))).scalar_one_or_none()
        company = (
            (await session.execute(select(Company).where(Company.id == prop.company_id))).scalar_one_or_none()
            if prop.company_id
            else None
        )
        contact = (
            (await session.execute(select(Contact).where(Contact.id == prop.contact_id))).scalar_one_or_none()
            if prop.contact_id
            else None
        )

        pdf_bytes = generate_proposal_pdf_bytes(
            proposal=prop,
            issuer_name=tenant.name if tenant else "TBTC Comercial",
            company=company,
            contact=contact,
            public_acceptance_url=build_public_proposal_url(prop.public_token),
        )
        return prop, pdf_bytes

    @classmethod
    async def send_proposal_via_whatsapp(
        cls,
        session: AsyncSession,
        tenant_id: uuid.UUID,
        proposal_id: uuid.UUID,
        recipient_number: str | None = None,
        instance_name: str | None = None,
        custom_message: str | None = None,
        attach_pdf: bool = True,
    ) -> dict[str, Any]:
        prop, pdf_bytes = await cls.render_proposal_pdf(session, tenant_id, proposal_id)

        contact = (
            (await session.execute(select(Contact).where(Contact.id == prop.contact_id))).scalar_one_or_none()
            if prop.contact_id
            else None
        )
        company = (
            (await session.execute(select(Company).where(Company.id == prop.company_id))).scalar_one_or_none()
            if prop.company_id
            else None
        )

        target = (
            recipient_number
            or (contact.whatsapp_e164 if contact else None)
            or (contact.phone if contact else None)
            or (company.whatsapp_number if company else None)
        )
        if not target:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Nenhum número WhatsApp informado nem encontrado no Contato/Empresa da proposta.",
            )

        wa_instance = await WhatsAppService.get_primary_active_instance(
            session=session,
            tenant_id=tenant_id,
            preferred_instance_name=instance_name,
        )
        if wa_instance is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Nenhuma instância WhatsApp (Evolution API) ativa encontrada para este tenant.",
            )

        public_url = build_public_proposal_url(prop.public_token)
        greeting_name = contact.preferred_name or contact.full_name if contact else "Olá"
        caption = custom_message or (
            f"Olá, {greeting_name}! Segue a *Proposta Comercial {prop.proposal_code}* ({prop.title}).\n\n"
            f"- *Investimento Mensal (MRR):* {format_brl(prop.total_mrr)}\n"
            f"- *Implantação / Setup:* {format_brl(prop.total_one_time)}\n"
            f"- *Prazo:* {prop.contract_months} meses | *Validade:* {prop.valid_until.strftime('%d/%m/%Y')}\n\n"
            f"Para revisar e aprovar digitalmente, acesse:\n{public_url}"
        )

        evo = EvolutionAPIClient(
            base_url=wa_instance.evolution_api_url,
            api_key=wa_instance.evolution_api_key,
        )

        delivery_mode = "document_pdf"
        if attach_pdf:
            try:
                b64_pdf = base64.b64encode(pdf_bytes).decode("ascii")
                evo_resp = await evo.send_media(
                    instance_name=wa_instance.instance_name,
                    number=target,
                    media_base64=b64_pdf,
                    file_name=f"{prop.proposal_code}.pdf",
                    caption=caption,
                    mediatype="document",
                    mimetype="application/pdf",
                )
            except Exception as exc:
                logger.warning("Falha no sendMedia PDF, aplicando fallback sendText: %s", exc)
                delivery_mode = "text_link_fallback"
                evo_resp = await evo.send_text(
                    instance_name=wa_instance.instance_name,
                    number=target,
                    text=caption,
                )
        else:
            delivery_mode = "text_link"
            evo_resp = await evo.send_text(
                instance_name=wa_instance.instance_name,
                number=target,
                text=caption,
            )

        now = datetime.now(timezone.utc)
        if prop.status == ProposalStatus.DRAFT:
            prop.status = ProposalStatus.SENT
        prop.sent_at = now
        await session.commit()

        return {
            "status": "sent",
            "proposal_code": prop.proposal_code,
            "recipient": target,
            "instance_name": wa_instance.instance_name,
            "delivery_mode": delivery_mode,
            "public_url": public_url,
            "evolution_response": evo_resp,
        }

    @classmethod
    async def accept_public_proposal(
        cls,
        session: AsyncSession,
        public_token: str,
        payload: PublicProposalAcceptRequest,
        client_ip: str | None = None,
    ) -> PublicProposalAcceptResponse:
        tenant_id = await cls.resolve_tenant_by_token(session, public_token)
        if tenant_id is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Proposta não encontrada.")

        await set_tenant_rls_context(session, tenant_id)

        prop = (
            await session.execute(
                select(CommercialProposal)
                .options(selectinload(CommercialProposal.items))
                .where(
                    CommercialProposal.tenant_id == tenant_id,
                    CommercialProposal.public_token == public_token.strip(),
                )
            )
        ).scalar_one_or_none()

        if prop is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Proposta não encontrada.")

        if prop.status == ProposalStatus.ACCEPTED:
            return PublicProposalAcceptResponse(
                status="already_accepted",
                proposal_code=prop.proposal_code,
                accepted_at=prop.accepted_at or datetime.now(timezone.utc),
                accepted_by_name=prop.accepted_by_name or payload.signer_name,
                deal_id=prop.deal_id,
                deal_status="won",
                message="Esta proposta já foi aprovada digitalmente anteriormente.",
            )

        now = datetime.now(timezone.utc)
        if prop.valid_until < now or prop.status in (ProposalStatus.EXPIRED, ProposalStatus.REJECTED):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Esta proposta está expirada ou cancelada. Solicite uma atualização ao consultor.",
            )

        prop.status = ProposalStatus.ACCEPTED
        prop.accepted_at = now
        prop.accepted_by_name = payload.signer_name.strip()
        prop.accepted_by_document = payload.signer_document.strip()
        prop.accepted_by_email = payload.signer_email.strip().lower() if payload.signer_email else None
        prop.accepted_ip = client_ip

        deal_status_str: str | None = None
        if prop.deal_id:
            deal = (
                await session.execute(select(Deal).where(Deal.id == prop.deal_id))
            ).scalar_one_or_none()
            if deal is not None:
                deal.status = DealStatus.WON
                deal.mrr_value = prop.total_mrr
                deal.one_time_value = prop.total_one_time
                deal.total_contract_value = prop.total_contract_value
                deal.contract_months = prop.contract_months
                deal.win_probability = 100
                deal_status_str = deal.status.value

                won_stage = (
                    await session.execute(
                        select(PipelineStage).where(
                            PipelineStage.pipeline_id == deal.pipeline_id,
                            PipelineStage.is_won_stage.is_(True),
                        )
                    )
                ).scalars().first()
                if won_stage and deal.stage_id != won_stage.id:
                    prev_stage = deal.stage_id
                    dur = max(0, int((now - deal.stage_entered_at).total_seconds()))
                    deal.stage_id = won_stage.id
                    deal.stage_entered_at = now
                    session.add(
                        DealStageHistory(
                            tenant_id=tenant_id,
                            deal_id=deal.id,
                            from_stage_id=prev_stage,
                            to_stage_id=won_stage.id,
                            changed_by_ai=False,
                            duration_in_previous_stage_seconds=dur,
                            entered_at=now,
                        )
                    )

        if prop.company_id:
            comp = (
                await session.execute(select(Company).where(Company.id == prop.company_id))
            ).scalar_one_or_none()
            if comp is not None:
                comp.lifecycle_stage = LifecycleStage.CUSTOMER

        session.add(
            AuditLog(
                tenant_id=tenant_id,
                actor_type="client_digital_acceptance",
                entity_type="commercial_proposal",
                record_id=prop.id,
                action="accept",
                changes_after={
                    "proposal_code": prop.proposal_code,
                    "signer_name": prop.accepted_by_name,
                    "signer_document": prop.accepted_by_document,
                    "accepted_ip": client_ip,
                },
            )
        )

        await session.commit()

        # Notifica Closer e Cliente via WhatsApp em modo best-effort
        try:
            wa_instance = await WhatsAppService.get_primary_active_instance(session, tenant_id)
            if wa_instance is not None:
                evo = EvolutionAPIClient(
                    base_url=wa_instance.evolution_api_url,
                    api_key=wa_instance.evolution_api_key,
                )
                if wa_instance.handoff_notify_number:
                    closer_msg = (
                        f"*[PROPOSTA APROVADA - FECHADO GANHO]*\n"
                        f"*Proposta:* {prop.proposal_code} - {prop.title}\n"
                        f"*Signatário:* {prop.accepted_by_name} ({prop.accepted_by_document})\n"
                        f"*MRR:* {format_brl(prop.total_mrr)} | *TCV:* {format_brl(prop.total_contract_value)}"
                    )
                    await evo.send_text(wa_instance.instance_name, wa_instance.handoff_notify_number, closer_msg, delay_ms=400)

                if prop.contact_id:
                    contact = (
                        await session.execute(select(Contact).where(Contact.id == prop.contact_id))
                    ).scalar_one_or_none()
                    target_client = contact.whatsapp_e164 or contact.phone if contact else None
                    if target_client:
                        client_msg = (
                            f"Confirmamos o aceite digital da *Proposta {prop.proposal_code}* ({prop.title}). "
                            f"Nossa equipe de onboarding técnico já foi acionada para iniciar a implantação!"
                        )
                        await evo.send_text(wa_instance.instance_name, target_client, client_msg, delay_ms=600)
        except Exception as exc:
            logger.warning("Falha ao enviar notificação WhatsApp pós-aceite: %s", exc)

        return PublicProposalAcceptResponse(
            status="accepted",
            proposal_code=prop.proposal_code,
            accepted_at=now,
            accepted_by_name=prop.accepted_by_name,
            deal_id=prop.deal_id,
            deal_status=deal_status_str,
            message="Proposta aprovada digitalmente com sucesso e oportunidade marcada como Fechado Ganho no CRM.",
        )
