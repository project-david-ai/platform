# src/api/entities_api/platform_tools/definitions/network_engineer/search_inventory_by_group.py

search_inventory_by_group = {
    "type": "function",
    "function": {
        "name": "search_inventory_by_group",
        "description": (
            "Search the network inventory mental map for devices belonging to a specific group. "
            "Use this for discovery when you need to know which devices exist in a particular location, "
            "role, or category (e.g., 'core', 'edge', 'firewalls', 'all'). Returns a list of devices."
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
