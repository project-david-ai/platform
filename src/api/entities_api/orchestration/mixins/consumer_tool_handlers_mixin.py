# src/api/entities_api/orchestration/mixins/consumer_tool_handlers_mixin.py
from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any, AsyncGenerator, Dict, Optional

from dotenv import load_dotenv
from projectdavid_common.validation import StatusEnum

from src.api.entities_api.constants.platform import ERROR_NO_CONTENT
from src.api.entities_api.services.logging_service import LoggingUtility

load_dotenv()
LOG = LoggingUtility()
logger = logging.getLogger(__name__)


class ConsumerToolHandlersMixin:
    """
    Level 2 Refactored Mixin: Server-side Tool Logic.
    Shifted error orchestration logic to the SDK to support agentic self-correction.
    """

    async def submit_tool_output(
        self,
        *,
        thread_id: str,
        assistant_id: str,
        tool_call_id: Optional[str] = None,
        content: str,
        action: Any,
        is_error: bool = False,
    ) -> None:
        """
        Push tool output to thread and update Action status (Async).
        Level 2: Agnostic to content; assumes the SDK provides either success JSON
        or a formatted 'Level 2' error instruction for the LLM.
        """
        if not content:
            content = ERROR_NO_CONTENT

        # Action status reflects this specific turn, not necessarily the whole Run.
        final_status = StatusEnum.failed if is_error else StatusEnum.completed

        try:
            # 1. Save the tool result to the message thread (role='tool')
            await asyncio.to_thread(
                self.project_david_client.messages.submit_tool_output,
                thread_id=thread_id,
                content=content,
                role="tool",
                assistant_id=assistant_id,
                tool_call_id=tool_call_id,
                tool_id=getattr(action, "id", "dummy"),
            )

            # 2. Mark the specific Action as finished
            await asyncio.to_thread(
                self.project_david_client.actions.update_action,
                action_id=action.id,
                status=final_status.value,
            )
        except Exception as exc:
            LOG.error("submit_tool_output failed: %s", exc, exc_info=True)
            await self._submit_fallback_error(
                thread_id,
                assistant_id,
                error_msg=f"CRITICAL_SYSTEM_ERROR: {exc}",
                action=action,
            )

    async def _submit_fallback_error(
        self,
        thread_id: str,
        assistant_id: str,
        *,
        tool_call_id: Optional[str] = None,
        error_msg: str,
        action: Any,
    ) -> None:
        """Fallback for critical database or infrastructure failures."""
        try:
            await asyncio.to_thread(
                self.project_david_client.messages.submit_tool_output,
                thread_id=thread_id,
                content=error_msg,
                role="tool",
                assistant_id=assistant_id,
                tool_call_id=tool_call_id,
                tool_id=getattr(action, "id", "dummy"),
            )
        finally:
            await asyncio.to_thread(
                self.project_david_client.actions.update_action,
                action_id=action.id,
                status=StatusEnum.failed.value,
            )

    # ------------------------------------------------------------------
    # ASYNC TOOL CALL PROCESSOR (Reactive Mode)
    # ------------------------------------------------------------------
    async def _handover_tool_handling(
        self,
        thread_id: str,
        assistant_id: str,
        content: Dict[str, Any],
        run_id: str,
        *,
        tool_call_id: Optional[str] = None,
        decision: Optional[Dict] = None,
        **kwargs,
    ) -> AsyncGenerator[str, None]:
        """
        Reactive Mode: Records the intent as an Action and yields a manifest.
        The SDK catches this manifest and manages the Turn 1 -> Turn 2 recursion.
        """
        tool_name = content.get("name") or content.get("tool_name")
        tool_args = content.get("arguments") or content.get("args") or {}

        # 1. Record the intent to call a tool in the DB
        action = await asyncio.to_thread(
            self.project_david_client.actions.create_action,
            tool_name=tool_name,
            run_id=run_id,
            tool_call_id=tool_call_id,
            function_args=tool_args,
            decision=decision,
        )

        # 2. Yield the tool_call_manifest to the SDK
        if action and action.id:
            yield json.dumps(
                {
                    "type": "tool_call_manifest",
                    "run_id": run_id,
                    "action_id": action.id,
                    "tool_call_id": tool_call_id,  # [L3] ENSURE THIS IS SENT
                    "tool": tool_name,
                    "args": tool_args,
                }
            )

        # 3. Pause the run state. SDK loop will resume by initiating a new turn.
        await asyncio.to_thread(
            self.project_david_client.runs.update_run_status,
            run_id,
            StatusEnum.pending_action.value,
        )
        return

    async def finalize_conversation(
        self,
        assistant_reply: str,
        thread_id: str,
        assistant_id: str,
        run_id: str,
        final_status: Optional[str] = None,
    ) -> None:
        """Saves final output and marks run completed (Async)."""
        if not assistant_reply:
            return

        LOG.info(f"TOOL-ROUTER ▸ Finalizing run {run_id}")
        await asyncio.to_thread(
            self._save_assistant_message,
            thread_id,
            assistant_id,
            run_id,
            assistant_reply,
            is_error=False,
            forced_status=final_status,
        )

    async def handle_error(
        self, assistant_reply: str, thread_id: str, assistant_id: str, run_id: str
    ) -> None:
        """Saves terminal error output and marks run failed (Async)."""
        await asyncio.to_thread(
            self._save_assistant_message,
            thread_id,
            assistant_id,
            run_id,
            assistant_reply or "An unexpected terminal error occurred.",
            is_error=True,
        )

    def _save_assistant_message(
        self,
        thread_id: str,
        assistant_id: str,
        run_id: str,
        content: str = "",
        *,
        is_error: bool,
        forced_status: Optional[str] = None,
    ) -> None:
        """Internal helper to persist assistant text and update run status."""
        self.project_david_client.messages.save_assistant_message_chunk(
            thread_id=thread_id,
            content=content,
            role="assistant",
            assistant_id=assistant_id,
            sender_id=assistant_id,
            is_last_chunk=True,
        )

        # Level 2: Only update run to terminal status if not mid-correction-loop
        status = forced_status or (
            StatusEnum.failed.value if is_error else StatusEnum.completed.value
        )
        self.project_david_client.runs.update_run_status(
            run_id=run_id, new_status=status
        )
