#!/usr/bin/env python3
"""CLI script to index a repository into the DeepVoice RAG knowledge base.

Usage:
    python scripts/index_repo.py <repo_path> [--collection code|docs|git_history] [--days 30]

Examples:
    # Index all collections (code, docs, git_history) from a repo
    python scripts/index_repo.py /path/to/repo

    # Index only code files
    python scripts/index_repo.py /path/to/repo --collection code

    # Index git history from the last 14 days
    python scripts/index_repo.py /path/to/repo --collection git_history --days 14
"""

import argparse
import hashlib
import logging
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Ensure the project root is on sys.path so `src` is importable.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from git import Repo as GitRepo  # noqa: E402

from src.rag.store import KnowledgeStore  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# File extensions to index as code
CODE_EXTENSIONS = {
    ".py", ".js", ".ts", ".tsx", ".jsx", ".go", ".rs", ".java",
    ".c", ".cpp", ".h", ".hpp", ".rb", ".php", ".swift", ".kt",
    ".scala", ".sh", ".bash", ".zsh", ".sql", ".yaml", ".yml",
    ".toml", ".json", ".html", ".css", ".scss",
}

# File extensions to index as documentation
DOC_EXTENSIONS = {".md", ".rst", ".txt", ".adoc"}

# Directories to skip when walking the file tree
SKIP_DIRS = {
    ".git", "__pycache__", "node_modules", ".venv", "venv",
    "dist", "build", ".eggs", ".tox", ".mypy_cache",
    ".pytest_cache", ".ruff_cache", "data",
}

# Maximum file size to index (256 KB)
MAX_FILE_SIZE = 256 * 1024


def _file_id(repo_path: str, file_path: str) -> str:
    """Generate a deterministic document ID from repo and file path."""
    relative = os.path.relpath(file_path, repo_path)
    return hashlib.sha256(f"{repo_path}:{relative}".encode()).hexdigest()[:16]


def _commit_id(repo_path: str, commit_hex: str) -> str:
    """Generate a deterministic document ID for a git commit."""
    return hashlib.sha256(f"{repo_path}:commit:{commit_hex}".encode()).hexdigest()[:16]


def index_code(repo_path: str, store: KnowledgeStore) -> int:
    """Walk the repo and index source code files into the 'code' collection."""
    print(f"[code] Scanning {repo_path} for source files...")
    documents: list[dict] = []

    for root, dirs, files in os.walk(repo_path):
        # Prune directories we don't want to descend into
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]

        for filename in files:
            ext = os.path.splitext(filename)[1].lower()
            if ext not in CODE_EXTENSIONS:
                continue

            filepath = os.path.join(root, filename)

            # Skip files that are too large
            try:
                if os.path.getsize(filepath) > MAX_FILE_SIZE:
                    continue
            except OSError:
                continue

            try:
                with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
                    content = f.read()
            except (OSError, UnicodeDecodeError):
                continue

            if not content.strip():
                continue

            relative_path = os.path.relpath(filepath, repo_path)
            doc = {
                "id": _file_id(repo_path, filepath),
                "content": f"File: {relative_path}\n\n{content}",
                "metadata": {
                    "source": os.path.basename(repo_path),
                    "type": "code",
                    "path": relative_path,
                    "language": ext.lstrip("."),
                    "indexed_at": datetime.now(timezone.utc).isoformat(),
                },
            }
            documents.append(doc)

    if documents:
        print(f"[code] Indexing {len(documents)} source files...")
        store.add_documents(documents, collection="code")
    else:
        print("[code] No source files found.")

    print(f"[code] Done. Indexed {len(documents)} files.")
    return len(documents)


def index_docs(repo_path: str, store: KnowledgeStore) -> int:
    """Walk the repo and index documentation files into the 'docs' collection."""
    print(f"[docs] Scanning {repo_path} for documentation files...")
    documents: list[dict] = []

    for root, dirs, files in os.walk(repo_path):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]

        for filename in files:
            ext = os.path.splitext(filename)[1].lower()
            if ext not in DOC_EXTENSIONS:
                continue

            filepath = os.path.join(root, filename)

            try:
                if os.path.getsize(filepath) > MAX_FILE_SIZE:
                    continue
            except OSError:
                continue

            try:
                with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
                    content = f.read()
            except (OSError, UnicodeDecodeError):
                continue

            if not content.strip():
                continue

            relative_path = os.path.relpath(filepath, repo_path)
            doc = {
                "id": _file_id(repo_path, filepath),
                "content": f"Document: {relative_path}\n\n{content}",
                "metadata": {
                    "source": os.path.basename(repo_path),
                    "type": "docs",
                    "path": relative_path,
                    "indexed_at": datetime.now(timezone.utc).isoformat(),
                },
            }
            documents.append(doc)

    if documents:
        print(f"[docs] Indexing {len(documents)} documentation files...")
        store.add_documents(documents, collection="docs")
    else:
        print("[docs] No documentation files found.")

    print(f"[docs] Done. Indexed {len(documents)} files.")
    return len(documents)


