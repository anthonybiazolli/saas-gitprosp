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
from sqlalchemy.dialects.postgresql import JSONB, UUID
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
    from app.models.crm import Company


class ContractStatus(str, enum.Enum):
    DRAFT = "draft"
    LEGAL_REVIEW = "legal_review"
    SENT_FOR_SIGNATURE = "sent_for_signature"
    PARTIALLY_SIGNED = "partially_signed"
    SIGNED = "signed"
    ACTIVE = "active"
    EXPIRED = "expired"
    CANCELLED = "cancelled"


class SignatureProvider(str, enum.Enum):
    ZAPSIGN = "zapsign"
    CLICKSIGN = "clicksign"
    AUTENTIQUE = "autentique"
    DOCUSIGN = "docusign"
    MANUAL_UPLOAD = "manual_upload"


class SubscriptionStatus(str, enum.Enum):
    ACTIVE = "active"
    PAST_DUE = "past_due"
    SUSPENDED = "suspended"
    CANCELLED = "cancelled"


class InvoiceStatus(str, enum.Enum):
    DRAFT = "draft"
    PENDING = "pending"
    PAID = "paid"
    OVERDUE = "overdue"
    CANCELLED = "cancelled"
    REFUNDED = "refunded"


class PaymentMethod(str, enum.Enum):
    PIX = "pix"
    BOLETO = "boleto"
    CREDIT_CARD = "credit_card"
    TED_WIRE = "ted_wire"


class CommissionStatus(str, enum.Enum):
    PENDING_PAYMENT = "pending_payment"
    APPROVED = "approved"
    PAID = "paid"
    CLAWBACK = "clawback"


class OnboardingStatus(str, enum.Enum):
    NOT_STARTED = "not_started"
    IN_PROGRESS = "in_progress"
    BLOCKED_CLIENT = "blocked_client"
    COMPLETED = "completed"


class TicketPriority(str, enum.Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    URGENT = "urgent"


class TicketStatus(str, enum.Enum):
    OPEN = "open"
    AI_TRIAGING = "ai_triaging"
    IN_PROGRESS = "in_progress"
    WAITING_CUSTOMER = "waiting_customer"
    ESCALATED = "escalated"
    RESOLVED = "resolved"
    CLOSED = "closed"


class SupportTier(str, enum.Enum):
    N1 = "n1"
    N2 = "n2"
    N3 = "n3"


class ContractTemplate(Base, UUIDPrimaryKeyMixin, TenantScopedMixin, TimestampMixin):
    __tablename__ = "contract_templates"

    name: Mapped[str] = mapped_column(String(150), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    html_body: Mapped[str] = mapped_column(Text, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class Contract(Base, UUIDPrimaryKeyMixin, TenantScopedMixin, TimestampMixin, SoftDeleteMixin):
    """Contrato comercial com versionamento, assinatura eletrônica e gatilho de integração ERP."""
    __tablename__ = "contracts"
    __table_args__ = (
        UniqueConstraint("tenant_id", "contract_number", name="uq_contracts_tenant_number"),
    )

    contract_number: Mapped[str] = mapped_column(String(50), nullable=False)
    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    deal_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("deals.id", ondelete="SET NULL"), nullable=True
    )
    template_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("contract_templates.id", ondelete="SET NULL"), nullable=True
    )

    status: Mapped[ContractStatus] = mapped_column(
        Enum(ContractStatus, name="contract_status_enum"),
        nullable=False,
        default=ContractStatus.DRAFT,
    )
    rendered_html: Mapped[str] = mapped_column(Text, nullable=False)
    mrr_value: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False, default=Decimal("0.00"))
    setup_value: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False, default=Decimal("0.00"))
    total_contract_value: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False)

    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    auto_renew: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    readjustment_index: Mapped[str] = mapped_column(String(20), nullable=False, default="IPCA")

    signature_provider: Mapped[SignatureProvider] = mapped_column(
        Enum(SignatureProvider, name="signature_provider_enum"),
        nullable=False,
        default=SignatureProvider.ZAPSIGN,
    )
    external_envelope_id: Mapped[str | None] = mapped_column(String(200), nullable=True, index=True)
    signers: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    signed_pdf_url: Mapped[str | None] = mapped_column(String(600), nullable=True)
    signed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    erp_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    erp_order_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    custom_fields: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)

    tenant: Mapped[Tenant] = relationship("Tenant", back_populates="contracts")
    company: Mapped[Company] = relationship("Company", back_populates="contracts")


