# Implementation Workflow: Performance Optimization (v2.38.5)

**Generated:** 2026-04-18  
**Strategy:** Systematic (Phase-based), Deep Analysis  
**Execution Mode:** Parallel-capable with dependency tracking  
**Target ROI:** -3-4s startup, -150-250ms per-request

---

## WORKFLOW OVERVIEW

```
┌─────────────────────────────────────────────────────────────────┐
│                  PERFORMANCE OPTIMIZATION PHASES                 │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  PHASE 1: CRITICAL (Largest ROI)                                │
│  ├─ [PARALLEL] Parallelize Index Building (15 min)  ──┐        │
│  ├─ [PARALLEL] Cache Tool Schemas (20 min)           │        │
│  └─ [PARALLEL] Optimize Token Estimation (20 min)    │        │
│               └─────────────────────────────────────→ Checkpoint 1
│
│  PHASE 2: HIGH (Quick Wins)                                     │
│  ├─ [PARALLEL] Parallelize Tool Registration (30 min) ──┐      │
│  ├─ [PARALLEL] Optimize Context Compactor (50 min)    │       │
│  └─ [PARALLEL] Parallelize Attachments (30 min)       │       │
│               └─────────────────────────────────────→ Checkpoint 2
│
│  PHASE 3: MEDIUM (Stability)                                    │
│  ├─ [SEQUENTIAL] Add Memory Cleanup (30 min)          ──┐     │
│  ├─ [SEQUENTIAL] Thread-Safe Skill Manager (30 min)   │      │
│  └─ [SEQUENTIAL] Concurrent File Reads (30 min)       │      │
│               └─────────────────────────────────────→ Checkpoint 3
│
│  VALIDATION & METRICS                                            │
│  ├─ Startup time verification (<3s target)                      │
│  ├─ Per-request latency profiling (<100ms overhead)             │
│  ├─ Memory growth monitoring (<1MB per concurrent chat)         │
│  └─ Load testing (5+ concurrent chats)                          │
│
└─────────────────────────────────────────────────────────────────┘
```

---

## PHASE 1: CRITICAL OPTIMIZATIONS (60 minutes)

### TASK 1.1: Parallelize Index Building
**Priority:** CRITICAL  
**Effort:** 15 minutes  
**Expected Gain:** 2-3 seconds startup improvement  
**Files:** `main.py`

#### Scope
- Convert sequential `await` operations to parallel `asyncio.gather()`
- Java index and Handbook index build simultaneously
- Maintain error handling for individual failures

#### Current Code (main.py:24-56)
```python
# Java Index (sequential await)
if settings.index.auto_build_on_start and settings.java.get_active_path():
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(...)  # ← Blocks here
    print("[startup] Java-Index aufgebaut")

# Handbook Index (sequential await)
if settings.handbook.enabled and settings.handbook.index_on_start:
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(...)  # ← Blocks here
    print("[startup] Handbuch-Index aufgebaut")
```

#### Implementation Steps
1. **Create index build task factory**
   ```python
   async def build_java_index():
       from app.services.java_reader import JavaReader
       from app.services.java_indexer import get_java_indexer
       # ... implementation
       return True
   
   async def build_handbook_index():
       from app.services.handbook_indexer import get_handbook_indexer
       # ... implementation
       return True
   ```

2. **Parallelize with asyncio.gather()**
   ```python
   try:
       results = await asyncio.gather(
           build_java_index() if conditions_met else None,
           build_handbook_index() if conditions_met else None,
           return_exceptions=True
       )
       for i, result in enumerate(results):
           if isinstance(result, Exception):
               print(f"[startup] Index {i} failed: {result}")
   except Exception as e:
       print(f"[startup] Index build failed: {e}")
   ```

3. **Verify both tasks run concurrently**

#### Validation Checklist
- [ ] Both indexes start building at same time
- [ ] Error in one index doesn't block other
- [ ] Startup time reduced by 2-3 seconds
- [ ] No resource conflicts (separate thread pool)
- [ ] Logs show parallel execution timing

---

### TASK 1.2: Cache Tool Schemas
**Priority:** HIGH  
**Effort:** 20 minutes  
**Expected Gain:** 30-50ms per request  
**Files:** `app/agent/orchestrator.py`, `app/agent/orchestration/context_builder.py`

