# src/api/entities_api/platform_tools/definitions/batfish/run_batfish_tool.py

run_batfish_tool = {
    "type": "function",
    "function": {
        "name": "run_batfish_tool",
        "description": (
            "Run a single named RCA (Root Cause Analysis) tool against the loaded Batfish snapshot. "
            "Each tool performs a specific type of network analysis using formal data-plane simulation. "
            "Use this for targeted analysis when you know exactly what to investigate. "
            "Available tools:\n"
            "  - get_device_os_inventory\n"
            "  - get_logical_topology_with_mtu\n"
            "  - get_ospf_failures\n"
            "  - get_bgp_failures\n"
            "  - get_undefined_references\n"
            "  - get_unused_structures\n"
            "  - get_acl_shadowing\n"
            "  - get_routing_loop_detection"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "batfish_tool_name": {
                    "type": "string",
                    "description": "The name of the RCA tool to run.",
                    "enum": [
                        "get_device_os_inventory",
                        "get_logical_topology_with_mtu",
                        "get_ospf_failures",
                        "get_bgp_failures",
                        "get_undefined_references",
                        "get_unused_structures",
                        "get_acl_shadowing",
                        "get_routing_loop_detection",
                    ],
                },
            },
            "required": ["batfish_tool_name"],
        },
    },
}
