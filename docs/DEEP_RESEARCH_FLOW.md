```

┌─────────────────────────────────────────────────────────────────────────────┐
│                          DEEP RESEARCH FLOW                                  │
└─────────────────────────────────────────────────────────────────────────────┘

USER QUERY: "Find NVIDIA's Q4 2024 revenue"
    │
    ▼
┌───────────────────────────────────────────────────────────────────────┐
│  MAIN THREAD (qwen_worker.py)                                         │
│  ┌─────────────────────────────────────────────────────────────┐     │
│  │ Check: is_deep_research = True?                             │     │
│  │   ✓ YES → Spawn SUPERVISOR Assistant                        │     │
│  │   self.assistant_id = ephemeral_supervisor.id               │     │
│  └─────────────────────────────────────────────────────────────┘     │
│         │                                                             │
│         ▼                                                             │
│  ┌─────────────────────────────────────────────────────────────┐     │
│  │ SUPERVISOR AGENT (on main thread)                           │     │
│  │ Tools: [update_scratchpad, append_scratchpad,               │     │
│  │         read_scratchpad, delegate_research_task]            │     │
│  └─────────────────────────────────────────────────────────────┘     │
│         │                                                             │
│         │ 1. Creates research plan                                   │
│         │ 2. Calls: update_scratchpad("Research Plan: ...")          │
│         │ 3. Calls: delegate_research_task(                          │
│         │           task="Find NVIDIA Q4 2024 revenue",              │
│         │           requirements="Direct link to IR or SEC")         │
│         ▼                                                             │
└───────────────────────────────────────────────────────────────────────┘
         │
         │ DELEGATION HANDOFF
         │ (handle_delegate_research_task in delegation_mixin.py)
         ▼
┌───────────────────────────────────────────────────────────────────────┐
│  EPHEMERAL WORKER ENVIRONMENT                                         │
│  ┌─────────────────────────────────────────────────────────────┐     │
│  │ 1. Create worker assistant (ephemeral_worker)               │     │
│  │ 2. Create worker thread   (ephemeral_thread)                │     │
│  │ 3. Create initial message (formatted_handoff_prompt)        │     │
│  │ 4. Create worker run      (ephemeral_run)                   │     │
│  └─────────────────────────────────────────────────────────────┘     │
│         │                                                             │
│         ▼                                                             │
│  ┌─────────────────────────────────────────────────────────────┐     │
│  │ WORKER AGENT (in isolated thread)                           │     │
│  │ Tools: [perform_web_search, read_web_page,                  │     │
│  │         scroll_web_page, search_web_page]                   │     │
│  └─────────────────────────────────────────────────────────────┘     │
│         │                                                             │
│         │ Worker executes its own tool call loop:                    │
│         │ ┌─────────────────────────────────────────────┐            │
│         │ │ 1. perform_web_search("NVIDIA Q4 2024")    │            │
│         │ │ 2. read_web_page("https://investor.nvidia...")│          │
│         │ │ 3. search_web_page(url, "revenue")          │            │
│         │ │ 4. [More tool calls as needed...]           │            │
│         │ └─────────────────────────────────────────────┘            │
│         │                                                             │
│         │ Final worker message:                                      │
│         │ "NVIDIA's fiscal Q4 2024 revenue was $26.04B..."           │
│         ▼                                                             │
│  ┌─────────────────────────────────────────────────────────────┐     │
│  │ WORKER THREAD STATE                                          │     │
│  │ messages_on_thread = [                                       │     │
│  │   {'role': 'user', 'content': '### Research Assignment...'},│     │
│  │   {'role': 'assistant', 'content': '<fc>{"name":"perform_...│     │
│  │   {'role': 'tool', 'tool_call_id': '...', 'content': '...'} │     │
│  │   {'role': 'assistant', 'content': '<fc>{"name":"read_we...│     │
│  │   {'role': 'tool', 'tool_call_id': '...', 'content': '...'} │     │
│  │   {'role': 'assistant', 'content': 'NVIDIA fiscal Q4...'}   │ ◄──┐│
│  │ ]                                                            │    ││
│  └─────────────────────────────────────────────────────────────┘    ││
└───────────────────────────────────────────────────────────────────────┘
         │                                                              │
         │ FETCH FINAL REPORT                                          │
         │ (_fetch_worker_final_report)                                │
         │                                                              │
         │ messages_on_thread[-1].get('content') ──────────────────────┘
         │ = "NVIDIA's fiscal Q4 2024 revenue was $26.04B..."
         │
         │ SUBMIT TO SUPERVISOR
         │ (submit_tool_output)
         ▼
┌───────────────────────────────────────────────────────────────────────┐
│  MAIN THREAD - SUPERVISOR RECEIVES REPORT                             │
│  ┌─────────────────────────────────────────────────────────────┐     │
│  │ Tool Response:                                               │     │
│  │ {                                                            │     │
│  │   'role': 'tool',                                            │     │
│  │   'tool_call_id': 'call_5d219fca',                           │     │
│  │   'content': 'NVIDIA fiscal Q4 2024 revenue was $26.04B...' │     │
│  │ }                                                            │     │
│  └─────────────────────────────────────────────────────────────┘     │
│         │                                                             │
│         ▼                                                             │
│  ┌─────────────────────────────────────────────────────────────┐     │
│  │ SUPERVISOR SYNTHESIZES FINAL ANSWER                          │     │
│  │ "Based on the research, NVIDIA's fiscal Q4 2024..."         │     │
│  └─────────────────────────────────────────────────────────────┘     │
│         │                                                             │
│         ▼                                                             │
│    STREAM TO USER                                                     │
└───────────────────────────────────────────────────────────────────────┘

```

### KEY ISOLATION POINTS:

1. Worker's internal tool calls (perform_web_search, read_web_page, etc.)
   stay ONLY in ephemeral_thread - NEVER bleed into main thread

2. Only the FINAL assistant message content from worker is extracted
   via messages_on_thread[-1].get('content')

3. This clean report is submitted as tool response to supervisor

4. Supervisor sees ONLY the final report, not the worker's tool execution

### THREAD CONTEXTS:

Main Thread Context (Supervisor sees):
  - User: "Find NVIDIA Q4 2024 revenue"
  - Assistant: <tool_call: delegate_research_task>
  - Tool: "NVIDIA fiscal Q4 2024 revenue was $26.04B..." ✓ CLEAN
  - Assistant: [Final synthesis to user]

Worker Thread Context (Isolated):
  - User: "### Research Assignment..."
  - Assistant: <tool_call: perform_web_search>
  - Tool: [search results]
  - Assistant: <tool_call: read_web_page>
  - Tool: [page content]
  - Assistant: "NVIDIA fiscal Q4 2024..." ← EXTRACTED