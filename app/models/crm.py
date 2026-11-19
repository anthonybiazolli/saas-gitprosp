from __future__ import annotations

import enum
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import (
    Base,
    SoftDeleteMixin,
    TenantScopedMixin,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
)

if TYPE_CHECKING:
    from app.models.core import Tenant
    from app.models.post_sales_erp import (
        Contract,
        HealthScoreLog,
        Invoice,
        OnboardingPlan,
        Subscription,
        Ticket,
    )


class CompanySize(str, enum.Enum):
    MEI = "mei"
    MICRO = "micro"
    SMALL = "small"
    MEDIUM = "medium"
    LARGE = "large"
    GOVERNMENT = "government"


class TaxRegime(str, enum.Enum):
    MEI = "mei"
    SIMPLES_NACIONAL = "simples_nacional"
    LUCRO_PRESUMIDO = "lucro_presumido"
    LUCRO_REAL = "lucro_real"
    ISENTO = "isento"
    UNKNOWN = "unknown"


class LifecycleStage(str, enum.Enum):
    PROSPECT = "prospect"
    LEAD = "lead"
    MQL = "mql"
    SQL = "sql"
    OPPORTUNITY = "opportunity"
    ACTIVE_CLIENT = "active_client"
    DELINQUENT = "delinquent"
    CHURNED = "churned"
    DISQUALIFIED = "disqualified"


class ContactSeniority(str, enum.Enum):
    FOUNDER_OWNER = "founder_owner"
    C_LEVEL = "c_level"
    VP_DIRECTOR = "vp_director"
    MANAGER = "manager"
    COORDINATOR = "coordinator"
    SPECIALIST_ANALYST = "specialist_analyst"
    OPERATIONAL = "operational"


class BuyingRole(str, enum.Enum):
    ECONOMIC_BUYER = "economic_buyer"
    CHAMPION = "champion"
    TECHNICAL_EVALUATOR = "technical_evaluator"
    INFLUENCER = "influencer"
    END_USER = "end_user"
    BLOCKER = "blocker"


class DealStatus(str, enum.Enum):
    OPEN = "open"
    WON = "won"
    LOST = "lost"
    ABANDONED = "abandoned"


class ActivityType(str, enum.Enum):
    CALL = "call"
    MEETING = "meeting"
    EMAIL = "email"
    WHATSAPP = "whatsapp"
    TASK = "task"
    PROPOSAL_SENT = "proposal_sent"
    NOTE = "note"


class BillingFrequency(str, enum.Enum):
    ONE_TIME = "one_time"
    MONTHLY = "monthly"
    QUARTERLY = "quarterly"
    SEMIANNUAL = "semiannual"
    ANNUAL = "annual"


