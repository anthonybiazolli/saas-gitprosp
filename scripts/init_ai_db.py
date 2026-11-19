from __future__ import annotations

import asyncio

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.db import session as db_session
from app.db.base import Base
from app.models import core, crm  # noqa: F401
from app.models import ai_engine  # noqa: F401

AI_TABLES = [
    "ai_agent_configs",
    "ai_knowledge_documents",
    "ai_knowledge_chunks",
    "ai_conversations",
    "ai_messages",
    "ai_followup_tasks",
]


def resolve_async_engine() -> AsyncEngine:
    engine = (
        getattr(db_session, "async_engine", None)
        or getattr(db_session, "engine", None)
        or db_session.AsyncSessionLocal.kw.get("bind")
    )
    if engine is None:
        raise RuntimeError(
            "Não foi possível localizar o AsyncEngine em app.db.session."
        )
    return engine


async def init_ai_schema() -> None:
    engine = resolve_async_engine()

    print("[1/4] Garantindo extensão pgvector no PostgreSQL...")
    async with engine.begin() as conn:
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector;"))

    print("[2/4] Criando tabelas do motor de Agentes IA, RAG Vetorial e Follow-up...")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    print("[3/4] Criando índice vetorial HNSW (vector_cosine_ops) no pgvector...")
    async with engine.begin() as conn:
        await conn.execute(
            text(
                """
                CREATE INDEX IF NOT EXISTS ix_ai_knowledge_chunks_embedding_hnsw
                ON ai_knowledge_chunks
                USING hnsw (embedding vector_cosine_ops);
                """
            )
        )

    print("[4/4] Aplicando políticas Row-Level Security (RLS) nas tabelas de IA...")
    async with engine.begin() as conn:
        for table in AI_TABLES:
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

    print("[CONCLUÍDO] Tabelas vetoriais, índice HNSW e políticas RLS da IA ativos.")


if __name__ == "__main__":
    asyncio.run(init_ai_schema())