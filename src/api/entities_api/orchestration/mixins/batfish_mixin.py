# src/api/entities_api/orchestration/mixins/batfish_mixin.py
from __future__ import annotations

import asyncio
import json
from typing import Any, AsyncGenerator, Dict, Optional

from projectdavid_common import ToolValidator
from projectdavid_common.utilities.logging_service import LoggingUtility
from projectdavid_common.validation import StatusEnum

LOG = LoggingUtility()

# ---------------------------------------------------------------------------
# TODO: Replace with programmatic injection via assistant/user settings.
# This will be resolved from the user's active incident context — likely
# stored in assistant metadata or a dedicated snapshot_id field — once the
# end-to-end agent flow has been validated.
# ---------------------------------------------------------------------------
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

    Each public handler corresponds to one LLM-callable tool.
    All calls are ownership-enforced server-side via snapshot_id + user_id.

    user_id is forwarded explicitly on every client call so that platform
    clients mounted with an admin API key correctly scope operations to the
    owning user — mirroring the fix applied to NetworkInventoryMixin.

    Tools:
      - refresh_snapshot          → ingest configs + load into Batfish
      - get_snapshot              → retrieve snapshot record by ID
      - list_snapshots            → list all snapshots for this user
      - delete_snapshot           → soft-delete a snapshot
      - run_batfish_tool          → run a single named RCA tool
      - run_all_batfish_tools     → run all RCA tools concurrently
    """

    # ------------------------------------------------------------------
    # 1. ERROR FORMATTING
    # ------------------------------------------------------------------

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
                "Call 'list_snapshots' to retrieve valid IDs, or call 'refresh_snapshot' to create a new one."
            )

        if "not loaded" in error_lower:
            return (
                f"⚠️ Snapshot not loaded in Batfish for tool '{tool_name}'.\n"
                "SYSTEM INSTRUCTION: Call 'refresh_snapshot' to re-ingest and reload the snapshot before retrying."
            )

        return (
            f"❌ Batfish Tool '{tool_name}' Error: {error_content}\n"
            "SYSTEM INSTRUCTION: Review arguments. If error persists, stop using this tool and report the failure."
        )

    # ------------------------------------------------------------------
    # 2. CORE EXECUTION ENGINE
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
        user_id: Optional[str] = None,
    ) -> AsyncGenerator[str, None]:
        """
        Shared execution engine for all Batfish tool handlers.
        user_id is forwarded to every client call as a query param override,
        allowing admin-keyed platform clients to act on behalf of real users.
        """
        ts_start = asyncio.get_event_loop().time()

        # --- [1] VALIDATING ---
        yield _status(run_id, tool_name, "Validating parameters...")

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

            # Resolve snapshot_id — LLM may omit it during preliminary testing.
            # Falls back to _DEFAULT_SNAPSHOT_ID until programmatic injection
            # via assistant settings is implemented.
            snapshot_id = arguments_dict.get("snapshot_id") or _DEFAULT_SNAPSHOT_ID
            if not arguments_dict.get("snapshot_id"):
                LOG.info(
                    "BATFISH ▸ snapshot_id not provided by LLM — "
                    "using hardcoded fallback: %s",
                    snapshot_id,
                )

            if tool_name == "refresh_snapshot":
                yield _status(
                    run_id,
                    tool_name,
                    f"Ingesting configs and loading snapshot '{arguments_dict['snapshot_name']}'...",
                )
                record = await asyncio.to_thread(
                    self.project_david_client.batfish.refresh_snapshot,
                    snapshot_name=arguments_dict["snapshot_name"],
                    configs_root=arguments_dict.get("configs_root"),
                    user_id=user_id,  # ← admin override
                )
                res = record.model_dump() if record else None

            elif tool_name == "get_snapshot":
                yield _status(
                    run_id, tool_name, f"Retrieving snapshot '{snapshot_id}'..."
                )
                record = await asyncio.to_thread(
                    self.project_david_client.batfish.get_snapshot,
                    snapshot_id=snapshot_id,
                    user_id=user_id,  # ← admin override
                )
                res = record.model_dump() if record else None

            elif tool_name == "list_snapshots":
                yield _status(run_id, tool_name, "Listing all snapshots...")
                records = await asyncio.to_thread(
                    self.project_david_client.batfish.list_snapshots,
                    user_id=user_id,  # ← admin override
                )
                res = [r.model_dump() for r in records] if records else []

            elif tool_name == "delete_snapshot":
                yield _status(
                    run_id, tool_name, f"Soft-deleting snapshot '{snapshot_id}'..."
                )
                success = await asyncio.to_thread(
                    self.project_david_client.batfish.delete_snapshot,
                    snapshot_id=snapshot_id,
                    user_id=user_id,  # ← admin override
                )
                res = {"deleted": success, "snapshot_id": snapshot_id}

            elif tool_name == "run_batfish_tool":
                named_tool = arguments_dict["batfish_tool_name"]
                yield _status(
                    run_id,
                    tool_name,
                    f"Running RCA tool '{named_tool}' against snapshot '{snapshot_id}'...",
                )
                res = await asyncio.to_thread(
                    self.project_david_client.batfish.run_tool,
                    snapshot_id=snapshot_id,
                    tool_name=named_tool,
                    user_id=user_id,  # ← admin override
                )

            elif tool_name == "run_all_batfish_tools":
                yield _status(
                    run_id,
                    tool_name,
                    f"Running all RCA tools against snapshot '{snapshot_id}'...",
                )
                res = await asyncio.to_thread(
                    self.project_david_client.batfish.run_all_tools,
                    snapshot_id=snapshot_id,
                    user_id=user_id,  # ← admin override
                )

            else:
                raise ValueError(f"Unknown Batfish tool: {tool_name}")

            # --- [4] RESULT ---
            if res is not None and res != [] and res != {}:
                final_content = json.dumps(res, indent=2)
                yield _status(run_id, tool_name, "Analysis complete.", status="success")
                is_error = False
            else:
                final_content = self._format_batfish_error(
                    tool_name, "Empty result (not found)", arguments_dict
                )
                yield _status(
                    run_id, tool_name, "Query yielded no results.", status="warning"
                )
                is_error = True

            # --- [5] UPDATE & SUBMIT ---
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
    # 3. PUBLIC HANDLERS
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
        """Ingest configs and load a new snapshot into Batfish."""
        async for event in self._execute_batfish_tool_logic(
            tool_name="refresh_snapshot",
            required_schema={"snapshot_name": str},
            thread_id=thread_id,
            run_id=run_id,
            assistant_id=assistant_id,
            arguments_dict=arguments_dict,
            tool_call_id=tool_call_id,
            decision=decision,
            user_id=user_id,
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
        """Retrieve a snapshot record by its opaque ID."""
        async for event in self._execute_batfish_tool_logic(
            tool_name="get_snapshot",
            required_schema={"snapshot_id": str},
            thread_id=thread_id,
            run_id=run_id,
            assistant_id=assistant_id,
            arguments_dict=arguments_dict,
            tool_call_id=tool_call_id,
            decision=decision,
            user_id=user_id,
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
        """List all snapshots owned by the authenticated user."""
        async for event in self._execute_batfish_tool_logic(
            tool_name="list_snapshots",
            required_schema={},
            thread_id=thread_id,
            run_id=run_id,
            assistant_id=assistant_id,
            arguments_dict=arguments_dict,
            tool_call_id=tool_call_id,
            decision=decision,
            user_id=user_id,
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
        """Soft-delete a snapshot by its opaque ID."""
        async for event in self._execute_batfish_tool_logic(
            tool_name="delete_snapshot",
            required_schema={"snapshot_id": str},
            thread_id=thread_id,
            run_id=run_id,
            assistant_id=assistant_id,
            arguments_dict=arguments_dict,
            tool_call_id=tool_call_id,
            decision=decision,
            user_id=user_id,
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
        """
        Run a single named RCA tool against the active snapshot.
        snapshot_id is optional — falls back to _DEFAULT_SNAPSHOT_ID
        until programmatic injection via assistant settings is implemented.
        """
        async for event in self._execute_batfish_tool_logic(
            tool_name="run_batfish_tool",
            required_schema={"batfish_tool_name": str},  # snapshot_id optional for now
            thread_id=thread_id,
            run_id=run_id,
            assistant_id=assistant_id,
            arguments_dict=arguments_dict,
            tool_call_id=tool_call_id,
            decision=decision,
            user_id=user_id,
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
        """
        Run all RCA tools concurrently against the active snapshot.
        snapshot_id is optional — falls back to _DEFAULT_SNAPSHOT_ID
        until programmatic injection via assistant settings is implemented.
        """
        async for event in self._execute_batfish_tool_logic(
            tool_name="run_all_batfish_tools",
            required_schema={},  # snapshot_id optional for now
            thread_id=thread_id,
            run_id=run_id,
            assistant_id=assistant_id,
            arguments_dict=arguments_dict,
            tool_call_id=tool_call_id,
            decision=decision,
            user_id=user_id,
        ):
            yield event
