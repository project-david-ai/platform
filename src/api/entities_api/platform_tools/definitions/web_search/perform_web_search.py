# src/api/entities_api/platform_tools/definitions/perform_web_search.py
perform_web_search = {
    "type": "function",
    "function": {
        "name": "perform_web_search",
        "description": "Performs a global web search (SERP) to find URLs when you do not have a specific link to visit. Returns a list of relevant search results (Titles + URLs). You must subsequently call 'read_web_page' on the most relevant results to get the actual content.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The natural language search query (e.g., 'latest AI news', 'who is the CEO of Anthropic', 'Python 3.12 release date').",
                },
            },
            "required": ["query"],
        },
    },
}
