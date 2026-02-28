# src/api/entities_api/orchestration/mixins/tool_routing_mixin.py

from __future__ import annotations

import asyncio
import json
import re
import uuid
from typing import AsyncGenerator, Dict, List, Optional, Union

from src.api.entities_api.constants.assistant import PLATFORM_TOOLS
from src.api.entities_api.constants.platform import SPECIAL_CASE_TOOL_HANDLING
from src.api.entities_api.services.logging_service import LoggingUtility

LOG = LoggingUtility()


class ToolRoutingMixin:
    """
    High-level routing of tool-calls.
    Level 3 Refactor: Supports batch extraction, plan isolation,
    and ID propagation for parallel self-correction.
    """

    FC_REGEX = re.compile(r"<fc>\s*(?P<payload>\{.*?\})\s*</fc>", re.DOTALL | re.I)

    _tool_response: bool = False
    _function_calls: List[Dict] = []
    _tools_called: List[str] = []  # Full ordered log of every tool dispatched this run

    # -----------------------------------------------------
    # State Management
    # -----------------------------------------------------
    def set_tool_response_state(self, value: bool) -> None:
        LOG.debug("TOOL-ROUTER ▸ set_tool_response_state(%s)", value)
        self._tool_response = value

    def get_tool_response_state(self) -> bool:
        return self._tool_response

    def set_function_call_state(
        self, value: Optional[Union[Dict, List[Dict]]] = None
    ) -> None:
        if value is None:
            self._function_calls = []
        elif isinstance(value, dict):
            self._function_calls = [value]
        else:
            self._function_calls = value

    def get_function_call_state(self) -> List[Dict]:
        return self._function_calls

    def reset_tools_called(self) -> None:
        """Reset the tools called log at the start of each turn."""
        self._tools_called = []

    def get_tools_called(self) -> List[str]:
        """
        Returns the full ordered log of every tool dispatched this run,
        including duplicates. Parallel tool batches are captured in
        dispatch order within each batch.
        """
        return list(self._tools_called)

    # -----------------------------------------------------
    # Helper Methods
    # -----------------------------------------------------
    def _normalize_arguments(self, payload: Dict) -> Dict:
        args = payload.get("arguments")
        if isinstance(args, str):
            try:
                clean_args = args.strip()
                if clean_args.startswith("```"):
                    clean_args = clean_args.strip("`").replace("json", "").strip()
                payload["arguments"] = json.loads(clean_args)
            except Exception:
                LOG.warning("TOOL-ROUTER ▸ failed to parse string arguments")
        return payload

    # -----------------------------------------------------
    # The Pedantic L3 Parser
    # -----------------------------------------------------
    def parse_and_set_function_calls(
        self, accumulated_content: str, assistant_reply: str
    ) -> List[Dict]:
        from src.api.entities_api.orchestration.mixins.json_utils_mixin import \
            JsonUtilsMixin

        if not isinstance(self, JsonUtilsMixin):
            raise TypeError("ToolRoutingMixin must be mixed with JsonUtilsMixin")

        body_to_scan = re.sub(
            r"<plan>.*?</plan>", "", accumulated_content, flags=re.DOTALL
        )

        matches = self.FC_REGEX.finditer(body_to_scan)
        results = []

        for m in matches:
            raw_payload = m.group("payload")
            parsed = self.ensure_valid_json(raw_payload)
            if parsed:
                if not parsed.get("id"):
                    parsed["id"] = f"call_{uuid.uuid4().hex[:8]}"
                normalized = self._normalize_arguments(parsed)
                results.append(normalized)

        if results:
            LOG.info(f"L3-PARSER ▸ Detected batch of {len(results)} tool(s).")
            self.set_tool_response_state(True)
            self.set_function_call_state(results)
            return results

        loose = self.extract_function_calls_within_body_of_text(assistant_reply)
        if loose:
            normalized_list = []
            for l in loose:
                if not l.get("id"):
                    l["id"] = f"call_{uuid.uuid4().hex[:8]}"
                normalized_list.append(self._normalize_arguments(l))

            self.set_tool_response_state(True)
            self.set_function_call_state(normalized_list)
            return normalized_list

        LOG.debug("L3-PARSER ✗ nothing found")
        return []

    # -----------------------------------------------------
    # Tool Processing & Dispatching (Level 3 Batch Enabled)
    # -----------------------------------------------------
    async def process_tool_calls(
        self,
        thread_id: str,
        run_id: str,
        assistant_id: str,
        tool_call_id: Optional[str] = None,
        *,
        model: str | None = None,
        api_key: str | None = None,
        decision: Optional[Dict] = None,
    ) -> AsyncGenerator:
        """
        Orchestrates the execution of a detected batch of tool calls.
        Level 3: Iterates through the batch, propagating IDs for history linking.
        """

        # CONFIRMED  - this works
        owner_id = self._batfish_owner_user_id

        LOG.info("TOOL-ROUTER ▸ The Real OwnerID: %s", owner_id)
        # Arguments: ('user_BG5JyzwSLb4dVfDqzJoH8u',)

        batch = self.get_function_call_state()
        if not batch:
            return

        LOG.info("TOOL-ROUTER ▸ Dispatching Turn Batch (%s total)", len(batch))

        for fc in batch:
            name = fc.get("name")
            args = fc.get("arguments")

            current_call_id = tool_call_id or fc.get("id")

            if not name and decision:
                inferred_name = (
                    decision.get("tool")
                    or decision.get("function")
                    or decision.get("name")
                )
                if inferred_name:
                    name = inferred_name
                    if args is None:
                        args = fc

            if not name:
                LOG.error(
                    "TOOL-ROUTER ▸ Failed to resolve tool name for item in batch."
                )
                continue

            LOG.info("TOOL-ROUTER ▶ dispatching: %s (ID: %s)", name, current_call_id)
            self._tools_called.append(name)  # ← capture every dispatch in order

            # ---------------------------------------------------------
            # 1. PLATFORM TOOLS (Explicit Routing)
            # ---------------------------------------------------------
            if name == "code_interpreter":
                async for chunk in self.handle_code_interpreter_action(
                    thread_id=thread_id,
                    run_id=run_id,
                    assistant_id=assistant_id,
                    arguments_dict=args,
                    tool_call_id=current_call_id,
                    decision=decision,
                ):
                    yield chunk

            elif name == "computer":
                async for chunk in self.handle_shell_action(
                    thread_id=thread_id,
                    run_id=run_id,
                    assistant_id=assistant_id,
                    arguments_dict=args,
                    tool_call_id=current_call_id,
                    decision=decision,
                ):
                    yield chunk

            elif name == "file_search":
                async for chunk in self.handle_file_search(
                    thread_id=thread_id,
                    run_id=run_id,
                    assistant_id=assistant_id,
                    arguments_dict=args,
                    tool_call_id=current_call_id,
                    decision=decision,
                ):
                    yield chunk

            elif name == "read_web_page":
                async for chunk in self.handle_read_web_page(
                    thread_id=thread_id,
                    run_id=run_id,
                    assistant_id=assistant_id,
                    arguments_dict=args,
                    tool_call_id=current_call_id,
                    decision=decision,
                ):
                    yield chunk

            elif name == "scroll_web_page":
                async for chunk in self.handle_scroll_web_page(
                    thread_id=thread_id,
                    run_id=run_id,
                    assistant_id=assistant_id,
                    arguments_dict=args,
                    tool_call_id=current_call_id,
                    decision=decision,
                ):
                    yield chunk

            elif name == "search_web_page":
                async for chunk in self.handle_search_web_page(
                    thread_id=thread_id,
                    run_id=run_id,
                    assistant_id=assistant_id,
                    arguments_dict=args,
                    tool_call_id=current_call_id,
                    decision=decision,
                ):
                    yield chunk

            elif name == "perform_web_search":
                async for chunk in self.handle_perform_web_search(
                    thread_id=thread_id,
                    run_id=run_id,
                    assistant_id=assistant_id,
                    arguments_dict=args,
                    tool_call_id=current_call_id,
                    decision=decision,
                ):
                    yield chunk

            # ---------------------------------------------------------
            # DELEGATION / DEEP RESEARCH / MEMORY TOOLS
            # ---------------------------------------------------------
            elif name == "delegate_research_task":
                async for chunk in self.handle_delegate_research_task(
                    thread_id=thread_id,
                    run_id=run_id,
                    assistant_id=assistant_id,
                    arguments_dict=args,
                    tool_call_id=current_call_id,
                    decision=decision,
                ):
                    yield chunk

            elif name == "delegate_engineer_task":
                async for chunk in self.handle_delegate_engineer_task(
                    thread_id=thread_id,
                    run_id=run_id,
                    assistant_id=assistant_id,
                    arguments_dict=args,
                    tool_call_id=current_call_id,
                    decision=decision,
                ):
                    yield chunk

            elif name == "read_scratchpad":
                async for chunk in self.handle_read_scratchpad(
                    thread_id=thread_id,
                    run_id=run_id,
                    assistant_id=assistant_id,
                    arguments_dict=args,
                    tool_call_id=current_call_id,
                    decision=decision,
                ):
                    yield chunk

            elif name == "update_scratchpad":
                async for chunk in self.handle_update_scratchpad(
                    thread_id=thread_id,
                    run_id=run_id,
                    assistant_id=assistant_id,
                    arguments_dict=args,
                    tool_call_id=current_call_id,
                    decision=decision,
                ):
                    yield chunk

            elif name == "append_scratchpad":
                async for chunk in self.handle_append_scratchpad(
                    thread_id=thread_id,
                    run_id=run_id,
                    assistant_id=assistant_id,
                    arguments_dict=args,
                    tool_call_id=current_call_id,
                    decision=decision,
                ):
                    yield chunk

            # ---------------------------------------------------------
            # NETWORK INVENTORY TOOLS
            # ---------------------------------------------------------
            elif name == "search_inventory_by_group":
                async for chunk in self.handle_search_inventory_by_group(
                    thread_id=thread_id,
                    run_id=run_id,
                    assistant_id=assistant_id,
                    arguments_dict=args,
                    tool_call_id=current_call_id,
                    decision=decision,
                    user_id=self._run_user_id,
                ):
                    yield chunk

            elif name == "get_device_info":
                async for chunk in self.handle_get_device_info(
                    thread_id=thread_id,
                    run_id=run_id,
                    assistant_id=assistant_id,
                    arguments_dict=args,
                    tool_call_id=current_call_id,
                    decision=decision,
                    user_id=self._run_user_id,
                ):
                    yield chunk

                # ---------------------------------------------------------
                # BATFISH ANALYSIS TOOLS (Junior Engineer Direct Dispatch)
                # Each tool maps to a dedicated handler that uses the
                # Senior's batfish_owner_user_id for snapshot scope.
                # ---------------------------------------------------------
            elif name == "get_device_os_inventory":
                LOG.info(
                    "TOOL-ROUTER ▶ [BATFISH] Routing get_device_os_inventory (user=%s)",
                    self._batfish_owner_user_id,
                )
                async for chunk in self.handle_get_device_os_inventory(
                    thread_id=thread_id,
                    run_id=run_id,
                    assistant_id=assistant_id,
                    arguments_dict=args,
                    tool_call_id=current_call_id,
                    decision=decision,
                    user_id=self._batfish_owner_user_id,
                ):
                    yield chunk

            elif name == "get_logical_topology_with_mtu":
                LOG.info(
                    "TOOL-ROUTER ▶ [BATFISH] Routing get_logical_topology_with_mtu (user=%s)",
                    self._batfish_owner_user_id,
                )
                async for chunk in self.handle_get_logical_topology_with_mtu(
                    thread_id=thread_id,
                    run_id=run_id,
                    assistant_id=assistant_id,
                    arguments_dict=args,
                    tool_call_id=current_call_id,
                    decision=decision,
                    user_id=self._batfish_owner_user_id,
                ):
                    yield chunk

            elif name == "get_ospf_failures":
                LOG.info(
                    "TOOL-ROUTER ▶ [BATFISH] Routing get_ospf_failures (user=%s)",
                    self._batfish_owner_user_id,
                )
                async for chunk in self.handle_get_ospf_failures(
                    thread_id=thread_id,
                    run_id=run_id,
                    assistant_id=assistant_id,
                    arguments_dict=args,
                    tool_call_id=current_call_id,
                    decision=decision,
                    user_id=self._batfish_owner_user_id,
                ):
                    yield chunk

            elif name == "get_bgp_failures":
                LOG.info(
                    "TOOL-ROUTER ▶ [BATFISH] Routing get_bgp_failures (user=%s)",
                    self._batfish_owner_user_id,
                )
                async for chunk in self.handle_get_bgp_failures(
                    thread_id=thread_id,
                    run_id=run_id,
                    assistant_id=assistant_id,
                    arguments_dict=args,
                    tool_call_id=current_call_id,
                    decision=decision,
                    user_id=self._batfish_owner_user_id,
                ):
                    yield chunk

            elif name == "get_undefined_references":
                LOG.info(
                    "TOOL-ROUTER ▶ [BATFISH] Routing get_undefined_references (user=%s)",
                    self._batfish_owner_user_id,
                )
                async for chunk in self.handle_get_undefined_references(
                    thread_id=thread_id,
                    run_id=run_id,
                    assistant_id=assistant_id,
                    arguments_dict=args,
                    tool_call_id=current_call_id,
                    decision=decision,
                    user_id=self._batfish_owner_user_id,
                ):
                    yield chunk

            elif name == "get_unused_structures":
                LOG.info(
                    "TOOL-ROUTER ▶ [BATFISH] Routing get_unused_structures (user=%s)",
                    self._batfish_owner_user_id,
                )
                async for chunk in self.handle_get_unused_structures(
                    thread_id=thread_id,
                    run_id=run_id,
                    assistant_id=assistant_id,
                    arguments_dict=args,
                    tool_call_id=current_call_id,
                    decision=decision,
                    user_id=self._batfish_owner_user_id,
                ):
                    yield chunk

            elif name == "get_acl_shadowing":
                LOG.info(
                    "TOOL-ROUTER ▶ [BATFISH] Routing get_acl_shadowing (user=%s)",
                    self._batfish_owner_user_id,
                )
                async for chunk in self.handle_get_acl_shadowing(
                    thread_id=thread_id,
                    run_id=run_id,
                    assistant_id=assistant_id,
                    arguments_dict=args,
                    tool_call_id=current_call_id,
                    decision=decision,
                    user_id=self._batfish_owner_user_id,
                ):
                    yield chunk

            elif name == "get_routing_loop_detection":
                LOG.info(
                    "TOOL-ROUTER ▶ [BATFISH] Routing get_routing_loop_detection (user=%s)",
                    self._batfish_owner_user_id,
                )
                async for chunk in self.handle_get_routing_loop_detection(
                    thread_id=thread_id,
                    run_id=run_id,
                    assistant_id=assistant_id,
                    arguments_dict=args,
                    tool_call_id=current_call_id,
                    decision=decision,
                    user_id=self._batfish_owner_user_id,
                ):
                    yield chunk

            # ---------------------------------------------------------
            # 3. CONSUMER TOOLS (Handover to SDK)
            # ---------------------------------------------------------
            else:
                async for chunk in self._process_tool_calls(
                    thread_id=thread_id,
                    assistant_id=assistant_id,
                    content=fc,
                    run_id=run_id,
                    tool_call_id=current_call_id,
                    api_key=api_key,
                    decision=decision,
                ):
                    yield chunk

        LOG.info("TOOL-ROUTER ▸ Batch dispatch Turn complete.")