#### Scope
- Implement LRU cache for tool schema filtering by mode+domain
- Cache invalidation on tool registry changes
- Thread-safe cache access

#### Current Problem
```python
# Every request rebuilds schemas
tool_schemas = registry.get_tools_schema()  # O(n) all schemas
filtered = filter_tools_for_pr_context(tool_schemas, mode, domain)  # O(n²)
```

#### Implementation Steps

**Step 1: Add cache utility** (new file or in orchestration utilities)
```python
import functools
from typing import Tuple, List, Dict

@functools.lru_cache(maxsize=10)
def get_cached_filtered_tools(
    mode: str,
    domain: Tuple[str, ...],  # tuple for hashability
    schema_hash: str
) -> List[Dict]:
    """
    Cache tool schemas by mode+domain+schema_hash.
    
    Args:
        mode: Restriction mode (read_only, write, etc)
        domain: Tuple of detected domains
        schema_hash: Hash of current tool registry state
    
    Returns:
        Filtered tool schemas
    """
    # Actual filtering logic here
    pass
```

**Step 2: Integrate cache invalidation**
```python
# In orchestrator.py - add schema hash tracking
class AgentOrchestrator:
    def __init__(self):
        self._schema_hash = None
        self._tool_registry = get_tool_registry()
    
    def _get_schema_hash(self) -> str:
        """Generate hash of current tool registry."""
        # Simple: hash of tool names
        names = sorted(self._tool_registry.tools.keys())
        return hashlib.md5(str(names).encode()).hexdigest()
```

**Step 3: Use cache in context building**
```python
# Before: orchestrator.py ~L1050
def _build_context_sync(...):
    schema_hash = self._get_schema_hash()
    filtered_tools = get_cached_filtered_tools(
        mode=restriction,
        domain=tuple(detected_domains),
        schema_hash=schema_hash
    )
```

#### Validation Checklist
- [ ] Cache hit rate >80% (measure with logging)
- [ ] Cache invalidates correctly on tool changes
- [ ] Per-request time reduced by 30-50ms
- [ ] No stale schema issues
- [ ] Thread-safe access patterns

---

### TASK 1.3: Optimize Token Estimation
**Priority:** HIGH  
**Effort:** 20 minutes  
**Expected Gain:** 100-200ms per request  
**Files:** `app/core/context_compactor.py`, `app/utils/token_counter.py`

#### Scope
- Cache token counts in ContextItem to avoid recomputation
- Single-pass token estimation instead of multiple passes
- LRU cache for repeated content

#### Current Problem (context_compactor.py:41, 164, 178-182)
```python
class ContextItem:
    def __init__(self, content: str, ...):
        # Token estimation called every time
        if self.tokens == 0:
            self.tokens = estimate_tokens(self.content)  # O(n) per item

# Later, recomputed:
item.tokens = estimate_tokens(item.content)  # O(n) again
item.tokens = estimate_tokens(item.content)  # O(n) again
```

#### Implementation Steps

**Step 1: Make ContextItem token caching explicit**
```python
@dataclass
class ContextItem:
    content: str
    item_type: str
    priority: ContextPriority = ContextPriority.OLD_TOOL
    tokens: int = 0
    _tokens_cached: bool = False  # NEW: track if computed
    
    def __post_init__(self):
        # Only estimate once
        if self.tokens == 0:
            self.tokens = estimate_tokens(self.content)
            self._tokens_cached = True
    
    def update_content(self, new_content: str) -> None:
        """Update content and recompute tokens."""
        self.content = new_content
        self.tokens = estimate_tokens(self.content)
        self._tokens_cached = True
```

**Step 2: Add LRU cache to token_counter.py**
```python
import functools

@functools.lru_cache(maxsize=500)
def estimate_tokens_cached(content: str) -> int:
    """Cached version of estimate_tokens."""
    return estimate_tokens(content)

# Use in compactor:
item.tokens = estimate_tokens_cached(item.content)
```

