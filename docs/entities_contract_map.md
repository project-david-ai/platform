# Entities System Contract Map

This document outlines the enforced contracts across all layers of the Entities system. These contracts define structure, behavior, and reliability guarantees for developers, integrators, and infrastructure.

---

## 1. API Layer Contracts

| Area | Contract Description | Enforced By |
|------|----------------------|-------------|
| POST /api/messages/process | Requires structured input (`content`, `thread_id`, `assistant_id`, `api_key`). Returns SSE JSON stream or error. | StreamRequest Pydantic model + FastAPI validation |
| JWT Auth | Requires bearer token or API key with valid HMAC if accessing file endpoints. | Depends(auth_guard) + middleware |
| Signed URL Handling | Requires exact HMAC signature and file ID, fails on mismatch. | get_signed_url() + download_file() |
| File Upload & Metadata | Files uploaded must generate metadata, return proper MIME headers. | FileService, FileProcessor, signed URL system |

---

## 2. Tooling / Function Calling Contracts

| Tool System | Contract Description | Enforced By |
|-------------|----------------------|-------------|
| Tool Invocation | tool_name, function_args, run_id required. Returns JSON-compatible output. | parse_and_set_function_calls, ActionService.create_action() |
| Tool Stream Lifecycle | Intermediate reasoning + function call + output must stream in order. | stream_response_hyperbolic(), stream_response_llama3() |
| Code Interpreter | Must stream hot_code chunks, detect entry via parse_code_interpreter_partial(). | CodeInterpreterMixin |
| Tool Output Handling | Must finalize run, update status, optionally update assistant reply. | RunService.update_run_status, parse_and_set_function_calls() |

---

## 3. Inference & Provider Routing Contracts

| Component | Contract | Enforced By |
|-----------|----------|-------------|
| InferenceProviderSelector.select_provider(model_id) | Must return (provider_instance, resolved_model_id) or raise ValueError | MODEL_MAP, sorted prefix match logic |
| InferenceArbiter.get_provider_instance(...) | Must return cached singleton or instantiate via LRU | InferenceArbiter, @lru_cache, _provider_cache |
| Provider Class | Must implement .stream(...) and .process_conversation(...) with unified return types | StreamingInterface (abstract base class) |
| SDK-Based Providers | Must handle failures gracefully (timeouts, API key errors, base URL absence) | stream(), try/except, yield json.dumps({"type": "error", ...}) |
| All Streams | Must emit structured chunks ({"type": "content"} etc.) | stream_response_hyperbolic, SynchronousInferenceStream |

---

## 4. Vector Store Contracts

| Action | Contract Description | Enforced By |
|--------|----------------------|-------------|
| File Indexing | Must convert PDF/CSV/docx/txt to embeddings + metadata. | FileProcessor, QdrantVectorClient |
| Search | Must return ranked results with associated file_id, segment, score. | VectorStoreService.search(), SearchResult schema |
| Shared ID Sync | collection_name must map to unique shared_id. | IdentifierService.generate_vector_id() |
| Multi-Store Separation | User files, assistant memory, and web search have separate stores. | Pilot Vector, User Content Vector, Scrub Space design |

---

## 5. SDK / Dev UX Contracts

| Interface | Contract | Enforced By |
|-----------|----------|-------------|
| SDK Client (Entities) | Must expose .assistants, .messages, .runs, etc., all lazily initialized. | Entities.__getattr__() dynamic routing |
| SDK Stream | Must emit JSON chunks via generator with structured types. | stream_inference_response() |
| SDK Tool Execution | Must allow on_action_required, on_complete, on_error callbacks. | RunMonitorClient |
| SDK Auth | Must attach API key to all outbound requests. | httpx client headers, Entities.__init__(api_key) |

---

## 6. Contracts Yet to Be Fully Formalized

| Contract | Status |
|----------|--------|
| Role-based API key scopes (read:vector, write:files) | ✅ Designed, not enforced |
| Unified Error Codes | ❌ Not yet standardized across API + stream |
| Streaming Replay Support | 🕓 In planning — would require persisted stream logs |
| Prompt Injection Auditing | 🔒 Optional; could track per-run inputs + outputs |
| Throttling / Quota Enforcement | 🧱 Not enforced, can be implemented via API key metadata |
