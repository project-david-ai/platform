# src/api/entities_api/routers/engineering.py
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from projectdavid_common.schemas.device_ingest_scema import \
    InventoryIngestRequest
from projectdavid_common.utilities.logging_service import LoggingUtility

from src.api.entities_api.dependencies import (get_api_key,
                                               get_inventory_service)
from src.api.entities_api.models.models import ApiKey as ApiKeyModel
from src.api.entities_api.services.inventory_service import InventoryService

router = APIRouter()
logging_utility = LoggingUtility()


@router.post("/engineer/inventory/ingest", status_code=status.HTTP_200_OK)
async def ingest_network_inventory(
    payload: InventoryIngestRequest,
    service: InventoryService = Depends(get_inventory_service),
    auth_key: ApiKeyModel = Depends(get_api_key),
):
    """The Engineer's Ingestion Point (User-scoped)."""
    try:
        device_dicts = [device.dict() for device in payload.devices]

        # Use getattr as a safe fallback just in case `clear_existing`
        # hasn't been added to your Pydantic schema yet.
        clear_existing = getattr(payload, "clear_existing", False)

        # Removed assistant_id! Scoped entirely to the User.
        count = await service.ingest_inventory(
            user_id=auth_key.user_id,
            devices=device_dicts,
            clear_existing=clear_existing,
        )

        return {
            "status": "success",
            "user_id": auth_key.user_id,
            "devices_ingested": count,
        }
    except Exception as e:
        logging_utility.error(f"Ingestion Failed: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/engineer/inventory/device/{hostname}", status_code=status.HTTP_200_OK)
async def get_device_info(
    hostname: str,
    user_id: Optional[str] = Query(default=None),
    service: InventoryService = Depends(get_inventory_service),
    auth_key: ApiKeyModel = Depends(get_api_key),
):
    effective_user_id = user_id if user_id else auth_key.user_id
    try:
        device = await service.get_device(effective_user_id, hostname)
        if not device:
            raise HTTPException(
                status_code=404, detail="Device not found in inventory map."
            )
        return device
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/engineer/inventory/group/{group}", status_code=status.HTTP_200_OK)
async def search_inventory_by_group(
    group: str,
    user_id: Optional[str] = Query(default=None),
    service: InventoryService = Depends(get_inventory_service),
    auth_key: ApiKeyModel = Depends(get_api_key),
):
    effective_user_id = user_id if user_id else auth_key.user_id
    try:
        devices = await service.search_by_group(effective_user_id, group)
        return {"group": group, "count": len(devices), "devices": devices}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
