# Plan: Fix Outline Mode to Match/Beat Classic Performance

## Diagnosis Summary

Outline mode (55.6%) vs Classic (88.9%) — 4 tasks regressed. Root causes:

1. **System prompt is blind to outline format** — the `<browser_state>` section (system_prompt.md:39-55) explicitly teaches the classic flat format. The model receives `=== PAGE OUTLINE ===`, `BANNER:`, `MAIN:`, `(ungrouped):` etc. without any instructions on how to interpret them.

2. **Missing compound component info** — `_serialize_outline_subtree` (serializer.py:1197) never checks `node.original_node._compound_children`. Classic mode emits `compound_components=((options=Basic Plan|Pro Plan|Enterprise Plan,count=4))` for `<select>` — outline emits nothing. This directly caused the Dropdown Interaction failure.

3. **Tokens went UP (+28%)** — the outline wrapper (`=== PAGE OUTLINE ===`, landmark headers, AX name annotations, heading markers) adds overhead on simple pages. The collapsing mechanism only helps on multi-step revisits to complex pages — not these benchmarks.

4. **Orphan depth is flat** — `_collect_orphan_elements` (serializer.py:1296-1299) recurses with the same `depth` parameter, never incrementing. All orphan content appears at the same indent level, destroying parent-child structure that classic mode preserves via depth-based indentation.

## Changes

### 1. Add Outline Format Documentation to System Prompt

**File**: `browser_use/agent/system_prompts/system_prompt.md` (and all 7 variants)

**What**: Add a conditional paragraph within the `<browser_state>` section. Since the system prompt is static (loaded once at init), we need a different approach:

**Actual approach**: In `browser_use/agent/prompts.py`, the `AgentMessagePrompt._get_browser_state_description()` method (line 222) builds the browser state string that goes in the user message's `<browser_state>` tag. Add an outline format hint *in the user message* when `self.outline_mode` is True, right before the elements text. This avoids touching 8 prompt files and works correctly since it's the user message that contains the actual DOM content.

Insert after the `Interactive elements{truncated_text}:` header (line 323) when outline_mode is True:

```
(Outline mode: elements grouped by page landmark — BANNER, NAVIGATION, MAIN, CONTENTINFO etc.
Headings shown as # H1, ## H2. Elements use same [index]<tag attrs /> format.
"quoted text" after an element = its accessible label. (ungrouped) = elements outside any landmark.)
```

~3 lines, no token bloat. Teaches the model the format inline.

### 2. Port Compound Component Rendering to Outline Serializer

**File**: `browser_use/dom/serializer/serializer.py`

**What**: In `_serialize_outline_subtree` (line 1216-1232), after building the interactive element line, add compound component info using the exact same logic as classic mode (lines 958-991).

Same for `_collect_orphan_elements` (line 1272-1285) — after the interactive element rendering.

Extract the compound rendering logic into a shared static method `_build_compound_string(node)` to avoid duplication between classic, outline-subtree, and orphan-collector.

### 3. Fix Orphan Element Depth Tracking

**File**: `browser_use/dom/serializer/serializer.py`

**What**: In `_collect_orphan_elements` (line 1296-1299), the recursive call passes `depth` unchanged:
```python
DOMTreeSerializer._collect_orphan_elements(
    child, include_attributes, lines, landmark_node_ids, depth,
)
```

This should be `depth + 1` (or at least `depth + 1` for non-text, non-heading children to preserve nesting). Without this, a `<form>` with nested `<input>` elements on a landmarkless page renders everything at the same indent level — the model can't tell which inputs belong to which form.

But be careful: we don't want infinite depth growth for deep DOM trees. Cap at `depth + 1` for interactive/heading nodes only, skip depth increment for wrapper divs. Actually, the simplest correct fix: increment depth for all children, matching how `_serialize_outline_subtree` does it (line 1244: `child_depth = depth if skip_root else depth + 1`).

### 4. Write Tests for Each Fix

**Files**: New/updated test in `tests/ci/`

For each fix, write a targeted test:

a. **Compound component test**: Create a page with `<select>` (native dropdown), serialize with outline mode, assert that options appear in the output string.

b. **Orphan depth test**: Create a page with no landmarks (plain `<body><form><input/><input/></form></body>`), serialize with outline mode, assert that inputs are indented deeper than the form.

c. **Outline format hint test**: Verify that when outline_mode=True, the browser state description includes the format hint text.

d. **End-to-end benchmark**: Run the mock benchmark suite in both modes, confirm 100% pass rate for both.

### 5. Run Full Benchmark Validation

- Run `uv run pytest -vxs tests/ci` to confirm no regressions
- Run mock benchmarks in both modes to confirm 100%/100%
- (Real LLM benchmarks are out of scope here — that requires API keys + time)

## Execution Order

1. Fix 2 (compound components) — most impactful for benchmark scores
2. Fix 3 (orphan depth) — structural correctness
3. Fix 1 (system prompt hint) — teaches model the format
4. Fix 4 (tests) — validate all fixes
5. Fix 5 (run full suite) — confirm no regressions

## What This Does NOT Fix (Future Work)

- **Token efficiency on simple pages**: outline adds wrapper overhead on small pages. A potential optimization: skip outline wrapping when < N landmarks detected (fallback to classic). Not worth the complexity now.
- **Real LLM validation**: need to re-run with actual models after these fixes to measure the delta.
- **Region collapsing**: the cross-step collapsing logic is correct but untestable on short benchmark tasks. Leave it as-is.
