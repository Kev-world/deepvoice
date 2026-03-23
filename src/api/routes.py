"""API route definitions for the DeepVoice backend."""

from __future__ import annotations

import logging
from typing import Any

import httpx
from fastapi import APIRouter, BackgroundTasks, HTTPException, Request

from src.api.models import (
    HealthResponse,
    IndexGitHistoryRequest,
    IndexRepositoryRequest,
    IndexStatsResponse,
    IndexStatusResponse,
    RAGCodeQueryRequest,
    RAGDocsQueryRequest,
    RAGGitQueryRequest,
    RAGQueryRequest,
    RAGQueryResponse,
    VoiceTokenRequest,
)
from src.config import settings
from src.rag.store import KnowledgeStore
from src.rag.indexer import RepositoryIndexer
from src.rag.retriever import KnowledgeRetriever

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_store(request: Request) -> KnowledgeStore:
    """Return the KnowledgeStore stored on app.state, or raise 503."""
    store: KnowledgeStore | None = getattr(request.app.state, "store", None)
    if store is None:
        raise HTTPException(status_code=503, detail="RAG services are not initialized yet.")
    return store


def _get_retriever(request: Request) -> KnowledgeRetriever:
    """Return the KnowledgeRetriever stored on app.state, or raise 503."""
    retriever: KnowledgeRetriever | None = getattr(request.app.state, "retriever", None)
    if retriever is None:
        raise HTTPException(status_code=503, detail="Retriever service is not initialized yet.")
    return retriever


def _get_indexer(request: Request) -> RepositoryIndexer:
    """Return the RepositoryIndexer stored on app.state, or raise 503."""
    indexer: RepositoryIndexer | None = getattr(request.app.state, "indexer", None)
    if indexer is None:
        raise HTTPException(status_code=503, detail="Indexer service is not initialized yet.")
    return indexer


def _build_rag_response(results: list[dict]) -> RAGQueryResponse:
    """Transform raw KnowledgeStore query results into a RAGQueryResponse.

    Concatenates the content from all retrieved documents into a single
    ``answer_context`` string and exposes metadata as ``sources``.
    """
    if not results:
        return RAGQueryResponse(answer_context="", sources=[])

    context_parts: list[str] = []
    sources: list[dict] = []

    for doc in results:
        content = doc.get("content", "")
        metadata = doc.get("metadata", {})
        context_parts.append(content)
        sources.append({
            "id": doc.get("id", ""),
            "content": content,
            "metadata": metadata,
            "distance": doc.get("distance"),
        })

    answer_context = "\n\n---\n\n".join(context_parts)
    return RAGQueryResponse(answer_context=answer_context, sources=sources)


# ---------------------------------------------------------------------------
# RAG Query Endpoints
# ---------------------------------------------------------------------------

@router.post("/rag/query", response_model=RAGQueryResponse)
async def rag_query(body: RAGQueryRequest, request: Request) -> RAGQueryResponse:
    """Query the knowledge base across all or a specific collection."""
    store = _get_store(request)
    try:
        if body.collection:
            results = store.query(
                query_text=body.query,
                n_results=body.n_results,
                collection=body.collection,
            )
        else:
            # Search across all collections and merge results
            all_results: list[dict] = []
            for col_name in ("code", "docs", "git_history"):
                try:
                    col_results = store.query(
                        query_text=body.query,
                        n_results=body.n_results,
                        collection=col_name,
                    )
                    all_results.extend(col_results)
                except Exception:
                    logger.warning("Failed to query collection '%s'", col_name)
            # Sort by distance (lower = more relevant) and take top N
            all_results.sort(key=lambda r: r.get("distance") or float("inf"))
            results = all_results[:body.n_results]
        return _build_rag_response(results)
    except Exception:
        logger.exception("Error processing RAG query")
        raise HTTPException(status_code=500, detail="Failed to process query.")


@router.post("/rag/query/code", response_model=RAGQueryResponse)
async def rag_query_code(body: RAGCodeQueryRequest, request: Request) -> RAGQueryResponse:
    """Query the code knowledge base specifically."""
    store = _get_store(request)
    try:
        results = store.query(
            query_text=body.query,
            n_results=body.n_results,
            collection="code",
        )
        return _build_rag_response(results)
    except Exception:
        logger.exception("Error processing code query")
        raise HTTPException(status_code=500, detail="Failed to process code query.")


@router.post("/rag/query/git", response_model=RAGQueryResponse)
async def rag_query_git(body: RAGGitQueryRequest, request: Request) -> RAGQueryResponse:
    """Query git history (e.g. 'what did you do yesterday')."""
    store = _get_store(request)
    try:
        results = store.query(
            query_text=body.query,
            n_results=body.n_results,
            collection="git_history",
        )
        return _build_rag_response(results)
    except Exception:
        logger.exception("Error processing git history query")
        raise HTTPException(status_code=500, detail="Failed to process git history query.")


