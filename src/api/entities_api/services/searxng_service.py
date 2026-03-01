import logging
from typing import Any, Dict, List, Optional

import httpx

logger = logging.getLogger("SearxNGService")

SEARXNG_URL = "http://searxng:8080"


class SearxNGResult:
    def __init__(self, data: Dict[str, Any]):
        self.title: str = data.get("title", "No Title")
        self.url: str = data.get("url", "")
        self.snippet: str = data.get("content", "")
        self.engine: str = data.get("engine", "unknown")
        self.score: float = data.get("score", 0.0)

    def __repr__(self):
        return f"<SearxNGResult title={self.title!r} url={self.url!r}>"


class SearxNGService:
    """
    Thin async client for the internal SearxNG container.
    Returns clean structured results — no scraping, no markdown parsing.
    """

    def __init__(self, base_url: str = SEARXNG_URL, timeout: int = 15):
        self.base_url = base_url
        self.timeout = timeout

    async def search(
        self,
        query: str,
        count: int = 10,
        engines: Optional[List[str]] = None,
        categories: str = "general",
        language: str = "en",
    ) -> List[SearxNGResult]:
        """
        Execute a search against SearxNG and return structured results.

        Args:
            query:      The search query string.
            count:      Max number of results to return (applied client-side).
            engines:    Optional list of engines e.g. ['duckduckgo', 'bing'].
                        If None, SearxNG uses its configured defaults.
            categories: SearxNG category — 'general', 'news', 'it', etc.
            language:   Language code for results.

        Returns:
            List of SearxNGResult objects, sorted by score descending.
        """
        params: Dict[str, Any] = {
            "q": query,
            "format": "json",
            "categories": categories,
            "language": language,
        }

        if engines:
            params["engines"] = ",".join(engines)

        logger.info(f"🔎 SearxNG query: '{query}' | engines={engines or 'default'}")

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.get(f"{self.base_url}/search", params=params)
                resp.raise_for_status()
                data = resp.json()
        except httpx.HTTPStatusError as e:
            logger.error(f"SearxNG HTTP error: {e}")
            raise RuntimeError(f"SearxNG returned {e.response.status_code}")
        except httpx.RequestError as e:
            logger.error(f"SearxNG connection error: {e}")
            raise RuntimeError(f"Could not reach SearxNG: {e}")

        raw_results = data.get("results", [])
        results = [SearxNGResult(r) for r in raw_results]

        # Sort by score, best first
        results.sort(key=lambda r: r.score, reverse=True)

        logger.info(f"✅ SearxNG returned {len(results)} results for '{query}'")
        return results[:count]

    async def format_for_agent(
        self,
        query: str,
        count: int = 10,
        engines: Optional[List[str]] = None,
    ) -> str:
        """
        Convenience method: search and return an agent-ready formatted string.
        This is a drop-in replacement for the current DDG markdown scrape output.
        """
        try:
            results = await self.search(query=query, count=count, engines=engines)
        except RuntimeError as e:
            return f"❌ SearxNG search failed: {e}"

        if not results:
            return f"❌ No results found for '{query}'. Try a broader query."

        lines = [f"🔍 SEARCH RESULTS for '{query}' ({len(results)} found):\n"]

        for i, r in enumerate(results, 1):
            authority_tag = ""
            if any(d in r.url for d in [".gov", ".edu", ".org"]):
                authority_tag = " [HIGH AUTHORITY]"
            elif "wikipedia.org" in r.url:
                authority_tag = " [ENCYCLOPEDIA]"

            lines.append(
                f"{i}. **{r.title}**{authority_tag}  [via {r.engine}]\n"
                f"   URL: {r.url}\n"
                f"   {r.snippet}\n"
            )

        lines.append(
            "\n👉 NEXT STEP: Call read_web_page(url='...') on the most relevant results above."
        )

        return "\n".join(lines)
