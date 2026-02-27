# src/api/entities_api/workers/hermes_worker.py
from __future__ import annotations

import asyncio
import json
import os
import uuid
from abc import ABC, abstractmethod
from typing import Any, AsyncGenerator, Dict, List, Optional, Union

from dotenv import load_dotenv
from projectdavid import StreamEvent
from projectdavid_common.utilities.logging_service import LoggingUtility
from projectdavid_common.validation import StatusEnum

from entities_api.cache.assistant_cache import AssistantCache
from entities_api.clients.delta_normalizer import DeltaNormalizer
# --- DEPENDENCIES ---
from src.api.entities_api.dependencies import get_redis, get_redis_sync
from src.api.entities_api.orchestration.engine.orchestrator_core import \
    OrchestratorCore
# --- MIXINS ---
from src.api.entities_api.orchestration.mixins.provider_mixins import \
    _ProviderMixins

load_dotenv()
LOG = LoggingUtility()


class HermesDefaultBaseWorker(
    _ProviderMixins,
    OrchestratorCore,
    ABC,
):
    """
    Async Base for 'deepcogito/cogito-v2-preview-llama-405B' (Hermes Type).
    Migrated to Async-First Architecture with Deep Research capabilities.
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

        # --- NEW: Engineer flow tracking variables ---
        self.is_engineer = None

        self._delete_ephemeral_thread = delete_ephemeral_thread or extra.get(
            "delete_ephemeral_thread"
        )
        self.ephemeral_supervisor_id = None
        self._delegation_api_key = self.api_key

        # --- [FIX 3] Missing Init Property ---
        self._research_worker_thread = None
        self._worker_thread = None

        # 1. Capture the 'assistant_cache' argument manually
        arg_assistant_cache_dict = extra.get("assistant_cache")

        # 2. Setup Redis
        self.redis = redis or get_redis_sync()

        # 3. Setup the Cache Service
        self._assistant_cache: AssistantCache | None = None
        if assistant_cache_service:
            self._assistant_cache = assistant_cache_service
        elif "assistant_cache" in extra and isinstance(
            extra["assistant_cache"], AssistantCache
        ):
            self._assistant_cache = extra["assistant_cache"]

        # 4. Setup Config
        legacy_config = extra.get("assistant_config") or arg_assistant_cache_dict
        self.assistant_config: Dict[str, Any] = (
            legacy_config if isinstance(legacy_config, dict) else {}
        )

        self._david_client: Any = None
        self.redis = redis or get_redis()
        self.assistant_id = assistant_id
        self.thread_id = thread_id
        self.base_url = base_url or os.getenv("BASE_URL")
        self.api_key = api_key or extra.get("api_key")

        self.model_name = extra.get(
            "model_name", "deepcogito/cogito-v2-preview-llama-405B"
        )
        self.max_context_window = extra.get("max_context_window", 128000)
        self.threshold_percentage = extra.get("threshold_percentage", 0.8)

        # Standardized tracking variables
        self._current_tool_call_id: str | None = None
        self._pending_tool_payload: Optional[Dict[str, Any]] = None
        self._decision_payload: Optional[Dict[str, Any]] = None

        self.setup_services()

        # Safety stubbing
        if not hasattr(self, "get_function_call_state"):
            LOG.error("CRITICAL: ToolRoutingMixin failed to load.")
            self.get_function_call_state = lambda: None
            self.set_function_call_state = lambda x: None
            self.set_tool_response_state = lambda x: None

        LOG.debug("DeepCogito worker initialized (assistant=%s)", assistant_id)

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
        Level 3 Agentic Stream with ID-Parity.
        Includes Deep Research Supervisor Logic & Standardized State Management.
        """

        # -----------------------------------
        # Ephemeral supervisor
        # -----------------------------------
        self.ephemeral_supervisor_id = None
        self._delegation_api_key = api_key

        # --- [FIX 1] Scratchpad Variable Initialization ---
        self._scratch_pad_thread = None

        redis = self.redis
        stream_key = f"stream:{run_id}"
        stop_event = self.start_cancellation_monitor(run_id)

        # --- [FIX] Capture original assistant_id BEFORE any identity swap ---
        _original_assistant_id = assistant_id

        # 1. State Initialization
        self._current_tool_call_id = None
        self._decision_payload = None
        self._tool_queue: List[Dict] = []

        accumulated: str = ""
        assistant_reply: str = ""
        decision_buffer: str = ""
        current_block: str | None = None

        pre_mapped_model = model
        try:
            # 2. Model Mapping
            if hasattr(self, "_get_model_map") and (
                mapped := self._get_model_map(model)
            ):
                model = mapped

            self.assistant_id = assistant_id
            await self._ensure_config_loaded()

            # --- [NEW] DEEP RESEARCH & ENGINEER LOGIC ---
            self.is_deep_research = self.assistant_config.get("deep_research", False)
            self.is_engineer = self.assistant_config.get("is_engineer", False)
            LOG.info(
                "[DEEP_RESEARCH_MODE]=%s | [ENGINEER_MODE]=%s",
                self.is_deep_research,
                self.is_engineer,
            )

            # C. Execute Identity Swap (Refactored for Generalized Roles)
            # This handles the supervisor creation, ID swapping, and config reloading
            await self._handle_role_based_identity_swap(
                requested_model=pre_mapped_model
            )

            # --- [FIX 1] Scratchpad Thread Binding ---
            self._scratch_pad_thread = thread_id

            agent_mode_setting = self.assistant_config.get("agent_mode", False)
            decision_telemetry = self.assistant_config.get("decision_telemetry", False)

            # --- [CRITICAL FIX START] ---
            # 1. Default to user preference (usually True for standard agents)
            web_access_setting = self.assistant_config.get("web_access", False)

            # 2. Extract Worker / Junior flags
            research_worker_setting = self.assistant_config.get(
                "is_research_worker", False
            )
            raw_meta = self.assistant_config.get("meta_data", {})
            junior_engineer_setting = (
                str(raw_meta.get("junior_engineer_calling", False)).lower() == "true"
            )

            # 3. CONFLICT RESOLUTION:
            if self.is_engineer:
                # SENIOR ENGINEER (SUPERVISOR)
                web_access_setting = False
                research_worker_setting = False
                junior_engineer_setting = False
                self.is_deep_research = False

            elif self.is_deep_research:
                # RESEARCH SUPERVISOR
                web_access_setting = False
                research_worker_setting = False
                junior_engineer_setting = False

            elif research_worker_setting:
                # RESEARCH WORKER
                web_access_setting = True
                junior_engineer_setting = False

            elif junior_engineer_setting:
                # JUNIOR NETWORK ENGINEER
                web_access_setting = False
                research_worker_setting = False

            # --- [FIX 2] Conflict Resolution Logging ---
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

            # 3. Context Setup
            ctx = await self._set_up_context_window(
                assistant_id=self.assistant_id,
                thread_id=thread_id,
                trunk=True,
                force_refresh=force_refresh,
                agent_mode=agent_mode_setting,
                decision_telemetry=decision_telemetry,
                web_access=web_access_setting,
                deep_research=self.is_deep_research,
                engineer=self.is_engineer,
                research_worker=research_worker_setting,
                junior_engineer=junior_engineer_setting,
            )

            if not api_key:
                yield json.dumps({"type": "error", "content": "Missing API key."})
                return

            yield json.dumps({"type": "status", "status": "started", "run_id": run_id})

            client = self._get_client_instance(api_key=api_key)
            raw_stream = client.stream_chat_completion(
                messages=ctx,
                model=model,
                max_tokens=10000,
                temperature=kwargs.get("temperature", 0.6),
                stream=True,
            )

            # 4. Stream Loop using Helper
            async for chunk in DeltaNormalizer.async_iter_deltas(raw_stream, run_id):
                if stop_event.is_set():
                    break

                # Delegate state management to the mixin helper
                (
                    current_block,
                    accumulated,
                    assistant_reply,
                    decision_buffer,
                    should_skip,
                ) = self._handle_chunk_accumulation(
                    chunk, current_block, accumulated, assistant_reply, decision_buffer
                )

                if should_skip:
                    continue

                yield json.dumps(chunk)
                await self._shunt_to_redis_stream(redis, stream_key, chunk)

            # Close any dangling blocks
            if current_block:
                accumulated += f"</{current_block}>"

            # =========================================================================
            # [FIXED] POST-STREAM PROCESSING MOVED INSIDE TRY BLOCK
            # This ensures we finalize/persist using the SUPERVISOR ID
            # before the 'finally' block restores the Original ID.
            # =========================================================================

            # 5. Post-Processing (Decision Parsing)
            if decision_buffer:
                try:
                    self._decision_payload = json.loads(decision_buffer.strip())
                except Exception:
                    LOG.warning(
                        f"Failed to parse decision buffer: {decision_buffer[:50]}..."
                    )

            yield json.dumps(
                {"type": "status", "status": "processing", "run_id": run_id}
            )

            # 6. Parse Tools & Sync IDs
            # The parser ensures every tool in the list has a 'id' key.
            tool_calls_batch = self.parse_and_set_function_calls(
                accumulated, assistant_reply
            )

            message_to_save = assistant_reply
            final_status = StatusEnum.completed.value

            if tool_calls_batch:
                # Update the internal queue for the dispatcher
                self._tool_queue = tool_calls_batch
                final_status = StatusEnum.pending_action.value

                # Build the Hermes/OpenAI Structured Envelope for the Dialogue
                tool_calls_structure = []
                for tool in tool_calls_batch:
                    tool_id = tool.get("id") or f"call_{uuid.uuid4().hex[:8]}"

                    tool_calls_structure.append(
                        {
                            "id": tool_id,
                            "type": "function",
                            "function": {
                                "name": tool.get("name"),
                                "arguments": (
                                    json.dumps(tool.get("arguments", {}))
                                    if isinstance(tool.get("arguments"), dict)
                                    else tool.get("arguments")
                                ),
                            },
                        }
                    )

                # CRITICAL: We overwrite message_to_save with the standard tool structure
                message_to_save = json.dumps(tool_calls_structure)

                LOG.info(
                    f"\n🚀 [L3 AGENT MANIFEST] Turn 1 Batch of {len(tool_calls_structure)}"
                )

            # Persistence: Assistant Plan/Actions saved to Thread
            if message_to_save:
                await self.finalize_conversation(
                    message_to_save, thread_id, self.assistant_id, run_id
                )

            # Update Run status to trigger Dispatch Turn
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

            # 2. Ephemeral Assistant Cleanup
            if self.ephemeral_supervisor_id:
                self.assistant_config = {}
                await self._ensure_config_loaded()
                await self._ephemeral_clean_up(
                    assistant_id=self.ephemeral_supervisor_id,
                    thread_id=thread_id,
                    delete_thread=False,
                )

            # --- [FIX] Restore original assistant identity AFTER cleanup & persistence ---
            self.assistant_id = _original_assistant_id

            # --- [FIX] Nullify ephemeral ID ---
            self.ephemeral_supervisor_id = None
