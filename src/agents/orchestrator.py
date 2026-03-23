"""Main meeting orchestrator that coordinates RAG, indexing, and sub-agents."""

import logging
from typing import Any

from src.config import Settings
from src.rag.store import KnowledgeStore
from src.rag.retriever import KnowledgeRetriever
from src.rag.indexer import RepositoryIndexer

logger = logging.getLogger(__name__)

# Mapping of query types to the ChromaDB collections they should search.
_QUERY_TYPE_TO_COLLECTION: dict[str, str] = {
    "code": "code",
    "docs": "docs",
    "git": "git_history",
    "git_history": "git_history",
    "general": "general",
}

# Maximum number of context results per retrieval query.
_DEFAULT_N_RESULTS = 5


class MeetingOrchestrator:
    """Coordinates between the Vocal Bridge agent, RAG system, and sub-agents.

    Typical lifecycle::

        orchestrator = MeetingOrchestrator(settings)
        await orchestrator.initialize(repo_paths=["/path/to/repo"])
        answer = await orchestrator.handle_query("How does auth work?", query_type="code")
    """

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or Settings()
        self._store: KnowledgeStore | None = None
        self._retriever: KnowledgeRetriever | None = None
        self._indexer: RepositoryIndexer | None = None
        self._initialized: bool = False
        self._indexed_repos: list[str] = []

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def initialize(
        self,
        repo_paths: list[str] | None = None,
    ) -> None:
        """Set up RAG stores and optionally index one or more repositories.

        Args:
            repo_paths: Optional list of repository paths to index during
                initialisation.  Each repository will have its code, docs,
                and git history indexed.
        """
        logger.info("Initializing MeetingOrchestrator...")

        try:
            self._store = KnowledgeStore(
                persist_directory=self._settings.chromadb_path,
            )
            self._retriever = KnowledgeRetriever(store=self._store)
            self._indexer = RepositoryIndexer(store=self._store)
        except Exception:
            logger.exception("Failed to initialize RAG components.")
            raise

        self._initialized = True
        logger.info("RAG components initialized successfully.")

        if repo_paths:
            for repo_path in repo_paths:
                await self.index_repository(repo_path)

    # ------------------------------------------------------------------
    # Query handling
    # ------------------------------------------------------------------

    async def handle_query(
        self,
        query: str,
        query_type: str = "general",
    ) -> str:
        """Route a natural-language query to the appropriate RAG retriever.

        Args:
            query: The user's question or search text.
            query_type: One of ``"code"``, ``"docs"``, ``"git"``,
                ``"git_history"``, or ``"general"``.  Controls which
                collection is searched.

        Returns:
            A formatted string containing the retrieved context, suitable for
            inclusion in a prompt or direct display.

        Raises:
            RuntimeError: If the orchestrator has not been initialised.
            ValueError: If *query_type* is not recognised.
        """
        self._ensure_initialized()

        collection = _QUERY_TYPE_TO_COLLECTION.get(query_type)
        if collection is None:
            raise ValueError(
                f"Unknown query_type '{query_type}'. "
                f"Must be one of: {', '.join(_QUERY_TYPE_TO_COLLECTION)}"
            )

        logger.info(
            "Handling query (type='%s', collection='%s'): %.120s",
            query_type,
            collection,
            query,
        )

        try:
            assert self._retriever is not None
            if collection == "code":
                context = self._retriever.retrieve_code(query, n_results=_DEFAULT_N_RESULTS)
            elif collection == "docs":
                context = self._retriever.retrieve_docs(query, n_results=_DEFAULT_N_RESULTS)
            elif collection == "git_history":
                context = self._retriever.retrieve_git_history(query, n_results=_DEFAULT_N_RESULTS)
            else:
                context = self._retriever.retrieve(query, n_results=_DEFAULT_N_RESULTS)
        except Exception:
            logger.exception("Retrieval failed for query: %.120s", query)
            return "I was unable to retrieve relevant information. Please try again."

        if context == "No relevant context found.":
            logger.info("No results found for query: %.120s", query)
            return "No relevant information was found in the knowledge base."

        return context

    # ------------------------------------------------------------------
    # Indexing
    # ------------------------------------------------------------------

    async def index_repository(self, repo_path: str) -> None:
        """Trigger full indexing of a repository (code, docs, and git history).

        Args:
            repo_path: Absolute or relative path to the repository root.

        Raises:
            RuntimeError: If the orchestrator has not been initialised.
        """
        self._ensure_initialized()

        logger.info("Starting full index of repository: %s", repo_path)

        assert self._indexer is not None
        try:
            self._indexer.index_repository(repo_path, collection="code")
            logger.info("Code indexing complete for %s.", repo_path)
        except Exception:
            logger.exception("Code indexing failed for %s.", repo_path)

        try:
            self._indexer.index_documentation(repo_path, collection="docs")
            logger.info("Docs indexing complete for %s.", repo_path)
        except Exception:
            logger.exception("Docs indexing failed for %s.", repo_path)

        try:
            await self._indexer.index_git_history(repo_path)
            logger.info("Git history indexing complete for %s.", repo_path)
        except Exception:
            logger.exception("Git history indexing failed for %s.", repo_path)

        if repo_path not in self._indexed_repos:
            self._indexed_repos.append(repo_path)

        logger.info("Repository indexing finished: %s", repo_path)

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

    async def get_status(self) -> dict[str, Any]:
        """Return the status of all sub-systems.

        Returns:
            A dict with keys:
                - initialized: Whether the orchestrator is ready.
                - store_stats: Per-collection document counts from KnowledgeStore.
                - indexed_repos: List of repositories that have been indexed.
                - indexer_ready: Whether the RepositoryIndexer is available.
                - retriever_ready: Whether the KnowledgeRetriever is available.
        """
        store_stats: dict[str, int] = {}
        if self._store is not None:
            try:
                store_stats = self._store.get_stats()
            except Exception:
                logger.exception("Failed to retrieve store stats.")

        return {
            "initialized": self._initialized,
            "store_stats": store_stats,
            "indexed_repos": list(self._indexed_repos),
            "indexer_ready": self._indexer is not None,
            "retriever_ready": self._retriever is not None,
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _ensure_initialized(self) -> None:
        """Raise if the orchestrator has not been initialised yet."""
        if not self._initialized:
            raise RuntimeError(
                "MeetingOrchestrator has not been initialized. "
                "Call `await orchestrator.initialize()` first."
            )

    @staticmethod
    def _format_results(results: list[dict[str, Any]], query_type: str) -> str:
        """Format retrieval results into a human-readable context string.

        Args:
            results: List of result dicts from the retriever.
            query_type: The original query type (used for labelling).

        Returns:
            A formatted multi-line string.
        """
        lines: list[str] = [
            f"Found {len(results)} relevant result(s) [{query_type}]:\n"
        ]
        for i, result in enumerate(results, start=1):
            metadata = result.get("metadata", {})
            source = metadata.get("source", "unknown")
            path = metadata.get("path", "")
            content = result.get("content", "").strip()

            header = f"--- Result {i} (source: {source}"
            if path:
                header += f", path: {path}"
            header += ") ---"

            lines.append(header)
            lines.append(content)
            lines.append("")

        return "\n".join(lines)
