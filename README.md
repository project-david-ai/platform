# Entities

[![License: PolyForm Noncommercial](https://img.shields.io/badge/license-PolyForm%20Noncommercial%201.0.0-blue.svg)](https://polyformproject.org/licenses/noncommercial/1.0.0/)
[![Docker Pulls](https://img.shields.io/docker/pulls/thanosprime/entities-api-api?label=API%20Pulls&logo=docker&style=flat-square)](https://hub.docker.com/r/thanosprime/entities-api-api)
[![Docker Image Version](https://img.shields.io/docker/v/thanosprime/entities-api-api?sort=semver&label=API%20Version&style=flat-square)](https://hub.docker.com/r/thanosprime/entities-api-api/tags)
[![CI](https://github.com/frankie336/entities_api/actions/workflows/ci.yml/badge.svg?branch=master)](https://github.com/frankie336/entities_api/actions/workflows/ci.yml)

![Entities Emblem](https://raw.githubusercontent.com/frankie336/entities_api/master/assets/entities_emblem_green.png)
---


---

## Build Quick Start

You can  start all API infrastructure and dependencies in three easy steps:

1  Build the docker containers in your development or production machine  
2. Run the basic admin bootstrap script to provision your admin credentials 
3. Run the bootstrap script to provision your first consumer user


[**Please follow the detailed steps here.**](https://github.com/frankie336/entities_api/blob/master/docs/boot_strap_guide.md)

## Inferece Quick Start 

The Entites API use cases an entpoints are exnesive. That
being said, please find a basic inference quick start example here: 

[**Inference Quick Start**](https://github.com/frankie336/projectdavid/#quick-Start)

---

The **Entities API** is for developing projects that interact with LLMs.  
It aggregates inference calls to multiple providers as well as local using the [Ollama](https://github.com/ollama) library.

This enables rapid and flexible deployment of advanced features such as conversation management,  
[function calling](/docs/function_calling.md), [code interpretation](/docs/code_interpretation.md), and more through easy-to-use API endpoints.


###  Universal Tool Use.  
Tool calling works the same across all models, all vendors, and all threads.  
We’re not shouting about it. It just works.

This enables rapid and flexible deployment of advanced features such as conversation management,  
[function calling](/docs/function_calling.md), [code interpretation](/docs/code_interpretation.md), and more through easy-to-use API endpoints.

###  Tools

From the model’s point of view: **they’re functions**.  
From your point of view: **they’re tools**.  
Entities bridges both  automatically.

---

##  Vector Memory, Reimagined

Most LLM stacks treat vector search as a bolt-on.  
Entities treats it as **first-class memory.**

We don’t just support RAG — we orchestrate it:

-   **Native Integration**  
  Built-in, not wrapped. Zero abstraction. Fully optimized.

-   **File-to-Vector Pipeline**  
  Drop in `.pdf`, `.csv`, `.txt`, or `.docx` — chunked, embedded, and stored instantly.

-   **Real Search, Not Toy Examples**  
  Fast, accurate similarity search with rich metadata and filtering support.

-   **Persistent & Private**  
  Your vector store lives on your terms — no hidden quotas or deletions.

-   **Per-Assistant or Per-User Memory**  
  Attach stores to assistants or keep them user-scoped — all via API or SDK.

> OpenAI charges `$0.10 / GB / day` + `$2.50 per 1,000 vector tool calls`.  
> **Entities gives you full vector memory control — at near-zero cost.**



---

##   Supported Models

> These are the primary models supported or targeted in Entities.  
> Availability may depend on provider integration status.


<details>
<summary><strong>📖 View Full Model Support List</strong></summary>

<!-- ✅ Supported Models -->
![DeepSeek-V3](https://img.shields.io/badge/DeepSeek--V3-✅%20Ready-brightgreen)
![DeepSeek-R1](https://img.shields.io/badge/DeepSeek--R1-✅%20Ready-brightgreen)
![DeepSeek-V3-0324](https://img.shields.io/badge/DeepSeek--V3--0324-✅%20Ready-brightgreen)
![DeepSeek-R1-Distill-Qwen-1.5B](https://img.shields.io/badge/DeepSeek--R1--Distill--Qwen--1.5B-✅%20Ready-brightgreen)
![DeepSeek-R1-Distill-Qwen-14B](https://img.shields.io/badge/DeepSeek--R1--Distill--Qwen--14B-✅%20Ready-brightgreen)
![DeepSeek-R1-Distill-Llama-70B-free](https://img.shields.io/badge/DeepSeek--R1--Distill--Llama--70B--free-✅%20Ready-brightgreen)

![LLaMA-3.3](https://img.shields.io/badge/LLaMA--3.3-✅%20Ready-brightgreen)
![LLaMA-3.3-Turbo](https://img.shields.io/badge/LLaMA--3.3--Turbo-✅%20Ready-brightgreen)
![LLaMA-3.2](https://img.shields.io/badge/LLaMA--3.2-✅%20Ready-brightgreen)
![Meta-LLaMA-3.1-70B](https://img.shields.io/badge/Meta--LLaMA--3.1--70B-✅%20Ready-brightgreen)
![Meta-LLaMA-3.1-405B](https://img.shields.io/badge/Meta--LLaMA--3.1--405B-✅%20Ready-brightgreen)
![Meta-LLaMA-3.1-8B](https://img.shields.io/badge/Meta--LLaMA--3.1--8B-✅%20Ready-brightgreen)
![Meta-LLaMA-3-70B](https://img.shields.io/badge/Meta--LLaMA--3--70B-✅%20Ready-brightgreen)
![LLaMA-3.2-11B-Vision-Turbo](https://img.shields.io/badge/LLaMA--3.2--11B--Vision--Turbo-✅%20Ready-brightgreen)
![LLaMA-3.2-90B-Vision-Turbo](https://img.shields.io/badge/LLaMA--3.2--90B--Vision--Turbo-✅%20Ready-brightgreen)
![LLaMA-Vision-Free](https://img.shields.io/badge/LLaMA--Vision--Free-✅%20Ready-brightgreen)
![LLaMA-Guard-2-8B](https://img.shields.io/badge/LLaMA--Guard--2--8B-✅%20Ready-brightgreen)

![Qwen-QwQ-32B](https://img.shields.io/badge/Qwen--QwQ--32B-✅%20Ready-brightgreen)
![Qwen-QwQ-32B-Preview](https://img.shields.io/badge/Qwen--QwQ--32B--Preview-✅%20Ready-brightgreen)
![Qwen2.5-72B-Instruct](https://img.shields.io/badge/Qwen2.5--72B--Instruct-✅%20Ready-brightgreen)
![Qwen2.5-Coder-32B](https://img.shields.io/badge/Qwen2.5--Coder--32B-✅%20Ready-brightgreen)
![Qwen2-VL-72B-Instruct](https://img.shields.io/badge/Qwen2--VL--72B--Instruct-✅%20Ready-brightgreen)

![Gemma-2-9B-IT](https://img.shields.io/badge/Gemma--2--9B--IT-✅%20Ready-brightgreen)

![Mistral-7B-V0.2](https://img.shields.io/badge/Mistral--7B--V0.2-✅%20Ready-brightgreen)
![Mistral-7B-V0.3](https://img.shields.io/badge/Mistral--7B--V0.3-✅%20Ready-brightgreen)

<!-- 🟡 In Progress Models -->
![Gemini-1.5-Pro](https://img.shields.io/badge/Gemini--1.5--Pro-🟡%20In%20Progress-yellow)
![Gemini-1.5-Flash](https://img.shields.io/badge/Gemini--1.5--Flash-🟡%20In%20Progress-yellow)
![Gemini-2.0-Pro](https://img.shields.io/badge/Gemini--2.0--Pro-🟡%20In%20Progress-yellow)
![Gemini-2.0-Flash](https://img.shields.io/badge/Gemini--2.0--Flash-🟡%20In%20Progress-yellow)
![Gemini-2.5-Pro](https://img.shields.io/badge/Gemini--2.5--Pro-🟡%20In%20Progress-yellow)

<!--  Experimental / Beta / Pending -->
![LearnLM-1.5-Pro](https://img.shields.io/badge/LearnLM--1.5--Pro-⚠️%20Experimental-orange)
![GPT-4](https://img.shields.io/badge/GPT--4-⚠️%20Beta-orange)
![Claude-3](https://img.shields.io/badge/Claude--3-❌%20Pending-lightgrey)

</details>

---

##  Inference Providers

> The following vendors are integrated or under development for use within the Entities API platform.  
> Providers may serve multiple models; model support varies by integration phase.

<!-- Repurposed column: shows that Entities can successfully handle function / tool calls for that provider -->

| Provider | Tool‑call Ready |
|----------|----------------|
| ![Hyperbolic](https://img.shields.io/badge/Hyperbolic-✅%20Integrated-brightgreen) | ![Tools OK](https://img.shields.io/badge/Tool%20Calls-✅%20Via%20Entities-brightgreen) |
| ![Together AI](https://img.shields.io/badge/Together%20AI-✅%20Integrated-brightgreen) | ![Tools OK](https://img.shields.io/badge/Tool%20Calls-✅%20Via%20Entities-brightgreen) |
| ![DeepSeek](https://img.shields.io/badge/DeepSeek-✅%20Integrated-brightgreen) | ![Tools OK](https://img.shields.io/badge/Tool%20Calls-✅%20Via%20Entities-brightgreen) |
| ![Ollama (Local)](https://img.shields.io/badge/Ollama%20(Local)-🟡%20In%20Progress-yellow) | ![Tools OK](https://img.shields.io/badge/Tool%20Calls-✅%20Via%20Entities-brightgreen) |


| Badge         | Meaning                            |
|---------------|------------------------------------|
| ✅ Integrated  | Fully integrated, streaming-ready  |
| 🟡 In Progress | In development or partially working |
| 🔲 Planned     | On roadmap, not yet implemented     |


---

![Network Diagram](assets/docker_containers.png)

---

# Table of Contents

<details>
<summary><strong> Table of Contents</strong></summary>

- [Supported Models](#-supported-models)
- [Supported Inference Providers](#-supported-inference-providers)
- [Entities vs LangChain](#-entities-vs-langchain-and-friends)
- [Why Entities](#-why-entities)
- [Bootstrap Setup](#-bootstrap-setup)
- [Docker Lifecycle](#-docker-lifecycle-commands)
- [Security Model](#-security-model)
- [State Management](#-state-management)
- [Entities V1 Cookbook](#-entities-v1-cookbook)
- [Docker Orchestration](#-entities-docker-orchestration)
- [Documentation Index](#-documentation-index)
- [Local Dev](#-local-dev)
- [API Explorer](#api-explorer)
- [License](#-license)

</details>



---


##  Entities  Cookbook

Looking for real examples and hands-on recipes?

The [Entities  Cookbook](https://github.com/frankie336/entities_cook_book) is a companion project containing:

- Standalone scripts for function calling, inference, file handling
- RAG demos and vector search examples
- Streaming and code interpretation patterns
- Local + remote inference setup walkthroughs

>  It's where the *how-to* lives — fork it, clone it, and run it today.

  [Visit the Cookbook](https://github.com/frankie336/entities_cook_book)


---

##  Entities Docker Orchestration

Looking to run the full system in a containerized environment?

The [Entities Docker Orchestration Repository](https://github.com/frankie336/entities) contains:

- Full Docker Compose stack (API, Sandbox, MySQL, Qdrant, Samba)
- Secure file-sharing and Firejail sandboxing
- Local `.env` scaffolding with secret generation
- Smart CLI tooling (`start.py`) for lifecycle commands
- Optional GPU-ready Ollama container for local LLMs

>  Built for rapid deployment in both development and production.️

  [Visit the Orchestration Repo](https://github.com/frankie336/entities)

---


## Entities vs. LangChain (and Friends)

| Feature                          | **Entities**                                    | LangChain / Others                     |
|----------------------------------|-------------------------------------------------|----------------------------------------|
| **Design Philosophy**            | Systems-level, composable, and user-controlled  | Framework-heavy, opinionated, abstract |
| **Interface Style**              | Explicit class-based SDK + REST API             | Chained declarative syntax             |
| **Vector Store Logic**           | Custom embeddings + Qdrant via HTTPx            | Plug-and-play vector wrappers          |
| **Tool Use & AI Calls**          | Native function calling + structured streaming  | Wrapper-based toolchains               |
| **Security Model**               | Firejail sandbox, subprocess isolation          | None / minimal                         |
| **Licensing Philosophy**         | Open-use, revenue-share model                   | Varies (often restrictive)             |
| **Docker Architecture**          | DevOps-ready, containerized, bootstrap-aware    | Rarely production-oriented             |
| **Local LLM Support**            | Ollama integration (opt-in)                     | Often cloud-dependent                  |
| **Buzzword Compliant**           | ❌ No agents, chains, or gimmicks               | ✅ All the latest acronyms             |

---

##   Why Entities?

> Entities is a **developer-native**, **security-conscious**, and **deeply composable AI framework** built for:
>
> - People who want to **own their stack**
> - Teams building **real-world intelligent assistants**
> - Engineers who prefer **systems control over chained magic**
> - Architects working **inside secure, disconnected, or trust-minimized environments**

You don’t “add a tool to a chain.”  
You **register tools, trigger runs, stream thoughts, and command vector memory.**

> Whether you're building in the open or operating behind the firewall —  
> **Entities is engineered for autonomy, auditability, and absolute control.**

A quiet revolution for those who’ve dreamed of an **OpenAI-class Assistants platform**,  
but **on their own terms**, **at near-zero cost**, and deployable against **any LLM model** — cloud or local.

This is **AI orchestration for the rest of us.**

---

##  Bootstrap Setup

For a full admin setup and assistant provisioning guide, see:

  [`docs/bootstrap.md`](https://github.com/frankie336/entities/blob/master/docs/boot_strap_guide.md)

This includes:
- Admin user creation
- Initial API key generation
- Regular user creation
- Default assistant setup

These scripts can be run directly in development (e.g., `python scripts/bootstrap_admin.py`)  
or invoked inside containers using the orchestration tools.

---
##   Docker Lifecycle Commands

Entities provides a unified orchestration script for building, running, and tearing down your container environment.

  [`docs/docker_commands.md`](https://github.com/frankie336/entities/blob/master/docs/docker_commands.md)

---

## Supported Inference Providers

| Provider                                        | Type                        |
|------------------------------------------------|-----------------------------|
| [Ollama](https://github.com/ollama)             | **Local** (Self-Hosted)     |
| [DeepSeek](https://platform.deepseek.com/)      | **Cloud** (Open-Source)     |
| [Hyperbolic](https://hyperbolic.xyz/)           | **Cloud** (Proprietary)     |
| [OpenAI](https://platform.openai.com/)          | **Cloud** (Proprietary)     |
| [together.ai](https://www.together.ai/)         | **Cloud** (Aggregated)      |
| [MS Azure Foundry](https://azure.microsoft.com) | **Cloud** (Enterprise)      |

---

##   Security Model

Entities places security at the forefront, employing:

- **Firejail Sandboxing**: Limits system access during code interpretation and local execution.
- **Subprocess Isolation**: Ensures code executions cannot interfere with core API logic, providing robust protection in multi-user environments.

---

##   State Management

Entities simplifies dialogue management with the [Threads](/docs/threads.md) endpoint.

```json
[
  {"role": "system", "content": "You are a helpful assistant."},
  {"role": "user", "content": "What’s the capital of France?"},
  {"role": "assistant", "content": "The capital of France is Paris."},
  {"role": "user", "content": "What’s the population of Paris?"},
  {"role": "assistant", "content": "Approximately 2.1 million."}
]
```

---

![Workflow](assets/quik_start-work_flow.png)

---

##   Documentation Index

### Core Concepts

- [Assistants](https://github.com/frankie336/projectdavid/blob/master/docs/assistants.md)  
- [Threads](https://github.com/frankie336/projectdavid/blob/master/docs/threads.md)  
- [Messages](https://github.com/frankie336/projectdavid/blob/master/docs/messages.md)  
- [Runs](https://github.com/frankie336/projectdavid/blob/master/docs/runs.md)  
- [Inference](https://github.com/frankie336/projectdavid/blob/master/docs/inference.md)  
- [Streaming](https://github.com/frankie336/projectdavid/blob/master/docs/streams.md)  

### Internals

- [Routes](/docs/routes.md)  
- [Security](/docs/security.md)  
- [Integration Status](/docs/model_integration_status.md)  
- [Files](/docs/files.md)  
- [Vector Store](https://github.com/frankie336/projectdavid/blob/master/docs/vector_store.md)  
- [Database](/docs/database.md)  
- [Tools](https://github.com/frankie336/projectdavid/blob/master/docs/tools.md)  
- [Users](/docs/users.md)

### Advanced Features

- [Function Calling](https://github.com/frankie336/projectdavid/blob/master/docs/function_calling.md)  
- [Code Interpretation](https://github.com/frankie336/projectdavid/blob/master/docs/code_interpretation.md)  

---

##   Local Dev

To run setup scripts directly during local development:

```bash
# Bootstrap admin user with defaults
python scripts/bootstrap_admin.py

# Create regular user
python scripts/create_user.py --name "Bob" --email "bob@example.com"

# Setup assistant (using admin API key + user ID)
python scripts/bootstrap_default_assistant.py
```

Install the SDK:

```bash
pip install projectdavid
```

Then follow usage docs at:  
  [`https://github.com/frankie336/projectdavid`](https://github.com/frankie336/projectdavid)

---

## API Explorer

- [`/altredoc`](http://your-domain/altredoc/)
- [`/mydocs`](http://your-domain/mydocs#/)

---

##   License

Distributed under the [PolyForm Noncommercial License 1.0.0](https://polyformproject.org/licenses/noncommercial/1.0.0/).  
Commercial licensing available upon request.
