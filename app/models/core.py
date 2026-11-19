from __future__ import annotations

import enum
import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
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
    from app.models.crm import Company, Contact, Deal, Pipeline, ProductCatalog
    from app.models.omnichannel_ai import AiAgentConfig, Cadence, ChannelAccount
    from app.models.post_sales_erp import Contract, Invoice, Subscription, Ticket


class PlanTier(str, enum.Enum):
    STARTER = "starter"
    GROWTH = "growth"
    SCALE = "scale"
    ENTERPRISE = "enterprise"


class UserRole(str, enum.Enum):
    OWNER = "owner"
    ADMIN = "admin"
    COMMERCIAL_MANAGER = "commercial_manager"
    SDR = "sdr"
    CLOSER = "closer"
    CS_MANAGER = "cs_manager"
    SUPPORT_AGENT = "support_agent"
    FINANCE = "finance"
    VIEWER = "viewer"


class CustomFieldEntity(str, enum.Enum):
    COMPANY = "company"
    CONTACT = "contact"
    DEAL = "deal"
    CONTRACT = "contract"
    TICKET = "ticket"
    PRODUCT = "product"


class CustomFieldType(str, enum.Enum):
    TEXT = "text"
    TEXTAREA = "textarea"
    NUMBER = "number"
    CURRENCY = "currency"
    PERCENTAGE = "percentage"
    DATE = "date"
    DATETIME = "datetime"
    BOOLEAN = "boolean"
    SELECT = "select"
    MULTISELECT = "multiselect"
    EMAIL = "email"
    PHONE = "phone"
    URL = "url"
    CNPJ_CPF = "cnpj_cpf"


class ErpProvider(str, enum.Enum):
    OMIE = "omie"
    BLING = "bling"
    TINY = "tiny"
    CONTA_AZUL = "conta_azul"
    TOTVS_PROTHEUS = "totvs_protheus"
    SAP_B1 = "sap_b1"
    SANKHYA = "sankhya"
    CUSTOM_WEBHOOK = "custom_webhook"


class SyncStatus(str, enum.Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    SUCCESS = "success"
    FAILED = "failed"
    DEAD_LETTER = "dead_letter"


class Tenant(Base, UUIDPrimaryKeyMixin, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "tenants"

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    slug: Mapped[str] = mapped_column(String(100), nullable=False, unique=True, index=True)
    cnpj: Mapped[str | None] = mapped_column(String(18), nullable=True, unique=True)
    legal_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    custom_domain: Mapped[str | None] = mapped_column(String(255), nullable=True, unique=True)

    plan_tier: Mapped[PlanTier] = mapped_column(
        Enum(PlanTier, name="plan_tier_enum"),
        nullable=False,
        default=PlanTier.GROWTH,
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    trial_ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    quotas: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=lambda: {
            "max_users": 15,
            "max_ai_tokens_monthly": 5000000,
            "max_prospecting_leads_monthly": 10000,
            "enabled_modules": [
                "crm",
                "prospecting",
                "omnichannel",
                "ai_sdr",
                "ai_followup",
                "contracts",
                "financial",
                "cs",
                "support",
                "bi",
            ],
        },
    )

    settings: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=lambda: {
            "timezone": "America/Sao_Paulo",
            "currency": "BRL",
            "business_hours": {
                "mon": ["08:00", "18:00"],
                "tue": ["08:00", "18:00"],
                "wed": ["08:00", "18:00"],
                "thu": ["08:00", "18:00"],
                "fri": ["08:00", "18:00"],
            },
        },
    )

    users: Mapped[list[User]] = relationship("User", back_populates="tenant", cascade="all, delete-orphan")
    teams: Mapped[list[Team]] = relationship("Team", back_populates="tenant", cascade="all, delete-orphan")
    custom_field_definitions: Mapped[list[CustomFieldDefinition]] = relationship(
        "CustomFieldDefinition", back_populates="tenant", cascade="all, delete-orphan"
    )
    companies: Mapped[list[Company]] = relationship("Company", back_populates="tenant")
    contacts: Mapped[list[Contact]] = relationship("Contact", back_populates="tenant")
    pipelines: Mapped[list[Pipeline]] = relationship("Pipeline", back_populates="tenant")
    products: Mapped[list[ProductCatalog]] = relationship("ProductCatalog", back_populates="tenant")
    deals: Mapped[list[Deal]] = relationship("Deal", back_populates="tenant")
    channel_accounts: Mapped[list[ChannelAccount]] = relationship("ChannelAccount", back_populates="tenant")
    ai_agents: Mapped[list[AiAgentConfig]] = relationship("AiAgentConfig", back_populates="tenant")
    cadences: Mapped[list[Cadence]] = relationship("Cadence", back_populates="tenant")
    contracts: Mapped[list[Contract]] = relationship("Contract", back_populates="tenant")
    subscriptions: Mapped[list[Subscription]] = relationship("Subscription", back_populates="tenant")
    invoices: Mapped[list[Invoice]] = relationship("Invoice", back_populates="tenant")
    tickets: Mapped[list[Ticket]] = relationship("Ticket", back_populates="tenant")


class Team(Base, UUIDPrimaryKeyMixin, TenantScopedMixin, TimestampMixin):
    __tablename__ = "teams"
    __table_args__ = (UniqueConstraint("tenant_id", "name", name="uq_teams_tenant_name"),)

    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    department: Mapped[str] = mapped_column(String(50), nullable=False, default="commercial")
    manager_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL", use_alter=True, name="fk_teams_manager_id_users"),
        nullable=True,
    )
    round_robin_index: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    tenant: Mapped[Tenant] = relationship("Tenant", back_populates="teams")
    members: Mapped[list[User]] = relationship(
        "User", back_populates="team", foreign_keys="User.team_id"
    )


