"""Camada de serviço do Módulo 8: Equipe Comercial, Metas & Portal Self-Service."""

from __future__ import annotations

import html
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.billing_commissions import BillingInvoice, SalesCommission
from app.models.crm_deals import ContractStatus, CrmContract, CrmDeal, DealStatus
from app.models.team_portal import (
    CustomerPortalToken,
    SalesQuotaPeriod,
    SalesRoleType,
    SalesTeamMember,
)
from app.schemas.team_portal import (
    LeaderboardEntryOut,
    PortalLinkCreateRequest,
    PortalLinkOut,
    PublicContractSignRequest,
    PublicPortalSummaryOut,
    QuotaUpsertRequest,
    TeamMemberCreate,
    TeamMemberUpdate,
    TeamPerformanceOut,
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _current_ym() -> str:
    return _utcnow().strftime("%Y-%m")


def _as_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _serialize_portal_link(row: CustomerPortalToken) -> PortalLinkOut:
    return PortalLinkOut(
        id=row.id,
        tenant_id=row.tenant_id,
        token=row.token,
        public_json_url=f"/api/v1/team/public/{row.token}",
        public_page_url=f"/api/v1/team/public/{row.token}/view",
        customer_name=row.customer_name,
        customer_email=row.customer_email,
        customer_document=row.customer_document,
        contract_id=row.contract_id,
        invoice_id=row.invoice_id,
        expires_at=row.expires_at,
        last_accessed_at=row.last_accessed_at,
        access_count=row.access_count,
        is_revoked=row.is_revoked,
        created_at=row.created_at,
    )


async def create_team_member(
    session: AsyncSession, tenant_id: uuid.UUID, data: TeamMemberCreate
) -> SalesTeamMember:
    dup_stmt = select(SalesTeamMember).where(
        SalesTeamMember.tenant_id == tenant_id,
        SalesTeamMember.email == data.email.strip().lower(),
    )
    if (await session.execute(dup_stmt)).scalars().first() is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Já existe um membro da equipe cadastrado com este e-mail.",
        )

    member = SalesTeamMember(
        tenant_id=tenant_id,
        full_name=data.full_name.strip(),
        email=data.email.strip().lower(),
        phone=data.phone,
        role=data.role,
        monthly_revenue_quota=Decimal(data.monthly_revenue_quota).quantize(Decimal("0.01")),
        monthly_deals_quota=data.monthly_deals_quota,
        is_active=data.is_active,
    )
    session.add(member)
    await session.flush()
    await session.refresh(member)
    return member


async def list_team_members(session: AsyncSession, tenant_id: uuid.UUID) -> list[SalesTeamMember]:
    stmt = (
        select(SalesTeamMember)
        .where(SalesTeamMember.tenant_id == tenant_id)
        .order_by(SalesTeamMember.is_active.desc(), SalesTeamMember.full_name.asc())
    )
    return list((await session.execute(stmt)).scalars().all())


async def update_team_member(
    session: AsyncSession, tenant_id: uuid.UUID, member_id: uuid.UUID, data: TeamMemberUpdate
) -> SalesTeamMember:
    stmt = select(SalesTeamMember).where(
        SalesTeamMember.tenant_id == tenant_id,
        SalesTeamMember.id == member_id,
    )
    member = (await session.execute(stmt)).scalars().first()
    if member is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Membro da equipe não encontrado.")

    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(member, field, value)

    await session.flush()
    await session.refresh(member)
    return member


