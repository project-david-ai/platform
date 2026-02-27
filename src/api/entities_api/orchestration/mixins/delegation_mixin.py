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
                "activity": activity,
                "state": state,
                "tool": "delegate_research_task",
                "run_id": run_id,
            }
        )

    def _engineer_status(self, activity: str, state: str, run_id: str) -> str:
        return json.dumps(
            {
                "type": "engineer_status",
                "activity": activity,
                "state": state,
                "tool": "delegate_engineer_task",
                "run_id": run_id,
            }
        )

    # ------------------------------------------------------------------
    # HELPER: Bridges blocking generators to async loop (Memory Leak Fix)
    # ------------------------------------------------------------------

    async def _stream_sync_generator(
        self, generator_func: Callable, *args, **kwargs
    ) -> AsyncGenerator[Any, None]:
        """
        Runs a synchronous generator in a background thread and yields items
        asynchronously. Includes strict cleanup to prevent pending task destruction.
        """
        queue = asyncio.Queue()
        loop = asyncio.get_running_loop()
        stop_event = threading.Event()

        def producer():
            try:
                for item in generator_func(*args, **kwargs):
                    if stop_event.is_set():
                        break
                    loop.call_soon_threadsafe(queue.put_nowait, item)
                loop.call_soon_threadsafe(queue.put_nowait, None)  # Sentinel
            except Exception as e:
                LOG.error(f"🧵[THREAD-ERR] {e}")
                loop.call_soon_threadsafe(queue.put_nowait, e)

        thread = threading.Thread(target=producer, daemon=True)
        thread.start()

        try:
            while True:
                item = await queue.get()
                if item is None:
                    break
                if isinstance(item, Exception):
                    raise item
                yield item
        except GeneratorExit:
            # The consumer stopped listening early (e.g. client disconnected)
            LOG.debug("🛑 [_stream_sync_generator] Generator exited cleanly early.")
            pass
        except asyncio.CancelledError:
            LOG.debug("🛑 [_stream_sync_generator] Task was cancelled.")
            raise
        finally:
            # Tell the background thread to stop iterating and clean up
            stop_event.set()

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
            "⏳[ENGINEER_DELEGATE] Polling action %s for developer completion...",
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
                    "⚠️[ENGINEER_DELEGATE] Poll error for action %s: %s", action_id, e
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
            LOG.exception("❌[WORKER_FINAL_REPORT_ERROR] Failed to fetch report: %s", e)
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
    # HANDLER 2: Engineer Delegation (Batfish RCA Data-Plane Simulation)
    # ------------------------------------------------------------------
    async def handle_delegate_engineer_task(
        self,
        thread_id,
        run_id,
        assistant_id,
        arguments_dict,
        tool_call_id,
        decision,
    ) -> AsyncGenerator[str, None]:
        """
        Senior Network Engineer → Junior Network Engineer delegation.

        Flow:
          Turn 1 — Junior plans and fires `run_batfish_tool`.
                   We intercept the tool call SERVER-SIDE, dynamically inject the snapshot_id,
                   execute the Batfish RCA tool directly, and submit the JSON output.
          Turn 2 — Inject an analysis prompt and create a new run so the
                   Junior can synthesize the JSON into evidence snips.
          Report — Fetch the Junior's final text and submit it back to the Senior.
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

        snapshot_id = args.get("snapshot_id", "UNKNOWN_SNAPSHOT")
        batfish_tools = args.get("batfish_tools", [])
        task_context = args.get("task_context", "No context provided.")
        flag_criteria = args.get("flag_criteria", "None specified.")

        # 2. Yield Initial Status
        yield self._engineer_status(
            f"Initializing Junior Engineer for Batfish RCA...",
            "in_progress",
            run_id,
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

        # 3.5 Reject invalid delegations immediately
        if not batfish_tools:
            LOG.error(
                "❌[ENGINEER_DELEGATE] Senior delegated task with NO Batfish tools."
            )
            error_msg = "⚠️ DELEGATION REJECTED: You assigned a task but provided an empty tool list. Provide specific Batfish RCA tools and try again."
            await self.submit_tool_output(
                thread_id=thread_id,
                assistant_id=assistant_id,
                tool_call_id=tool_call_id,
                content=error_msg,
                action=action,
                is_error=True,
            )
            if action:
                await asyncio.to_thread(
                    self.project_david_client.actions.update_action,
                    action_id=action.id,
                    status=StatusEnum.failed.value,
                )
            yield self._engineer_status(
                "Delegation rejected: Empty tools.", "error", run_id
            )
            return

        ephemeral_junior = None
        execution_had_error = False
        ephemeral_run = None
        ephemeral_thread = None

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
                f"### NEW INCIDENT TASK DELEGATION\n\n"
                f"**TARGET BATFISH RCA TOOLS:**\n{json.dumps(batfish_tools, indent=2)}\n\n"
                f"**TASK CONTEXT:**\n{task_context}\n\n"
                f"**FLAG CRITERIA:**\n{flag_criteria}\n\n"
                f"**CRITICAL INSTRUCTION:**\n"
                f"You MUST immediately call the `run_batfish_tool` tool for the RCA tools listed above. "
                f"Do NOT output conversational text first. Invoke the tool immediately."
            )

            msg = await self.create_ephemeral_message(
                ephemeral_thread.id, prompt, ephemeral_junior.id
            )
            ephemeral_run = await self.create_ephemeral_run(
                ephemeral_junior.id, ephemeral_thread.id
            )

            yield self._engineer_status(
                "Junior Engineer active. Streaming...", "in_progress", run_id
            )

            LOG.info(f"🔄[SENIOR_THREAD_ID]: {thread_id}")
            LOG.info(f"🔄[JUNIOR_THREAD_ID]: {ephemeral_thread.id}")

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

            tool_completed = False

            # 6. Stream Turn 1
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

                # ✅ INTERCEPT: Server-Side Nested Tool Execution
                if isinstance(event, ToolCallRequestEvent):
                    if event.tool_name == "run_batfish_tool":
                        named_tool = event.args.get("batfish_tool_name")
                        LOG.info(
                            f"🔧[ENGINEER_DELEGATE] Executing Batfish Tool server-side: {named_tool}"
                        )

                        yield self._engineer_status(
                            f"Running Batfish analysis: {named_tool}...",
                            "in_progress",
                            run_id,
                        )

                        try:
                            # Programmatically inject the snapshot_id the Junior doesn't know about
                            res = await asyncio.to_thread(
                                self.project_david_client.batfish.run_tool,
                                snapshot_id=snapshot_id,
                                tool_name=named_tool,
                            )
                            tool_output = (
                                json.dumps(res, indent=2) if res else "No issues found."
                            )
                        except Exception as exc:
                            LOG.error(f"❌ Batfish Tool Error: {exc}")
                            tool_output = f"Error running tool {named_tool}: {str(exc)}"

                        # Submit the result straight back to the Junior's ephemeral thread
                        await self.submit_tool_output(
                            thread_id=ephemeral_thread.id,
                            assistant_id=ephemeral_junior.id,
                            tool_call_id=event.tool_call_id,
                            content=tool_output,
                            action=None,
                            is_error=False,
                        )
                        tool_completed = True
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

            # ------------------------------------------------------------------
            # 6.5 SECOND TURN
            # ------------------------------------------------------------------
            if tool_completed:
                yield self._engineer_status(
                    "Batfish output received. Junior Engineer synthesising results...",
                    "in_progress",
                    run_id,
                )

                analysis_prompt = (
                    f"The Batfish tools have been successfully executed. "
                    f"The RCA JSON output has been returned as a tool result in your context.\n\n"
                    f"**MANDATORY INSTRUCTIONS:**\n"
                    f"1. You MUST evaluate the output against the flag criteria: {flag_criteria}\n"
                    f"2. You MUST use the `append_scratchpad` tool to log the ✅ [RAW DATA] and any 🚩[FLAG]s.\n"
                    f"3. You MUST reply with a synthesized summary containing explicit Evidence SNIPS from the JSON.\n"
                    f"Do NOT output just a newline. Hallucination will not be tolerated."
                )

                analysis_msg = await self.create_ephemeral_message(
                    ephemeral_thread.id, analysis_prompt, ephemeral_junior.id
                )

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
                    "❌ [ENGINEER_DELEGATE] Junior never triggered a Batfish tool."
                )
                execution_had_error = True

            # 7. Fetch final report
            # NOTE: Run lifecycle is managed platform-side. No polling required.
            final_content = await self._fetch_worker_final_report(
                thread_id=ephemeral_thread.id
            )

            LOG.critical("██████[FINAL_CONTENT_BY_JUNIOR]=%s ██████", final_content)

            if not final_content:
                if tool_completed:
                    LOG.info(
                        "⚠️ [ENGINEER_DELEGATE] Junior output no text in Turn 2, synthesizing success."
                    )
                    final_content = "✅ Batfish analysis successfully executed. See supplementary scratchpad."
                    execution_had_error = False
                else:
                    LOG.critical(
                        "██████[ENGINEER_DELEGATE_TOTAL_FAILURE] Junior completely failed. ██████"
                    )
                    final_content = "⚠️ JUNIOR FORMATTING ERROR: The Junior Engineer failed to invoke the Batfish tool."
                    execution_had_error = True

            # 8. Submit output
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
