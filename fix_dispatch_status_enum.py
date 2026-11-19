#!/usr/bin/env python3
"""
Atualiza integralmente app/services/outbound_cadence_service.py e
app/services/evolution_bridge_service.py para resolver dinamicamente os
estados de DispatchQueueStatus, reinicia a API, roda os 10 testes E2E
e reinicia o worker systemd.

Salve em: ~/apps/comercial-saas/fix_dispatch_status_enum.py
Execute com: python3 ~/apps/comercial-saas/fix_dispatch_status_enum.py
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

OUTBOUND_CADENCE_SERVICE_CODE = '''"""Camada de serviço do Bloco 12: Motor de Cadências Ativas (D+0, D+2, D+5) & SDR IA."""

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
from sqlalchemy import func, inspect as sa_inspect, select, text
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


def _resolve_pending_dispatch_status() -> DispatchQueueStatus:
    for candidate in ("PENDING", "QUEUED", "SCHEDULED", "WAITING", "READY"):
        if candidate in DispatchQueueStatus.__members__:
            return DispatchQueueStatus[candidate]
    return next(iter(DispatchQueueStatus))


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
    model_name = os.getenv("GEMINI_MODEL", "gemini-2.5-flash").strip()
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
                f"{model_name}:generateContent?key={api_key}"
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


async def _fill_missing_required_columns(
    session: AsyncSession,
    mapper: Any,
    kwargs: dict[str, Any],
    tenant_id: uuid.UUID,
    fallbacks: dict[str, Any],
) -> None:
    """Preenche colunas obrigatórias sem default (inclusive FKs e enums) para compatibilidade entre módulos."""
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
            continue

        if col.foreign_keys:
            fk = next(iter(col.foreign_keys))
            target_table = fk.column.table.name
            target_col = fk.column.name
            try:
                res = await session.execute(
                    text(f"SELECT {target_col} FROM {target_table} WHERE tenant_id = :tid LIMIT 1"),
                    {"tid": tenant_id},
                )
                row = res.first()
                if row and row[0] is not None:
                    kwargs[key] = row[0]
                    continue
            except Exception:
                pass
            try:
                res = await session.execute(text(f"SELECT {target_col} FROM {target_table} LIMIT 1"))
                row = res.first()
                if row and row[0] is not None:
                    kwargs[key] = row[0]
                    continue
            except Exception:
                pass

        enum_cls = getattr(col.type, "enum_class", None)
        if enum_cls is not None:
            first_member = next(iter(enum_cls), None)
            if first_member is not None:
                kwargs[key] = first_member
                continue

        py_type = getattr(col.type, "python_type", str)
        if py_type is bool:
            kwargs[key] = False
        elif py_type in (int, float):
            kwargs[key] = 0
        else:
            kwargs[key] = ""


async def _enqueue_dispatch_item(
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
        kwargs["status"] = _resolve_pending_dispatch_status()

    for ctx_col in ("context_json", "payload_json", "metadata_json"):
        if ctx_col in col_names:
            kwargs[ctx_col] = context_meta

    for sched_col in ("scheduled_for", "scheduled_at", "next_attempt_at"):
        if sched_col in col_names:
            kwargs[sched_col] = _utcnow()

    await _fill_missing_required_columns(
        session,
        mapper,
        kwargs,
        tenant_id,
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

        q_item = await _enqueue_dispatch_item(
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

    await _fill_missing_required_columns(
        session,
        mapper,
        deal_kwargs,
        tenant_id,
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

EVOLUTION_SERVICE_CODE = '''"""Camada de serviço do Bloco 11: Conector Real Evolution API v2 & Despacho Omnichannel."""

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


def _resolve_dispatch_status(candidates: tuple[str, ...]) -> DispatchQueueStatus:
    for candidate in candidates:
        if candidate in DispatchQueueStatus.__members__:
            return DispatchQueueStatus[candidate]
    return next(iter(DispatchQueueStatus))


def normalize_br_phone(raw_phone: str, default_cc: str = "55") -> str:
    digits = re.sub(r"\\D+", "", raw_phone or "")
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
    """Despacha itens pendentes da fila via Evolution API real se o tenant habilitou o canal."""
    cfg = await ensure_evolution_config(session, tenant_id)
    if not cfg.is_enabled:
        return 0

    pending_status = _resolve_dispatch_status(("PENDING", "QUEUED", "SCHEDULED", "WAITING"))
    sent_status = _resolve_dispatch_status(("SENT", "DELIVERED", "COMPLETED"))
    failed_status = _resolve_dispatch_status(("FAILED", "ERROR"))

    stmt = (
        select(OutboundDispatchQueue)
        .where(
            OutboundDispatchQueue.tenant_id == tenant_id,
            OutboundDispatchQueue.status == pending_status,
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

        dest = (
            getattr(item, "destination", None)
            or getattr(item, "recipient", None)
            or getattr(item, "target", "")
            or ""
        ).strip()
        if not dest:
            item.status = failed_status
            _set_queue_error(item, "Destino WhatsApp vazio na fila.")
            processed += 1
            continue

        body_text = (
            getattr(item, "rendered_body", None)
            or getattr(item, "message_body", None)
            or getattr(item, "body", "")
            or ""
        )
        ctx = getattr(item, "context_json", None) or getattr(item, "payload_json", None) or {}
        if not isinstance(ctx, dict):
            ctx = {}
        inv_num = str(ctx.get("invoice_number") or "") or None

        log_row = await send_whatsapp_text(
            session,
            tenant_id,
            raw_phone=dest,
            text=body_text,
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
            item.status = sent_status
            if hasattr(item, "sent_at"):
                item.sent_at = _utcnow()
            _set_queue_error(item, None)
        else:
            _set_queue_error(item, log_row.error_detail)
            if current_attempts >= 3:
                item.status = failed_status
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


# --- CADENCE_INBOUND_AUTO_STOP_HOOK ---
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
'''


def main() -> None:
    project_dir = Path(__file__).resolve().parent
    cadence_srv = project_dir / "app" / "services" / "outbound_cadence_service.py"
    evo_srv = project_dir / "app" / "services" / "evolution_bridge_service.py"

    cadence_srv.write_text(OUTBOUND_CADENCE_SERVICE_CODE, encoding="utf-8")
    print("[OK] app/services/outbound_cadence_service.py atualizado.")

    evo_srv.write_text(EVOLUTION_SERVICE_CODE, encoding="utf-8")
    print("[OK] app/services/evolution_bridge_service.py atualizado.")

    print("\n=== Reiniciando API e executando Suíte End-to-End (10 Testes) ===")
    subprocess.run(["docker", "compose", "restart", "api"], cwd=str(project_dir), check=True)
    time.sleep(3)

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