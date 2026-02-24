```text 
================================================================================================
                                PHASE 1: THE FIRST STREAM
================================================================================================
   USER SCRIPT (main.py)              SDK (Stream Wrapper)             SERVER (API)
          |                                   |                              |
[Line 107] for event in stream_events():      |                              |
          | --------------------------------> |                              |
          |                                   | --(POST /completions)------> |
          |                                   |                              | [GptOssBaseWorker]
          |                                   |                              | -> stream()
          |                                   |                              | -> <fc> detected
          |                                   | <--(Yield Manifest)--------- | -> Yields Manifest
          |                                   |                              | -> RETURN (Server Dies)
          |                                   |                              | X
          | <--(Yield ToolCallRequestEvent)-- |                              |
          |                                   |                              |
[Line 137] tool_event = event                 |                              |
          |                                   |                              |
[Loop Ends]                                   |                              |


================================================================================================
                                PHASE 2: THE "GAP" (Local Execution)
================================================================================================
          |                                   |                              |
[Line 153] tool_event.execute(my_func)        |                              |
          | --------------------------------> | [ToolCallRequestEvent]       |
          |                                   | -> calls my_func()           |
          |                                   |    (Local Calculation)       |
          |                                   |                              |
          |                                   | -> submit_tool_output()      |
          |                                   | --(POST /submit_tool)------> | [API Endpoint]
          |                                   |                              | -> Updates DB (Action=Completed)
          |                                   |                              | -> Returns 200 OK
          | <--(Returns True)---------------- |                              |
          |                                   |                              |


================================================================================================
                                PHASE 3: THE SECOND STREAM (Explicit Restart)
================================================================================================
          |                                   |                              |
[Line 164] if tool_executed_successfully:     |                              |
          |                                   |                              |
[Line 179] for event in stream_events():      |                              |
          | (This is a NEW function call!)    |                              |
          | --------------------------------> |                              |
          |                                   | --(POST /completions)------> |
          |                                   |                              | [GptOssBaseWorker]
          |                                   |                              | -> stream(force_refresh=True)
          |                                   |                              | -> _set_up_context_window()
          |                                   |                              |    (Loads DB state from Phase 2)
          |                                   |                              | -> LLM generates answer
          |                                   | <--(Yield Content)---------- |
          | <--(Yield ContentEvent)---------- |                              |
          |                                   |                              |
[Line 200] print(payload)
```                     |                              |