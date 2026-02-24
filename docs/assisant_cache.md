```
[ INFERENCE REQUEST ]
                                              │
                                              ▼
+-----------------------------------------------------------------------------------------+
|                                  INFERENCE ARBITER                                      |
|  (Singleton / Gatekeeper)                                                               |
|                                                                                         |
|   +--------------------------+          +-------------------------------------------+   |
|   |  Provider LRU Cache      |          | System Message Builder                    |   |
|   | (Holds LLM Clients)      |<-------- | (Uses Cache to get Instructions + Tools)  |   |
|   +------------+-------------+          +---------------------+---------------------+   |
|                |                                              |                         |
+----------------|----------------------------------------------|-------------------------+
                 │                                              │
                 ▼                                              ▼
+-----------------------------------------------------------------------------------------+
|                                    ASSISTANT CACHE                                      |
|  (Wrapper for Redis + Fallback Logic)                                                   |
|                                                                                         |
|   1. Check RAM/Redis first.                                                             |
|   2. If MISS -> Call Internal API (ProjectDavid SDK).                                   |
|   3. Normalize Pydantic models -> Dicts.                                                |
|   4. Write to Redis (TTL: 300s).                                                        |
+----------------+----------------------------------------------+-------------------------+
                 │                                              │
        (Hit)    │                                     (Miss)   │
      +----------+-----------+                        +---------+-----------+
      │                      │                        │                     │
      ▼                      ▼                        ▼                     ▼
+-----------+          +-----------+          +---------------+      +--------------+
| AsyncRedis|   OR     | SyncRedis |          | ProjectDavid  |----->|  Internal    |
|  Client   |          |  Client   |          |      SDK      |      |     DB       |
+-----------+          +-----------+          +---------------+      +--------------+
      │                      │
      └─────────┬────────────┘
                ▼
         [ REDIS STORE ]
   Key: "assistant:{id}:config"
   Val: {
     "instructions": "...",
     "tools": [...]
   }