class User(Base, UUIDPrimaryKeyMixin, TenantScopedMixin, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "users"
    __table_args__ = (UniqueConstraint("tenant_id", "email", name="uq_users_tenant_email"),)

    team_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("teams.id", ondelete="SET NULL"), nullable=True, index=True
    )
    full_name: Mapped[str] = mapped_column(String(180), nullable=False)
    email: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    phone: Mapped[str | None] = mapped_column(String(30), nullable=True)
    avatar_url: Mapped[str | None] = mapped_column(String(500), nullable=True)

    role: Mapped[UserRole] = mapped_column(
        Enum(UserRole, name="user_role_enum"),
        nullable=False,
        default=UserRole.CLOSER,
    )
    granular_permissions: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)

    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    is_available_for_routing: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    max_active_leads: Mapped[int] = mapped_column(Integer, nullable=False, default=50)

    calendar_provider: Mapped[str | None] = mapped_column(String(30), nullable=True)
    calendar_external_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    calendar_credentials: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    meeting_link: Mapped[str | None] = mapped_column(String(500), nullable=True)

    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    tenant: Mapped[Tenant] = relationship("Tenant", back_populates="users")
    team: Mapped[Team | None] = relationship("Team", back_populates="members", foreign_keys=[team_id])


class CustomFieldDefinition(Base, UUIDPrimaryKeyMixin, TenantScopedMixin, TimestampMixin):
    """
    Motor de personalização dinâmica: permite que cada tenant crie campos arbitrários
    com validação tipada para qualquer entidade do CRM/Pós-venda.
    """
    __tablename__ = "custom_field_definitions"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "entity_type", "field_key", name="uq_custom_field_tenant_entity_key"
        ),
    )

    entity_type: Mapped[CustomFieldEntity] = mapped_column(
        Enum(CustomFieldEntity, name="custom_field_entity_enum"),
        nullable=False,
        index=True,
    )
    field_key: Mapped[str] = mapped_column(String(100), nullable=False)
    label: Mapped[str] = mapped_column(String(150), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    field_type: Mapped[CustomFieldType] = mapped_column(
        Enum(CustomFieldType, name="custom_field_type_enum"),
        nullable=False,
    )
    options: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    default_value: Mapped[Any | None] = mapped_column(JSONB, nullable=True)
    validation_rules: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    group_name: Mapped[str] = mapped_column(String(100), nullable=False, default="Geral")
    display_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    is_required: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_searchable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    is_visible_in_kanban: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    tenant: Mapped[Tenant] = relationship("Tenant", back_populates="custom_field_definitions")


class AuditLog(Base, UUIDPrimaryKeyMixin, TenantScopedMixin):
    """Trilha de auditoria imutável de ações humanas e autônomas da IA."""
    __tablename__ = "audit_logs"
    __table_args__ = (
        Index("ix_audit_logs_tenant_entity_record", "tenant_id", "entity_type", "record_id"),
        Index("ix_audit_logs_created_at", "created_at"),
    )

    actor_type: Mapped[str] = mapped_column(String(30), nullable=False)
    actor_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    entity_type: Mapped[str] = mapped_column(String(60), nullable=False)
    record_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    action: Mapped[str] = mapped_column(String(40), nullable=False)
    changes_before: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    changes_after: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    ip_address: Mapped[str | None] = mapped_column(String(45), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )


class ErpIntegrationConfig(Base, UUIDPrimaryKeyMixin, TenantScopedMixin, TimestampMixin):
    """Configuração de conectores ERP por tenant para continuidade da esteira pós-fechamento."""
    __tablename__ = "erp_integration_configs"

    name: Mapped[str] = mapped_column(String(120), nullable=False)
    provider: Mapped[ErpProvider] = mapped_column(
        Enum(ErpProvider, name="erp_provider_enum"),
        nullable=False,
    )
    base_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    encrypted_credentials: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    field_mappings: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    trigger_events: Mapped[list[str]] = mapped_column(
        ARRAY(String),
        nullable=False,
        default=lambda: ["contract.signed", "invoice.paid"],
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class ErpSyncLog(Base, UUIDPrimaryKeyMixin, TenantScopedMixin, TimestampMixin):
    """Fila e histórico de integração com ERPs com suporte a retentativa e idempotência."""
    __tablename__ = "erp_sync_logs"
    __table_args__ = (
        Index("ix_erp_sync_logs_tenant_status", "tenant_id", "status"),
    )

    erp_config_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("erp_integration_configs.id", ondelete="CASCADE"), nullable=False
    )
    event_type: Mapped[str] = mapped_column(String(80), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(50), nullable=False)
    entity_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    idempotency_key: Mapped[str] = mapped_column(String(150), nullable=False, unique=True)

    status: Mapped[SyncStatus] = mapped_column(
        Enum(SyncStatus, name="sync_status_enum"),
        nullable=False,
        default=SyncStatus.PENDING,
    )
    request_payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    response_payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    http_status_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    external_erp_record_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
