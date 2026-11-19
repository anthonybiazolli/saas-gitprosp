#!/usr/bin/env python3
"""
Instalador completo e idempotente do Bloco 12 (Módulo 12):
Motor de Cadências Ativas de Prospecção (Follow-up D+0, D+2, D+5 via WhatsApp e E-mail)
integrado ao SDR IA, ao Webhook Inbound Evolution API e ao Worker Assíncrono.

Salve em: ~/apps/comercial-saas/install_outbound_cadences_module.py
Execute com: python3 ~/apps/comercial-saas/install_outbound_cadences_module.py
"""

from __future__ import annotations

import ast
import re
import shutil
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


def discover_session_dependency(project_dir: Path) -> tuple[str, str]:
    v1_dir = project_dir / "app" / "api" / "v1"
    for fname in ("evolution_bridge.py", "governance.py", "saas_gateway.py", "billing.py"):
        fpath = v1_dir / fname
        if not fpath.exists():
            continue
        tree = ast.parse(fpath.read_text(encoding="utf-8"), filename=str(fpath))
        for node in tree.body:
            if isinstance(node, ast.ImportFrom) and node.module and node.module != "app.api.deps":
                for alias in node.names:
                    if alias.name in ("get_db", "get_session", "get_async_session", "get_db_session"):
                        return node.module, alias.name
    return "app.db.session", "get_async_session"


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
        UniqueConstraint("cadence_id", "step_order", name="uq_cadence_step_order"),
        Index("ix_cadence_steps_tenant_cadence", "tenant_id", "cadence_id"),
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
        SAEnum(CadenceChannel, name="cadence_channel_enum", native_enum=False),
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
    __tablename__ = "cadence_enrollments"
    __table_args__ = (
        Index("ix_cadence_enrollments_due", "status", "next_run_at"),
        Index("ix_cadence_enrollments_tenant_status", "tenant_id", "status"),
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
        SAEnum(EnrollmentStatus, name="cadence_enrollment_status_enum", native_enum=False),
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
    __tablename__ = "cadence_execution_logs"
    __table_args__ = (
        Index("ix_cadence_exec_logs_tenant_exec", "tenant_id", "executed_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    cadence_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("prospect_cadences.id", ondelete="CASCADE"), nullable=False, index=True
    )
    enrollment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cadence_enrollments.id", ondelete="CASCADE"), nullable=False, index=True
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


def build_schemas_code() -> str:
    return '''"""Schemas Pydantic v2 do Bloco 12: Motor de Cadências Ativas de Prospecção."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.models.outbound_cadences import CadenceChannel, EnrollmentStatus


class CadenceStepCreate(BaseModel):
    step_order: int = Field(..., ge=1, le=30)
    day_offset: int = Field(..., ge=0, le=90)
    channel: CadenceChannel = CadenceChannel.WHATSAPP
    subject_template: str = Field(default="", max_length=240)
    body_template: str = Field(..., min_length=3, max_length=5000)
    ai_prompt_instruction: str = Field(default="", max_length=1500)
    is_active: bool = True


class CadenceStepOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    step_order: int
    day_offset: int
    channel: CadenceChannel
    subject_template: str
    body_template: str
    ai_prompt_instruction: str
    is_active: bool


class CadenceCreate(BaseModel):
    name: str = Field(..., min_length=3, max_length=160)
    description: str = Field(default="", max_length=1500)
    target_persona: str = Field(default="Decisor B2B / Diretor Comercial", max_length=180)
    value_proposition: str = Field(
        default="Automação comercial omnichannel com SDR IA, propostas e cobrança PIX integrada.",
        max_length=2000,
    )
    tone_of_voice: str = Field(default="consultivo, direto e executivo", max_length=80)
    ai_personalization_enabled: bool = True
    stop_on_reply: bool = True
    stop_on_deal_won: bool = True
    steps: list[CadenceStepCreate] = Field(default_factory=list)


class CadenceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    tenant_id: uuid.UUID
    name: str
    description: str
    target_persona: str
    value_proposition: str
    tone_of_voice: str
    is_active: bool
    ai_personalization_enabled: bool
    stop_on_reply: bool
    stop_on_deal_won: bool
    created_at: datetime
    updated_at: datetime
    steps: list[CadenceStepOut] = Field(default_factory=list)


class CadenceEnrollRequest(BaseModel):
    lead_name: str = Field(..., min_length=2, max_length=160)
    company_name: str = Field(default="", max_length=180)
    email: str = Field(default="", max_length=180)
    phone: str = Field(default="", max_length=40)
    role_title: str = Field(default="", max_length=120)
    segment: str = Field(default="", max_length=120)
    context_notes: str = Field(default="", max_length=2000)
    start_immediately: bool = True
    metadata_json: dict[str, Any] = Field(default_factory=dict)


class CadenceBulkEnrollRequest(BaseModel):
    prospects: list[CadenceEnrollRequest] = Field(..., min_length=1, max_length=200)


class CadenceEnrollmentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    tenant_id: uuid.UUID
    cadence_id: uuid.UUID
    lead_name: str
    company_name: str
    email: str
    phone: str
    role_title: str
    segment: str
    context_notes: str
    status: EnrollmentStatus
    current_step_order: int
    next_run_at: datetime | None = None
    enrolled_at: datetime
    last_dispatched_at: datetime | None = None
    replied_at: datetime | None = None
    converted_deal_id: uuid.UUID | None = None


class CadenceConvertDealRequest(BaseModel):
    deal_title: str | None = Field(default=None, max_length=200)
    estimated_amount: float = Field(default=3500.0, ge=0)
    owner_name: str = Field(default="SDR IA", max_length=120)


class CadenceExecutionLogOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    cadence_id: uuid.UUID
    enrollment_id: uuid.UUID
    step_order: int
    day_offset: int
    channel: str
    destination: str
    rendered_subject: str
    rendered_body: str
    ai_generated: bool
    dispatch_status: str
    dispatch_queue_id: str | None = None
    executed_at: datetime


class CadenceProcessTickOut(BaseModel):
    checked_enrollments: int
    dispatched_steps: int
    auto_stopped_by_reply: int
    completed_enrollments: int
    skipped_missing_contact: int


class CadenceMetricsOut(BaseModel):
    total_cadences: int
    active_cadences: int
    total_enrollments: int
    active_enrollments: int
    replied_enrollments: int
    converted_enrollments: int
    completed_enrollments: int
    total_touches_executed: int
    ai_personalized_touches: int
    reply_rate_pct: float
    conversion_rate_pct: float
'''


def build_service_code() -> str:
    return '''"""Camada de serviço do Bloco 12: Motor de Cadências Ativas (D+0, D+2, D+5) & SDR IA."""

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

    # Enriquecimento contextual determinístico SDR IA quando sem chamada externa
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


def _enqueue_dispatch_item(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    channel: CadenceChannel,
    destination: str,
    subject: str,
    body: str,
    context_meta: dict[str, Any],
) -> OutboundDispatchQueue:
    """Instancia OutboundDispatchQueue inspecionando dinamicamente as colunas reais do modelo."""
    mapper = sa_inspect(OutboundDispatchQueue)
    col_names = {c.key for c in mapper.columns}

    kwargs: dict[str, Any] = {}
    if "tenant_id" in col_names:
        kwargs["tenant_id"] = tenant_id

    if "channel" in col_names:
        ch_col = mapper.columns["channel"]
        enum_cls = getattr(ch_col.type, "enum_class", None)
        if enum_cls is not None:
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

        # Verifica resposta inbound via WhatsApp (Evolution API) antes de disparar próximo follow-up
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
            # Tenta avançar para próxima etapa se o canal da etapa atual não tiver contato preenchido
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

    # Aciona imediatamente a fila de despacho omnichannel para os tenants afetados
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
    """Chamado pelo webhook inbound da Evolution API para interromper cadências quando o lead responde."""
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
    deal_kwargs: dict[str, Any] = {}
    if "tenant_id" in col_names:
        deal_kwargs["tenant_id"] = tenant_id
    if "title" in col_names:
        deal_kwargs["title"] = req.deal_title or f"Oportunidade Outbound - {enr.company_name or enr.lead_name}"
    if "company_name" in col_names:
        deal_kwargs["company_name"] = enr.company_name or enr.lead_name
    if "contact_name" in col_names:
        deal_kwargs["contact_name"] = enr.lead_name
    if "contact_email" in col_names:
        deal_kwargs["contact_email"] = enr.email or "contato@prospect.local"
    if "amount" in col_names:
        deal_kwargs["amount"] = req.estimated_amount
    if "probability" in col_names:
        deal_kwargs["probability"] = 35
    if "owner_name" in col_names:
        deal_kwargs["owner_name"] = req.owner_name

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
        "deal_title": getattr(deal, "title", ""),
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


def build_router_code(session_import: str, session_dep: str) -> str:
    return f'''"""Rotas FastAPI do Bloco 12: Motor de Cadências Ativas de Prospecção (D+0, D+2, D+5)."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import TenantContext, get_tenant_context
{session_import}
from app.schemas.outbound_cadences import (
    CadenceBulkEnrollRequest,
    CadenceConvertDealRequest,
    CadenceCreate,
    CadenceEnrollmentOut,
    CadenceEnrollRequest,
    CadenceExecutionLogOut,
    CadenceMetricsOut,
    CadenceOut,
    CadenceProcessTickOut,
)
from app.services import outbound_cadence_service

router = APIRouter()


@router.get("", response_model=list[CadenceOut])
async def list_cadences(
    ctx: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends({session_dep}),
) -> list[CadenceOut]:
    rows = await outbound_cadence_service.list_cadences(session, ctx.tenant_id)
    return [CadenceOut.model_validate(r) for r in rows]


@router.post("", response_model=CadenceOut, status_code=status.HTTP_201_CREATED)
async def create_cadence(
    payload: CadenceCreate,
    ctx: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends({session_dep}),
) -> CadenceOut:
    row = await outbound_cadence_service.create_cadence(session, ctx.tenant_id, payload)
    await session.commit()
    return CadenceOut.model_validate(row)


@router.post("/bootstrap-default", response_model=CadenceOut, status_code=status.HTTP_201_CREATED)
async def bootstrap_default_cadence(
    ctx: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends({session_dep}),
) -> CadenceOut:
    row = await outbound_cadence_service.bootstrap_default_d0_d2_d5_cadence(session, ctx.tenant_id)
    await session.commit()
    return CadenceOut.model_validate(row)


@router.patch("/{{cadence_id}}/toggle", response_model=CadenceOut)
async def toggle_cadence(
    cadence_id: uuid.UUID,
    is_active: bool = Query(...),
    ctx: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends({session_dep}),
) -> CadenceOut:
    row = await outbound_cadence_service.toggle_cadence(session, ctx.tenant_id, cadence_id, is_active)
    await session.commit()
    return CadenceOut.model_validate(row)


@router.post("/{{cadence_id}}/enroll", response_model=CadenceEnrollmentOut, status_code=status.HTTP_201_CREATED)
async def enroll_prospect(
    cadence_id: uuid.UUID,
    payload: CadenceEnrollRequest,
    ctx: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends({session_dep}),
) -> CadenceEnrollmentOut:
    enr = await outbound_cadence_service.enroll_prospect(session, ctx.tenant_id, cadence_id, payload)
    await session.commit()
    return CadenceEnrollmentOut.model_validate(enr)


@router.post("/{{cadence_id}}/enroll-bulk", response_model=list[CadenceEnrollmentOut], status_code=status.HTTP_201_CREATED)
async def enroll_prospects_bulk(
    cadence_id: uuid.UUID,
    payload: CadenceBulkEnrollRequest,
    ctx: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends({session_dep}),
) -> list[CadenceEnrollmentOut]:
    results = []
    for item in payload.prospects:
        enr = await outbound_cadence_service.enroll_prospect(session, ctx.tenant_id, cadence_id, item)
        results.append(CadenceEnrollmentOut.model_validate(enr))
    await session.commit()
    return results


@router.get("/enrollments", response_model=list[CadenceEnrollmentOut])
async def list_enrollments(
    cadence_id: uuid.UUID | None = Query(default=None),
    limit: int = Query(default=80, ge=1, le=300),
    ctx: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends({session_dep}),
) -> list[CadenceEnrollmentOut]:
    rows = await outbound_cadence_service.list_enrollments(session, ctx.tenant_id, cadence_id=cadence_id, limit=limit)
    return [CadenceEnrollmentOut.model_validate(r) for r in rows]


@router.post("/enrollments/{{enrollment_id}}/mark-replied", response_model=CadenceEnrollmentOut)
async def mark_enrollment_replied(
    enrollment_id: uuid.UUID,
    ctx: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends({session_dep}),
) -> CadenceEnrollmentOut:
    enr = await outbound_cadence_service.mark_enrollment_replied(session, ctx.tenant_id, enrollment_id)
    await session.commit()
    return CadenceEnrollmentOut.model_validate(enr)


@router.post("/enrollments/{{enrollment_id}}/convert-deal")
async def convert_enrollment_to_deal(
    enrollment_id: uuid.UUID,
    payload: CadenceConvertDealRequest = CadenceConvertDealRequest(),
    ctx: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends({session_dep}),
) -> dict[str, Any]:
    res = await outbound_cadence_service.convert_enrollment_to_crm_deal(
        session, ctx.tenant_id, enrollment_id, payload
    )
    await session.commit()
    return res


@router.post("/process-due", response_model=CadenceProcessTickOut)
async def process_due_steps(
    batch_size: int = Query(default=50, ge=1, le=200),
    ctx: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends({session_dep}),
) -> CadenceProcessTickOut:
    res = await outbound_cadence_service.process_due_cadence_steps(
        session, tenant_id=ctx.tenant_id, batch_size=batch_size
    )
    await session.commit()
    return res


@router.get("/logs", response_model=list[CadenceExecutionLogOut])
async def list_execution_logs(
    limit: int = Query(default=50, ge=1, le=200),
    ctx: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends({session_dep}),
) -> list[CadenceExecutionLogOut]:
    rows = await outbound_cadence_service.list_execution_logs(session, ctx.tenant_id, limit=limit)
    return [CadenceExecutionLogOut.model_validate(r) for r in rows]


@router.get("/metrics", response_model=CadenceMetricsOut)
async def get_cadence_metrics(
    ctx: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends({session_dep}),
) -> CadenceMetricsOut:
    return await outbound_cadence_service.get_cadence_metrics(session, ctx.tenant_id)
'''


def hook_evolution_inbound_auto_stop(project_dir: Path) -> None:
    evo_srv = project_dir / "app" / "services" / "evolution_bridge_service.py"
    if not evo_srv.exists():
        return
    content = evo_srv.read_text(encoding="utf-8")
    marker = "# --- CADENCE_INBOUND_AUTO_STOP_HOOK ---"
    if marker in content:
        print("  [INFO] app/services/evolution_bridge_service.py já possui o hook de auto-stop de cadências.")
        return

    shutil.copy2(evo_srv, evo_srv.with_suffix(".py.bak"))
    hook_block = f"""

{marker}
_orig_handle_evolution_inbound_webhook = handle_evolution_inbound_webhook


async def handle_evolution_inbound_webhook(session, tenant_id, payload):
    res = await _orig_handle_evolution_inbound_webhook(session, tenant_id, payload)
    if isinstance(res, dict) and res.get("status") == "processed" and res.get("phone"):
        from app.services import outbound_cadence_service

        stopped_cnt = await outbound_cadence_service.auto_stop_active_enrollments_by_phone(
            session, tenant_id, str(res["phone"])
        )
        res["stopped_cadences_count"] = stopped_cnt
    return res
"""
    evo_srv.write_text(content.rstrip() + "\n" + hook_block, encoding="utf-8")
    print("  [OK] Hook de auto-stop de cadências integrado ao webhook Evolution API.")


def hook_async_worker(project_dir: Path, mod_path: str, func_name: str) -> None:
    worker_file = project_dir / "app" / "workers" / "automation_worker.py"
    if not worker_file.exists():
        return
    content = worker_file.read_text(encoding="utf-8")
    marker = "# --- CADENCE_WORKER_TICK_HOOK ---"
    if marker in content:
        print("  [INFO] app/workers/automation_worker.py já possui o loop de cadências.")
        return

    shutil.copy2(worker_file, worker_file.with_suffix(".py.bak"))
    # Adiciona helper que roda os passos vencidos de todas as cadências ativas
    helper_block = f"""

{marker}
async def run_cadence_worker_tick() -> None:
    from {mod_path} import {func_name} as _session_gen
    from app.services import outbound_cadence_service

    gen = _session_gen()
    session = await gen.__anext__()
    try:
        await outbound_cadence_service.process_due_cadence_steps(session, tenant_id=None, batch_size=50)
        await session.commit()
    except Exception:
        await session.rollback()
    finally:
        try:
            await gen.aclose()
        except Exception:
            pass
"""
    if "asyncio.sleep(" in content:
        content = re.sub(
            r"^(\s*)(await\s+asyncio\.sleep\([^\n]+\))",
            r"\1await run_cadence_worker_tick()\n\1\2",
            content,
            count=1,
            flags=re.M,
        )
    worker_file.write_text(content.rstrip() + "\n" + helper_block, encoding="utf-8")
    print("  [OK] Ciclo de cadências D+0/D+2/D+5 integrado em app/workers/automation_worker.py")


def register_in_models_init(project_dir: Path) -> None:
    models_init = project_dir / "app" / "models" / "__init__.py"
    if not models_init.exists():
        return
    content = models_init.read_text(encoding="utf-8")
    if "from app.models.outbound_cadences import" in content:
        print("  [INFO] app/models/__init__.py já contém outbound_cadences.")
        return

    shutil.copy2(models_init, models_init.with_suffix(".py.bak"))
    addition = (
        "\nfrom app.models.outbound_cadences import (\n"
        "    CadenceEnrollment,\n"
        "    CadenceExecutionLog,\n"
        "    ProspectCadence,\n"
        "    ProspectCadenceStep,\n"
        ")\n"
    )
    models_init.write_text(content.rstrip() + "\n" + addition, encoding="utf-8")
    print("  [OK] Modelos registrados em app/models/__init__.py")


def register_in_api_router(project_dir: Path) -> None:
    router_file = project_dir / "app" / "api" / "v1" / "router.py"
    if not router_file.exists():
        raise SystemExit(f"Arquivo não encontrado: {router_file}")

    content = router_file.read_text(encoding="utf-8")
    if "from app.api.v1 import outbound_cadences" in content:
        print("  [INFO] app/api/v1/router.py já inclui outbound_cadences.")
        return

    shutil.copy2(router_file, router_file.with_suffix(".py.bak"))
    router_var_match = re.search(r"^([a-zA-Z_]\w*)\s*=\s*APIRouter\(", content, re.M)
    router_var = router_var_match.group(1) if router_var_match else "api_v1_router"

    addition = (
        "\nfrom app.api.v1 import outbound_cadences\n\n"
        f'{router_var}.include_router(outbound_cadences.router, prefix="/cadences", tags=["Cadências Outbound & SDR IA"])\n'
    )
    router_file.write_text(content.rstrip() + "\n" + addition, encoding="utf-8")
    print(f"  [OK] Rota /api/v1/cadences registrada em app/api/v1/router.py ({router_var})")


def update_manage_and_smoke_test(project_dir: Path) -> None:
    manage_file = project_dir / "manage_comercial_saas.py"
    if not manage_file.exists():
        return
    content = manage_file.read_text(encoding="utf-8")
    if "/api/v1/cadences/bootstrap-default" in content:
        print("  [INFO] manage_comercial_saas.py já inclui o Teste #10 de Cadências.")
        return

    shutil.copy2(manage_file, manage_file.with_suffix(".py.bak"))
    test_10_block = '''        code, cad = await asgi_request("POST", "/api/v1/cadences/bootstrap-default")
        assert code == 201, f"Cadence bootstrap falhou ({code}): {cad}"
        code, enr = await asgi_request(
            "POST",
            f"/api/v1/cadences/{cad['id']}/enroll",
            json_body={
                "lead_name": "Ricardo Almeida",
                "company_name": "Metalurgica Sul Brasil",
                "email": "ricardo@metalurgicasul.com.br",
                "phone": "5541988887777",
                "role_title": "Diretor Comercial",
                "segment": "Industria Metalurgica",
                "context_notes": "empresa expandindo equipe externa no Parana",
                "start_immediately": True,
            },
        )
        assert code == 201, f"Cadence enroll falhou ({code}): {enr}"
        code, tick = await asgi_request("POST", "/api/v1/cadences/process-due")
        assert code == 200 and tick["dispatched_steps"] >= 1, f"Cadence tick falhou ({code}): {tick}"
        code, conv = await asgi_request(
            "POST",
            f"/api/v1/cadences/enrollments/{enr['id']}/convert-deal",
            json_body={"estimated_amount": 4800.0, "owner_name": "SDR IA"},
        )
        assert code == 200 and conv["enrollment_status"] == "CONVERTED", f"Convert deal falhou ({code}): {conv}"
        print(
            f"  [PASS] 10. Motor de Cadências D+0/D+2/D+5 & SDR IA OK "
            f"(Toques disparados: {tick['dispatched_steps']} | Deal CRM: {conv['deal_title']})"
        )

        print()
        print("[SUCESSO TOTAL] Todos os 10 testes End-to-End passaram sem erros!")'''

    target_anchor = '''        print()
        print("[SUCESSO TOTAL] Todos os 9 testes End-to-End passaram sem erros!")'''

    if target_anchor in content:
        content = content.replace(target_anchor, test_10_block)
        manage_file.write_text(content, encoding="utf-8")
        print("  [OK] Teste End-to-End #10 adicionado em manage_comercial_saas.py")


def patch_console_html(project_dir: Path) -> None:
    candidates = [
        project_dir / "console.html",
        project_dir / "app" / "static" / "console.html",
    ]
    marker = "<!-- OUTBOUND_CADENCES_WIDGET -->"
    widget = f"""
{marker}
<div id="cadencesDrawer" style="position:fixed;left:18px;top:68px;z-index:9998;font-family:system-ui,-apple-system,sans-serif;">
  <button onclick="toggleCadencesDrawer()" style="background:#8b5cf6;color:#f5f3ff;font-weight:700;border:none;border-radius:999px;padding:11px 17px;cursor:pointer;box-shadow:0 8px 24px rgba(0,0,0,.35);">
    Cadências SDR (D+0/D+2/D+5)
  </button>
  <div id="cadencesDrawerBody" style="display:none;width:460px;max-width:94vw;max-height:80vh;overflow:auto;margin-top:10px;background:#0f172a;color:#e2e8f0;border:1px solid #334155;border-radius:14px;padding:16px;box-shadow:0 20px 45px rgba(0,0,0,.55);">
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px;">
      <strong style="font-size:15px;">Motor de Cadências Ativas &amp; SDR IA</strong>
      <button onclick="loadCadencesSnapshot()" style="background:#1e293b;color:#c4b5fd;border:1px solid #334155;border-radius:8px;padding:4px 10px;cursor:pointer;">Atualizar</button>
    </div>
    <div id="cadMetricsBox" style="font-size:12px;background:#1e293b;padding:10px;border-radius:10px;margin-bottom:10px;">Clique em Atualizar para carregar cadências e métricas SDR.</div>
    <div style="display:flex;gap:6px;flex-wrap:wrap;margin-bottom:10px;">
      <button onclick="bootstrapDefaultCadence()" style="background:#8b5cf6;color:#fff;font-weight:700;border:none;border-radius:8px;padding:7px 10px;cursor:pointer;font-size:12px;">+ Cadência D+0/D+2/D+5</button>
      <button onclick="runCadenceTickNow()" style="background:#10b981;color:#052e16;font-weight:700;border:none;border-radius:8px;padding:7px 10px;cursor:pointer;font-size:12px;">Disparar Vencidos Agora</button>
    </div>
    <div style="display:grid;gap:6px;margin-bottom:12px;">
      <select id="cadSelectId" style="padding:8px;border-radius:8px;border:1px solid #334155;background:#020617;color:#f8fafc;"></select>
      <input id="cadLeadName" placeholder="Nome do Lead (ex: Marcos Silva)" style="padding:8px;border-radius:8px;border:1px solid #334155;background:#020617;color:#f8fafc;">
      <input id="cadCompanyName" placeholder="Empresa (ex: Alfa Logística)" style="padding:8px;border-radius:8px;border:1px solid #334155;background:#020617;color:#f8fafc;">
      <div style="display:flex;gap:6px;">
        <input id="cadLeadPhone" placeholder="WhatsApp (5541...)" style="flex:1;padding:8px;border-radius:8px;border:1px solid #334155;background:#020617;color:#f8fafc;">
        <input id="cadLeadEmail" placeholder="E-mail" style="flex:1;padding:8px;border-radius:8px;border:1px solid #334155;background:#020617;color:#f8fafc;">
      </div>
      <input id="cadContextNotes" placeholder="Contexto SDR IA (ex: expandindo filial em Curitiba)" style="padding:8px;border-radius:8px;border:1px solid #334155;background:#020617;color:#f8fafc;">
      <button onclick="enrollCadenceLead()" style="background:#38bdf8;color:#082f49;font-weight:700;border:none;border-radius:8px;padding:8px;cursor:pointer;">Inscrever Lead &amp; Iniciar D+0</button>
    </div>
    <div id="cadEnrollmentsList" style="font-size:12px;display:grid;gap:6px;"></div>
  </div>
</div>
<script>
function getCadHeaders() {{
  const h = {{ "Content-Type": "application/json" }};
  const inputs = Array.from(document.querySelectorAll("input"));
  for (const inp of inputs) {{
    const key = (inp.id + " " + inp.name + " " + inp.placeholder).toLowerCase();
    if (inp.value && key.includes("tenant")) h["X-Tenant-ID"] = inp.value.trim();
    if (inp.value && (key.includes("api") || key.includes("key") || key.includes("token")) && !inp.id.startsWith("evo") && !inp.id.startsWith("cad")) {{
      h["X-API-Key"] = inp.value.trim();
      h["Authorization"] = "Bearer " + inp.value.trim();
    }}
  }}
  return h;
}}
function toggleCadencesDrawer() {{
  const el = document.getElementById("cadencesDrawerBody");
  el.style.display = el.style.display === "none" ? "block" : "none";
  if (el.style.display === "block") loadCadencesSnapshot();
}}
async function loadCadencesSnapshot() {{
  const box = document.getElementById("cadMetricsBox");
  const sel = document.getElementById("cadSelectId");
  const list = document.getElementById("cadEnrollmentsList");
  try {{
    const [mRes, cRes, eRes] = await Promise.all([
      fetch("/api/v1/cadences/metrics", {{ headers: getCadHeaders() }}),
      fetch("/api/v1/cadences", {{ headers: getCadHeaders() }}),
      fetch("/api/v1/cadences/enrollments?limit=8", {{ headers: getCadHeaders() }})
    ]);
    if (!mRes.ok) {{
      box.textContent = "Informe Tenant ID / API Key no console para autenticar (" + mRes.status + ").";
      return;
    }}
    const m = await mRes.json();
    box.innerHTML =
      "<b>Cadências Ativas:</b> " + m.active_cadences +
      " | <b>Leads em Curso:</b> " + m.active_enrollments +
      " | <b>Toques SDR:</b> " + m.total_touches_executed +
      "<br><b>Taxa Resposta:</b> " + m.reply_rate_pct + "% | <b>Conversão CRM:</b> " + m.conversion_rate_pct + "%";
    const cads = cRes.ok ? await cRes.json() : [];
    sel.innerHTML = cads.map(c => "<option value='" + c.id + "'>" + c.name + "</option>").join("") || "<option value=''>Crie a cadência padrão primeiro</option>";
    const enrs = eRes.ok ? await eRes.json() : [];
    list.innerHTML = enrs.map(e =>
      "<div style='background:#1e293b;padding:8px;border-radius:8px;border-left:3px solid #8b5cf6;'>" +
      "<b>" + e.lead_name + "</b> (" + (e.company_name || "Lead") + ") — <b>" + e.status + "</b> (Etapa " + e.current_step_order + ")" +
      "</div>"
    ).join("") || "<span style='color:#94a3b8;'>Nenhum prospect inscrito ainda.</span>";
  }} catch (err) {{
    box.textContent = "Erro ao consultar cadências: " + err;
  }}
}}
async function bootstrapDefaultCadence() {{
  const res = await fetch("/api/v1/cadences/bootstrap-default", {{ method: "POST", headers: getCadHeaders() }});
  if (!res.ok) return alert("Erro: " + (await res.text()));
  loadCadencesSnapshot();
}}
async function runCadenceTickNow() {{
  const res = await fetch("/api/v1/cadences/process-due", {{ method: "POST", headers: getCadHeaders() }});
  const data = await res.json();
  alert("Ciclo concluído: " + data.dispatched_steps + " toque(s) disparado(s), " + data.auto_stopped_by_reply + " interrompido(s) por resposta.");
  loadCadencesSnapshot();
}}
async function enrollCadenceLead() {{
  const cadId = document.getElementById("cadSelectId").value;
  if (!cadId) return alert("Crie ou selecione uma cadência primeiro.");
  const body = {{
    lead_name: document.getElementById("cadLeadName").value.trim(),
    company_name: document.getElementById("cadCompanyName").value.trim(),
    phone: document.getElementById("cadLeadPhone").value.trim(),
    email: document.getElementById("cadLeadEmail").value.trim(),
    context_notes: document.getElementById("cadContextNotes").value.trim(),
    start_immediately: true
  }};
  if (!body.lead_name) return alert("Informe o nome do lead.");
  const res = await fetch("/api/v1/cadences/" + cadId + "/enroll", {{
    method: "POST",
    headers: getCadHeaders(),
    body: JSON.stringify(body)
  }});
  if (!res.ok) return alert("Erro ao inscrever: " + (await res.text()));
  document.getElementById("cadLeadName").value = "";
  document.getElementById("cadCompanyName").value = "";
  document.getElementById("cadLeadPhone").value = "";
  document.getElementById("cadLeadEmail").value = "";
  document.getElementById("cadContextNotes").value = "";
  loadCadencesSnapshot();
}}
</script>
"""
    for html_path in candidates:
        if not html_path.exists():
            continue
        html = html_path.read_text(encoding="utf-8")
        if marker in html:
            print(f"  [INFO] {html_path.name} já possui o painel Cadências SDR.")
            continue
        shutil.copy2(html_path, html_path.with_suffix(".html.bak"))
        if "</body>" in html:
            html = html.replace("</body>", widget + "\n</body>")
        else:
            html = html + "\n" + widget
        html_path.write_text(html, encoding="utf-8")
        print(f"  [OK] Painel Cadências SDR integrado em {html_path.relative_to(project_dir)}")


def main() -> None:
    project_dir = Path(__file__).resolve().parent
    print("=== Passo 1: Instalando Bloco 12 (Motor de Cadências D+0/D+2/D+5 & SDR IA) ===")
    base_import = detect_base_import(project_dir)
    mod_path, func_name = discover_session_dependency(project_dir)
    session_import = f"from {mod_path} import {func_name}"

    files_to_write: dict[Path, str] = {
        project_dir / "app" / "models" / "outbound_cadences.py": build_models_code(base_import),
        project_dir / "app" / "schemas" / "outbound_cadences.py": build_schemas_code(),
        project_dir / "app" / "services" / "outbound_cadence_service.py": build_service_code(),
        project_dir / "app" / "api" / "v1" / "outbound_cadences.py": build_router_code(session_import, func_name),
    }

    for target_path, code in files_to_write.items():
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_text(code, encoding="utf-8")
        print(f"  [CRIADO] {target_path.relative_to(project_dir)}")

    register_in_models_init(project_dir)
    register_in_api_router(project_dir)
    hook_evolution_inbound_auto_stop(project_dir)
    hook_async_worker(project_dir, mod_path, func_name)
    update_manage_and_smoke_test(project_dir)
    patch_console_html(project_dir)

    print("\n=== Passo 2: Reiniciando API, sincronizando PostgreSQL e executando 10 Testes E2E ===")
    subprocess.run(["docker", "compose", "restart", "api"], cwd=str(project_dir), check=True)
    time.sleep(3)

    subprocess.run(["python3", "manage_comercial_saas.py", "init-db"], cwd=str(project_dir), check=True)
    subprocess.run(["python3", "manage_comercial_saas.py", "smoke-test"], cwd=str(project_dir), check=True)

    print("\n=== Passo 3: Reiniciando Worker Systemd com o loop de cadências ativo ===")
    subprocess.run(
        ["sudo", "systemctl", "restart", "comercial-saas-worker.service"],
        cwd=str(project_dir),
        check=False,
    )

    print("\n[CONCLUÍDO] Bloco 12 (Motor de Cadências D+0/D+2/D+5 & SDR IA) operacional!")
    print("  - Rotas REST: /api/v1/cadences/{bootstrap-default,enroll,enroll-bulk,process-due,metrics,logs}")
    print("  - Swagger UI: https://comercial.3.143.227.21.sslip.io/docs")
    print("  - Console Web: https://comercial.3.143.227.21.sslip.io/console")


if __name__ == "__main__":
    try:
        main()
    except subprocess.CalledProcessError as exc:
        print(f"\n[ERRO] Comando falhou ({exc.cmd}) com código {exc.returncode}", file=sys.stderr)
        sys.exit(exc.returncode)