# src/api/entities_api/orchestration/mixins/batfish_mixin.py
from __future__ import annotations

import asyncio
import json
from typing import Any, AsyncGenerator, Dict, Optional

from projectdavid_common import ToolValidator
from projectdavid_common.utilities.logging_service import LoggingUtility
from projectdavid_common.validation import StatusEnum

LOG = LoggingUtility()

_DEFAULT_SNAPSHOT_ID = "incident_001"


def _status(run_id: str, tool: str, message: str, status: str = "running") -> str:
    return json.dumps(
        {
            "type": "engineer_status",
            "run_id": run_id,
            "tool": tool,
            "status": status,
            "message": message,
        }
    )


class BatfishMixin:
    """
    Drives the **Agentic Network Analysis Tools** powered by Batfish.
    """

    @staticmethod
    def _format_batfish_error(
        tool_name: str, error_content: str, inputs: Dict[str, Any]
    ) -> str:
        if not error_content:
            error_content = "Unknown Error (Empty Response)"

        error_lower = error_content.lower()

        if "validation" in error_lower or "missing" in error_lower:
            return (
                f"❌ SCHEMA ERROR: Invalid arguments for '{tool_name}'.\n"
                f"ERROR DETAILS: {error_content}\n"
                f"PROVIDED ARGS: {json.dumps(inputs)}\n"
                "SYSTEM INSTRUCTION: Check the tool definition and retry with valid arguments."
            )

        if "not found" in error_lower:
            return (
                f"⚠️ Snapshot not found for tool '{tool_name}'.\n"
                "SYSTEM INSTRUCTION: The snapshot_id may be invalid or already deleted. "
            )

        if "not loaded" in error_lower:
            return (
                f"⚠️ Snapshot not loaded in Batfish for tool '{tool_name}'.\n"
                "SYSTEM INSTRUCTION: Call 'refresh_snapshot' to re-ingest and reload."
            )

        return (
            f"❌ Batfish Tool '{tool_name}' Error: {error_content}\n"
            "SYSTEM INSTRUCTION: Review arguments or report the failure."
        )

    # ------------------------------------------------------------------
    # CORE EXECUTION ENGINE
    # ------------------------------------------------------------------

    async def _execute_batfish_tool_logic(
        self,
        tool_name: str,
        required_schema: Dict[str, Any],
        thread_id: str,
        run_id: str,
        assistant_id: str,
        arguments_dict: Dict[str, Any],
        tool_call_id: Optional[str],
        decision: Optional[Dict],
        user_id: Optional[str] = None,  # <-- PROPAGATED
    ) -> AsyncGenerator[str, None]:
        ts_start = asyncio.get_event_loop().time()

        yield _status(run_id, tool_name, "Validating parameters...")

        # --- [1] VALIDATION ---
        validator = ToolValidator()
        validator.schema_registry = {tool_name: required_schema}
        validation_error = validator.validate_args(tool_name, arguments_dict)

        if validation_error:
            LOG.warning(f"{tool_name} ▸ Validation Failed: {validation_error}")
            yield _status(
                run_id,
                tool_name,
                f"Validation failed: {validation_error}",
                status="error",
            )
            await self.submit_tool_output(
                thread_id=thread_id,
                assistant_id=assistant_id,
                tool_call_id=tool_call_id,
                content=self._format_batfish_error(
                    tool_name, f"Validation Error: {validation_error}", arguments_dict
                ),
                action=None,
                is_error=True,
            )
            return

        # --- [2] CREATE ACTION ---
        yield _status(run_id, tool_name, "Initializing tool action...")
        action = await asyncio.to_thread(
            self.project_david_client.actions.create_action,
            tool_name=tool_name,
            run_id=run_id,
            tool_call_id=tool_call_id,
            function_args=arguments_dict,
            decision=decision,
        )

        # --- [3] DISPATCH ---
        try:
            res = None
            snapshot_id = arguments_dict.get("snapshot_id") or _DEFAULT_SNAPSHOT_ID

            if not arguments_dict.get("snapshot_id"):
                LOG.info(
                    f"BATFISH ▸ snapshot_id omitted — fallback: {_DEFAULT_SNAPSHOT_ID}"
                )

            if tool_name == "refresh_snapshot":
                yield _status(
                    run_id, tool_name, "Ingesting configs and loading snapshot..."
                )
                record = await asyncio.to_thread(
                    self.project_david_client.batfish.refresh_snapshot,
                    snapshot_name=arguments_dict.get("snapshot_name"),
                    configs_root=arguments_dict.get("configs_root"),
                    user_id=user_id,  # <-- PLUMBED
                )
                res = record.model_dump() if record else None

            elif tool_name == "get_snapshot":
                yield _status(
                    run_id, tool_name, f"Retrieving snapshot '{snapshot_id}'..."
                )
                record = await asyncio.to_thread(
                    self.project_david_client.batfish.get_snapshot,
                    snapshot_id=snapshot_id,
                    user_id=user_id,  # <-- PLUMBED
                )
                res = record.model_dump() if record else None

            elif tool_name == "list_snapshots":
                yield _status(run_id, tool_name, "Listing snapshots...")
                records = await asyncio.to_thread(
                    self.project_david_client.batfish.list_snapshots,
                    user_id=user_id,  # <-- PLUMBED
                )
                res = [r.model_dump() for r in records] if records else []

            elif tool_name == "delete_snapshot":
                yield _status(
                    run_id, tool_name, f"Deleting snapshot '{snapshot_id}'..."
                )
                success = await asyncio.to_thread(
                    self.project_david_client.batfish.delete_snapshot,
                    snapshot_id=snapshot_id,
                    user_id=user_id,  # <-- PLUMBED
                )
                res = {"deleted": success, "snapshot_id": snapshot_id}

            elif tool_name == "run_batfish_tool":
                named_tool = arguments_dict["batfish_tool_name"]
                yield _status(
                    run_id, tool_name, f"Running RCA analysis: {named_tool}..."
                )
                res = await asyncio.to_thread(
                    self.project_david_client.batfish.run_tool,
                    snapshot_id=snapshot_id,
                    tool_name=named_tool,
                    user_id=user_id,  # <-- PLUMBED
                )

            elif tool_name == "run_all_batfish_tools":
                yield _status(run_id, tool_name, "Running all RCA tools...")
                res = await asyncio.to_thread(
                    self.project_david_client.batfish.run_all_tools,
                    snapshot_id=snapshot_id,
                    user_id=user_id,  # <-- PLUMBED
                )
            else:
                raise ValueError(f"Unknown Batfish tool: {tool_name}")

            # --- [4] RESULT RESOLUTION ---
            if res is not None and res != [] and res != {}:
                final_content = json.dumps(res, indent=2)
                yield _status(run_id, tool_name, "Analysis complete.", status="success")
                is_error = False
            else:
                final_content = (
                    "No configuration issues or results found during Batfish execution."
                )
                yield _status(
                    run_id,
                    tool_name,
                    "Analysis complete (no issues found).",
                    status="success",
                )
                is_error = False

            # --- [5] UPDATE DB & SUBMIT OUTPUT ---
            await asyncio.to_thread(
                self.project_david_client.actions.update_action,
                action_id=action.id,
                status=(
                    StatusEnum.completed.value
                    if not is_error
                    else StatusEnum.failed.value
                ),
            )
            await self.submit_tool_output(
                thread_id=thread_id,
                assistant_id=assistant_id,
                tool_call_id=tool_call_id,
                content=final_content,
                action=action,
                is_error=is_error,
            )

            LOG.info(
                "[%s] %s completed in %.2fs",
                run_id,
                tool_name,
                asyncio.get_event_loop().time() - ts_start,
            )

        except Exception as exc:
            LOG.error(f"[{run_id}] {tool_name} HARD FAILURE: {exc}", exc_info=True)
            yield _status(
                run_id, tool_name, f"Critical failure: {str(exc)}", status="error"
            )
            await asyncio.to_thread(
                self.project_david_client.actions.update_action,
                action_id=action.id,
                status=StatusEnum.failed.value,
            )
            await self.submit_tool_output(
                thread_id=thread_id,
                assistant_id=assistant_id,
                tool_call_id=tool_call_id,
                content=self._format_batfish_error(tool_name, str(exc), arguments_dict),
                action=action,
                is_error=True,
            )

    # ------------------------------------------------------------------
    # PUBLIC HANDLERS
    # ------------------------------------------------------------------

    async def handle_refresh_snapshot(
        self,
        thread_id: str,
        run_id: str,
        assistant_id: str,
        arguments_dict: Dict[str, Any],
        tool_call_id: Optional[str] = None,
        decision: Optional[Dict] = None,
        user_id: Optional[str] = None,
    ) -> AsyncGenerator[str, None]:
        async for event in self._execute_batfish_tool_logic(
            "refresh_snapshot",
            {"snapshot_name": str},
            thread_id,
            run_id,
            assistant_id,
            arguments_dict,
            tool_call_id,
            decision,
            user_id,
        ):
            yield event

    async def handle_get_snapshot(
        self,
        thread_id: str,
        run_id: str,
        assistant_id: str,
        arguments_dict: Dict[str, Any],
        tool_call_id: Optional[str] = None,
        decision: Optional[Dict] = None,
        user_id: Optional[str] = None,
    ) -> AsyncGenerator[str, None]:
        async for event in self._execute_batfish_tool_logic(
            "get_snapshot",
            {"snapshot_id": str},
            thread_id,
            run_id,
            assistant_id,
            arguments_dict,
            tool_call_id,
            decision,
            user_id,
        ):
            yield event

    async def handle_list_snapshots(
        self,
        thread_id: str,
        run_id: str,
        assistant_id: str,
        arguments_dict: Dict[str, Any],
        tool_call_id: Optional[str] = None,
        decision: Optional[Dict] = None,
        user_id: Optional[str] = None,
    ) -> AsyncGenerator[str, None]:
        async for event in self._execute_batfish_tool_logic(
            "list_snapshots",
            {},
            thread_id,
            run_id,
            assistant_id,
            arguments_dict,
            tool_call_id,
            decision,
            user_id,
        ):
            yield event

    async def handle_delete_snapshot(
        self,
        thread_id: str,
        run_id: str,
        assistant_id: str,
        arguments_dict: Dict[str, Any],
        tool_call_id: Optional[str] = None,
        decision: Optional[Dict] = None,
        user_id: Optional[str] = None,
    ) -> AsyncGenerator[str, None]:
        async for event in self._execute_batfish_tool_logic(
            "delete_snapshot",
            {"snapshot_id": str},
            thread_id,
            run_id,
            assistant_id,
            arguments_dict,
            tool_call_id,
            decision,
            user_id,
        ):
            yield event

    async def handle_run_batfish_tool(
        self,
        thread_id: str,
        run_id: str,
        assistant_id: str,
        arguments_dict: Dict[str, Any],
        tool_call_id: Optional[str] = None,
        decision: Optional[Dict] = None,
        user_id: Optional[str] = None,
    ) -> AsyncGenerator[str, None]:
        async for event in self._execute_batfish_tool_logic(
            "run_batfish_tool",
            {"batfish_tool_name": str},
            thread_id,
            run_id,
            assistant_id,
            arguments_dict,
            tool_call_id,
            decision,
            user_id,
        ):
            yield event

    async def handle_run_all_batfish_tools(
        self,
        thread_id: str,
        run_id: str,
        assistant_id: str,
        arguments_dict: Dict[str, Any],
        tool_call_id: Optional[str] = None,
        decision: Optional[Dict] = None,
        user_id: Optional[str] = None,
    ) -> AsyncGenerator[str, None]:
        async for event in self._execute_batfish_tool_logic(
            "run_all_batfish_tools",
            {},
            thread_id,
            run_id,
            assistant_id,
            arguments_dict,
            tool_call_id,
            decision,
            user_id,
        ):
            yield event
