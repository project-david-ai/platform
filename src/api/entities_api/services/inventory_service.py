# src/api/entities_api/services/inventory_service.py
import logging
from typing import Dict, List, Optional

from projectdavid_common.utilities.logging_service import LoggingUtility

from src.api.entities_api.cache.inventory_cache import InventoryCache

logger = LoggingUtility()


class InventoryService:
    def __init__(self, inventory_cache: InventoryCache):
        self.cache = inventory_cache

    async def ingest_inventory(
        self,
        user_id: str,
        assistant_id: str,
        devices: List[Dict],
        clear_existing: bool = False,
    ) -> int:

        # 1. Handle clearing the map if requested
        if clear_existing:
            logger.info(
                f"Clearing existing inventory for Assistant {assistant_id} (User {user_id})"
            )
            # Note: You need to implement a `clear_inventory` method in InventoryCache
            # using pattern matching or keeping an index of keys to delete.
            # await self.cache.clear_inventory(user_id, assistant_id)

        if not devices:
            return 0

        # 2. Validation
        valid_devices = []
        for dev in devices:
            if "host_name" not in dev:
                logger.warning(
                    f"Skipped device missing 'host_name' (User {user_id}, Assistant {assistant_id})"
                )
                continue
            valid_devices.append(dev)

        if not valid_devices:
            return 0

        # 3. Store in cache
        logger.info(
            f"Ingesting {len(valid_devices)} devices for User {user_id}, Assistant {assistant_id}"
        )
        return await self.cache.ingest_inventory(user_id, assistant_id, valid_devices)

    async def search_by_group(
        self, user_id: str, assistant_id: str, group: str
    ) -> List[Dict]:
        if not group:
            return []
        logger.debug(f"Searching inventory for group '{group}'")
        return await self.cache.search_by_group(user_id, assistant_id, group)

    async def get_device(
        self, user_id: str, assistant_id: str, hostname: str
    ) -> Optional[Dict]:
        if not hostname:
            return None
        logger.debug(f"Fetching device '{hostname}'")
        return await self.cache.get_device(user_id, assistant_id, hostname)