class Company(Base, UUIDPrimaryKeyMixin, TenantScopedMixin, TimestampMixin, SoftDeleteMixin):
    """
    Cadastro Corporativo B2B/B2C Ultra-Completo:
    Unifica dados fiscais da Receita Federal, CNAEs, QSA, múltiplos endereços,
    dados bancários, perfil técnico/ICP, rastreamento UTM e campos customizados JSONB.
    """
    __tablename__ = "companies"
    __table_args__ = (
        UniqueConstraint("tenant_id", "cnpj", name="uq_companies_tenant_cnpj"),
        Index("ix_companies_tenant_lifecycle", "tenant_id", "lifecycle_stage"),
        Index("ix_companies_tenant_cnae", "tenant_id", "primary_cnae_code"),
        Index("ix_companies_tenant_geo", "tenant_id", "state_uf", "city"),
        Index("ix_companies_custom_fields_gin", "custom_fields", postgresql_using="gin"),
        Index("ix_companies_secondary_cnaes_gin", "secondary_cnaes", postgresql_using="gin"),
        Index("ix_companies_partners_qsa_gin", "partners_qsa", postgresql_using="gin"),
    )

    legal_name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    trade_name: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    cnpj: Mapped[str | None] = mapped_column(String(18), nullable=True, index=True)
    is_headquarters: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    parent_company_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.id", ondelete="SET NULL"), nullable=True
    )

    state_registration: Mapped[str | None] = mapped_column(String(30), nullable=True)
    is_ie_exempt: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    municipal_registration: Mapped[str | None] = mapped_column(String(30), nullable=True)
    suframa_code: Mapped[str | None] = mapped_column(String(30), nullable=True)

    tax_regime: Mapped[TaxRegime] = mapped_column(
        Enum(TaxRegime, name="tax_regime_enum"),
        nullable=False,
        default=TaxRegime.UNKNOWN,
    )
    company_size: Mapped[CompanySize] = mapped_column(
        Enum(CompanySize, name="company_size_enum"),
        nullable=False,
        default=CompanySize.SMALL,
    )
    legal_nature_code: Mapped[str | None] = mapped_column(String(10), nullable=True)
    legal_nature_description: Mapped[str | None] = mapped_column(String(200), nullable=True)
    share_capital: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)
    founded_at: Mapped[date | None] = mapped_column(Date, nullable=True)
    cadastral_status: Mapped[str] = mapped_column(String(40), nullable=False, default="ATIVA")
    cadastral_status_date: Mapped[date | None] = mapped_column(Date, nullable=True)

    primary_cnae_code: Mapped[str | None] = mapped_column(String(15), nullable=True)
    primary_cnae_description: Mapped[str | None] = mapped_column(String(300), nullable=True)
    secondary_cnaes: Mapped[list[dict[str, str]]] = mapped_column(JSONB, nullable=False, default=list)
    partners_qsa: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)

    postal_code: Mapped[str | None] = mapped_column(String(12), nullable=True, index=True)
    city: Mapped[str | None] = mapped_column(String(120), nullable=True)
    city_ibge_code: Mapped[str | None] = mapped_column(String(10), nullable=True)
    state_uf: Mapped[str | None] = mapped_column(String(2), nullable=True)
    country_code: Mapped[str] = mapped_column(String(3), nullable=False, default="BRA")

    fiscal_address: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    billing_address: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    shipping_address: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)

    primary_email: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    financial_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    primary_phone: Mapped[str | None] = mapped_column(String(30), nullable=True)
    whatsapp_number: Mapped[str | None] = mapped_column(String(30), nullable=True, index=True)
    additional_phones: Mapped[list[dict[str, str]]] = mapped_column(JSONB, nullable=False, default=list)
    additional_emails: Mapped[list[dict[str, str]]] = mapped_column(JSONB, nullable=False, default=list)

    website: Mapped[str | None] = mapped_column(String(300), nullable=True)
    domain: Mapped[str | None] = mapped_column(String(180), nullable=True, index=True)
    linkedin_url: Mapped[str | None] = mapped_column(String(300), nullable=True)
    instagram_handle: Mapped[str | None] = mapped_column(String(100), nullable=True)

    bank_details: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    credit_limit: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False, default=Decimal("0.00"))
    payment_terms_days: Mapped[int] = mapped_column(Integer, nullable=False, default=30)

    lifecycle_stage: Mapped[LifecycleStage] = mapped_column(
        Enum(LifecycleStage, name="lifecycle_stage_enum"),
        nullable=False,
        default=LifecycleStage.PROSPECT,
    )
    lead_source: Mapped[str | None] = mapped_column(String(80), nullable=True)
    utm_data: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)

    icp_score: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    icp_tier: Mapped[str | None] = mapped_column(String(10), nullable=True)
    employee_count_min: Mapped[int | None] = mapped_column(Integer, nullable=True)
    employee_count_max: Mapped[int | None] = mapped_column(Integer, nullable=True)
    estimated_annual_revenue: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)

    tech_stack: Mapped[list[str]] = mapped_column(ARRAY(String), nullable=False, default=list)
    current_competitors: Mapped[list[str]] = mapped_column(ARRAY(String), nullable=False, default=list)
    identified_pain_points: Mapped[list[str]] = mapped_column(ARRAY(String), nullable=False, default=list)
    enrichment_raw_data: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    last_enriched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    owner_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    sdr_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    cs_owner_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    prospecting_campaign_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("prospecting_campaigns.id", ondelete="SET NULL"), nullable=True
    )

    tags: Mapped[list[str]] = mapped_column(ARRAY(String), nullable=False, default=list)
    custom_fields: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    erp_external_id: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)

    tenant: Mapped[Tenant] = relationship("Tenant", back_populates="companies")
    contacts_assoc: Mapped[list[CompanyContact]] = relationship(
        "CompanyContact", back_populates="company", cascade="all, delete-orphan"
    )
    deals: Mapped[list[Deal]] = relationship("Deal", back_populates="company")
    contracts: Mapped[list[Contract]] = relationship("Contract", back_populates="company")
    subscriptions: Mapped[list[Subscription]] = relationship("Subscription", back_populates="company")
    invoices: Mapped[list[Invoice]] = relationship("Invoice", back_populates="company")
    tickets: Mapped[list[Ticket]] = relationship("Ticket", back_populates="company")
    health_scores: Mapped[list[HealthScoreLog]] = relationship("HealthScoreLog", back_populates="company")
    onboarding_plans: Mapped[list[OnboardingPlan]] = relationship("OnboardingPlan", back_populates="company")


