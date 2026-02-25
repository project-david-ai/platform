# src/api/entities_api/orchestration/mixins/delegation_mixin.py
from __future__ import annotations

import asyncio
import json
import threading
from contextlib import asynccontextmanager
from typing import Any, AsyncGenerator, Callable, Dict, Optional

from projectdavid import ToolCallRequestEvent
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

# How long to wait for any worker run to complete before giving up (seconds).
_WORKER_RUN_TIMEOUT = 1200

# How often to poll the run status (seconds)
_WORKER_POLL_INTERVAL = 2.0

# How long to wait for developer backend to handle an intercepted tool call (seconds).
_ACTION_COMPLETION_TIMEOUT = 120.0

# How often to poll action status (seconds)
_ACTION_POLL_INTERVAL = 1.5


class DelegationMixin:
    """
    Spins up ephemeral Worker Loops using the project_david_client strictly.

    Supports two delegation workflows that share all plumbing but differ
    in the assistant profile and prompt structure:

      1. Research Workflow  →  handle_delegate_research_task
         Supervisor:  L4 Research Supervisor
         Worker:      L4 Research Worker (web search tools)
         Status type: "research_status"

      2. Network Engineering Workflow  →  handle_delegate_engineer_task
         Supervisor:  Senior Network Engineer
         Worker:      Junior Network Engineer (SSH + diagnostic tools)
         Status type: "engineer_status"

    Emission style:
    - All stream events are emitted as raw JSON strings conforming to the
      EVENT_CONTRACT via the dedicated status helpers.
    - The "type" field is the discriminator for frontend routing:
        "research_status"  — research delegation lifecycle events
        "engineer_status"  — engineer delegation lifecycle events
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._delegation_api_key = None
        self._delete_ephemeral_thread = False
        self._delegation_model = None
        self._research_worker_thread = None

        # Shared scratchpad thread — set to the Supervisor's thread_id at
        # delegation time so both Supervisor and Worker read/write the same pad.
        self._scratch_pad_thread = None

    # ------------------------------------------------------------------
    # EMISSION HELPERS
    # One helper per workflow so frontend routing stays clean.
    # ------------------------------------------------------------------

    def _research_status(self, activity: str, state: str, run_id: str) -> str:
        return json.dumps(
            {
                "type": "research_status",
                "message": activity,
                "status": state,
                "tool": "delegate_research_task",
                "run_id": run_id,
            }
        )

    def _engineer_status(self, activity: str, state: str, run_id: str) -> str:
        return json.dumps(
            {
                "type": "engineer_status",
                "message": activity,
                "status": state,
                "tool": "delegate_engineer_task",
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
        Runs a synchronous generator in a background thread and yields items
        asynchronously. Required because the SDK's synchronous_inference_stream
        uses blocking iteration which is incompatible with uvloop directly.
        """
        queue = asyncio.Queue()
        loop = asyncio.get_running_loop()

        def producer():
            try:
                for item in generator_func(*args, **kwargs):
                    loop.call_soon_threadsafe(queue.put_nowait, item)
                loop.call_soon_threadsafe(queue.put_nowait, None)  # Sentinel
            except Exception as e:
                LOG.error(f"🧵[THREAD-ERR] {e}")
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
    # HELPER: Poll run status until terminal state or timeout.
    # ------------------------------------------------------------------

    async def _wait_for_run_completion(
        self,
        run_id: str,
        thread_id: str,
        timeout: float = _WORKER_RUN_TIMEOUT,
        poll_interval: float = _WORKER_POLL_INTERVAL,
    ) -> str:
        """
        Polls a worker run until it reaches a terminal state.
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
    # HELPER: Poll action status until terminal state or timeout.
    # Used after emitting a ToolInterceptEvent to block until the
    # developer's backend has executed the tool and submitted the result.
    # ------------------------------------------------------------------

    async def _wait_for_action_completion(
        self,
        action_id: str,
        timeout: float = _ACTION_COMPLETION_TIMEOUT,
        poll_interval: float = _ACTION_POLL_INTERVAL,
    ) -> bool:
        """
        Polls until the given action reaches 'completed' or 'failed'.
        Returns True if completed cleanly, False if timed out or failed.
        """
        terminal_states = {"completed", "failed"}
        elapsed = 0.0

        LOG.info(
            "⏳ [ENGINEER_DELEGATE] Polling action %s for developer completion...",
            action_id,
        )

        while elapsed < timeout:
            try:
                action = await asyncio.to_thread(
                    self.project_david_client.actions.get_action, action_id
                )
                status = getattr(action, "status", None)
                # Handle enum or raw string
                status_value = status.value if hasattr(status, "value") else str(status)

                LOG.debug(
                    "[ENGINEER_DELEGATE] Action %s status: %s (elapsed=%.1fs)",
                    action_id,
                    status_value,
                    elapsed,
                )

                if status_value in terminal_states:
                    LOG.info(
                        "✅ [ENGINEER_DELEGATE] Action %s reached terminal state: %s",
                        action_id,
                        status_value,
                    )
                    return status_value == "completed"

            except Exception as e:
                LOG.warning(
                    "⚠️ [ENGINEER_DELEGATE] Poll error for action %s: %s", action_id, e
                )

            await asyncio.sleep(poll_interval)
            elapsed += poll_interval

        LOG.error(
            "⏳ [ENGINEER_DELEGATE] Timed out waiting for action %s after %.0fs.",
            action_id,
            timeout,
        )
        return False

    # ------------------------------------------------------------------
    # HELPER: Lifecycle cleanup for any ephemeral assistant + thread.
    # ------------------------------------------------------------------

    async def _ephemeral_clean_up(
        self, assistant_id: str, thread_id: str, delete_thread: bool = False
    ):
        LOG.info(f"🧹 [CLEANUP] Assistant: {assistant_id} | Thread: {thread_id}")
        if delete_thread and thread_id != "unknown_thread":
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

    # ------------------------------------------------------------------
    # HELPER: Intercept tool outputs for inspection without breaking flow.
    # ------------------------------------------------------------------

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
    # EPHEMERAL ASSISTANT FACTORIES
    # ------------------------------------------------------------------

    async def create_ephemeral_worker_assistant(self):
        manager = AssistantManager()
        return await manager.create_ephemeral_worker_assistant()

    async def create_ephemeral_junior_engineer(self):
        manager = AssistantManager()
        return await manager.create_ephemeral_junior_engineer()

    # ------------------------------------------------------------------
    # SHARED PRIMITIVES
    # ------------------------------------------------------------------

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
        Retrieves the most recent plain-text assistant message from a thread.
        Skips tool calls, tool outputs, and empty messages.
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

                if role != "assistant":
                    continue
                if tool_calls:
                    continue
                if not isinstance(content, str) or not content.strip():
                    continue

                final_text = content.strip()
                LOG.info(
                    "✅[WORKER_FINAL_REPORT] Found report (length=%d): %s...",
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
    # INTERNAL: Shared stream consumer.
    # ------------------------------------------------------------------

    async def _stream_worker_inference(
        self,
        ephemeral_thread,
        ephemeral_worker_id: str,
        msg,
        ephemeral_run,
        run_id: str,
    ) -> AsyncGenerator[str, None]:
        """
        Configures the synchronous inference stream for an ephemeral worker,
        iterates its events, and yields delegation chunk JSON strings.
        """
        sync_stream = self.project_david_client.synchronous_inference_stream
        sync_stream.setup(
            thread_id=ephemeral_thread.id,
            assistant_id=ephemeral_worker_id,
            message_id=msg.id,
            run_id=ephemeral_run.id,
            api_key=self._delegation_api_key,
        )

        LOG.critical(
            "🎬 WORKER STREAM STARTING - If you see this but no content chunks, "
            "check process_tool_calls wiring"
        )

        async for event in self._stream_sync_generator(
            sync_stream.stream_events,
            model=self._delegation_model,
        ):
            # 🛑 GUARD 1: Drop status/system events
            if (
                hasattr(event, "tool")
                or hasattr(event, "status")
                or getattr(event, "type", "") == "status"
            ):
                continue

            # 🛑 GUARD 2: Drop tool call argument frames
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

    # ------------------------------------------------------------------
    # HANDLER 1: Research Delegation
    # ------------------------------------------------------------------
    async def handle_delegate_research_task(
        self, thread_id, run_id, assistant_id, arguments_dict, tool_call_id, decision
    ) -> AsyncGenerator[str, None]:
        """
        Research Supervisor → Research Worker delegation.
        """
        self._scratch_pad_thread = thread_id

        LOG.info(f"🔄 [RESEARCH_DELEGATE] STARTING. Run: {run_id}")

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
            LOG.error(f"❌ [RESEARCH_DELEGATE] Action creation failed: {e}")

        ephemeral_worker = None
        execution_had_error = False
        ephemeral_run = None
        ephemeral_thread = None

        try:
            ephemeral_worker = await self.create_ephemeral_worker_assistant()

            if not self._research_worker_thread:
                LOG.info("🧵 Creating new ephemeral thread for Research Worker...")
                self._research_worker_thread = await asyncio.to_thread(
                    self.project_david_client.threads.create_thread
                )
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

            LOG.info(f"🔄[SUPERVISORS_THREAD_ID]: {thread_id}")
            LOG.info(f"🔄[WORKERS_THREAD_ID]: {self._research_worker_thread}")

            async for chunk_event in self._stream_worker_inference(
                ephemeral_thread, ephemeral_worker.id, msg, ephemeral_run, run_id
            ):
                yield chunk_event

            yield self._research_status(
                "Worker processing. Waiting for completion...", "in_progress", run_id
            )

            try:
                final_run_status = await self._wait_for_run_completion(
                    run_id=ephemeral_run.id,
                    thread_id=ephemeral_thread.id,
                )
                LOG.info(
                    "✅ [RESEARCH_DELEGATE] Worker run completed. Status=%s",
                    final_run_status,
                )
            except asyncio.TimeoutError:
                LOG.error(
                    "⏳ [RESEARCH_DELEGATE] Worker run timed out. Attempting fetch anyway."
                )
                execution_had_error = True

            final_content = await self._fetch_worker_final_report(
                thread_id=ephemeral_thread.id
            )

            LOG.critical(
                "██████ [FINAL_THREAD_CONTENT_SUBMITTED_BY_RESEARCH_WORKER]=%s ██████",
                final_content,
            )

            if not final_content:
                LOG.critical(
                    "██████ [RESEARCH_DELEGATE_TOTAL_FAILURE] No content generated by the worker ██████"
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
            LOG.error(f"❌[RESEARCH_DELEGATE] Error: {e}", exc_info=True)
            yield self._research_status(f"Error: {str(e)}", "error", run_id)

        finally:
            if ephemeral_worker:
                thread_id_to_clean = (
                    ephemeral_thread.id if ephemeral_thread else "unknown_thread"
                )
                await self._ephemeral_clean_up(
                    ephemeral_worker.id,
                    thread_id_to_clean,
                    self._delete_ephemeral_thread,
                )

            yield self._research_status(
                "Delegation complete.",
                "completed" if not execution_had_error else "error",
                run_id,
            )

    # ------------------------------------------------------------------
    # HANDLER 2: Engineer Delegation
    # ------------------------------------------------------------------
    async def handle_delegate_engineer_task(
        self, thread_id, run_id, assistant_id, arguments_dict, tool_call_id, decision
    ) -> AsyncGenerator[str, None]:
        """
        Senior Network Engineer → Junior Network Engineer delegation.

        Flow:
          Turn 1 — Junior plans and fires execute_network_command.
                    We intercept the ToolCallRequestEvent and yield a
                    ToolInterceptEvent for the developer's backend to handle.
          [BLOCK] — Poll until the developer confirms the action is completed.
          Turn 2 — Inject an analysis prompt and create a new run so the
                    Junior can reason over the returned CLI output.
          Report  — Fetch the Junior's final text and submit it back to the Senior.
        """
        self._scratch_pad_thread = thread_id

        LOG.info(f"🔄 [ENGINEER_DELEGATE] STARTING. Run: {run_id}")

        # 1. Parse Arguments Safely
        if isinstance(arguments_dict, str):
            try:
                args = json.loads(arguments_dict)
            except Exception:
                LOG.warning("⚠️ Failed to parse arguments string as JSON.")
                args = {}
        else:
            args = arguments_dict

        # 2. Yield Initial Status
        yield self._engineer_status(
            "Initializing Junior Engineer...", "in_progress", run_id
        )

        # 3. Create Action (DB)
        action = None
        try:
            action = await asyncio.to_thread(
                self.project_david_client.actions.create_action,
                tool_name="delegate_engineer_task",
                run_id=run_id,
                tool_call_id=tool_call_id,
                function_args=arguments_dict,
                decision=decision,
            )
        except Exception as e:
            LOG.error(f"❌ [ENGINEER_DELEGATE] Action creation failed: {e}")

        ephemeral_junior = None
        execution_had_error = False
        ephemeral_run = None
        ephemeral_thread = None

        # Track the intercepted action so we can poll it in step 6.5
        intercepted_action_id: Optional[str] = None

        try:
            # 4. Setup Ephemeral Assistant & Thread
            ephemeral_junior = await self.create_ephemeral_junior_engineer()

            if not self._research_worker_thread:
                LOG.info("🧵 Creating new ephemeral thread for Junior Engineer...")
                self._research_worker_thread = await asyncio.to_thread(
                    self.project_david_client.threads.create_thread
                )
            ephemeral_thread = self._research_worker_thread

            prompt = (
                f"TARGET DEVICE: {args.get('hostname', 'NOT SPECIFIED')}\n"
                f"COMMANDS:\n{json.dumps(args.get('commands', []), indent=2)}\n"
                f"TASK CONTEXT: {args.get('task_context', 'No context provided.')}\n"
                f"FLAG IF: {args.get('flag_criteria', 'No specific flag criteria provided.')}"
            )

            msg = await self.create_ephemeral_message(
                ephemeral_thread.id, prompt, ephemeral_junior.id
            )
            ephemeral_run = await self.create_ephemeral_run(
                ephemeral_junior.id, ephemeral_thread.id
            )

            yield self._engineer_status(
                f"Junior Engineer active on {args.get('hostname', '?')}. Streaming...",
                "in_progress",
                run_id,
            )

            LOG.info(f"🔄 [SENIOR_THREAD_ID]: {thread_id}")
            LOG.info(f"🔄 [JUNIOR_THREAD_ID]: {ephemeral_thread.id}")

            # 5. Configure Stream (Turn 1)
            sync_stream = self.project_david_client.synchronous_inference_stream
            sync_stream.setup(
                thread_id=ephemeral_thread.id,
                assistant_id=ephemeral_junior.id,
                message_id=msg.id,
                run_id=ephemeral_run.id,
                api_key=self._delegation_api_key,
            )

            LOG.critical("🎬 JUNIOR ENGINEER STREAM STARTING — TURN 1")

            # 6. Stream Turn 1 — intercept the tool call, yield it for developer handling
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

                # 🛑 GUARD 2: Exclude Tool Call Argument Frames
                if getattr(event, "tool_calls", None) or getattr(
                    event, "function_call", None
                ):
                    continue

                # ✅ INTERCEPT: Capture the action_id and yield the event for
                #    the developer's backend to execute via execute_intercepted().
                if isinstance(event, ToolCallRequestEvent):
                    intercepted_action_id = event.action_id  # ← capture for polling
                    LOG.info(
                        "🔧 [ENGINEER_DELEGATE] Intercepted tool call: %s | action_id: %s",
                        event.tool_name,
                        intercepted_action_id,
                    )
                    yield json.dumps(
                        {
                            "type": "tool_intercept",
                            "tool_name": event.tool_name,
                            "args": event.args,
                            "action_id": event.action_id,
                            "tool_call_id": event.tool_call_id,
                            "origin": "junior_engineer",
                            "thread_id": ephemeral_thread.id,
                            "run_id": run_id,
                            "origin_run_id": ephemeral_run.id,
                            "origin_assistant_id": ephemeral_junior.id,
                        }
                    )
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

            # ------------------------------------------------------------------
            # 6.5 SECOND TURN — only fires if a tool was intercepted
            # ------------------------------------------------------------------
            if intercepted_action_id:
                yield self._engineer_status(
                    "Waiting for local tool execution to complete...",
                    "in_progress",
                    run_id,
                )

                tool_completed = await self._wait_for_action_completion(
                    action_id=intercepted_action_id,
                )

                if tool_completed:
                    LOG.info(
                        "✅ [ENGINEER_DELEGATE] Action %s confirmed complete. Triggering Turn 2.",
                        intercepted_action_id,
                    )
                    yield self._engineer_status(
                        "Command output received. Junior Engineer analysing results...",
                        "in_progress",
                        run_id,
                    )

                    # Inject an analysis prompt — gives the Junior clear direction
                    # for Turn 2 without needing it to re-examine its own tool call.
                    analysis_prompt = (
                        f"The network command has been executed on {args.get('hostname', 'the target device')}. "
                        f"The CLI output has been returned as a tool result in your context.\n\n"
                        f"Please now:\n"
                        f"1. Carefully analyse the command output.\n"
                        f"2. Identify any issues, anomalies, or items matching the flag criteria: "
                        f"{args.get('flag_criteria', 'any notable findings')}.\n"
                        f"3. Provide a concise diagnostic report summarising your findings "
                        f"and any recommended next steps."
                    )

                    analysis_msg = await self.create_ephemeral_message(
                        ephemeral_thread.id, analysis_prompt, ephemeral_junior.id
                    )

                    # New run — tool result + analysis prompt are now last on the thread
                    ephemeral_run = await self.create_ephemeral_run(
                        ephemeral_junior.id, ephemeral_thread.id
                    )

                    LOG.critical("🎬 JUNIOR ENGINEER STREAM STARTING — TURN 2")

                    sync_stream.setup(
                        thread_id=ephemeral_thread.id,
                        assistant_id=ephemeral_junior.id,
                        message_id=analysis_msg.id,
                        run_id=ephemeral_run.id,
                        api_key=self._delegation_api_key,
                    )

                    async for event in self._stream_sync_generator(
                        sync_stream.stream_events,
                        model=self._delegation_model,
                    ):
                        if (
                            hasattr(event, "tool")
                            or hasattr(event, "status")
                            or getattr(event, "type", "") == "status"
                        ):
                            continue

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

                else:
                    LOG.error(
                        "❌ [ENGINEER_DELEGATE] Action %s did not complete in time. "
                        "Skipping Turn 2.",
                        intercepted_action_id,
                    )
                    execution_had_error = True

            # 7. Wait for the active run (Turn 1 if no intercept, Turn 2 if intercept) to complete
            yield self._engineer_status(
                "Junior processing. Waiting for completion...", "in_progress", run_id
            )

            try:
                final_run_status = await self._wait_for_run_completion(
                    run_id=ephemeral_run.id,
                    thread_id=ephemeral_thread.id,
                )
                LOG.info(
                    "✅ [ENGINEER_DELEGATE] Junior run completed. Status=%s",
                    final_run_status,
                )
            except asyncio.TimeoutError:
                LOG.error(
                    "⏳ [ENGINEER_DELEGATE] Junior run timed out. Attempting fetch anyway."
                )
                execution_had_error = True

            # 8. Fetch final report
            final_content = await self._fetch_worker_final_report(
                thread_id=ephemeral_thread.id
            )

            LOG.critical(
                "██████[FINAL_THREAD_CONTENT_SUBMITTED_BY_JUNIOR_ENGINEER]=%s ██████",
                final_content,
            )

            if not final_content:
                LOG.critical(
                    "██████[ENGINEER_DELEGATE_TOTAL_FAILURE] No confirmation generated by Junior Engineer ██████"
                )
                final_content = "No confirmation generated by Junior Engineer."
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
            LOG.error(f"❌ [ENGINEER_DELEGATE] Error: {e}", exc_info=True)
            yield self._engineer_status(f"Error: {str(e)}", "error", run_id)

        finally:
            if ephemeral_junior:
                thread_id_to_clean = (
                    ephemeral_thread.id if ephemeral_thread else "unknown_thread"
                )
                await self._ephemeral_clean_up(
                    ephemeral_junior.id,
                    thread_id_to_clean,
                    self._delete_ephemeral_thread,
                )

            yield self._engineer_status(
                "Junior Engineer task complete.",
                "completed" if not execution_had_error else "error",
                run_id,
            )
