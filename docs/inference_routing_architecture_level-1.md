```text
[ INFERENCE ARBITER ] <--- (Owner of Redis & AssistantCache)
                         |
      ___________________|____________________________________________
     |                   |                                            |
[ GROQ FACTORY ]  [ HYPERBOLIC FACTORY ]  [ OLLAMA / TOGETHER / GOOGLE ]
                         |
                         | (1) Arbiter injects shared state into Factory
                         |
                { REGIONAL SUB-ROUTER }
                |----------------------|
                | Matches "deepseek-"  |----(2) Resolves Worker Class
                | Matches "qwen/"      |
                |______________________|
                         |
                         | (3) arbiter.get_provider_instance(WorkerClass)
                         |
          _______________V________________________
         |         PERSISTENT WORKER              |
         |----------------------------------------|
         | Managed via LRU Cache (maxsize=32)     |
         | Stays "warm" in memory for faster hits |
         |________________________________________|
```
