### 🧠 Assistant Run Monitoring Logic

This system uses `HttpRunMonitor` to asynchronously track the lifecycle of an assistant run. It listens for status changes such as `queued`, `in_progress`, `requires_action`, and `completed`, and allows you to respond to key events like tool invocations (`action_required`).

The monitor is run in a separate background thread and does **not block** the streaming response to the client.

---

### 🔄 Run Lifecycle Monitoring Table

| **Run Status**      | **Triggered Event**  | **Handler Callback in Monitor** |
|---------------------|----------------------|----------------------------------|
| `queued`            | —                    | —                                |
| `in_progress`       | —                    | —                                |
| `requires_action`   | `action_required`    | `on_action_required`             |
| `completed`         | `complete`           | `on_complete`                    |
| `failed`            | `error`              | `on_error`                       |
| `cancelling`        | —                    | (rare; monitor may detect stall) |
| `cancelled`         | `complete`           | `on_complete`                    |

---

### 🧩 How It Works

1. **Run is created** via the `client.runs.create_run(...)` method.
2. A `MonitorLauncher` instance spawns a background thread that invokes `HttpRunMonitor.start(...)`.
3. The monitor:
   - Polls the run status periodically.
   - Emits lifecycle events like `status_changed`, `complete`, `action_required`, etc.
4. Your callbacks (`on_status_change`, `on_complete`, `on_action_required`) handle these events.
5. When `requires_action` is reached, the monitor fetches pending tool calls so you can process or simulate execution.

---

### ✅ Example Output (Logging)

```
[MonitorLauncher] Starting monitor for run run_abc123
[MONITOR STATUS] run_abc123: None → queued
[MONITOR STATUS] run_abc123: queued → in_progress
[MONITOR STATUS] run_abc123: in_progress → requires_action
[ACTION_REQUIRED] run run_abc123 has 1 pending action(s)
[ACTION] Tool: search, Args: {'query': 'Tupac'}
[MONITOR COMPLETE] run run_abc123 ended with status: completed
```
