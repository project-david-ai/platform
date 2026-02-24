import json
from typing import Dict, List, Optional

from redis.asyncio import Redis


class InventoryCache:
    """
    Manages the storage and retrieval of Network Device configurations.
    Scoped by 'user_id' and 'assistant_id' to ensure complete multi-tenant isolation.
    """

    def __init__(self, redis: Redis):
        self.redis = redis
        self.ttl = 86400  # 24 Hours

    # --- KEY GENERATION (Scoped) ---

    def _dev_key(self, user_id: str, assistant_id: str, hostname: str) -> str:
        """
        Key for the actual JSON data blob of a device.
        Format: net_eng:usr:{user_id}:ast:{assistant_id}:inv:device:{hostname}
        """
        return f"net_eng:usr:{user_id}:ast:{assistant_id}:inv:device:{hostname.lower()}"

    def _group_key(self, user_id: str, assistant_id: str, group: str) -> str:
        """
        Key for the Set containing hostnames belonging to a group.
        Format: net_eng:usr:{user_id}:ast:{assistant_id}:inv:group:{group}
        """
        return f"net_eng:usr:{user_id}:ast:{assistant_id}:inv:group:{group.lower()}"

    # --- PUBLIC METHODS ---

    async def ingest_inventory(
        self, user_id: str, assistant_id: str, devices: List[Dict]
    ) -> int:
        """
        Stores network_device_commands into the specific User's and Assistant's namespace.
        """
        async with self.redis.pipeline() as pipe:
            for dev in devices:
                hostname = dev["host_name"]

                # 1. Store Device Data (Scoped to User + Assistant)
                dev_key = self._dev_key(user_id, assistant_id, hostname)

                # Add the owner context to the blob for sanity checking later
                dev["owner_id"] = user_id
                dev["assistant_id"] = assistant_id

                pipe.set(dev_key, json.dumps(dev), ex=self.ttl)

                # 2. Update Group Indexes (Scoped to User + Assistant)
                if "groups" in dev and isinstance(dev["groups"], list):
                    for group in dev["groups"]:
                        g_key = self._group_key(user_id, assistant_id, group)
                        pipe.sadd(g_key, hostname)
                        pipe.expire(g_key, self.ttl)

                # 3. Always add to 'all' group for this scope
                all_key = self._group_key(user_id, assistant_id, "all")
                pipe.sadd(all_key, hostname)
                pipe.expire(all_key, self.ttl)

            await pipe.execute()

        return len(devices)

    async def search_by_group(
        self, user_id: str, assistant_id: str, group: str
    ) -> List[Dict]:
        """
        Retrieves network_device_commands for a specific group, strictly within the User's and Assistant's scope.
        """
        # 1. Get hostnames from the scoped group set
        target_group_key = self._group_key(user_id, assistant_id, group)
        hostnames_bytes = await self.redis.smembers(target_group_key)

        if not hostnames_bytes:
            return []

        hostnames = [
            h.decode("utf-8") if isinstance(h, bytes) else h for h in hostnames_bytes
        ]

        # 2. Pipeline the retrieval using the scoped keys
        async with self.redis.pipeline() as pipe:
            for host in hostnames:
                # IMPORTANT: Must use the exact same _dev_key scope
                pipe.get(self._dev_key(user_id, assistant_id, host))

            results = await pipe.execute()

        # 3. Deserialize
        clean_results = []
        for raw_json in results:
            if raw_json:
                try:
                    clean_results.append(json.loads(raw_json))
                except json.JSONDecodeError:
                    continue

        return clean_results

    async def get_device(
        self, user_id: str, assistant_id: str, hostname: str
    ) -> Optional[Dict]:
        """
        Retrieves a single device by hostname within the User's and Assistant's scope.
        """
        key = self._dev_key(user_id, assistant_id, hostname)
        raw_json = await self.redis.get(key)

        if raw_json:
            try:
                return json.loads(raw_json)
            except json.JSONDecodeError:
                return None
        return None