class Subscription(Base, UUIDPrimaryKeyMixin, TenantScopedMixin, TimestampMixin):
    """Motor de faturamento recorrente (MRR/ARR)."""
    __tablename__ = "subscriptions"

    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    contract_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("contracts.id", ondelete="RESTRICT"), nullable=False
    )
    status: Mapped[SubscriptionStatus] = mapped_column(
        Enum(SubscriptionStatus, name="subscription_status_enum"),
        nullable=False,
        default=SubscriptionStatus.ACTIVE,
    )
    mrr_amount: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False)
    billing_day: Mapped[int] = mapped_column(Integer, nullable=False, default=10)
    next_billing_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    payment_method: Mapped[PaymentMethod] = mapped_column(
        Enum(PaymentMethod, name="payment_method_enum"),
        nullable=False,
        default=PaymentMethod.PIX,
    )
    gateway_provider: Mapped[str] = mapped_column(String(40), nullable=False, default="asaas")
    gateway_subscription_id: Mapped[str | None] = mapped_column(String(150), nullable=True)

    tenant: Mapped[Tenant] = relationship("Tenant", back_populates="subscriptions")
    company: Mapped[Company] = relationship("Company", back_populates="subscriptions")


class Invoice(Base, UUIDPrimaryKeyMixin, TenantScopedMixin, TimestampMixin):
    """Títulos financeiros, cobranças via gateway, NF-e e régua de inadimplência."""
    __tablename__ = "invoices"
    __table_args__ = (
        UniqueConstraint("tenant_id", "invoice_number", name="uq_invoices_tenant_number"),
        Index("ix_invoices_tenant_status_due", "tenant_id", "status", "due_date"),
    )

    invoice_number: Mapped[str] = mapped_column(String(50), nullable=False)
    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    contract_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("contracts.id", ondelete="SET NULL"), nullable=True
    )
    subscription_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("subscriptions.id", ondelete="SET NULL"), nullable=True
    )

    status: Mapped[InvoiceStatus] = mapped_column(
        Enum(InvoiceStatus, name="invoice_status_enum"),
        nullable=False,
        default=InvoiceStatus.PENDING,
    )
    payment_method: Mapped[PaymentMethod] = mapped_column(
        Enum(PaymentMethod, name="payment_method_enum"), nullable=False
    )

    gross_amount: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False)
    discount_amount: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False, default=Decimal("0.00"))
    tax_amount: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False, default=Decimal("0.00"))
    net_amount: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False)

    due_date: Mapped[date] = mapped_column(Date, nullable=False)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    gateway_provider: Mapped[str] = mapped_column(String(40), nullable=False, default="asaas")
    gateway_charge_id: Mapped[str | None] = mapped_column(String(150), nullable=True, index=True)
    boleto_url: Mapped[str | None] = mapped_column(String(600), nullable=True)
    pix_copy_paste: Mapped[str | None] = mapped_column(Text, nullable=True)

    nfe_number: Mapped[str | None] = mapped_column(String(50), nullable=True)
    nfe_access_key: Mapped[str | None] = mapped_column(String(60), nullable=True)
    nfe_pdf_url: Mapped[str | None] = mapped_column(String(600), nullable=True)
    dunning_step_level: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    tenant: Mapped[Tenant] = relationship("Tenant", back_populates="invoices")
    company: Mapped[Company] = relationship("Company", back_populates="invoices")


class CommissionRule(Base, UUIDPrimaryKeyMixin, TenantScopedMixin, TimestampMixin):
    __tablename__ = "commission_rules"

    name: Mapped[str] = mapped_column(String(120), nullable=False)
    target_role: Mapped[str] = mapped_column(String(40), nullable=False)
    percentage_on_mrr: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False, default=Decimal("0.00"))
    percentage_on_setup: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False, default=Decimal("0.00"))
    fixed_bonus_per_deal: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False, default=Decimal("0.00"))
    clawback_window_days: Mapped[int] = mapped_column(Integer, nullable=False, default=90)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class CommissionRecord(Base, UUIDPrimaryKeyMixin, TenantScopedMixin, TimestampMixin):
    __tablename__ = "commission_records"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    deal_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("deals.id", ondelete="RESTRICT"), nullable=False
    )
    invoice_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("invoices.id", ondelete="SET NULL"), nullable=True
    )
    amount: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False)
    status: Mapped[CommissionStatus] = mapped_column(
        Enum(CommissionStatus, name="commission_status_enum"),
        nullable=False,
        default=CommissionStatus.PENDING_PAYMENT,
    )
    reference_month: Mapped[str] = mapped_column(String(7), nullable=False)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class OnboardingPlan(Base, UUIDPrimaryKeyMixin, TenantScopedMixin, TimestampMixin):
    """Playbook estruturado de implantação pós-venda (Customer Success)."""
    __tablename__ = "onboarding_plans"

    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True
    )
    contract_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("contracts.id", ondelete="SET NULL"), nullable=True
    )
    cs_owner_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    status: Mapped[OnboardingStatus] = mapped_column(
        Enum(OnboardingStatus, name="onboarding_status_enum"),
        nullable=False,
        default=OnboardingStatus.NOT_STARTED,
    )
    kickoff_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    target_go_live_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    actual_go_live_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    progress_percent: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    company: Mapped[Company] = relationship("Company", back_populates="onboarding_plans")
    milestones: Mapped[list[OnboardingMilestone]] = relationship(
        "OnboardingMilestone",
        back_populates="plan",
        order_by="OnboardingMilestone.display_order",
        cascade="all, delete-orphan",
    )


