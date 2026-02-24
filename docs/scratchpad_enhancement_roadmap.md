# Scratchpad Enhancement Roadmap
**Future Architecture Improvements for Deep Research System**

---

## Executive Summary

The current scratchpad implementation (`update_scratchpad`, `append_scratchpad`, `read_scratchpad`) is functionally present but **underutilized** in the single-delegation architecture. This document outlines potential enhancements that would unlock significant value from the scratchpad system.

**Current State**: Scratchpad tools exist but provide minimal value in single-run supervisor model  
**Future Vision**: Scratchpad becomes a persistent knowledge base and cache layer  
**Priority**: Low (ship current architecture first, revisit when user demand emerges)

---

## 🎯 Use Cases That Would Justify Scratchpad

### 1. **Multi-Turn Research Sessions**

**Scenario**: User has a complex research question that requires multiple iterations

```
Turn 1:
User: "Compare NVIDIA vs AMD revenue growth 2020-2024"
Supervisor: 
  ✓ Delegates to worker for NVIDIA data
  ✓ Stores findings in scratchpad
  ✓ Returns to user: "NVIDIA grew 459%"
  
Turn 2:
User: "What about AMD?"
Supervisor:
  ✓ Reads scratchpad (already has NVIDIA data)
  ✓ Delegates only for AMD data
  ✓ Synthesizes comparison using cached NVIDIA + new AMD data
  
Turn 3:
User: "Add Intel to the comparison"
Supervisor:
  ✓ Reads scratchpad (has NVIDIA + AMD)
  ✓ Delegates only for Intel
  ✓ Three-way comparison without re-researching
```

**Value Proposition**:
- Reduces redundant API calls
- Faster response times for follow-ups
- Context accumulation over conversation
- Better user experience for complex research

---

### 2. **Scratchpad as Research Cache**

**Implementation Pattern**:

```python
async def handle_research_query(user_query, thread_id):
    # Step 1: Check if we already have this data
    scratchpad = await read_scratchpad(thread_id)
    
    # Step 2: Parse what's already known
    if "NVIDIA 2024 revenue: $60.9B" in scratchpad:
        LOG.info("Cache hit! Using existing research.")
        return extract_from_scratchpad(scratchpad, entity="NVIDIA")
    
    # Step 3: Only delegate for missing data
    else:
        LOG.info("Cache miss. Delegating new research.")
        result = await delegate_research_task(...)
        await append_scratchpad(f"NVIDIA 2024 revenue: {result}")
        return result
```

**Benefits**:
- Prevents duplicate web searches
- Saves worker compute cycles
- Reduces cost (fewer tool calls)
- Enables "progressive enhancement" of knowledge

---

### 3. **Persistent Supervisor Across Conversation**

**Current Architecture** (Ephemeral):
```python
# Each run creates a new supervisor
if is_deep_research:
    ephemeral_supervisor = await create_ephemeral_research_supervisor()
    self.assistant_id = ephemeral_supervisor.id
# Supervisor dies after run completes
```

**Proposed Architecture** (Persistent):
```python
# One supervisor per thread, persists across turns
async def get_or_create_thread_supervisor(thread_id):
    supervisor_id = await redis.get(f"supervisor:{thread_id}")
    
    if not supervisor_id:
        supervisor = await create_supervisor_assistant()
        await redis.set(f"supervisor:{thread_id}", supervisor.id)
        return supervisor.id
    
    return supervisor_id

# Scratchpad becomes persistent memory for this supervisor
scratchpad_key = f"scratchpad:{thread_id}"
```

**Advantages**:
- Supervisor accumulates knowledge over the entire conversation
- Scratchpad becomes long-term memory
- Can track multi-step research plans across turns
- Better context for synthesizing complex answers

---

### 4. **Parallel Delegation (Advanced)**

**Use Case**: User asks comparative question requiring multiple independent research tasks

