# src/api/entities_api/platform_tools/definitions/delegation/delegate_engineer_task.py

delegate_engineer_task = {
    "type": "function",
    "function": {
        "name": "delegate_engineer_task",
        "description": (
            "Delegate a focused diagnostic task to a Junior Network Engineer. "
            "The Junior will SSH to the specified device, execute the given command set, "
            "analyze output against the flag criteria you provide, and append findings "
            "to the shared scratchpad. Use this for every device-level data collection task. "
            "You must resolve the hostname from inventory BEFORE calling this tool."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "hostname": {
                    "type": "string",
                    "description": (
                        "The exact hostname of the target device, already resolved from inventory."
                    ),
                },
                "commands": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Ordered list of read-only CLI commands to run on the device. "
                        "Maximum 8 commands per delegation."
                    ),
                    "minItems": 1,
                    "maxItems": 8,
                },
                "task_context": {
                    "type": "string",
                    "description": (
                        "The diagnostic hypothesis this task is testing. "
                        "Example: 'Verify BGP peer state after reported WAN packet loss on Site B uplink.'"
                    ),
                },
                "flag_criteria": {
                    "type": "string",
                    "description": (
                        "Explicit conditions the Junior must flag with 🚩 in the scratchpad. "
                        "Example: 'Flag if any BGP peer is not Established. "
                        "Flag if GigabitEthernet0/1 is down/down. "
                        "Flag if error counters exceed 1000 on any interface.'"
                    ),
                },
            },
            "required": ["hostname", "commands", "task_context", "flag_criteria"],
            "additionalProperties": False,
        },
    },
}
