from __future__ import annotations

import asyncio
import json
import os
import re
import uuid
from typing import Any

from fastapi import HTTPException, status
from google import genai
from google.genai import types
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ai_engine import AIKnowledgeChunk, AIKnowledgeDocument

EMBEDDING_DIMENSION = 768


def get_gemini_api_key() -> str:
    api_key = (os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY") or "").strip()
    if not api_key or api_key.startswith("your_") or api_key == "changeme":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="GEMINI_API_KEY não configurada. Defina sua chave válida em ~/apps/comercial-saas/.env e reinicie o container api.",
        )
    return api_key


def get_genai_client() -> genai.Client:
    return genai.Client(api_key=get_gemini_api_key())


def get_default_gemini_model() -> str:
    return (os.getenv("GEMINI_MODEL") or "gemini-2.5-flash").strip()


def get_fallback_gemini_model() -> str:
    return (os.getenv("GEMINI_FALLBACK_MODEL") or "gemini-2.0-flash").strip()


def get_default_embedding_model() -> str:
    return (os.getenv("GEMINI_EMBEDDING_MODEL") or "gemini-embedding-001").strip()


async def generate_content_resilient(
    client: genai.Client,
    contents: Any,
    config: types.GenerateContentConfig,
    preferred_model: str | None = None,
) -> Any:
    """
    Executa generate_content com retry/backoff exponencial e fallback automático
    para modelo secundário em caso de instabilidade transitória (429 / 503 / timeout).
    """
    primary = (preferred_model or get_default_gemini_model()).strip()
    fallback = get_fallback_gemini_model().strip()
    models_to_try = [primary]
    if fallback and fallback != primary:
        models_to_try.append(fallback)

    last_exc: Exception | None = None
    for model_name in models_to_try:
        for attempt in range(2):
            try:
                return await client.aio.models.generate_content(
                    model=model_name,
                    contents=contents,
                    config=config,
                )
            except Exception as exc:
                last_exc = exc
                err_str = str(exc)
                transient = any(code in err_str for code in ("429", "503", "500", "UNAVAILABLE", "RESOURCE_EXHAUSTED", "Deadline"))
                if transient and attempt == 0:
                    await asyncio.sleep(1.2)
                    continue
                break

    if last_exc is not None:
        raise last_exc
    raise RuntimeError("Falha inesperada ao invocar o modelo Gemini.")


async def generate_structured_json(
    prompt: str,
    schema: dict[str, Any],
    preferred_model: str | None = None,
    temperature: float = 0.25,
) -> dict[str, Any]:
    client = get_genai_client()
    cfg = types.GenerateContentConfig(
        temperature=temperature,
        response_mime_type="application/json",
        response_schema=schema,
    )
    resp = await generate_content_resilient(
        client=client,
        contents=prompt,
        config=cfg,
        preferred_model=preferred_model,
    )
    raw_text = (resp.text or "").strip()
    if not raw_text:
        return {}
    try:
        parsed = json.loads(raw_text)
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


def split_text_into_chunks(
    text: str,
    chunk_size: int = 900,
    chunk_overlap: int = 150,
) -> list[str]:
    cleaned = re.sub(r"\n{3,}", "\n\n", text.strip())
    if not cleaned:
        return []
    if len(cleaned) <= chunk_size:
        return [cleaned]

    chunks: list[str] = []
    start = 0
    text_len = len(cleaned)

    while start < text_len:
        end = min(text_len, start + chunk_size)
        if end < text_len:
            para_break = cleaned.rfind("\n\n", start + int(chunk_size * 0.5), end)
            if para_break != -1:
                end = para_break + 2
            else:
                sentence_break = cleaned.rfind(". ", start + int(chunk_size * 0.5), end)
                if sentence_break != -1:
                    end = sentence_break + 2

        piece = cleaned[start:end].strip()
        if piece:
            chunks.append(piece)

        if end >= text_len:
            break
        start = max(start + 1, end - chunk_overlap)

    return chunks