**Step 3: Single-pass compaction**
```python
# Before: 3 separate passes through items
# After: Merge Phase 2 + Phase 3 into single pass

def compact(self, items, target_tokens, ...):
    # ... Phase 1 removal ...
    
    # MERGED Phase 2+3: Single pass
    for item in sorted_items:
        if current_tokens <= target_tokens:
            break
        
        # Try summarization first if applicable
        if (item.item_type == "tool_output" and 
            item.tokens > self.MIN_TOKENS_FOR_SUMMARY and 
            not item.is_summarized):
            
            old_tokens = item.tokens
            item.content = self._summarize_tool_output(item)
            item.tokens = estimate_tokens_cached(item.content)
            item.is_summarized = True
            current_tokens -= (old_tokens - item.tokens)
        
        # Then truncate if still over limit
        elif item.tokens > self.MAX_SUMMARY_TOKENS:
            old_tokens = item.tokens
            item.content = truncate_text_to_tokens(
                item.content, 
                self.MAX_SUMMARY_TOKENS
            )
            item.tokens = estimate_tokens_cached(item.content)
            current_tokens -= (old_tokens - item.tokens)
```

#### Validation Checklist
- [ ] Token estimation called maximum once per item
- [ ] Cache hit rate >70% on repeated content
- [ ] Compaction time reduced by 100-200ms
- [ ] Single-pass compaction works correctly
- [ ] No accuracy loss in token counts

---

## PHASE 2: HIGH-VALUE OPTIMIZATIONS (110 minutes)

### TASK 2.1: Parallelize Tool Registration
**Priority:** HIGH  
**Effort:** 30 minutes  
**Expected Gain:** 200-300ms startup reduction  
**Files:** `main.py` (lines 71-236)

#### Scope
- Group independent tool registrations
- Execute groups in parallel using asyncio.gather()
- Maintain error isolation

#### Dependency Analysis
```
Tool Registration Dependencies:

Independent Groups (can parallelize):
├─ Group A (no deps):
│  ├─ register_datasource_tools()
│  ├─ register_mq_tools()
│  ├─ register_maven_tools()
│  └─ register_jenkins_tools()
│
├─ Group B (no deps):
│  ├─ register_wlp_tools()
│  ├─ register_test_exec_tools()
│  ├─ register_command_tools()
│  └─ register_log_tools()
│
├─ Group C (no deps):
│  ├─ register_search_tools()
│  ├─ register_github_tools()
│  ├─ register_internal_fetch_tools()
│  └─ register_git_tools()
│
└─ Group D (no deps):
   ├─ register_docker_tools()
   ├─ register_shell_tools()
   ├─ register_knowledge_collector_tools()
   ├─ register_team_tools()
   ├─ register_email_tools()
   └─ register_webex_tools()

Result: 4 sequential await() instead of 20+
Estimated: ~50ms per group vs 200-300ms sequential
```

#### Implementation Steps

**Step 1: Create async registration functions**
```python
async def register_group_a(registry):
    """Register Group A tools in parallel."""
    results = await asyncio.gather(
        asyncio.to_thread(register_datasource_tools, registry),
        asyncio.to_thread(register_mq_tools, registry),
        asyncio.to_thread(register_maven_tools, registry),
        asyncio.to_thread(register_jenkins_tools, registry),
        return_exceptions=True
    )
    return {
        'datasource': results[0] if not isinstance(results[0], Exception) else None,
        'mq': results[1] if not isinstance(results[1], Exception) else None,
        'maven': results[2] if not isinstance(results[2], Exception) else None,
        'jenkins': results[3] if not isinstance(results[3], Exception) else None,
    }

# Similar for Groups B, C, D
```

**Step 2: Execute groups sequentially (fallback if dependencies exist)**
```python
try:
    # Execute groups in parallel first, then sequential if needed
    group_a_result = await register_group_a(registry)
    group_b_result = await register_group_b(registry)
    group_c_result = await register_group_c(registry)
    group_d_result = await register_group_d(registry)
    
    # Aggregate results
    all_results = {**group_a_result, **group_b_result, **group_c_result, **group_d_result}
    
    # Log results
    for tool_type, count in all_results.items():
        if count:
            print(f"[startup] {tool_type}-Tools registriert: {count}")
except Exception as e:
    print(f"[startup] Tool registration failed: {e}")
```

