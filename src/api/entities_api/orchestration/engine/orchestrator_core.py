"""
orchestrator_core.py
────────────────────
The ultra-thin root class.

• Glues together every functional mix-in in the correct MRO order
• Carries only a few shared state fields
• Leaves `stream` + `process_conversation` abstract so each provider
  (Hyperbolic, OpenAI, Together, …) can implement its own wire-format.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, AsyncGenerator, Dict, List, Optional, Union

# -------------------------------------------------------------------------
# IMPORTS
# -------------------------------------------------------------------------
from projectdavid import StreamEvent
from projectdavid_common import \
    ToolValidator  # Assumed available based on snippet
from projectdavid_common.schemas.enums import StatusEnum
from projectdavid_common.utilities.logging_service import LoggingUtility

from entities_api.cache.assistant_cache import AssistantCache
from entities_api.dependencies import get_redis_sync
from entities_api.utils.assistant_manager import AssistantManager
from entities_api.utils.delegation_model_map import get_delegated_model
from src.api.entities_api.constants.assistant import PLATFORM_TOOLS
# Mixins
from src.api.entities_api.orchestration.mixins.code_execution_mixin import \
    CodeExecutionMixin
from src.api.entities_api.orchestration.mixins.consumer_tool_handlers_mixin import \
    ConsumerToolHandlersMixin
from src.api.entities_api.orchestration.mixins.conversation_context_mixin import \
    ConversationContextMixin
from src.api.entities_api.orchestration.mixins.json_utils_mixin import \
    JsonUtilsMixin
from src.api.entities_api.orchestration.mixins.platform_tool_handlers_mixin import \
    PlatformToolHandlersMixin
from src.api.entities_api.orchestration.mixins.service_registry_mixin import \
    ServiceRegistryMixin
from src.api.entities_api.orchestration.mixins.shell_execution_mixin import \
    ShellExecutionMixin
from src.api.entities_api.orchestration.mixins.streaming_mixin import \
    StreamingMixin
from src.api.entities_api.orchestration.mixins.tool_routing_mixin import \
    ToolRoutingMixin

LOG = LoggingUtility()


@dataclass
class StreamState:
    """Container for mutable state during the streaming process."""

    accumulated: str = ""
    assistant_reply: str = ""
    reasoning_reply: str = ""
    decision_buffer: str = ""
    plan_buffer: str = ""
    current_block: str | None = None


class OrchestratorCore(
    ServiceRegistryMixin,
    JsonUtilsMixin,
    ConversationContextMixin,
    ToolRoutingMixin,
    PlatformToolHandlersMixin,
    ConsumerToolHandlersMixin,
    StreamingMixin,
    CodeExecutionMixin,
    ShellExecutionMixin,
    ABC,
):

    def __init__(
        self,
        *,
        assistant_id: str | None = None,
        thread_id: str | None = None,
        redis=None,
        base_url: str | None = None,
        api_key: str | None = None,
        assistant_cache_service: Optional[AssistantCache] = None,
        **extra,
    ) -> None:

        self.is_deep_research = None

        # 1. Setup Redis (Critical for the Mixin fallback)
        self.redis = redis or get_redis_sync()

        # 2. Setup the Cache Service
        if assistant_cache_service:
            self._assistant_cache = assistant_cache_service
        elif "assistant_cache" in extra and isinstance(
            extra["assistant_cache"], AssistantCache
        ):
            self._assistant_cache = extra["assistant_cache"]

        # 3. Setup the Data/Config
        legacy_config = extra.get("assistant_config") or extra.get("assistant_cache")
        self.assistant_config: Dict[str, Any] = (
            legacy_config if isinstance(legacy_config, dict) else {}
        )

        # 4. Core State
        self._current_tool_call_id: Optional[str] = None
        self._decision_payload: Any = None

    """
    All behaviour resides in the mix-ins.
    Concrete provider classes only need to implement:
        • stream()
    """

    tool_response: Optional[bool] = None
    function_call: Optional[dict] = None
    _cancelled: bool = False
    code_mode: bool = False

    # --------------------------------------------------------------------------
    # ABSTRACT METHODS
    # --------------------------------------------------------------------------

    @abstractmethod
    def _get_client_instance(self, api_key: str):
        """Return the specific provider client."""
        pass

    @abstractmethod
    def _execute_stream_request(self, client, payload: dict) -> Any:
        """Execute the stream request (Sync or Async-to-Sync)."""
        pass

    @abstractmethod
    async def stream(
        self,
        thread_id: str,
        message_id: str,
        run_id: str,
        assistant_id: str,
        model: Any,
        *,
        force_refresh: bool = False,
        stream_reasoning: bool = False,
        api_key: Optional[str] = None,
    ) -> AsyncGenerator[str, None]:
        """
        Must open a streaming connection to the underlying LLM provider,
        parse deltas into the mix-in JSON chunk format and `yield` them.
        """
        yield ""

    # --------------------------------------------------------------------------
    # CONFIG HELPERS
    # --------------------------------------------------------------------------

    @property
    def assistant_cache(self) -> dict:
        return self._assistant_cache

    @assistant_cache.setter
    def assistant_cache(self, value: dict) -> None:
        self._assistant_cache = value

    def get_assistant_cache(self) -> dict:
        return self._assistant_cache

    async def load_assistant_config(self):
        """
        Populates self.assistant_config from Redis if not already set.
        """
        if not self.assistant_config and self.assistant_id:
            self.assistant_config = (
                await self.assistant_cache.retrieve(self.assistant_id) or {}
            )
            LOG.debug(f"Loaded config for {self.assistant_id}")

    async def _handle_role_based_identity_swap(self, requested_model: Any) -> None:
        """
        Performs the 'Hot Swap' for specialized agent loops:
        Deep Research (Supervisor -> Workers) OR Engineering (Senior -> Junior)
        """
        is_engineer_active = getattr(self, "is_engineer", False)

        # 1. ENFORCE MUTUAL EXCLUSIVITY
        if self.is_deep_research and is_engineer_active:
            LOG.error(
                "CRITICAL: Assistant configured with BOTH Deep Research and Engineer. Defaulting to Engineer."
            )
            self.is_deep_research = False  # Force exclusivity (or raise ValueError)

        # 2. Check if either mode is active
        if not self.is_deep_research and not is_engineer_active:
            return

        assistant_manager = AssistantManager()

        # ==========================================
        # PATH A: DEEP RESEARCH SWAP
        # ==========================================
        if self.is_deep_research:
            LOG.critical("██████ [DEEP_RESEARCH_MODE_ACTIVE] ██████")
            ephemeral_lead = (
                await assistant_manager.create_ephemeral_research_supervisor()
            )
            self._worker_thread = await assistant_manager.create_ephemeral_thread()

        # ==========================================
        # PATH B: ENGINEER SWAP
        # ==========================================
        elif is_engineer_active:
            LOG.critical("██████ [ENGINEER_MODE_ACTIVE] ██████")
            ephemeral_lead = await assistant_manager.create_ephemeral_senior_engineer()
            self._worker_thread = await assistant_manager.create_ephemeral_thread()

        # ==========================================
        # COMMON IDENTITY SWAP LOGIC
        # ==========================================
        self.assistant_id = ephemeral_lead.id
        self.ephemeral_supervisor_id = ephemeral_lead.id

        # Flush and Reload Configuration (Grabs the Senior/Supervisor system prompt & tools)
        self.assistant_config = {}
        await self._ensure_config_loaded()

        # Set Delegated Inference Model
        self._delegation_model = get_delegated_model(requested_model=requested_model)

    def _handle_chunk_accumulation(
        self,
        chunk: Dict[str, Any],
        current_block: str | None,
        accumulated: str,
        assistant_reply: str,
        decision_buffer: str = "",  # Added to capture raw decision JSON
    ) -> tuple[str | None, str, str, str, bool]:
        """
        Handle chunk accumulation with XML block management.
        Updates state for: content, call_arguments (fc), reasoning (think), plan, and decision.

        Returns:
            tuple: (current_block, accumulated, assistant_reply, decision_buffer, should_skip_yield)
        """
        ctype = chunk.get("type")
        ccontent = chunk.get("content") or ""
        should_skip = False

        # --- 1. CONTENT (Standard Text) ---
        if ctype == "content":
            # Close any active XML block if we switch back to content
            if current_block:
                accumulated += f"</{current_block}>"
                current_block = None

            assistant_reply += ccontent
            accumulated += ccontent

        # --- 2. TOOL ARGUMENTS (<fc>) ---
        # Normalized from Kimi's <|tool_call_argument_begin|> or Qwen's <tool_call>
        elif ctype == "call_arguments":
            if current_block != "fc":
                if current_block:
                    accumulated += f"</{current_block}>"
                accumulated += "<fc>"
                current_block = "fc"

            accumulated += ccontent
            should_skip = True  # We usually don't yield raw args to the frontend stream

        # --- 3. REASONING (<think>) ---
        elif ctype == "reasoning":
            if current_block != "think":
                if current_block:
                    accumulated += f"</{current_block}>"
                accumulated += "<think>"
                current_block = "think"

            # Note: We usually DO yield reasoning, so no accumulated += ccontent here strictly for 'assistant_reply'
            # but we do add it to 'accumulated' for history preservation.
            accumulated += ccontent

        # --- 4. PLANNING (<plan>) ---
        elif ctype == "plan":
            if current_block != "plan":
                if current_block:
                    accumulated += f"</{current_block}>"
                accumulated += "<plan>"
                current_block = "plan"

            accumulated += ccontent

        # --- 5. DECISION (<decision>) ---
        elif ctype == "decision":
            if current_block != "decision":
                if current_block:
                    accumulated += f"</{current_block}>"
                accumulated += "<decision>"
                current_block = "decision"

            accumulated += ccontent
            decision_buffer += (
                ccontent  # Capture specific buffer for JSON parsing later
            )

        return current_block, accumulated, assistant_reply, decision_buffer, should_skip

    def _update_stream_state(self, chunk: dict, state: StreamState) -> None:
        """
        Updates the accumulation buffers based on the chunk type and current XML block state.
        Encapsulates the Real-Time State Machine logic.
        """
        ctype = chunk.get("type")
        ccontent = chunk.get("content") or ""

        if ctype == "content":
            if state.current_block == "fc":
                state.accumulated += "</fc>"
            elif state.current_block == "think":
                state.accumulated += "</think>"
            elif state.current_block == "plan":
                state.accumulated += "</plan>"
            state.current_block = None
            state.assistant_reply += ccontent
            state.accumulated += ccontent

        elif ctype == "call_arguments":
            if state.current_block != "fc":
                if state.current_block == "think":
                    state.accumulated += "</think>"
                elif state.current_block == "plan":
                    state.accumulated += "</plan>"
                state.accumulated += "<fc>"
                state.current_block = "fc"
            state.accumulated += ccontent

        elif ctype == "reasoning":
            if state.current_block != "think":
                if state.current_block == "fc":
                    state.accumulated += "</fc>"
                elif state.current_block == "plan":
                    state.accumulated += "</plan>"
                state.accumulated += "<think>"
                state.current_block = "think"
            state.reasoning_reply += ccontent

        elif ctype == "plan":
            if state.current_block != "plan":
                if state.current_block == "fc":
                    state.accumulated += "</fc>"
                elif state.current_block == "think":
                    state.accumulated += "</think>"
                state.accumulated += "<plan>"
                state.current_block = "plan"
            state.plan_buffer += ccontent
            state.accumulated += ccontent

        elif ctype == "decision":
            state.decision_buffer += ccontent
            if state.current_block == "fc":
                state.accumulated += "</fc>"
            elif state.current_block == "think":
                state.accumulated += "</think>"
            elif state.current_block == "plan":
                state.accumulated += "</plan>"
            state.current_block = "decision"

    async def _ensure_config_loaded(self):
        if not self.assistant_id:
            LOG.warning("⚠️ Cannot load config: No assistant_id provided.")
            return

        try:
            cached_data = await self.assistant_cache.retrieve(self.assistant_id)
            if cached_data:
                self.assistant_config = cached_data
                LOG.info(
                    f"✅ Config Loaded for {self.assistant_id} | "
                    f"AgentMode: {self.assistant_config.get('agent_mode')}"
                )
            else:
                LOG.warning(
                    f"⚠️ Cache Miss for {self.assistant_id} — fetching from source"
                )
                # Fallback: fetch from DB and re-populate cache
                fresh_config = await self._fetch_assistant_config_from_db(
                    self.assistant_id
                )
                if fresh_config:
                    self.assistant_config = fresh_config
                    await self.assistant_cache.store(self.assistant_id, fresh_config)
                    LOG.info(f"✅ Config rehydrated from DB for {self.assistant_id}")
                else:
                    LOG.error(
                        f"❌ FATAL: No config found anywhere for {self.assistant_id}"
                    )

        except Exception as e:
            LOG.error(f"❌ Error loading assistant config: {e}")

    def _build_tool_structure(self, batch):
        """Helper to format tool calls for history."""
        structure = []
        for tool in batch:
            tool_id = tool.get("id") or f"call_{uuid.uuid4().hex[:8]}"
            structure.append(
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
        return structure

    # --------------------------------------------------------------------------
    # CORE ORCHESTRATION LOOP (Recursive Level 3)
    # --------------------------------------------------------------------------
    async def process_conversation(
        self,
        thread_id: str,
        message_id: Optional[str],
        run_id: str,
        assistant_id: str,
        model: Any,
        api_key: Optional[str] = None,
        max_turns: int = 200,
        **kwargs,
    ) -> AsyncGenerator[Union[str, StreamEvent], None]:
        """
        Level 3 Recursive Orchestrator (Batch-Aware).

        Logic Flow:
        1. Stream LLM tokens.
        2. Check for tool calls.
        3. If NO tools -> Finish.
        4. If tools -> Execute tools -> Loop back to Step 1 (Recursion).
        """

        # Capture original identity BEFORE stream() can perform any swap.
        # process_conversation owns the full identity lifecycle — stream() must
        # not restore it in its own finally block.
        _original_assistant_id = assistant_id

        await self._ensure_config_loaded()

        tool_validator = ToolValidator()
        turn_count = 0
        current_message_id = message_id

        try:
            while turn_count < max_turns:
                turn_count += 1
                LOG.info(f"ORCHESTRATOR ▸ Turn {turn_count} start [Run: {run_id}]")

                # ------------------------------------------------------------------
                # 1. RESET INTERNAL TURN STATE
                # ------------------------------------------------------------------
                self.set_tool_response_state(False)
                self.set_function_call_state(None)
                self._current_tool_call_id = None

                # ------------------------------------------------------------------
                # 2. INFERENCE TURN (STREAM MODEL OUTPUT)
                # After this loop completes, self.assistant_id may have been swapped
                # to the supervisor ID by _handle_deep_research_identity_swap().
                # ------------------------------------------------------------------
                try:
                    async for chunk in self.stream(
                        thread_id=thread_id,
                        message_id=current_message_id,
                        run_id=run_id,
                        assistant_id=assistant_id,
                        model=model,
                        force_refresh=(turn_count > 1),
                        api_key=api_key,
                        **kwargs,
                    ):
                        yield chunk

                except Exception as stream_exc:
                    LOG.error(
                        f"ORCHESTRATOR ▸ Turn {turn_count} stream failed: {stream_exc}"
                    )
                    yield json.dumps(
                        {"type": "error", "content": f"Stream failure: {stream_exc}"}
                    )
                    break

                # ------------------------------------------------------------------
                # 3. CHECK FOR TOOLS
                # ------------------------------------------------------------------
                batch: List[Dict] = self.get_function_call_state()

                if not batch:
                    LOG.info(f"ORCHESTRATOR ▸ Turn {turn_count} completed with text.")
                    break

                # ------------------------------------------------------------------
                # 4. TOOL ARGUMENT VALIDATION GATE
                # ------------------------------------------------------------------
                for call in batch:
                    tool_name = call.get("name")
                    args = call.get("arguments", {})

                    validation_event = tool_validator.validate_args(
                        tool_name=tool_name,
                        args=args,
                    )

                    if validation_event:
                        yield validation_event

                has_consumer_tool = any(
                    call.get("name") not in PLATFORM_TOOLS for call in batch
                )

                # ------------------------------------------------------------------
                # 5. TOOL EXECUTION TURN
                # self.assistant_id is read HERE — after stream() has completed its
                # identity swap — so it correctly reflects the supervisor or worker
                # ID rather than the original assistant ID.
                # ------------------------------------------------------------------
                async for chunk in self.process_tool_calls(
                    thread_id=thread_id,
                    run_id=run_id,
                    assistant_id=self.assistant_id,  # ← live post-swap identity
                    tool_call_id=self._current_tool_call_id,
                    model=model,
                    api_key=api_key,
                    decision=self._decision_payload,
                ):
                    yield chunk

                # ------------------------------------------------------------------
                # 6. RECURSION DECISION
                # ------------------------------------------------------------------
                if not has_consumer_tool:
                    LOG.info(
                        f"ORCHESTRATOR ▸ Platform batch {turn_count} complete. Looping..."
                    )
                    await asyncio.sleep(0.5)
                    current_message_id = None
                    continue

                LOG.info(f"ORCHESTRATOR ▸ Consumer tool detected. Handing over to SDK.")
                return

            # ----------------------------------------------------------------------
            # FAILSAFE — reachable only when the while condition expires naturally
            # ----------------------------------------------------------------------
            if turn_count >= max_turns:
                LOG.error("ORCHESTRATOR ▸ Max turns reached.")
                yield json.dumps(
                    {"type": "error", "content": "Maximum conversation turns exceeded"}
                )

        finally:
            # ------------------------------------------------------------------
            # IDENTITY TEARDOWN
            # Owned exclusively here so cleanup never races ahead of tool dispatch.
            # stream() is responsible ONLY for stopping its cancellation monitor.
            # ------------------------------------------------------------------
            if self.ephemeral_supervisor_id:
                await self._ephemeral_clean_up(
                    assistant_id=self.ephemeral_supervisor_id,
                    thread_id=thread_id,
                    delete_thread=False,
                )
            self.assistant_id = _original_assistant_id
            self.ephemeral_supervisor_id = None
            LOG.info(
                f"ORCHESTRATOR ▸ Identity restored to {_original_assistant_id}. "
                f"Ephemeral state cleared."
            )
