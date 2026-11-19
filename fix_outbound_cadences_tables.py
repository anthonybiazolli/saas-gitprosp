#!/usr/bin/env python3
"""
Corrige a colisão de nome da tabela 'cadence_enrollments' no Bloco 12
(usando 'prospect_cadence_enrollments' e 'prospect_cadence_execution_logs'),
garante compatibilidade de colunas em OutboundDispatchQueue e CrmDeal,
sincroniza o PostgreSQL e executa a suíte End-to-End completa (10 testes).

Salve em: ~/apps/comercial-saas/fix_outbound_cadences_tables.py
Execute com: python3 ~/apps/comercial-saas/fix_outbound_cadences_tables.py
"""

from __future__ import annotations

import re
import subprocess
import sys
import time
from pathlib import Path


def detect_base_import(project_dir: Path) -> str:
    for rel in ("app/models/evolution_bridge.py", "app/models/core.py", "app/models/saas_gateway.py"):
        path = project_dir / rel
        if path.exists():
            m = re.search(
                r"^from\s+(app\.[a-zA-Z0-9_.]+)\s+import\s+.*\bBase\b",
                path.read_text(encoding="utf-8"),
                re.M,
            )
            if m:
                return f"from {m.group(1)} import Base"
    return "from app.db.base import Base"


def build_models_code(base_import: str) -> str:
    return f'''"""Modelos SQLAlchemy do Bloco 12: Motor de Cadências Ativas de Prospecção (D+0, D+2, D+5)."""

from __future__ import annotations

import enum
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum as SAEnum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

{base_import}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class CadenceChannel(str, enum.Enum):
    WHATSAPP = "WHATSAPP"
    EMAIL = "EMAIL"


class EnrollmentStatus(str, enum.Enum):
    ACTIVE = "ACTIVE"
    PAUSED = "PAUSED"
    COMPLETED = "COMPLETED"
    REPLIED = "REPLIED"
    CONVERTED = "CONVERTED"
    OPTED_OUT = "OPTED_OUT"


class ProspectCadence(Base):
    __tablename__ = "prospect_cadences"
    __table_args__ = (
        Index("ix_prospect_cadences_tenant_active", "tenant_id", "is_active"),
        {{"extend_existing": True}},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    target_persona: Mapped[str] = mapped_column(String(180), default="Decisor B2B / Diretor Comercial", nullable=False)
    value_proposition: Mapped[str] = mapped_column(
        Text,
        default="Automação comercial omnichannel com SDR IA, propostas e cobrança PIX integrada.",
        nullable=False,
    )
    tone_of_voice: Mapped[str] = mapped_column(String(80), default="consultivo, direto e executivo", nullable=False)

    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    ai_personalization_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    stop_on_reply: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    stop_on_deal_won: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=_utcnow, nullable=False
    )

    steps: Mapped[list["ProspectCadenceStep"]] = relationship(
        "ProspectCadenceStep",
        back_populates="cadence",
        cascade="all, delete-orphan",
        order_by="ProspectCadenceStep.step_order",
        lazy="selectin",
    )


class ProspectCadenceStep(Base):
    __tablename__ = "prospect_cadence_steps"
    __table_args__ = (
        UniqueConstraint("cadence_id", "step_order", name="uq_prospect_cadence_step_order"),
        Index("ix_prospect_cadence_steps_tenant_cadence", "tenant_id", "cadence_id"),
        {{"extend_existing": True}},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    cadence_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("prospect_cadences.id", ondelete="CASCADE"), nullable=False, index=True
    )
    step_order: Mapped[int] = mapped_column(Integer, nullable=False)
    day_offset: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    channel: Mapped[CadenceChannel] = mapped_column(
        SAEnum(CadenceChannel, name="prospect_cadence_channel_enum", native_enum=False),
        default=CadenceChannel.WHATSAPP,
        nullable=False,
    )
    subject_template: Mapped[str] = mapped_column(String(240), default="", nullable=False)
    body_template: Mapped[str] = mapped_column(Text, nullable=False)
    ai_prompt_instruction: Mapped[str] = mapped_column(Text, default="", nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    cadence: Mapped["ProspectCadence"] = relationship("ProspectCadence", back_populates="steps")


class CadenceEnrollment(Base):
    __tablename__ = "prospect_cadence_enrollments"
    __table_args__ = (
        Index("ix_prospect_cadence_enrollments_due", "status", "next_run_at"),
        Index("ix_prospect_cadence_enrollments_tenant_status", "tenant_id", "status"),
        {{"extend_existing": True}},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    cadence_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("prospect_cadences.id", ondelete="CASCADE"), nullable=False, index=True
    )

    lead_name: Mapped[str] = mapped_column(String(160), nullable=False)
    company_name: Mapped[str] = mapped_column(String(180), default="", nullable=False)
    email: Mapped[str] = mapped_column(String(180), default="", nullable=False)
    phone: Mapped[str] = mapped_column(String(40), default="", nullable=False)
    role_title: Mapped[str] = mapped_column(String(120), default="", nullable=False)
    segment: Mapped[str] = mapped_column(String(120), default="", nullable=False)
    context_notes: Mapped[str] = mapped_column(Text, default="", nullable=False)

    status: Mapped[EnrollmentStatus] = mapped_column(
        SAEnum(EnrollmentStatus, name="prospect_cadence_enrollment_status_enum", native_enum=False),
        default=EnrollmentStatus.ACTIVE,
        nullable=False,
    )
    current_step_order: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    enrolled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    last_dispatched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    replied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    converted_deal_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)


class CadenceExecutionLog(Base):
    __tablename__ = "prospect_cadence_execution_logs"
    __table_args__ = (
        Index("ix_prospect_cadence_exec_logs_tenant_exec", "tenant_id", "executed_at"),
        {{"extend_existing": True}},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    cadence_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("prospect_cadences.id", ondelete="CASCADE"), nullable=False, index=True
    )
    enrollment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("prospect_cadence_enrollments.id", ondelete="CASCADE"), nullable=False, index=True
    )
    step_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    step_order: Mapped[int] = mapped_column(Integer, nullable=False)
    day_offset: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    channel: Mapped[str] = mapped_column(String(24), nullable=False)
    destination: Mapped[str] = mapped_column(String(180), nullable=False)
    rendered_subject: Mapped[str] = mapped_column(String(240), default="", nullable=False)
    rendered_body: Mapped[str] = mapped_column(Text, nullable=False)
    ai_generated: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    dispatch_status: Mapped[str] = mapped_column(String(32), default="ENQUEUED", nullable=False)
    dispatch_queue_id: Mapped[str | None] = mapped_column(String(80), nullable=True)

    executed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
'''


