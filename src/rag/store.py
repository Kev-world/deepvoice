"""Knowledge store backed by ChromaDB with persistent storage."""

import logging
from datetime import datetime, timezone
from typing import Optional

import chromadb
from chromadb import EmbeddingFunction, Documents, Embeddings

from src.rag.embeddings import EmbeddingService

logger = logging.getLogger(__name__)

VALID_COLLECTIONS = ("code", "docs", "git_history", "general")
DEFAULT_PERSIST_DIR = "./data/chromadb"


class _SentenceTransformerEmbeddingFunction(EmbeddingFunction):
    """Adapter that exposes EmbeddingService through ChromaDB's embedding function interface."""

    def __init__(self, embedding_service: EmbeddingService) -> None:
        self._service = embedding_service

    def __call__(self, input: Documents) -> Embeddings:
        return self._service.embed_texts(input)


class KnowledgeStore:
    """Persistent vector store for RAG knowledge, backed by ChromaDB.

    Supports multiple named collections (code, docs, git_history, general) and
    uses sentence-transformers embeddings via the EmbeddingService.
    """

    def __init__(
        self,
        persist_directory: str = DEFAULT_PERSIST_DIR,
        embedding_service: Optional[EmbeddingService] = None,
    ) -> None:
        self._persist_directory = persist_directory
        self._embedding_service = embedding_service or EmbeddingService()
        self._embedding_function = _SentenceTransformerEmbeddingFunction(
            self._embedding_service
        )

        logger.info(
            "Initializing ChromaDB with persistent storage at '%s'.",
            persist_directory,
        )
        self._client = chromadb.PersistentClient(path=persist_directory)

    def _get_collection(self, name: str) -> chromadb.Collection:
        """Get or create a ChromaDB collection by name.

        Args:
            name: The collection name. Must be one of the valid collection names.

        Returns:
            A ChromaDB Collection object.

        Raises:
            ValueError: If the collection name is not in the allowed set.
        """
        if name not in VALID_COLLECTIONS:
            raise ValueError(
                f"Invalid collection name '{name}'. "
                f"Must be one of: {', '.join(VALID_COLLECTIONS)}"
            )
        return self._client.get_or_create_collection(
            name=name,
            embedding_function=self._embedding_function,
        )

    def add_documents(self, documents: list[dict], collection: str = "general") -> None:
        """Add documents to a collection.

        Each document dict must contain:
            - id: Unique identifier for the document.
            - content: The text content to store and embed.
            - metadata: A dict with keys: source, type, path, indexed_at.

        Args:
            documents: A list of document dicts to add.
            collection: The target collection name.
        """
        if not documents:
            logger.warning("No documents provided to add_documents; skipping.")
            return

        col = self._get_collection(collection)

        ids: list[str] = []
        contents: list[str] = []
        metadatas: list[dict] = []

        for doc in documents:
            doc_id = doc["id"]
            content = doc["content"]
            metadata = doc.get("metadata", {})

            # Ensure indexed_at is present.
            if "indexed_at" not in metadata:
                metadata["indexed_at"] = datetime.now(timezone.utc).isoformat()

            ids.append(doc_id)
            contents.append(content)
            metadatas.append(metadata)

        # ChromaDB supports batched upserts.
        logger.info(
            "Adding %d documents to collection '%s'.", len(ids), collection
        )
        col.upsert(ids=ids, documents=contents, metadatas=metadatas)
        logger.info("Successfully added %d documents.", len(ids))

    def query(
        self,
        query_text: str,
        n_results: int = 5,
        filter_type: Optional[str] = None,
        collection: str = "general",
    ) -> list[dict]:
        """Query a collection for documents similar to the query text.

        Args:
            query_text: The natural-language query.
            n_results: Maximum number of results to return.
            filter_type: Optional metadata 'type' value to filter on.
            collection: The collection to query.

        Returns:
            A list of dicts, each containing id, content, metadata, and distance.
        """
        col = self._get_collection(collection)

        where_filter: Optional[dict] = None
        if filter_type is not None:
            where_filter = {"type": filter_type}

        logger.debug(
            "Querying collection '%s' with n_results=%d, filter_type=%s.",
            collection,
            n_results,
            filter_type,
        )

        results = col.query(
            query_texts=[query_text],
            n_results=n_results,
            where=where_filter,
        )

        documents: list[dict] = []
        if results and results["ids"] and results["ids"][0]:
            for i, doc_id in enumerate(results["ids"][0]):
                doc: dict = {
                    "id": doc_id,
                    "content": (
                        results["documents"][0][i] if results["documents"] else ""
                    ),
                    "metadata": (
                        results["metadatas"][0][i] if results["metadatas"] else {}
                    ),
                    "distance": (
                        results["distances"][0][i] if results["distances"] else None
                    ),
                }
                documents.append(doc)

        logger.debug("Query returned %d results.", len(documents))
        return documents

    def delete_collection(self, name: str) -> None:
        """Delete a collection by name.

        Args:
            name: The collection to delete.
        """
        logger.info("Deleting collection '%s'.", name)
        try:
            self._client.delete_collection(name=name)
            logger.info("Collection '%s' deleted.", name)
        except ValueError:
            logger.warning("Collection '%s' does not exist; nothing to delete.", name)

    def list_collections(self) -> list[str]:
        """List the names of all existing collections.

        Returns:
            A list of collection name strings.
        """
        collections = self._client.list_collections()
        names = [col.name for col in collections]
        logger.debug("Found %d collections: %s", len(names), names)
        return names

    def get_stats(self) -> dict:
        """Return document counts per collection.

        Returns:
            A dict mapping collection names to their document counts.
        """
        stats: dict[str, int] = {}
        for name in VALID_COLLECTIONS:
            try:
                col = self._client.get_or_create_collection(
                    name=name,
                    embedding_function=self._embedding_function,
                )
                stats[name] = col.count()
            except Exception:
                logger.exception(
                    "Failed to get stats for collection '%s'.", name
                )
                stats[name] = 0

        logger.info("Knowledge store stats: %s", stats)
        return stats
