# src/api/entities_api/platform_tools/definitions/delegation/delegate_engineer_task.py

delegate_engineer_task = {
    "type": "function",
    "function": {
        "name": "delegate_engineer_task",
        "description": (
            "Delegate a Batfish Root Cause Analysis task to a Junior Network Engineer. "
            "The Junior will execute the specified Batfish RCA tools against the loaded "
            "static config snapshot, evaluate the JSON output against your flag criteria, "
            "and return a synthesized evidence summary directly to you. "
            "You must specify which Batfish tools to run — the Junior handles execution. "
            "Available tools: get_ospf_failures, get_bgp_failures, get_routing_loop_detection, "
            "get_acl_shadowing, get_undefined_references, get_unused_structures, "
            "get_logical_topology_with_mtu."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "batfish_tools": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Ordered list of Batfish RCA tool names for the Junior to execute. "
                        "Example: ['get_ospf_failures', 'get_undefined_references']"
                    ),
                    "minItems": 1,
                },
                "task_context": {
                    "type": "string",
                    "description": (
                        "The diagnostic hypothesis this task is testing. "
                        "Example: 'Investigating OSPF adjacency drop on core-sw1 — "
                        "checking for session failures and MTU mismatches.'"
                    ),
                },
                "flag_criteria": {
                    "type": "string",
                    "description": (
                        "Explicit conditions the Junior must flag with 🚩 in findings. "
                        "Example: 'Flag any OSPF session failures on core-sw1. "
                        "Flag any MTU mismatches on OSPF-enabled interfaces.'"
                    ),
                },
            },
            "required": ["batfish_tools", "task_context", "flag_criteria"],
            "additionalProperties": False,
        },
    },
}
