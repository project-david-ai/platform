<pre>
+--------------------+      +----------------------------------+      +---------------------+      +---------------------+      +--------------------+
| User (Browser)     |      | Flask App (/api/messages/process)|      | ProjectDavid SDK    |      | ProjectDavid Backend|      | LLM Service        |
|                    |      | (generate_chunks, faux_handler)  |      | (client.* methods)  |      | API (/v1/...)       |      |                    |
+--------------------+      +----------------------------------+      +---------------------+      +---------------------+      +--------------------+
          |                         |                                 |                     |                     |
          | 1. POST /api/messages/process                             |                     |                     |
          |    (User message)             |                           |                     |                     |
          |----------------------------->| 2. create_message          |                     |                     |
          |                              |--------------------------->| 3. POST /messages   |                     |
          |                              |                             |------------------->| Save message         |
          |                              | 4. create_run              |                     |                     |
          |                              |--------------------------->| 5. POST /runs       |                     |
          |                              |                             |------------------->| Start run            |
          |                              | 6. Return streaming resp   |                     |                     |
          |<-----------------------------|   (generate_chunks begins) |                     |                     |
          | (SSE stream starts)          |                             |                     |                     |
          |                              | 7. sync_stream.setup       |                     |                     |
          |                              |    sync_stream.stream_chunks|                    |                     |
          |                              |--------------------------->| 8. POST /completions|                     |
          |                              |                             |------------------->| → LLM process 1      |
          |<-----------------------------|<---------------------------|<--------------------|<---------------------|<-- [LLM chunks 1]
          | (Initial assistant chunks)   |                             |                     |                     |
          |                              | 9. sync_stream.close       |                     |                     |
          |                              |                             |                     |                     |
          |                              |                             |                     | 10. run status →     |
          |                              |                             |                     |     action_required  |
          |                              | 11. poll_and_execute_action(faux_tool_handler)    |                     |
          |                              |--------------------------->| 12. SDK helper loop |                     |
          |                              |                             |<-------------------| 13. GET /runs/{id}   |
          |                              |                             |------------------->| Return: action_req   |
          |                              |                             |<-------------------| 14. GET /actions     |
          |                              |                             |------------------->| Return action info   |
          |                              |<- - - - - - - - - - - - - - | 15. faux_handler() |                     |
          |                              |   (executes tool logic)     |                     |                     |
          |                              |                             | 16. got result      |                     |
          |                              |--------------------------->| submit_tool_output |                     |
          |                              |                             |<-------------------| 17. POST /messages   |
          |                              |                             |------------------->| Save output, resume  |
          |                              |<---------------------------| 18. helper returns  |                     |
          |                              | 19. yield status: complete |                     |                     |
          |<-----------------------------|                             |                     |                     |
          | (Tool complete status)       |                             |                     |                     |
          |                              | 20. final_stream.setup     |                     |                     |
          |                              |     final_stream.stream_chunks                     |                     |
          |                              |--------------------------->| 21. POST /completions|                     |
          |                              |                             |------------------->| → LLM process 2      |
          |<-----------------------------|<---------------------------|<--------------------|<---------------------|<-- [LLM chunks 2]
          | (Final assistant chunks)     |                             |                     |                     |
          |                              | 22. final_stream.close     |                     |                     |
          |                              | 23. yield status: done     |                     |                     |
          |<-----------------------------|                             |                     |                     |
          | (Final status chunk)         |                             |                     |                     |
          |                              | 24. generator finishes     |                     |                     |
          | [SSE stream ends]            |                             |                     |                     |
</pre>
