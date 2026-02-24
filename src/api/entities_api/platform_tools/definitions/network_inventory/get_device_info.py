# src/api/entities_api/platform_tools/definitions/network_inventory/search_inventory_by_group.py

get_device_info = {
    "type": "function",
    "function": {
        "name": "get_device_info",
        "description": (
            "Retrieve detailed information and metadata for a specific network device from the inventory map. "
            "Use this when you already know the exact hostname of the device and need its connection details, "
            "platform, IP address, or other specific configuration data."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "hostname": {
                    "type": "string",
                    "description": "The exact hostname of the network device to look up (e.g., 'sw1.datacenter.local').",
                }
            },
            "required": ["hostname"],
            "additionalProperties": False,
        },
    },
}
