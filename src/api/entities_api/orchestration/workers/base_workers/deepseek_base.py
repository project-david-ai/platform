# src/api/entities_api/workers/deepseek_worker.py
from __future__ import annotations

import asyncio
import json
import os
from abc import ABC, abstractmethod
from typing import Any, AsyncGenerator, Dict, List, Optional, Union

from dotenv import load_dotenv
from projectdavid import StreamEvent
from projectdavid_common.utilities.logging_service import LoggingUtility
from projectdavid_common.validation import StatusEnum

from entities_api.cache.assistant_cache import AssistantCache
from entities_api.clients.delta_normalizer import DeltaNormalizer

# --- [FIX 1] ADDED MISSING IMPORT ---
# --- DEPENDENCIES ---
from src.api.entities_api.dependencies import get_redis, get_redis_sync
from src.api.entities_api.orchestration.engine.orchestrator_core import (
    OrchestratorCore,
    StreamState,
)

# --- MIXINS ---
from src.api.entities_api.orchestration.mixins.provider_mixins import _ProviderMixins

load_dotenv()
LOG = LoggingUtility()


class DeepSeekBaseWorker(
    _ProviderMixins,
    OrchestratorCore,
    ABC,
):
    """
    Async Base for DeepSeek Providers.
    Migrated to Async-First Architecture.
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

        self.api_key = api_key or extra.get("api_key")
        self.is_deep_research = None
        self._delete_ephemeral_thread = delete_ephemeral_thread or extra.get(
            "delete_ephemeral_thread"
        )
        self.ephemeral_supervisor_id = None
        self._delegation_api_key = self.api_key

        # --- [FIX 3] Missing Init Property ---
        self._research_worker_thread = None

        self.redis = redis or get_redis_sync()

        # 3. Setup the Cache Service (The "New Way")
        if assistant_cache_service:
            self._assistant_cache = assistant_cache_service
        elif "assistant_cache" in extra and isinstance(
            extra["assistant_cache"], AssistantCache
        ):
            # Handle case where it might be passed via **extra
            self._assistant_cache = extra["assistant_cache"]

        # 4. Setup the Data/Config (The "Old Way" renamed)
        legacy_config = extra.get("assistant_config") or extra.get("assistant_cache")
        self.assistant_config: Dict[str, Any] = (
            legacy_config if isinstance(legacy_config, dict) else {}
        )

        self._david_client: Any = None
        self.redis = redis or get_redis()
        self.assistant_id = assistant_id
        self.thread_id = thread_id
        self.base_url = base_url or os.getenv("BASE_URL")
        self.api_key = api_key or extra.get("api_key")

        self.model_name = extra.get("model_name", "deepseek-ai/DeepSeek-V3")
        self.max_context_window = extra.get("max_context_window", 128000)
        self.threshold_percentage = extra.get("threshold_percentage", 0.8)

        self._current_tool_call_id: str | None = None
        self._pending_tool_payload: Optional[Dict[str, Any]] = None
        self._decision_payload: Optional[Dict[str, Any]] = None

        self.setup_services()

        # Ensure mixin stubs exist if failed to load (Standardized from GptOss)
        if not hasattr(self, "get_function_call_state"):
            LOG.error("CRITICAL: ToolRoutingMixin failed to load.")
            self.get_function_call_state = lambda: None
            self.set_function_call_state = lambda x: None
            self.set_tool_response_state = lambda x: None

        LOG.debug("Hyperbolic-Ds1 provider ready (assistant=%s)", assistant_id)

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
        Level 3 Agentic Stream (Native Mode):
        - Uses raw XML/Tag persistence to prevent Llama/DeepSeek persona breakage.
        - Maintains internal batching for parallel tool execution.
        """

        self._delegation_api_key = api_key
        self.ephemeral_supervisor_id = None

        # --- [FIX 1] Scratchpad Variable Initialization ---
        self._scratch_pad_thread = None

        redis = self.redis
        stream_key = f"stream:{run_id}"
        stop_event = self.start_cancellation_monitor(run_id)

        # --- [FIX] Capture original assistant_id BEFORE any identity swap ---
        _original_assistant_id = assistant_id

        # Early Variable Initialization
        self._current_tool_call_id = None
        self._decision_payload = None
        self._tool_queue: List[Dict] = []

        # Initialize the State Machine Container
        state = StreamState()

        pre_mapped_model = model
        try:
            if hasattr(self, "_get_model_map") and (
                mapped := self._get_model_map(model)
            ):
                model = mapped

            # Ensure cache is hot before starting
            self.assistant_id = assistant_id
            await self._ensure_config_loaded()

            # --- DEEP RESEARCH INTEGRATION ---
            self.is_deep_research = self.assistant_config.get("deep_research", False)
            LOG.info("[DEEP_RESEARCH_MODE]=%s", self.is_deep_research)

            # C. Execute Identity Swap (Refactored)
            # This handles the supervisor creation, ID swapping, and config reloading
            await self._handle_deep_research_identity_swap(
                requested_model=pre_mapped_model
            )

            # --- [FIX 1] Scratchpad Thread Binding ---
            self._scratch_pad_thread = thread_id

            # ---------------------------------

            agent_mode_setting = self.assistant_config.get("agent_mode", False)
            decision_telemetry = self.assistant_config.get("decision_telemetry", False)

            # --- [CRITICAL FIX START] ---
            # 1. Default to user preference (usually True for standard agents)
            web_access_setting = self.assistant_config.get("web_access", False)

            # 2. Check if this is a research worker
            research_worker_setting = self.assistant_config.get(
                "is_research_worker", False
            )

            # 3. CONFLICT RESOLUTION:
            if self.is_deep_research:
                web_access_setting = False  # Supervisor creates plans, does not browse
                research_worker_setting = False  # Supervisor is NOT a worker

            # 4. WORKER LOGIC (Only if NOT a Supervisor):
            elif research_worker_setting:
                web_access_setting = True

            # --- [FIX 2] Research Worker Conflict Resolution (Added missing consolidated log,
            # ensuring NO redundant fetches override the conflict resolution block above!) ---
            LOG.critical(
                "██████ [ROLE CONFIG] DeepResearch (Supervisor)=%s | Worker=%s | WebAccess=%s ██████",
                self.is_deep_research,
                research_worker_setting,
                web_access_setting,
            )

            # Updated to use self.assistant_id (handles identity swap) and pass deep_research flag
            ctx = await self._set_up_context_window(
                assistant_id=self.assistant_id,
                thread_id=thread_id,
                trunk=True,
                force_refresh=force_refresh,
                agent_mode=agent_mode_setting,
                decision_telemetry=decision_telemetry,
                web_access=web_access_setting,
                deep_research=self.is_deep_research,
                research_worker=research_worker_setting,
            )

            if not api_key:
                yield json.dumps({"type": "error", "content": "Missing API key."})
                return

            yield json.dumps({"type": "status", "status": "started", "run_id": run_id})

            client = self._get_client_instance(api_key=api_key)

            # --- [DEBUG] RAW CONTEXT DUMP ---
            LOG.info(
                f"\nRAW_CTX_DUMP:\n{json.dumps(ctx, indent=2, ensure_ascii=False)}"
            )

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

                # --- REAL-TIME STATE MACHINE UPDATE ---
                self._update_stream_state(chunk, state)

                # Handle Control Flow
                ctype = chunk.get("type")
                if ctype == "call_arguments":
                    continue

                yield json.dumps(chunk)
                await self._shunt_to_redis_stream(redis, stream_key, chunk)

            # Cleanup open tags
            if state.current_block == "fc":
                state.accumulated += "</fc>"
            elif state.current_block == "think":
                state.accumulated += "</think>"
            elif state.current_block == "plan":
                state.accumulated += "</plan>"

            # =========================================================================
            # [FIXED] POST-STREAM PROCESSING MOVED INSIDE TRY BLOCK
            # This ensures we finalize/persist using the SUPERVISOR ID
            # before the 'finally' block restores the Original ID.
            # =========================================================================

            # --- POST-STREAM: BATCH VALIDATION ---
            if state.decision_buffer:
                try:
                    self._decision_payload = json.loads(state.decision_buffer.strip())
                except Exception:
                    pass

            yield json.dumps(
                {"type": "status", "status": "processing", "run_id": run_id}
            )

            # --- [LEVEL 3] NATIVE PERSISTENCE (PRESERVED AS REQUESTED) ---
            # The parser finds the tools to drive the backend (Action records).
            tool_calls_batch = self.parse_and_set_function_calls(
                state.accumulated, state.assistant_reply
            )

            # [ORIGINAL LOGIC]: Saving the RAW text (state.accumulated)
            message_to_save = state.accumulated
            final_status = StatusEnum.completed.value

            if tool_calls_batch:
                # We still keep the tool_queue so the dispatcher knows what to execute
                self._tool_queue = tool_calls_batch
                final_status = StatusEnum.pending_action.value

                # [LOGGING]
                LOG.info(
                    f"🚀 [L3 NATIVE MODE] Turn 1 Batch size: {len(tool_calls_batch)}"
                )

            # Persistence: Save the raw <plan> and <fc> text exactly as Llama intended
            if message_to_save:
                # [FIX]: Use self.assistant_id to save under the supervisor's ID (if applicable)
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
                yield json.dumps(
                    {"type": "status", "status": "complete", "run_id": run_id}
                )

        except Exception as exc:
            LOG.error(f"DEBUG: Stream Exception: {exc}")
            err = {"type": "error", "content": f"Stream error: {exc}", "run_id": run_id}
            yield json.dumps(err)
            await self._shunt_to_redis_stream(redis, stream_key, err)

        finally:
            # 1. Ensure cancellation monitor is stopped
            stop_event.set()

            # --- [FIX] Ephemeral cleanup runs FIRST ---
            if self.ephemeral_supervisor_id:
                self.assistant_config = {}
                await self._ensure_config_loaded()
                # We use the helper method we wrote earlier, ensuring 'await' is used
                await self._ephemeral_clean_up(
                    assistant_id=self.ephemeral_supervisor_id,
                    thread_id=thread_id,
                    delete_thread=False,
                )

            # --- [FIX] Restore original assistant identity AFTER cleanup & persistence ---
            self.assistant_id = _original_assistant_id

            # --- [FIX] Nullify ephemeral ID ---
            self.ephemeral_supervisor_id = None
