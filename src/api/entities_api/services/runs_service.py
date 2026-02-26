# src/api/entities_api/services/run_service.py
import json
import time
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from fastapi import HTTPException
from projectdavid_common import UtilsInterface, ValidationInterface
from projectdavid_common.utilities.logging_service import LoggingUtility
from projectdavid_common.validation import StatusEnum
from sqlalchemy.orm import Session

from src.api.entities_api.db.database import SessionLocal
from src.api.entities_api.models.models import Assistant, Run

validator = ValidationInterface()

# Fields that are safe to update mid-run via update_run_fields.
# Anything not in this set is silently ignored, preventing accidental
# corruption of structural fields like assistant_id, thread_id, object, etc.
MUTABLE_RUN_FIELDS = {
    "started_at",
    "completed_at",
    "failed_at",
    "last_error",
    "incomplete_details",
    "current_turn",
    "usage",
    "meta_data",
}


class RunService:
    def __init__(self) -> None:
        self.logger = LoggingUtility()

    # -----------------------------------
    # Helpers
    # -----------------------------------
    @staticmethod
    def _to_epoch(value: Optional[Any]) -> Optional[int]:
        if value is None:
            return None
        if isinstance(value, (int, float)):
            return int(value)
        if isinstance(value, datetime):
            return int(value.timestamp())
        try:
            return int(str(value))
        except Exception:
            return None

    @staticmethod
    def _ensure_dict(value: Any) -> Dict[str, Any]:
        """Ensures Pydantic receives a dict, preventing validation crashes."""
        if value is None:
            return {}
        if isinstance(value, dict):
            return value
        if isinstance(value, str):
            try:
                v = json.loads(value)
                return v if isinstance(v, dict) else {}
            except Exception:
                return {}
        return {}

    def _get_run_or_404(self, run_id: str, db: Session) -> Run:
        run = db.query(Run).filter(Run.id == run_id).first()
        if not run:
            raise HTTPException(status_code=404, detail="Run not found")
        return run

    def _to_read_model(self, r: Run) -> validator.Run:
        """Standardized mapping to prevent 'missing field' errors in Pydantic."""
        return validator.Run(
            id=r.id,
            user_id=r.user_id,
            assistant_id=r.assistant_id,
            cancelled_at=self._to_epoch(r.cancelled_at),
            completed_at=self._to_epoch(r.completed_at),
            created_at=self._to_epoch(r.created_at),
            expires_at=self._to_epoch(r.expires_at) or 0,
            failed_at=self._to_epoch(r.failed_at),
            incomplete_details=r.incomplete_details,
            instructions=r.instructions or "",
            last_error=r.last_error,
            max_completion_tokens=r.max_completion_tokens,
            max_prompt_tokens=r.max_prompt_tokens,
            meta_data=self._ensure_dict(r.meta_data),
            model=r.model or "",
            object=r.object or "thread.run",
            parallel_tool_calls=bool(r.parallel_tool_calls),
            required_action=r.required_action,
            response_format=r.response_format or "text",
            started_at=self._to_epoch(r.started_at),
            status=(r.status.value if hasattr(r.status, "value") else r.status),
            thread_id=r.thread_id,
            tool_choice=r.tool_choice or "none",
            tools=(r.tools if r.tools is not None else []),
            truncation_strategy=r.truncation_strategy or "auto",
            usage=r.usage or {},
            temperature=r.temperature if r.temperature is not None else 0.7,
            top_p=r.top_p if r.top_p is not None else 0.9,
            tool_resources=self._ensure_dict(r.tool_resources),
        )

    # -----------------------------------
    # CRUD
    # -----------------------------------
    def create_run(
        self, run_data: validator.RunCreate, *, user_id: str
    ) -> validator.Run:
        with SessionLocal() as db:
            assistant = (
                db.query(Assistant)
                .filter(Assistant.id == run_data.assistant_id)
                .first()
            )
            if not assistant:
                raise HTTPException(status_code=404, detail="Assistant not found")

            tools_override = getattr(run_data, "tools", None)
            effective_tools = (
                tools_override
                if tools_override is not None
                else (assistant.tool_configs or [])
            )

            ts_value = "auto"
            if (
                hasattr(run_data, "truncation_strategy")
                and run_data.truncation_strategy
            ):
                ts_value = getattr(
                    run_data.truncation_strategy, "value", run_data.truncation_strategy
                )

            new_run = Run(
                id=UtilsInterface.IdentifierService.generate_run_id(),
                user_id=user_id,
                assistant_id=run_data.assistant_id,
                thread_id=run_data.thread_id,
                status=StatusEnum.queued,
                model=run_data.model or assistant.model,
                instructions=run_data.instructions or assistant.instructions,
                tools=effective_tools,
                temperature=getattr(run_data, "temperature", assistant.temperature),
                top_p=getattr(run_data, "top_p", assistant.top_p),
                max_turns=assistant.max_turns,
                agent_mode=assistant.agent_mode,
                meta_data=run_data.meta_data or {},
                tool_resources=getattr(
                    run_data, "tool_resources", assistant.tool_resources
                )
                or {},
                created_at=int(time.time()),
                expires_at=int(time.time() + 3600),
                object="thread.run",
                parallel_tool_calls=getattr(run_data, "parallel_tool_calls", True),
                response_format=getattr(run_data, "response_format", "text"),
                truncation_strategy=ts_value,
            )

            db.add(new_run)
            db.commit()
            db.refresh(new_run)
            self.logger.info("Run created successfully: %s", new_run.id)
            return self._to_read_model(new_run)

    def retrieve_run(self, run_id: str) -> validator.RunReadDetailed:
        with SessionLocal() as db:
            run = self._get_run_or_404(run_id, db)
            base_data = self._to_read_model(run).dict()
            return validator.RunReadDetailed(**base_data, actions=[])

    def update_run_status(self, run_id: str, new_status: str) -> validator.Run:
        with SessionLocal() as db:
            run = self._get_run_or_404(run_id, db)
            try:
                run.status = StatusEnum(new_status)
            except ValueError:
                raise HTTPException(
                    status_code=400, detail=f"Invalid status: {new_status}"
                )
            db.commit()
            db.refresh(run)
            return self._to_read_model(run)

    def update_run_fields(self, run_id: str, **kwargs) -> validator.Run:
        """
        Targeted field update for mid-run lifecycle writes.

        Accepts any subset of MUTABLE_RUN_FIELDS. All other kwargs are silently
        ignored so callers cannot accidentally corrupt structural columns.

        Designed to be called from within process_conversation at lifecycle
        boundaries — turn start, clean exit, error paths, and max-turns failsafe.

        Usage:
            svc.update_run_fields(run_id, started_at=int(time.time()), current_turn=1)
            svc.update_run_fields(run_id, completed_at=int(time.time()))
            svc.update_run_fields(run_id, failed_at=int(time.time()), last_error="...")
        """
        safe = {k: v for k, v in kwargs.items() if k in MUTABLE_RUN_FIELDS}

        if not safe:
            self.logger.warning(
                "update_run_fields called for %s with no valid fields: %s",
                run_id,
                list(kwargs.keys()),
            )
            return self.retrieve_run(run_id)

        with SessionLocal() as db:
            run = self._get_run_or_404(run_id, db)

            for field, value in safe.items():
                # Special case: meta_data always merges, never replaces
                if field == "meta_data" and isinstance(value, dict):
                    current = self._ensure_dict(run.meta_data)
                    current.update(value)
                    run.meta_data = current
                else:
                    setattr(run, field, value)

            db.commit()
            db.refresh(run)
            self.logger.info("Run %s fields updated: %s", run_id, list(safe.keys()))
            return self._to_read_model(run)

    def list_runs(
        self,
        *,
        user_id: str,
        limit: int = 20,
        order: str = "asc",
        thread_id: Optional[str] = None,
    ) -> Tuple[List[validator.Run], bool]:
        with SessionLocal() as db:
            q = db.query(Run).filter(Run.user_id == user_id)
            if thread_id:
                q = q.filter(Run.thread_id == thread_id)

            q = q.order_by(
                Run.created_at.asc() if order == "asc" else Run.created_at.desc()
            )

            rows = q.limit(limit + 1).all()
            has_more = len(rows) > limit
            rows = rows[:limit]

            return [self._to_read_model(r) for r in rows], has_more

    def cancel_run(self, run_id: str) -> validator.Run:
        with SessionLocal() as db:
            run = self._get_run_or_404(run_id, db)

            if run.status in {StatusEnum.completed, StatusEnum.cancelled}:
                raise HTTPException(status_code=400, detail="Run is already finished")

            run.status = StatusEnum.cancelled
            run.cancelled_at = int(time.time())

            db.commit()
            db.refresh(run)
            return self._to_read_model(run)

    def update_run(
        self,
        run_id: str,
        new_metadata: Dict[str, Any],
        *,
        user_id: Optional[str] = None,
    ) -> validator.Run:
        with SessionLocal() as db:
            run = self._get_run_or_404(run_id, db)

            if user_id and run.user_id != user_id:
                raise HTTPException(status_code=404, detail="Run not found")

            current = self._ensure_dict(run.meta_data)
            current.update(self._ensure_dict(new_metadata))
            run.meta_data = current

            db.commit()
            db.refresh(run)
            return self._to_read_model(run)
