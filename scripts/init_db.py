from __future__ import annotations

import asyncio
import uuid
from pathlib import Path

from sqlalchemy import select, text

from app.db.session import AsyncSessionLocal, async_engine
from app.models import (
    Base,
    Pipeline,
    PipelineStage,
    PlanTier,
    Tenant,
    set_tenant_rls_context,
)


async def apply_extensions_and_create_schema() -> None:
    print("[1/3] Ativando extensões PostgreSQL (pgcrypto, vector, pg_trgm, unaccent)...")
    async with async_engine.begin() as conn:
        await conn.execute(text('CREATE EXTENSION IF NOT EXISTS "pgcrypto";'))
        await conn.execute(text('CREATE EXTENSION IF NOT EXISTS "vector";'))
        await conn.execute(text('CREATE EXTENSION IF NOT EXISTS "pg_trgm";'))
        await conn.execute(text('CREATE EXTENSION IF NOT EXISTS "unaccent";'))

        print("[2/3] Criando todas as 41 tabelas multi-tenant...")
        await conn.run_sync(Base.metadata.create_all)

        print("[3/3] Aplicando índices Trigram/HNSW e políticas Row-Level Security (RLS)...")
        sql_path = Path(__file__).resolve().parent.parent / "sql" / "001_extensions_rls.sql"
        sql_script = sql_path.read_text(encoding="utf-8")
        raw_conn = await conn.get_raw_connection()
        await raw_conn.driver_connection.execute(sql_script)


async def seed_initial_tenant() -> None:
    async with AsyncSessionLocal() as session:
        async with session.begin():
            existing = await session.execute(
                select(Tenant).where(Tenant.slug == "tenant-matriz")
            )
            tenant = existing.scalar_one_or_none()

            if tenant is None:
                tenant = Tenant(
                    id=uuid.uuid4(),
                    name="Operação Comercial Matriz",
                    slug="tenant-matriz",
                    plan_tier=PlanTier.ENTERPRISE,
                    is_active=True,
                )
                session.add(tenant)
                await session.flush()

                await set_tenant_rls_context(session, tenant.id)

                pipeline = Pipeline(
                    tenant_id=tenant.id,
                    name="Funil Comercial B2B Padrão",
                    description="Esteira end-to-end de prospecção até fechamento",
                    pipeline_type="sales",
                    is_default=True,
                )
                session.add(pipeline)
                await session.flush()

                stages_data = [
                    ("Prospecção / Base Fria", 1, 5, "#64748B", False, False),
                    ("Abordagem IA SDR", 2, 15, "#3B82F6", False, False),
                    ("Qualificado (SQL)", 3, 35, "#8B5CF6", False, False),
                    ("Diagnóstico / Demo", 4, 55, "#F59E0B", False, False),
                    ("Proposta / Contrato Enviado", 5, 80, "#10B981", False, False),
                    ("Fechado Ganho", 6, 100, "#059669", True, False),
                    ("Perdido", 7, 0, "#EF4444", False, True),
                ]
                for name, order, prob, color, is_won, is_lost in stages_data:
                    session.add(
                        PipelineStage(
                            tenant_id=tenant.id,
                            pipeline_id=pipeline.id,
                            name=name,
                            display_order=order,
                            win_probability=prob,
                            color_hex=color,
                            is_won_stage=is_won,
                            is_lost_stage=is_lost,
                        )
                    )

                print(f"[SEED] Tenant inicial criado com sucesso! ID: {tenant.id}")
            else:
                print(f"[SEED] Tenant já existente. ID: {tenant.id}")


async def main() -> None:
    await apply_extensions_and_create_schema()
    await seed_initial_tenant()
    await async_engine.dispose()
    print("[CONCLUÍDO] Banco de dados multi-tenant pronto para operação.")


if __name__ == "__main__":
    asyncio.run(main())
