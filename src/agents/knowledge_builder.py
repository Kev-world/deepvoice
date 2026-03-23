"""Sub-agent for orchestrating knowledge base builds from repository data."""

import logging
import time
from enum import Enum
from typing import Any

from src.rag.store import KnowledgeStore
from src.rag.indexer import RepositoryIndexer
from src.agents.repo_analyzer import RepoAnalyzer

logger = logging.getLogger(__name__)


class _BuildPhase(str, Enum):
    """Phases of a knowledge base build."""

    IDLE = "idle"
    ANALYZING = "analyzing"
    INDEXING_CODE = "indexing_code"
    INDEXING_DOCS = "indexing_docs"
    INDEXING_GIT = "indexing_git"
    COMPLETE = "complete"
    FAILED = "failed"


class KnowledgeBuilder:
    """Orchestrates full knowledge base builds from repository sources.

    Coordinates the ``RepoAnalyzer`` (for structure/dependency analysis) with
    the ``RepositoryIndexer`` (for embedding code, docs, and git history into
    the vector store).
    """

    def __init__(self) -> None:
        self._analyzer = RepoAnalyzer()
        self._phase: _BuildPhase = _BuildPhase.IDLE
        self._current_repo: str | None = None
        self._started_at: float | None = None
        self._finished_at: float | None = None
        self._last_error: str | None = None
        self._build_stats: dict[str, Any] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def build_from_repo(
        self,
        repo_path: str,
        store: KnowledgeStore,
        indexer: RepositoryIndexer,
    ) -> None:
        """Run a full knowledge base build for a repository.

        The build proceeds through the following stages:
        1. Analyse repository structure and generate a summary document.
        2. Index source code into the ``code`` collection.
        3. Index documentation into the ``docs`` collection.
        4. Index git history into the ``git_history`` collection.

        Progress can be monitored via :meth:`get_build_status`.

        Args:
            repo_path: Path to the repository root.
            store: The :class:`KnowledgeStore` to write into.
            indexer: The :class:`RepositoryIndexer` that handles embedding.
        """
        self._reset_state(repo_path)

        logger.info("Starting full knowledge build for: %s", repo_path)

        # Phase 1 -- Analyse structure and store a summary document.
        try:
            self._phase = _BuildPhase.ANALYZING
            summary = self._analyzer.generate_summary(repo_path)
            structure = self._analyzer.analyze_structure(repo_path)

            summary_doc = {
                "id": f"repo_summary:{repo_path}",
                "content": summary,
                "metadata": {
                    "source": "repo_analyzer",
                    "type": "summary",
                    "path": repo_path,
                },
            }
            store.add_documents([summary_doc], collection="general")

            self._build_stats["languages"] = structure.get("languages", {})
            self._build_stats["frameworks"] = structure.get("frameworks", [])
            self._build_stats["total_files"] = structure.get("total_files", 0)

            logger.info("Repository analysis stored in knowledge base.")
        except Exception as exc:
            logger.exception("Repository analysis failed for %s.", repo_path)
            self._fail(str(exc))
            return

        # Phase 2 -- Index code.
        try:
            self._phase = _BuildPhase.INDEXING_CODE
            await indexer.index_code(repo_path)
            logger.info("Code indexing complete.")
        except Exception as exc:
            logger.exception("Code indexing failed for %s.", repo_path)
            self._fail(str(exc))
            return

        # Phase 3 -- Index docs.
        try:
            self._phase = _BuildPhase.INDEXING_DOCS
            await indexer.index_docs(repo_path)
            logger.info("Docs indexing complete.")
        except Exception as exc:
            logger.exception("Docs indexing failed for %s.", repo_path)
            self._fail(str(exc))
            return

        # Phase 4 -- Index git history.
        try:
            self._phase = _BuildPhase.INDEXING_GIT
            await indexer.index_git_history(repo_path)
            logger.info("Git history indexing complete.")
        except Exception as exc:
            logger.exception("Git history indexing failed for %s.", repo_path)
            self._fail(str(exc))
            return

        self._phase = _BuildPhase.COMPLETE
        self._finished_at = time.monotonic()

        elapsed = self._finished_at - (self._started_at or self._finished_at)
        logger.info(
            "Full knowledge build finished for %s in %.1fs.", repo_path, elapsed
        )

    async def refresh_git_history(
        self,
        repo_path: str,
        store: KnowledgeStore,
        indexer: RepositoryIndexer,
        days: int = 7,
    ) -> None:
        """Re-index only recent git history for a repository.

        This is a lightweight operation suitable for periodic refresh to keep
        the knowledge base up to date with recent commits.

        Args:
            repo_path: Path to the repository root.
            store: The :class:`KnowledgeStore` (unused directly but kept for
                interface consistency).
            indexer: The :class:`RepositoryIndexer` that handles embedding.
            days: Number of days of history to re-index.
        """
        logger.info(
            "Refreshing git history for %s (last %d days).", repo_path, days
        )

        self._current_repo = repo_path
        self._phase = _BuildPhase.INDEXING_GIT
        self._started_at = time.monotonic()
        self._last_error = None

        try:
            await indexer.index_git_history(repo_path, days=days)
            self._phase = _BuildPhase.COMPLETE
            self._finished_at = time.monotonic()

            elapsed = self._finished_at - self._started_at
            logger.info(
                "Git history refresh complete for %s in %.1fs.", repo_path, elapsed
            )
        except Exception as exc:
            logger.exception("Git history refresh failed for %s.", repo_path)
            self._fail(str(exc))

    async def get_build_status(self) -> dict[str, Any]:
        """Return the current status of the knowledge build.

        Returns:
            A dict with keys:
                - phase: Current build phase.
                - repo: The repository being processed (or last processed).
                - elapsed_seconds: Time elapsed since the build started.
                - error: Last error message, if any.
                - stats: Build statistics (languages, frameworks, file counts).
        """
        elapsed: float | None = None
        if self._started_at is not None:
            end = self._finished_at or time.monotonic()
            elapsed = round(end - self._started_at, 2)

        return {
            "phase": self._phase.value,
            "repo": self._current_repo,
            "elapsed_seconds": elapsed,
            "error": self._last_error,
            "stats": dict(self._build_stats),
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _reset_state(self, repo_path: str) -> None:
        """Reset internal tracking state for a new build."""
        self._phase = _BuildPhase.IDLE
        self._current_repo = repo_path
        self._started_at = time.monotonic()
        self._finished_at = None
        self._last_error = None
        self._build_stats = {}

    def _fail(self, message: str) -> None:
        """Record a build failure."""
        self._phase = _BuildPhase.FAILED
        self._finished_at = time.monotonic()
        self._last_error = message
