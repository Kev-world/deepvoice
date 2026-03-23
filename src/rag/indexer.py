"""Repository and documentation indexer for the RAG knowledge base."""

import hashlib
import logging
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from src.rag.store import KnowledgeStore

logger = logging.getLogger(__name__)

# File extensions that should be indexed.
INDEXABLE_EXTENSIONS: set[str] = {
    ".py",
    ".js",
    ".ts",
    ".tsx",
    ".jsx",
    ".go",
    ".rs",
    ".java",
    ".md",
    ".txt",
    ".yaml",
    ".yml",
    ".json",
    ".toml",
    ".cfg",
    ".ini",
    ".sh",
    ".bash",
    ".zsh",
    ".html",
    ".css",
    ".scss",
    ".sql",
    ".graphql",
    ".proto",
    ".dockerfile",
}

# Filenames (without extension) that should be indexed regardless of extension.
INDEXABLE_FILENAMES: set[str] = {
    "Dockerfile",
    "Makefile",
    ".env.example",
}

# Directories to always skip.
SKIP_DIRECTORIES: set[str] = {
    ".git",
    "__pycache__",
    "node_modules",
    ".venv",
    "venv",
    ".tox",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "dist",
    "build",
    ".next",
    ".nuxt",
    "target",
    "vendor",
    ".eggs",
    "*.egg-info",
    ".cache",
}