async def upsert_quota_period(
    session: AsyncSession, tenant_id: uuid.UUID, data: QuotaUpsertRequest
) -> SalesQuotaPeriod:
    member_stmt = select(SalesTeamMember).where(
        SalesTeamMember.tenant_id == tenant_id,
        SalesTeamMember.id == data.member_id,
    )
    if (await session.execute(member_stmt)).scalars().first() is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Membro da equipe não encontrado.")

    q_stmt = select(SalesQuotaPeriod).where(
        SalesQuotaPeriod.tenant_id == tenant_id,
        SalesQuotaPeriod.member_id == data.member_id,
        SalesQuotaPeriod.period_ym == data.period_ym,
    )
    quota = (await session.execute(q_stmt)).scalars().first()
    if quota is None:
        quota = SalesQuotaPeriod(
            tenant_id=tenant_id,
            member_id=data.member_id,
            period_ym=data.period_ym,
            revenue_target=Decimal(data.revenue_target).quantize(Decimal("0.01")),
            deals_target=data.deals_target,
            notes=data.notes,
        )
        session.add(quota)
    else:
        quota.revenue_target = Decimal(data.revenue_target).quantize(Decimal("0.01"))
        quota.deals_target = data.deals_target
        quota.notes = data.notes

    await session.flush()
    await session.refresh(quota)
    return quota


async def list_quotas(
    session: AsyncSession, tenant_id: uuid.UUID, period_ym: str | None = None
) -> list[SalesQuotaPeriod]:
    stmt = select(SalesQuotaPeriod).where(SalesQuotaPeriod.tenant_id == tenant_id)
    if period_ym:
        stmt = stmt.where(SalesQuotaPeriod.period_ym == period_ym)
    stmt = stmt.order_by(SalesQuotaPeriod.period_ym.desc())
    return list((await session.execute(stmt)).scalars().all())


async def get_team_leaderboard(
    session: AsyncSession, tenant_id: uuid.UUID, period_ym: str | None = None
) -> TeamPerformanceOut:
    target_ym = period_ym or _current_ym()
    members = await list_team_members(session, tenant_id)
    quotas = await list_quotas(session, tenant_id, period_ym=target_ym)
    quota_by_member = {q.member_id: q for q in quotas}

    deals = list(
        (await session.execute(select(CrmDeal).where(CrmDeal.tenant_id == tenant_id))).scalars().all()
    )
    commissions = list(
        (await session.execute(select(SalesCommission).where(SalesCommission.tenant_id == tenant_id))).scalars().all()
    )

    leaderboard: list[LeaderboardEntryOut] = []
    total_target = Decimal("0.00")
    total_achieved = Decimal("0.00")

    for member in members:
        if not member.is_active:
            continue

        q_override = quota_by_member.get(member.id)
        rev_target = Decimal(q_override.revenue_target) if q_override else Decimal(member.monthly_revenue_quota)
        deals_target = q_override.deals_target if q_override else member.monthly_deals_quota

        name_lower = member.full_name.strip().lower()
        member_won_deals = [
            d
            for d in deals
            if d.status == DealStatus.WON
            and (d.owner_name or "").strip().lower() == name_lower
            and (d.closed_at or d.updated_at).strftime("%Y-%m") == target_ym
        ]
        member_open_deals = [
            d
            for d in deals
            if d.status == DealStatus.OPEN
            and (d.owner_name or "").strip().lower() == name_lower
        ]
        member_comms = [
            c
            for c in commissions
            if (c.beneficiary_name or "").strip().lower() == name_lower
            and c.created_at.strftime("%Y-%m") == target_ym
        ]

        rev_achieved = sum((Decimal(d.amount) for d in member_won_deals), Decimal("0.00"))
        open_val = sum((Decimal(d.amount) for d in member_open_deals), Decimal("0.00"))
        comm_val = sum((Decimal(c.commission_amount) for c in member_comms), Decimal("0.00"))
        attainment = round((float(rev_achieved) / float(rev_target)) * 100.0, 2) if rev_target > 0 else 0.0

        total_target += rev_target
        total_achieved += rev_achieved

        leaderboard.append(
            LeaderboardEntryOut(
                member_id=member.id,
                full_name=member.full_name,
                role=member.role,
                revenue_target=rev_target,
                revenue_achieved=rev_achieved,
                revenue_attainment_percent=attainment,
                deals_target=deals_target,
                deals_won=len(member_won_deals),
                open_pipeline_value=open_val,
                commissions_earned=comm_val,
            )
        )

    leaderboard.sort(key=lambda item: (item.revenue_achieved, item.open_pipeline_value), reverse=True)
    team_attainment = round((float(total_achieved) / float(total_target)) * 100.0, 2) if total_target > 0 else 0.0

    return TeamPerformanceOut(
        period_ym=target_ym,
        total_revenue_target=total_target,
        total_revenue_achieved=total_achieved,
        team_attainment_percent=team_attainment,
        leaderboard=leaderboard,
    )