class Contact(Base, UUIDPrimaryKeyMixin, TenantScopedMixin, TimestampMixin, SoftDeleteMixin):
    """Cadastro de Pessoas / Decisores / Stakeholders com governança granular de LGPD."""
    __tablename__ = "contacts"
    __table_args__ = (
        Index("ix_contacts_tenant_whatsapp", "tenant_id", "whatsapp_e164"),
        Index("ix_contacts_tenant_email", "tenant_id", "email"),
        Index("ix_contacts_custom_fields_gin", "custom_fields", postgresql_using="gin"),
    )

    full_name: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    preferred_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    cpf: Mapped[str | None] = mapped_column(String(14), nullable=True, index=True)
    birth_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    avatar_url: Mapped[str | None] = mapped_column(String(500), nullable=True)

    job_title: Mapped[str | None] = mapped_column(String(150), nullable=True)
    department: Mapped[str | None] = mapped_column(String(100), nullable=True)
    seniority: Mapped[ContactSeniority | None] = mapped_column(
        Enum(ContactSeniority, name="contact_seniority_enum"), nullable=True
    )
    buying_role: Mapped[BuyingRole | None] = mapped_column(
        Enum(BuyingRole, name="buying_role_enum"), nullable=True
    )

    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    secondary_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(30), nullable=True)
    whatsapp_e164: Mapped[str | None] = mapped_column(String(25), nullable=True)
    telegram_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    linkedin_url: Mapped[str | None] = mapped_column(String(300), nullable=True)
    instagram_handle: Mapped[str | None] = mapped_column(String(100), nullable=True)

    opt_in_whatsapp: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    opt_in_email: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    opt_in_phone: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    opt_in_sms: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    lgpd_consent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    lgpd_legal_basis: Mapped[str | None] = mapped_column(String(80), nullable=True)

    preferred_channel: Mapped[str] = mapped_column(String(30), nullable=False, default="whatsapp")
    preferred_contact_window: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    timezone: Mapped[str] = mapped_column(String(60), nullable=False, default="America/Sao_Paulo")
    language: Mapped[str] = mapped_column(String(10), nullable=False, default="pt-BR")

    lead_score: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_contacted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_replied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    owner_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    tags: Mapped[list[str]] = mapped_column(ARRAY(String), nullable=False, default=list)
    custom_fields: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)

    tenant: Mapped[Tenant] = relationship("Tenant", back_populates="contacts")
    companies_assoc: Mapped[list[CompanyContact]] = relationship(
        "CompanyContact", back_populates="contact", cascade="all, delete-orphan"
    )


class CompanyContact(Base, UUIDPrimaryKeyMixin, TenantScopedMixin, TimestampMixin):
    """Vínculo N:N entre Empresas e Pessoas."""
    __tablename__ = "company_contacts"
    __table_args__ = (
        UniqueConstraint("tenant_id", "company_id", "contact_id", name="uq_company_contact_pair"),
    )

    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True
    )
    contact_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("contacts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    role_description: Mapped[str | None] = mapped_column(String(120), nullable=True)
    is_primary_decision_maker: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_billing_contact: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_legal_signatory: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    company: Mapped[Company] = relationship("Company", back_populates="contacts_assoc")
    contact: Mapped[Contact] = relationship("Contact", back_populates="companies_assoc")


class ProspectingCampaign(Base, UUIDPrimaryKeyMixin, TenantScopedMixin, TimestampMixin):
    """Campanhas de extração/enriquecimento de CNPJs e listas ICP para outbound."""
    __tablename__ = "prospecting_campaigns"

    name: Mapped[str] = mapped_column(String(180), nullable=False)
    source_type: Mapped[str] = mapped_column(String(50), nullable=False)
    icp_filters: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="draft")
    total_extracted: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_enriched: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    auto_enroll_cadence_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cadences.id", ondelete="SET NULL"), nullable=True
    )


