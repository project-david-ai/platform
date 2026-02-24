# Understanding Hermes Channel Tags

Hermes 3 (and GPT-OSS based models) utilizes a "channel" system to structure its output. Unlike standard LLMs that generate a single stream of text, Hermes organizes its generation into distinct modes of operation—separating **internal thought**, **tool usage**, and **final responses**.

## The Core Concept
The model outputs a continuous string, but it injects special XML-like markers to switch "channels." This allows parsers to treat "thinking" differently from "speaking."

### The Primary Channels

| Tag | Channel Name | Description |
| :--- | :--- | :--- |
| `<|channel|>analysis` | **Reasoning / CoT** | The model's internal monologue. Here it plans, analyzes step-by-step, and decides on tool usage. This is typically hidden from the end user. |
| `<|channel|>final` | **Final Answer** | The actual response intended for the user. This is the "clean" output after the thinking process is complete. |
| `<|channel|>commentary` | **Tool Meta** | Used during tool execution to describe *why* a tool is being called or to interpret the results of a tool call. |
| `<|call|>` | **Tool Call** | Marks the beginning of a function call payload (often JSON) intended for an execution environment. |
| `<|message|>` | **Delimiter** | A separator tag often used to mark the transition from the channel header to the actual text payload. |

---

## 🚀 The Flow of Execution

When a user sends a complex prompt, the raw text stream from the model typically looks like this:

### 1. Standard Reasoning Flow
```xml
<|channel|>analysis<|message|>
The user is asking for a Python function to calculate Fibonacci numbers. 
I should provide a recursive solution and an iterative one for efficiency.
I will write the code and explain it.
<|channel|>final<|message|>
Here is the Python code for the Fibonacci sequence:
def fib(n): ...
```

### 2. Tool Calling Flow
```xml
<|channel|>analysis<|message|>
The user wants to know the weather in Tokyo. I need to use the `get_weather` tool.
<|channel|>commentary<|message|>
Calling weather API for Tokyo, JP.
<|call|>
{"name": "get_weather", "arguments": {"location": "Tokyo"}}
```

---

## ⚠️ Important: Provider-Side Parsing

**Not all raw streams require manual parsing.**

Some modern inference providers (e.g., **Hyperbolic**) automatically detect these Hermes-specific tags on the server side. In these cases:

1.  **Pre-Parsing:** The API intercepts the `<|channel|>analysis` block.
2.  **Shunting:** It strips these tags from the `content` string entirely.
3.  **Structured Output:** The reasoning text is moved into a dedicated API field (e.g., `reasoning_content` in the delta object), and tool calls are populated into the standard `tool_calls` array.

**Implication:** If your provider handles this, you do **not** need to implement the state machine logic below. You can simply read from the specific keys in the JSON response, significantly simplifying client-side code and reducing latency.

---

## Manual Parsing Strategy (Fallback)

If you are running the model locally (e.g., vLLM, Ollama) or using a "raw" provider that does not parse these tags, a **State Machine** approach is required:

1.  **Default State (`content`)**: Stream text to the user.
2.  **Detection**: If a buffer detects `<|channel|>analysis`, switch state to `reasoning`.
3.  **Buffering**: While in `reasoning`, accumulate text into a specific `reasoning_content` field (do not show to user).
4.  **Transition**: When `<|channel|>final` is detected, switch state back to `content` and flush the buffer to the user's screen.