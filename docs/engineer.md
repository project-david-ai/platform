# 🏗️ PROJECT ENGINEER: High-Fidelity Network Autonomous Agent
### Architecture Design Document v1.0

## 1. Executive Summary
"Engineer" is a CCIE-level autonomous agent designed for network diagnostics and remediation. Unlike traditional automation, it decouples the **data collection** from the **cognitive analysis**.

It utilizes a **"Store & Slice" architecture**, dumping massive raw network outputs (via Netmiko) into a temporary Redis cache, allowing the LLM to query specific patterns ("grep") rather than ingesting megabytes of log data. It operates with two distinct hands: an **Internal Hand** (SSH to devices) and an **External Hand** (Local container for triangulation).

---

## 2. System Architecture Diagram

```ascii
                                  +------------------+
                                  |   USER / UI      |
                                  | (Observer View)  |
                                  +--------+---------+
                                           |
       +-----------------------------------|-----------------------------------+
       | ORCHESTRATOR CORE                 |                                   |
       |                                   v                                   |
       |  +--------------------+    +------------+      +-------------------+  |
       |  |  AGENCY BRAIN      |<---| Controller |----->|  SIDECAR SHELL    |  |
       |  | (LLM + Context)    |    | (Python)   |      | ("External Hand") |  |
       |  +---------+----------+    +------+-----+      |  (Ping/Trace/Dig) |  |
       |            ^                      |            +-------------------+  |
       |            | (2. Grep)            | (1. Dispatch)                     |
       |            |                      v                                   |
       |  +---------+----------+    +------------+                             |
       |  |   REDIS CACHE      |<---|  NETMIKO   |      (SSH / Telnet)         |
       |  | ("Store & Slice")  |    |  WORKER    |---------------------------->|  TARGET
       |  +--------------------+    +-----+------+                             |  DEVICE
       |                                  |                                    |
       +----------------------------------|------------------------------------+
                                          |
                                   (Live Stream)
                                          v
                                  +----------------+
                                  | WEBSOCKET PIPE |
                                  | (To User UI)   |
                                  +----------------+