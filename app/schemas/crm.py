from __future__ import annotations

import re
import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.core import CustomFieldEntity, CustomFieldType
from app.models.crm import (
    BillingFrequency,
    BuyingRole,
    CompanySize,
    ContactSeniority,
    DealStatus,
    LifecycleStage,
    TaxRegime,
)


def _empty_to_none(v: Any) -> Any:
    if isinstance(v, str) and v.strip() == "":
        return None
    return v


# ==============================================================================
# 1. CUSTOM FIELD DEFINITIONS
# ==============================================================================


class CustomFieldOptionSchema(BaseModel):
    value: str = Field(..., min_length=1, max_length=100)
    label: str = Field(..., min_length=1, max_length=150)
    color: str | None = Field(None, pattern=r"^#[0-9A-Fa-f]{6}$")


class CustomFieldDefinitionCreate(BaseModel):
    entity_type: CustomFieldEntity
    field_key: str = Field(..., min_length=2, max_length=100, pattern=r"^[a-z][a-z0-9_]*$")
    label: str = Field(..., min_length=2, max_length=150)
    description: str | None = None
    field_type: CustomFieldType
    options: list[CustomFieldOptionSchema] = Field(default_factory=list)
    default_value: Any | None = None
    validation_rules: dict[str, Any] = Field(default_factory=dict)
    group_name: str = Field(default="Geral", max_length=100)
    display_order: int = 0
    is_required: bool = False
    is_searchable: bool = True
    is_visible_in_kanban: bool = False
    is_active: bool = True


class CustomFieldDefinitionUpdate(BaseModel):
    label: str | None = Field(None, min_length=2, max_length=150)
    description: str | None = None
    options: list[CustomFieldOptionSchema] | None = None
    default_value: Any | None = None
    validation_rules: dict[str, Any] | None = None
    group_name: str | None = Field(None, max_length=100)
    display_order: int | None = None
    is_required: bool | None = None
    is_searchable: bool | None = None
    is_visible_in_kanban: bool | None = None
    is_active: bool | None = None


class CustomFieldDefinitionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    tenant_id: uuid.UUID
    entity_type: CustomFieldEntity
    field_key: str
    label: str
    description: str | None
    field_type: CustomFieldType
    options: list[dict[str, Any]]
    default_value: Any | None
    validation_rules: dict[str, Any]
    group_name: str
    display_order: int
    is_required: bool
    is_searchable: bool
    is_visible_in_kanban: bool
    is_active: bool
    created_at: datetime
    updated_at: datetime


# ==============================================================================
# 2. COMPANIES (EMPRESAS B2B/B2C)
# ==============================================================================


