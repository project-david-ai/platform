from __future__ import annotations

import asyncio
import json
from typing import Any, AsyncGenerator, Dict, Optional

from projectdavid_common import ToolValidator
from projectdavid_common.utilities.logging_service import LoggingUtility
from projectdavid_common.validation import StatusEnum

LOG = LoggingUtility()


def _status(run_id: str, tool: str, message: str, status: str = "running") -> str:
    """
    Emit a status event as raw JSON conforming to the stream EVENT_CONTRACT.

    Shape:
        {
            "type":    "engineer_status",
            "run_id":  "<uuid>",
            "tool":    "<tool_name>",
            "status":  "running" | "success" | "error" | "warning",
            "message": "<human readable>"
        }

    The SDK deserializes and routes this.
    """
    return json.dumps(
        {
            "type": "engineer_status",
            "run_id": run_id,
            "tool": tool,
            "status": status,
            "message": message,
        }
    )


class NetworkEngineerMixin:
    """
    Drives the **Agentic Network Engineering Tools** (Inventory Search, Device Lookups).

    Features:
    - Secure, isolated queries to the network inventory cache.
    - Standardized event emissions mirroring the WebSearchMixin.
    - Resilient error handling and validation logic.
    """

    # ------------------------------------------------------------------
    # 1. HELPER METHODS & LOGIC
    # ------------------------------------------------------------------

    @staticmethod
    def _format_engineer_tool_error(
        tool_name: str, error_content: str, inputs: Dict
    ) -> str:
        """Translates failures into actionable instructions for the LMM."""
        if not error_content:
            error_content = "Unknown Error (Empty Response)"

        error_lower = error_content.lower()

        if "validation error" in error_lower or "missing arguments" in error_lower:
            return (
                f"❌ SCHEMA ERROR: Invalid arguments for '{tool_name}'.\n"
                f"ERROR DETAILS: {error_content}\n"
                "SYSTEM INSTRUCTION: Check the tool definition and retry with valid arguments."
            )

        if "not found" in error_lower or "empty" in error_lower:
            return (
                f"⚠️ Tool '{tool_name}' returned no results.\n"
                f"SYSTEM INSTRUCTION: The requested device or group could not be found in the current inventory map. "
                "Consider asking the user to verify the hostname/group or upload a new inventory."
            )

        return (
            f"❌ Engineering Tool '{tool_name}' Error: {error_content}\n"
            "SYSTEM INSTRUCTION: Review arguments. If error persists, stop using this tool."
        )

    # ------------------------------------------------------------------
    # 2. CORE EXECUTION LOGIC (The Engine)
    # ------------------------------------------------------------------

    async def _execute_engineer_tool_logic(
        self,
        tool_name: str,
        required_keys: list[str],
        thread_id: str,
        run_id: str,
        assistant_id: str,
        arguments_dict: Dict[str, Any],
        tool_call_id: Optional[str],
        decision: Optional[Dict],
    ) -> AsyncGenerator[str, None]:
        """
        Shared core logic for Inventory Search and Device Lookup.
        """
        ts_start = asyncio.get_event_loop().time()

        # --- [1] STATUS: VALIDATING ---
        yield _status(run_id, tool_name, "Validating parameters...")

        # --- VALIDATION ---
        validator = ToolValidator()
        validator.schema_registry = {tool_name: required_keys}
        validation_error = validator.validate_args(tool_name, arguments_dict)

        if validation_error:
            LOG.warning(f"{tool_name} ▸ Validation Failed: {validation_error}")
            yield _status(run_id, tool_name, "Validation failed.", status="error")

            error_feedback = self._format_engineer_tool_error(
                tool_name, f"Validation Error: {validation_error}", arguments_dict
            )

            try:
                action = await asyncio.to_thread(
                    self.project_david_client.actions.create_action,
                    tool_name=tool_name,
                    run_id=run_id,
                    tool_call_id=tool_call_id,
                    function_args=arguments_dict,
                    decision=decision,
                )
                await self.submit_tool_output(
                    thread_id=thread_id,
                    assistant_id=assistant_id,
                    tool_call_id=tool_call_id,
                    content=error_feedback,
                    action=action,
                    is_error=True,
                )
                await asyncio.to_thread(
                    self.project_david_client.actions.update_action,
                    action_id=action.id,
                    status=StatusEnum.failed.value,
                )
            except Exception as e:
                LOG.error(f"Failed to submit validation error: {e}")

            return

        # --- [2] STATUS: CREATING ACTION ---
        yield _status(run_id, tool_name, "Initializing tool action...")

        try:
            action = await asyncio.to_thread(
                self.project_david_client.actions.create_action,
                tool_name=tool_name,
                run_id=run_id,
                tool_call_id=tool_call_id,
                function_args=arguments_dict,
                decision=decision,
            )
        except Exception as e:
            LOG.error(f"{tool_name} ▸ Action creation failed: {e}")
            yield _status(run_id, tool_name, "Internal system error.", status="error")
            return

        # --- [3] EXECUTION ---
        try:
            final_content = ""

            if tool_name == "search_inventory_by_group":
                group = arguments_dict["group"]
                yield _status(
                    run_id,
                    tool_name,
                    f"Searching inventory map for group: '{group}'...",
                )

                # Assume the orchestrator's client has been updated to query the engineer API
                devices = await asyncio.to_thread(
                    self.project_david_client.engineer.search_inventory_by_group,
                    assistant_id=assistant_id,
                    group=group,
                )

                if devices:
                    final_content = json.dumps(devices, indent=2)
                else:
                    final_content = "⚠️ Error: No devices found in that group. They may not be ingested yet."

            elif tool_name == "get_device_info":
                hostname = arguments_dict["hostname"]
                yield _status(
                    run_id, tool_name, f"Looking up device details for: '{hostname}'..."
                )

                device = await asyncio.to_thread(
                    self.project_david_client.engineer.get_device_info,
                    assistant_id=assistant_id,
                    hostname=hostname,
                )

                if device:
                    final_content = json.dumps(device, indent=2)
                else:
                    final_content = (
                        f"⚠️ Error: Device '{hostname}' not found in the mental map."
                    )

            else:
                raise ValueError(f"Unknown engineer tool: {tool_name}")

            # --- [4] RESULT ANALYSIS ---
            if final_content is None:
                final_content = "❌ Error: Tool execution returned no data."

            is_soft_failure = (
                "❌ Error" in final_content or "⚠️ Error" in str(final_content)[0:50]
            )

            if is_soft_failure:
                yield _status(
                    run_id,
                    tool_name,
                    "Query yielded no results or encountered an issue.",
                    status="warning",
                )
                final_content = self._format_engineer_tool_error(
                    tool_name, final_content, arguments_dict
                )
            else:
                yield _status(
                    run_id,
                    tool_name,
                    "Data retrieved successfully.",
                    status="success",
                )

            # --- [5] SUBMIT OUTPUT ---
            await self.submit_tool_output(
                thread_id=thread_id,
                assistant_id=assistant_id,
                tool_call_id=tool_call_id,
                content=final_content,
                action=action,
                is_error=is_soft_failure,
            )

            # --- [6] UPDATE ACTION ---
            await asyncio.to_thread(
                self.project_david_client.actions.update_action,
                action_id=action.id,
                status=(
                    StatusEnum.completed.value
                    if not is_soft_failure
                    else StatusEnum.failed.value
                ),
            )

            LOG.info(
                "[%s] %s completed in %.2fs",
                run_id,
                tool_name,
                asyncio.get_event_loop().time() - ts_start,
            )

        except Exception as exc:
            # --- [7] HARD FAILURE ---
            LOG.error(f"[{run_id}] {tool_name} HARD FAILURE: {exc}")
            yield _status(
                run_id, tool_name, f"Critical failure: {str(exc)}", status="error"
            )

            error_hint = self._format_engineer_tool_error(
                tool_name, str(exc), arguments_dict
            )

            try:
                await asyncio.to_thread(
                    self.project_david_client.actions.update_action,
                    action_id=action.id,
                    status=StatusEnum.failed.value,
                )
                await self.submit_tool_output(
                    thread_id=thread_id,
                    assistant_id=assistant_id,
                    tool_call_id=tool_call_id,
                    content=error_hint,
                    action=action,
                    is_error=True,
                )
            except Exception:
                pass

    # ------------------------------------------------------------------
    # 3. PUBLIC HANDLERS
    # ------------------------------------------------------------------

    async def handle_search_inventory_by_group(
        self,
        thread_id: str,
        run_id: str,
        assistant_id: str,
        arguments_dict: Dict[str, Any],
        tool_call_id: Optional[str] = None,
        decision: Optional[Dict] = None,
    ) -> AsyncGenerator[str, None]:
        """Handler for 'search_inventory_by_group'."""
        async for event in self._execute_engineer_tool_logic(
            tool_name="search_inventory_by_group",
            required_keys=["group"],
            thread_id=thread_id,
            run_id=run_id,
            assistant_id=assistant_id,
            arguments_dict=arguments_dict,
            tool_call_id=tool_call_id,
            decision=decision,
        ):
            yield event

    async def handle_get_device_info(
        self,
        thread_id: str,
        run_id: str,
        assistant_id: str,
        arguments_dict: Dict[str, Any],
        tool_call_id: Optional[str] = None,
        decision: Optional[Dict] = None,
    ) -> AsyncGenerator[str, None]:
        """Handler for 'get_device_info'."""
        async for event in self._execute_engineer_tool_logic(
            tool_name="get_device_info",
            required_keys=["hostname"],
            thread_id=thread_id,
            run_id=run_id,
            assistant_id=assistant_id,
            arguments_dict=arguments_dict,
            tool_call_id=tool_call_id,
            decision=decision,
        ):
            yield event