class OnboardingMilestone(Base, UUIDPrimaryKeyMixin, TenantScopedMixin, TimestampMixin):
    __tablename__ = "onboarding_milestones"

    plan_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("onboarding_plans.id", ondelete="CASCADE"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(180), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    display_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    due_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    is_completed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    plan: Mapped[OnboardingPlan] = relationship("OnboardingPlan", back_populates="milestones")


class HealthScoreLog(Base, UUIDPrimaryKeyMixin, TenantScopedMixin):
    """Histórico de saúde da carteira para prevenção preditiva de Churn e identificação de Upsell."""
    __tablename__ = "health_score_logs"

    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True
    )
    overall_score: Mapped[int] = mapped_column(Integer, nullable=False)
    financial_score: Mapped[int] = mapped_column(Integer, nullable=False)
    support_score: Mapped[int] = mapped_column(Integer, nullable=False)
    engagement_score: Mapped[int] = mapped_column(Integer, nullable=False)
    churn_risk_level: Mapped[str] = mapped_column(String(20), nullable=False)
    ai_insights: Mapped[str | None] = mapped_column(Text, nullable=True)
    calculated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )

    company: Mapped[Company] = relationship("Company", back_populates="health_scores")


class NpsSurvey(Base, UUIDPrimaryKeyMixin, TenantScopedMixin, TimestampMixin):
    __tablename__ = "nps_surveys"

    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True
    )
    contact_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("contacts.id", ondelete="SET NULL"), nullable=True
    )
    survey_type: Mapped[str] = mapped_column(String(30), nullable=False, default="nps")
    score: Mapped[int] = mapped_column(Integer, nullable=False)
    feedback_comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    touchpoint: Mapped[str] = mapped_column(String(60), nullable=False, default="periodic")


class SlaPolicy(Base, UUIDPrimaryKeyMixin, TenantScopedMixin, TimestampMixin):
    __tablename__ = "sla_policies"

    name: Mapped[str] = mapped_column(String(120), nullable=False)
    priority: Mapped[TicketPriority] = mapped_column(
        Enum(TicketPriority, name="ticket_priority_enum"), nullable=False
    )
    first_response_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    resolution_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    business_hours_only: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class Ticket(Base, UUIDPrimaryKeyMixin, TenantScopedMixin, TimestampMixin, SoftDeleteMixin):
    """Helpdesk técnico integrado com triagem autônoma N1 por IA e controle rigoroso de SLA."""
    __tablename__ = "tickets"
    __table_args__ = (
        UniqueConstraint("tenant_id", "ticket_number", name="uq_tickets_tenant_number"),
        Index("ix_tickets_tenant_status_priority", "tenant_id", "status", "priority"),
    )

    ticket_number: Mapped[str] = mapped_column(String(40), nullable=False)
    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True
    )
    contact_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("contacts.id", ondelete="SET NULL"), nullable=True
    )
    assigned_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    sla_policy_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sla_policies.id", ondelete="SET NULL"), nullable=True
    )

    subject: Mapped[str] = mapped_column(String(220), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str] = mapped_column(String(80), nullable=False, default="technical")
    priority: Mapped[TicketPriority] = mapped_column(
        Enum(TicketPriority, name="ticket_priority_enum"),
        nullable=False,
        default=TicketPriority.MEDIUM,
    )
    tier: Mapped[SupportTier] = mapped_column(
        Enum(SupportTier, name="support_tier_enum"),
        nullable=False,
        default=SupportTier.N1,
    )
    status: Mapped[TicketStatus] = mapped_column(
        Enum(TicketStatus, name="ticket_status_enum"),
        nullable=False,
        default=TicketStatus.OPEN,
    )

    first_response_due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolution_due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    first_responded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    sla_breached: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    ai_triage_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    custom_fields: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)

    tenant: Mapped[Tenant] = relationship("Tenant", back_populates="tickets")
    company: Mapped[Company] = relationship("Company", back_populates="tickets")
    messages: Mapped[list[TicketMessage]] = relationship(
        "TicketMessage", back_populates="ticket", cascade="all, delete-orphan"
    )


class TicketMessage(Base, UUIDPrimaryKeyMixin, TenantScopedMixin, TimestampMixin):
    __tablename__ = "ticket_messages"

    ticket_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tickets.id", ondelete="CASCADE"), nullable=False, index=True
    )
    author_type: Mapped[str] = mapped_column(String(30), nullable=False)
    author_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    body: Mapped[str] = mapped_column(Text, nullable=False)
    is_internal_note: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    attachments: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)

    ticket: Mapped[Ticket] = relationship("Ticket", back_populates="messages")
