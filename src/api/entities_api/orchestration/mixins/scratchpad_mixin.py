from __future__ import annotations

import asyncio
import json
from typing import Any, AsyncGenerator, Dict, Optional

from projectdavid_common import ToolValidator
from projectdavid_common.utilities.logging_service import LoggingUtility
from projectdavid_common.validation import StatusEnum

LOG = LoggingUtility()


def _scratchpad_status(
    run_id: str,
    operation: str,
    state: str,
    tool: Optional[str] = None,
    activity: Optional[str] = None,
    entry: Optional[str] = None,
    assistant_id: Optional[str] = None,
) -> str:
    """
    Emit a status event as raw JSON conforming to the stream EVENT_CONTRACT.

    Shape:
        {
            "type":         "scratchpad_status",
            "run_id":       "<uuid>",
            "operation":    "read" | "update" | "append",
            "state":        "in_progress" | "success" | "completed" | "error",
            "tool":         "<tool_name>",     (optional)
            "activity":     "<human readable>",(optional)
            "entry":        "<entry text>",    (optional)
            "assistant_id": "<asst_id>"        (optional)
        }
    """
    payload = {
        "type": "scratchpad_status",
        "run_id": run_id,
        "operation": operation,
        "state": state,
    }

    if tool is not None:
        payload["tool"] = tool
    if activity is not None:
        payload["activity"] = activity
    if entry is not None:
        payload["entry"] = entry
    if assistant_id is not None:
        payload["assistant_id"] = assistant_id

    return json.dumps(payload)


class ScratchpadMixin:

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._scratch_pad_thread: Optional[str] = None

    async def _execute_scratchpad_logic(
        self,
        tool_name: str,
        operation_type: str,
        thread_id: Optional[str],
        run_id: str,
        assistant_id: str,
        arguments_dict: Dict[str, Any],
        tool_call_id: str,
        decision: Any,
        scratch_pad_thread: Optional[str] = None,
    ) -> AsyncGenerator[str, None]:

        if scratch_pad_thread:
            thread_id = scratch_pad_thread

        LOG.info(f"SCRATCHPAD ▸ scratchpad thread id: {scratch_pad_thread}")

        # Injecting assistant_id into the human-readable logs
        # so you can easily track Worker vs Supervisor activity
        _OP_LABELS = {
            "read": (
                f"📖 Reading scratchpad ({assistant_id})...",
                f"📖 Scratchpad read by {assistant_id}.",
            ),
            "update": (
                f"✏️ Updating scratchpad ({assistant_id})...",
                f"✏️ Scratchpad updated by {assistant_id}.",
            ),
            "append": (
                f"📝 Appending to scratchpad ({assistant_id})...",
                f"📝 Scratchpad entry written by {assistant_id}.",
            ),
        }

        label_start, label_done = _OP_LABELS.get(
            operation_type,
            (
                f"Accessing memory ({assistant_id})...",
                f"Memory synchronized by {assistant_id}.",
            ),
        )

        yield _scratchpad_status(
            run_id=run_id,
            operation=operation_type,
            state="in_progress",
            tool=tool_name,
            activity=label_start,
            assistant_id=assistant_id,
        )

        if operation_type != "read":
            required = {"content": str} if operation_type == "update" else {"note": str}

            validator = ToolValidator()
            validator.schema_registry = {tool_name: required}

            if err := validator.validate_args(tool_name, arguments_dict):
                yield _scratchpad_status(
                    run_id=run_id,
                    operation=operation_type,
                    state="error",
                    tool=tool_name,
                    activity=f"Validation error: {err}",
                    assistant_id=assistant_id,
                )

                await self.submit_tool_output(
                    thread_id=thread_id,
                    assistant_id=assistant_id,
                    tool_call_id=tool_call_id,
                    content=f"Error: {err}",
                    action=None,
                    is_error=True,
                )
                return

        action = await asyncio.to_thread(
            self.project_david_client.actions.create_action,
            tool_name=tool_name,
            run_id=run_id,
            tool_call_id=tool_call_id,
            function_args=arguments_dict,
            decision=decision,
        )

        try:
            if operation_type == "read":
                res = await asyncio.to_thread(
                    self.project_david_client.tools.scratchpad_read,
                    thread_id=thread_id,
                )
            elif operation_type == "update":
                res = await asyncio.to_thread(
                    self.project_david_client.tools.scratchpad_update,
                    thread_id=thread_id,
                    content=arguments_dict.get("content"),
                )
            else:
                res = await asyncio.to_thread(
                    self.project_david_client.tools.scratchpad_append,
                    thread_id=thread_id,
                    note=arguments_dict.get("note"),
                )

            # Determine entry text for the frontend scratchpad component
            if operation_type == "append":
                entry_text = arguments_dict.get("note", "")
            elif operation_type == "update":
                entry_text = arguments_dict.get("content", "")
            elif operation_type == "read":
                entry_text = res if isinstance(res, str) else json.dumps(res)
            else:
                entry_text = ""

            # Yield raw scratchpad content event — consumed directly by routes.py
            if entry_text:
                yield _scratchpad_status(
                    run_id=run_id,
                    operation=operation_type,
                    state="success",
                    entry=entry_text,
                    assistant_id=assistant_id,
                )

            # Done-label activity event
            yield _scratchpad_status(
                run_id=run_id,
                operation=operation_type,
                state="completed",
                tool=tool_name,
                activity=label_done,
                assistant_id=assistant_id,
            )

            await asyncio.to_thread(
                self.project_david_client.actions.update_action,
                action_id=action.id,
                status=StatusEnum.completed.value,
            )

            await self.submit_tool_output(
                thread_id=thread_id,
                assistant_id=assistant_id,
                tool_call_id=tool_call_id,
                content=res,
                action=action,
            )

        except Exception as e:
            yield _scratchpad_status(
                run_id=run_id,
                operation=operation_type,
                state="error",
                tool=tool_name,
                activity=f"Scratchpad error: {str(e)}",
                assistant_id=assistant_id,
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
                content=f"Error: {e}",
                action=action,
                is_error=True,
            )

    async def handle_read_scratchpad(self, *args, **kwargs):
        async for s in self._execute_scratchpad_logic(
            "read_scratchpad", "read", *args, **kwargs
        ):
            yield s

    async def handle_update_scratchpad(self, *args, **kwargs):
        async for s in self._execute_scratchpad_logic(
            "update_scratchpad", "update", *args, **kwargs
        ):
            yield s

    async def handle_append_scratchpad(self, *args, **kwargs):
        async for s in self._execute_scratchpad_logic(
            "append_scratchpad", "append", *args, **kwargs
        ):
            yield s
