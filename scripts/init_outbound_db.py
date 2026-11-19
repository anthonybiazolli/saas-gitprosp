from __future__ import annotations

import asyncio

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.db import session as db_session
from app.db.base import Base
from app.models import ai_engine, core, crm, proposals, whatsapp  # noqa: F401
from app.models import outbound  # noqa: F401

OUTBOUND_TABLES = [
    "prospecting_campaigns",
    "outbound_leads",
    "cadence_sequences",
    "cadence_steps",
    "cadence_enrollments",
]


def resolve_async_engine() -> AsyncEngine:
    engine = (
        getattr(db_session, "async_engine", None)
        or getattr(db_session, "engine", None)
        or db_session.AsyncSessionLocal.kw.get("bind")
    )
    if engine is None:
        raise RuntimeError("Não foi possível localizar o AsyncEngine em app.db.session.")
    return engine


async def init_outbound_schema() -> None:
    engine = resolve_async_engine()

    print("[1/2] Criando tabelas de Prospecção Outbound e Cadências Multi-Etapa...")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    print("[2/2] Aplicando políticas Row-Level Security (RLS) nas tabelas outbound/cadências...")
    async with engine.begin() as conn:
        for table in OUTBOUND_TABLES:
            await conn.execute(text(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;"))
            await conn.execute(text(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY;"))
            await conn.execute(text(f"DROP POLICY IF EXISTS tenant_isolation_policy ON {table};"))
            await conn.execute(
                text(
                    f"""
                    CREATE POLICY tenant_isolation_policy ON {table}
                    USING (tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid)
                    WITH CHECK (tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid);
                    """
                )
            )

    print("[CONCLUÍDO] Tabelas de Prospecção Outbound e Cadências ativas com RLS.")


if __name__ == "__main__":
    asyncio.run(init_outbound_schema())
