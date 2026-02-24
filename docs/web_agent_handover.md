# 📂 PROJECT HANDOVER: Native Web Browsing Agent (Level 3)

**Date:** February 6, 2026
**Status:** Functional Prototype (v2) - Pagination Logic Implemented
**Goal:** Create a robust, token-safe web browsing tool for an LLM Agent capable of sequencing actions.

---

## 1. Context & Architecture
We are building a `UniversalWebReader` class that serves as the "eyes" of an AI agent. 

### The Problems Solved:
1.  **Garbage/JS Walls:** Solved via a "Hybrid Fetch" system. It first tries a fast HTTP request (or `w3m` on Linux), and if it detects "JavaScript required" stubs, it fails over to a Headless Playwright browser.
2.  **Context Window Crashes:** Solved via a "Pagination/Caching" system. Instead of dumping 50k tokens of text into the prompt, the system caches the full website in memory and serves it in 4000-character "pages" (Chunks).

### The Tooling Strategy
The Agent does not see the whole internet at once. It acts like a user with a Kindle:
1.  **`read(url)`**: Fetches the site, stores it, and returns **Page 0**.
2.  **`scroll(url, page)`**: returns **Page X** from the cache.

---

## 2. Current Codebase (v2.1)
*Dependencies: `playwright`, `html2text`, `requests`, `asyncio`*
*System Requirement: Playwright browsers installed (`playwright install chromium`), `w3m` (optional, for Linux speed).*

```python
import asyncio
import logging
import platform
import subprocess
import hashlib
from typing import Dict, Optional

import html2text
import requests
from playwright.async_api import async_playwright

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("WebReader")

class UniversalWebReader:
    def __init__(self):
        # Configure HTML to Markdown converter
        self.converter = html2text.HTML2Text()
        self.converter.ignore_links = False
        self.converter.ignore_images = True
        self.converter.ignore_tables = False
        self.converter.body_width = 0 

        # --- MEMORY MANAGEMENT ---
        # Format: { "url_hash": { "full_text": str, "chunks": [str], "url": str } }
        self.session_cache: Dict[str, dict] = {}
        
        # 4000 chars ~= 1000 tokens (Safe for context windows)
        self.CHUNK_SIZE = 4000 

        self.garbage_triggers = [
            "enable javascript", "please wait", "loading...", 
            "cookie settings", "browser not supported", 
            "javascript is required", "checking your browser"
        ]

    async def read(self, url: str) -> str:
        """
        Action 1: Load a URL.
        Fetches content, processes it, stores it in memory, and returns PAGE 1.
        """
        logger.info(f"📖 Reading: {url}")
        
        # 1. Fetch Content (Try Static, then Dynamic)
        content = self._fetch_static(url)
        source = "Static (Fast)"

        if not self._is_valid_content(content):
            logger.info("⚠️  Static fetch failed/garbage. Switching to Headless Browser...")
            content = await self._fetch_dynamic(url)
            source = "Dynamic (High-Fidelity)"

        # 2. Process and Cache the Content
        cache_id = self._cache_content(url, content)
        
        # 3. Return the first page (Chunk 0)
        return self._get_page_view(cache_id, page_index=0, source=source)

    async def scroll(self, url: str, page: int) -> str:
        """
        Action 2: Scroll / Pagination.
        Allows the agent to request specific pages of a previously loaded URL.
        """
        cache_id = self._generate_id(url)
        
        if cache_id not in self.session_cache:
            return "❌ Error: This URL has not been loaded yet. Please call read(url) first."
        
        return self._get_page_view(cache_id, page_index=page)

    def _cache_content(self, url: str, text: str) -> str:
        """Slices the text into chunks and saves to memory."""
        cache_id = self._generate_id(url)
        clean_text = text.strip()
        
        # Create chunks (Pages)
        chunks = [clean_text[i:i+self.CHUNK_SIZE] for i in range(0, len(clean_text), self.CHUNK_SIZE)]
        
        if not chunks:
            chunks = ["(Empty Page)"]

        self.session_cache[cache_id] = {
            "url": url,
            "full_text": clean_text,
            "chunks": chunks,
            "total_pages": len(chunks)
        }
        return cache_id

    def _get_page_view(self, cache_id: str, page_index: int, source: str = "Memory") -> str:
        """Formats the output specifically for an LLM to understand its position."""
        data = self.session_cache.get(cache_id)
        if not data:
            return "❌ Error: Cache missing."

        total_pages = data["total_pages"]
        if page_index >= total_pages:
            return f"❌ Error: Page {page_index} out of bounds. Total pages: {total_pages}. (Try page {total_pages - 1})"
        
        chunk_content = data["chunks"][page_index]
        
        # --- PROMPT ENGINEERING THE OUTPUT ---
        output = []
        output.append(f"--- 🌐 WEB BROWSER: {data['url']} ---")
        output.append(f"--- 📊 SOURCE: {source} | VIEW: Page {page_index} of {total_pages - 1} ---")
        output.append(f"--- 📏 SIZE: {len(data['full_text'])} chars total ---\n")
        output.append(chunk_content)
        output.append("\n" + "="*30)
        
        if page_index < total_pages - 1:
            output.append(f"👉 SYSTEM NOTICE: Content continues. To read the next part, call `scroll(url='{data['url']}', page={page_index + 1})`")
        else:
            output.append("✅ SYSTEM NOTICE: End of Document.")
            
        return "\n".join(output)

    def _generate_id(self, url: str) -> str:
        return hashlib.md5(url.encode()).hexdigest()
    
    def _fetch_static(self, url: str) -> str:
        try:
            if platform.system() == "Windows":
                headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/91.0.4472.124"}
                response = requests.get(url, headers=headers, timeout=10)
                response.raise_for_status()
                return self.converter.handle(response.text)
            else:
                # Optimized for Docker/Linux environments
                result = subprocess.run(["w3m", "-dump", "-T", "text/html", url], capture_output=True, text=True, timeout=10)
                return result.stdout.strip()
        except Exception as e:
            logger.error(f"Static fetch error: {e}")
            return ""

    async def _fetch_dynamic(self, url: str) -> str:
        async with async_playwright() as p:
            try:
                browser = await p.chromium.launch(headless=True)
                context = await browser.new_context(user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/91.0.4472.124")
                page = await context.new_page()
                await page.goto(url, timeout=60000, wait_until="networkidle")
                html = await page.content()
                await browser.close()
                return self.converter.handle(html)
            except Exception as e:
                return f"Error reading page: {e}"

    def _is_valid_content(self, text: str) -> bool:
        if not text or len(text) < 300: return False
        header = text.lower()[:1000]
        if any(trigger in header for trigger in self.garbage_triggers): return False
        return True