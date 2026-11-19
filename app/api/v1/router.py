from __future__ import annotations

from fastapi import APIRouter

from app.api.v1 import (
    ai_agents,
    companies,
    contacts,
    custom_fields,
    deals,
    outbound,
    pipelines,
    proposals,
    whatsapp,
)

api_v1_router = APIRouter(prefix="/api/v1")

api_v1_router.include_router(custom_fields.router)
api_v1_router.include_router(companies.router)
api_v1_router.include_router(contacts.router)
api_v1_router.include_router(pipelines.router)
api_v1_router.include_router(deals.router)
api_v1_router.include_router(ai_agents.router)
api_v1_router.include_router(whatsapp.router)
api_v1_router.include_router(whatsapp.webhook_router)
api_v1_router.include_router(proposals.router)
api_v1_router.include_router(proposals.public_router)
api_v1_router.include_router(outbound.router)

from app.api.v1 import crm_deals

api_v1_router.include_router(crm_deals.router, prefix="/crm", tags=["CRM Pipeline & Contratos"])

from app.api.v1 import billing

api_v1_router.include_router(billing.router, prefix="/billing", tags=["Faturamento & Comissões"])

from app.api.v1 import automation

api_v1_router.include_router(automation.router, prefix="/automation", tags=["Automação & Relatório Executivo"])

from app.api.v1 import team_portal

api_v1_router.include_router(team_portal.router, prefix="/team", tags=["Equipe, Metas & Portal do Cliente"])

from app.api.v1 import saas_gateway

api_v1_router.include_router(saas_gateway.router, prefix="/saas", tags=["Planos SaaS & Webhooks Inbound"])

from app.api.v1 import governance

api_v1_router.include_router(governance.router, prefix="/governance", tags=["Governança & Exportações CSV"])
