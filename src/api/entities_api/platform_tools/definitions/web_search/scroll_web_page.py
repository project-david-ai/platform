scroll_web_page = {
    "type": "function",
    "function": {
        "name": "scroll_web_page",
        "description": "Retrieves a specific page chunk from a URL that has previously been visited via 'read_web_page'. Use this ONLY to read long narratives continuously. Do NOT use this to scan for keywords; use 'search_web_page' for that.",
        "parameters": {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "The URL of the website previously visited.",
                },
                "page": {
                    "type": "integer",
                    "description": "The 0-indexed page number to retrieve. (e.g., 0 is the start, 1 is the next chunk).",
                },
            },
            "required": ["url", "page"],
        },
    },
}
