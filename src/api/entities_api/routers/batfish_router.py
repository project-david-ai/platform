# src/api/entities_api/routers/batfish_router.py
"""
Batfish Router — Tenant-Isolated
=================================

Every endpoint resolves the caller's user_id from their API key,
then enforces ownership via BatfishService._require_snapshot_access().
No caller can touch another tenant's snapshot.

Endpoints:
  POST /batfish/snapshot/refresh              → ingest + load (creates or refreshes)
  GET  /batfish/snapshots                     → list caller's snapshots
  GET  /batfish/snapshot/{snapshot_name}      → get single snapshot record
  DELETE /batfish/snapshot/{snapshot_name}    → soft-delete snapshot
  POST /batfish/tool/{tool_name}              → single RCA tool call
  POST /batfish/tools/all                     → all tools concurrently
  GET  /batfish/tools                         → list available tools
  GET  /batfish/health                        → Batfish reachability
"""

from typing import List

from fastapi import APIRouter, Depends, HTTPException, Query, status
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


# ── Dependency — service with DB session injected ─────────────────────────────


def get_service(db: Session = Depends(get_db)) -> BatfishService:
    return BatfishService(db)


# ── SNAPSHOT REFRESH ──────────────────────────────────────────────────────────


@router.post(
    "/batfish/snapshot/refresh",
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
    Recursively ingests configs from configs_root (defaults to GNS3_ROOT),
    stages them under a namespaced snapshot_key ({user_id}_{snapshot_name}),
    and loads the snapshot into Batfish.
    Creates the snapshot record if new, refreshes it if it already exists.
    """
    try:
        record = service.refresh_snapshot(
            user_id=auth_key.user_id,
            snapshot_name=snapshot_name,
            configs_root=configs_root,
        )
        return {
            "status": "ready",
            "snapshot_name": record.snapshot_name,
            "snapshot_key": record.snapshot_key,
            "device_count": record.device_count,
            "devices": record.devices,
        }
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
    status_code=status.HTTP_200_OK,
    summary="List all snapshots owned by the caller",
)
def list_snapshots(
    service: BatfishService = Depends(get_service),
    auth_key: ApiKeyModel = Depends(get_api_key),
):
    records = service.list_snapshots(user_id=auth_key.user_id)
    return [
        {
            "snapshot_name": r.snapshot_name,
            "snapshot_key": r.snapshot_key,
            "device_count": r.device_count,
            "status": r.status.value,
            "last_ingested_at": r.last_ingested_at,
            "updated_at": r.updated_at,
        }
        for r in records
    ]


# ── GET SNAPSHOT ──────────────────────────────────────────────────────────────


@router.get(
    "/batfish/snapshot/{snapshot_name}",
    status_code=status.HTTP_200_OK,
    summary="Get a single snapshot record",
)
def get_snapshot(
    snapshot_name: str,
    service: BatfishService = Depends(get_service),
    auth_key: ApiKeyModel = Depends(get_api_key),
):
    try:
        r = service.get_snapshot(snapshot_name, user_id=auth_key.user_id)
        return {
            "snapshot_name": r.snapshot_name,
            "snapshot_key": r.snapshot_key,
            "device_count": r.device_count,
            "devices": r.devices,
            "status": r.status.value,
            "configs_root": r.configs_root,
            "last_ingested_at": r.last_ingested_at,
            "created_at": r.created_at,
            "updated_at": r.updated_at,
        }
    except BatfishSnapshotNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))


# ── DELETE SNAPSHOT ───────────────────────────────────────────────────────────


@router.delete(
    "/batfish/snapshot/{snapshot_name}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Soft-delete a snapshot",
)
def delete_snapshot(
    snapshot_name: str,
    service: BatfishService = Depends(get_service),
    auth_key: ApiKeyModel = Depends(get_api_key),
):
    try:
        service.delete_snapshot(snapshot_name, user_id=auth_key.user_id)
    except BatfishSnapshotNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except BatfishServiceError as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── SINGLE TOOL CALL ──────────────────────────────────────────────────────────


@router.post(
    "/batfish/tool/{tool_name}",
    status_code=status.HTTP_200_OK,
    summary="Run a single RCA tool (LLM function-call endpoint)",
)
async def run_tool(
    tool_name: str,
    snapshot_name: str = Query(..., description="Snapshot to run tool against"),
    service: BatfishService = Depends(get_service),
    auth_key: ApiKeyModel = Depends(get_api_key),
):
    """
    Ownership-enforced single tool call.
    This is the endpoint the LLM agent hits for each function call.
    """
    try:
        result = await service.run_tool(
            user_id=auth_key.user_id,
            snapshot_name=snapshot_name,
            tool_name=tool_name,
        )
        return {
            "tool": tool_name,
            "snapshot_name": snapshot_name,
            "result": result,
        }
    except BatfishSnapshotNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except BatfishToolError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except BatfishServiceError as e:
        logging_utility.error(f"Tool '{tool_name}' failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


# ── ALL TOOLS ─────────────────────────────────────────────────────────────────


@router.post(
    "/batfish/tools/all",
    status_code=status.HTTP_200_OK,
    summary="Run all RCA tools concurrently",
)
async def run_all_tools(
    snapshot_name: str = Query(..., description="Snapshot to run all tools against"),
    service: BatfishService = Depends(get_service),
    auth_key: ApiKeyModel = Depends(get_api_key),
):
    try:
        results = await service.run_all_tools(
            user_id=auth_key.user_id,
            snapshot_name=snapshot_name,
        )
        return {"snapshot_name": snapshot_name, "results": results}
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
def batfish_health(
    service: BatfishService = Depends(get_service),
):
    try:
        return service.check_health()
    except ConnectionError as e:
        raise HTTPException(status_code=503, detail=str(e))
