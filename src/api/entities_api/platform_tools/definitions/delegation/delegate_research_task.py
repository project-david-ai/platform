delegate_research_task = {
    "type": "function",
    "function": {
        "name": "delegate_research_task",
        "description": (
            "Delegates a specific, narrow research question to a specialized Web Worker. "
            "Use this to find specific facts without polluting your own context window. "
            "Returns a summarized report."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "task": {
                    "type": "string",
                    "description": "The specific question to research (e.g. 'Find the 2024 revenue of NVIDIA').",
                },
                "requirements": {
                    "type": "string",
                    "description": "Any constraints (e.g. 'Must include citation links', 'Compare vs 2023').",
                },
            },
            "required": ["task"],
        },
    },
}
delegate_research_task = {
    "type": "function",
    "function": {
        "name": "delegate_research_task",
        "description": (
            "Delegates a specific, narrow research question to a specialized Web Worker. "
            "Use this to find specific facts without polluting your own context window. "
            "Returns a summarized report."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "task": {
                    "type": "string",
                    "description": "The specific question to research (e.g. 'Find the 2024 revenue of NVIDIA').",
                },
                "requirements": {
                    "type": "string",
                    "description": "Any constraints (e.g. 'Must include citation links', 'Compare vs 2023').",
                },
            },
            "required": ["task"],
        },
    },
}
# src/api/entities_api/platform_tools/definitions/network_inventory/search_inventory_by_group.py

search_inventory_by_group = {
    "type": "function",
    "function": {
        "name": "search_inventory_by_group",
        "description": (
            "Search the network inventory mental map for network_device_commands belonging to a specific group. "
            "Use this for discovery when you need to know which network_device_commands exist in a particular location, "
            "role, or category (e.g., 'core', 'edge', 'firewalls', 'all'). Returns a list of network_device_commands."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "group": {
                    "type": "string",
                    "description": "The name of the network group to search for (e.g., 'core', 'all').",
                }
            },
            "required": ["group"],
            "additionalProperties": False,
        },
    },
}
