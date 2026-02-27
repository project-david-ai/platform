# src/api/entities_api/routers/batfish_router.py
from fastapi import APIRouter, Depends, HTTPException, Query, status
from projectdavid_common.utilities.logging_service import LoggingUtility

from src.api.entities_api.dependencies import get_batfish_service
from src.api.entities_api.services.batfish_service import BatfishService

router = APIRouter()
logging_utility = LoggingUtility()


@router.post("/batfish/snapshot/refresh", status_code=status.HTTP_200_OK)
async def refresh_snapshot(
    incident: str = Query(..., description="Snapshot name e.g. incident_001"),
    service: BatfishService = Depends(get_batfish_service),
):
    """Pull latest GNS3 configs and refresh the Batfish snapshot."""
    try:
        result = service.ingest_configs(incident)
        return {
            "status": "success",
            "incident": incident,
            "devices_ingested": len(result["devices"]),
            "devices": result["devices"],
            "snapshot_path": result["snapshot_path"],
        }
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logging_utility.error(f"Snapshot refresh failed: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/batfish/rca/prompt", status_code=status.HTTP_200_OK)
async def get_rca_prompt(
    incident: str = Query(..., description="Snapshot name e.g. incident_001"),
    refresh: bool = Query(
        default=True, description="Re-ingest configs before querying"
    ),
    service: BatfishService = Depends(get_batfish_service),
):
    """
    Full pipeline: optionally refresh snapshot, run Batfish queries,
    return structured prompt ready for LLM consumption.
    """
    try:
        if refresh:
            result = service.refresh_and_build_prompt(incident)
        else:
            prompt = service.build_rca_prompt(incident)
            result = {"snapshot": incident, "prompt": prompt}
        return result
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logging_utility.error(f"RCA prompt build failed: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/batfish/snapshot/{incident}/devices", status_code=status.HTTP_200_OK)
async def list_snapshot_devices(
    incident: str,
    service: BatfishService = Depends(get_batfish_service),
):
    """List config files currently in a snapshot."""
    from pathlib import Path

    snapshot_path = Path("/data/snapshots") / incident / "configs"
    if not snapshot_path.exists():
        raise HTTPException(status_code=404, detail=f"Snapshot '{incident}' not found.")
    devices = [f.stem for f in snapshot_path.glob("*.cfg")]
    return {"incident": incident, "device_count": len(devices), "devices": devices}


@router.get("/batfish/health", status_code=status.HTTP_200_OK)
async def batfish_health(
    service: BatfishService = Depends(get_batfish_service),
):
    """Check if Batfish backend is reachable."""
    try:
        return service.check_health()
    except ConnectionError as e:
        raise HTTPException(status_code=503, detail=str(e))
