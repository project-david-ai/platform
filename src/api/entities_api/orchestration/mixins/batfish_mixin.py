from __future__ import annotations

import asyncio
import json
import os
from typing import Any, AsyncGenerator, Dict, Optional

from projectdavid_common import ToolValidator
from projectdavid_common.utilities.logging_service import LoggingUtility
from projectdavid_common.validation import StatusEnum

from src.api.entities_api.db.database import SessionLocal
from src.api.entities_api.services.batfish_service import (BatfishService,
                                                           BatfishServiceError)

LOG = LoggingUtility()

_DEFAULT_SNAPSHOT_ID = "snap_774bc864d2ef4e71abb6"


def _batfish_status(
    run_id: str, tool: str, message: str, status: str = "running"
) -> str:
    """
    Emit a status event as raw JSON conforming to the stream EVENT_CONTRACT.
    """
    return json.dumps(
        {
            "type": "batfish_status",
            "run_id": run_id,
            "tool": tool,
            "status": status,
            "message": message,
        }
    )


class BatfishMixin:
    """
    Drive the **Batfish Root Cause Analysis Tools**.

    Features:
    - Bulletproof action tracking and output submission.
    - Captures run_user_id mapping explicitly (so Junior Engineer queries Batfish using Senior's scope).
    - Emits real-time stream state tracking.
    - Queries BatfishService directly — no SDK indirection.
    """

    def _get_batfish_service(self) -> BatfishService:
        """
        Lazily instantiate a BatfishService backed by its own SessionLocal session.
        The session is reused for the lifetime of this mixin instance.
        """
        if not hasattr(self, "_batfish_service") or self._batfish_service is None:
            self._batfish_service = BatfishService(SessionLocal())
        return self._batfish_service

    @staticmethod
    def _format_batfish_tool_error(
        tool_name: str, error_content: str, inputs: Dict
    ) -> str:
        """Translates Batfish failures into actionable instructions for the LLM."""
        if not error_content:
            error_content = "Unknown Error (Empty Response)"

        error_lower = error_content.lower()

        if "validation error" in error_lower or "missing arguments" in error_lower:
            return (
                f"❌ SCHEMA ERROR: Invalid arguments for '{tool_name}'.\n"
                f"ERROR DETAILS: {error_content}\n"
                "SYSTEM INSTRUCTION: Check the tool definition and retry with valid arguments."
            )

        if "404" in error_content or "not found" in error_lower:
            return (
                f"❌ Batfish Tool '{tool_name}' Failed: Snapshot or Data Not Found.\n"
                f"ERROR DETAILS: {error_content}\n"
                "SYSTEM INSTRUCTION: Verify the snapshot_id or target elements."
            )

        return (
            f"❌ Batfish Tool '{tool_name}' Error: {error_content}\n"
            "SYSTEM INSTRUCTION: The simulation engine encountered an error. Document this failure in your scratchpad."
        )

    async def _execute_batfish_tool_logic(
        self,
        tool_name: str,
        required_keys: list[str],
        thread_id: str,
        run_id: str,
        assistant_id: str,
        arguments_dict: Dict[str, Any],
        tool_call_id: Optional[str],
        decision: Optional[Dict],
        actual_batfish_tool: Optional[str] = None,
        user_id: Optional[str] = None,
    ) -> AsyncGenerator[str, None]:
        """
        Shared core logic for Batfish tools.
        If `actual_batfish_tool` is provided, we pass that to BatfishService (useful for wrapper tools).
        Otherwise, we use `tool_name`.
        """

        ts_start = asyncio.get_event_loop().time()
        target_tool = actual_batfish_tool or tool_name

        # --- [1] STATUS: VALIDATING ---
        yield _batfish_status(
            run_id, tool_name, f"Validating parameters for {target_tool}..."
        )

        # --- VALIDATION ---
        validator = ToolValidator()
        validator.schema_registry = {tool_name: required_keys}
        validation_error = validator.validate_args(tool_name, arguments_dict)

        if validation_error:
            LOG.warning(f"{tool_name} ▸ Validation Failed: {validation_error}")
            yield _batfish_status(
                run_id, tool_name, "Validation failed.", status="error"
            )

            error_feedback = self._format_batfish_tool_error(
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
        yield _batfish_status(
            run_id, tool_name, f"Initializing Batfish simulation action..."
        )

        action = None
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
            yield _batfish_status(
                run_id, tool_name, "Internal system error.", status="error"
            )
            return

        # --- [3] EXECUTION ---
        try:
            # Resolve user_id: explicit arg -> delegate propagation -> run DB -> env fallback
            user_id = user_id or getattr(self, "_batfish_owner_user_id", None)
            if not user_id:
                try:
                    run_obj = await asyncio.to_thread(
                        self.project_david_client.runs.retrieve_run, run_id=run_id
                    )
                    user_id = run_obj.user_id
                except Exception:
                    pass
            if not user_id:
                user_id = os.getenv("ENTITIES_USER_ID")

            snapshot_id = arguments_dict.get("snapshot_id", _DEFAULT_SNAPSHOT_ID)

            yield _batfish_status(
                run_id, tool_name, f"Executing '{target_tool}' on dataplane snapshot..."
            )
            LOG.info(
                f"[{run_id}] Executing batfish tool '{target_tool}' for snapshot '{snapshot_id}' (user_id={user_id})"
            )

            LOG.critical(
                "[BATFISH DEBUG] user_id=%s | snapshot_id=%s | target_tool=%s",
                user_id,
                snapshot_id,
                target_tool,
            )

            # --- CALL BATFISH SERVICE DIRECTLY ---
            try:
                result = await self._get_batfish_service().run_tool(
                    user_id=user_id,
                    snapshot_id=snapshot_id,
                    tool_name=target_tool,
                )
            except BatfishServiceError as svc_err:
                raise RuntimeError(str(svc_err)) from svc_err

            # Normalise result to string
            if isinstance(result, dict) and "result" in result:
                final_content = str(result["result"])
            elif isinstance(result, str):
                final_content = result
            else:
                final_content = json.dumps(result, indent=2)

            is_soft_failure = False

            if not final_content or "error" in str(final_content).lower()[0:50]:
                is_soft_failure = True
                yield _batfish_status(
                    run_id,
                    tool_name,
                    "Tool execution returned an error.",
                    status="warning",
                )
                final_content = self._format_batfish_tool_error(
                    tool_name, final_content or "No Data Returned", arguments_dict
                )
            else:
                yield _batfish_status(
                    run_id,
                    tool_name,
                    "Simulation data retrieved successfully.",
                    status="success",
                )

            # --- [4] SUBMIT OUTPUT ---
            await self.submit_tool_output(
                thread_id=thread_id,
                assistant_id=assistant_id,
                tool_call_id=tool_call_id,
                content=final_content,
                action=action,
                is_error=is_soft_failure,
            )

            # --- [5] UPDATE ACTION ---
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
            # --- [6] HARD FAILURE ---
            LOG.error(f"[{run_id}] {tool_name} HARD FAILURE: {exc}", exc_info=True)
            yield _batfish_status(
                run_id, tool_name, f"Critical failure: {str(exc)}", status="error"
            )

            error_hint = self._format_batfish_tool_error(
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
    # PUBLIC HANDLERS
    # ------------------------------------------------------------------

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
        actual_tool = arguments_dict.get("tool_name")
        async for event in self._execute_batfish_tool_logic(
            tool_name="run_batfish_tool",
            required_keys=["tool_name"],
            thread_id=thread_id,
            run_id=run_id,
            assistant_id=assistant_id,
            arguments_dict=arguments_dict,
            tool_call_id=tool_call_id,
            decision=decision,
            actual_batfish_tool=actual_tool,
            user_id=user_id,
        ):
            yield event

    async def handle_get_ospf_failures(
        self,
        thread_id: str,
        run_id: str,
        assistant_id: str,
        arguments_dict: Dict[str, Any],
        tool_call_id: str,
        decision: Dict,
        user_id: Optional[str] = None,
    ) -> AsyncGenerator[str, None]:
        LOG.info(
            "[BATFISH] handle_get_ospf_failures called (user_id=%s, run_id=%s)",
            user_id,
            run_id,
        )
        async for event in self._execute_batfish_tool_logic(
            "get_ospf_failures",
            [],
            thread_id,
            run_id,
            assistant_id,
            arguments_dict,
            tool_call_id,
            decision,
            user_id=user_id,
        ):
            yield event

    async def handle_get_bgp_failures(
        self,
        thread_id: str,
        run_id: str,
        assistant_id: str,
        arguments_dict: Dict[str, Any],
        tool_call_id: str,
        decision: Dict,
        user_id: Optional[str] = None,
    ) -> AsyncGenerator[str, None]:
        LOG.info(
            "[BATFISH] handle_get_bgp_failures called (user_id=%s, run_id=%s)",
            user_id,
            run_id,
        )
        async for event in self._execute_batfish_tool_logic(
            "get_bgp_failures",
            [],
            thread_id,
            run_id,
            assistant_id,
            arguments_dict,
            tool_call_id,
            decision,
            user_id=user_id,
        ):
            yield event

    async def handle_get_routing_loop_detection(
        self,
        thread_id: str,
        run_id: str,
        assistant_id: str,
        arguments_dict: Dict[str, Any],
        tool_call_id: str,
        decision: Dict,
        user_id: Optional[str] = None,
    ) -> AsyncGenerator[str, None]:
        LOG.info(
            "[BATFISH] handle_get_routing_loop_detection called (user_id=%s, run_id=%s)",
            user_id,
            run_id,
        )
        async for event in self._execute_batfish_tool_logic(
            "get_routing_loop_detection",
            [],
            thread_id,
            run_id,
            assistant_id,
            arguments_dict,
            tool_call_id,
            decision,
            user_id=user_id,
        ):
            yield event

    async def handle_get_acl_shadowing(
        self,
        thread_id: str,
        run_id: str,
        assistant_id: str,
        arguments_dict: Dict[str, Any],
        tool_call_id: str,
        decision: Dict,
        user_id: Optional[str] = None,
    ) -> AsyncGenerator[str, None]:
        LOG.info(
            "[BATFISH] handle_get_acl_shadowing called (user_id=%s, run_id=%s)",
            user_id,
            run_id,
        )
        async for event in self._execute_batfish_tool_logic(
            "get_acl_shadowing",
            [],
            thread_id,
            run_id,
            assistant_id,
            arguments_dict,
            tool_call_id,
            decision,
            user_id=user_id,
        ):
            yield event

    async def handle_get_undefined_references(
        self,
        thread_id: str,
        run_id: str,
        assistant_id: str,
        arguments_dict: Dict[str, Any],
        tool_call_id: str,
        decision: Dict,
        user_id: Optional[str] = None,
    ) -> AsyncGenerator[str, None]:
        LOG.info(
            "[BATFISH] handle_get_undefined_references called (user_id=%s, run_id=%s)",
            user_id,
            run_id,
        )
        async for event in self._execute_batfish_tool_logic(
            "get_undefined_references",
            [],
            thread_id,
            run_id,
            assistant_id,
            arguments_dict,
            tool_call_id,
            decision,
            user_id=user_id,
        ):
            yield event

    async def handle_get_unused_structures(
        self,
        thread_id: str,
        run_id: str,
        assistant_id: str,
        arguments_dict: Dict[str, Any],
        tool_call_id: str,
        decision: Dict,
        user_id: Optional[str] = None,
    ) -> AsyncGenerator[str, None]:
        LOG.info(
            "[BATFISH] handle_get_unused_structures called (user_id=%s, run_id=%s)",
            user_id,
            run_id,
        )
        async for event in self._execute_batfish_tool_logic(
            "get_unused_structures",
            [],
            thread_id,
            run_id,
            assistant_id,
            arguments_dict,
            tool_call_id,
            decision,
            user_id=user_id,
        ):
            yield event

    async def handle_get_logical_topology_with_mtu(
        self,
        thread_id: str,
        run_id: str,
        assistant_id: str,
        arguments_dict: Dict[str, Any],
        tool_call_id: str,
        decision: Dict,
        user_id: Optional[str] = None,
    ) -> AsyncGenerator[str, None]:
        LOG.info(
            "[BATFISH] handle_get_logical_topology_with_mtu called (user_id=%s, run_id=%s)",
            user_id,
            run_id,
        )
        async for event in self._execute_batfish_tool_logic(
            "get_logical_topology_with_mtu",
            [],
            thread_id,
            run_id,
            assistant_id,
            arguments_dict,
            tool_call_id,
            decision,
            user_id=user_id,
        ):
            yield event

    async def handle_get_device_os_inventory(
        self,
        thread_id: str,
        run_id: str,
        assistant_id: str,
        arguments_dict: Dict[str, Any],
        tool_call_id: str,
        decision: Dict,
        user_id: Optional[str] = None,
    ) -> AsyncGenerator[str, None]:
        LOG.info(
            "[BATFISH] handle_get_device_os_inventory called (user_id=%s, run_id=%s)",
            user_id,
            run_id,
        )
        async for event in self._execute_batfish_tool_logic(
            "get_device_os_inventory",
            [],
            thread_id,
            run_id,
            assistant_id,
            arguments_dict,
            tool_call_id,
            decision,
            user_id=user_id,
        ):
            yield event
