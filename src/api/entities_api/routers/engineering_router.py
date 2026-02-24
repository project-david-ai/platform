# src/api/entities_api/routers/engineering.py
from fastapi import APIRouter, Depends, HTTPException, status
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
    service: InventoryService = Depends(get_inventory_service),  # <-- Using Service!
    auth_key: ApiKeyModel = Depends(get_api_key),
):
    """The Engineer's Ingestion Point."""
    try:
        device_dicts = [device.dict() for device in payload.devices]

        # Passing clear_existing properly down to the service
        count = await service.ingest_inventory(
            user_id=auth_key.user_id,
            assistant_id=payload.assistant_id,
            devices=device_dicts,
            clear_existing=payload.clear_existing,  # Ensure this is in your Pydantic schema!
        )

        return {
            "status": "success",
            "assistant_id": payload.assistant_id,
            "devices_ingested": count,
        }
    except Exception as e:
        logging_utility.error(f"Ingestion Failed: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/engineer/inventory/device/{hostname}", status_code=status.HTTP_200_OK)
async def get_device_info(
    hostname: str,
    assistant_id: str,
    service: InventoryService = Depends(get_inventory_service),
    auth_key: ApiKeyModel = Depends(get_api_key),
):
    """Matches the 'get_device_info' platform tool."""
    try:
        device = await service.get_device(auth_key.user_id, assistant_id, hostname)
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
    assistant_id: str,
    service: InventoryService = Depends(get_inventory_service),
    auth_key: ApiKeyModel = Depends(get_api_key),
):
    """Matches the 'search_inventory_by_group' platform tool."""
    try:
        devices = await service.search_by_group(auth_key.user_id, assistant_id, group)
        return {"group": group, "count": len(devices), "devices": devices}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
