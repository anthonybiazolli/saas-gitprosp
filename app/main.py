from __future__ import annotations

from typing import Any

from fastapi import Depends, FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.router import api_v1_router
from app.core.config import settings
from app.db.session import get_db_session
from app.models.core import Tenant

app = FastAPI(
    title=settings.APP_NAME,
    version="1.1.0",
    description="API Comercial End-to-End Multi-Tenant com RLS, Validação Dinâmica Pydantic v2 e CRM Completo",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(IntegrityError)
async def sqlalchemy_integrity_exception_handler(
    request: Request, exc: IntegrityError
) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_409_CONFLICT,
        content={
            "detail": "Conflito de integridade no banco de dados (registro duplicado ou chave estrangeira inválida).",
        },
    )


@app.get("/health", tags=["Infraestrutura"])
async def healthcheck() -> dict[str, str]:
    return {"status": "ok", "service": settings.APP_NAME, "version": "1.1.0"}


@app.get("/api/v1/tenants", tags=["Multi-Tenancy"])
async def list_tenants(session: AsyncSession = Depends(get_db_session)) -> list[dict[str, Any]]:
    result = await session.execute(select(Tenant).where(Tenant.deleted_at.is_(None)))
    tenants = result.scalars().all()
    return [
        {
            "id": str(t.id),
            "name": t.name,
            "slug": t.slug,
            "plan_tier": t.plan_tier.value,
            "is_active": t.is_active,
        }
        for t in tenants
    ]


app.include_router(api_v1_router)

from pathlib import Path as _ConsolePath
from fastapi.responses import HTMLResponse as _ConsoleHTMLResponse

@app.get("/console", response_class=_ConsoleHTMLResponse, include_in_schema=False)
async def serve_operational_console() -> _ConsoleHTMLResponse:
    html_path = _ConsolePath(__file__).resolve().parent / "static" / "console.html"
    return _ConsoleHTMLResponse(content=html_path.read_text(encoding="utf-8"))