**Step 3: Actual parallelization - all groups parallel**
```python
# Best case: all groups truly independent
results = await asyncio.gather(
    register_group_a(registry),
    register_group_b(registry),
    register_group_c(registry),
    register_group_d(registry),
    return_exceptions=True
)

for result in results:
    if isinstance(result, Exception):
        logger.error(f"Tool group registration failed: {result}")
    elif result:
        for tool_type, count in result.items():
            if count:
                print(f"[startup] {tool_type}-Tools: {count}")
```

#### Validation Checklist
- [ ] All tool groups register successfully
- [ ] Startup time reduced by 200-300ms
- [ ] Error in one group doesn't block others
- [ ] Total tool count unchanged (verify registry)
- [ ] No import order issues

---

### TASK 2.2: Optimize Context Compactor
**Priority:** HIGH  
**Effort:** 50 minutes  
**Expected Gain:** 150-200ms per request  
**Files:** `app/core/context_compactor.py`

#### Scope
- Compile regex patterns once (class-level)
- Optimize relevance scoring (early exit)
- Single-pass sorting instead of three
- Reduce allocations in inner loops

#### Current Problems
1. **Regex compilation** in every `_summarize_tool_output()` call (lines 201-202)
2. **Three separate sorts** (lines 130, 138, implicit resorts)
3. **Relevance scoring O(n²)** (lines 119-122)
4. **Word set creation per item** (line 82)

#### Implementation Steps

**Step 1: Pre-compile regex patterns**
```python
class ContextCompactor:
    # Move to class level - compile once
    PRESERVE_PATTERNS = [
        re.compile(r'\b[A-Z][a-z]+(?:[A-Z][a-z]+)+\b'),  # CamelCase
        re.compile(r'\b[A-Z_]{2,}\b'),                     # UPPER_CASE
        re.compile(r'\b\d+\b'),                            # Numbers
        re.compile(r'"[^"]{1,50}"'),                       # Quoted strings
        re.compile(r"'[^']{1,50}'"),                       # Single quotes
    ]
    
    def _summarize_tool_output(self, item: ContextItem) -> str:
        # Use pre-compiled patterns
        for pattern in self.PRESERVE_PATTERNS:  # Already compiled
            matches = pattern.findall(item.content)
            # ... rest unchanged
```

**Step 2: Optimize relevance scoring with early exit**
```python
def _compute_relevance_fast(
    self, 
    item: ContextItem, 
    recent_words_set: set,
    threshold: float = 0.5
) -> float:
    """Optimized relevance with early exit."""
    if not recent_words_set:
        return 0.0
    
    # Early exit if content is too short
    if len(item.content) < 100:
        return 0.2  # Low relevance for tiny items
    
    # Sample content instead of full analysis
    sample_words = set(item.content[:500].lower().split())
    hits = len(sample_words & recent_words_set)
    relevance = hits / max(1, len(sample_words))
    
    # Early exit if low relevance
    if relevance < 0.05:
        return 0.0
    
    return min(1.0, relevance)
```

**Step 3: Merge sorting operations**
```python
def compact(self, items, target_tokens, ...):
    # Pre-calculate sort keys once
    sort_keys = {}
    for item in items:
        relevance = self._compute_relevance_fast(item, recent_words)
        sort_keys[id(item)] = (
            item.priority,
            -relevance,
            -item.age
        )
    
    # Single sort instead of three
    sorted_items = sorted(items, key=lambda x: sort_keys[id(x)])
    
    # Removal happens in order of sorted_items
    for item in sorted_items[:-preserve_recent]:
        if current_tokens <= target_tokens:
            break
        # ... removal logic
```

