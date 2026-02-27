# src/api/entities_api/routers/batfish_router.py
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from projectdavid_common.schemas.batfish_schema import BatfishSnapshotRead
from projectdavid_common.utilities.logging_service import LoggingUtility
from sqlalchemy.orm import Session

from src.api.entities_api.dependencies import get_api_key, get_db
from src.api.entities_api.models.models import ApiKey as ApiKeyModel
from src.api.entities_api.services.batfish_service import (
    TOOLS, BatfishService, BatfishServiceError, BatfishSnapshotConflictError,
    BatfishSnapshotNotFoundError, BatfishToolError)

router = APIRouter()
logging_utility = LoggingUtility()


def get_service(db: Session = Depends(get_db)) -> BatfishService:
    return BatfishService(db)


def resolve_user_id(auth_key: ApiKeyModel, user_id_override: Optional[str]) -> str:
    if user_id_override and getattr(auth_key, "is_admin", False):
        return user_id_override
    return auth_key.user_id


# ── CREATE — new snapshot, 409 if name already exists ────────────────────────


@router.post(
    "/batfish/snapshots",
    response_model=BatfishSnapshotRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new snapshot — returns opaque id for all future calls",
)
async def create_snapshot(
    snapshot_name: str = Query(..., description="Human label e.g. 'incident_001'"),
    configs_root: str = Query(
        None, description="Override config root (server-side path)"
    ),
    user_id: Optional[str] = Query(None, description="Admin override"),
    service: BatfishService = Depends(get_service),
    auth_key: ApiKeyModel = Depends(get_api_key),
):
    try:
        record = service.create_snapshot(
            user_id=resolve_user_id(auth_key, user_id),
            snapshot_name=snapshot_name,
            configs_root=configs_root,
        )
        return BatfishSnapshotRead.model_validate(record)
    except BatfishSnapshotConflictError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except BatfishServiceError as e:
        logging_utility.error(f"create_snapshot failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


# ── REFRESH — re-ingest existing snapshot by id, 404 if not found ────────────


@router.post(
    "/batfish/snapshots/{snapshot_id}/refresh",
    response_model=BatfishSnapshotRead,
    status_code=status.HTTP_200_OK,
    summary="Re-ingest configs for an existing snapshot",
)
async def refresh_snapshot(
    snapshot_id: str,
    configs_root: str = Query(
        None, description="Override config root (server-side path)"
    ),
    user_id: Optional[str] = Query(None, description="Admin override"),
    service: BatfishService = Depends(get_service),
    auth_key: ApiKeyModel = Depends(get_api_key),
):
    try:
        record = service.refresh_snapshot(
            snapshot_id=snapshot_id,
            user_id=resolve_user_id(auth_key, user_id),
            configs_root=configs_root,
        )
        return BatfishSnapshotRead.model_validate(record)
    except BatfishSnapshotNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except BatfishServiceError as e:
        logging_utility.error(f"refresh_snapshot failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


# ── LIST ──────────────────────────────────────────────────────────────────────


@router.get(
    "/batfish/snapshots",
    response_model=list[BatfishSnapshotRead],
    status_code=status.HTTP_200_OK,
    summary="List all snapshots owned by the caller",
)
def list_snapshots(
    user_id: Optional[str] = Query(None, description="Admin override"),
    service: BatfishService = Depends(get_service),
    auth_key: ApiKeyModel = Depends(get_api_key),
):
    records = service.list_snapshots(user_id=resolve_user_id(auth_key, user_id))
    return [BatfishSnapshotRead.model_validate(r) for r in records]


# ── GET ───────────────────────────────────────────────────────────────────────


@router.get(
    "/batfish/snapshots/{snapshot_id}",
    response_model=BatfishSnapshotRead,
    status_code=status.HTTP_200_OK,
    summary="Get a single snapshot by id",
)
def get_snapshot(
    snapshot_id: str,
    user_id: Optional[str] = Query(None, description="Admin override"),
    service: BatfishService = Depends(get_service),
    auth_key: ApiKeyModel = Depends(get_api_key),
):
    try:
        return BatfishSnapshotRead.model_validate(
            service.get_snapshot(
                snapshot_id, user_id=resolve_user_id(auth_key, user_id)
            )
        )
    except BatfishSnapshotNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))


# ── DELETE ────────────────────────────────────────────────────────────────────


@router.delete(
    "/batfish/snapshots/{snapshot_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Soft-delete a snapshot",
)
def delete_snapshot(
    snapshot_id: str,
    user_id: Optional[str] = Query(None, description="Admin override"),
    service: BatfishService = Depends(get_service),
    auth_key: ApiKeyModel = Depends(get_api_key),
):
    try:
        service.delete_snapshot(snapshot_id, user_id=resolve_user_id(auth_key, user_id))
    except BatfishSnapshotNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except BatfishServiceError as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── SINGLE TOOL ───────────────────────────────────────────────────────────────


@router.post(
    "/batfish/snapshots/{snapshot_id}/tools/{tool_name}",
    status_code=status.HTTP_200_OK,
    summary="Run a single RCA tool — LLM function-call endpoint",
)
async def run_tool(
    snapshot_id: str,
    tool_name: str,
    user_id: Optional[str] = Query(None, description="Admin override"),
    service: BatfishService = Depends(get_service),
    auth_key: ApiKeyModel = Depends(get_api_key),
):
    try:
        result = await service.run_tool(
            user_id=resolve_user_id(auth_key, user_id),
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
    summary="Run all RCA tools concurrently",
)
async def run_all_tools(
    snapshot_id: str,
    user_id: Optional[str] = Query(None, description="Admin override"),
    service: BatfishService = Depends(get_service),
    auth_key: ApiKeyModel = Depends(get_api_key),
):
    try:
        results = await service.run_all_tools(
            user_id=resolve_user_id(auth_key, user_id),
            snapshot_id=snapshot_id,
        )
        return {"snapshot_id": snapshot_id, "results": results}
    except BatfishSnapshotNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except BatfishServiceError as e:
        logging_utility.error(f"run_all_tools failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


# ── TOOL MANIFEST ─────────────────────────────────────────────────────────────


@router.get("/batfish/tools", status_code=status.HTTP_200_OK)
def list_tools():
    return {"tools": TOOLS}


# ── HEALTH ────────────────────────────────────────────────────────────────────


@router.get("/batfish/health", status_code=status.HTTP_200_OK)
def batfish_health(service: BatfishService = Depends(get_service)):
    try:
        return service.check_health()
    except ConnectionError as e:
        raise HTTPException(status_code=503, detail=str(e))
