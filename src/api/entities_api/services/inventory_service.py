import logging
from typing import Dict, List, Optional

from projectdavid_common.utilities.logging_service import LoggingUtility

from entities_api.cache.inventory_cache import InventoryCache

# FIX APPLIED: Added parenthesis to instantiate the logger
logger = LoggingUtility()


class InventoryService:
    def __init__(self, inventory_cache: InventoryCache):
        self.cache = inventory_cache

    async def ingest_inventory(self, user_id: str, assistant_id: str, devices: List[Dict]) -> int:
        if not devices:
            return 0

        valid_devices = []
        for dev in devices:
            if "host_name" not in dev:
                logger.warning(
                    f"Skipped a device missing 'host_name' during ingestion for User {user_id}, Assistant {assistant_id}"
                )
                continue
            valid_devices.append(dev)

        if not valid_devices:
            return 0

        logger.info(
            f"Ingesting {len(valid_devices)} valid devices for User {user_id}, Assistant {assistant_id}"
        )
        return await self.cache.ingest_inventory(user_id, assistant_id, valid_devices)

    async def search_by_group(self, user_id: str, assistant_id: str, group: str) -> List[Dict]:
        if not group:
            return []

        logger.debug(
            f"Searching inventory for group '{group}' (User: {user_id}, Assistant: {assistant_id})"
        )
        return await self.cache.search_by_group(user_id, assistant_id, group)

    async def get_device(self, user_id: str, assistant_id: str, hostname: str) -> Optional[Dict]:
        if not hostname:
            return None

        logger.debug(f"Fetching device '{hostname}' (User: {user_id}, Assistant: {assistant_id})")
        return await self.cache.get_device(user_id, assistant_id, hostname)
