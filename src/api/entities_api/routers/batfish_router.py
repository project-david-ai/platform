# src/api/entities_api/routers/batfish_router.py
from fastapi import APIRouter, Depends, HTTPException, Query, status
from projectdavid_common.schemas.batfish_schema import BatfishSnapshotRead
from projectdavid_common.utilities.logging_service import LoggingUtility
from sqlalchemy.orm import Session

from src.api.entities_api.dependencies import (get_api_key,
                                               get_batfish_service, get_db)
from src.api.entities_api.models.models import ApiKey as ApiKeyModel
from src.api.entities_api.services.batfish_service import (
    TOOLS, BatfishService, BatfishServiceError, BatfishSnapshotConflictError,
    BatfishSnapshotNotFoundError, BatfishToolError)

router = APIRouter()
logging_utility = LoggingUtility()


def get_service(db: Session = Depends(get_db)) -> BatfishService:
    return BatfishService(db)


# ── CREATE / REFRESH SNAPSHOT ─────────────────────────────────────────────────


@router.post(
    "/batfish/snapshots",
    response_model=BatfishSnapshotRead,
    status_code=status.HTTP_200_OK,
    summary="Ingest configs and load snapshot into Batfish",
)
async def refresh_snapshot(
    snapshot_name: str = Query(
        ..., description="Incident/tenant label e.g. 'incident_001'"
    ),
    configs_root: str = Query(
        None, description="Override config root path (server-side)"
    ),
    service: BatfishService = Depends(get_service),
    auth_key: ApiKeyModel = Depends(get_api_key),
):
    """
    Creates a new snapshot with a server-generated opaque ID, stages configs,
    and loads into Batfish. Returns the full snapshot record — callers must
    store the returned `id` for all subsequent calls.
    """
    try:
        record = service.refresh_snapshot(
            user_id=auth_key.user_id,
            snapshot_name=snapshot_name,
            configs_root=configs_root,
        )
        return BatfishSnapshotRead.model_validate(record)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except BatfishSnapshotConflictError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except BatfishServiceError as e:
        logging_utility.error(f"refresh_snapshot failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


# ── LIST SNAPSHOTS ────────────────────────────────────────────────────────────


@router.get(
    "/batfish/snapshots",
    response_model=list[BatfishSnapshotRead],
    status_code=status.HTTP_200_OK,
    summary="List all snapshots owned by the caller",
)
def list_snapshots(
    service: BatfishService = Depends(get_service),
    auth_key: ApiKeyModel = Depends(get_api_key),
):
    records = service.list_snapshots(user_id=auth_key.user_id)
    return [BatfishSnapshotRead.model_validate(r) for r in records]


# ── GET SNAPSHOT ──────────────────────────────────────────────────────────────


@router.get(
    "/batfish/snapshots/{snapshot_id}",
    response_model=BatfishSnapshotRead,
    status_code=status.HTTP_200_OK,
    summary="Get a single snapshot record by its ID",
)
def get_snapshot(
    snapshot_id: str,
    service: BatfishService = Depends(get_service),
    auth_key: ApiKeyModel = Depends(get_api_key),
):
    try:
        return BatfishSnapshotRead.model_validate(
            service.get_snapshot(snapshot_id, user_id=auth_key.user_id)
        )
    except BatfishSnapshotNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))


# ── DELETE SNAPSHOT ───────────────────────────────────────────────────────────


@router.delete(
    "/batfish/snapshots/{snapshot_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Soft-delete a snapshot by its ID",
)
def delete_snapshot(
    snapshot_id: str,
    service: BatfishService = Depends(get_service),
    auth_key: ApiKeyModel = Depends(get_api_key),
):
    try:
        service.delete_snapshot(snapshot_id, user_id=auth_key.user_id)
    except BatfishSnapshotNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except BatfishServiceError as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── SINGLE TOOL CALL ──────────────────────────────────────────────────────────


@router.post(
    "/batfish/snapshots/{snapshot_id}/tools/{tool_name}",
    status_code=status.HTTP_200_OK,
    summary="Run a single RCA tool against a snapshot",
)
async def run_tool(
    snapshot_id: str,
    tool_name: str,
    service: BatfishService = Depends(get_service),
    auth_key: ApiKeyModel = Depends(get_api_key),
):
    try:
        result = await service.run_tool(
            user_id=auth_key.user_id,
            snapshot_id=snapshot_id,
            tool_name=tool_name,
        )
        return {"tool": tool_name, "snapshot_id": snapshot_id, "result": result}
    except BatfishSnapshotNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except BatfishToolError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except BatfishServiceError as e:
        logging_utility.error(f"Tool '{tool_name}' failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


# ── ALL TOOLS ─────────────────────────────────────────────────────────────────


@router.post(
    "/batfish/snapshots/{snapshot_id}/tools/all",
    status_code=status.HTTP_200_OK,
    summary="Run all RCA tools concurrently against a snapshot",
)
async def run_all_tools(
    snapshot_id: str,
    service: BatfishService = Depends(get_service),
    auth_key: ApiKeyModel = Depends(get_api_key),
):
    try:
        results = await service.run_all_tools(
            user_id=auth_key.user_id,
            snapshot_id=snapshot_id,
        )
        return {"snapshot_id": snapshot_id, "results": results}
    except BatfishSnapshotNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except BatfishServiceError as e:
        logging_utility.error(f"run_all_tools failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


# ── TOOL MANIFEST ─────────────────────────────────────────────────────────────


@router.get(
    "/batfish/tools",
    status_code=status.HTTP_200_OK,
    summary="List available RCA tool names",
)
def list_tools():
    return {"tools": TOOLS}


# ── HEALTH ────────────────────────────────────────────────────────────────────


@router.get(
    "/batfish/health",
    status_code=status.HTTP_200_OK,
    summary="Check Batfish backend reachability",
)
def batfish_health(service: BatfishService = Depends(get_service)):
    try:
        return service.check_health()
    except ConnectionError as e:
        raise HTTPException(status_code=503, detail=str(e))