```python
# User: "Compare the 2024 revenue of NVIDIA, AMD, and Intel"

# Supervisor creates research plan
await update_scratchpad("""
Research Plan: GPU Vendor Revenue Comparison
─────────────────────────────────────────────
Tasks:
  1. [ ] NVIDIA 2024 revenue
  2. [ ] AMD 2024 revenue  
  3. [ ] Intel GPU division 2024 revenue
  
Status: Delegating tasks in parallel...
""")

# Launch parallel workers
results = await asyncio.gather(
    delegate_research_task(task="Find NVIDIA 2024 revenue"),
    delegate_research_task(task="Find AMD 2024 revenue"),
    delegate_research_task(task="Find Intel GPU 2024 revenue"),
)

# Update scratchpad as results come in
await append_scratchpad(f"✓ NVIDIA: {results[0]}")
await append_scratchpad(f"✓ AMD: {results[1]}")
await append_scratchpad(f"✓ Intel: {results[2]}")

# Synthesize final answer
return synthesize_comparison(results)
```

**Benefits**:
- 3x faster for comparative queries (parallel vs sequential)
- Scratchpad tracks completion status
- Can show real-time progress to user
- Gracefully handles partial failures (2/3 tasks succeed)

**Note**: This requires architectural changes beyond just scratchpad

---

### 5. **Research Journal / Audit Trail**

**Concept**: Scratchpad becomes a human-readable log of the research process

```
╔════════════════════════════════════════════════════════════════╗
║  DEEP RESEARCH SESSION: Tech Company Revenue Analysis          ║
║  Started: 2024-02-13 22:47 UTC                                ║
╚════════════════════════════════════════════════════════════════╝

[22:47:15] User Query: "Find NVIDIA's Q4 2024 revenue"

[22:47:16] Research Plan Created:
  • Target: NVIDIA fiscal Q4 2024 (ended Jan 28, 2024)
  • Sources needed: Investor Relations, SEC filings
  • Estimated complexity: Medium

[22:47:18] Delegated to Worker (ID: worker_a3f8b2e1)
  
[22:47:45] Worker Report Received:
  → NVIDIA fiscal Q4 2024 revenue: $26.04 billion
  → Source: https://investor.nvidia.com/...
  → Confidence: High (official SEC filing)

[22:47:46] ✓ Research Complete

─────────────────────────────────────────────────────────────────

[22:48:02] User Follow-up: "What about AMD?"

[22:48:03] Cache Check:
  ✗ AMD revenue not in scratchpad
  ✓ NVIDIA data already available (skipping re-research)

[22:48:04] Delegated to Worker (ID: worker_b7c4d9a2)

[22:48:31] Worker Report Received:
  → AMD fiscal Q4 2024 revenue: $6.17 billion
  → Source: https://ir.amd.com/...
  → Confidence: High

[22:48:32] Synthesis:
  Comparison: NVIDIA ($26.04B) vs AMD ($6.17B)
  NVIDIA revenue is 4.2× larger than AMD

[22:48:33] ✓ Comparison Complete
```

**Value**:
- Users can review the research process
- Debugging tool for developers
- Transparency into data sources
- Exportable research reports

---

## 🔧 Implementation Requirements

### 1. **Persistent Scratchpad Storage**

**Current** (Assumed):
- Scratchpad stored in Redis with ephemeral key
- Key dies when supervisor is deleted

**Needed**:
```python
# Thread-scoped scratchpad
SCRATCHPAD_KEY = f"scratchpad:{thread_id}"
SCRATCHPAD_TTL = 86400  # 24 hours

# Structure
{
    "created_at": "2024-02-13T22:47:00Z",
    "thread_id": "thread_abc123",
    "supervisor_id": "asst_xyz789",
    "content": "Research Plan: ...",
    "version": 3,  # Incremented on each update
    "last_updated": "2024-02-13T22:48:33Z"
}
```

---

### 2. **Scratchpad Versioning**

**Problem**: Concurrent updates could cause data loss

**Solution**: Optimistic locking

```python
async def update_scratchpad(thread_id, new_content):
    current = await redis.get(f"scratchpad:{thread_id}")
    current_version = current.get("version", 0)
    
    new_scratchpad = {
        **current,
        "content": new_content,
        "version": current_version + 1,
        "last_updated": utc_now()
    }
    
    # Atomic update with version check
    success = await redis.set_if_version_matches(
        key=f"scratchpad:{thread_id}",
        value=new_scratchpad,
        expected_version=current_version
    )
    
    if not success:
        raise ConcurrentModificationError("Scratchpad changed during update")
```