class CompanyCreate(BaseModel):
    legal_name: str = Field(..., min_length=2, max_length=255)
    trade_name: str | None = Field(None, max_length=255)
    cnpj: str | None = Field(None, max_length=18)
    is_headquarters: bool = True
    parent_company_id: uuid.UUID | None = None

    state_registration: str | None = Field(None, max_length=30)
    is_ie_exempt: bool = False
    municipal_registration: str | None = Field(None, max_length=30)
    suframa_code: str | None = Field(None, max_length=30)

    tax_regime: TaxRegime = TaxRegime.UNKNOWN
    company_size: CompanySize = CompanySize.SMALL
    legal_nature_code: str | None = Field(None, max_length=10)
    legal_nature_description: str | None = Field(None, max_length=200)
    share_capital: Decimal | None = Field(None, ge=0)
    founded_at: date | None = None
    cadastral_status: str = Field(default="ATIVA", max_length=40)
    cadastral_status_date: date | None = None

    primary_cnae_code: str | None = Field(None, max_length=15)
    primary_cnae_description: str | None = Field(None, max_length=300)
    secondary_cnaes: list[dict[str, str]] = Field(default_factory=list)
    partners_qsa: list[dict[str, Any]] = Field(default_factory=list)

    postal_code: str | None = Field(None, max_length=12)
    city: str | None = Field(None, max_length=120)
    city_ibge_code: str | None = Field(None, max_length=10)
    state_uf: str | None = Field(None, min_length=2, max_length=2)
    country_code: str = Field(default="BRA", min_length=3, max_length=3)

    fiscal_address: dict[str, Any] = Field(default_factory=dict)
    billing_address: dict[str, Any] = Field(default_factory=dict)
    shipping_address: dict[str, Any] = Field(default_factory=dict)

    primary_email: str | None = Field(None, max_length=255)
    financial_email: str | None = Field(None, max_length=255)
    primary_phone: str | None = Field(None, max_length=30)
    whatsapp_number: str | None = Field(None, max_length=30)
    additional_phones: list[dict[str, str]] = Field(default_factory=list)
    additional_emails: list[dict[str, str]] = Field(default_factory=list)

    website: str | None = Field(None, max_length=300)
    domain: str | None = Field(None, max_length=180)
    linkedin_url: str | None = Field(None, max_length=300)
    instagram_handle: str | None = Field(None, max_length=100)

    bank_details: dict[str, Any] = Field(default_factory=dict)
    credit_limit: Decimal = Field(default=Decimal("0.00"), ge=0)
    payment_terms_days: int = Field(default=30, ge=0)

    lifecycle_stage: LifecycleStage = LifecycleStage.PROSPECT
    lead_source: str | None = Field(None, max_length=80)
    utm_data: dict[str, Any] = Field(default_factory=dict)

    icp_score: int = Field(default=0, ge=0, le=100)
    icp_tier: str | None = Field(None, max_length=10)
    employee_count_min: int | None = Field(None, ge=0)
    employee_count_max: int | None = Field(None, ge=0)
    estimated_annual_revenue: Decimal | None = Field(None, ge=0)

    tech_stack: list[str] = Field(default_factory=list)
    current_competitors: list[str] = Field(default_factory=list)
    identified_pain_points: list[str] = Field(default_factory=list)
    enrichment_raw_data: dict[str, Any] = Field(default_factory=dict)

    owner_id: uuid.UUID | None = None
    sdr_id: uuid.UUID | None = None
    cs_owner_id: uuid.UUID | None = None
    prospecting_campaign_id: uuid.UUID | None = None

    tags: list[str] = Field(default_factory=list)
    custom_fields: dict[str, Any] = Field(default_factory=dict)
    notes: str | None = None
    erp_external_id: str | None = Field(None, max_length=120)

    @field_validator("cnpj", "primary_email", "financial_email", "domain", "state_uf", mode="before")
    @classmethod
    def normalize_blank_strings(cls, v: Any) -> Any:
        v = _empty_to_none(v)
        if isinstance(v, str) and len(v) == 2:
            return v.upper()
        return v


class CompanyUpdate(BaseModel):
    legal_name: str | None = Field(None, min_length=2, max_length=255)
    trade_name: str | None = Field(None, max_length=255)
    cnpj: str | None = Field(None, max_length=18)
    is_headquarters: bool | None = None
    parent_company_id: uuid.UUID | None = None

    state_registration: str | None = Field(None, max_length=30)
    is_ie_exempt: bool | None = None
    municipal_registration: str | None = Field(None, max_length=30)
    suframa_code: str | None = Field(None, max_length=30)

    tax_regime: TaxRegime | None = None
    company_size: CompanySize | None = None
    legal_nature_code: str | None = None
    legal_nature_description: str | None = None
    share_capital: Decimal | None = Field(None, ge=0)
    founded_at: date | None = None
    cadastral_status: str | None = None

    primary_cnae_code: str | None = None
    primary_cnae_description: str | None = None
    secondary_cnaes: list[dict[str, str]] | None = None
    partners_qsa: list[dict[str, Any]] | None = None

    postal_code: str | None = None
    city: str | None = None
    city_ibge_code: str | None = None
    state_uf: str | None = Field(None, min_length=2, max_length=2)
    country_code: str | None = None

    fiscal_address: dict[str, Any] | None = None
    billing_address: dict[str, Any] | None = None
    shipping_address: dict[str, Any] | None = None

    primary_email: str | None = None
    financial_email: str | None = None
    primary_phone: str | None = None
    whatsapp_number: str | None = None
    additional_phones: list[dict[str, str]] | None = None
    additional_emails: list[dict[str, str]] | None = None

    website: str | None = None
    domain: str | None = None
    linkedin_url: str | None = None
    instagram_handle: str | None = None

    bank_details: dict[str, Any] | None = None
    credit_limit: Decimal | None = Field(None, ge=0)
    payment_terms_days: int | None = Field(None, ge=0)

    lifecycle_stage: LifecycleStage | None = None
    lead_source: str | None = None
    utm_data: dict[str, Any] | None = None

    icp_score: int | None = Field(None, ge=0, le=100)
    icp_tier: str | None = None
    employee_count_min: int | None = None
    employee_count_max: int | None = None
    estimated_annual_revenue: Decimal | None = None

    tech_stack: list[str] | None = None
    current_competitors: list[str] | None = None
    identified_pain_points: list[str] | None = None

    owner_id: uuid.UUID | None = None
    sdr_id: uuid.UUID | None = None
    cs_owner_id: uuid.UUID | None = None

    tags: list[str] | None = None
    custom_fields: dict[str, Any] | None = None
    notes: str | None = None
    erp_external_id: str | None = None

    @field_validator("cnpj", "primary_email", "financial_email", "domain", "state_uf", mode="before")
    @classmethod
    def normalize_blank_strings(cls, v: Any) -> Any:
        v = _empty_to_none(v)
        if isinstance(v, str) and len(v) == 2:
            return v.upper()
        return v


class CompanyResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    tenant_id: uuid.UUID
    legal_name: str
    trade_name: str | None
    cnpj: str | None
    is_headquarters: bool
    state_registration: str | None
    is_ie_exempt: bool
    municipal_registration: str | None
    tax_regime: TaxRegime
    company_size: CompanySize
    share_capital: Decimal | None
    founded_at: date | None
    cadastral_status: str
    primary_cnae_code: str | None
    primary_cnae_description: str | None
    secondary_cnaes: list[dict[str, str]]
    partners_qsa: list[dict[str, Any]]
    postal_code: str | None
    city: str | None
    city_ibge_code: str | None
    state_uf: str | None
    country_code: str
    fiscal_address: dict[str, Any]
    billing_address: dict[str, Any]
    shipping_address: dict[str, Any]
    primary_email: str | None
    financial_email: str | None
    primary_phone: str | None
    whatsapp_number: str | None
    website: str | None
    domain: str | None
    linkedin_url: str | None
    instagram_handle: str | None
    credit_limit: Decimal
    payment_terms_days: int
    lifecycle_stage: LifecycleStage
    lead_source: str | None
    icp_score: int
    icp_tier: str | None
    estimated_annual_revenue: Decimal | None
    tech_stack: list[str]
    identified_pain_points: list[str]
    owner_id: uuid.UUID | None
    sdr_id: uuid.UUID | None
    cs_owner_id: uuid.UUID | None
    tags: list[str]
    custom_fields: dict[str, Any]
    notes: str | None
    erp_external_id: str | None
    created_at: datetime
    updated_at: datetime


# ==============================================================================
# 3. CONTACTS & COMPANY LINKS
# ==============================================================================


class CompanyLinkInput(BaseModel):
    company_id: uuid.UUID
    role_description: str | None = Field(None, max_length=120)
    is_primary_decision_maker: bool = False
    is_billing_contact: bool = False
    is_legal_signatory: bool = False


class CompanyLinkResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    company_id: uuid.UUID
    contact_id: uuid.UUID
    role_description: str | None
    is_primary_decision_maker: bool
    is_billing_contact: bool
    is_legal_signatory: bool
    is_active: bool


class ContactCreate(BaseModel):
    full_name: str = Field(..., min_length=2, max_length=200)
    preferred_name: str | None = Field(None, max_length=100)
    cpf: str | None = Field(None, max_length=14)
    birth_date: date | None = None
    avatar_url: str | None = Field(None, max_length=500)

    job_title: str | None = Field(None, max_length=150)
    department: str | None = Field(None, max_length=100)
    seniority: ContactSeniority | None = None
    buying_role: BuyingRole | None = None

    email: str | None = Field(None, max_length=255)
    secondary_email: str | None = Field(None, max_length=255)
    phone: str | None = Field(None, max_length=30)
    whatsapp_e164: str | None = Field(None, max_length=25)
    telegram_id: str | None = Field(None, max_length=80)
    linkedin_url: str | None = Field(None, max_length=300)
    instagram_handle: str | None = Field(None, max_length=100)

    opt_in_whatsapp: bool = True
    opt_in_email: bool = True
    opt_in_phone: bool = True
    opt_in_sms: bool = True
    lgpd_consent_at: datetime | None = None
    lgpd_legal_basis: str | None = Field(None, max_length=80)

    preferred_channel: str = Field(default="whatsapp", max_length=30)
    preferred_contact_window: dict[str, Any] = Field(default_factory=dict)
    timezone: str = Field(default="America/Sao_Paulo", max_length=60)
    language: str = Field(default="pt-BR", max_length=10)

    lead_score: int = Field(default=0, ge=0, le=100)
    owner_id: uuid.UUID | None = None
    tags: list[str] = Field(default_factory=list)
    custom_fields: dict[str, Any] = Field(default_factory=dict)
    company_links: list[CompanyLinkInput] = Field(default_factory=list)

    @field_validator("cpf", "email", "secondary_email", "whatsapp_e164", mode="before")
    @classmethod
    def normalize_blank(cls, v: Any) -> Any:
        return _empty_to_none(v)


