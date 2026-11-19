from __future__ import annotations

import asyncio

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.db import session as db_session
from app.db.base import Base
from app.models import ai_engine, core, crm, whatsapp  # noqa: F401
from app.models import proposals  # noqa: F401

PROPOSAL_TABLES = [
    "catalog_items",
    "commercial_proposals",
    "proposal_items",
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


async def init_proposals_schema() -> None:
    engine = resolve_async_engine()

    print("[1/3] Criando tabelas catalog_items, commercial_proposals e proposal_items...")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    print("[2/3] Aplicando políticas Row-Level Security (RLS) nas tabelas de Propostas/CPQ...")
    async with engine.begin() as conn:
        for table in PROPOSAL_TABLES:
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

    print("[3/3] Criando função SECURITY DEFINER resolve_proposal_token_tenant...")
    async with engine.begin() as conn:
        await conn.execute(
            text(
                """
                CREATE OR REPLACE FUNCTION resolve_proposal_token_tenant(p_public_token TEXT)
                RETURNS UUID
                LANGUAGE sql
                SECURITY DEFINER
                SET search_path = public
                AS $$
                    SELECT tenant_id
                    FROM commercial_proposals
                    WHERE public_token = p_public_token
                    LIMIT 1;
                $$;
                """
            )
        )

    print("[CONCLUÍDO] Módulo de Catálogo CPQ, Propostas e Aceite Digital ativo no PostgreSQL.")


if __name__ == "__main__":
    asyncio.run(init_proposals_schema())