---

### 3. **Smart Cache Invalidation**

**Challenge**: When is cached research stale?

```python
CACHE_RULES = {
    "company_revenue": {
        "ttl_hours": 24,  # Financial data changes daily
        "requires_refresh": ["fiscal_year_end", "earnings_release"]
    },
    "product_pricing": {
        "ttl_hours": 1,  # Pricing can change hourly
        "requires_refresh": ["sale_event", "new_product_launch"]
    },
    "historical_data": {
        "ttl_hours": 8760,  # 1 year - historical facts don't change
        "requires_refresh": []
    }
}

async def should_refresh_cache(topic, last_researched):
    rules = CACHE_RULES.get(topic, {"ttl_hours": 24})
    age_hours = (utc_now() - last_researched).total_seconds() / 3600
    return age_hours > rules["ttl_hours"]
```

---

### 4. **Scratchpad Query Interface**

**Enable the supervisor to intelligently query its own memory**:

```python
# Instead of reading the entire scratchpad as text:
await read_scratchpad()  # Returns full text blob

# Provide structured queries:
await scratchpad_query(
    thread_id=thread_id,
    query={
        "entity": "NVIDIA",
        "metric": "revenue",
        "time_period": "Q4 2024"
    }
)
# Returns: {"value": "$26.04B", "source": "...", "confidence": "high"}
```

**Benefits**:
- Faster lookups (no LLM parsing required)
- Structured data for comparisons
- Enables semantic search over research history

---

## 📊 Cost-Benefit Analysis

### **Cost of Implementation**:
- Development time: ~2-3 weeks
- Additional Redis storage: ~1-5KB per conversation (negligible)
- Complexity: Medium (requires state management + cache invalidation logic)

### **Benefit**:
| Scenario | Current | With Scratchpad | Savings |
|----------|---------|-----------------|---------|
| Single query | 1 delegation | 1 delegation | 0% |
| Follow-up question (same topic) | 1 delegation | 0 delegations (cached) | **100%** |
| 3-way comparison | 3 sequential delegations | 3 parallel delegations | **66% latency** |
| 5-turn research session | 5 delegations | 2-3 delegations | **40-60%** |

**ROI**: High for power users doing multi-turn research, low for casual "one-shot" queries

---

## 🚦 Recommendation: Phased Rollout

### **Phase 1: Ship Current Architecture** (Now)
- ✅ Single delegation model
- ✅ Ephemeral supervisors
- ❌ Remove scratchpad tools (or keep as no-ops)
- **Goal**: Validate core deep research value prop

### **Phase 2: Add Persistent Scratchpad** (When users ask for it)
- ✅ Persistent supervisors per thread
- ✅ Scratchpad as cache layer
- ✅ Basic cache invalidation
- **Trigger**: Users complain about re-researching same data

### **Phase 3: Advanced Features** (Product-market fit achieved)
- ✅ Parallel delegation
- ✅ Structured scratchpad queries
- ✅ Research journal export
- ✅ Cross-conversation knowledge graph

---

## 🎬 Decision Framework

**Implement scratchpad enhancements IF**:
1. ✅ Users frequently ask follow-up questions requiring same data
2. ✅ Average research session has 3+ turns
3. ✅ Cost of redundant API calls becomes significant
4. ✅ Users request "show me your research process" feature

**Skip for now IF**:
1. ✅ Most queries are single-shot ("find X" → answer → done)
2. ✅ Thread memory already handles follow-ups adequately
3. ✅ Development resources better spent elsewhere
4. ✅ No user complaints about redundant research

---

## 📝 Future-Proofing Notes

Even if scratchpad isn't implemented now, keep the **tool definitions** in the supervisor's toolset:

**Reason**: 
- The LLM might organically start using them for planning (even if no-op)
- Keeps the API surface stable for future enhancement
- Acts as "documentation" of the supervisor's intended capabilities

