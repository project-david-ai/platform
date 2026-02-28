from __future__ import annotations

import asyncio
import json
import os
import threading
from contextlib import asynccontextmanager
from typing import Any, AsyncGenerator, Callable, Dict, Optional

from projectdavid_common.utilities.logging_service import LoggingUtility
from projectdavid_common.validation import StatusEnum

from src.api.entities_api.orchestration.mixins.batfish_mixin import \
    _DEFAULT_SNAPSHOT_ID
from src.api.entities_api.utils.assistant_manager import AssistantManager

LOG = LoggingUtility()

_TERMINAL_RUN_STATES = {"completed", "failed", "cancelled", "expired"}
_WORKER_RUN_TIMEOUT = 1200
_WORKER_POLL_INTERVAL = 2.0
_ACTION_COMPLETION_TIMEOUT = 120.0
_ACTION_POLL_INTERVAL = 1.5


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
        self._ephemeral_user_map = None

    # ------------------------------------------------------------------
    # EMISSION HELPERS
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
    # HELPER: Bridges blocking generators to async loop
    # ------------------------------------------------------------------

    async def _stream_sync_generator(
        self, generator_func: Callable, *args, **kwargs
    ) -> AsyncGenerator[Any, None]:
        queue = asyncio.Queue()
        loop = asyncio.get_running_loop()
        stop_event = threading.Event()

        def producer():
            try:
                for item in generator_func(*args, **kwargs):
                    if stop_event.is_set():
                        break
                    loop.call_soon_threadsafe(queue.put_nowait, item)
                loop.call_soon_threadsafe(queue.put_nowait, None)
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
            LOG.debug("🛑 [_stream_sync_generator] Generator exited cleanly early.")
        except asyncio.CancelledError:
            LOG.debug("🛑[_stream_sync_generator] Task was cancelled.")
            raise
        finally:
            stop_event.set()

    # ------------------------------------------------------------------
    # HELPER: Shared stream consumer — zero guards, pure pass-through
    # ------------------------------------------------------------------

    async def _stream_worker_inference(
        self,
        ephemeral_thread,
        ephemeral_worker_id: str,
        msg,
        ephemeral_run,
        run_id: str,
    ) -> AsyncGenerator[str, None]:
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
            sync_stream.stream_events, model=self._delegation_model
        ):
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
        self, assistant_id: str, thread_id: str, delete_thread: bool = False
    ):
        LOG.info(f"🧹 [CLEANUP] Assistant: {assistant_id} | Thread: {thread_id}")
        if delete_thread and thread_id != "unknown_thread":
            try:
                await asyncio.to_thread(
                    self.project_david_client.threads.delete_thread, thread_id=thread_id
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
                    "✅[WORKER_FINAL_REPORT] Found report (length=%d): %s...",
                    len(final_text),
                    final_text[:100],
                )
                return final_text
            LOG.info("ℹ️[WORKER_FINAL_REPORT] No final text report found yet.")
            return None
        except Exception as e:
            LOG.exception("❌[WORKER_FINAL_REPORT_ERROR] Failed to fetch report: %s", e)
            return None

    # ------------------------------------------------------------------
    # HANDLER 1: Research Delegation
    # ------------------------------------------------------------------

    async def handle_delegate_research_task(
        self, thread_id, run_id, assistant_id, arguments_dict, tool_call_id, decision
    ) -> AsyncGenerator[str, None]:

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
            LOG.error(f"❌[RESEARCH_DELEGATE] Action creation failed: {e}")

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
                ephemeral_worker.id,
                ephemeral_thread.id,
            )

            yield self._research_status(
                "Worker active. Streaming...", "in_progress", run_id
            )

            max_junior_turns = 5
            for _ in range(max_junior_turns):
                async for chunk_event in self._stream_worker_inference(
                    ephemeral_thread, ephemeral_worker.id, msg, ephemeral_run, run_id
                ):
                    yield chunk_event

                junior_run_status = await asyncio.to_thread(
                    self.project_david_client.runs.retrieve_run, run_id=ephemeral_run.id
                )
                status_value = (
                    junior_run_status.status.value
                    if hasattr(junior_run_status.status, "value")
                    else str(junior_run_status.status)
                )

                if status_value in ("action_required", "requires_action"):
                    async for tool_chunk in self.process_tool_calls(
                        thread_id=ephemeral_thread.id,
                        run_id=ephemeral_run.id,
                        assistant_id=ephemeral_worker.id,
                    ):
                        yield tool_chunk
                    continue
                break

            yield self._research_status(
                "Worker processing. Waiting for completion...", "in_progress", run_id
            )

            try:
                final_run_status = await self._wait_for_run_completion(
                    run_id=ephemeral_run.id, thread_id=ephemeral_thread.id
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
                "██████[FINAL_THREAD_CONTENT_SUBMITTED_BY_RESEARCH_WORKER]=%s ██████",
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
        self,
        thread_id,
        run_id,
        assistant_id,
        arguments_dict,
        tool_call_id,
        decision,
    ) -> AsyncGenerator[str, None]:

        self._scratch_pad_thread = thread_id
        LOG.info(f"🔄 [ENGINEER_DELEGATE] STARTING. Run: {run_id}")

        if isinstance(arguments_dict, str):
            try:
                args = json.loads(arguments_dict)
            except Exception:
                LOG.warning("⚠️ Failed to parse arguments string as JSON.")
                args = {}
        else:
            args = arguments_dict

        batfish_tool = args.get("batfish_tool")
        task_context = args.get("task_context", "No context provided.")
        flag_criteria = args.get("flag_criteria", "None specified.")

        yield self._engineer_status(
            f"Initializing Junior Engineer for {batfish_tool}...", "in_progress", run_id
        )

        if not batfish_tool:
            LOG.error(
                "❌[ENGINEER_DELEGATE] Senior delegated task with NO Batfish tool."
            )
            await self.submit_tool_output(
                thread_id=thread_id,
                assistant_id=assistant_id,
                tool_call_id=tool_call_id,
                content="⚠️ DELEGATION REJECTED: Empty tool. Provide a specific Batfish RCA tool.",
                action=None,
                is_error=True,
            )
            yield self._engineer_status(
                "Delegation rejected: Empty tool.", "error", run_id
            )
            return

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
            LOG.error(f"❌[ENGINEER_DELEGATE] Action creation failed: {e}")

        ephemeral_junior = None
        execution_had_error = False
        ephemeral_run = None
        ephemeral_thread = None

        try:
            # ------------------------------------------------------------------
            # RESOLVE THE SNAPSHOT OWNER (SENIOR's user_id)
            # run_id here is the SENIOR's run — use the admin client to read
            # the real user_id since project_david_client returns user_id=None.
            # ------------------------------------------------------------------
            origin_user_id = getattr(self, "_batfish_owner_user_id", None)

            if not origin_user_id:
                try:
                    from projectdavid import Entity

                    admin_client = Entity(api_key=os.environ.get("ADMIN_API_KEY"))
                    run_obj = await asyncio.to_thread(
                        admin_client.runs.retrieve_run, run_id=run_id
                    )
                    origin_user_id = run_obj.user_id
                    self._batfish_owner_user_id = origin_user_id
                    LOG.info(
                        "ENGINEER_DELEGATE ▸ Resolved batfish_owner_user_id from Senior run: %s",
                        origin_user_id,
                    )
                except Exception as e:
                    LOG.warning(
                        "ENGINEER_DELEGATE ▸ Could not resolve run_user_id: %s", e
                    )

            if not origin_user_id:
                origin_user_id = os.getenv("ENTITIES_USER_ID")
                LOG.warning(
                    "ENGINEER_DELEGATE ▸ Falling back to ENTITIES_USER_ID: %s",
                    origin_user_id,
                )

            LOG.info("ENGINEER_DELEGATE ▸ batfish_owner_user_id=%s", origin_user_id)

            ephemeral_junior = await self.create_ephemeral_junior_engineer()

            # ------------------------------------------------------------------
            # FRESH THREAD PER INVOCATION — never reuse stale state
            # ------------------------------------------------------------------
            LOG.info("🧵 Creating new ephemeral thread for Junior Engineer...")
            ephemeral_thread = await asyncio.to_thread(
                self.project_david_client.threads.create_thread
            )

            prompt = (
                f"### INCIDENT ANALYSIS TASK\n\n"
                f"**TASK CONTEXT:**\n{task_context}\n\n"
                f"**TOOL TO EXECUTE:**\n`{batfish_tool}`\n\n"
                f"**FLAG CRITERIA:**\n{flag_criteria}\n\n"
                f"**CRITICAL INSTRUCTION:**\n"
                f"1. You MUST execute the `{batfish_tool}` function immediately.\n"
                f"2. Analyze the returned JSON output against the flag criteria.\n"
                f"3. Use the `append_scratchpad` tool to log the ✅ [RAW DATA] and any 🚩[FLAG]s.\n"
                f"4. Once execution is complete, reply with a synthesized summary containing explicit Evidence SNIPS."
            )

            msg = await self.create_ephemeral_message(
                ephemeral_thread.id, prompt, ephemeral_junior.id
            )

            # ------------------------------------------------------------------
            # STAMP THE SENIOR's user_id INTO THE JUNIOR's RUN METADATA
            # The Junior's fresh server-side worker instance has no other way
            # to know the real snapshot owner — it reads this back on stream start.
            # ------------------------------------------------------------------
            ephemeral_run = await self.create_ephemeral_run(
                ephemeral_junior.id,
                ephemeral_thread.id,
                meta_data={"batfish_owner_user_id": origin_user_id},
            )

            LOG.info(
                "ENGINEER_DELEGATE ▸ Stamped batfish_owner_user_id=%s into ephemeral run metadata",
                origin_user_id,
            )

            yield self._engineer_status(
                f"Junior Engineer executing {batfish_tool}...", "in_progress", run_id
            )

            # ------------------------------------------------------------------
            # STREAM LOOP
            # ------------------------------------------------------------------
            max_junior_turns = 5
            for _ in range(max_junior_turns):
                async for chunk_event in self._stream_worker_inference(
                    ephemeral_thread, ephemeral_junior.id, msg, ephemeral_run, run_id
                ):
                    yield chunk_event

                junior_run_status = await asyncio.to_thread(
                    self.project_david_client.runs.retrieve_run, run_id=ephemeral_run.id
                )
                status_value = (
                    junior_run_status.status.value
                    if hasattr(junior_run_status.status, "value")
                    else str(junior_run_status.status)
                )

                if status_value in ("action_required", "requires_action"):
                    async for tool_chunk in self.process_tool_calls(
                        thread_id=ephemeral_thread.id,
                        run_id=ephemeral_run.id,
                        assistant_id=ephemeral_junior.id,
                    ):
                        yield tool_chunk
                    continue

                break

            yield self._engineer_status(
                "Junior processing. Waiting for completion...", "in_progress", run_id
            )

            # ------------------------------------------------------------------
            # WAIT & FETCH
            # ------------------------------------------------------------------
            try:
                final_run_status = await self._wait_for_run_completion(
                    run_id=ephemeral_run.id, thread_id=ephemeral_thread.id
                )
                LOG.info(
                    "✅ [ENGINEER_DELEGATE] Junior run completed. Status=%s",
                    final_run_status,
                )
            except asyncio.TimeoutError:
                LOG.error(
                    "⏳[ENGINEER_DELEGATE] Junior run timed out. Attempting fetch anyway."
                )
                execution_had_error = True

            final_content = await self._fetch_worker_final_report(
                thread_id=ephemeral_thread.id
            )

            LOG.critical(
                "██████[FINAL_THREAD_CONTENT_SUBMITTED_BY_JUNIOR_ENGINEER]=%s ██████",
                final_content,
            )

            if not final_content:
                LOG.critical(
                    "██████[ENGINEER_DELEGATE_TOTAL_FAILURE] No content generated by the worker ██████"
                )
                final_content = "⚠️ Junior Engineer failed to produce a report."
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
            LOG.error(f"❌[ENGINEER_DELEGATE] Error: {e}", exc_info=True)
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
