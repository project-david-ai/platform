# src/api/entities_api/routers/the_engineer_router.py

from fastapi import APIRouter, Depends, HTTPException, status
# --- Schemas ---
# Importing the schema we defined in projectdavid_common
from projectdavid_common.schemas.device_ingest_scema import \
    InventoryIngestRequest
from projectdavid_common.utilities.logging_service import LoggingUtility

from src.api.entities_api.cache.inventory_cache import InventoryCache
# --- Core Dependencies ---
from src.api.entities_api.dependencies import get_api_key, get_inventory_cache
from src.api.entities_api.models.models import ApiKey as ApiKeyModel

# --- Router Setup ---
router = APIRouter()
logging_utility = LoggingUtility()


@router.post(
    "/engineer/inventory/ingest",
    summary="Upload Network Map (The Engineer's Eyes)",
    status_code=status.HTTP_200_OK,
)
async def ingest_network_inventory(
    payload: InventoryIngestRequest,
    cache: InventoryCache = Depends(get_inventory_cache),
    auth_key: ApiKeyModel = Depends(get_api_key),
):
    """
    **The Engineer's Ingestion Point.**

    The SDK (running securely in the customer's network) pushes the device inventory here.
    This creates the "Mental Map" for the Assistant.

    - **Scope:** Data is isolated by `assistant_id` (Tenant).
    - **Security:** No passwords are sent here. Only metadata (IP, Hostname, Platform).
    - **Outcome:** The Assistant can now 'see' these network_device_commands to plan actions.
    """
    try:
        logging_utility.info(
            f"The Engineer: Ingesting inventory for Assistant '{payload.assistant_id}' "
            f"via User ID {auth_key.user_id}"
        )

        # 1. Convert Pydantic models to standard dicts for Redis
        # (Redis cache expects a list of dicts, not Pydantic objects)
        device_dicts = [device.dict() for device in payload.devices]

        # 2. Ingest into the Assistant-Specific Scope
        # FIX APPLIED: Added user_id
        count = await cache.ingest_inventory(
            user_id=auth_key.user_id,
            assistant_id=payload.assistant_id,
            devices=device_dicts,
        )

        return {
            "status": "success",
            "assistant_id": payload.assistant_id,
            "devices_ingested": count,
            "message": "The Engineer's mental map has been updated.",
        }

    except Exception as e:
        # FIX APPLIED: Added exc_info=True to print tracebacks on future errors
        logging_utility.error(f"The Engineer Ingestion Failed: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to ingest inventory: {str(e)}",
        )
