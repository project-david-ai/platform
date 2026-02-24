import asyncio
import json
import os
from typing import Any, Dict, List, Union

from projectdavid import Entity
from redis import Redis as SyncRedis

try:
    from redis.asyncio import Redis as AsyncRedis
except ImportError:

    class AsyncRedis:
        pass


REDIS_ASSISTANT_TTL = int(os.getenv("REDIS_ASSISTANT_TTL_SECONDS", "300"))


class AssistantCache:

    def __init__(
        self, redis: Union[SyncRedis, "AsyncRedis"], pd_base_url: str, pd_api_key: str
    ):
        self.redis = redis
        self.pd_base_url = pd_base_url
        self.pd_api_key = pd_api_key

    def _cache_key(self, assistant_id: str) -> str:
        return f"assistant:{assistant_id}:config"

    async def get(self, assistant_id: str):
        key = self._cache_key(assistant_id)
        if isinstance(self.redis, AsyncRedis):
            raw = await self.redis.get(key)
        else:
            raw = await asyncio.to_thread(self.redis.get, key)
        return json.loads(raw) if raw else None

    async def set(self, assistant_id: str, payload: dict):
        key = self._cache_key(assistant_id)
        data = json.dumps(payload)
        if isinstance(self.redis, AsyncRedis):
            await self.redis.set(key, data, ex=REDIS_ASSISTANT_TTL)
        else:
            await asyncio.to_thread(self.redis.set, key, data, ex=REDIS_ASSISTANT_TTL)

    def _normalize_bool(self, value: Union[str, bool, int]) -> bool:
        """Helper to safely convert metadata strings/ints to boolean."""
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.lower() in ("true", "1", "yes", "on")
        if isinstance(value, int):
            return value == 1
        return False

    async def retrieve(self, assistant_id: str):
        """
        Fetches assistant details and caches them.
        """
        # 1. Check Cache
        cached = await self.get(assistant_id)
        if cached:
            return cached

        client = Entity(base_url=self.pd_base_url, api_key=self.pd_api_key)

        # 2. Fetch Assistant from the platform
        assistant = await asyncio.to_thread(
            client.assistants.retrieve_assistant, assistant_id=assistant_id
        )

        # 3. Extract Tools directly from the Assistant object
        raw_tools = getattr(assistant, "tools", []) or []

        # Normalize tools for JSON serialization
        clean_tools = []
        for t in raw_tools:
            if isinstance(t, dict):
                clean_tools.append(t)
            elif hasattr(t, "model_dump"):
                clean_tools.append(t.model_dump())
            elif hasattr(t, "dict"):
                clean_tools.append(t.dict())
            else:
                clean_tools.append(dict(t))

        # 4. Safely Extract Metadata
        # We need the full dict so the worker can do: config.get("meta_data").get("key")
        raw_meta = getattr(assistant, "meta_data", {}) or {}

        # Safe extraction of the research worker flag (handling strings vs bools)
        research_worker_val = raw_meta.get("research_worker_calling", False)
        is_research_worker = self._normalize_bool(research_worker_val)

        junior_engineer_val = raw_meta.get("junior_engineer", False)
        is_junior_engineer = self._normalize_bool(junior_engineer_val)

        # 5. Construct Payload
        payload = {
            "instructions": assistant.instructions,
            "tools": clean_tools,
            "agent_mode": assistant.agent_mode,
            "decision_telemetry": assistant.decision_telemetry,
            "web_access": assistant.web_access,
            "deep_research": assistant.deep_research,
            "meta_data": raw_meta,
            "is_research_worker": is_research_worker,
            "junior_engineer": is_junior_engineer,
            "is_engineer": assistant.engineer,
        }

        # 6. Save to Redis
        await self.set(assistant_id, payload)
        return payload

    async def delete(self, assistant_id: str):
        key = self._cache_key(assistant_id)
        if isinstance(self.redis, AsyncRedis):
            await self.redis.delete(key)
        else:
            await asyncio.to_thread(self.redis.delete, key)

    def invalidate_sync(self, assistant_id: str):
        key = self._cache_key(assistant_id)
        if hasattr(self.redis, "delete") and not asyncio.iscoroutinefunction(
            self.redis.delete
        ):
            self.redis.delete(key)
        else:
            try:
                asyncio.run(self.delete(assistant_id))
            except RuntimeError:
                pass

    def retrieve_sync(self, assistant_id: str):
        return asyncio.run(self.retrieve(assistant_id))
