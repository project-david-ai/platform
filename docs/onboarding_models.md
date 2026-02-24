## Phase 1: Global Configuration & Dependency Chain

1. **Update Model Map**: Add the model identifier and its provider-specific string to `MODEL_MAP` in `projectdavid_common.constants.ai_model_map`.
   * *Example:* `"hyperbolic/meta-llama/Llama-3.3-70B": "meta-llama/Llama-3.3-70B-Instruct"`
2. **Publish Common**: Version and publish the `projectdavid_common` package.
3. **Update Core Package**: Update the `projectdavid` package to require the new `projectdavid_common` version and publish.
4. **Rebuild Environment**: 
   * Update `api_unhashed_reqs.txt` and `sandbox_reqs_unhashed.txt` with the new `projectdavid` version.
   * Rebuild the API Docker containers to ensure the `InferenceArbiter` can resolve the new model keys.

---

## Phase 2: Top-Level & Provider Routing

1. **Top-Level Selection**: Ensure the model prefix (e.g., `hyperbolic/`) is registered in `TOP_LEVEL_ROUTING_MAP` within `src/api/entities_api/orchestration/engine/inference_provider_selector.py`.
2. **Sub-model Dispatching**:
   * Open the provider's `handler.py` (e.g., `src/api/entities_api/orchestration/providers/hyperbolic/handler.py`).
   * Add an entry to `SUBMODEL_CLASS_MAP` to route the specific model ID to the correct **Worker Class**.
   * *Note:* If the inference provider is entirely new, implement a new `ProviderHandler` following the pattern in the Hyperbolic handler.

---

## Phase 3: Worker Implementation

1. **Evaluate Model Family**:
   * **Existing Family:** If the model belongs to a supported family (e.g., Llama, Qwen, DeepSeek), the existing worker should handle it. Test for streaming compatibility and function-call normalization.
   * **New Family:** If the model requires unique state-machine logic (e.g., specific XML reasoning tags), implement a new Worker Class in the provider directory.
2. **Worker Architecture**:
   * Inherit from `_ProviderMixins` and `OrchestratorCore`.
   * Define `max_context_window` and `threshold_percentage` for the `Truncator`.
   * Implement or override the `stream` generator.
   * Ensure the `DeltaNormalizer` (or equivalent) correctly yields standardized chunks (`content`, `reasoning`, `call_arguments`).

---

## Implementation Flowchart




```text

```text
STEP 1: CONFIGURATION
[ MODEL_MAP ] --------> [ Publish Common ] --------> [ Rebuild Docker ]
(Common Lib)            (Update projectdavid)        (API & Sandbox)

                               |
STEP 2: ROUTING                v
+-----------------------------------------------------------------------+
| InferenceProviderSelector: Match model prefix (e.g., "hyperbolic/")   |
+-----------------------------------------------------------------------+
            |
            +--> [ New Provider? ] --YES--> [ Create ProviderHandler.py ]
            |          |
            |          NO
            |          |
            v          v
+-----------------------------------------------------------------------+
| ProviderHandler: Match sub-model ID to Worker (SUBMODEL_CLASS_MAP)    |
+-----------------------------------------------------------------------+
            |
            +--> [ New Family? ] --YES--> [ Create Worker Class (.py) ]
            |          |                  (Inherit OrchestratorCore)
            |          NO
            |          |
            v          v
STEP 3: EXECUTION
+-----------------------------------------------------------------------+
|  Worker: Process context -> Stream -> Normalize -> Function Call      |
+-----------------------------------------------------------------------+------+
|  Worker: Process context -> Stream -> Normalize -> Function Call      |
+-----------------------------------------------------------------------+
```