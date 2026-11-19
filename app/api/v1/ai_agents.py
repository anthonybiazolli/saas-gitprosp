from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.api.deps import TenantContext, get_tenant_context, write_audit_log
from app.models.ai_engine import (
    AIAgentConfig,
    AIAgentRole,
    AIConversation,
    AIConversationStatus,
    AIFollowupStatus,
    AIFollowupTask,
    AIKnowledgeDocument,
)
from app.schemas.ai_agents import (
    AIAgentConfigCreate,
    AIAgentConfigResponse,
    AIAgentConfigUpdate,
    AIConversationDetailResponse,
    AIConversationSummaryResponse,
    AIKnowledgeDocumentCreate,
    AIKnowledgeDocumentResponse,
    AIKnowledgeSearchHit,
    AIKnowledgeSearchRequest,
    FollowupScheduleRequest,
    FollowupTaskResponse,
    SDRRespondRequest,
    SDRRespondResponse,
)
from app.services.autonomous_agents import AutonomousAgentsOrchestrator
from app.services.gemini_rag import GeminiRAGService

router = APIRouter(prefix="/ai", tags=["Agentes IA (SDR, Follow-up & RAG)"])


@router.post("/agents", response_model=AIAgentConfigResponse, status_code=status.HTTP_201_CREATED)
async def create_ai_agent(
    payload: AIAgentConfigCreate,
    ctx: TenantContext = Depends(get_tenant_context),
) -> AIAgentConfig:
    dup = (
        await ctx.session.execute(
            select(AIAgentConfig.id).where(
                AIAgentConfig.tenant_id == ctx.tenant_id,
                AIAgentConfig.slug == payload.slug,
            )
        )
    ).scalar_one_or_none()
    if dup is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Já existe um agente com o slug '{payload.slug}' neste tenant.",
        )

    agent = AIAgentConfig(tenant_id=ctx.tenant_id, **payload.model_dump())
    ctx.session.add(agent)
    await ctx.session.flush()
    await write_audit_log(
        ctx,
        entity_type="ai_agent_config",
        record_id=agent.id,
        action="create",
        changes_after={"slug": agent.slug, "role": agent.role.value, "model": agent.gemini_model},
    )
    await ctx.session.commit()
    await ctx.session.refresh(agent)
    return agent


@router.get("/agents", response_model=list[AIAgentConfigResponse])
async def list_ai_agents(
    role: AIAgentRole | None = Query(None),
    ctx: TenantContext = Depends(get_tenant_context),
) -> list[AIAgentConfig]:
    stmt = select(AIAgentConfig).where(AIAgentConfig.tenant_id == ctx.tenant_id)
    if role is not None:
        stmt = stmt.where(AIAgentConfig.role == role)
    stmt = stmt.order_by(AIAgentConfig.created_at.asc())
    return list((await ctx.session.execute(stmt)).scalars().all())


@router.patch("/agents/{agent_id}", response_model=AIAgentConfigResponse)
async def update_ai_agent(
    agent_id: uuid.UUID,
    payload: AIAgentConfigUpdate,
    ctx: TenantContext = Depends(get_tenant_context),
) -> AIAgentConfig:
    agent = (
        await ctx.session.execute(
            select(AIAgentConfig).where(
                AIAgentConfig.id == agent_id,
                AIAgentConfig.tenant_id == ctx.tenant_id,
            )
        )
    ).scalar_one_or_none()
    if agent is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agente IA não encontrado.")

    for key, val in payload.model_dump(exclude_unset=True).items():
        setattr(agent, key, val)

    await ctx.session.commit()
    await ctx.session.refresh(agent)
    return agent


@router.post(
    "/knowledge/documents",
    response_model=AIKnowledgeDocumentResponse,
    status_code=status.HTTP_201_CREATED,
)
async def ingest_knowledge_document(
    payload: AIKnowledgeDocumentCreate,
    ctx: TenantContext = Depends(get_tenant_context),
) -> AIKnowledgeDocument:
    return await GeminiRAGService.ingest_document(
        session=ctx.session,
        tenant_id=ctx.tenant_id,
        title=payload.title,
        content=payload.content,
        category=payload.category,
        source_type=payload.source_type,
        tags=payload.tags,
        chunk_size=payload.chunk_size,
        chunk_overlap=payload.chunk_overlap,
    )


@router.get("/knowledge/documents", response_model=list[AIKnowledgeDocumentResponse])
async def list_knowledge_documents(
    category: str | None = Query(None),
    ctx: TenantContext = Depends(get_tenant_context),
) -> list[AIKnowledgeDocument]:
    stmt = select(AIKnowledgeDocument).where(AIKnowledgeDocument.tenant_id == ctx.tenant_id)
    if category:
        stmt = stmt.where(AIKnowledgeDocument.category == category.strip().lower())
    stmt = stmt.order_by(AIKnowledgeDocument.created_at.desc())
    return list((await ctx.session.execute(stmt)).scalars().all())


@router.post("/knowledge/search", response_model=list[AIKnowledgeSearchHit])
async def search_knowledge_base_endpoint(
    payload: AIKnowledgeSearchRequest,
    ctx: TenantContext = Depends(get_tenant_context),
) -> list[AIKnowledgeSearchHit]:
    hits = await GeminiRAGService.semantic_search(
        session=ctx.session,
        tenant_id=ctx.tenant_id,
        query=payload.query,
        category=payload.category,
        top_k=payload.top_k,
        min_similarity=payload.min_similarity,
    )
    return [AIKnowledgeSearchHit(**h) for h in hits]


