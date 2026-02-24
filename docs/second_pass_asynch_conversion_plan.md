# Roadmap: Moving from "Async Purgatory" to Pure Async Performance

## Phase 1: The Networking Layer (Internal API Client)
**Target:** `project_david_client`
**Status:** Current bottleneck (Synchronous `httpx` or `requests` calls).

### Actions:
1. **Refactor Client:** Convert the internal library to use `httpx.AsyncClient`.
2. **Standardize Awaitables:** All calls like `client.runs.update_status` should become natively `awaitable`.
3. **Benefit:** This removes 70% of the `asyncio.to_thread` wrappers in your `GptOssBaseWorker` and `ConsumerToolHandlersMixin`.

---

## Phase 2: The State Layer (Cache & Redis)
**Target:** Assistant Cache & Redis state.
**Status:** Middle-tier bottleneck (Sync Redis blocking the event loop).

### Actions:
1. **Async Driver:** Use `redis.asyncio` instead of the standard `redis` python package.
2. **Non-blocking State:** Refactor `_shunt_to_redis_stream` and `get_assistant_cache` to be `async def`.
3. **Benefit:** Reduces "Micro-stutter" in the LLM stream. Tokens will flow more smoothly because the event loop isn't waiting on Redis I/O.

---

## Phase 3: The Persistence Layer (Database)
**Target:** SQLAlchemy & PostgreSQL.
**Status:** The "Final Boss" of bottlenecks.

### Actions:
1. **Async Engine:** Replace the database driver (e.g., `psycopg2`) with `asyncpg`.
2. **Session Management:** Switch from `Session` to `AsyncSession`.
3. **Query Refactor:** 
   - **No Lazy Loading:** You must explicitly join tables using `joinedload` or `selectinload`.
   - **Explicit Execution:** Use `await session.execute(stmt)` instead of `query.all()`.
4. **Benefit:** The app can handle thousands of concurrent tool-polling loops on a single CPU core.

---

## Final Comparison: Code Cleanliness

### Current "Frankenstein" Code (Sync wrapped in Async)
```python
# Boilerplate heavy, thread-unsafe if not careful
await asyncio.to_thread(
    self.project_david_client.actions.create_action,
    tool_name=tool_name,
    run_id=run_id
)