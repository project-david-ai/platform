## 🔐 Security & Privacy

**Entities is designed with sovereignty in mind.**

By default, your data and logic remain under your control. The only external services involved are **inference providers**, and only under specific, transparent conditions:

### ☁️ When External Providers Are Used

We may route requests to inference APIs (e.g., **DeepSeek**, **TogetherAI**, **Google**) if:

- A model is **proprietary** and not available for local inference.
- You explicitly choose to **leverage high-precision inference** without provisioning your own GPU infrastructure.

These are the only circumstances where **third-party services or APIs** are invoked.

---

### 🧠 When Local Inference Is Used

When configured for **local inference**, Entities is fully **self-contained**.

- No external API calls  
- No data leaves your environment  
- Embedding, vector search, tool usage, and orchestration happen on your infrastructure

This mode is ideal for **air-gapped deployments**, **regulated industries**, and **sovereign AI workloads**.

---

### 🛡️ Philosophy

> **“If it doesn't need to leave, it doesn't.”**

Entities gives you full control over **what runs where**, with architectural separation between:

- Vector stores  
- Inference  
- Tool execution  
- User data  

And we don’t just support local-first. We **prefer** it.