class ContactUpdate(BaseModel):
    full_name: str | None = Field(None, min_length=2, max_length=200)
    preferred_name: str | None = None
    cpf: str | None = None
    birth_date: date | None = None
    avatar_url: str | None = None

    job_title: str | None = None
    department: str | None = None
    seniority: ContactSeniority | None = None
    buying_role: BuyingRole | None = None

    email: str | None = None
    secondary_email: str | None = None
    phone: str | None = None
    whatsapp_e164: str | None = None
    telegram_id: str | None = None
    linkedin_url: str | None = None
    instagram_handle: str | None = None

    opt_in_whatsapp: bool | None = None
    opt_in_email: bool | None = None
    opt_in_phone: bool | None = None
    opt_in_sms: bool | None = None
    lgpd_consent_at: datetime | None = None
    lgpd_legal_basis: str | None = None

    preferred_channel: str | None = None
    preferred_contact_window: dict[str, Any] | None = None
    timezone: str | None = None
    language: str | None = None

    lead_score: int | None = Field(None, ge=0, le=100)
    owner_id: uuid.UUID | None = None
    tags: list[str] | None = None
    custom_fields: dict[str, Any] | None = None

    @field_validator("cpf", "email", "secondary_email", "whatsapp_e164", mode="before")
    @classmethod
    def normalize_blank(cls, v: Any) -> Any:
        return _empty_to_none(v)


class ContactResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    tenant_id: uuid.UUID
    full_name: str
    preferred_name: str | None
    cpf: str | None
    birth_date: date | None
    job_title: str | None
    department: str | None
    seniority: ContactSeniority | None
    buying_role: BuyingRole | None
    email: str | None
    secondary_email: str | None
    phone: str | None
    whatsapp_e164: str | None
    linkedin_url: str | None
    instagram_handle: str | None
    opt_in_whatsapp: bool
    opt_in_email: bool
    opt_in_phone: bool
    opt_in_sms: bool
    lgpd_legal_basis: str | None
    preferred_channel: str
    lead_score: int
    owner_id: uuid.UUID | None
    tags: list[str]
    custom_fields: dict[str, Any]
    companies_assoc: list[CompanyLinkResponse] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


# ==============================================================================
# 4. PIPELINES & STAGES
# ==============================================================================


class PipelineStageCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    display_order: int = 0
    win_probability: int = Field(default=10, ge=0, le=100)
    rotting_days_limit: int | None = Field(None, ge=1)
    color_hex: str = Field(default="#3B82F6", pattern=r"^#[0-9A-Fa-f]{6}$")
    required_deal_fields: list[str] = Field(default_factory=list)
    is_won_stage: bool = False
    is_lost_stage: bool = False


class PipelineStageUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=120)
    display_order: int | None = None
    win_probability: int | None = Field(None, ge=0, le=100)
    rotting_days_limit: int | None = Field(None, ge=1)
    color_hex: str | None = Field(None, pattern=r"^#[0-9A-Fa-f]{6}$")
    required_deal_fields: list[str] | None = None
    is_won_stage: bool | None = None
    is_lost_stage: bool | None = None


class PipelineStageResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    pipeline_id: uuid.UUID
    name: str
    display_order: int
    win_probability: int
    rotting_days_limit: int | None
    color_hex: str
    required_deal_fields: list[str]
    is_won_stage: bool
    is_lost_stage: bool


class PipelineCreate(BaseModel):
    name: str = Field(..., min_length=2, max_length=120)
    description: str | None = None
    pipeline_type: str = Field(default="sales", max_length=40)
    is_default: bool = False
    is_active: bool = True
    stages: list[PipelineStageCreate] = Field(default_factory=list)


class PipelineUpdate(BaseModel):
    name: str | None = Field(None, min_length=2, max_length=120)
    description: str | None = None
    pipeline_type: str | None = None
    is_default: bool | None = None
    is_active: bool | None = None


class PipelineResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    tenant_id: uuid.UUID
    name: str
    description: str | None
    pipeline_type: str
    is_default: bool
    is_active: bool
    stages: list[PipelineStageResponse] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


# ==============================================================================
# 5. DEALS, ITEMS & KANBAN
# ==============================================================================


class DealItemInput(BaseModel):
    product_id: uuid.UUID
    quantity: Decimal = Field(default=Decimal("1.000"), gt=0)
    unit_price: Decimal = Field(..., ge=0)
    discount_percent: Decimal = Field(default=Decimal("0.00"), ge=0, le=100)
    billing_frequency: BillingFrequency = BillingFrequency.MONTHLY


class DealItemResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    product_id: uuid.UUID
    quantity: Decimal
    unit_price: Decimal
    discount_percent: Decimal
    subtotal: Decimal
    billing_frequency: BillingFrequency


class DealStageHistoryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    from_stage_id: uuid.UUID | None
    to_stage_id: uuid.UUID
    changed_by_user_id: uuid.UUID | None
    changed_by_ai: bool
    duration_in_previous_stage_seconds: int | None
    entered_at: datetime


class DealCreate(BaseModel):
    title: str = Field(..., min_length=2, max_length=220)
    company_id: uuid.UUID
    primary_contact_id: uuid.UUID | None = None
    pipeline_id: uuid.UUID | None = None
    stage_id: uuid.UUID | None = None

    owner_id: uuid.UUID | None = None
    sdr_id: uuid.UUID | None = None

    mrr_value: Decimal = Field(default=Decimal("0.00"), ge=0)
    one_time_value: Decimal = Field(default=Decimal("0.00"), ge=0)
    total_contract_value: Decimal | None = Field(None, ge=0)
    discount_amount: Decimal = Field(default=Decimal("0.00"), ge=0)
    contract_months: int = Field(default=12, ge=1)

    expected_close_date: date | None = None
    qualification_data: dict[str, Any] = Field(
        default_factory=lambda: {
            "budget": None,
            "authority": None,
            "need": None,
            "timeline": None,
            "decision_criteria": None,
        }
    )
    tags: list[str] = Field(default_factory=list)
    custom_fields: dict[str, Any] = Field(default_factory=dict)
    items: list[DealItemInput] = Field(default_factory=list)


class DealUpdate(BaseModel):
    title: str | None = Field(None, min_length=2, max_length=220)
    primary_contact_id: uuid.UUID | None = None
    owner_id: uuid.UUID | None = None
    sdr_id: uuid.UUID | None = None

    mrr_value: Decimal | None = Field(None, ge=0)
    one_time_value: Decimal | None = Field(None, ge=0)
    total_contract_value: Decimal | None = Field(None, ge=0)
    discount_amount: Decimal | None = Field(None, ge=0)
    contract_months: int | None = Field(None, ge=1)

    win_probability: int | None = Field(None, ge=0, le=100)
    expected_close_date: date | None = None
    qualification_data: dict[str, Any] | None = None
    tags: list[str] | None = None
    custom_fields: dict[str, Any] | None = None


class DealMoveStageRequest(BaseModel):
    target_stage_id: uuid.UUID
    changed_by_ai: bool = False
    loss_reason: str | None = Field(None, max_length=150)
    loss_notes: str | None = None
    loss_competitor: str | None = Field(None, max_length=120)


class DealResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    tenant_id: uuid.UUID
    title: str
    company_id: uuid.UUID
    primary_contact_id: uuid.UUID | None
    pipeline_id: uuid.UUID
    stage_id: uuid.UUID
    owner_id: uuid.UUID | None
    sdr_id: uuid.UUID | None
    status: DealStatus
    mrr_value: Decimal
    one_time_value: Decimal
    total_contract_value: Decimal
    discount_amount: Decimal
    contract_months: int
    win_probability: int
    expected_close_date: date | None
    closed_at: datetime | None
    stage_entered_at: datetime
    loss_reason: str | None
    loss_notes: str | None
    loss_competitor: str | None
    qualification_data: dict[str, Any]
    tags: list[str]
    custom_fields: dict[str, Any]
    items: list[DealItemResponse] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class PaginatedResponse(BaseModel):
    items: list[Any]
    total: int
    page: int
    page_size: int
    pages: int
