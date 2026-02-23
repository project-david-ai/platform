# src/api/entities_api/orchestration/mixins/delegation_mixin.py
from __future__ import annotations

import asyncio
import json
import threading
from contextlib import asynccontextmanager
from typing import Any, AsyncGenerator, Callable, Dict

from projectdavid_common.utilities.logging_service import LoggingUtility
from projectdavid_common.validation import StatusEnum

from src.api.entities_api.utils.assistant_manager import AssistantManager

LOG = LoggingUtility()

# Terminal run states aligned with RunStatus enum VALUES (not names).
#
# RunStatus reference (from SDK):
#   queued          → not terminal
#   in_progress     → not terminal
#   pending         → not terminal
#   processing      → not terminal
#   retrying        → not terminal
#   action_required → not terminal (worker's own tool loop handles this)
#   completed       → TERMINAL ✓
#   failed          → TERMINAL ✓
#   cancelled       → TERMINAL ✓
#   expired         → TERMINAL ✓
_TERMINAL_RUN_STATES = {"completed", "failed", "cancelled", "expired"}

# How long to wait for the worker run to complete before giving up (seconds)
_WORKER_RUN_TIMEOUT = 1200

# How often to poll the run status (seconds)
_WORKER_POLL_INTERVAL = 2.0


class DelegationMixin:
    """
    Spins up an ephemeral Worker Loop using the project_david_client strictly.
    Lifecycle matches the central Orchestrator stream logic.

    Emission style:
    - All stream events are emitted as raw JSON strings conforming to the
      EVENT_CONTRACT via the _research_status() helper.
    - type: 'research_status' is the discriminator for this mixin's events,
      distinct from 'activity' (generic), 'code_status' (sandbox), and
      'web' (web tool progress).
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._delegation_api_key = None
        self._delete_ephemeral_thread = False
        self._delegation_model = None
        self._research_worker_thread = None

        self._scratch_pad_thread = None

    # ------------------------------------------------------------------
    # EMISSION HELPER
    # ------------------------------------------------------------------
    def _research_status(self, activity: str, state: str, run_id: str) -> str:
        """
        Emits a research delegation status event conforming to the EVENT_CONTRACT.

        Shape:
            {
                "type":     "research_status",
                "activity": "<human readable>",
                "state":    "in_progress" | "completed" | "error",
                "tool":     "delegate_research_task",
                "run_id":   "<uuid>"
            }
        """
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
    # HELPER: Bridges blocking generators to async loop (Fixes uvloop error)
    # ------------------------------------------------------------------
    async def _stream_sync_generator(
        self, generator_func: Callable, *args, **kwargs
    ) -> AsyncGenerator[Any, None]:
        """
        Runs a synchronous generator in a background thread and yields items asynchronously.
        """
        queue = asyncio.Queue()
        loop = asyncio.get_running_loop()

        def producer():
            try:
                for item in generator_func(*args, **kwargs):
                    loop.call_soon_threadsafe(queue.put_nowait, item)
                loop.call_soon_threadsafe(queue.put_nowait, None)  # Sentinel
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
    # HELPER: Poll run status until terminal state or timeout
    # ------------------------------------------------------------------
    async def _wait_for_run_completion(
        self,
        run_id: str,
        thread_id: str,
        timeout: float = _WORKER_RUN_TIMEOUT,
        poll_interval: float = _WORKER_POLL_INTERVAL,
    ) -> str:
        """
        Polls the worker's run status until it reaches a terminal state.

        RunStatus enum values (from SDK):
            Active (keep polling):
                queued, in_progress, pending, processing, retrying, action_required

            Terminal (stop polling):
                completed, failed, cancelled, expired

        Returns the final status string value.
        Raises asyncio.TimeoutError if timeout is exceeded.
        """
        LOG.info(
            "⏳ [DELEGATE] Waiting for worker run %s to complete (timeout=%ss)...",
            run_id,
            timeout,
        )

        elapsed = 0.0
        while elapsed < timeout:
            try:
                run = await asyncio.to_thread(
                    self.project_david_client.runs.retrieve_run,
                    run_id=run_id,
                )

                # run.status is a RunStatus str-enum — .value gives the raw string
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

    async def _ephemeral_clean_up(
        self, assistant_id: str, thread_id: str, delete_thread: bool = False
    ):
        LOG.info(f"🧹 [CLEANUP] Assistant: {assistant_id} | Thread: {thread_id}")
        if delete_thread:
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
            # await manager.delete_assistant(assistant_id=assistant_id, permanent=True)
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

    async def create_ephemeral_worker_assistant(self):
        manager = AssistantManager()
        return await manager.create_ephemeral_worker_assistant()

    async def create_ephemeral_message(self, thread_id, content, assistant_id):
        return await asyncio.to_thread(
            self.project_david_client.messages.create_message,
            thread_id=thread_id,
            assistant_id=assistant_id,
            content=content,
        )

    async def create_ephemeral_run(self, assistant_id, thread_id):
        return await asyncio.to_thread(
            self.project_david_client.runs.create_run,
            assistant_id=assistant_id,
            thread_id=thread_id,
        )

    async def _fetch_worker_final_report(self, thread_id: str) -> str | None:
        """
        Retrieves the most recent text response from the assistant.
        Ignores tool calls, tool outputs, and empty messages.
        """
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

                # Must be from the assistant
                if role != "assistant":
                    continue

                # Must NOT be a tool call (dispatching a search)
                if tool_calls:
                    continue

                # Must contain actual text
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
            LOG.exception(
                "❌ [WORKER_FINAL_REPORT_ERROR] Failed to fetch report: %s", e
            )
            return None

    # ------------------------------------------------------------------
    # MAIN HANDLER
    # ------------------------------------------------------------------
    async def handle_delegate_research_task(
        self, thread_id, run_id, assistant_id, arguments_dict, tool_call_id, decision
    ) -> AsyncGenerator[str, None]:

        self._scratch_pad_thread = thread_id

        LOG.info(f"🔄 [DELEGATE] STARTING. Run: {run_id}")

        # 1. Parse Arguments
        if isinstance(arguments_dict, str):
            try:
                args = json.loads(arguments_dict)
            except Exception:
                args = {"task": arguments_dict}
        else:
            args = arguments_dict

        # 2. Yield Initial Status
        yield self._research_status(
            "Initializing delegation worker...", "in_progress", run_id
        )

        # 3. Create Action (DB)
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
            LOG.error(f"❌ [DELEGATE] Action creation failed: {e}")

        ephemeral_worker = None
        execution_had_error = False
        ephemeral_run = None

        try:
            # 4. Setup Ephemeral Assistant & Thread
            ephemeral_worker = await self.create_ephemeral_worker_assistant()
            ephemeral_thread = self._research_worker_thread

            prompt = f"TASK: {args.get('task')}\nREQ: {args.get('requirements')}"

            msg = await self.create_ephemeral_message(
                ephemeral_thread.id, prompt, ephemeral_worker.id
            )
            ephemeral_run = await self.create_ephemeral_run(
                ephemeral_worker.id, ephemeral_thread.id
            )

            yield self._research_status(
                "Worker active. Streaming...", "in_progress", run_id
            )

            LOG.info(f"🔄 [SUPERVISORS_THREAD_ID]: {thread_id}")
            LOG.info(f"🔄 [WORKERS_THREAD_ID]: {self._research_worker_thread}")

            # 5. Configure Stream
            sync_stream = self.project_david_client.synchronous_inference_stream
            sync_stream.setup(
                thread_id=ephemeral_thread.id,
                assistant_id=ephemeral_worker.id,
                message_id=msg.id,
                run_id=ephemeral_run.id,
                api_key=self._delegation_api_key,
            )

            # 6. Stream Execution — covers first inference pass only.
            #    Subsequent tool-call passes run inside the SDK's own run loop.
            LOG.critical(
                "🎬 WORKER STREAM STARTING - If you see this but no content chunks, "
                "check process_tool_calls wiring"
            )

            captured_stream_content = ""

            async for event in self._stream_sync_generator(
                sync_stream.stream_events,
                model=self._delegation_model,
            ):
                # 🛑 GUARD 1: Exclude Status/System Events
                if (
                    hasattr(event, "tool")
                    or hasattr(event, "status")
                    or getattr(event, "type", "") == "status"
                ):
                    continue

                # 🛑 GUARD 2: Exclude Tool Call Arguments
                if getattr(event, "tool_calls", None) or getattr(
                    event, "function_call", None
                ):
                    continue

                chunk_content = getattr(event, "content", None) or getattr(
                    event, "text", None
                )
                chunk_reasoning = getattr(event, "reasoning", None)

                # A. Reasoning (stream only)
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

                # B. Content (stream + buffer)
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

            # 7. Wait for run completion before fetching
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
                LOG.error(
                    "⏳ [DELEGATE] Worker run timed out. Attempting fetch anyway."
                )
                execution_had_error = True
                final_run_status = "timed_out"

            # 8. Fetch final report
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

            # 9. Submit tool output back to supervisor
            await self.submit_tool_output(
                thread_id=thread_id,
                assistant_id=assistant_id,
                tool_call_id=tool_call_id,
                content=final_content,
                action=action,
                is_error=execution_had_error,
            )

            # 10. Update action status
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
                    ephemeral_thread.id,
                    self._delete_ephemeral_thread,
                )

            yield self._research_status(
                "Delegation complete.",
                "completed" if not execution_had_error else "error",
                run_id,
            )
