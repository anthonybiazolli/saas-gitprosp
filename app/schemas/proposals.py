from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.models.proposals import BillingType, ProposalStatus


class CatalogItemCreate(BaseModel):
    sku: str = Field(..., min_length=2, max_length=60, pattern=r"^[A-Za-z0-9._-]+$")
    name: str = Field(..., min_length=2, max_length=180)
    category: str = Field(default="geral", max_length=80)
    description: str | None = None
    billing_type: BillingType = BillingType.RECURRING
    unit_price: Decimal = Field(..., ge=Decimal("0.00"))
    unit_label: str = Field(default="unidade", max_length=40)
    default_contract_months: int = Field(default=12, ge=1, le=120)
    is_active: bool = True
    metadata_json: dict[str, Any] = Field(default_factory=dict)


class CatalogItemUpdate(BaseModel):
    name: str | None = Field(None, min_length=2, max_length=180)
    category: str | None = Field(None, max_length=80)
    description: str | None = None
    billing_type: BillingType | None = None
    unit_price: Decimal | None = Field(None, ge=Decimal("0.00"))
    unit_label: str | None = Field(None, max_length=40)
    default_contract_months: int | None = Field(None, ge=1, le=120)
    is_active: bool | None = None
    metadata_json: dict[str, Any] | None = None


class CatalogItemResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    tenant_id: uuid.UUID
    sku: str
    name: str
    category: str
    description: str | None
    billing_type: BillingType
    unit_price: Decimal
    unit_label: str
    default_contract_months: int
    is_active: bool
    metadata_json: dict[str, Any]
    created_at: datetime
    updated_at: datetime


class ProposalItemInput(BaseModel):
    catalog_item_id: uuid.UUID | None = Field(
        None, description="ID opcional de item do catálogo para herdar preço, nome e modalidade"
    )
    name: str | None = Field(None, max_length=180)
    description: str | None = None
    billing_type: BillingType | None = None
    quantity: Decimal = Field(default=Decimal("1.00"), gt=Decimal("0.00"))
    unit_price: Decimal | None = Field(None, ge=Decimal("0.00"))


class ProposalItemResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    catalog_item_id: uuid.UUID | None
    display_order: int
    name: str
    description: str | None
    billing_type: BillingType
    quantity: Decimal
    unit_price: Decimal
    line_total: Decimal


class ProposalCreate(BaseModel):
    title: str = Field(..., min_length=3, max_length=220)
    company_id: uuid.UUID | None = None
    contact_id: uuid.UUID | None = None
    deal_id: uuid.UUID | None = None
    conversation_id: uuid.UUID | None = None
    contract_months: int = Field(default=12, ge=1, le=120)
    discount_percent: Decimal = Field(default=Decimal("0.00"), ge=Decimal("0.00"), le=Decimal("100.00"))
    valid_days: int = Field(default=7, ge=1, le=90)
    executive_summary: str | None = None
    terms_and_conditions: str | None = None
    auto_sync_deal: bool = Field(
        default=True,
        description="Se true, atualiza ou cria o Deal no CRM e move para a etapa de Proposta Enviada",
    )
    items: list[ProposalItemInput] = Field(..., min_length=1)
    metadata_json: dict[str, Any] = Field(default_factory=dict)


class ProposalResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    tenant_id: uuid.UUID
    proposal_code: str
    public_token: str
    title: str
    company_id: uuid.UUID | None
    contact_id: uuid.UUID | None
    deal_id: uuid.UUID | None
    conversation_id: uuid.UUID | None
    status: ProposalStatus
    contract_months: int
    discount_percent: Decimal
    subtotal_mrr: Decimal
    subtotal_one_time: Decimal
    discount_amount: Decimal
    total_mrr: Decimal
    total_one_time: Decimal
    total_contract_value: Decimal
    valid_until: datetime
    executive_summary: str | None
    terms_and_conditions: str | None
    sent_at: datetime | None
    viewed_at: datetime | None
    accepted_at: datetime | None
    accepted_by_name: str | None
    accepted_by_document: str | None
    accepted_by_email: str | None
    created_at: datetime
    updated_at: datetime


class ProposalDetailResponse(ProposalResponse):
    items: list[ProposalItemResponse] = Field(default_factory=list)
    public_url: str | None = None
    pdf_download_url: str | None = None


class ProposalSendWhatsAppRequest(BaseModel):
    recipient_number: str | None = Field(
        None,
        description="Número WhatsApp destino (ex: 5541998887766). Se omitido, usa o WhatsApp do Contato ou Empresa vinculados.",
    )
    instance_name: str | None = Field(
        None,
        description="Instância Evolution API específica (opcional; usa a principal ativa do tenant se omitido).",
    )
    custom_message: str | None = Field(
        None,
        max_length=2000,
        description="Mensagem personalizada que acompanha o PDF e o link de aceite.",
    )
    attach_pdf: bool = Field(
        default=True,
        description="Se true, anexa o documento PDF gerado diretamente na conversa do WhatsApp.",
    )


class PublicProposalViewResponse(BaseModel):
    proposal_code: str
    title: str
    status: ProposalStatus
    issuer_name: str
    client_company_name: str | None
    client_cnpj: str | None
    client_contact_name: str | None
    contract_months: int
    discount_percent: Decimal
    subtotal_mrr: Decimal
    subtotal_one_time: Decimal
    discount_amount: Decimal
    total_mrr: Decimal
    total_one_time: Decimal
    total_contract_value: Decimal
    valid_until: datetime
    executive_summary: str | None
    terms_and_conditions: str | None
    accepted_at: datetime | None
    accepted_by_name: str | None
    items: list[ProposalItemResponse]


class PublicProposalAcceptRequest(BaseModel):
    signer_name: str = Field(..., min_length=3, max_length=160)
    signer_document: str = Field(..., min_length=5, max_length=40, description="CPF ou CNPJ do signatário")
    signer_email: str | None = Field(None, max_length=160)


class PublicProposalAcceptResponse(BaseModel):
    status: str
    proposal_code: str
    accepted_at: datetime
    accepted_by_name: str
    deal_id: uuid.UUID | None
    deal_status: str | None
    message: str
