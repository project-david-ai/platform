# src/api/entities_api/workers/qwen_worker.py
from __future__ import annotations

import asyncio
import json
import os
import uuid
from abc import ABC, abstractmethod
from typing import Any, AsyncGenerator, Dict, Optional, Union

from dotenv import load_dotenv
from projectdavid import StreamEvent
from projectdavid_common.utilities.logging_service import LoggingUtility
from projectdavid_common.validation import StatusEnum

from entities_api.cache.assistant_cache import AssistantCache
from entities_api.clients.delta_normalizer import DeltaNormalizer

# --- DEPENDENCIES ---
from src.api.entities_api.dependencies import get_redis_sync
from src.api.entities_api.orchestration.engine.orchestrator_core import OrchestratorCore

# --- MIXINS ---
from src.api.entities_api.orchestration.mixins.provider_mixins import _ProviderMixins

load_dotenv()
LOG = LoggingUtility()


class QwenBaseWorker(
    _ProviderMixins,
    OrchestratorCore,
    ABC,
):
    """
    Async Base for Qwen Providers (Hyperbolic, Together, etc.).
    Handles QwQ-32B/Qwen2.5/Kimi specific stream parsing using DeltaNormalizer.
    """

    def __init__(
        self,
        *,
        assistant_id: str | None = None,
        thread_id: str | None = None,
        redis=None,
        base_url: str | None = None,
        api_key: str | None = None,
        delete_ephemeral_thread: bool = False,
        assistant_cache_service: Optional[AssistantCache] = None,
        **extra,
    ) -> None:

        # 1. Config & Dependencies
        self.api_key = api_key or extra.get("api_key")
        # ephemeral worker config
        # These objects are used for deep search and engineering flows
        self.is_deep_research = None
        self.is_engineer = None
        self._delete_ephemeral_thread = delete_ephemeral_thread or extra.get(
            "delete_ephemeral_thread"
        )
        self.ephemeral_supervisor_id = None
        self._delegation_api_key = self.api_key
        self._research_worker_thread = None
        self._worker_thread = None

        self.redis = redis or get_redis_sync()

        # 2. Cache Service Setup
        if assistant_cache_service:
            self._assistant_cache = assistant_cache_service
        elif "assistant_cache" in extra and isinstance(extra["assistant_cache"], AssistantCache):
            self._assistant_cache = extra["assistant_cache"]

        # 3. Config Legacy Support
        legacy_config = extra.get("assistant_config") or extra.get("assistant_cache")
        self.assistant_config: Dict[str, Any] = (
            legacy_config if isinstance(legacy_config, dict) else {}
        )

        self._david_client: Any = None
        self.assistant_id = assistant_id
        self.thread_id = thread_id
        self.base_url = base_url or os.getenv("BASE_URL")

        self.model_name = extra.get("model_name", "qwen/Qwen1_5-32B-Chat")
        self.max_context_window = extra.get("max_context_window", 128000)
        self.threshold_percentage = extra.get("threshold_percentage", 0.8)

        # 4. State Tracking
        self._current_tool_call_id: str | None = None
        self._pending_tool_payload: Optional[Dict[str, Any]] = None
        self._decision_payload: Optional[Dict[str, Any]] = None

        self.setup_services()

        # 5. Mixin Safety Stub
        if not hasattr(self, "get_function_call_state"):
            LOG.error("CRITICAL: ToolRoutingMixin failed to load.")
            self.get_function_call_state = lambda: None
            self.set_function_call_state = lambda x: None
            self.set_tool_response_state = lambda x: None

        LOG.debug(f"{self.__class__.__name__} ready (assistant={assistant_id})")

    @abstractmethod
    def _get_client_instance(self, api_key: str):
        pass

    async def stream(
        self,
        thread_id: str,
        message_id: str | None,
        run_id: str,
        assistant_id: str,
        model: Any,
        *,
        force_refresh: bool = False,
        stream_reasoning: bool = True,
        api_key: str | None = None,
        **kwargs,
    ) -> AsyncGenerator[Union[str, StreamEvent], None]:
        """
        Unified stream method supporting four distinct assistant roles:

          1. SENIOR ENGINEER (Supervisor)  — is_engineer=True in assistant config
             Plans the incident, delegates to Junior Engineers, writes the Change Request.
             No web access. No SSH. Uses update_scratchpad / read_scratchpad / delegate_engineer_task.

          2. RESEARCH SUPERVISOR           — deep_research=True in assistant config
             Plans the research, delegates to Research Workers. No web access.

          3. RESEARCH WORKER               — is_research_worker=True in assistant config
             Browses the web, appends verified facts to the shared scratchpad.
             Web access enabled. Used exclusively by the deep research workflow.

          4. JUNIOR ENGINEER               — junior_engineer=True in assistant config
             SSHs to network devices, runs delegated command sets, appends raw evidence
             and flags to the shared scratchpad. No web access.

          5. STANDARD ASSISTANT            — no role flags set
             Normal user-facing assistant. Uses whatever is configured on the assistant record.

        Role flags are set via assistant metadata at ephemeral creation time and read here
        from the normalized assistant_config cache. Exactly one role is ever active per
        stream invocation — the conflict resolution block enforces mutual exclusivity.
        """
        # ------------------------------------------------------------------
        # Ephemeral supervisor / delegation state
        # ------------------------------------------------------------------
        self.ephemeral_supervisor_id = None
        self._scratch_pad_thread = None
        self._delegation_api_key = api_key
        redis = self.redis
        stream_key = f"stream:{run_id}"
        stop_event = self.start_cancellation_monitor(run_id)

        # Capture original assistant_id BEFORE any identity swap —
        # the swap may mutate self.assistant_id to the supervisor's ID,
        # and we need the original for cleanup / fallback.
        _original_assistant_id = assistant_id

        # --- 1. State Initialization ---
        self._current_tool_call_id = None
        self._decision_payload = None
        self._tool_queue = []
        accumulated: str = ""
        assistant_reply: str = ""
        decision_buffer: str = ""
        current_block: str | None = None
        pre_mapped_model = model

        try:
            # --- 2. Model Resolution ---
            if hasattr(self, "_get_model_map") and (mapped := self._get_model_map(model)):
                model = mapped

            self.assistant_id = assistant_id

            # Load the normalized config for the requested assistant from cache.
            # This populates self.assistant_config with metadata, flags, and settings.
            await self._ensure_config_loaded()

            # ------------------------------------------------------------------
            # 3. ROLE FLAG EXTRACTION
            # Read all role signals from the assistant's normalized config.
            # These are set via meta_data at ephemeral creation time (or via the
            # assistant record for standard assistants).
            # ------------------------------------------------------------------
            self.is_deep_research = self.assistant_config.get("deep_research", False)
            self.is_engineer = self.assistant_config.get("is_engineer", False)

            agent_mode_setting = self.assistant_config.get("agent_mode", False)
            decision_telemetry = self.assistant_config.get("decision_telemetry", False)

            # Default web_access from config — may be overridden by role resolution below
            web_access_setting = self.assistant_config.get("web_access", False)

            # Worker role flags — mutually exclusive, enforced below
            research_worker_setting = self.assistant_config.get("is_research_worker", False)

            # Extract from meta_data for dynamic ephemeral flags
            raw_meta = self.assistant_config.get("meta_data", {})
            junior_engineer_setting = (
                str(raw_meta.get("junior_engineer_calling", False)).lower() == "true"
            )

            # ------------------------------------------------------------------
            # 4. ROLE CONFLICT RESOLUTION
            # Exactly one role is active per invocation.
            # Priority: Senior Engineer > Research Supervisor > Research Worker > Junior Engineer > Standard
            # ------------------------------------------------------------------
            if self.is_engineer:
                # SENIOR ENGINEER (SUPERVISOR)
                # Plans the incident, delegates tasks, authors the Change Request.
                # Must NOT browse the web or SSH to devices.
                web_access_setting = False
                research_worker_setting = False
                junior_engineer_setting = False
                self.is_deep_research = False

            elif self.is_deep_research:
                # RESEARCH SUPERVISOR
                # Plans research, delegates tasks.
                web_access_setting = False
                research_worker_setting = False
                junior_engineer_setting = False

            elif research_worker_setting:
                # RESEARCH WORKER
                # Browses the web on behalf of the research supervisor.
                web_access_setting = True
                junior_engineer_setting = False

            elif junior_engineer_setting:
                # JUNIOR NETWORK ENGINEER
                # SSHs to devices, runs diagnostic commands, appends evidence to scratchpad.
                web_access_setting = False
                research_worker_setting = False

            # else: STANDARD ASSISTANT — all flags remain at config defaults.

            LOG.critical(
                "██████ [ROLE CONFIG] "
                "SeniorEngineer=%s | "
                "DeepResearch=%s | "
                "ResearchWorker=%s | "
                "JuniorEngineer=%s | "
                "WebAccess=%s ██████",
                self.is_engineer,
                self.is_deep_research,
                research_worker_setting,
                junior_engineer_setting,
                web_access_setting,
            )

            # ------------------------------------------------------------------
            # 5. IDENTITY SWAP (Supervisor roles only)
            # Delegates to parent class Orchestrator method. A no-op for workers.
            # ------------------------------------------------------------------
            await self._handle_role_based_identity_swap(requested_model=pre_mapped_model)

            # Pin the scratchpad to the current thread so that both the Senior
            # and any spawned Junior write to the same shared pad.
            self._scratch_pad_thread = thread_id

            # ------------------------------------------------------------------
            # 6. CONTEXT WINDOW CONSTRUCTION
            # Passes all resolved role flags into the context builder.
            # The builder injects the correct system prompt and tool definitions
            # based on which flag is active.
            # ------------------------------------------------------------------
            ctx = await self._set_up_context_window(
                assistant_id=self.assistant_id,
                thread_id=thread_id,
                trunk=True,
                force_refresh=force_refresh,
                agent_mode=agent_mode_setting,
                decision_telemetry=decision_telemetry,
                web_access=web_access_setting,
                deep_research=self.is_deep_research,
                engineer=self.is_engineer,  # ← new signal
                research_worker=research_worker_setting,
                junior_engineer=junior_engineer_setting,  # ← new signal
            )

            if not api_key:
                yield json.dumps({"type": "error", "content": "Missing API key."})
                return

            yield json.dumps({"type": "status", "status": "started", "run_id": run_id})

            client = self._get_client_instance(api_key=api_key)

            LOG.info(f"\nRAW_CTX_DUMP_QUEN:\n{json.dumps(ctx, indent=2, ensure_ascii=False)}")

            # ------------------------------------------------------------------
            # 7. THE STREAM LOOP
            # DeltaNormalizer handles Qwen/Kimi-specific tag parsing and yields
            # normalized chunks. Accumulation is handled by _handle_chunk_accumulation.
            # ------------------------------------------------------------------
            raw_stream = client.stream_chat_completion(
                messages=ctx,
                model=model,
                max_tokens=10000,
                temperature=kwargs.get("temperature", 0.6),
                stream=True,
            )

            async for chunk in DeltaNormalizer.async_iter_deltas(raw_stream, run_id):
                if stop_event.is_set():
                    break

                (
                    current_block,
                    accumulated,
                    assistant_reply,
                    decision_buffer,
                    should_skip,
                ) = self._handle_chunk_accumulation(
                    chunk,
                    current_block,
                    accumulated,
                    assistant_reply,
                    decision_buffer,
                )

                if should_skip:
                    continue

                yield json.dumps(chunk)
                await self._shunt_to_redis_stream(redis, stream_key, chunk)

            # Ensure any dangling XML tag is closed cleanly at end of stream
            if current_block:
                accumulated += f"</{current_block}>"

            # ------------------------------------------------------------------
            # 8. POST-STREAM PROCESSING
            # Kept inside the try block to ensure we finalize and persist using
            # the correct (possibly swapped) assistant_id before the finally
            # block has any opportunity to restore state.
            # ------------------------------------------------------------------

            # 8a. Extract Decision Payload from buffered XML block (if any)
            if decision_buffer:
                try:
                    self._decision_payload = json.loads(decision_buffer.strip())
                except Exception:
                    LOG.warning(f"Failed to parse decision buffer: {decision_buffer[:50]}...")

            # 8b. Extract Tool Calls from accumulated stream output
            tool_calls_batch = self.parse_and_set_function_calls(accumulated, assistant_reply)

            message_to_save = assistant_reply
            final_status = StatusEnum.completed.value

            # ------------------------------------------------------------------
            # 9. TOOL CALL ENVELOPE CONSTRUCTION
            # If the assistant emitted tool calls, build the standardised envelope
            # and flag the run as pending_action so the orchestrator picks it up.
            # ------------------------------------------------------------------
            if tool_calls_batch:
                self._tool_queue = tool_calls_batch
                final_status = StatusEnum.pending_action.value

                tool_calls_structure = []
                for tool in tool_calls_batch:
                    t_id = tool.get("id") or f"call_{uuid.uuid4().hex[:8]}"
                    tool_calls_structure.append(
                        {
                            "id": t_id,
                            "type": "function",
                            "function": {
                                "name": tool.get("name"),
                                "arguments": json.dumps(tool.get("arguments", {})),
                            },
                        }
                    )

                # Persist the structural representation, not the raw text
                message_to_save = json.dumps(tool_calls_structure)

            yield json.dumps({"type": "status", "status": "processing", "run_id": run_id})

            # ------------------------------------------------------------------
            # 10. FINALIZE & PERSIST
            # self.assistant_id is the correct ID at this point —
            # either the original assistant or the swapped supervisor ID.
            # ------------------------------------------------------------------
            if message_to_save:
                await self.finalize_conversation(
                    message_to_save, thread_id, self.assistant_id, run_id
                )

            if self.project_david_client:
                await asyncio.to_thread(
                    self.project_david_client.runs.update_run_status,
                    run_id,
                    final_status,
                )

            if not tool_calls_batch:
                yield json.dumps({"type": "status", "status": "complete", "run_id": run_id})

        except Exception as exc:
            LOG.error(f"Stream Exception: {exc}")
            yield json.dumps({"type": "error", "content": str(exc), "run_id": run_id})

        finally:
            # Always stop the cancellation monitor, regardless of outcome.
            stop_event.set()
