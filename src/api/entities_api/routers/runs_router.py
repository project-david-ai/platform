import asyncio
import json
from typing import Any, Dict, Literal, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from projectdavid_common import UtilsInterface, ValidationInterface
from projectdavid_common.schemas.enums import StatusEnum
from pydantic import ValidationError
from sqlalchemy.orm import Session
from sse_starlette.sse import EventSourceResponse
from starlette import status

from src.api.entities_api.db.database import \
    SessionLocal  # Needed for the fixed SSE endpoint
# The `get_db` dependency is now only needed for the admin check.
from src.api.entities_api.dependencies import get_api_key, get_db
from src.api.entities_api.models.models import ApiKey as ApiKeyModel
from src.api.entities_api.models.models import User as UserModel
from src.api.entities_api.services.actions_service import ActionService
from src.api.entities_api.services.runs_service import RunService

ent_validator = ValidationInterface()
logging_utility = UtilsInterface.LoggingUtility()
router = APIRouter()


@router.post("/runs", response_model=ValidationInterface.Run)
def create_run(
    run: ValidationInterface.RunCreate,
    auth_key: ApiKeyModel = Depends(get_api_key),
):
    user_id = auth_key.user_id
    logging_utility.info("[%s] Creating run for thread %s", user_id, run.thread_id)
    # --- FIX APPLIED HERE ---
    run_service = RunService()
    try:
        new_run = run_service.create_run(run, user_id=user_id)
        logging_utility.info("Run created successfully: %s", new_run.id)
        return new_run
    except HTTPException as e:
        logging_utility.error("HTTP error during run creation: %s", str(e))
        raise
    except Exception as e:
        logging_utility.error("Unexpected error during run creation: %s", str(e))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred.",
        )


@router.get("/runs/{run_id}", response_model=ValidationInterface.RunReadDetailed)
def retrieve_run(
    run_id: str,
    auth_key: ApiKeyModel = Depends(get_api_key),
):
    logging_utility.info(f"[{auth_key.user_id}] Retrieving run ID: {run_id}")
    # --- FIX APPLIED HERE ---
    run_service = RunService()
    try:
        run = run_service.retrieve_run(run_id)
        logging_utility.info(f"Run retrieved successfully: {run_id}")
        return run
    except HTTPException as e:
        logging_utility.error(f"HTTP error retrieving run {run_id}: {str(e)}")
        raise e
    except Exception as e:
        logging_utility.error(f"Unexpected error retrieving run {run_id}: {str(e)}")
        raise HTTPException(status_code=500, detail="An unexpected error occurred.")


@router.put("/runs/{run_id}/status", response_model=ValidationInterface.Run)
def update_run_status(
    run_id: str,
    status_update: ValidationInterface.RunStatusUpdate,
    db: Session = Depends(get_db),  # 'db' is kept here for the admin check only
    auth_key: ApiKeyModel = Depends(get_api_key),
):
    logging_utility.info(
        f"[{auth_key.user_id}] Updating status of run {run_id} → {status_update.status}"
    )
    # This direct DB access for an admin check is an exception and can remain.
    requesting_admin = (
        db.query(UserModel).filter(UserModel.id == auth_key.user_id).first()
    )
    if not requesting_admin or not requesting_admin.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin privileges required to create users.",
        )

    # --- FIX APPLIED HERE ---
    run_service = RunService()
    try:
        updated_run = run_service.update_run_status(run_id, status_update.status)
        logging_utility.info(f"Run status updated: {run_id}")
        return updated_run
    except ValidationError as e:
        raise HTTPException(status_code=422, detail=f"Validation error: {e.errors()}")
    except HTTPException as e:
        raise e
    except Exception as e:
        raise HTTPException(status_code=500, detail="An unexpected error occurred.")