def index_git_history(repo_path: str, store: KnowledgeStore, days: int = 30) -> int:
    """Index recent git commit history into the 'git_history' collection."""
    print(f"[git_history] Reading git history from the last {days} days...")

    try:
        repo = GitRepo(repo_path)
    except Exception as exc:
        print(f"[git_history] Error opening git repo: {exc}")
        return 0

    since_date = datetime.now(timezone.utc) - timedelta(days=days)
    documents: list[dict] = []

    try:
        commits = list(repo.iter_commits(since=since_date.isoformat()))
    except Exception as exc:
        print(f"[git_history] Error reading commits: {exc}")
        return 0

    print(f"[git_history] Found {len(commits)} commits in the last {days} days.")

    for commit in commits:
        commit_date = datetime.fromtimestamp(commit.committed_date, tz=timezone.utc)

        # Build a summary of changed files
        try:
            stats = commit.stats
            changed_files = list(stats.files.keys())
            files_summary = ", ".join(changed_files[:20])
            if len(changed_files) > 20:
                files_summary += f" ... and {len(changed_files) - 20} more"
        except Exception:
            files_summary = "(unable to read diff stats)"

        content = (
            f"Commit: {commit.hexsha[:8]}\n"
            f"Author: {commit.author.name} <{commit.author.email}>\n"
            f"Date: {commit_date.strftime('%Y-%m-%d %H:%M:%S UTC')}\n"
            f"Message: {commit.message.strip()}\n"
            f"Files changed: {files_summary}\n"
            f"Stats: {stats.total['insertions']} insertions, "
            f"{stats.total['deletions']} deletions, "
            f"{stats.total['files']} files"
        )

        doc = {
            "id": _commit_id(repo_path, commit.hexsha),
            "content": content,
            "metadata": {
                "source": os.path.basename(repo_path),
                "type": "git_commit",
                "commit_sha": commit.hexsha[:8],
                "author": str(commit.author.name),
                "date": commit_date.isoformat(),
                "indexed_at": datetime.now(timezone.utc).isoformat(),
            },
        }
        documents.append(doc)

    if documents:
        print(f"[git_history] Indexing {len(documents)} commits...")
        store.add_documents(documents, collection="git_history")
    else:
        print("[git_history] No commits found in the given time range.")

    print(f"[git_history] Done. Indexed {len(documents)} commits.")
    return len(documents)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Index a repository into the DeepVoice RAG knowledge base."
    )
    parser.add_argument("repo_path", help="Path to the git repository to index.")
    parser.add_argument(
        "--collection",
        choices=["code", "docs", "git_history"],
        default=None,
        help="Which collection to index. If omitted, all three are indexed.",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=30,
        help="Number of days of git history to index (default: 30).",
    )
    args = parser.parse_args()

    repo_path = os.path.abspath(args.repo_path)
    if not os.path.isdir(repo_path):
        print(f"Error: '{repo_path}' is not a valid directory.")
        sys.exit(1)

    print(f"Repository: {repo_path}")
    print(f"Collections: {args.collection or 'all (code, docs, git_history)'}")
    print(f"Git history window: {args.days} days")
    print()

    store = KnowledgeStore()

    collections = [args.collection] if args.collection else ["code", "docs", "git_history"]
    total = 0

    for collection in collections:
        if collection == "code":
            total += index_code(repo_path, store)
        elif collection == "docs":
            total += index_docs(repo_path, store)
        elif collection == "git_history":
            total += index_git_history(repo_path, store, days=args.days)
        print()

    print(f"Indexing complete. Total documents indexed: {total}")

    # Show final store stats
    stats = store.get_stats()
    print("\nKnowledge store stats:")
    for name, count in stats.items():
        print(f"  {name}: {count} documents")


if __name__ == "__main__":
    main()