async def create_portal_link(
    session: AsyncSession, tenant_id: uuid.UUID, data: PortalLinkCreateRequest
) -> PortalLinkOut:
    if data.contract_id is not None:
        c_res = await session.execute(
            select(CrmContract).where(CrmContract.tenant_id == tenant_id, CrmContract.id == data.contract_id)
        )
        if c_res.scalars().first() is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Contrato informado não encontrado.")

    if data.invoice_id is not None:
        i_res = await session.execute(
            select(BillingInvoice).where(BillingInvoice.tenant_id == tenant_id, BillingInvoice.id == data.invoice_id)
        )
        if i_res.scalars().first() is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Fatura informada não encontrada.")

    token_str = f"prt_{secrets.token_urlsafe(32)}"
    row = CustomerPortalToken(
        tenant_id=tenant_id,
        token=token_str,
        customer_name=data.customer_name.strip(),
        customer_email=data.customer_email,
        customer_document=data.customer_document,
        contract_id=data.contract_id,
        invoice_id=data.invoice_id,
        expires_at=_utcnow() + timedelta(days=data.expires_in_days),
        is_revoked=False,
    )
    session.add(row)
    await session.flush()
    await session.refresh(row)
    return _serialize_portal_link(row)


async def list_portal_links(session: AsyncSession, tenant_id: uuid.UUID) -> list[PortalLinkOut]:
    stmt = (
        select(CustomerPortalToken)
        .where(CustomerPortalToken.tenant_id == tenant_id)
        .order_by(CustomerPortalToken.created_at.desc())
        .limit(100)
    )
    rows = list((await session.execute(stmt)).scalars().all())
    return [_serialize_portal_link(r) for r in rows]


async def revoke_portal_link(
    session: AsyncSession, tenant_id: uuid.UUID, link_id: uuid.UUID
) -> PortalLinkOut:
    stmt = select(CustomerPortalToken).where(
        CustomerPortalToken.tenant_id == tenant_id,
        CustomerPortalToken.id == link_id,
    )
    row = (await session.execute(stmt)).scalars().first()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Link de portal não encontrado.")
    row.is_revoked = True
    await session.flush()
    await session.refresh(row)
    return _serialize_portal_link(row)


async def _get_valid_portal_token(session: AsyncSession, token: str) -> CustomerPortalToken:
    stmt = select(CustomerPortalToken).where(CustomerPortalToken.token == token)
    row = (await session.execute(stmt)).scalars().first()
    if row is None or row.is_revoked:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Link de portal inválido ou revogado.")
    if _as_utc(row.expires_at) < _utcnow():
        raise HTTPException(status_code=status.HTTP_410_GONE, detail="Este link de portal expirou.")
    return row