SERVICE_CODE = '''"""Camada de serviço do Bloco 12: Motor de Cadências Ativas (D+0, D+2, D+5) & SDR IA."""

from __future__ import annotations

import asyncio
import json
import os
import re
import urllib.request
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import func, inspect as sa_inspect, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.automation_engine import DispatchQueueStatus, OutboundDispatchQueue
from app.models.crm_deals import CrmDeal
from app.models.evolution_bridge import OmnichannelMessageLog
from app.models.outbound_cadences import (
    CadenceChannel,
    CadenceEnrollment,
    CadenceExecutionLog,
    EnrollmentStatus,
    ProspectCadence,
    ProspectCadenceStep,
)
from app.schemas.outbound_cadences import (
    CadenceConvertDealRequest,
    CadenceCreate,
    CadenceEnrollRequest,
    CadenceMetricsOut,
    CadenceProcessTickOut,
    CadenceStepCreate,
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _normalize_digits(raw: str) -> str:
    return re.sub(r"\\D+", "", raw or "")


def _render_template(template: str, enrollment: CadenceEnrollment, cadence: ProspectCadence) -> str:
    first_name = (enrollment.lead_name.strip().split() or ["Olá"])[0]
    mapping = {
        "lead_name": enrollment.lead_name,
        "first_name": first_name,
        "company_name": enrollment.company_name or "sua empresa",
        "role_title": enrollment.role_title or "liderança comercial",
        "segment": enrollment.segment or "seu segmento",
        "context_notes": enrollment.context_notes or "",
        "value_proposition": cadence.value_proposition,
        "target_persona": cadence.target_persona,
    }
    rendered = template
    for key, val in mapping.items():
        rendered = rendered.replace("{{" + key + "}}", str(val))
        rendered = rendered.replace("{" + key + "}", str(val))
    return rendered.strip()


async def _personalize_with_sdr_ai(
    cadence: ProspectCadence,
    step: ProspectCadenceStep,
    enrollment: CadenceEnrollment,
    base_subject: str,
    base_body: str,
) -> tuple[str, str, bool]:
    """Refina o copy da etapa usando Gemini API (se configurada) ou motor contextual SDR IA."""
    if not cadence.ai_personalization_enabled:
        return base_subject, base_body, False

    api_key = os.getenv("GEMINI_API_KEY", "").strip()
    if api_key:
        prompt = (
            f"Você é um SDR B2B sênior com tom {cadence.tone_of_voice}.\\n"
            f"Persona alvo: {cadence.target_persona}\\n"
            f"Proposta de valor: {cadence.value_proposition}\\n"
            f"Lead: {enrollment.lead_name} | Cargo: {enrollment.role_title or 'Decisor'} | "
            f"Empresa: {enrollment.company_name or 'N/A'} | Segmento: {enrollment.segment or 'B2B'}\\n"
            f"Contexto do lead: {enrollment.context_notes or 'Sem observações adicionais'}\\n"
            f"Etapa D+{step.day_offset} ({step.channel.value}). Instrução: {step.ai_prompt_instruction}\\n"
            f"Assunto base: {base_subject}\\n"
            f"Mensagem base:\\n{base_body}\\n\\n"
            'Retorne APENAS um JSON válido no formato {"subject": "...", "body": "..."} '
            "mantendo a mensagem objetiva, natural em português do Brasil e sem placeholders."
        )

        def _call_gemini() -> tuple[str, str, bool]:
            url = (
                "https://generativelanguage.googleapis.com/v1beta/models/"
                f"gemini-2.5-flash:generateContent?key={api_key}"
            )
            payload = {
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {"temperature": 0.45, "maxOutputTokens": 600},
            }
            req = urllib.request.Request(
                url=url,
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=12.0) as resp:
                raw = json.loads(resp.read().decode("utf-8", errors="replace"))
            txt = (
                raw.get("candidates", [{}])[0]
                .get("content", {})
                .get("parts", [{}])[0]
                .get("text", "")
                .strip()
            )
            txt = re.sub(r"^```(?:json)?|```$", "", txt, flags=re.M).strip()
            parsed = json.loads(txt)
            subj = str(parsed.get("subject") or base_subject).strip()
            body = str(parsed.get("body") or base_body).strip()
            return subj, body, True

        try:
            return await asyncio.to_thread(_call_gemini)
        except Exception:
            pass

    context_hook = ""
    if enrollment.context_notes:
        context_hook = f" Vi que {enrollment.context_notes.rstrip('.')}."
    elif enrollment.segment:
        context_hook = f" Temos ajudado operações do setor de {enrollment.segment} a acelerar conversão."

    enriched_body = base_body
    if context_hook and context_hook.strip() not in enriched_body:
        lines = enriched_body.splitlines()
        if len(lines) >= 2:
            lines.insert(1, context_hook.strip())
            enriched_body = "\\n".join(lines)
        else:
            enriched_body = f"{enriched_body}{context_hook}"

    return base_subject, enriched_body, True


def _fill_missing_required_columns(mapper: Any, kwargs: dict[str, Any], fallbacks: dict[str, Any]) -> None:
    """Preenche colunas obrigatórias sem default caso existam no modelo alvo."""
    for col in mapper.columns:
        key = col.key
        if key in kwargs or col.primary_key or col.nullable:
            continue
        if col.default is not None or col.server_default is not None:
            continue
        if key in fallbacks:
            enum_cls = getattr(col.type, "enum_class", None)
            val = fallbacks[key]
            if enum_cls is not None and isinstance(val, str) and val in enum_cls.__members__:
                kwargs[key] = enum_cls[val]
            else:
                kwargs[key] = val
        else:
            enum_cls = getattr(col.type, "enum_class", None)
            if enum_cls is not None:
                first_member = next(iter(enum_cls), None)
                if first_member is not None:
                    kwargs[key] = first_member


def _enqueue_dispatch_item(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    channel: CadenceChannel,
    destination: str,
    subject: str,
    body: str,
    context_meta: dict[str, Any],
) -> OutboundDispatchQueue:
    mapper = sa_inspect(OutboundDispatchQueue)
    col_names = {c.key for c in mapper.columns}

    kwargs: dict[str, Any] = {}
    if "tenant_id" in col_names:
        kwargs["tenant_id"] = tenant_id

    if "channel" in col_names:
        ch_col = mapper.columns["channel"]
        enum_cls = getattr(ch_col.type, "enum_class", None)
        if enum_cls is not None and channel.value in enum_cls.__members__:
            kwargs["channel"] = enum_cls[channel.value]
        else:
            kwargs["channel"] = channel.value

    for dest_col in ("destination", "recipient", "target"):
        if dest_col in col_names:
            kwargs[dest_col] = destination

    for subj_col in ("subject", "rendered_subject", "title"):
        if subj_col in col_names:
            kwargs[subj_col] = subject

    for body_col in ("rendered_body", "message_body", "body"):
        if body_col in col_names:
            kwargs[body_col] = body

    if "status" in col_names:
        kwargs["status"] = DispatchQueueStatus.PENDING

    for ctx_col in ("context_json", "payload_json", "metadata_json"):
        if ctx_col in col_names:
            kwargs[ctx_col] = context_meta

    for sched_col in ("scheduled_for", "scheduled_at", "next_attempt_at"):
        if sched_col in col_names:
            kwargs[sched_col] = _utcnow()

    _fill_missing_required_columns(
        mapper,
        kwargs,
        {
            "event_type": "OUTBOUND_CADENCE_TOUCH",
            "trigger_event": "OUTBOUND_CADENCE_TOUCH",
            "attempts": 0,
            "max_attempts": 3,
        },
    )

    item = OutboundDispatchQueue(**kwargs)
    session.add(item)
    return item


async def create_cadence(
    session: AsyncSession, tenant_id: uuid.UUID, payload: CadenceCreate
) -> ProspectCadence:
    cadence = ProspectCadence(
        tenant_id=tenant_id,
        name=payload.name.strip(),
        description=payload.description.strip(),
        target_persona=payload.target_persona.strip(),
        value_proposition=payload.value_proposition.strip(),
        tone_of_voice=payload.tone_of_voice.strip(),
        is_active=True,
        ai_personalization_enabled=payload.ai_personalization_enabled,
        stop_on_reply=payload.stop_on_reply,
        stop_on_deal_won=payload.stop_on_deal_won,
    )
    session.add(cadence)
    await session.flush()

    sorted_steps = sorted(payload.steps, key=lambda s: (s.step_order, s.day_offset))
    for idx, st in enumerate(sorted_steps, start=1):
        step_row = ProspectCadenceStep(
            tenant_id=tenant_id,
            cadence_id=cadence.id,
            step_order=idx,
            day_offset=st.day_offset,
            channel=st.channel,
            subject_template=st.subject_template.strip(),
            body_template=st.body_template.strip(),
            ai_prompt_instruction=st.ai_prompt_instruction.strip(),
            is_active=st.is_active,
        )
        session.add(step_row)

    await session.flush()
    await session.refresh(cadence)
    return cadence


async def bootstrap_default_d0_d2_d5_cadence(
    session: AsyncSession, tenant_id: uuid.UUID
) -> ProspectCadence:
    existing_stmt = (
        select(ProspectCadence)
        .where(
            ProspectCadence.tenant_id == tenant_id,
            ProspectCadence.name == "Cadência Outbound B2B Padrão (D+0 / D+2 / D+5)",
        )
        .limit(1)
    )
    existing = (await session.execute(existing_stmt)).scalars().first()
    if existing is not None:
        return existing

    default_payload = CadenceCreate(
        name="Cadência Outbound B2B Padrão (D+0 / D+2 / D+5)",
        description="Sequência multicanal automatizada com abordagem inicial no WhatsApp (D+0), diagnóstico executivo por E-mail (D+2) e follow-up final no WhatsApp (D+5).",
        target_persona="Diretor Comercial / Sócio / Head de Operações",
        value_proposition="Centralize prospecção outbound, propostas, cobrança PIX automatizada e atendimento WhatsApp em um único fluxo.",
        tone_of_voice="consultivo, direto e executivo",
        ai_personalization_enabled=True,
        stop_on_reply=True,
        stop_on_deal_won=True,
        steps=[
            CadenceStepCreate(
                step_order=1,
                day_offset=0,
                channel=CadenceChannel.WHATSAPP,
                subject_template="Abordagem Inicial D+0",
                body_template=(
                    "Olá {{first_name}}, tudo bem? Aqui é da equipe comercial.\\n"
                    "Estava analisando a operação da {{company_name}} e notei uma oportunidade prática para acelerar a conversão comercial e reduzir follow-ups manuais.\\n"
                    "Faz sentido trocarmos 5 minutos ainda esta semana para eu te mostrar como funciona na prática?"
                ),
                ai_prompt_instruction="Primeiro contato via WhatsApp: curto, humano, sem parecer spam, focado em abrir conversa com {{first_name}} da {{company_name}}.",
            ),
            CadenceStepCreate(
                step_order=2,
                day_offset=2,
                channel=CadenceChannel.EMAIL,
                subject_template="Diagnóstico comercial para {{company_name}} ({{first_name}})",
                body_template=(
                    "Olá {{first_name}},\\n\\n"
                    "Tentei contato rápido via WhatsApp há dois dias sobre a operação comercial da {{company_name}}.\\n"
                    "{{value_proposition}}\\n\\n"
                    "Em operações semelhantes, estruturamos cadências ativas, emissão de propostas e baixa automática de PIX com comissão em tempo real.\\n"
                    "Você teria 10 minutos na quinta ou sexta-feira para uma demonstração objetiva?\\n\\n"
                    "Abraço,\\nEquipe Comercial"
                ),
                ai_prompt_instruction="E-mail executivo D+2: reforçar proposta de valor com clareza e call-to-action de 10 minutos.",
            ),
            CadenceStepCreate(
                step_order=3,
                day_offset=5,
                channel=CadenceChannel.WHATSAPP,
                subject_template="Follow-up Final D+5",
                body_template=(
                    "Oi {{first_name}}, passando só para encerrar meu ciclo de contatos por aqui e não poluir sua caixa.\\n"
                    "Se estruturar o funil comercial e automatizar cobranças/WhatsApp da {{company_name}} entrar no radar nos próximos meses, é só me responder por aqui que retomamos!"
                ),
                ai_prompt_instruction="Mensagem de break-up D+5 via WhatsApp: leve, respeitosa, gerando gatilho de resposta rápida.",
            ),
        ],
    )
    return await create_cadence(session, tenant_id, default_payload)


async def list_cadences(session: AsyncSession, tenant_id: uuid.UUID) -> list[ProspectCadence]:
    stmt = (
        select(ProspectCadence)
        .where(ProspectCadence.tenant_id == tenant_id)
        .order_by(ProspectCadence.created_at.desc())
    )
    return list((await session.execute(stmt)).scalars().all())


async def toggle_cadence(
    session: AsyncSession, tenant_id: uuid.UUID, cadence_id: uuid.UUID, is_active: bool
) -> ProspectCadence:
    stmt = select(ProspectCadence).where(
        ProspectCadence.tenant_id == tenant_id, ProspectCadence.id == cadence_id
    )
    cadence = (await session.execute(stmt)).scalars().first()
    if cadence is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Cadência não encontrada.")
    cadence.is_active = is_active
    await session.flush()
    await session.refresh(cadence)
    return cadence


async def enroll_prospect(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    cadence_id: uuid.UUID,
    req: CadenceEnrollRequest,
) -> CadenceEnrollment:
    cadence_stmt = select(ProspectCadence).where(
        ProspectCadence.tenant_id == tenant_id, ProspectCadence.id == cadence_id
    )
    cadence = (await session.execute(cadence_stmt)).scalars().first()
    if cadence is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Cadência não encontrada.")
    if not cadence.is_active:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Cadência está pausada/inativa.")
    if not req.phone.strip() and not req.email.strip():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Informe ao menos telefone (WhatsApp) ou e-mail do prospect.",
        )

    active_steps = [s for s in cadence.steps if s.is_active]
    if not active_steps:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Cadência não possui etapas ativas.")

    first_step = sorted(active_steps, key=lambda s: s.step_order)[0]
    now = _utcnow()
    next_run = now if (req.start_immediately and first_step.day_offset == 0) else now + timedelta(days=first_step.day_offset)

    enrollment = CadenceEnrollment(
        tenant_id=tenant_id,
        cadence_id=cadence.id,
        lead_name=req.lead_name.strip(),
        company_name=req.company_name.strip(),
        email=req.email.strip(),
        phone=req.phone.strip(),
        role_title=req.role_title.strip(),
        segment=req.segment.strip(),
        context_notes=req.context_notes.strip(),
        status=EnrollmentStatus.ACTIVE,
        current_step_order=first_step.step_order,
        next_run_at=next_run,
        enrolled_at=now,
        metadata_json=req.metadata_json or {},
    )
    session.add(enrollment)
    await session.flush()
    await session.refresh(enrollment)
    return enrollment


async def _has_recent_inbound_whatsapp_reply(
    session: AsyncSession, tenant_id: uuid.UUID, raw_phone: str, since: datetime
) -> bool:
    digits = _normalize_digits(raw_phone)
    if len(digits) < 8:
        return False
    suffix = digits[-8:]
    stmt = (
        select(OmnichannelMessageLog.id)
        .where(
            OmnichannelMessageLog.tenant_id == tenant_id,
            OmnichannelMessageLog.direction == "INBOUND",
            OmnichannelMessageLog.created_at >= since,
            OmnichannelMessageLog.phone_number.like(f"%{suffix}"),
        )
        .limit(1)
    )
    return (await session.execute(stmt)).first() is not None


async def process_due_cadence_steps(
    session: AsyncSession,
    tenant_id: uuid.UUID | None = None,
    batch_size: int = 50,
) -> CadenceProcessTickOut:
    """Executa todos os passos vencidos das cadências ativas e aciona o despachante omnichannel."""
    now = _utcnow()
    filters = [
        CadenceEnrollment.status == EnrollmentStatus.ACTIVE,
        CadenceEnrollment.next_run_at.is_not(None),
        CadenceEnrollment.next_run_at <= now,
    ]
    if tenant_id is not None:
        filters.append(CadenceEnrollment.tenant_id == tenant_id)

    due_stmt = (
        select(CadenceEnrollment)
        .where(*filters)
        .order_by(CadenceEnrollment.next_run_at.asc())
        .limit(batch_size)
    )
    enrollments = list((await session.execute(due_stmt)).scalars().all())

    checked = 0
    dispatched = 0
    auto_stopped = 0
    completed = 0
    skipped_missing = 0
    touched_tenants: set[uuid.UUID] = set()

    for enr in enrollments:
        checked += 1
        cadence = (
            await session.execute(
                select(ProspectCadence).where(ProspectCadence.id == enr.cadence_id)
            )
        ).scalars().first()
        if cadence is None or not cadence.is_active:
            continue

        if cadence.stop_on_reply and enr.phone:
            if await _has_recent_inbound_whatsapp_reply(session, enr.tenant_id, enr.phone, enr.enrolled_at):
                enr.status = EnrollmentStatus.REPLIED
                enr.replied_at = now
                enr.next_run_at = None
                auto_stopped += 1
                continue

        active_steps = sorted([s for s in cadence.steps if s.is_active], key=lambda s: s.step_order)
        current_step = next((s for s in active_steps if s.step_order == enr.current_step_order), None)
        if current_step is None:
            enr.status = EnrollmentStatus.COMPLETED
            enr.next_run_at = None
            completed += 1
            continue

        dest = enr.phone.strip() if current_step.channel == CadenceChannel.WHATSAPP else enr.email.strip()
        if not dest:
            skipped_missing += 1
            next_steps = [s for s in active_steps if s.step_order > current_step.step_order]
            if next_steps:
                nxt = next_steps[0]
                delta_days = max(0, nxt.day_offset - current_step.day_offset)
                enr.current_step_order = nxt.step_order
                enr.next_run_at = now + timedelta(days=delta_days)
            else:
                enr.status = EnrollmentStatus.COMPLETED
                enr.next_run_at = None
                completed += 1
            continue

        base_subject = _render_template(current_step.subject_template, enr, cadence)
        base_body = _render_template(current_step.body_template, enr, cadence)
        final_subject, final_body, ai_used = await _personalize_with_sdr_ai(
            cadence, current_step, enr, base_subject, base_body
        )

        q_item = _enqueue_dispatch_item(
            session=session,
            tenant_id=enr.tenant_id,
            channel=current_step.channel,
            destination=dest,
            subject=final_subject,
            body=final_body,
            context_meta={
                "source": "OUTBOUND_CADENCE",
                "cadence_id": str(cadence.id),
                "enrollment_id": str(enr.id),
                "step_order": current_step.step_order,
                "day_offset": current_step.day_offset,
                "lead_name": enr.lead_name,
                "company_name": enr.company_name,
            },
        )
        await session.flush()

        exec_log = CadenceExecutionLog(
            tenant_id=enr.tenant_id,
            cadence_id=cadence.id,
            enrollment_id=enr.id,
            step_id=current_step.id,
            step_order=current_step.step_order,
            day_offset=current_step.day_offset,
            channel=current_step.channel.value,
            destination=dest,
            rendered_subject=final_subject[:240],
            rendered_body=final_body,
            ai_generated=ai_used,
            dispatch_status="ENQUEUED",
            dispatch_queue_id=str(getattr(q_item, "id", "")) or None,
            executed_at=now,
        )
        session.add(exec_log)

        enr.last_dispatched_at = now
        touched_tenants.add(enr.tenant_id)
        dispatched += 1

        next_steps = [s for s in active_steps if s.step_order > current_step.step_order]
        if next_steps:
            nxt = next_steps[0]
            delta_days = max(0, nxt.day_offset - current_step.day_offset)
            enr.current_step_order = nxt.step_order
            enr.next_run_at = now + timedelta(days=delta_days)
        else:
            enr.status = EnrollmentStatus.COMPLETED
            enr.next_run_at = None
            completed += 1

    await session.flush()

    if touched_tenants:
        from app.services import automation_service

        for tid in touched_tenants:
            try:
                await automation_service.process_dispatch_queue(session, tid, batch_size=batch_size)
            except Exception:
                pass

    return CadenceProcessTickOut(
        checked_enrollments=checked,
        dispatched_steps=dispatched,
        auto_stopped_by_reply=auto_stopped,
        completed_enrollments=completed,
        skipped_missing_contact=skipped_missing,
    )


async def auto_stop_active_enrollments_by_phone(
    session: AsyncSession, tenant_id: uuid.UUID, raw_phone: str
) -> int:
    digits = _normalize_digits(raw_phone)
    if len(digits) < 8:
        return 0
    suffix = digits[-8:]
    stmt = select(CadenceEnrollment).where(
        CadenceEnrollment.tenant_id == tenant_id,
        CadenceEnrollment.status == EnrollmentStatus.ACTIVE,
    )
    active_list = list((await session.execute(stmt)).scalars().all())
    stopped = 0
    now = _utcnow()
    for enr in active_list:
        if _normalize_digits(enr.phone).endswith(suffix):
            cad = (
                await session.execute(
                    select(ProspectCadence).where(ProspectCadence.id == enr.cadence_id)
                )
            ).scalars().first()
            if cad is None or cad.stop_on_reply:
                enr.status = EnrollmentStatus.REPLIED
                enr.replied_at = now
                enr.next_run_at = None
                stopped += 1
    if stopped:
        await session.flush()
    return stopped


async def mark_enrollment_replied(
    session: AsyncSession, tenant_id: uuid.UUID, enrollment_id: uuid.UUID
) -> CadenceEnrollment:
    stmt = select(CadenceEnrollment).where(
        CadenceEnrollment.tenant_id == tenant_id, CadenceEnrollment.id == enrollment_id
    )
    enr = (await session.execute(stmt)).scalars().first()
    if enr is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Inscrição não encontrada.")
    enr.status = EnrollmentStatus.REPLIED
    enr.replied_at = _utcnow()
    enr.next_run_at = None
    await session.flush()
    await session.refresh(enr)
    return enr


async def convert_enrollment_to_crm_deal(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    enrollment_id: uuid.UUID,
    req: CadenceConvertDealRequest,
) -> dict[str, Any]:
    stmt = select(CadenceEnrollment).where(
        CadenceEnrollment.tenant_id == tenant_id, CadenceEnrollment.id == enrollment_id
    )
    enr = (await session.execute(stmt)).scalars().first()
    if enr is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Inscrição não encontrada.")

    mapper = sa_inspect(CrmDeal)
    col_names = {c.key for c in mapper.columns}
    deal_title = req.deal_title or f"Oportunidade Outbound - {enr.company_name or enr.lead_name}"

    deal_kwargs: dict[str, Any] = {}
    if "tenant_id" in col_names:
        deal_kwargs["tenant_id"] = tenant_id
    if "title" in col_names:
        deal_kwargs["title"] = deal_title
    if "company_name" in col_names:
        deal_kwargs["company_name"] = enr.company_name or enr.lead_name
    if "contact_name" in col_names:
        deal_kwargs["contact_name"] = enr.lead_name
    if "contact_email" in col_names:
        deal_kwargs["contact_email"] = enr.email or "contato@prospect.local"
    if "contact_phone" in col_names:
        deal_kwargs["contact_phone"] = enr.phone or ""
    if "amount" in col_names:
        deal_kwargs["amount"] = req.estimated_amount
    if "value" in col_names:
        deal_kwargs["value"] = req.estimated_amount
    if "probability" in col_names:
        deal_kwargs["probability"] = 35
    if "owner_name" in col_names:
        deal_kwargs["owner_name"] = req.owner_name

    _fill_missing_required_columns(
        mapper,
        deal_kwargs,
        {
            "stage": "QUALIFICATION",
            "status": "OPEN",
            "source": "OUTBOUND_CADENCE",
            "currency": "BRL",
            "notes": enr.context_notes or "Gerado via Cadência Outbound SDR IA",
        },
    )

    deal = CrmDeal(**deal_kwargs)
    session.add(deal)
    await session.flush()
    await session.refresh(deal)

    enr.status = EnrollmentStatus.CONVERTED
    enr.converted_deal_id = deal.id
    enr.next_run_at = None
    if enr.replied_at is None:
        enr.replied_at = _utcnow()
    await session.flush()

    return {
        "enrollment_id": str(enr.id),
        "enrollment_status": enr.status.value,
        "deal_id": str(deal.id),
        "deal_title": getattr(deal, "title", deal_title),
        "estimated_amount": float(req.estimated_amount),
    }


async def list_enrollments(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    cadence_id: uuid.UUID | None = None,
    limit: int = 80,
) -> list[CadenceEnrollment]:
    filters = [CadenceEnrollment.tenant_id == tenant_id]
    if cadence_id is not None:
        filters.append(CadenceEnrollment.cadence_id == cadence_id)
    stmt = (
        select(CadenceEnrollment)
        .where(*filters)
        .order_by(CadenceEnrollment.enrolled_at.desc())
        .limit(limit)
    )
    return list((await session.execute(stmt)).scalars().all())


async def list_execution_logs(
    session: AsyncSession, tenant_id: uuid.UUID, limit: int = 50
) -> list[CadenceExecutionLog]:
    stmt = (
        select(CadenceExecutionLog)
        .where(CadenceExecutionLog.tenant_id == tenant_id)
        .order_by(CadenceExecutionLog.executed_at.desc())
        .limit(limit)
    )
    return list((await session.execute(stmt)).scalars().all())


async def get_cadence_metrics(session: AsyncSession, tenant_id: uuid.UUID) -> CadenceMetricsOut:
    total_cad = int(
        (await session.execute(select(func.count(ProspectCadence.id)).where(ProspectCadence.tenant_id == tenant_id))).scalar_one() or 0
    )
    active_cad = int(
        (
            await session.execute(
                select(func.count(ProspectCadence.id)).where(
                    ProspectCadence.tenant_id == tenant_id, ProspectCadence.is_active.is_(True)
                )
            )
        ).scalar_one()
        or 0
    )

    async def _count_enr(st: EnrollmentStatus | None = None) -> int:
        conds = [CadenceEnrollment.tenant_id == tenant_id]
        if st is not None:
            conds.append(CadenceEnrollment.status == st)
        return int((await session.execute(select(func.count(CadenceEnrollment.id)).where(*conds))).scalar_one() or 0)

    total_enr = await _count_enr()
    active_enr = await _count_enr(EnrollmentStatus.ACTIVE)
    replied_enr = await _count_enr(EnrollmentStatus.REPLIED)
    converted_enr = await _count_enr(EnrollmentStatus.CONVERTED)
    completed_enr = await _count_enr(EnrollmentStatus.COMPLETED)

    total_touches = int(
        (await session.execute(select(func.count(CadenceExecutionLog.id)).where(CadenceExecutionLog.tenant_id == tenant_id))).scalar_one() or 0
    )
    ai_touches = int(
        (
            await session.execute(
                select(func.count(CadenceExecutionLog.id)).where(
                    CadenceExecutionLog.tenant_id == tenant_id, CadenceExecutionLog.ai_generated.is_(True)
                )
            )
        ).scalar_one()
        or 0
    )

    engaged = replied_enr + converted_enr
    reply_rate = round((engaged / total_enr) * 100.0, 2) if total_enr > 0 else 0.0
    conv_rate = round((converted_enr / total_enr) * 100.0, 2) if total_enr > 0 else 0.0

    return CadenceMetricsOut(
        total_cadences=total_cad,
        active_cadences=active_cad,
        total_enrollments=total_enr,
        active_enrollments=active_enr,
        replied_enrollments=replied_enr,
        converted_enrollments=converted_enr,
        completed_enrollments=completed_enr,
        total_touches_executed=total_touches,
        ai_personalized_touches=ai_touches,
        reply_rate_pct=reply_rate,
        conversion_rate_pct=conv_rate,
    )
'''


