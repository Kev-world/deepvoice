"""FastAPI application entry-point for DeepVoice."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator

import uvicorn
from pathlib import Path
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from src.config import settings
from src.api.routes import router
from src.api.meet_routes import router as meet_router
from src.bridge.bridge_routes import router as bridge_router
from src.rag.embeddings import EmbeddingService
from src.rag.store import KnowledgeStore
from src.rag.indexer import RepositoryIndexer
from src.rag.retriever import KnowledgeRetriever
from src.rag.web_indexer import WebIndexer

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Initialise RAG services on startup and tear down on shutdown."""
    logging.basicConfig(
        level=settings.log_level.upper(),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    logger.info("Initializing RAG services...")
    try:
        embedding_service = EmbeddingService(model_name=settings.embedding_model)
        store = KnowledgeStore(
            persist_directory=settings.chromadb_path,
            embedding_service=embedding_service,
        )
        indexer = RepositoryIndexer(store=store)
        retriever = KnowledgeRetriever(store=store)

        app.state.store = store
        app.state.indexer = indexer
        app.state.retriever = retriever
        web_indexer = WebIndexer(store=store)
        app.state.web_indexer = web_indexer
        logger.info("RAG services initialized successfully.")
    except Exception:
        logger.exception("Failed to initialize RAG services — endpoints will return 503.")

    yield

    # Shutdown: release resources if needed.
    logger.info("Shutting down DeepVoice application.")


app = FastAPI(
    title="DeepVoice",
    description="Voice AI meeting agent with RAG-powered knowledge base.",
    version="0.1.0",
    lifespan=lifespan,
)

# -- Middleware ----------------------------------------------------------------
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# -- Routes --------------------------------------------------------------------
app.include_router(router)
app.include_router(meet_router)
app.include_router(bridge_router)

# -- Static files & UI ---------------------------------------------------------
_static_dir = Path(__file__).resolve().parent.parent / "static"
if _static_dir.is_dir():

    @app.get("/")
    async def serve_ui():
        return FileResponse(str(_static_dir / "lobby.html"))

    @app.get("/meet.html")
    async def serve_meet_html():
        return FileResponse(str(_static_dir / "meet.html"))

    @app.get("/meet")
    async def serve_meet():
        return FileResponse(str(_static_dir / "meet.html"))

    @app.get("/lobby.html")
    async def serve_lobby_html():
        return FileResponse(str(_static_dir / "lobby.html"))

    @app.get("/index.html")
    async def serve_index_html():
        return FileResponse(str(_static_dir / "index.html"))

    # Mount static files last so explicit routes above take priority.
    app.mount("/static", StaticFiles(directory=str(_static_dir)), name="static")


# -- Runner --------------------------------------------------------------------
if __name__ == "__main__":
    uvicorn.run(
        "src.main:app",
        host=settings.host,
        port=settings.port,
        log_level=settings.log_level,
        reload=True,
    )