async def get_public_portal_summary(session: AsyncSession, token: str) -> PublicPortalSummaryOut:
    row = await _get_valid_portal_token(session, token)
    row.last_accessed_at = _utcnow()
    row.access_count += 1

    contract_data: dict | None = None
    if row.contract_id is not None:
        c = (
            await session.execute(
                select(CrmContract).where(CrmContract.tenant_id == row.tenant_id, CrmContract.id == row.contract_id)
            )
        ).scalars().first()
        if c is not None:
            contract_data = {
                "id": str(c.id),
                "contract_number": c.contract_number,
                "title": c.title,
                "customer_name": c.customer_name,
                "total_value": str(c.total_value),
                "recurring_monthly_value": str(c.recurring_monthly_value),
                "currency": c.currency,
                "status": c.status.value,
                "terms_summary": c.terms_summary,
                "signed_at": c.signed_at.isoformat() if c.signed_at else None,
                "signed_by_name": c.signed_by_name,
            }

    invoice_data: dict | None = None
    if row.invoice_id is not None:
        inv = (
            await session.execute(
                select(BillingInvoice).where(
                    BillingInvoice.tenant_id == row.tenant_id, BillingInvoice.id == row.invoice_id
                )
            )
        ).scalars().first()
        if inv is not None:
            invoice_data = {
                "id": str(inv.id),
                "invoice_number": inv.invoice_number,
                "description": inv.description,
                "net_amount": str(inv.net_amount),
                "currency": inv.currency,
                "payment_method": inv.payment_method.value,
                "status": inv.status.value,
                "due_date": inv.due_date.isoformat(),
                "pix_copy_paste": inv.pix_copy_paste,
                "payment_link": inv.payment_link,
            }

    await session.flush()
    return PublicPortalSummaryOut(
        token=row.token,
        customer_name=row.customer_name,
        customer_email=row.customer_email,
        customer_document=row.customer_document,
        expires_at=row.expires_at,
        contract=contract_data,
        invoice=invoice_data,
    )


async def sign_contract_via_portal(
    session: AsyncSession, token: str, req: PublicContractSignRequest
) -> PublicPortalSummaryOut:
    if not req.accepted_terms:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="É obrigatório aceitar os termos do contrato para assinar.",
        )
    row = await _get_valid_portal_token(session, token)
    if row.contract_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Este link de portal não possui contrato vinculado.",
        )

    c = (
        await session.execute(
            select(CrmContract).where(CrmContract.tenant_id == row.tenant_id, CrmContract.id == row.contract_id)
        )
    ).scalars().first()
    if c is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Contrato vinculado não encontrado.")

    c.signed_at = _utcnow()
    c.signed_by_name = req.signed_by_name.strip()
    c.signed_by_document = req.signed_by_document.strip()
    c.status = ContractStatus.ACTIVE
    if c.start_date is None:
        c.start_date = c.signed_at

    await session.flush()
    return await get_public_portal_summary(session, token)


