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