class RepositoryIndexer:
    """Indexes repository source code, documentation, and git history into the knowledge store."""

    def __init__(self, store: Optional[KnowledgeStore] = None) -> None:
        self._store = store or KnowledgeStore()

    # ------------------------------------------------------------------
    # Public methods
    # ------------------------------------------------------------------

    def index_repository(
        self, repo_path: str, collection: str = "code"
    ) -> None:
        """Walk a repository directory and index all supported source files.

        Args:
            repo_path: Absolute or relative path to the repository root.
            collection: The knowledge store collection to write to.
        """
        repo = Path(repo_path).resolve()
        if not repo.is_dir():
            raise FileNotFoundError(f"Repository path does not exist: {repo}")

        logger.info("Indexing repository at '%s' into collection '%s'.", repo, collection)
        documents: list[dict] = []

        for root, dirs, files in os.walk(repo):
            # Prune directories we want to skip (modifying dirs in-place).
            dirs[:] = [d for d in dirs if d not in SKIP_DIRECTORIES]

            for filename in files:
                filepath = os.path.join(root, filename)
                if not self._should_index_file(filepath):
                    continue

                content = self._read_file_safe(filepath)
                if content is None:
                    continue

                rel_path = os.path.relpath(filepath, repo)
                chunks = self._chunk_text(content)

                for idx, chunk in enumerate(chunks):
                    doc_id = self._make_id(rel_path, idx)
                    documents.append(
                        {
                            "id": doc_id,
                            "content": chunk,
                            "metadata": {
                                "source": str(repo),
                                "type": "code",
                                "path": rel_path,
                                "chunk_index": idx,
                                "indexed_at": datetime.now(timezone.utc).isoformat(),
                            },
                        }
                    )

        if documents:
            self._store.add_documents(documents, collection=collection)
        logger.info(
            "Repository indexing complete. Indexed %d chunks from '%s'.",
            len(documents),
            repo,
        )

    def index_documentation(
        self, docs_path: str, collection: str = "docs"
    ) -> None:
        """Index markdown and text documentation files.

        Args:
            docs_path: Path to a directory containing documentation files.
            collection: The knowledge store collection to write to.
        """
        docs_dir = Path(docs_path).resolve()
        if not docs_dir.is_dir():
            raise FileNotFoundError(f"Documentation path does not exist: {docs_dir}")

        logger.info(
            "Indexing documentation at '%s' into collection '%s'.",
            docs_dir,
            collection,
        )
        documents: list[dict] = []
        doc_extensions = {".md", ".txt", ".rst"}

        for root, dirs, files in os.walk(docs_dir):
            dirs[:] = [d for d in dirs if d not in SKIP_DIRECTORIES]

            for filename in files:
                filepath = os.path.join(root, filename)
                ext = os.path.splitext(filename)[1].lower()
                if ext not in doc_extensions:
                    continue

                content = self._read_file_safe(filepath)
                if content is None:
                    continue

                rel_path = os.path.relpath(filepath, docs_dir)
                chunks = self._chunk_text(content)

                for idx, chunk in enumerate(chunks):
                    doc_id = self._make_id(f"docs/{rel_path}", idx)
                    documents.append(
                        {
                            "id": doc_id,
                            "content": chunk,
                            "metadata": {
                                "source": str(docs_dir),
                                "type": "documentation",
                                "path": rel_path,
                                "chunk_index": idx,
                                "indexed_at": datetime.now(timezone.utc).isoformat(),
                            },
                        }
                    )

        if documents:
            self._store.add_documents(documents, collection=collection)
        logger.info(
            "Documentation indexing complete. Indexed %d chunks from '%s'.",
            len(documents),
            docs_dir,
        )

    def index_git_history(
        self,
        repo_path: str,
        days: int = 30,
        collection: str = "git_history",
    ) -> None:
        """Index recent git commit history including messages, diffs, and author info.

        Args:
            repo_path: Path to a git repository.
            days: Number of days of history to index.
            collection: The knowledge store collection to write to.
        """
        repo = Path(repo_path).resolve()
        if not repo.is_dir():
            raise FileNotFoundError(f"Repository path does not exist: {repo}")

        logger.info(
            "Indexing git history (last %d days) from '%s' into collection '%s'.",
            days,
            repo,
            collection,
        )

        # Fetch commit hashes for the time range.
        commit_hashes = self._git_log_hashes(str(repo), days)
        if not commit_hashes:
            logger.info("No git commits found in the last %d days.", days)
            return

        documents: list[dict] = []

        for commit_hash in commit_hashes:
            commit_info = self._git_commit_detail(str(repo), commit_hash)
            if commit_info is None:
                continue

            # Build a human-readable commit summary.
            content_parts = [
                f"Commit: {commit_info['hash']}",
                f"Author: {commit_info['author']}",
                f"Date: {commit_info['date']}",
                f"Message: {commit_info['message']}",
            ]
            if commit_info["diff"]:
                content_parts.append(f"\nDiff:\n{commit_info['diff']}")

            full_content = "\n".join(content_parts)
            chunks = self._chunk_text(full_content, chunk_size=1500, overlap=200)

            for idx, chunk in enumerate(chunks):
                doc_id = self._make_id(f"git/{commit_hash}", idx)
                documents.append(
                    {
                        "id": doc_id,
                        "content": chunk,
                        "metadata": {
                            "source": str(repo),
                            "type": "git_commit",
                            "path": f"git/{commit_hash}",
                            "author": commit_info["author"],
                            "commit_hash": commit_hash,
                            "commit_date": commit_info["date"],
                            "chunk_index": idx,
                            "indexed_at": datetime.now(timezone.utc).isoformat(),
                        },
                    }
                )

        if documents:
            self._store.add_documents(documents, collection=collection)
        logger.info(
            "Git history indexing complete. Indexed %d chunks from %d commits.",
            len(documents),
            len(commit_hashes),
        )

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _chunk_text(
        self,
        text: str,
        chunk_size: int = 1000,
        overlap: int = 200,
    ) -> list[str]:
        """Split text into overlapping chunks.

        Args:
            text: The text to split.
            chunk_size: Maximum character length of each chunk.
            overlap: Number of overlapping characters between consecutive chunks.

        Returns:
            A list of text chunks.
        """
        if not text:
            return []

        if len(text) <= chunk_size:
            return [text]

        chunks: list[str] = []
        start = 0

        while start < len(text):
            end = start + chunk_size

            # Try to break at a newline boundary to keep logical units together.
            if end < len(text):
                newline_pos = text.rfind("\n", start + chunk_size // 2, end)
                if newline_pos != -1:
                    end = newline_pos + 1

            chunk = text[start:end].strip()
            if chunk:
                chunks.append(chunk)

            start = end - overlap
            # Guard against infinite loops when overlap >= effective chunk size.
            if start <= (end - chunk_size):
                start = end

        return chunks

    def _should_index_file(self, filepath: str) -> bool:
        """Determine whether a file should be indexed.

        Skips binary files, hidden files, and files in excluded directories.

        Args:
            filepath: Absolute path to the file.

        Returns:
            True if the file should be indexed.
        """
        filename = os.path.basename(filepath)

        # Check exact filename matches first.
        if filename in INDEXABLE_FILENAMES:
            return True

        # Check extension.
        ext = os.path.splitext(filename)[1].lower()
        if ext not in INDEXABLE_EXTENSIONS:
            return False

        # Skip hidden files (but not .env.example which is caught above).
        if filename.startswith(".") and filename not in INDEXABLE_FILENAMES:
            return False

        # Skip files in excluded directories (belt-and-suspenders check).
        parts = Path(filepath).parts
        for part in parts:
            if part in SKIP_DIRECTORIES:
                return False

        return True

    @staticmethod
    def _read_file_safe(filepath: str, max_size_bytes: int = 1_000_000) -> Optional[str]:
        """Read a file's text content, returning None on failure or if too large.

        Args:
            filepath: Path to the file.
            max_size_bytes: Skip files larger than this threshold.

        Returns:
            File contents as a string, or None.
        """
        try:
            size = os.path.getsize(filepath)
            if size > max_size_bytes:
                logger.debug("Skipping large file (%d bytes): %s", size, filepath)
                return None
            if size == 0:
                return None

            with open(filepath, "r", encoding="utf-8", errors="replace") as fh:
                return fh.read()
        except (OSError, UnicodeDecodeError) as exc:
            logger.debug("Could not read file '%s': %s", filepath, exc)
            return None

    @staticmethod
    def _make_id(path: str, chunk_index: int) -> str:
        """Create a deterministic document ID from path and chunk index.

        Args:
            path: A relative file path or identifier.
            chunk_index: The zero-based chunk index.

        Returns:
            A hex-digest string suitable for use as a document ID.
        """
        raw = f"{path}::{chunk_index}"
        return hashlib.sha256(raw.encode()).hexdigest()[:16]

    # ------------------------------------------------------------------
    # Git helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _git_log_hashes(repo_path: str, days: int) -> list[str]:
        """Return commit hashes from the last *days* days.

        Args:
            repo_path: Path to the git repository.
            days: How many days of history to include.

        Returns:
            A list of commit hash strings (newest first).
        """
        try:
            result = subprocess.run(
                [
                    "git",
                    "-C",
                    repo_path,
                    "log",
                    f"--since={days} days ago",
                    "--pretty=format:%H",
                ],
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
            )
            if result.returncode != 0:
                logger.error("git log failed: %s", result.stderr.strip())
                return []
            hashes = [h.strip() for h in result.stdout.strip().splitlines() if h.strip()]
            return hashes
        except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
            logger.error("Failed to run git log: %s", exc)
            return []

    @staticmethod
    def _git_commit_detail(repo_path: str, commit_hash: str) -> Optional[dict]:
        """Fetch detailed information for a single commit.

        Args:
            repo_path: Path to the git repository.
            commit_hash: The full SHA of the commit.

        Returns:
            A dict with keys: hash, author, date, message, diff.  None on failure.
        """
        try:
            # Get commit metadata.
            meta_result = subprocess.run(
                [
                    "git",
                    "-C",
                    repo_path,
                    "log",
                    "-1",
                    "--pretty=format:%H%n%an%n%ai%n%s",
                    commit_hash,
                ],
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
            if meta_result.returncode != 0:
                logger.error(
                    "git log for commit %s failed: %s",
                    commit_hash,
                    meta_result.stderr.strip(),
                )
                return None

            lines = meta_result.stdout.strip().splitlines()
            if len(lines) < 4:
                return None

            # Get the diff (limit size to avoid huge diffs).
            diff_result = subprocess.run(
                [
                    "git",
                    "-C",
                    repo_path,
                    "diff",
                    f"{commit_hash}~1",
                    commit_hash,
                    "--stat",
                    "--patch",
                    "--no-color",
                ],
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
            diff_text = diff_result.stdout.strip() if diff_result.returncode == 0 else ""

            # Truncate very large diffs.
            max_diff_chars = 5000
            if len(diff_text) > max_diff_chars:
                diff_text = diff_text[:max_diff_chars] + "\n... [diff truncated]"

            return {
                "hash": lines[0],
                "author": lines[1],
                "date": lines[2],
                "message": lines[3],
                "diff": diff_text,
            }
        except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
            logger.error("Failed to get commit detail for %s: %s", commit_hash, exc)
            return None
