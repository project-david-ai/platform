# 🧭 Handler Naming Convention Guide

This document defines the standardized naming conventions for **Entities Inference Handlers**, ensuring clarity, extensibility, and alignment with provider/model identifiers.

---

## ✅ General Naming Rule

Use the format:

```
<provider>_<model_family>_<version>.py
```

Where:
- `<provider>` = inference backend (e.g. `google`, `hyperbolic`, `togetherai`)
- `<model_family>` = LLM family or project (e.g. `llama`, `deepseek`, `gemini`)
- `<version>` = version string normalized for Python compatibility (see below)

---

## 🔢 Versioning Normalization

When handling models with dot or dash versions (e.g. `3.3`, `1.5-pro`, `002`), **normalize using underscores**:

| Raw Model Identifier | Normalized File Name |
|----------------------|----------------------|
| `llama3.3` | `hyperbolic_llama_3_3.py` |
| `Meta-Llama-3.1-8B-Instruct` | `hyperbolic_llama_3_1_8b_instruct.py` |
| `gemini-1.5-pro-002` | `google_gemini_1_5_pro_002.py` |
| `DeepSeek-V3-0324` | `hyperbolic_deepseek_v3_0324.py` |

---

## 🧠 Class Naming Inside Files

Use PascalCase matching the file's meaning:

```python
# Inside hb_llama.py
class HyperbolicLlama33Handler(BaseInferenceHandler):
    ...
```

Or

```python
class Hyperbolic_Llama_3_3(BaseInferenceHandler):
    ...
```

Stick with one naming form across all providers.

---

## 🛠 Utility: Normalize Model IDs

Use this function to standardize identifiers:

```python
def normalize_model_id(model_id: str) -> str:
    return model_id.lower().replace("-", "_").replace(".", "_").replace("/", "_")
```

Example:

```python
normalize_model_id("meta-llama/Meta-Llama-3.3-70B-Instruct")
# → "meta_llama_meta_llama_3_3_70b_instruct"
```

---

## 🔍 Benefits

- ✅ Predictable file locations
- ✅ Easily grep/searchable
- ✅ Compatible with import statements
- ✅ Resilient to provider/model growth
