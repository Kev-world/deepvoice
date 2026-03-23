"""Web URL indexer for the RAG knowledge base."""

import hashlib
import logging
import re
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import urlparse, urljoin

import httpx
from bs4 import BeautifulSoup

from src.rag.store import KnowledgeStore

logger = logging.getLogger(__name__)


class WebIndexer:
    """Fetches web pages and indexes their content into the knowledge store."""

    def __init__(self, store: Optional[KnowledgeStore] = None) -> None:
        self._store = store or KnowledgeStore()

    async def index_url(self, url: str, collection: str = "docs") -> dict:
        """Fetch a single URL and index its content.

        Args:
            url: The URL to fetch and index.
            collection: The knowledge store collection to write to.

        Returns:
            A dict with: url, title, chunks_indexed, status
        """
        logger.info("Fetching URL: %s", url)

        try:
            content, title = await self._fetch_page(url)
        except Exception as e:
            logger.error("Failed to fetch %s: %s", url, e)
            return {"url": url, "title": "", "chunks_indexed": 0, "status": "error", "error": str(e)}

        if not content or not content.strip():
            return {"url": url, "title": title, "chunks_indexed": 0, "status": "empty"}

        chunks = self._chunk_text(content)
        documents = []

        for idx, chunk in enumerate(chunks):
            doc_id = self._make_id(url, idx)
            documents.append({
                "id": doc_id,
                "content": chunk,
                "metadata": {
                    "source": url,
                    "type": "web_document",
                    "path": urlparse(url).path or url,
                    "title": title,
                    "chunk_index": idx,
                    "indexed_at": datetime.now(timezone.utc).isoformat(),
                },
            })

        if documents:
            self._store.add_documents(documents, collection=collection)

        logger.info("Indexed %d chunks from %s (%s)", len(documents), url, title)
        return {"url": url, "title": title, "chunks_indexed": len(documents), "status": "ok"}

    async def index_urls(self, urls: list[str], collection: str = "docs") -> list[dict]:
        """Fetch and index multiple URLs.

        Args:
            urls: List of URLs to fetch and index.
            collection: The knowledge store collection to write to.

        Returns:
            List of result dicts, one per URL.
        """
        results = []
        for url in urls:
            result = await self.index_url(url.strip(), collection=collection)
            results.append(result)
        return results

    async def index_sitemap(self, sitemap_url: str, collection: str = "docs", max_pages: int = 50) -> list[dict]:
        """Fetch a sitemap.xml and index all pages found in it.

        Args:
            sitemap_url: URL to a sitemap.xml file.
            collection: The knowledge store collection.
            max_pages: Maximum number of pages to index.

        Returns:
            List of result dicts.
        """
        logger.info("Fetching sitemap: %s", sitemap_url)

        try:
            async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
                resp = await client.get(sitemap_url, headers={"User-Agent": "DeepVoice-Bot/1.0"})
                resp.raise_for_status()
                xml_text = resp.text
        except Exception as e:
            logger.error("Failed to fetch sitemap %s: %s", sitemap_url, e)
            return [{"url": sitemap_url, "status": "error", "error": str(e)}]

        # Parse sitemap XML - extract <loc> tags
        urls = re.findall(r'<loc>(.*?)</loc>', xml_text)
        urls = urls[:max_pages]

        logger.info("Found %d URLs in sitemap (indexing up to %d)", len(urls), max_pages)
        return await self.index_urls(urls, collection=collection)

    async def _fetch_page(self, url: str) -> tuple[str, str]:
        """Fetch a web page and extract its text content.

        Returns:
            Tuple of (text_content, page_title)
        """
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
            resp = await client.get(url, headers={
                "User-Agent": "DeepVoice-Bot/1.0",
                "Accept": "text/html,application/xhtml+xml,text/plain,text/markdown",
            })
            resp.raise_for_status()
            content_type = resp.headers.get("content-type", "")
            raw = resp.text

        # If it's plain text or markdown, return as-is
        if "text/plain" in content_type or "text/markdown" in content_type:
            return raw, urlparse(url).path.split("/")[-1] or url

        # Parse HTML and extract meaningful text
        soup = BeautifulSoup(raw, "html.parser")

        title = ""
        if soup.title and soup.title.string:
            title = soup.title.string.strip()

        # Remove script, style, nav, footer, header elements
        for tag in soup(["script", "style", "nav", "footer", "header", "aside",
                          "noscript", "iframe", "svg", "form"]):
            tag.decompose()

        # Try to find the main content area
        main = soup.find("main") or soup.find("article") or soup.find(role="main")
        if main:
            text = main.get_text(separator="\n", strip=True)
        else:
            # Fallback to body
            body = soup.find("body")
            text = body.get_text(separator="\n", strip=True) if body else soup.get_text(separator="\n", strip=True)

        # Clean up: collapse multiple blank lines
        text = re.sub(r'\n{3,}', '\n\n', text)

        # Prepend title as context
        if title:
            text = f"# {title}\n\n{text}"

        return text, title

    def _chunk_text(self, text: str, chunk_size: int = 1000, overlap: int = 200) -> list[str]:
        """Split text into overlapping chunks."""
        if not text:
            return []
        if len(text) <= chunk_size:
            return [text]

        chunks = []
        start = 0
        while start < len(text):
            end = start + chunk_size
            if end < len(text):
                newline_pos = text.rfind("\n", start + chunk_size // 2, end)
                if newline_pos != -1:
                    end = newline_pos + 1

            chunk = text[start:end].strip()
            if chunk:
                chunks.append(chunk)

            start = end - overlap
            if start <= (end - chunk_size):
                start = end

        return chunks

    @staticmethod
    def _make_id(url: str, chunk_index: int) -> str:
        """Create a deterministic document ID."""
        raw = f"web:{url}::{chunk_index}"
        return hashlib.sha256(raw.encode()).hexdigest()[:16]
