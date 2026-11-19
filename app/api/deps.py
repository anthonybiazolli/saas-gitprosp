from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from fastapi import Depends, Header, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import set_tenant_rls_context
from app.db.session import get_db_session
from app.models.core import AuditLog, Tenant


@dataclass(slots=True)
class TenantContext:
    session: AsyncSession
    tenant_id: uuid.UUID
    user_id: uuid.UUID | None
    ip_address: str | None
    user_agent: str | None


async def get_tenant_context(
    request: Request,
    x_tenant_id: uuid.UUID = Header(..., description="UUID do Tenant ativo"),
    x_user_id: uuid.UUID | None = Header(None, description="UUID opcional do usuário executor"),
    session: AsyncSession = Depends(get_db_session),
) -> TenantContext:
    result = await session.execute(
        select(Tenant.id, Tenant.is_active, Tenant.deleted_at).where(Tenant.id == x_tenant_id)
    )
    tenant_row = result.first()
    if tenant_row is None or tenant_row.deleted_at is not None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Tenant não encontrado.",
        )
    if not tenant_row.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Tenant suspenso ou inativo.",
        )

    await set_tenant_rls_context(session, x_tenant_id)

    client_ip = request.client.host if request.client else None
    user_agent = request.headers.get("user-agent")

    return TenantContext(
        session=session,
        tenant_id=x_tenant_id,
        user_id=x_user_id,
        ip_address=client_ip,
        user_agent=user_agent[:500] if user_agent else None,
    )


async def write_audit_log(
    ctx: TenantContext,
    entity_type: str,
    record_id: uuid.UUID,
    action: str,
    changes_before: dict[str, Any] | None = None,
    changes_after: dict[str, Any] | None = None,
) -> None:
    log = AuditLog(
        tenant_id=ctx.tenant_id,
        actor_type="user" if ctx.user_id else "api",
        actor_id=ctx.user_id,
        entity_type=entity_type,
        record_id=record_id,
        action=action,
        changes_before=changes_before,
        changes_after=changes_after,
        ip_address=ctx.ip_address,
        user_agent=ctx.user_agent,
    )
    ctx.session.add(log)
