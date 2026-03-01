# src/api/entities_api/orchestration/mixins/delegation_mixin.py
from __future__ import annotations

import asyncio
import json
import os
import threading
from contextlib import asynccontextmanager
from typing import Any, AsyncGenerator, Callable, Dict, Optional

from projectdavid.events import ScratchpadEvent
from projectdavid_common.utilities.logging_service import LoggingUtility
from projectdavid_common.validation import StatusEnum

from src.api.entities_api.utils.assistant_manager import AssistantManager

LOG = LoggingUtility()

_TERMINAL_RUN_STATES = {"completed", "failed", "cancelled", "expired"}
_WORKER_RUN_TIMEOUT = 1200
_WORKER_POLL_INTERVAL = 2.0


class DelegationMixin:

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._delegation_api_key = None
        self._delete_ephemeral_thread = False
        self._delegation_model = None
        self._research_worker_thread = None
        self._scratch_pad_thread = None
        self._run_user_id = None
        self._batfish_owner_user_id = None

    # ------------------------------------------------------------------
    # EMISSION HELPER
    # ------------------------------------------------------------------

    def _research_status(self, activity: str, state: str, run_id: str) -> str:
        return json.dumps(
            {
                "type": "research_status",
                "activity": activity,
                "state": state,
                "tool": "delegate_research_task",
                "run_id": run_id,
            }
        )

    # ------------------------------------------------------------------
    # HELPER: Bridges blocking generators to async loop
    # ------------------------------------------------------------------

    async def _stream_sync_generator(
        self, generator_func: Callable, *args, **kwargs
    ) -> AsyncGenerator[Any, None]:
        queue = asyncio.Queue()
        loop = asyncio.get_running_loop()

        def producer():
            try:
                for item in generator_func(*args, **kwargs):
                    loop.call_soon_threadsafe(queue.put_nowait, item)
                loop.call_soon_threadsafe(queue.put_nowait, None)
            except Exception as e:
                LOG.error(f"🧵 [THREAD-ERR] {e}")
                loop.call_soon_threadsafe(queue.put_nowait, e)

        threading.Thread(target=producer, daemon=True).start()

        while True:
            item = await queue.get()
            if item is None:
                break
            if isinstance(item, Exception):
                raise item
            yield item

    # ------------------------------------------------------------------
    # HELPER: Poll run status until terminal
    # ------------------------------------------------------------------

    async def _wait_for_run_completion(
        self,
        run_id: str,
        thread_id: str,
        timeout: float = _WORKER_RUN_TIMEOUT,
        poll_interval: float = _WORKER_POLL_INTERVAL,
    ) -> str:
        LOG.info(
            "⏳ [DELEGATE] Waiting for worker run %s to complete (timeout=%ss)...",
            run_id,
            timeout,
        )
        elapsed = 0.0
        while elapsed < timeout:
            try:
                run = await asyncio.to_thread(
                    self.project_david_client.runs.retrieve_run, run_id=run_id
                )
                status_value = (
                    run.status.value
                    if hasattr(run.status, "value")
                    else str(run.status)
                )
                LOG.critical(
                    "██████ [DELEGATE_POLL] run_id=%s status=%s elapsed=%.1fs ██████",
                    run_id,
                    status_value,
                    elapsed,
                )
                if status_value in _TERMINAL_RUN_STATES:
                    LOG.critical(
                        "██████ [DELEGATE_POLL] run_id=%s reached terminal state=%s ██████",
                        run_id,
                        status_value,
                    )
                    return status_value
            except Exception as e:
                LOG.warning("⚠️ [DELEGATE_POLL] Error polling run %s: %s", run_id, e)
            await asyncio.sleep(poll_interval)
            elapsed += poll_interval
        LOG.error("❌ [DELEGATE_POLL] run_id=%s timed out after %ss.", run_id, timeout)
        raise asyncio.TimeoutError(
            f"Worker run {run_id} did not complete within {timeout}s"
        )

    # ------------------------------------------------------------------
    # HELPER: Lifecycle cleanup
    # ------------------------------------------------------------------
    async def _ephemeral_clean_up(
        self, assistant_id: str, thread_id: Optional[str], delete_thread: bool = False
    ):
        LOG.info(f"🧹 [CLEANUP] Assistant: {assistant_id} | Thread: {thread_id}")
        if delete_thread and thread_id:
            try:
                await asyncio.to_thread(
                    self.project_david_client.threads.delete_thread,
                    thread_id=thread_id,
                )
            except Exception as e:
                LOG.warning(f"⚠️ [CLEANUP] Thread delete failed: {e}")
        try:
            manager = AssistantManager()
            pass
        except Exception as e:
            LOG.error(f"❌ [CLEANUP] Assistant delete failed: {e}")

    @asynccontextmanager
    async def _capture_tool_outputs(self, capture_dict: Dict[str, str]):
        original = self.submit_tool_output

        async def intercept(
            thread_id,
            assistant_id,
            tool_call_id,
            content,
            action=None,
            is_error=False,
            **kwargs,
        ):
            capture_dict[tool_call_id] = str(content)
            await original(
                thread_id,
                assistant_id,
                tool_call_id,
                content,
                action,
                is_error,
                **kwargs,
            )

        self.submit_tool_output = intercept
        try:
            yield
        finally:
            self.submit_tool_output = original

    # ------------------------------------------------------------------
    # EPHEMERAL FACTORIES
    # ------------------------------------------------------------------
    async def create_ephemeral_worker_assistant(self):
        manager = AssistantManager()
        return await manager.create_ephemeral_worker_assistant()

    async def create_ephemeral_junior_engineer(self):
        manager = AssistantManager()
        return await manager.create_ephemeral_junior_engineer()

    async def create_ephemeral_thread(self):
        return await asyncio.to_thread(self.project_david_client.threads.create_thread)

    async def create_ephemeral_message(self, thread_id, content, assistant_id):
        return await asyncio.to_thread(
            self.project_david_client.messages.create_message,
            thread_id=thread_id,
            assistant_id=assistant_id,
            content=content,
        )

    async def create_ephemeral_run(
        self, assistant_id, thread_id, meta_data: Dict | None = None
    ):
        return await asyncio.to_thread(
            self.project_david_client.runs.create_run,
            assistant_id=assistant_id,
            thread_id=thread_id,
            **({"meta_data": meta_data} if meta_data else {}),
        )

    async def _fetch_worker_final_report(self, thread_id: str) -> str | None:
        try:
            messages = await asyncio.to_thread(
                self.project_david_client.messages.get_formatted_messages,
                thread_id=thread_id,
            )
            if not messages:
                LOG.warning(f"[{thread_id}] No messages found in thread.")
                return None
            for msg in reversed(messages):
                role = msg.get("role")
                content = msg.get("content")
                tool_calls = msg.get("tool_calls")
                if role != "assistant":
                    continue
                if tool_calls:
                    continue
                if not isinstance(content, str) or not content.strip():
                    continue
                final_text = content.strip()
                LOG.info(
                    "✅ [WORKER_FINAL_REPORT] Found report (length=%d): %s...",
                    len(final_text),
                    final_text[:100],
                )
                return final_text
            LOG.info("ℹ️ [WORKER_FINAL_REPORT] No final text report found yet.")
            return None
        except Exception as e:
            LOG.exception("❌[WORKER_FINAL_REPORT_ERROR] Failed to fetch report: %s", e)
            return None

    # ------------------------------------------------------------------
    # HANDLER 1: Research Delegation — RESTORED working structure
    # Additions: origin_user_id resolution + metadata stamp on ephemeral run
    # ------------------------------------------------------------------
    async def handle_delegate_research_task(
        self, thread_id, run_id, assistant_id, arguments_dict, tool_call_id, decision
    ) -> AsyncGenerator[str, None]:
        """
        Supervisor → Worker research delegation.

        Flow:
          - Spawns an ephemeral worker assistant on its own thread.
          - Streams worker content, reasoning, and scratchpad events back
            through the senior's stream so the backend consumer sees everything
            in a single unified pipe.
          - Scratchpad events are intercepted BEFORE the broad attribute guards
            fire — this is the critical ordering that keeps them from being
            swallowed silently.
          - Submits the worker's final report back to the supervisor as a
            tool output, completing the delegation loop.
        """

        # Capture Senior's thread_id before anything mutates state
        self._scratch_pad_thread = thread_id
        LOG.info(f"🔄 [DELEGATE] STARTING. Run: {run_id}")

        if isinstance(arguments_dict, str):
            try:
                args = json.loads(arguments_dict)
            except Exception:
                args = {"task": arguments_dict}
        else:
            args = arguments_dict

        yield self._research_status(
            "Initializing delegation worker...", "in_progress", run_id
        )

        action = None
        try:
            action = await asyncio.to_thread(
                self.project_david_client.actions.create_action,
                tool_name="delegate_research_task",
                run_id=run_id,
                tool_call_id=tool_call_id,
                function_args=arguments_dict,
                decision=decision,
            )
        except Exception as e:
            LOG.error(f"❌[DELEGATE] Action creation failed: {e}")

        ephemeral_worker = None
        ephemeral_thread = None
        execution_had_error = False
        ephemeral_run = None

        try:
            # ------------------------------------------------------------------
            # RESOLVE SENIOR's user_id — run_id here is the SENIOR's run.
            # Cached on self so subsequent tool calls skip the DB hit.
            # ------------------------------------------------------------------
            origin_user_id = getattr(self, "_batfish_owner_user_id", None)

            if not origin_user_id:
                run_obj = await asyncio.to_thread(
                    self.project_david_client.runs.retrieve_run, run_id=run_id
                )
                origin_user_id = run_obj.user_id
                self._batfish_owner_user_id = origin_user_id

            LOG.info(
                "RESEARCH_DELEGATE ▸ origin_user_id=%s | scratch_pad_thread=%s",
                origin_user_id,
                self._scratch_pad_thread,
            )

            ephemeral_worker = await self.create_ephemeral_worker_assistant()

            # Create a localized ephemeral thread before pushing the message
            ephemeral_thread = await self.create_ephemeral_thread()
            self._research_worker_thread = ephemeral_thread

            prompt = f"TASK: {args.get('task')}\nREQ: {args.get('requirements')}"

            msg = await self.create_ephemeral_message(
                ephemeral_thread.id, prompt, ephemeral_worker.id
            )

            # ------------------------------------------------------------------
            # STAMP both the Senior's user_id AND thread_id into the ephemeral
            # run's meta_data. The fresh server-side worker reads both back on
            # stream boot — the DB record is the only shared state between them.
            # ------------------------------------------------------------------
            ephemeral_run = await self.create_ephemeral_run(
                ephemeral_worker.id,
                ephemeral_thread.id,
                meta_data={
                    "batfish_owner_user_id": origin_user_id,
                    "scratch_pad_thread": self._scratch_pad_thread,
                },
            )

            LOG.info(
                "RESEARCH_DELEGATE ▸ Stamped meta_data: batfish_owner_user_id=%s | scratch_pad_thread=%s",
                origin_user_id,
                self._scratch_pad_thread,
            )

            yield self._research_status(
                "Worker active. Streaming...", "in_progress", run_id
            )

            LOG.info(f"🔄 [SUPERVISORS_THREAD_ID]: {thread_id}")
            LOG.info(f"🔄 [WORKERS_THREAD_ID]: {ephemeral_thread.id}")

            # Configure stream — original working pattern, untouched
            sync_stream = self.project_david_client.synchronous_inference_stream
            sync_stream.setup(
                thread_id=ephemeral_thread.id,
                assistant_id=ephemeral_worker.id,
                message_id=msg.id,
                run_id=ephemeral_run.id,
                api_key=self._delegation_api_key,
            )

            LOG.critical(
                "🎬 WORKER STREAM STARTING - If you see this but no content chunks, "
                "check process_tool_calls wiring"
            )

            captured_stream_content = ""

            async for event in self._stream_sync_generator(
                sync_stream.stream_events,
                model=self._delegation_model,
            ):
                # ----------------------------------------------------------------
                # ✅ INTERCEPT: ScratchpadEvent — MUST come before the broad guards.
                #
                # ScratchpadEvent carries a 'tool' attribute which causes GUARD 1
                # below to swallow it silently if we don't intercept first.
                # We re-emit it onto the senior's stream using the senior's run_id
                # so the backend consumer routes it correctly through section G2.
                # The 'origin' field lets the frontend distinguish worker-sourced
                # scratchpad events from senior-sourced ones if needed.
                # ----------------------------------------------------------------
                if isinstance(event, ScratchpadEvent):
                    LOG.info(
                        "📋 [DELEGATE] Worker ScratchpadEvent intercepted: "
                        "op=%s state=%s activity=%s",
                        event.operation,
                        event.state,
                        event.activity,
                    )
                    yield json.dumps(
                        {
                            "type": "scratchpad_status",
                            "state": event.state,
                            "operation": event.operation,
                            "activity": event.activity,
                            "tool": event.tool,
                            "entry": event.entry or event.content or "",
                            "run_id": run_id,  # ← senior's run_id
                            "assistant_id": event.assistant_id,
                            "origin": "research_worker",  # ← source tag for frontend
                        }
                    )
                    continue

                # ----------------------------------------------------------------
                # 🛑 GUARD 1: Exclude Status / System Events
                # Comes AFTER ScratchpadEvent check — order is critical.
                # ----------------------------------------------------------------
                if (
                    hasattr(event, "tool")
                    or hasattr(event, "status")
                    or getattr(event, "type", "") == "status"
                ):
                    continue

                # ----------------------------------------------------------------
                # 🛑 GUARD 2: Exclude Tool Call Argument Frames
                # ----------------------------------------------------------------
                if getattr(event, "tool_calls", None) or getattr(
                    event, "function_call", None
                ):
                    continue

                chunk_content = getattr(event, "content", None) or getattr(
                    event, "text", None
                )
                chunk_reasoning = getattr(event, "reasoning", None)

                if chunk_reasoning:
                    yield json.dumps(
                        {
                            "stream_type": "delegation",
                            "chunk": {
                                "type": "reasoning",
                                "content": chunk_reasoning,
                                "run_id": run_id,
                            },
                        }
                    )

                if chunk_content and isinstance(chunk_content, str):
                    captured_stream_content += chunk_content
                    yield json.dumps(
                        {
                            "stream_type": "delegation",
                            "chunk": {
                                "type": "content",
                                "content": chunk_content,
                                "run_id": run_id,
                            },
                        }
                    )

            yield self._research_status(
                "Worker processing. Waiting for completion...", "in_progress", run_id
            )

            try:
                final_run_status = await self._wait_for_run_completion(
                    run_id=ephemeral_run.id,
                    thread_id=ephemeral_thread.id,
                )
                LOG.info(
                    "✅ [DELEGATE] Worker run completed. Status=%s", final_run_status
                )
            except asyncio.TimeoutError:
                LOG.error("⏳[DELEGATE] Worker run timed out. Attempting fetch anyway.")
                execution_had_error = True
                final_run_status = "timed_out"

            final_content = await self._fetch_worker_final_report(
                thread_id=ephemeral_thread.id
            )

            LOG.critical(
                "██████ [FINAL_THREAD_CONTENT_SUBMITTED_BY_RESEARCH_WORKER]=%s ██████",
                final_content,
            )

            if not final_content:
                LOG.critical(
                    "██████ [DELEGATE_TOTAL_FAILURE] No content generated by the worker ██████"
                )
                final_content = "No report generated by worker."
                execution_had_error = True

            await self.submit_tool_output(
                thread_id=thread_id,
                assistant_id=assistant_id,
                tool_call_id=tool_call_id,
                content=final_content,
                action=action,
                is_error=execution_had_error,
            )

            if action:
                await asyncio.to_thread(
                    self.project_david_client.actions.update_action,
                    action_id=action.id,
                    status=(
                        StatusEnum.completed.value
                        if not execution_had_error
                        else StatusEnum.failed.value
                    ),
                )

        except Exception as e:
            execution_had_error = True
            LOG.error(f"❌ [DELEGATE] Error: {e}", exc_info=True)
            yield self._research_status(f"Error: {str(e)}", "error", run_id)

        finally:
            if ephemeral_worker:
                await self._ephemeral_clean_up(
                    ephemeral_worker.id,
                    ephemeral_thread.id if ephemeral_thread else None,
                    self._delete_ephemeral_thread,
                )

            yield self._research_status(
                "Delegation complete.",
                "completed" if not execution_had_error else "error",
                run_id,
            )
