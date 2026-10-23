# NIBO Gap Resolution Agent — Minimal Scope (5 hours)

**Phase:** Week 3 Maven Extension  
**Scope:** Autonomous gap resolution before underwriter review  
**Deliverable:** DMV integration + mock medical records + basic gap resolver  

---

## What This Adds

**Current NIBO workflow:**
```
PDF → Extract → Apply 6 rules → Propose NIBO/READY → Underwriter reviews
```

**New workflow with gap resolver:**
```
PDF → Extract → Apply 6 rules → [NEW] Resolve gaps → Better proposal → Underwriter reviews
                                     (30 seconds)      (fewer gaps)
```

---

## What Gets Built (Minimal Scope)

### 1. Core Gap Resolver (`gap_resolver.py`)
- **GapResolver class** — orchestrates resolution attempts
- **GapResolution dataclass** — tracks each gap's resolution state
- **resolve_gap()** — tries auto-fetch, falls back to manual request
- **batch_resolve()** — resolves multiple gaps in parallel

### 2. DMV Integration (`integrations/dmv.py`)
- Fetch driver's license from state DMV API
- Validate expiration
- Return: valid license ✅ or expired/not found ❌
- Mock implementation for testing (no real API calls)

### 3. Mock Medical Records (`integrations/medical_records.py`)
- Placeholder that returns mock data
- Real implementation deferred (requires provider API contract)
- Allows gap resolver to accept "medical records" resolution method

### 4. Extended Models (`models.py`)
- `GapResolution` — tracks one gap's resolution attempt
- `EnhancedAnalysisState` — extends `AnalysisState` with resolution tracking

### 5. New LangGraph Node (`agent.py`)
- Add `resolve_gaps` node after `propose_status`
- Wire into existing agent graph
- Re-run rules if gaps are resolved

### 6. Updated Tests
- Mock DMV API calls
- Test gap resolution logic
- Integration test: gaps → resolved → better proposal

---

## Implementation Order

**Phase 1: Scaffolding (30 min)**
- [ ] Create `integrations/` folder
- [ ] Create `gap_resolver.py` with stub
- [ ] Extend `models.py` with GapResolution
- [ ] Wire resolve_gaps node into agent.py

**Phase 2: DMV Integration (2 hours)**
- [ ] Implement `integrations/dmv.py` (mock + real endpoint)
- [ ] Test with sample DL numbers
- [ ] Update gap resolver to call DMV for `id_valid` gaps

**Phase 3: Mock Medical (30 min)**
- [ ] Implement `integrations/medical_records.py` (returns mock data)
- [ ] Wire into gap resolver

**Phase 4: Testing (1 hour)**
- [ ] Write `tests/test_gap_resolver.py`
- [ ] Test gap resolution flow
- [ ] Verify no regressions: `pytest -q`

**Phase 5: Integration (30 min)**
- [ ] Test full flow: PDFs → gaps → resolved → better proposal
- [ ] Verify audit trail captures resolution attempts
- [ ] Update AGENTS.md with new node

**Total: ~5 hours**

---

## Design Principles

✅ **Still human-in-the-loop:** Agent proposes, underwriter approves  
✅ **Auditable:** Every resolution attempt logged  
✅ **Graceful degradation:** API fails → fall back to manual request  
✅ **Deterministic:** Same gaps always try same resolution methods  
✅ **Testable:** All integrations mockable for unit tests  

---

## Success Criteria

- [ ] `pytest -q` passes (all tests)
- [ ] Gap resolver can resolve 80% of `id_valid` gaps autonomously
- [ ] Underwriter sees gap resolution status in proposal
- [ ] Audit log captures every resolution attempt
- [ ] No changes to existing NIBO rules or approval flow
- [ ] Code follows existing style (type hints, pure functions, sparse docstrings)

---

## Files to Create/Modify

**Create:**
- `gap_resolver.py`
- `integrations/__init__.py`
- `integrations/dmv.py`
- `integrations/medical_records.py`
- `tests/test_gap_resolver.py`

**Modify:**
- `models.py` (add GapResolution, EnhancedAnalysisState)
- `agent.py` (add resolve_gaps node)
- `AGENTS.md` (document new node)

---