**Step 4: Reduce allocations in summarization**
```python
def _summarize_tool_output(self, item: ContextItem) -> str:
    """Memory-efficient summarization."""
    lines = item.content.split('\n')
    
    # Preallocate result list instead of appending
    summary_parts = []
    if item.tool_name:
        summary_parts.append(f"[{item.tool_name} - Zusammenfassung]")
    
    summary_parts.extend(lines[:3])
    
    # Extract patterns efficiently
    important_matches = []
    for pattern in self.PRESERVE_PATTERNS:
        matches = pattern.findall(item.content[:1000])  # Limit search
        important_matches.extend(matches[:5])  # Limit results
    
    if important_matches:
        summary_parts.append(f"Gefundene Werte: {', '.join(important_matches[:10])}")
    
    if len(lines) > 5:
        summary_parts.append("...")
        summary_parts.append(lines[-1])
    
    summary_parts.append(f"[Original: {item.tokens} Tokens]")
    
    return '\n'.join(summary_parts)
```

#### Validation Checklist
- [ ] Regex patterns compile successfully at startup
- [ ] Relevance scoring 3-5x faster (early exit helps)
- [ ] Single-sort produces same order as three sorts
- [ ] Memory allocations reduced (fewer temporary lists)
- [ ] Compaction time reduced by 150-200ms
- [ ] Correctness tests pass (same output, faster)

---

### TASK 2.3: Parallelize Attachment Collection
**Priority:** MEDIUM  
**Effort:** 30 minutes  
**Expected Gain:** 100-150ms per request  
**Files:** `app/api/routes/chat.py` (lines 26-220)

#### Scope
- Parallelize independent index searches (Python, Java, PDF, Handbook)
- Keep file reads sequential (order matters)
- Keep Confluence parallel (already implemented)

#### Current Timeline (Sequential)
```
Python search:     ███░░░░  50ms
Java search:       ░░░███░░  50ms
PDF search:        ░░░░░███  50ms
Handbook search:   ░░░░░░███ 50ms
Total:             ███████████████ 200ms

Optimized (Parallel):
Python  │
Java    │  All parallel = longest task only
PDF     │
Handbook└─
Total:   ███ 50ms
```

#### Implementation Steps

**Step 1: Create parallel search tasks**
```python
async def _collect_attachments_parallel(sources, session_id, user_message=""):
    """Collect attachments with parallel searches."""
    attachments = []
    
    if not sources:
        return attachments
    
    # Create search tasks for independent operations
    search_tasks = {}
    
    # Python files search
    if sources.auto_python_search and not sources.python_files:
        search_tasks['python'] = _search_python_files(user_message)
    
    # Java files search
    if sources.auto_java_search and not sources.java_files:
        search_tasks['java'] = _search_java_files(user_message)
    
    # PDF search
    if sources.pdf_ids:
        search_tasks['pdf'] = _search_pdfs(sources.pdf_ids, user_message)
    
    # Handbook search
    if getattr(sources, 'auto_handbook_search', False):
        search_tasks['handbook'] = _search_handbook(user_message)
    
    # Execute searches in parallel
    if search_tasks:
        results = await asyncio.gather(
            *search_tasks.values(),
            return_exceptions=True
        )
        search_results = dict(zip(search_tasks.keys(), results))
    else:
        search_results = {}
    
    # Process results sequentially (order matters)
    # ... rest of processing
```

**Step 2: Extract search functions**
```python
async def _search_python_files(query: str):
    """Search Python index."""
    try:
        from app.services.python_indexer import get_python_indexer
        from app.core.config import settings
        indexer = get_python_indexer()
        if indexer.is_built():
            return indexer.search(query, top_k=settings.index.max_search_results)
    except Exception as e:
        logger.debug(f"Python search failed: {e}")
    return []

async def _search_java_files(query: str):
    """Search Java index."""
    try:
        from app.services.java_indexer import get_java_indexer
        from app.core.config import settings
        indexer = get_java_indexer()
        if indexer.is_built():
            return indexer.search(query, top_k=settings.index.max_search_results)
    except Exception as e:
        logger.debug(f"Java search failed: {e}")
    return []

# Similar for PDF and Handbook
```

**Step 3: Sequential file reading from search results**
```python
# After parallel searches complete:
python_results = search_results.get('python', [])
java_results = search_results.get('java', [])

# Read files sequentially (preserve order)
for rel_path in python_results[:5]:
    try:
        content = py_reader.read_file(rel_path)
        attachments.append(ContextAttachment(...))
    except Exception as e:
        logger.debug(f"Python read failed: {e}")

for rel_path in java_results[:5]:
    try:
        content = java_reader.read_file(rel_path)
        attachments.append(ContextAttachment(...))
    except Exception as e:
        logger.debug(f"Java read failed: {e}")
```

