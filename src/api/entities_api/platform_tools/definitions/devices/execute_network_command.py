execute_network_command = {
    "type": "function",
    "function": {
        "name": "execute_network_command",
        "description": (
            "Executes CLI commands on a target network device via SSH (Internal Hand). "
            "Use this to gather live operational data, routing tables, or interface statuses. "
            "CRITICAL: Network outputs can be massive. Always use 'filter_pattern' (grep) for large outputs "
            "like 'show run' or 'show tech' to avoid overwhelming your memory."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "hostname": {
                    "type": "string",
                    "description": "The exact hostname of the target device (must exist in inventory).",
                },
                "commands": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "A list of commands to execute in sequence (e.g., ['terminal length 0', 'show ip route']).",
                },
                "filter_pattern": {
                    "type": "string",
                    "description": "Optional regex or string pattern to filter the output (acts like 'include' or 'grep'). Leave null for full output.",
                },
            },
            "required": ["hostname", "commands"],
            "additionalProperties": False,
        },
    },
}
