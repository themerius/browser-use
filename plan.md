# Plan: Fix Outline Mode to Beat Classic

## Diagnosis Summary

Outline mode **77.8%** vs Classic **88.9%** (−11.1pp). From trace analysis, two root causes dominate:

### Root Cause 1: Region collapsing hides interactive elements (CRITICAL)
After the first step on a page, `detect_unchanged_regions()` marks landmarks as unchanged because the same `backend_node_id` set and `element_count` persist. The agent sees:
```
MAIN (unchanged, 5 elements)
```
instead of the actual form fields with their indices. The agent literally cannot interact with elements it cannot see. This caused the WCAG Compliant Form to spiral for 13 steps (161s) — clicking nav links to 404s, getting validation errors, and never completing the form.

### Root Cause 2: Duplicate elements in nested landmarks
When `<nav>` is inside `<header>`, both are landmarks. The current code serializes BANNER's full subtree (including NAV children), then serializes NAV's subtree separately. Same elements appear twice — pure token waste.

### Secondary: No-landmark pages get overhead without benefit
The WCAG Non-Compliant Form (div-soup) has zero landmarks, so everything goes to `(ungrouped):` with the `=== PAGE OUTLINE ===` / `=== END OUTLINE ===` wrapper. This is strictly worse than classic mode.

## Changes

### 1. Remove region collapsing entirely
**Files:** `serializer.py`, `service.py`, `prompts.py`, `views.py`

Delete the cross-step collapsing mechanism. Every landmark is always fully serialized. This is the simplest fix and removes the #1 failure mode.

- `serializer.py:serialize_outline_tree()` — remove `detect_unchanged_regions()` call and the `if unchanged.get(key, False):` collapsed branch. Always serialize full content.
- `serializer.py:serialize_outline_tree()` — drop `previous_landmarks` parameter entirely.
- `views.py:llm_representation()` — drop `previous_landmarks` parameter.
- `prompts.py:AgentMessagePrompt` — drop `previous_landmarks` from init and `_get_browser_state_description()`.
- `service.py` lines 1111-1115 — remove the post-step `previous_landmarks = detect_landmarks(...)` update.
- `prompts.py:MessageManager` — remove `previous_landmarks` attribute.

The outline hint text already doesn't mention collapsing, so no change needed there.

### 2. Deduplicate nested landmarks
**File:** `serializer.py:serialize_outline_tree()` (lines 1137-1165)

Before serializing an outer landmark's subtree, collect node IDs of all its sub-regions. Pass an `exclude_ids` set to `_serialize_outline_subtree()` so sub-region nodes are skipped during the parent pass. They'll only appear under their own sub-region header.

- Add `exclude_ids: set[int] | None = None` parameter to `_serialize_outline_subtree()`
- In `serialize_outline_tree()`, for each landmark with sub_regions, build `sub_ids = {id(sub.node) for sub in lm.sub_regions}` and pass as `exclude_ids`
- In `_serialize_outline_subtree()`, if `id(node) in exclude_ids`, return early (skip that subtree)

### 3. Fall back to classic on landmarkless pages
**File:** `serializer.py:serialize_outline_tree()`

After `detect_landmarks()`, if the result is empty (no landmarks found), call `DOMTreeSerializer.serialize_tree(node, include_attributes)` directly and return. No outline wrapper, no `(ungrouped)` — just the proven classic format.

This handles div-soup pages (like the WCAG Non-Compliant Form) where outline adds overhead without structural benefit.

### 4. Update outline hint for accuracy
**File:** `prompts.py` line 319-325

Update the hint to remove mention of region change tracking (since we're removing it) and keep it minimal:
```
(Outline mode: elements grouped by page landmark — BANNER, NAVIGATION, MAIN, CONTENTINFO etc.
Headings shown as # H1, ## H2. Elements use same [index]<tag attrs /> format.
"quoted text" after an element = its accessible label.
Pages without landmarks use flat element list.)
```

### 5. Update tests & run benchmarks
- Update existing outline serializer tests to remove expectations about `(unchanged, N elements)`
- Verify deduplication with a nested landmark test case
- Verify landmarkless fallback
- Run `uv run pytest -vxs tests/ci` for full regression check
- Re-run mock benchmarks in both modes to confirm 100%/100%

## Execution Order

1. Change 1 (kill collapsing) — eliminates the critical failure mode
2. Change 2 (deduplicate) — reduces token waste
3. Change 3 (landmarkless fallback) — handles div-soup pages
4. Change 4 (hint update) — minor text fix
5. Change 5 (tests + benchmarks) — validate everything

## What we're NOT doing

- **No hybrid flat+outline** — overkill, the root causes are simpler
- **No adaptive thresholds** — the landmarkless fallback in Change 3 is sufficient
- **No system prompt changes** — the inline hint in the user message is the right place
- **No changes to benchmark evaluation** — it's working correctly

## Expected Outcome

- WCAG Compliant Form: PASS (agent can see form fields every step)
- WCAG Non-Compliant Form: PASS (falls back to classic, zero overhead)
- Token usage: competitive with classic (deduplication offsets outline structure cost)
- Pass rate: ≥88.9% (match or beat classic's 8/9)