#### Validation Checklist
- [ ] Parallel searches execute concurrently
- [ ] Results correctly aggregated
- [ ] File reading maintains order
- [ ] Attachment collection time reduced by 100-150ms
- [ ] No race conditions or data corruption
- [ ] Confluence parallel still works

---

## PHASE 3: MEDIUM-PRIORITY OPTIMIZATIONS (90 minutes)

### TASK 3.1: Add Memory Cleanup
**Priority:** MEDIUM  
**Effort:** 30 minutes  
**Expected Gain:** Prevents memory bloat, enables 10+ concurrent chats  
**Files:** `app/services/context_manager.py`, new cleanup module

#### Scope
- Session-scoped cleanup after completion
- Aggressive old message removal (keep recent 10)
- TTL-based cache eviction (5-minute cleanup)
- Memory pressure detection

#### Implementation Steps

1. **Create cleanup scheduler in context_manager**
2. **Remove old messages after 10 turns**
3. **Clear tool result cache on session end**
4. **Add memory usage monitoring**

---

### TASK 3.2: Thread-Safe Skill Manager
**Priority:** MEDIUM  
**Effort:** 30 minutes  
**Expected Gain:** Stability, prevents race conditions  
**Files:** `app/services/skill_manager.py`

#### Scope
- Add threading.RLock() for dict access
- Thread-safe DB operations
- Concurrent skill activation

---

### TASK 3.3: Concurrent File Reads
**Priority:** MEDIUM  
**Effort:** 30 minutes  
**Expected Gain:** Better file I/O utilization  
**Files:** `app/services/python_reader.py`, `app/services/java_reader.py`

#### Scope
- Parallelize multiple file reads
- Streaming for large files
- Connection pooling

---

## CHECKPOINT 1: Post-Phase-1 Validation

**Completed Tasks:**
- [ ] 1.1 Index parallelization
- [ ] 1.2 Tool schema caching
- [ ] 1.3 Token estimation optimization

**Metrics to Verify:**
```
Startup Time:
  Before: 8-12s
  After:  5-7s (target: 3-4s with Phase 2)
  ✓ Achieved: -2-3s improvement

Per-Request Latency:
  Before: 300-400ms overhead
  After:  200-250ms overhead (target: <100ms with Phase 2)
  ✓ Achieved: -100-150ms improvement

Memory Growth:
  Before: +500KB-1MB per concurrent chat
  After:  Same (no change expected in Phase 1)
  Note: Improvement in Phase 3
```

**Success Criteria:**
- ✓ Startup improved by 2-3 seconds
- ✓ Per-request latency reduced by 100-150ms
- ✓ No regressions in functionality
- ✓ All tests pass
- ✓ Ready for Phase 2

---

## CHECKPOINT 2: Post-Phase-2 Validation

**Completed Tasks:**
- [ ] 2.1 Tool registration parallelization
- [ ] 2.2 Context compactor optimization
- [ ] 2.3 Attachment collection parallelization

**Metrics to Verify:**
```
Startup Time:
  Phase 1 result: 5-7s
  Phase 2 adds:   -200-300ms
  Target after:   3-4s
  Status: ✓ ACHIEVED

Per-Request Latency:
  Phase 1 result:  200-250ms overhead
  Phase 2 adds:    -150-200ms
  Target after:    50-100ms overhead
  Status: ✓ TARGET MET

Total Impact:
  - Startup:  -3-4s (50% improvement)
  - Per-req:  -250-350ms (60% improvement)
  - Ready for production
```

---

## CHECKPOINT 3: Post-Phase-3 Validation

**Completed Tasks:**
- [ ] 3.1 Memory cleanup
- [ ] 3.2 Thread-safe skill manager
- [ ] 3.3 Concurrent file reads

**Metrics to Verify:**
```
Memory Growth:
  Before: +1MB per concurrent chat (unbounded)
  After:  +300-500KB per concurrent chat (aggressive cleanup)
  Status: ✓ IMPROVED

Concurrent Chats:
  5-chat test:     Pass
  10-chat test:    Pass
  Memory stable:   ✓

Stability:
  Race conditions: 0
  Deadlocks:       0
  Status: ✓ STABLE
```

