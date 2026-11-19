from __future__ import annotations

import asyncio

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.db import session as db_session
from app.db.base import Base
from app.models import ai_engine, core, crm  # noqa: F401
from app.models import whatsapp  # noqa: F401


def resolve_async_engine() -> AsyncEngine:
    engine = (
        getattr(db_session, "async_engine", None)
        or getattr(db_session, "engine", None)
        or db_session.AsyncSessionLocal.kw.get("bind")
    )
    if engine is None:
        raise RuntimeError("Não foi possível localizar o AsyncEngine em app.db.session.")
    return engine


async def init_whatsapp_schema() -> None:
    engine = resolve_async_engine()

    print("[1/3] Criando tabela whatsapp_instances...")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    print("[2/3] Aplicando política Row-Level Security (RLS) em whatsapp_instances...")
    async with engine.begin() as conn:
        await conn.execute(text("ALTER TABLE whatsapp_instances ENABLE ROW LEVEL SECURITY;"))
        await conn.execute(text("ALTER TABLE whatsapp_instances FORCE ROW LEVEL SECURITY;"))
        await conn.execute(text("DROP POLICY IF EXISTS tenant_isolation_policy ON whatsapp_instances;"))
        await conn.execute(
            text(
                """
                CREATE POLICY tenant_isolation_policy ON whatsapp_instances
                USING (tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid)
                WITH CHECK (tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid);
                """
            )
        )

    print("[3/3] Criando função SECURITY DEFINER resolve_whatsapp_instance_tenant...")
    async with engine.begin() as conn:
        await conn.execute(
            text(
                """
                CREATE OR REPLACE FUNCTION resolve_whatsapp_instance_tenant(p_instance_name TEXT)
                RETURNS UUID
                LANGUAGE sql
                SECURITY DEFINER
                SET search_path = public
                AS $$
                    SELECT tenant_id
                    FROM whatsapp_instances
                    WHERE instance_name = p_instance_name
                      AND is_active = TRUE
                    LIMIT 1;
                $$;
                """
            )
        )

    print("[CONCLUÍDO] Integração WhatsApp / Evolution API pronta no PostgreSQL.")


if __name__ == "__main__":
    asyncio.run(init_whatsapp_schema())