class GeminiRAGService:
    @staticmethod
    async def embed_texts(
        texts: list[str],
        task_type: str = "RETRIEVAL_DOCUMENT",
    ) -> list[list[float]]:
        if not texts:
            return []
        client = get_genai_client()
        model_name = get_default_embedding_model()

        all_vectors: list[list[float]] = []
        batch_size = 32
        for i in range(0, len(texts), batch_size):
            batch = texts[i : i + batch_size]
            response = await client.aio.models.embed_content(
                model=model_name,
                contents=batch,
                config=types.EmbedContentConfig(
                    task_type=task_type,
                    output_dimensionality=EMBEDDING_DIMENSION,
                ),
            )
            if not response.embeddings:
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail="A API Gemini não retornou vetores de embedding.",
                )
            for emb in response.embeddings:
                all_vectors.append(list(emb.values))

        return all_vectors

    @classmethod
    async def ingest_document(
        cls,
        session: AsyncSession,
        tenant_id: uuid.UUID,
        title: str,
        content: str,
        category: str = "geral",
        source_type: str = "manual_text",
        tags: list[str] | None = None,
        chunk_size: int = 900,
        chunk_overlap: int = 150,
    ) -> AIKnowledgeDocument:
        chunks = split_text_into_chunks(
            text=content,
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
        )
        if not chunks:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="O conteúdo do documento está vazio após normalização.",
            )

        vectors = await cls.embed_texts(chunks, task_type="RETRIEVAL_DOCUMENT")

        doc = AIKnowledgeDocument(
            tenant_id=tenant_id,
            title=title,
            category=category.strip().lower(),
            source_type=source_type,
            raw_content=content,
            tags=tags or [],
            chunk_count=len(chunks),
            is_active=True,
        )
        session.add(doc)
        await session.flush()

        for idx, (chunk_text, vector) in enumerate(zip(chunks, vectors)):
            session.add(
                AIKnowledgeChunk(
                    tenant_id=tenant_id,
                    document_id=doc.id,
                    chunk_index=idx,
                    content=chunk_text,
                    token_estimate=max(1, len(chunk_text) // 4),
                    metadata_json={
                        "title": title,
                        "category": doc.category,
                        "tags": tags or [],
                    },
                    embedding=vector,
                )
            )

        await session.commit()
        await session.refresh(doc)
        return doc

    @classmethod
    async def semantic_search(
        cls,
        session: AsyncSession,
        tenant_id: uuid.UUID,
        query: str,
        category: str | None = None,
        top_k: int = 4,
        min_similarity: float = 0.45,
    ) -> list[dict[str, Any]]:
        vectors = await cls.embed_texts([query], task_type="RETRIEVAL_QUERY")
        query_vector = vectors[0]

        distance_expr = AIKnowledgeChunk.embedding.cosine_distance(query_vector).label("distance")

        filters = [
            AIKnowledgeChunk.tenant_id == tenant_id,
            AIKnowledgeDocument.tenant_id == tenant_id,
            AIKnowledgeDocument.is_active.is_(True),
        ]
        if category:
            filters.append(AIKnowledgeDocument.category == category.strip().lower())

        stmt = (
            select(
                AIKnowledgeChunk.id,
                AIKnowledgeChunk.document_id,
                AIKnowledgeChunk.chunk_index,
                AIKnowledgeChunk.content,
                AIKnowledgeDocument.title.label("document_title"),
                AIKnowledgeDocument.category.label("category"),
                distance_expr,
            )
            .join(AIKnowledgeDocument, AIKnowledgeDocument.id == AIKnowledgeChunk.document_id)
            .where(*filters)
            .order_by(distance_expr.asc())
            .limit(top_k)
        )

        rows = (await session.execute(stmt)).all()
        hits: list[dict[str, Any]] = []
        for row in rows:
            similarity = round(max(0.0, 1.0 - float(row.distance)), 4)
            if similarity >= min_similarity:
                hits.append(
                    {
                        "chunk_id": str(row.id),
                        "document_id": str(row.document_id),
                        "document_title": row.document_title,
                        "category": row.category,
                        "chunk_index": row.chunk_index,
                        "similarity": similarity,
                        "content": row.content,
                    }
                )
        return hits
