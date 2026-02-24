```text

STAGES: [ 1: ENTRY ] -> [ 2: ROUTING ] -> [ 3: FACTORY ] -> [ 4: WORKER ] -> [ 5: EXIT ]

[ STAGE 1: ENTRY GATE ] 
  File: src/api/entities_api/routers/inference_router.py
  Action: Post-request validated; Arbiter initialized.
    V
[ STAGE 2: PROVIDER SELECTOR ]
  File: entities_api/orchestration/engine/inference_provider_selector.py
  Action: "hyperbolic/" prefix matches to HyperbolicHandler.
    V
[ STAGE 3: PROVIDER FACTORY ]
  File: .../providers/hypherbolic/new_handler.py
  Action: "deepseek-r1" matches to HyperbolicDs1 worker class.
    V
[ STAGE 4: SPECIALIZED WORKER ]
  File: .../providers/hypherbolic/models.py
  Action: Worker executes token-refinement logic (<fc>, <think>).
    V
[ STAGE 5: ASYNC STREAM BUS ]
  File: src/api/entities_api/routers/inference_router.py
  Action: Sync generator tokens shunted to Async Queue -> SSE output.

```
