"""Camada de serviço do Módulo de Faturamento, Cobranças e Comissões."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.billing_commissions import (
    BillingEventType,
    BillingInvoice,
    BillingPaymentEvent,
    CommissionRoleType,
    CommissionRule,
    CommissionStatus,
    InvoiceStatus,
    PaymentMethodType,
    SalesCommission,
)
from app.models.crm_deals import CrmContract, CrmDeal
from app.schemas.billing_commissions import (
    CommissionRuleCreate,
    CommissionStatusUpdateRequest,
    ContractInvoiceGenerateRequest,
    FinancialSummaryOut,
    InvoiceCreate,
    InvoiceOut,
    InvoicePayRequest,
    InvoicePayResponse,
    SalesCommissionOut,
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _build_pix_payload(invoice_number: str, customer_name: str, net_amount: Decimal) -> str:
    clean_customer = "".join(ch for ch in customer_name.upper() if ch.isalnum() or ch == " ")[:20].strip() or "CLIENTE"
    amount_str = f"{net_amount:.2f}"
    return (
        f"00020126360014BR.GOV.BCB.PIX0114+5541999999999"
        f"52040000530398654{len(amount_str):02d}{amount_str}"
        f"5802BR59{len(clean_customer):02d}{clean_customer}"
        f"6008CURITIBA62{len(invoice_number)+4:02d}05{len(invoice_number):02d}{invoice_number}6304ABCD"
    )


async def _next_invoice_number(session: AsyncSession, tenant_id: uuid.UUID) -> str:
    year = _utcnow().year
    res = await session.execute(select(func.count(BillingInvoice.id)).where(BillingInvoice.tenant_id == tenant_id))
    seq = int(res.scalar_one() or 0) + 1
    return f"FAT-{year}-{seq:04d}"


async def ensure_default_commission_rules(session: AsyncSession, tenant_id: uuid.UUID) -> list[CommissionRule]:
    stmt = (
        select(CommissionRule)
        .where(CommissionRule.tenant_id == tenant_id)
        .order_by(CommissionRule.created_at.asc())
    )
    existing = list((await session.execute(stmt)).scalars().all())
    if existing:
        return existing

    defaults = [
        CommissionRule(
            tenant_id=tenant_id,
            name="Closer Comercial Padrão (10%)",
            role_type=CommissionRoleType.CLOSER,
            percentage=Decimal("10.00"),
            fixed_bonus=Decimal("0.00"),
            is_active=True,
        ),
        CommissionRule(
            tenant_id=tenant_id,
            name="SDR / Pré-Vendas Padrão (3%)",
            role_type=CommissionRoleType.SDR,
            percentage=Decimal("3.00"),
            fixed_bonus=Decimal("0.00"),
            is_active=True,
        ),
    ]
    for rule in defaults:
        session.add(rule)
    await session.flush()

    res = await session.execute(stmt)
    return list(res.scalars().all())


async def create_invoice(
    session: AsyncSession, tenant_id: uuid.UUID, data: InvoiceCreate
) -> BillingInvoice:
    net_amount = (Decimal(data.amount) - Decimal(data.discount_amount)).quantize(Decimal("0.01"))
    if net_amount <= Decimal("0.00"):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="O valor líquido da fatura deve ser maior que zero.",
        )

    invoice_number = await _next_invoice_number(session, tenant_id)
    due_date = data.due_date or (_utcnow() + timedelta(days=data.due_in_days))
    charge_id = f"chg_{uuid.uuid4().hex[:16]}"
    pix_code = (
        _build_pix_payload(invoice_number, data.customer_name, net_amount)
        if data.payment_method == PaymentMethodType.PIX
        else None
    )
    payment_link = f"https://pay.tbtc.local/faturas/{invoice_number.lower()}"

    invoice = BillingInvoice(
        tenant_id=tenant_id,
        contract_id=data.contract_id,
        deal_id=data.deal_id,
        invoice_number=invoice_number,
        customer_name=data.customer_name,
        customer_email=data.customer_email,
        customer_document=data.customer_document,
        description=data.description,
        amount=Decimal(data.amount).quantize(Decimal("0.01")),
        discount_amount=Decimal(data.discount_amount).quantize(Decimal("0.01")),
        net_amount=net_amount,
        currency=data.currency,
        payment_method=data.payment_method,
        status=InvoiceStatus.PENDING,
        due_date=due_date,
        external_charge_id=charge_id,
        pix_copy_paste=pix_code,
        payment_link=payment_link,
        closer_name=data.closer_name,
        sdr_name=data.sdr_name,
        metadata_json=data.metadata_json,
    )
    session.add(invoice)
    await session.flush()

    session.add(
        BillingPaymentEvent(
            tenant_id=tenant_id,
            invoice_id=invoice.id,
            event_type=BillingEventType.CREATED,
            amount=net_amount,
            gateway_reference=charge_id,
            notes=f"Fatura {invoice_number} emitida via {data.payment_method.value}.",
            payload={"invoice_number": invoice_number, "payment_method": data.payment_method.value},
        )
    )
    await session.flush()
    await session.refresh(invoice)
    return invoice


async def generate_invoice_from_contract(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    contract_id: uuid.UUID,
    req: ContractInvoiceGenerateRequest,
) -> BillingInvoice:
    contract_stmt = select(CrmContract).where(
        CrmContract.tenant_id == tenant_id,
        CrmContract.id == contract_id,
    )
    contract = (await session.execute(contract_stmt)).scalars().first()
    if contract is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Contrato não encontrado.")

    base_value = (
        Decimal(contract.recurring_monthly_value)
        if Decimal(contract.recurring_monthly_value) > Decimal("0.00")
        else Decimal(contract.total_value)
    )
    if base_value <= Decimal("0.00"):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="O contrato não possui valor financeiro maior que zero.",
        )

    closer_name = req.closer_name
    if not closer_name and contract.deal_id:
        deal = (
            await session.execute(select(CrmDeal).where(CrmDeal.id == contract.deal_id))
        ).scalars().first()
        if deal and deal.owner_name:
            closer_name = deal.owner_name

    payload = InvoiceCreate(
        contract_id=contract.id,
        deal_id=contract.deal_id,
        customer_name=contract.customer_name,
        customer_email=contract.customer_email,
        customer_document=contract.customer_document,
        description=req.description or f"Faturamento {contract.contract_number} - {contract.title}",
        amount=base_value,
        discount_amount=req.discount_amount,
        currency=contract.currency,
        payment_method=req.payment_method,
        due_in_days=req.due_in_days,
        closer_name=closer_name,
        sdr_name=req.sdr_name,
        metadata_json={"contract_number": contract.contract_number},
    )
    return await create_invoice(session, tenant_id, payload)


async def list_invoices(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    status_filter: InvoiceStatus | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[BillingInvoice]:
    stmt = select(BillingInvoice).where(BillingInvoice.tenant_id == tenant_id)
    if status_filter is not None:
        stmt = stmt.where(BillingInvoice.status == status_filter)
    stmt = stmt.order_by(BillingInvoice.created_at.desc()).limit(limit).offset(offset)
    return list((await session.execute(stmt)).scalars().all())


async def get_invoice_or_404(
    session: AsyncSession, tenant_id: uuid.UUID, invoice_id: uuid.UUID
) -> BillingInvoice:
    stmt = select(BillingInvoice).where(
        BillingInvoice.tenant_id == tenant_id,
        BillingInvoice.id == invoice_id,
    )
    invoice = (await session.execute(stmt)).scalars().first()
    if invoice is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Fatura não encontrada.")
    return invoice


async def pay_invoice(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    invoice_id: uuid.UUID,
    req: InvoicePayRequest,
) -> InvoicePayResponse:
    invoice = await get_invoice_or_404(session, tenant_id, invoice_id)
    if invoice.status == InvoiceStatus.CANCELLED:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Fatura cancelada não pode ser paga.")

    if req.closer_name:
        invoice.closer_name = req.closer_name
    if req.sdr_name:
        invoice.sdr_name = req.sdr_name

    if invoice.status != InvoiceStatus.PAID:
        invoice.status = InvoiceStatus.PAID
        invoice.paid_at = _utcnow()
        session.add(
            BillingPaymentEvent(
                tenant_id=tenant_id,
                invoice_id=invoice.id,
                event_type=BillingEventType.PAID,
                amount=invoice.net_amount,
                gateway_reference=req.gateway_reference or invoice.external_charge_id,
                notes=req.notes or f"Liquidação confirmada para {invoice.invoice_number}.",
                payload={"paid_at": invoice.paid_at.isoformat()},
            )
        )

    # Evita duplicar comissões se pay_invoice for chamado de forma idempotente
    existing_comm_stmt = select(SalesCommission).where(
        SalesCommission.tenant_id == tenant_id,
        SalesCommission.invoice_id == invoice.id,
    )
    existing_comms = list((await session.execute(existing_comm_stmt)).scalars().all())
    if existing_comms:
        await session.flush()
        await session.refresh(invoice)
        return InvoicePayResponse(
            invoice=InvoiceOut.model_validate(invoice),
            generated_commissions=[SalesCommissionOut.model_validate(c) for c in existing_comms],
        )

    rules = await ensure_default_commission_rules(session, tenant_id)
    active_rules = [r for r in rules if r.is_active]

    generated: list[SalesCommission] = []
    for rule in active_rules:
        beneficiary: str | None = None
        if rule.role_type == CommissionRoleType.CLOSER:
            beneficiary = invoice.closer_name or "Executivo Closer"
        elif rule.role_type == CommissionRoleType.SDR and invoice.sdr_name:
            beneficiary = invoice.sdr_name
        elif rule.role_type == CommissionRoleType.PARTNER:
            partner = (invoice.metadata_json or {}).get("partner_name")
            if partner:
                beneficiary = str(partner)

        if not beneficiary:
            continue

        comm_val = (
            (Decimal(invoice.net_amount) * Decimal(rule.percentage)) / Decimal("100")
            + Decimal(rule.fixed_bonus)
        ).quantize(Decimal("0.01"))

        if comm_val <= Decimal("0.00"):
            continue

        comm = SalesCommission(
            tenant_id=tenant_id,
            rule_id=rule.id,
            invoice_id=invoice.id,
            contract_id=invoice.contract_id,
            deal_id=invoice.deal_id,
            beneficiary_name=beneficiary,
            role_type=rule.role_type,
            base_amount=invoice.net_amount,
            commission_amount=comm_val,
            status=CommissionStatus.PENDING,
            notes=f"Regra '{rule.name}' ({rule.percentage}%) sobre fatura {invoice.invoice_number}.",
        )
        session.add(comm)
        generated.append(comm)

    await session.flush()
    await session.refresh(invoice)
    for c in generated:
        await session.refresh(c)

    return InvoicePayResponse(
        invoice=InvoiceOut.model_validate(invoice),
        generated_commissions=[SalesCommissionOut.model_validate(c) for c in generated],
    )


async def update_invoice_status(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    invoice_id: uuid.UUID,
    new_status: InvoiceStatus,
    event_type: BillingEventType,
    note: str,
) -> BillingInvoice:
    invoice = await get_invoice_or_404(session, tenant_id, invoice_id)
    invoice.status = new_status
    session.add(
        BillingPaymentEvent(
            tenant_id=tenant_id,
            invoice_id=invoice.id,
            event_type=event_type,
            amount=invoice.net_amount,
            gateway_reference=invoice.external_charge_id,
            notes=note,
        )
    )
    await session.flush()
    await session.refresh(invoice)
    return invoice


async def create_commission_rule(
    session: AsyncSession, tenant_id: uuid.UUID, data: CommissionRuleCreate
) -> CommissionRule:
    rule = CommissionRule(
        tenant_id=tenant_id,
        name=data.name,
        role_type=data.role_type,
        percentage=Decimal(data.percentage).quantize(Decimal("0.01")),
        fixed_bonus=Decimal(data.fixed_bonus).quantize(Decimal("0.01")),
        is_active=data.is_active,
    )
    session.add(rule)
    await session.flush()
    await session.refresh(rule)
    return rule


async def list_commissions(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    status_filter: CommissionStatus | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[SalesCommission]:
    stmt = select(SalesCommission).where(SalesCommission.tenant_id == tenant_id)
    if status_filter is not None:
        stmt = stmt.where(SalesCommission.status == status_filter)
    stmt = stmt.order_by(SalesCommission.created_at.desc()).limit(limit).offset(offset)
    return list((await session.execute(stmt)).scalars().all())


async def update_commission_status(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    commission_id: uuid.UUID,
    req: CommissionStatusUpdateRequest,
) -> SalesCommission:
    stmt = select(SalesCommission).where(
        SalesCommission.tenant_id == tenant_id,
        SalesCommission.id == commission_id,
    )
    comm = (await session.execute(stmt)).scalars().first()
    if comm is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Comissão não encontrada.")

    comm.status = req.status
    if req.notes:
        comm.notes = req.notes
    if req.status == CommissionStatus.PAID and comm.paid_at is None:
        comm.paid_at = _utcnow()

    await session.flush()
    await session.refresh(comm)
    return comm


async def get_financial_summary(session: AsyncSession, tenant_id: uuid.UUID) -> FinancialSummaryOut:
    await ensure_default_commission_rules(session, tenant_id)

    invoices = list(
        (await session.execute(select(BillingInvoice).where(BillingInvoice.tenant_id == tenant_id))).scalars().all()
    )
    commissions = list(
        (await session.execute(select(SalesCommission).where(SalesCommission.tenant_id == tenant_id))).scalars().all()
    )

    valid_invoices = [i for i in invoices if i.status != InvoiceStatus.CANCELLED]
    paid_invoices = [i for i in invoices if i.status == InvoiceStatus.PAID]
    pending_invoices = [i for i in invoices if i.status in (InvoiceStatus.PENDING, InvoiceStatus.DRAFT)]
    overdue_invoices = [i for i in invoices if i.status == InvoiceStatus.OVERDUE]

    total_invoiced = sum((Decimal(i.net_amount) for i in valid_invoices), Decimal("0.00"))
    total_paid = sum((Decimal(i.net_amount) for i in paid_invoices), Decimal("0.00"))
    total_pending = sum((Decimal(i.net_amount) for i in pending_invoices), Decimal("0.00"))
    total_overdue = sum((Decimal(i.net_amount) for i in overdue_invoices), Decimal("0.00"))

    comm_pending = sum(
        (Decimal(c.commission_amount) for c in commissions if c.status == CommissionStatus.PENDING),
        Decimal("0.00"),
    )
    comm_approved = sum(
        (Decimal(c.commission_amount) for c in commissions if c.status == CommissionStatus.APPROVED),
        Decimal("0.00"),
    )
    comm_paid = sum(
        (Decimal(c.commission_amount) for c in commissions if c.status == CommissionStatus.PAID),
        Decimal("0.00"),
    )

    total_active_commissions = comm_pending + comm_approved + comm_paid
    net_after_comm = (total_paid - total_active_commissions).quantize(Decimal("0.01"))

    return FinancialSummaryOut(
        invoices_total_count=len(valid_invoices),
        total_invoiced_amount=total_invoiced,
        total_paid_amount=total_paid,
        total_pending_amount=total_pending,
        total_overdue_amount=total_overdue,
        paid_invoices_count=len(paid_invoices),
        pending_invoices_count=len(pending_invoices),
        overdue_invoices_count=len(overdue_invoices),
        commissions_pending_amount=comm_pending,
        commissions_approved_amount=comm_approved,
        commissions_paid_amount=comm_paid,
        net_revenue_after_commissions=net_after_comm,
    )
