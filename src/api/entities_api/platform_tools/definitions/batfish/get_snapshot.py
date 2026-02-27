get_snapshot = {
    "type": "function",
    "function": {
        "name": "get_snapshot",
        "description": (
            "Retrieve the full metadata record for a previously created snapshot by its "
            "opaque ID. Returns status, device count, device list, timestamps, and any "
            "error messages. Useful for checking if a snapshot is active before running tools."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "snapshot_id": {
                    "type": "string",
                    "description": "The opaque snapshot ID returned by refresh_snapshot.",
                },
            },
            "required": ["snapshot_id"],
        },
    },
}