@router.delete(
    "/knowledge/documents/{document_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response, response_model=None)
async def delete_knowledge_document(
    document_id: uuid.UUID,
    ctx: TenantContext = Depends(get_tenant_context),
) -> Response:
    doc = (
        await ctx.session.execute(
            select(AIKnowledgeDocument).where(
                AIKnowledgeDocument.id == document_id,
                AIKnowledgeDocument.tenant_id == ctx.tenant_id,
            )
        )
    ).scalar_one_or_none()
    if doc is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Documento não encontrado.")

    await ctx.session.delete(doc)
    await ctx.session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/sdr/respond", response_model=SDRRespondResponse)
async def sdr_respond(
    payload: SDRRespondRequest,
    ctx: TenantContext = Depends(get_tenant_context),
) -> SDRRespondResponse:
    return await AutonomousAgentsOrchestrator.handle_sdr_inbound(
        session=ctx.session,
        tenant_id=ctx.tenant_id,
        payload=payload,
    )


@router.get("/conversations", response_model=list[AIConversationSummaryResponse])
async def list_ai_conversations(
    status_filter: AIConversationStatus | None = Query(None, alias="status"),
    channel: str | None = Query(None),
    ctx: TenantContext = Depends(get_tenant_context),
) -> list[AIConversation]:
    stmt = select(AIConversation).where(AIConversation.tenant_id == ctx.tenant_id)
    if status_filter is not None:
        stmt = stmt.where(AIConversation.status == status_filter)
    if channel:
        stmt = stmt.where(AIConversation.channel == channel)
    stmt = stmt.order_by(AIConversation.updated_at.desc()).limit(100)
    return list((await ctx.session.execute(stmt)).scalars().all())


@router.get("/conversations/{conversation_id}", response_model=AIConversationDetailResponse)
async def get_ai_conversation_detail(
    conversation_id: uuid.UUID,
    ctx: TenantContext = Depends(get_tenant_context),
) -> AIConversation:
    stmt = (
        select(AIConversation)
        .options(selectinload(AIConversation.messages))
        .where(
            AIConversation.id == conversation_id,
            AIConversation.tenant_id == ctx.tenant_id,
        )
    )
    conv = (await ctx.session.execute(stmt)).scalar_one_or_none()
    if conv is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversa não encontrada.")
    return conv


@router.post("/conversations/{conversation_id}/resume-ai", response_model=AIConversationSummaryResponse)
async def resume_ai_control(
    conversation_id: uuid.UUID,
    ctx: TenantContext = Depends(get_tenant_context),
) -> AIConversation:
    conv = (
        await ctx.session.execute(
            select(AIConversation).where(
                AIConversation.id == conversation_id,
                AIConversation.tenant_id == ctx.tenant_id,
            )
        )
    ).scalar_one_or_none()
    if conv is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversa não encontrada.")

    conv.status = AIConversationStatus.ACTIVE_AI
    conv.handoff_reason = None
    await ctx.session.commit()
    await ctx.session.refresh(conv)
    return conv


@router.post("/followups/schedule", response_model=FollowupTaskResponse, status_code=status.HTTP_201_CREATED)
async def schedule_followup_endpoint(
    payload: FollowupScheduleRequest,
    ctx: TenantContext = Depends(get_tenant_context),
) -> AIFollowupTask:
    conv = (
        await ctx.session.execute(
            select(AIConversation).where(
                AIConversation.id == payload.conversation_id,
                AIConversation.tenant_id == ctx.tenant_id,
            )
        )
    ).scalar_one_or_none()
    if conv is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversa não encontrada.")

    scheduled_at = datetime.now(timezone.utc) + timedelta(minutes=payload.delay_minutes)
    task = AIFollowupTask(
        tenant_id=ctx.tenant_id,
        conversation_id=conv.id,
        company_id=conv.company_id,
        contact_id=conv.contact_id,
        deal_id=conv.deal_id,
        attempt_number=1,
        channel=payload.channel or conv.channel,
        objective=payload.objective,
        scheduled_for=scheduled_at,
        status=AIFollowupStatus.PENDING,
    )
    ctx.session.add(task)
    await ctx.session.commit()
    await ctx.session.refresh(task)
    return task


@router.get("/followups", response_model=list[FollowupTaskResponse])
async def list_followup_tasks(
    status_filter: AIFollowupStatus | None = Query(None, alias="status"),
    ctx: TenantContext = Depends(get_tenant_context),
) -> list[AIFollowupTask]:
    stmt = select(AIFollowupTask).where(AIFollowupTask.tenant_id == ctx.tenant_id)
    if status_filter is not None:
        stmt = stmt.where(AIFollowupTask.status == status_filter)
    stmt = stmt.order_by(AIFollowupTask.scheduled_for.asc()).limit(100)
    return list((await ctx.session.execute(stmt)).scalars().all())


@router.post("/followups/process-due", response_model=list[FollowupTaskResponse])
async def process_due_followups_now(
    ctx: TenantContext = Depends(get_tenant_context),
) -> list[AIFollowupTask]:
    now = datetime.now(timezone.utc)
    due_tasks = (
        await ctx.session.execute(
            select(AIFollowupTask)
            .where(
                AIFollowupTask.tenant_id == ctx.tenant_id,
                AIFollowupTask.status == AIFollowupStatus.PENDING,
                AIFollowupTask.scheduled_for <= now,
            )
            .order_by(AIFollowupTask.scheduled_for.asc())
            .limit(20)
        )
    ).scalars().all()

    processed: list[AIFollowupTask] = []
    for task in due_tasks:
        result_task = await AutonomousAgentsOrchestrator.execute_followup_task(ctx.session, task)
        processed.append(result_task)
    return processed
