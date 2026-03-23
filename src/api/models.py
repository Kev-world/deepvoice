"""Pydantic request and response models for the DeepVoice API."""

from __future__ import annotations

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# RAG Query
# ---------------------------------------------------------------------------

class RAGQueryRequest(BaseModel):
    """Generic RAG query request."""

    query: str = Field(..., description="Natural-language query to run against the knowledge base.")
    n_results: int = Field(default=5, ge=1, le=50, description="Number of results to retrieve.")
    collection: str | None = Field(default=None, description="Optional collection name to search within.")


class RAGCodeQueryRequest(BaseModel):
    """Query specifically targeting code knowledge."""

    query: str = Field(..., description="Natural-language query about code.")
    n_results: int = Field(default=5, ge=1, le=50)


class RAGGitQueryRequest(BaseModel):
    """Query targeting git history (e.g. 'what did you do yesterday')."""

    query: str = Field(..., description="Natural-language query about git history.")
    n_results: int = Field(default=10, ge=1, le=100)


class RAGDocsQueryRequest(BaseModel):
    """Query targeting documentation."""

    query: str = Field(..., description="Natural-language query about documentation.")
    n_results: int = Field(default=5, ge=1, le=50)


class SourceDocument(BaseModel):
    """A single source document returned by the retriever."""

    content: str
    metadata: dict


class RAGQueryResponse(BaseModel):
    """Response from any RAG query endpoint."""

    answer_context: str = Field(..., description="Concatenated context suitable for answering the query.")
    sources: list[dict] = Field(default_factory=list, description="Source documents with metadata.")


# ---------------------------------------------------------------------------
# Index Management
# ---------------------------------------------------------------------------

class IndexRepositoryRequest(BaseModel):
    """Request to index a code repository."""

    repo_path: str = Field(..., description="Absolute or relative path to the repository.")
    collection: str = Field(default="code", description="Target collection name.")


class IndexGitHistoryRequest(BaseModel):
    """Request to index git commit history."""

    repo_path: str = Field(..., description="Absolute or relative path to the repository.")
    days: int = Field(default=30, ge=1, le=365, description="Number of days of history to index.")


class IndexStatusResponse(BaseModel):
    """Response after starting an indexing job."""

    status: str = "indexing"
    message: str


class IndexUrlRequest(BaseModel):
    """Request to index one or more web URLs."""

    urls: list[str] = Field(..., description="List of URLs to index")
    collection: str = Field(default="docs", description="Target collection")


class IndexSitemapRequest(BaseModel):
    """Request to index all pages from a sitemap.xml URL."""

    sitemap_url: str = Field(..., description="URL of the sitemap.xml")
    collection: str = Field(default="docs", description="Target collection")
    max_pages: int = Field(default=50, ge=1, le=200, description="Max pages to index")


class IndexStatsResponse(BaseModel):
    """Statistics about indexed collections."""

    collections: dict = Field(default_factory=dict, description="Per-collection document counts.")
    total_documents: int = 0


# ---------------------------------------------------------------------------
# Voice Token
# ---------------------------------------------------------------------------

class VoiceTokenRequest(BaseModel):
    """Request a LiveKit token via Vocal Bridge."""

    participant_name: str = Field(default="User", description="Display name of the participant.")


class VoiceTokenResponse(BaseModel):
    """Proxied response from the Vocal Bridge token endpoint.

    The exact shape depends on the Vocal Bridge API; we forward the full
    payload so callers always get the latest fields.
    """

    # We store the raw payload so we are not tightly coupled to upstream schema changes.
    livekit_url: str | None = None
    token: str | None = None
    room_name: str | None = None

    class Config:
        extra = "allow"


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

class HealthResponse(BaseModel):
    """Simple health-check response."""

    status: str = "ok"
