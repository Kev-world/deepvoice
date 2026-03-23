"""High-level retrieval interface for the RAG knowledge base."""

import logging
from typing import Optional

from src.rag.store import KnowledgeStore

logger = logging.getLogger(__name__)


class KnowledgeRetriever:
    """Wraps KnowledgeStore to provide convenient, formatted retrieval methods.

    Each public method queries the relevant collection(s) and returns a
    human-readable context string that can be injected into an LLM prompt.
    """

    def __init__(self, store: Optional[KnowledgeStore] = None) -> None:
        self._store = store or KnowledgeStore()

    # ------------------------------------------------------------------
    # Public retrieval methods
    # ------------------------------------------------------------------

    def retrieve(self, question: str, n_results: int = 5) -> str:
        """Query all relevant collections and return a merged context string.

        Searches code, docs, and git_history collections, deduplicates, and
        returns the top results sorted by relevance (lowest distance first).

        Args:
            question: The natural-language question to answer.
            n_results: Maximum number of results *per collection*.

        Returns:
            A formatted context string with source citations.
        """
        all_results: list[dict] = []

        for collection in ("code", "docs", "git_history"):
            try:
                results = self._store.query(
                    query_text=question,
                    n_results=n_results,
                    collection=collection,
                )
                # Tag each result with its source collection.
                for r in results:
                    r["collection"] = collection
                all_results.extend(results)
            except Exception:
                logger.exception(
                    "Error querying collection '%s'; skipping.", collection
                )

        # Sort by distance (ascending -- lower is more relevant).
        all_results.sort(key=lambda r: r.get("distance") or float("inf"))

        # Keep only the top n_results overall.
        top_results = all_results[:n_results]

        context = self.format_context(top_results)
        logger.debug(
            "retrieve() returned %d results for question: %.80s...",
            len(top_results),
            question,
        )
        return context

    def retrieve_code(self, question: str, n_results: int = 5) -> str:
        """Query the code collection and return formatted context.

        Args:
            question: The natural-language question.
            n_results: Maximum number of results.

        Returns:
            Formatted context string.
        """
        results = self._store.query(
            query_text=question,
            n_results=n_results,
            collection="code",
        )
        for r in results:
            r["collection"] = "code"
        return self.format_context(results)

    def retrieve_docs(self, question: str, n_results: int = 5) -> str:
        """Query the docs collection and return formatted context.

        Args:
            question: The natural-language question.
            n_results: Maximum number of results.

        Returns:
            Formatted context string.
        """
        results = self._store.query(
            query_text=question,
            n_results=n_results,
            collection="docs",
        )
        for r in results:
            r["collection"] = "docs"
        return self.format_context(results)

    def retrieve_git_history(self, question: str, n_results: int = 5) -> str:
        """Query the git_history collection and return formatted context.

        Args:
            question: The natural-language question.
            n_results: Maximum number of results.

        Returns:
            Formatted context string.
        """
        results = self._store.query(
            query_text=question,
            n_results=n_results,
            collection="git_history",
        )
        for r in results:
            r["collection"] = "git_history"
        return self.format_context(results)

    # ------------------------------------------------------------------
    # Formatting
    # ------------------------------------------------------------------

    @staticmethod
    def format_context(results: list[dict]) -> str:
        """Format a list of retrieved documents into a readable context string.

        Each entry includes source citation information (collection, file path,
        and relevance distance) followed by the document content.

        Args:
            results: A list of result dicts as returned by KnowledgeStore.query,
                     optionally augmented with a 'collection' key.

        Returns:
            A multi-line string suitable for inclusion in a prompt. Returns a
            short message if no results are available.
        """
        if not results:
            return "No relevant context found."

        sections: list[str] = []

        for idx, result in enumerate(results, start=1):
            metadata = result.get("metadata", {})
            collection = result.get("collection", metadata.get("type", "unknown"))
            path = metadata.get("path", "unknown")
            distance = result.get("distance")
            content = result.get("content", "").strip()

            header_parts = [
                f"[{idx}]",
                f"Source: {collection}",
                f"Path: {path}",
            ]
            if distance is not None:
                header_parts.append(f"Relevance: {1.0 - distance:.2f}")

            header = " | ".join(header_parts)
            sections.append(f"{header}\n{content}")

        return "\n\n---\n\n".join(sections)
