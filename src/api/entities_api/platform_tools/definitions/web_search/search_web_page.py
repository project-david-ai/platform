# src/api/entities_api/platform_tools/definitions/search_web_page.py
search_web_page = {
    "type": "function",
    "function": {
        "name": "search_web_page",
        "description": "Scans ALL pages of a previously loaded URL for a specific keyword or phrase. Returns context snippets around the matches. This is much faster and saves more context than scrolling page-by-page.",
        "parameters": {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "The URL of the website previously visited.",
                },
                "query": {
                    "type": "string",
                    "description": "The specific keyword or phrase to find (e.g., 'pricing', 'contact support', 'API key').",
                },
            },
            "required": ["url", "query"],
        },
    },
}