@router.put("/runs/{run_id}/metadata", response_model=ValidationInterface.Run)
def update_run_metadata(
    run_id: str,
    payload: Dict[str, Any] = Body(...),
    auth_key: ApiKeyModel = Depends(get_api_key),
):
    logging_utility.info("[%s] Updating metadata for run %s", auth_key.user_id, run_id)
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Body must be a JSON object")
    metadata = (
        payload["metadata"]
        if "metadata" in payload and isinstance(payload["metadata"], dict)
        else payload
    )
    # --- FIX APPLIED HERE ---
    svc = RunService()
    updated = svc.update_run(run_id, metadata, user_id=auth_key.user_id)
    return updated


@router.post("/runs/{run_id}/cancel", response_model=ValidationInterface.Run)
def cancel_run(
    run_id: str,
    auth_key: ApiKeyModel = Depends(get_api_key),
):
    logging_utility.info(f"[{auth_key.user_id}] Cancelling run {run_id}")
    # --- FIX APPLIED HERE ---
    run_service = RunService()
    try:
        cancelled_run = run_service.cancel_run(run_id)
        logging_utility.info(f"Run cancelled successfully: {run_id}")
        return cancelled_run
    except HTTPException as e:
        raise e
    except Exception as e:
        raise HTTPException(status_code=500, detail="An unexpected error occurred.")


@router.get(
    "/runs/{run_id}/events",
    summary="Stream run‑lifecycle events (SSE)",
    response_class=EventSourceResponse,
)
async def stream_run_events(
    request: Request,
    run_id: str,
    auth_key: ApiKeyModel = Depends(get_api_key),
):
    # --- CRITICAL FIX APPLIED HERE ---
    # Services are now instantiated once. Their methods handle sessions internally,
    # so they are safe to reuse without holding a connection.
    run_svc = RunService()
    action_svc = ActionService()

    async def event_generator():
        while True:
            if await request.is_disconnected():
                break
            # Each call to a service method will use its own short-lived session.
            run = run_svc.retrieve_run(run_id)
            if not run:
                yield {"event": "error", "data": '{"msg":"run not found"}'}
                break
            if run.status == StatusEnum.pending_action:
                pending = action_svc.get_pending_actions(run_id)
                if pending:
                    for act in pending:
                        data = act.dict() if hasattr(act, "dict") else act
                        yield {"event": "action_required", "data": json.dumps(data)}
                    break  # Stop streaming after sending the required action.
            await asyncio.sleep(0.5)

    return EventSourceResponse(event_generator())


@router.get("/runs", response_model=ValidationInterface.RunListResponse)
def list_runs(
    limit: int = Query(20, ge=1, le=100),
    order: Literal["asc", "desc"] = Query("asc"),
    thread_id: Optional[str] = Query(None),
    auth_key: ApiKeyModel = Depends(get_api_key),
):
    user_id = auth_key.user_id
    logging_utility.info(
        "[%s] Listing runs (limit=%s, order=%s, thread_id=%s)",
        user_id,
        limit,
        order,
        thread_id,
    )
    # --- FIX APPLIED HERE ---
    svc = RunService()
    try:
        runs, has_more = svc.list_runs(
            user_id=user_id, limit=limit, order=order, thread_id=thread_id
        )
        return {
            "object": "list",
            "data": runs,
            "first_id": runs[0].id if runs else None,
            "last_id": runs[-1].id if runs else None,
            "has_more": has_more,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail="An unexpected error occurred.")


@router.get(
    "/threads/{thread_id}/runs", response_model=ValidationInterface.RunListResponse
)
def list_runs_for_thread(
    thread_id: str,
    limit: int = Query(20, ge=1, le=100),
    order: Literal["asc", "desc"] = Query("asc"),
    auth_key: ApiKeyModel = Depends(get_api_key),
):
    user_id = auth_key.user_id
    logging_utility.info(
        "[%s] Listing runs for thread %s (limit=%s, order=%s)",
        user_id,
        thread_id,
        limit,
        order,
    )
    # --- FIX APPLIED HERE ---
    svc = RunService()
    try:
        runs, has_more = svc.list_runs(
            user_id=user_id, limit=limit, order=order, thread_id=thread_id
        )
        return {
            "object": "list",
            "data": runs,
            "first_id": runs[0].id if runs else None,
            "last_id": runs[-1].id if runs else None,
            "has_more": has_more,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail="An unexpected error occurred.")