def render_public_portal_html(summary: PublicPortalSummaryOut) -> str:
    cust_name = html.escape(summary.customer_name)
    contract_block = "<p style='color:#94a3b8;'>Nenhum contrato vinculado a este link.</p>"
    if summary.contract:
        c = summary.contract
        is_signed = c.get("status") in ("SIGNED", "ACTIVE")
        status_badge = (
            f"<span style='background:#065f46;color:#6ee7b7;padding:3px 10px;border-radius:999px;font-size:12px;'>Assinado por {html.escape(str(c.get('signed_by_name') or ''))}</span>"
            if is_signed
            else "<span style='background:#78350f;color:#fcd34d;padding:3px 10px;border-radius:999px;font-size:12px;'>Aguardando Assinatura</span>"
        )
        sign_form = (
            ""
            if is_signed
            else f"""
            <div style="margin-top:14px;display:grid;gap:8px;">
              <input id="signerName" placeholder="Nome completo do representante legal" style="padding:10px;border-radius:8px;border:1px solid #334155;background:#020617;color:#f8fafc;">
              <input id="signerDoc" placeholder="CPF ou CNPJ do signatário" style="padding:10px;border-radius:8px;border:1px solid #334155;background:#020617;color:#f8fafc;">
              <button onclick="signPortalContract()" style="background:#10b981;color:#06281e;font-weight:700;border:none;border-radius:8px;padding:11px;cursor:pointer;">Assinar Contrato Digitalmente</button>
            </div>
            """
        )
        contract_block = f"""
        <div style="background:#1e293b;padding:16px;border-radius:12px;margin-bottom:16px;">
          <div style="display:flex;justify-content:space-between;align-items:center;gap:8px;">
            <strong>Contrato {html.escape(str(c['contract_number']))} — {html.escape(str(c['title']))}</strong>
            {status_badge}
          </div>
          <p style="margin:8px 0;color:#cbd5e1;">Valor: <b>{html.escape(str(c['currency']))} {html.escape(str(c['total_value']))}</b></p>
          <p style="margin:8px 0;font-size:13px;color:#94a3b8;">{html.escape(str(c.get('terms_summary') or 'Condições comerciais conforme proposta aprovada.'))}</p>
          {sign_form}
        </div>
        """

    invoice_block = "<p style='color:#94a3b8;'>Nenhuma cobrança vinculada a este link.</p>"
    if summary.invoice:
        inv = summary.invoice
        pix_code = html.escape(str(inv.get("pix_copy_paste") or ""))
        pix_section = (
            f"""
            <div style="margin-top:10px;">
              <label style="font-size:12px;color:#94a3b8;">PIX Copia e Cola:</label>
              <textarea id="pixPayload" readonly rows="3" style="width:100%;margin-top:4px;padding:8px;border-radius:8px;border:1px solid #334155;background:#020617;color:#38bdf8;font-size:12px;">{pix_code}</textarea>
              <button onclick="copyPix()" style="margin-top:6px;background:#38bdf8;color:#082f49;font-weight:700;border:none;border-radius:8px;padding:8px 14px;cursor:pointer;">Copiar Código PIX</button>
            </div>
            """
            if pix_code
            else ""
        )
        invoice_block = f"""
        <div style="background:#1e293b;padding:16px;border-radius:12px;">
          <div style="display:flex;justify-content:space-between;align-items:center;">
            <strong>Fatura {html.escape(str(inv['invoice_number']))}</strong>
            <span style="background:#1d4ed8;color:#dbeafe;padding:3px 10px;border-radius:999px;font-size:12px;">{html.escape(str(inv['status']))}</span>
          </div>
          <p style="margin:8px 0;color:#cbd5e1;">{html.escape(str(inv['description']))} — <b>{html.escape(str(inv['currency']))} {html.escape(str(inv['net_amount']))}</b></p>
          {pix_section}
        </div>
        """

    safe_token = html.escape(summary.token)
    return f"""<!doctype html>
<html lang="pt-BR">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>Portal do Cliente — {cust_name}</title>
</head>
<body style="margin:0;background:#020617;color:#f8fafc;font-family:system-ui,-apple-system,sans-serif;padding:24px;">
  <div style="max-width:680px;margin:0 auto;background:#0f172a;border:1px solid #334155;border-radius:16px;padding:24px;">
    <h2 style="margin-top:0;">Portal do Cliente</h2>
    <p style="color:#94a3b8;">Olá, <strong style="color:#f8fafc;">{cust_name}</strong>. Confira abaixo seu contrato e dados de pagamento.</p>
    {contract_block}
    {invoice_block}
  </div>
  <script>
    async function signPortalContract() {{
      const signed_by_name = document.getElementById("signerName").value.trim();
      const signed_by_document = document.getElementById("signerDoc").value.trim();
      if (!signed_by_name || !signed_by_document) return alert("Informe nome completo e CPF/CNPJ.");
      const res = await fetch("/api/v1/team/public/{safe_token}/sign-contract", {{
        method: "POST",
        headers: {{ "Content-Type": "application/json" }},
        body: JSON.stringify({{ signed_by_name, signed_by_document, accepted_terms: true }})
      }});
      if (!res.ok) return alert("Erro ao assinar: " + (await res.text()));
      alert("Contrato assinado com sucesso!");
      location.reload();
    }}
    function copyPix() {{
      const el = document.getElementById("pixPayload");
      el.select();
      navigator.clipboard.writeText(el.value);
      alert("Código PIX copiado!");
    }}
  </script>
</body>
</html>"""