def main() -> None:
    project_dir = Path(__file__).resolve().parent
    base_import = detect_base_import(project_dir)

    models_path = project_dir / "app" / "models" / "outbound_cadences.py"
    service_path = project_dir / "app" / "services" / "outbound_cadence_service.py"

    models_path.write_text(build_models_code(base_import), encoding="utf-8")
    print("[OK] app/models/outbound_cadences.py atualizado (prospect_cadence_enrollments / prospect_cadence_execution_logs).")

    service_path.write_text(SERVICE_CODE, encoding="utf-8")
    print("[OK] app/services/outbound_cadence_service.py atualizado com preenchimento seguro de colunas.")

    print("\n=== Reiniciando API, sincronizando PostgreSQL e executando 10 Testes E2E ===")
    subprocess.run(["docker", "compose", "restart", "api"], cwd=str(project_dir), check=True)
    time.sleep(3)

    subprocess.run(["python3", "manage_comercial_saas.py", "init-db"], cwd=str(project_dir), check=True)
    subprocess.run(["python3", "manage_comercial_saas.py", "smoke-test"], cwd=str(project_dir), check=True)

    print("\n=== Reiniciando Worker Systemd ===")
    subprocess.run(
        ["sudo", "systemctl", "restart", "comercial-saas-worker.service"],
        cwd=str(project_dir),
        check=False,
    )


if __name__ == "__main__":
    try:
        main()
    except subprocess.CalledProcessError as exc:
        print(f"\n[ERRO] Comando falhou ({exc.cmd}) com código {exc.returncode}", file=sys.stderr)
        sys.exit(exc.returncode)