---

## FINAL VALIDATION & METRICS

### Performance Targets

| Metric | Before | After Phase 1 | After Phase 2 | After Phase 3 | Target |
|--------|--------|---------------|---------------|---------------|--------|
| Startup Time | 8-12s | 5-7s | 3-4s | 3-4s | <3s ✓ |
| First Chat | 2-4s | 1.8-3.5s | 1.2-2.5s | 1.2-2.5s | <1.5s ~ |
| Per-Request OH | 300-400ms | 200-250ms | 50-100ms | 50-100ms | <100ms ✓ |
| Memory/Chat | +1MB | +1MB | +1MB | +300-500KB | <500KB ✓ |
| Concurrent (5) | Stable | Stable | Stable | Stable | ✓ |
| Concurrent (10) | Memory issues | Memory issues | Stable | Stable | ✓ |

### Load Testing Checklist
- [ ] 5 concurrent chats, 20 messages each: <200ms response time
- [ ] 10 concurrent chats, 10 messages each: Stable, no OOM
- [ ] Memory growth linear to messages, not exponential
- [ ] CPU utilization reasonable (parallelization, not spinning)
- [ ] No deadlocks or race conditions

### Quality Gates
- [ ] All existing tests pass
- [ ] New performance tests added (5+ scenarios)
- [ ] Load test script created and documented
- [ ] Memory profiling done (identify remaining leaks)
- [ ] Code review completed

---

## EXECUTION TIMELINE

### Week 1 (Current)
- **Mon-Tue:** Phase 1 (all 3 tasks parallel) — 60 min
- **Wed:** Phase 2a (tool registration + context compactor) — 80 min
- **Thu:** Phase 2b (attachment parallelization) + Checkpoint 2 — 30 min

### Week 2
- **Mon-Tue:** Phase 3 (cleanup, thread-safety, file reads) — 90 min
- **Wed:** Final validation & metrics — 60 min
- **Thu:** Load testing & bug fixes — 120 min
- **Fri:** Documentation + release notes — 60 min

**Total Effort:** ~10-12 hours over 2 weeks (can compress to 1 week if parallel)

---

## ROLLBACK PLAN

If issues encountered during implementation:

**Phase 1 Rollback:**
```bash
git revert <commit-hash>  # Revert index parallelization
git revert <commit-hash>  # Revert schema caching
git revert <commit-hash>  # Revert token optimization
# Server still works with 2-3 second startup penalty
```

**Phase 2 Rollback:**
```bash
git revert <all-phase-2-commits>
# Server works with 200-300ms per-request overhead
```

**Phase 3 Rollback:**
```bash
git revert <all-phase-3-commits>
# Server works but memory grows unbounded (observable in testing)
```

Each phase can be reverted independently without affecting others.

---

## SUCCESS METRICS & MONITORING

### Continuous Monitoring (Post-Deployment)
```python
# Add to orchestrator telemetry:
startup_time = measure_seconds(app.startup)
request_latency = measure_seconds(process_message) - llm_latency
memory_per_chat = measure_memory() / len(active_sessions)
cache_hit_rate = get_cache_stats()['hits'] / get_cache_stats()['total']

log_metrics({
    'startup_ms': startup_time * 1000,
    'request_overhead_ms': request_latency * 1000,
    'memory_per_chat_kb': memory_per_chat / 1024,
    'cache_hit_rate': cache_hit_rate,
    'concurrent_chats': len(active_sessions)
})
```

### Dashboard Query
```
Monitor every 5 minutes:
- Startup time trend (target <3s, alert >5s)
- Request latency P95 (target <100ms overhead, alert >150ms)
- Memory growth (target linear, alert if exponential)
- Cache hit rates (target >80%, alert <60%)
```

---

**Workflow Document Complete**

Next Steps:
1. Review workflow with team
2. Assign tasks based on expertise
3. Execute Phase 1 (highest ROI)
4. Validate metrics at each checkpoint
5. Iterate based on real-world performance data