class Pipeline(Base, UUIDPrimaryKeyMixin, TenantScopedMixin, TimestampMixin):
    __tablename__ = "pipelines"

    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    pipeline_type: Mapped[str] = mapped_column(String(40), nullable=False, default="sales")
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    tenant: Mapped[Tenant] = relationship("Tenant", back_populates="pipelines")
    stages: Mapped[list[PipelineStage]] = relationship(
        "PipelineStage",
        back_populates="pipeline",
        order_by="PipelineStage.display_order",
        cascade="all, delete-orphan",
    )
    deals: Mapped[list[Deal]] = relationship("Deal", back_populates="pipeline")


class PipelineStage(Base, UUIDPrimaryKeyMixin, TenantScopedMixin, TimestampMixin):
    __tablename__ = "pipeline_stages"

    pipeline_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("pipelines.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    display_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    win_probability: Mapped[int] = mapped_column(Integer, nullable=False, default=10)
    rotting_days_limit: Mapped[int | None] = mapped_column(Integer, nullable=True)
    color_hex: Mapped[str] = mapped_column(String(7), nullable=False, default="#3B82F6")

    required_deal_fields: Mapped[list[str]] = mapped_column(ARRAY(String), nullable=False, default=list)
    is_won_stage: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_lost_stage: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    pipeline: Mapped[Pipeline] = relationship("Pipeline", back_populates="stages")
    deals: Mapped[list[Deal]] = relationship("Deal", back_populates="stage")


class ProductCatalog(Base, UUIDPrimaryKeyMixin, TenantScopedMixin, TimestampMixin, SoftDeleteMixin):
    """Catálogo de Produtos/Serviços com atributos fiscais prontos para faturamento e ERP."""
    __tablename__ = "product_catalog"
    __table_args__ = (UniqueConstraint("tenant_id", "sku", name="uq_products_tenant_sku"),)

    sku: Mapped[str] = mapped_column(String(80), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    category: Mapped[str | None] = mapped_column(String(100), nullable=True)
    product_type: Mapped[str] = mapped_column(String(30), nullable=False, default="service")

    unit_price: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False)
    cost_price: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False, default=Decimal("0.00"))
    setup_fee: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False, default=Decimal("0.00"))
    billing_frequency: Mapped[BillingFrequency] = mapped_column(
        Enum(BillingFrequency, name="billing_frequency_enum"),
        nullable=False,
        default=BillingFrequency.MONTHLY,
    )

    ncm_code: Mapped[str | None] = mapped_column(String(15), nullable=True)
    nbs_code: Mapped[str | None] = mapped_column(String(15), nullable=True)
    service_list_code: Mapped[str | None] = mapped_column(String(15), nullable=True)
    tax_metadata: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    erp_product_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    tenant: Mapped[Tenant] = relationship("Tenant", back_populates="products")


