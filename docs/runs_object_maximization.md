# Run Object State Maximisation Backlog
> ProjectDavid — Primitives Utilisation Review  
> Scope: Excludes Network Engineering workflow

---

## Background

The `Run` DB model carries a rich set of fields that are either completely unused
or only partially wired up. The orchestrator maintains equivalent state as local
variables that evaporate at the end of each request. This backlog tracks the
opportunities to persist that state back to the run record, turning the Run object
into a live, queryable audit trail for every inference session.

---

## 1. Timestamp Fields — Completely Unused

**Fields:** `started_at`, `completed_at`, `failed_at`

**Current state:**  
Only `status` is updated via `update_run_status`. The timestamps are never set.
A run can show `completed` or `failed` with no indication of when it happened.

**What should happen:**
- `started_at` — written at the top of `process_conversation` before Turn 1 fires
- `completed_at` — written in the finalise block on clean exit
- `failed_at` — written in the exception handler on any terminal failure

**Implementation surface:** `process_conversation` in the core orchestrator.

**Effort:** Low — three one-liners calling `update_run` at the correct lifecycle points.

---

## 2. `last_error` + `incomplete_details` — Errors Evaporate

**Fields:** `last_error`, `incomplete_details`

**Current state:**  
The orchestrator catches exceptions and yields `{"type": "error", "content": str(exc)}`
to the SSE stream. The error string is never persisted. Once the stream closes, the
reason for failure is gone. `incomplete_details` is never written at all.

**What should happen:**
- `last_error` — persisted in every `except` block that currently only yields an error chunk
- `incomplete_details` — written when the run exits via the max-turns failsafe:
  `"Max turns (200) reached without terminal tool call or clean text completion"`

**Implementation surface:** `process_conversation` exception handler and the
`turn_count >= max_turns` failsafe block.

**Effort:** Low — writing to two fields at points that are already instrumented
with `LOG.error`.

---

## 3. `current_turn` — Agent Progress Is Invisible

**Field:** `current_turn`

**Current state:**  
`turn_count` is a local integer incremented on every iteration of the
`while turn_count < max_turns` loop. It is never written back to the DB.
From the outside, a long-running agentic run is a black box — there is no way
to know whether it is on Turn 1 or Turn 47.

**What should happen:**  
Write `current_turn` back to the run record at the top of each loop iteration,
immediately after the increment. This gives any monitoring surface a live view
of agent progress without polling the stream.

**Implementation surface:** Top of the `while` loop in `process_conversation`,
alongside the existing `LOG.info(f"ORCHESTRATOR ▸ Turn {turn_count} start")`.

**Effort:** Low — one async `update_run` call per turn. Consider batching with
the `started_at` write to avoid a DB round trip on Turn 1.

---

## 4. `usage` — Token Costs Are Not Tracked

**Field:** `usage`

**Current state:**  
`usage` is initialised to `{}` and never updated. For multi-turn agentic runs
spanning 10–50 inference calls across potentially expensive models, there is
no per-run token accounting at all.

**What should happen:**  
Accumulate `prompt_tokens`, `completion_tokens`, and `total_tokens` across all
turns. Write the final accumulated totals to `usage` in the finalise block.

**Implementation surface:**  
The inference client (`stream_chat_completion`) likely returns usage metadata
in the final chunk or response object. Capture it per turn, accumulate into a
local dict, and persist at `completed_at` time.

**Schema target:**
```json
{
  "prompt_tokens": 14200,
  "completion_tokens": 3800,
  "total_tokens": 18000,
  "turns": 7
}
```

**Effort:** Medium — requires verifying that usage data is accessible from the
provider client response and normalising across providers (Together, Hyperbolic).

---

## 5. `meta_data` — Massive Untapped Surface

**Field:** `meta_data` (MutableDict JSON)

**Current state:**  
Passed through from run creation and never updated during execution. For agentic
runs, all interesting runtime state lives only in local variables or the Redis
scratchpad.

**What should happen:**  
Use `meta_data` as the live runtime envelope for agent state. Updated
progressively as the run executes — not just written once at the end.

**Proposed schema for agentic runs:**
```json
{
  "agent": {
    "turn": 7,
    "phase": "tool_execution",
    "tools_called": ["read_scratchpad", "update_scratchpad", "delegate_research_task"],
    "tool_call_count": 12,
    "last_tool": "update_scratchpad",
    "last_tool_at": 1740520800
  }
}
```

**Why this matters:**  
Without this, the only way to inspect a live run is to parse the SSE stream.
With this, any monitoring surface, admin dashboard, or SDK consumer can call
`retrieve_run` and see exactly where the agent is and what it has done.

**Implementation surface:** `process_tool_calls` in `ToolRoutingMixin` —
after each tool dispatches, append the tool name to `meta_data.agent.tools_called`
and update `last_tool` and `last_tool_at`.

**Effort:** Medium — needs a safe read-modify-write pattern to avoid clobbering
existing metadata. `RunService.update_run` already does a merge, so the pattern
is available.

---

## 6. `tools` — No Post-Run Audit of What Actually Fired

**Field:** `tools` (JSON)

**Current state:**  
Populated at run creation with the assistant's `tool_configs`. Never updated.
So the field reflects what tools were *available*, not what was *called*.
There is no post-run record of which tools actually fired during the session.

**What should happen:**  
Maintain a deduplicated list of tool names that were actually invoked and
write it back to `tools` (or a separate `meta_data` key) at run completion.

**Note:** Overwriting `tools` with called-tools would break the original
intent of the field (available tool definitions). The cleaner solution is
`meta_data.agent.tools_called` as described in item 5, keeping `tools`
as the capability manifest and `meta_data` as the execution record.

**Effort:** Low once `meta_data` write-back is in place — it's a free
by-product of item 5.

---

## Priority Order (Suggested)

| # | Field(s) | Effort | Value |
|---|----------|--------|-------|
| 1 | `started_at`, `completed_at`, `failed_at` | Low | High — basic lifecycle observability |
| 2 | `last_error`, `incomplete_details` | Low | High — errors currently disappear |
| 3 | `current_turn` | Low | Medium — live agent progress visibility |
| 4 | `meta_data` (agent envelope) | Medium | High — unlocks dashboard/monitoring |
| 5 | `usage` | Medium | Medium — cost tracking per run |
| 6 | `tools` audit (via `meta_data`) | Low (after #4) | Medium — free once #4 lands |

---

## Implementation Notes

- `RunService.update_run` performs a safe dict merge on `meta_data` — use this
  for all progressive writes to avoid clobbering existing keys.
- All writes from within `process_conversation` should be `asyncio.to_thread`
  wrapped since `RunService` methods are synchronous.
- `started_at` and `current_turn` (Turn 1) can be batched into a single
  `update_run` call to avoid an extra DB round trip at run start.
- Timestamp fields (`started_at`, `completed_at`, `failed_at`) use epoch int —
  consistent with the existing `created_at` pattern.