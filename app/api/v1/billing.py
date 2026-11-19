"""Rotas FastAPI do Módulo de Faturamento, Cobranças e Comissões."""

from __future__ import annotations

import inspect
import uuid
from typing import Any

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import TenantContext, get_tenant_context, write_audit_log
from app.db.session import get_db_session
from app.models.billing_commissions import BillingEventType, CommissionStatus, InvoiceStatus
from app.schemas.billing_commissions import (
    CommissionRuleCreate,
    CommissionRuleOut,
    CommissionStatusUpdateRequest,
    ContractInvoiceGenerateRequest,
    FinancialSummaryOut,
    InvoiceCreate,
    InvoiceOut,
    InvoicePayRequest,
    InvoicePayResponse,
    SalesCommissionOut,
)
from app.services import billing_service

router = APIRouter()


async def _record_audit(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    action: str,
    entity_type: str,
    entity_id: str,
    details: dict[str, Any] | None = None,
) -> None:
    try:
        sig = inspect.signature(write_audit_log)
        kwargs: dict[str, Any] = {}
        for param_name in sig.parameters:
            if param_name in ("session", "db"):
                kwargs[param_name] = session
            elif param_name == "tenant_id":
                kwargs[param_name] = tenant_id
            elif param_name == "action":
                kwargs[param_name] = action
            elif param_name in ("entity_type", "resource_type", "entity"):
                kwargs[param_name] = entity_type
            elif param_name in ("entity_id", "resource_id"):
                kwargs[param_name] = entity_id
            elif param_name in ("details", "metadata", "payload"):
                kwargs[param_name] = details or {}
        res = write_audit_log(**kwargs)
        if inspect.isawaitable(res):
            await res
    except Exception:
        pass


@router.post("/invoices", response_model=InvoiceOut, status_code=status.HTTP_201_CREATED)
async def create_invoice(
    payload: InvoiceCreate,
    ctx: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(get_db_session),
) -> InvoiceOut:
    inv = await billing_service.create_invoice(session, ctx.tenant_id, payload)
    await _record_audit(session, ctx.tenant_id, "billing.invoice.created", "billing_invoice", str(inv.id))
    await session.commit()
    return InvoiceOut.model_validate(inv)


@router.post(
    "/contracts/{contract_id}/generate-invoice",
    response_model=InvoiceOut,
    status_code=status.HTTP_201_CREATED,
)
async def generate_contract_invoice(
    contract_id: uuid.UUID,
    payload: ContractInvoiceGenerateRequest,
    ctx: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(get_db_session),
) -> InvoiceOut:
    inv = await billing_service.generate_invoice_from_contract(session, ctx.tenant_id, contract_id, payload)
    await _record_audit(
        session,
        ctx.tenant_id,
        "billing.invoice.generated_from_contract",
        "billing_invoice",
        str(inv.id),
        {"contract_id": str(contract_id)},
    )
    await session.commit()
    return InvoiceOut.model_validate(inv)


@router.get("/invoices", response_model=list[InvoiceOut])
async def list_invoices(
    status_filter: InvoiceStatus | None = Query(default=None, alias="status"),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    ctx: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(get_db_session),
) -> list[InvoiceOut]:
    items = await billing_service.list_invoices(
        session, ctx.tenant_id, status_filter=status_filter, limit=limit, offset=offset
    )
    return [InvoiceOut.model_validate(i) for i in items]


@router.get("/invoices/{invoice_id}", response_model=InvoiceOut)
async def get_invoice(
    invoice_id: uuid.UUID,
    ctx: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(get_db_session),
) -> InvoiceOut:
    inv = await billing_service.get_invoice_or_404(session, ctx.tenant_id, invoice_id)
    return InvoiceOut.model_validate(inv)


@router.post("/invoices/{invoice_id}/pay", response_model=InvoicePayResponse)
async def pay_invoice(
    invoice_id: uuid.UUID,
    payload: InvoicePayRequest,
    ctx: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(get_db_session),
) -> InvoicePayResponse:
    res = await billing_service.pay_invoice(session, ctx.tenant_id, invoice_id, payload)
    await _record_audit(session, ctx.tenant_id, "billing.invoice.paid", "billing_invoice", str(invoice_id))
    await session.commit()
    return res


@router.post("/invoices/{invoice_id}/mark-overdue", response_model=InvoiceOut)
async def mark_invoice_overdue(
    invoice_id: uuid.UUID,
    ctx: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(get_db_session),
) -> InvoiceOut:
    inv = await billing_service.update_invoice_status(
        session,
        ctx.tenant_id,
        invoice_id,
        InvoiceStatus.OVERDUE,
        BillingEventType.OVERDUE_MARKED,
        "Fatura marcada como vencida / inadimplente.",
    )
    await session.commit()
    return InvoiceOut.model_validate(inv)


@router.post("/invoices/{invoice_id}/cancel", response_model=InvoiceOut)
async def cancel_invoice(
    invoice_id: uuid.UUID,
    ctx: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(get_db_session),
) -> InvoiceOut:
    inv = await billing_service.update_invoice_status(
        session,
        ctx.tenant_id,
        invoice_id,
        InvoiceStatus.CANCELLED,
        BillingEventType.CANCELLED,
        "Fatura cancelada pelo operador.",
    )
    await session.commit()
    return InvoiceOut.model_validate(inv)


@router.get("/commission-rules", response_model=list[CommissionRuleOut])
async def list_commission_rules(
    ctx: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(get_db_session),
) -> list[CommissionRuleOut]:
    rules = await billing_service.ensure_default_commission_rules(session, ctx.tenant_id)
    await session.commit()
    return [CommissionRuleOut.model_validate(r) for r in rules]


@router.post("/commission-rules", response_model=CommissionRuleOut, status_code=status.HTTP_201_CREATED)
async def create_commission_rule(
    payload: CommissionRuleCreate,
    ctx: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(get_db_session),
) -> CommissionRuleOut:
    rule = await billing_service.create_commission_rule(session, ctx.tenant_id, payload)
    await session.commit()
    return CommissionRuleOut.model_validate(rule)


@router.get("/commissions", response_model=list[SalesCommissionOut])
async def list_commissions(
    status_filter: CommissionStatus | None = Query(default=None, alias="status"),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    ctx: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(get_db_session),
) -> list[SalesCommissionOut]:
    items = await billing_service.list_commissions(
        session, ctx.tenant_id, status_filter=status_filter, limit=limit, offset=offset
    )
    return [SalesCommissionOut.model_validate(c) for c in items]


@router.post("/commissions/{commission_id}/status", response_model=SalesCommissionOut)
async def update_commission_status(
    commission_id: uuid.UUID,
    payload: CommissionStatusUpdateRequest,
    ctx: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(get_db_session),
) -> SalesCommissionOut:
    comm = await billing_service.update_commission_status(session, ctx.tenant_id, commission_id, payload)
    await session.commit()
    return SalesCommissionOut.model_validate(comm)


@router.get("/summary", response_model=FinancialSummaryOut)
async def get_financial_summary(
    ctx: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(get_db_session),
) -> FinancialSummaryOut:
    summary = await billing_service.get_financial_summary(session, ctx.tenant_id)
    await session.commit()
    return summary