@router.post("/rag/query/docs", response_model=RAGQueryResponse)
async def rag_query_docs(body: RAGDocsQueryRequest, request: Request) -> RAGQueryResponse:
    """Query the documentation knowledge base."""
    store = _get_store(request)
    try:
        results = store.query(
            query_text=body.query,
            n_results=body.n_results,
            collection="docs",
        )
        return _build_rag_response(results)
    except Exception:
        logger.exception("Error processing docs query")
        raise HTTPException(status_code=500, detail="Failed to process documentation query.")


# ---------------------------------------------------------------------------
# Index Management Endpoints
# ---------------------------------------------------------------------------

def _run_index_repository(indexer: RepositoryIndexer, repo_path: str, collection: str) -> None:
    """Background task: index a repository into the knowledge store."""
    try:
        logger.info("Starting repository indexing: %s -> %s", repo_path, collection)
        indexer.index_repository(repo_path=repo_path, collection=collection)
        logger.info("Finished repository indexing: %s -> %s", repo_path, collection)
    except Exception:
        logger.exception("Repository indexing failed: %s", repo_path)


def _run_index_git_history(indexer: RepositoryIndexer, repo_path: str, days: int) -> None:
    """Background task: index git history into the knowledge store."""
    try:
        logger.info("Starting git history indexing: %s (last %d days)", repo_path, days)
        indexer.index_git_history(repo_path=repo_path, days=days)
        logger.info("Finished git history indexing: %s", repo_path)
    except Exception:
        logger.exception("Git history indexing failed: %s", repo_path)


@router.post("/index/repository", response_model=IndexStatusResponse)
async def index_repository(
    body: IndexRepositoryRequest,
    request: Request,
    background_tasks: BackgroundTasks,
) -> IndexStatusResponse:
    """Kick off background indexing of a code repository."""
    indexer = _get_indexer(request)
    background_tasks.add_task(_run_index_repository, indexer, body.repo_path, body.collection)
    return IndexStatusResponse(
        status="indexing",
        message=f"Indexing started for repository '{body.repo_path}' into collection '{body.collection}'.",
    )


@router.post("/index/git-history", response_model=IndexStatusResponse)
async def index_git_history(
    body: IndexGitHistoryRequest,
    request: Request,
    background_tasks: BackgroundTasks,
) -> IndexStatusResponse:
    """Kick off background indexing of git commit history."""
    indexer = _get_indexer(request)
    background_tasks.add_task(_run_index_git_history, indexer, body.repo_path, body.days)
    return IndexStatusResponse(
        status="indexing",
        message=f"Git history indexing started for '{body.repo_path}' (last {body.days} days).",
    )


@router.get("/index/stats", response_model=IndexStatsResponse)
async def index_stats(request: Request) -> IndexStatsResponse:
    """Return statistics about indexed collections."""
    store = _get_store(request)
    try:
        stats = store.get_stats()
        total = sum(stats.values())
        return IndexStatsResponse(
            collections=stats,
            total_documents=total,
        )
    except Exception:
        logger.exception("Error retrieving index stats")
        raise HTTPException(status_code=500, detail="Failed to retrieve index statistics.")


# ---------------------------------------------------------------------------
# Voice Token Endpoint
# ---------------------------------------------------------------------------

VOCAL_BRIDGE_TOKEN_URL = "https://vocalbridgeai.com/api/v1/token"


@router.post("/voice-token")
async def generate_voice_token(body: VoiceTokenRequest) -> dict:
    """Generate a LiveKit token by calling the Vocal Bridge API."""
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                VOCAL_BRIDGE_TOKEN_URL,
                headers={
                    "Authorization": f"Bearer {settings.vocal_bridge_api_key}",
                    "Content-Type": "application/json",
                },
                json={"participant_name": body.participant_name},
            )
            response.raise_for_status()
            return response.json()
    except httpx.HTTPStatusError as exc:
        logger.error(
            "Vocal Bridge API returned %s: %s",
            exc.response.status_code,
            exc.response.text,
        )
        raise HTTPException(
            status_code=exc.response.status_code,
            detail=f"Vocal Bridge API error: {exc.response.text}",
        )
    except httpx.RequestError as exc:
        logger.exception("Failed to reach Vocal Bridge API")
        raise HTTPException(
            status_code=502,
            detail=f"Could not reach Vocal Bridge API: {exc}",
        )


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

@router.get("/health", response_model=HealthResponse)
async def health_check() -> HealthResponse:
    """Simple liveness probe."""
    return HealthResponse(status="ok")
