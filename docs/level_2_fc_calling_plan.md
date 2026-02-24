
# 🚀 Roadmap: Level 2 Reliable Tool Calling (OSS Models)

## 1. 🛡️ Robust Parsing (The "Silver Bullet")
- [ ] **Implement Regex Extraction Utility**:
  - Stop relying on clean JSON output.
  - Create `extract_json_safely(text)` to find `{...}` or ` ```json ... ``` ` patterns amidst chatty responses.
- [ ] **Context-Free Grammar / JSON Mode**:
  - Check `DeepSeekWorker` and `Entity` client configs.
  - Ensure `response_format={"type": "json_object"}` is passed to providers (Together/Hyperbolic) where supported.

## 2. 🔄 The Self-Correction Loop (Orchestrator)
- [ ] **Refactor `process_tool_calls` for Feedback**:
  - Current behavior: Crash/Log on invalid args.
  - **New behavior**: Catch `ValidationError`, format as a "System/Tool Error" message, and **yield it back** to the message history.
- [ ] **Implement `feed_error_back_to_model`**:
  - Create a method to inject the error: *"Error: Missing required argument 'departure'. Please retry."*
  - Trigger a re-run immediately after injection.

## 3. 🧠 Reasoning & Telemetry Alignment
- [ ] **Correlate Decisions**:
  - In `events_driven_unified_inference_test.py`:
  - Assert that if a `DecisionEvent` (Intent) is received, a `ToolCallRequestEvent` MUST follow within $N$ tokens.
  - Log a warning flag: `[HALLUCINATION_RISK]` if Intent exists but no Tool Call is generated.

## 4. 📝 System Prompts (OSS Optimization)
- [ ] **Update System Instructions**:
  - Move away from implicit schema definitions.
  - Explicitly inject Type Hints: *"Arguments must be valid JSON. Departure is a string."*
  - Add a One-Shot example in the prompt showing a correct tool call format for the specific model being tested.

## 5. 🧪 Testing
- [ ] **Create "Broken Tool" Benchmark**:
  - New test case: Ask the model to call a tool with ambiguous info (e.g., "Book a flight").
  - **Success Criteria**: The model asks a clarifying question OR fails arguments, receives the error, and then asks/fixes it (instead of crashing).