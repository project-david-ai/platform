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

# How long to wait for any worker run to complete before giving up (seconds).
# Applies to both research workers and junior engineers.
_WORKER_RUN_TIMEOUT = 1200

# How often to poll the run status (seconds)
_WORKER_POLL_INTERVAL = 2.0


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

    def _engineer_status(self, activity: str, state: str, run_id: str) -> str:
        """
        Emits a network engineer delegation status event conforming to the
        EVENT_CONTRACT.  Structurally identical to _research_status but carries
        a distinct "type" discriminator so the frontend can route engineer
        lifecycle events separately from research events.

        Shape:
            {
                "type":     "engineer_status",
                "activity": "<human readable>",
                "state":    "in_progress" | "completed" | "error",
                "tool":     "delegate_engineer_task",
                "run_id":   "<uuid>"
            }
        """
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
    # HELPER: Bridges blocking generators to async loop (Fixes uvloop error)
    # Shared by both research and engineer delegation paths.
    # ------------------------------------------------------------------

    async def _stream_sync_generator(
        self, generator_func: Callable, *args, **kwargs
    ) -> AsyncGenerator[Any, None]:
        """
        Runs a synchronous generator in a background thread and yields items
        asynchronously.  Required because the SDK's synchronous_inference_stream
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
    # HELPER: Poll run status until terminal state or timeout.
    # Shared by both research and engineer delegation paths.
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

    # ------------------------------------------------------------------
    # HELPER: Lifecycle cleanup for any ephemeral assistant + thread.
    # Shared by both research and engineer delegation paths.
    # ------------------------------------------------------------------

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

    # ------------------------------------------------------------------
    # HELPER: Intercept tool outputs for inspection without breaking flow.
    # Shared by both research and engineer delegation paths.
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
    # Each workflow gets its own factory so assistant profiles (system
    # prompt + tool set) stay fully decoupled from the shared plumbing.
    # ------------------------------------------------------------------

    async def create_ephemeral_worker_assistant(self):
        """
        Spawns a Research Worker assistant pre-loaded with:
          - L4 Research Worker instructions
          - Web search + scratchpad tools
        Used exclusively by handle_delegate_research_task.
        """
        manager = AssistantManager()
        return await manager.create_ephemeral_worker_assistant()

    async def create_ephemeral_junior_engineer(self):
        """
        Spawns a Junior Network Engineer assistant pre-loaded with:
          - JUNIOR_ENGINEER_INSTRUCTIONS as system prompt
          - Network tools: execute_device_commands, run_local_diagnostic,
            search_inventory_by_group, get_device_info,
            read_scratchpad, append_scratchpad
        Used exclusively by handle_delegate_engineer_task.
        """
        manager = AssistantManager()
        return await manager.create_ephemeral_junior_engineer()

    # ------------------------------------------------------------------
    # SHARED PRIMITIVES: message / run creation and report fetching.
    # Called identically by both delegation handlers.
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

        Used by both research and engineer delegation paths to collect the
        worker's one-line confirmation (or any final text) after run completion.
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

                # Must NOT be a tool call (dispatching work)
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
    # INTERNAL: Shared stream consumer.
    #
    # Both delegation handlers perform identical stream filtering — strip
    # status/system events and tool call frames, pass through reasoning and
    # content chunks.  Extracted here to keep both handlers DRY.
    #
    # Yields raw JSON strings conforming to the delegation chunk contract:
    #   { "stream_type": "delegation", "chunk": { "type": ..., "content": ..., "run_id": ... } }
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

        Guards applied:
          - Drops status/system events (have .tool or .status attributes, or type == "status")
          - Drops tool call frames (.tool_calls or .function_call present)
          - Passes through reasoning chunks and content chunks
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

            # A. Reasoning chunks — stream only, not buffered
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

            # B. Content chunks — stream through to caller
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
    # HANDLER 1: Research Delegation  (unchanged from original)
    #
    # Called when the L4 Research Supervisor invokes `delegate_research_task`.
    # Spins up an ephemeral Research Worker, streams its first inference pass,
    # polls until the run is terminal, fetches the final report, and submits
    # the report as a tool output back to the Supervisor's thread.
    # ------------------------------------------------------------------

    async def handle_delegate_research_task(
        self, thread_id, run_id, assistant_id, arguments_dict, tool_call_id, decision
    ) -> AsyncGenerator[str, None]:
        """
        Research Supervisor → Research Worker delegation.

        Expected argument fields:
          - task         : the research micro-task description
          - requirements : any specific output requirements
        """
        # Pin the scratchpad to the Supervisor's thread so the Worker writes
        # its ✅/❓/⚠️ entries to the same shared pad the Supervisor reads.
        self._scratch_pad_thread = thread_id

        LOG.info(f"🔄 [RESEARCH_DELEGATE] STARTING. Run: {run_id}")

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

        # 3. Create Action record in DB
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

        try:
            # 4. Spin up Research Worker assistant + use existing worker thread slot
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

            # 5. Stream first inference pass via shared consumer
            async for chunk_event in self._stream_worker_inference(
                ephemeral_thread, ephemeral_worker.id, msg, ephemeral_run, run_id
            ):
                yield chunk_event

            # 6. Wait for run to reach a terminal state
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

            # 7. Fetch the Worker's final text report from the thread
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

            # 8. Submit the Worker's report as tool output back to the Supervisor
            await self.submit_tool_output(
                thread_id=thread_id,
                assistant_id=assistant_id,
                tool_call_id=tool_call_id,
                content=final_content,
                action=action,
                is_error=execution_had_error,
            )

            # 9. Close out the action record
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
            LOG.error(f"❌ [RESEARCH_DELEGATE] Error: {e}", exc_info=True)
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

    # ------------------------------------------------------------------
    # HANDLER 2: Engineer Delegation  (new)
    #
    # Called when the Senior Network Engineer invokes `delegate_engineer_task`.
    # Spins up an ephemeral Junior Engineer, streams its first inference pass,
    # polls until the run is terminal, fetches the final one-line confirmation,
    # and submits it as a tool output back to the Senior's thread.
    #
    # The Junior's real deliverable is its scratchpad entries (✅/🚩/⚠️),
    # not its text reply.  The text reply is just a confirmation signal.
    # Both Senior and Junior share the same scratchpad thread.
    # ------------------------------------------------------------------

    async def handle_delegate_engineer_task(
        self, thread_id, run_id, assistant_id, arguments_dict, tool_call_id, decision
    ) -> AsyncGenerator[str, None]:
        """
        Senior Network Engineer → Junior Network Engineer delegation.

        Expected argument fields (from delegate_engineer_task tool definition):
          - hostname       : exact device hostname, pre-resolved from inventory
          - commands       : list of read-only CLI commands (max 8)
          - task_context   : diagnostic hypothesis being tested
          - flag_criteria  : explicit conditions the Junior must 🚩 flag
        """
        # Pin the scratchpad to the Senior's thread so the Junior's
        # append_scratchpad calls land in the same pad the Senior reads.
        self._scratch_pad_thread = thread_id

        LOG.info(f"🔄 [ENGINEER_DELEGATE] STARTING. Run: {run_id}")

        # 1. Parse Arguments
        if isinstance(arguments_dict, str):
            try:
                args = json.loads(arguments_dict)
            except Exception:
                args = {"task": arguments_dict}
        else:
            args = arguments_dict

        # 2. Yield Initial Status
        yield self._engineer_status(
            "Initializing Junior Engineer...", "in_progress", run_id
        )

        # 3. Create Action record in DB
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

        try:
            # 4. Spin up Junior Engineer assistant + use existing worker thread slot
            ephemeral_junior = await self.create_ephemeral_junior_engineer()
            ephemeral_thread = self._research_worker_thread

            # Build a structured prompt that maps directly to the Junior's
            # execution algorithm: TARGET → COMMANDS → CONTEXT → FLAG CRITERIA.
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
            LOG.info(f"🔄 [JUNIOR_THREAD_ID]: {self._research_worker_thread}")

            # 5. Stream first inference pass via shared consumer
            async for chunk_event in self._stream_worker_inference(
                ephemeral_thread, ephemeral_junior.id, msg, ephemeral_run, run_id
            ):
                yield chunk_event

            # 6. Wait for run to reach a terminal state
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

            # 7. Fetch the Junior's one-line confirmation from the thread.
            #    The real data is in the scratchpad — this is just the signal
            #    that tells the Senior the Junior is done and has appended.
            final_content = await self._fetch_worker_final_report(
                thread_id=ephemeral_thread.id
            )

            LOG.critical(
                "██████ [FINAL_THREAD_CONTENT_SUBMITTED_BY_JUNIOR_ENGINEER]=%s ██████",
                final_content,
            )

            if not final_content:
                LOG.critical(
                    "██████ [ENGINEER_DELEGATE_TOTAL_FAILURE] No confirmation from Junior Engineer ██████"
                )
                final_content = "No confirmation generated by Junior Engineer."
                execution_had_error = True

            # 8. Submit the Junior's confirmation as tool output back to the Senior.
            #    The Senior will then call read_scratchpad to inspect the real findings.
            await self.submit_tool_output(
                thread_id=thread_id,
                assistant_id=assistant_id,
                tool_call_id=tool_call_id,
                content=final_content,
                action=action,
                is_error=execution_had_error,
            )

            # 9. Close out the action record
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
                await self._ephemeral_clean_up(
                    ephemeral_junior.id,
                    ephemeral_thread.id,
                    self._delete_ephemeral_thread,
                )

            yield self._engineer_status(
                "Junior Engineer task complete.",
                "completed" if not execution_had_error else "error",
                run_id,
            )