class Deal(Base, UUIDPrimaryKeyMixin, TenantScopedMixin, TimestampMixin, SoftDeleteMixin):
    """Oportunidade / Negócio no Kanban do CRM."""
    __tablename__ = "deals"
    __table_args__ = (
        Index("ix_deals_tenant_pipeline_stage", "tenant_id", "pipeline_id", "stage_id"),
        Index("ix_deals_custom_fields_gin", "custom_fields", postgresql_using="gin"),
    )

    title: Mapped[str] = mapped_column(String(220), nullable=False)
    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True
    )
    primary_contact_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("contacts.id", ondelete="SET NULL"), nullable=True
    )
    pipeline_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("pipelines.id", ondelete="RESTRICT"), nullable=False
    )
    stage_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("pipeline_stages.id", ondelete="RESTRICT"), nullable=False
    )

    owner_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    sdr_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    status: Mapped[DealStatus] = mapped_column(
        Enum(DealStatus, name="deal_status_enum"),
        nullable=False,
        default=DealStatus.OPEN,
    )
    mrr_value: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False, default=Decimal("0.00"))
    one_time_value: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False, default=Decimal("0.00"))
    total_contract_value: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False, default=Decimal("0.00"))
    discount_amount: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False, default=Decimal("0.00"))
    contract_months: Mapped[int] = mapped_column(Integer, nullable=False, default=12)

    win_probability: Mapped[int] = mapped_column(Integer, nullable=False, default=10)
    expected_close_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    stage_entered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )

    loss_reason: Mapped[str | None] = mapped_column(String(150), nullable=True)
    loss_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    loss_competitor: Mapped[str | None] = mapped_column(String(120), nullable=True)

    qualification_data: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=lambda: {
            "budget": None,
            "authority": None,
            "need": None,
            "timeline": None,
            "decision_criteria": None,
        },
    )
    tags: Mapped[list[str]] = mapped_column(ARRAY(String), nullable=False, default=list)
    custom_fields: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)

    tenant: Mapped[Tenant] = relationship("Tenant", back_populates="deals")
    company: Mapped[Company] = relationship("Company", back_populates="deals")
    pipeline: Mapped[Pipeline] = relationship("Pipeline", back_populates="deals")
    stage: Mapped[PipelineStage] = relationship("PipelineStage", back_populates="deals")
    items: Mapped[list[DealItem]] = relationship("DealItem", back_populates="deal", cascade="all, delete-orphan")
    stage_history: Mapped[list[DealStageHistory]] = relationship(
        "DealStageHistory", back_populates="deal", cascade="all, delete-orphan"
    )


class DealItem(Base, UUIDPrimaryKeyMixin, TenantScopedMixin, TimestampMixin):
    __tablename__ = "deal_items"

    deal_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("deals.id", ondelete="CASCADE"), nullable=False, index=True
    )
    product_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("product_catalog.id", ondelete="RESTRICT"), nullable=False
    )
    quantity: Mapped[Decimal] = mapped_column(Numeric(12, 3), nullable=False, default=Decimal("1.000"))
    unit_price: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False)
    discount_percent: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False, default=Decimal("0.00"))
    subtotal: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False)
    billing_frequency: Mapped[BillingFrequency] = mapped_column(
        Enum(BillingFrequency, name="billing_frequency_enum"), nullable=False
    )

    deal: Mapped[Deal] = relationship("Deal", back_populates="items")


class DealStageHistory(Base, UUIDPrimaryKeyMixin, TenantScopedMixin):
    """Histórico temporal de movimentação de etapas para métricas de conversão e SLA de funil."""
    __tablename__ = "deal_stage_history"

    deal_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("deals.id", ondelete="CASCADE"), nullable=False, index=True
    )
    from_stage_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("pipeline_stages.id", ondelete="SET NULL"), nullable=True
    )
    to_stage_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("pipeline_stages.id", ondelete="RESTRICT"), nullable=False
    )
    changed_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    changed_by_ai: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    duration_in_previous_stage_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    entered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )

    deal: Mapped[Deal] = relationship("Deal", back_populates="stage_history")


class Activity(Base, UUIDPrimaryKeyMixin, TenantScopedMixin, TimestampMixin):
    """Linha do tempo unificada de atividades (ligações, reuniões, notas, tarefas)."""
    __tablename__ = "activities"

    company_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.id", ondelete="CASCADE"), nullable=True, index=True
    )
    contact_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("contacts.id", ondelete="SET NULL"), nullable=True, index=True
    )
    deal_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("deals.id", ondelete="CASCADE"), nullable=True, index=True
    )
    assigned_to_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )

    activity_type: Mapped[ActivityType] = mapped_column(
        Enum(ActivityType, name="activity_type_enum"), nullable=False
    )
    subject: Mapped[str] = mapped_column(String(220), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    outcome: Mapped[str | None] = mapped_column(String(100), nullable=True)
    recording_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    ai_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