**Implementation**:
```python
async def handle_update_scratchpad(content, thread_id):
    # Phase 1: No-op (just log)
    LOG.info(f"Scratchpad update (not persisted): {content[:100]}...")
    return {"status": "acknowledged", "persisted": False}
    
    # Phase 2: Enable when ready
    # await redis.set(f"scratchpad:{thread_id}", content)
    # return {"status": "saved", "persisted": True}
```

---

## 🔗 Related Enhancements

If implementing scratchpad, consider also:

1. **Supervisor Reflection Loop**
   - After worker returns, supervisor asks: "Does this fully answer the query?"
   - If no, creates follow-up delegation
   - Scratchpad tracks iteration count

2. **Research Quality Scoring**
   - Supervisor rates worker reports (1-5 stars)
   - Stores in scratchpad for audit
   - Enables debugging poor research quality

3. **Cross-Thread Knowledge Transfer**
   - User asks similar question in new thread
   - System suggests: "I researched this yesterday, would you like those results?"
   - Scratchpad becomes global knowledge base (with privacy controls)

---

## 📚 References

- [ChatGPT Memory Feature](https://help.openai.com/en/articles/8590148-memory-in-chatgpt) - Similar concept for personalization
- [LangChain Memory](https://python.langchain.com/docs/modules/memory/) - Memory patterns for agents
- [Redis Cache Patterns](https://redis.io/docs/manual/patterns/cache/) - Cache invalidation strategies

---

## ✅ Action Items (When Ready to Implement)

- [ ] Design scratchpad schema (JSON structure)
- [ ] Implement persistent supervisor per thread
- [ ] Add cache lookup before delegation
- [ ] Build scratchpad query interface
- [ ] Create cache invalidation rules
- [ ] Add metrics (cache hit rate, avg delegations per session)
- [ ] User testing: Does this improve UX?
- [ ] Documentation: How to use scratchpad for debugging

---

**Last Updated**: 2024-02-13  
**Status**: Deferred - Ship v1 without it, revisit based on user feedback  
**Owner**: TBD

---

## Appendix: Code Snippets

### Example: Cache-Aware Delegation

```python
async def intelligent_delegate(task, requirements, thread_id):
    """
    Checks scratchpad before delegating to avoid redundant research.
    """
    # Step 1: Parse what we're looking for
    entities = extract_entities(task)  # e.g., ["NVIDIA", "revenue", "Q4 2024"]
    
    # Step 2: Check cache
    scratchpad = await read_scratchpad(thread_id)
    cached_results = []
    missing_entities = []
    
    for entity in entities:
        cached = extract_from_scratchpad(scratchpad, entity)
        if cached and not is_stale(cached):
            cached_results.append(cached)
            LOG.info(f"✓ Cache hit: {entity}")
        else:
            missing_entities.append(entity)
            LOG.info(f"✗ Cache miss: {entity}")
    
    # Step 3: Only delegate for missing data
    if missing_entities:
        refined_task = f"Find {', '.join(missing_entities)}"
        new_results = await delegate_research_task(
            task=refined_task,
            requirements=requirements
        )
        
        # Step 4: Update cache
        await append_scratchpad(f"Researched: {new_results}")
        
        return cached_results + [new_results]
    else:
        LOG.info("All data in cache - no delegation needed!")
        return cached_results
```

### Example: Parallel Delegation

```python
async def parallel_research(tasks: list[str], thread_id):
    """
    Launches multiple workers in parallel, tracks progress in scratchpad.
    """
    await update_scratchpad(f"""
    Research Plan: {len(tasks)} parallel tasks
    {chr(10).join(f'  [ ] {task}' for task in tasks)}
    """)
    
    async def delegate_and_update(task, index):
        result = await delegate_research_task(task=task)
        await append_scratchpad(f"  [✓] Task {index+1}: Complete")
        return result
    
    results = await asyncio.gather(*[
        delegate_and_update(task, i) 
        for i, task in enumerate(tasks)
    ])
    
    await append_scratchpad("All tasks complete!")
    return results
```

---

**End of Document**
