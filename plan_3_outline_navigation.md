# Plan 3: Hierarchical Outline + Screen Reader Navigation

## Goal

Implement a new serialization mode that presents the DOM as a **hierarchically-grouped, landmark-structured outline** instead of a flat indexed element list. The design borrows directly from screen reader navigation primitives: landmark regions, heading hierarchy, and region-level collapsing.

**The thesis** (REPORT.md §8): `required_model_size ≈ f(task_complexity / input_structure_quality)`. Better-structured input reduces apparent task complexity, enabling smaller models to navigate pages that currently require frontier LLMs. The hierarchical outline is the concrete implementation of "better-structured input."

**What changes**: The serializer output format — from a flat indented element list to a landmark-grouped, heading-annotated outline. Everything upstream (CDP extraction, enhanced DOM tree, AX node enrichment) and downstream (agent prompt consumption, action execution) stays the same.

---

## Current State

### What the LLM sees today

```
[1]<a>Home</a>
[2]<a>Products</a>
[3]<a>Contact</a>
[4]<input placeholder="Search..." />
[5]<h2>Featured Products</h2>
[6]<button>Add to Cart</button>
[7]<button>Add to Cart</button>
[8]<a>Next Page</a>
[9]<footer>© 2026 Acme Corp</footer>
```

The LLM must scan the entire list to understand page structure. It doesn't know [1]-[3] are navigation, [6]-[7] are in the main content area, or that [5] is a section heading. This structure is inferrable by humans from context but opaque to smaller models.

### What already exists in the data

The `EnhancedDOMTreeNode` already carries:
- **`ax_node.role`**: Contains landmark roles (`navigation`, `main`, `banner`, `contentinfo`, `complementary`, `region`, `search`, `form`) — available from `Accessibility.getFullAXTree()` but **never used for grouping**
- **`ax_node.name`**: Accessible name for landmarks (e.g., "Primary navigation", "Search")
- **Tag names**: `h1`–`h6` are in the tree as regular elements — heading levels never extracted as structural hierarchy
- **Parent-child relationships**: Full tree preserved in `EnhancedDOMTreeNode.children_nodes` and `SimplifiedNode.children`
- **`role` in DEFAULT_INCLUDE_ATTRIBUTES**: The `role` attribute is already in the serialized output, but inline with other attributes — not as structural grouping headers

**The gap is not data collection — it's serialization format.**

---

## Architecture

### No new files required for core implementation

The change is primarily in:
1. `browser_use/dom/serializer/serializer.py` — new `serialize_outline_tree()` method alongside existing `serialize_tree()`
2. `browser_use/dom/views.py` — `SerializedDOMState.llm_representation()` gains an `outline_mode` parameter
3. `browser_use/agent/prompts.py` — passes the outline mode flag through
4. `browser_use/agent/views.py` — `AgentSettings` gains an `outline_mode` field
5. `browser_use/agent/system_prompts/system_prompt.md` — updated `<browser_state>` documentation

### Supporting files

```
browser_use/dom/serializer/
├── serializer.py           # MODIFIED: add serialize_outline_tree()
├── outline.py              # NEW: landmark detection, heading hierarchy, region collapsing
├── clickable_elements.py   # unchanged
└── paint_order.py          # unchanged
```

---

## Step 1: `browser_use/dom/serializer/outline.py` — Landmark and Heading Extraction

A pure function module that operates on the `SimplifiedNode` tree (already built by the existing pipeline) and annotates it with structural metadata.

### Landmark Detection

```python
LANDMARK_ROLES = {
    'banner',         # <header>, role="banner"
    'navigation',     # <nav>, role="navigation"
    'main',           # <main>, role="main"
    'complementary',  # <aside>, role="complementary"
    'contentinfo',    # <footer>, role="contentinfo"
    'search',         # role="search", <search>
    'form',           # <form>, role="form" (only when named)
    'region',         # role="region" (only when named)
}

# HTML5 elements that imply landmark roles (WAI-ARIA spec)
IMPLICIT_LANDMARK_TAGS = {
    'header': 'banner',       # only when not nested in article/section
    'nav': 'navigation',
    'main': 'main',
    'aside': 'complementary',
    'footer': 'contentinfo',  # only when not nested in article/section
    'search': 'search',
}
```

**`detect_landmarks(root: SimplifiedNode) -> list[LandmarkRegion]`**

Walks the `SimplifiedNode` tree. For each node, checks:
1. `node.original_node.ax_node.role` against `LANDMARK_ROLES`
2. `node.original_node.tag_name` against `IMPLICIT_LANDMARK_TAGS`
3. Requires `ax_node.name` for `region` and `form` roles (unnamed ones are not landmarks per WAI-ARIA spec)

Returns a flat list of `LandmarkRegion` dataclasses:
```python
@dataclass
class LandmarkRegion:
    role: str                     # e.g., "navigation", "main"
    name: str | None              # e.g., "Primary", "Search" (from ax_node.name)
    node: SimplifiedNode          # The landmark root node
    depth: int                    # Tree depth of the landmark
    children: list[SimplifiedNode]  # Direct children within this region
    element_count: int            # Total interactive elements in this region
```

**Edge cases:**
- Nested landmarks: a `<nav>` inside `<main>` creates two regions. The `<nav>` is listed as a sub-region of `MAIN`, not a sibling.
- Multiple instances: two `<nav>` elements produce two `NAVIGATION` regions, disambiguated by `ax_node.name` (e.g., "Primary navigation", "Footer links").
- Unnamed regions: elements that are landmarks only when named (`form`, `region`) are excluded if `ax_node.name` is None.

### Heading Hierarchy Extraction

**`extract_heading_hierarchy(root: SimplifiedNode) -> list[HeadingNode]`**

Walks the tree looking for `h1`–`h6` tags. Builds a hierarchy:

```python
@dataclass
class HeadingNode:
    level: int                    # 1–6
    text: str                     # Heading text content
    node: SimplifiedNode          # The heading element
    parent_landmark: str | None   # Which landmark contains this heading
    children: list[HeadingNode]   # Sub-headings (h2 under h1, etc.)
```

This produces a table-of-contents structure:
```
h1: Product Catalog
  h2: Featured Products
  h2: Categories
    h3: Electronics
    h3: Clothing
  h2: Recently Viewed
```

### Region Change Detection (for Cross-Step Collapsing)

**`detect_unchanged_regions(current: list[LandmarkRegion], previous: list[LandmarkRegion]) -> dict[str, bool]`**

Compares landmark regions between consecutive steps. A region is "unchanged" if:
1. Same landmark role + name
2. Same interactive element count
3. Same element backend_node_ids (order-independent set comparison)

Unchanged regions can be collapsed in the serialized output to save tokens.

---

## Step 2: Modify `browser_use/dom/serializer/serializer.py` — New Outline Serializer

Add `serialize_outline_tree()` as a new static method alongside the existing `serialize_tree()`. The existing method is untouched — this is additive.

### Output Format

```
=== PAGE OUTLINE ===
BANNER: "Acme Store"
  [logo] [search] [cart]
NAV: "Primary"
  [1]<a>Home</a>  [2]<a>Products</a>  [3]<a>Contact</a>
NAV: "Search"
  [4]<input placeholder="Search..." />
MAIN:
  ## Featured Products
    Product 1: [6]<button>Add to Cart — $29.99</button>
    Product 2: [7]<button>Add to Cart — $49.99</button>
  [8]<a>Next Page →</a>
CONTENTINFO:
  [9]© 2026 Acme Corp  [10]<a>Privacy</a>  [11]<a>Terms</a>
=== END OUTLINE ===
```

### Serialization Rules

1. **Landmark headers**: `ROLE: "name"` (uppercase role, quoted name). If unnamed: just `ROLE:`
2. **Heading hierarchy**: `## Heading Text` using markdown heading syntax (h1=`#`, h2=`##`, etc.) — natural for LLMs trained on markdown
3. **Elements within landmarks**: Indented under their landmark, preserving the existing `[index]<tag>text</tag>` format
4. **Non-interactive structural text**: Preserved as contextual text between elements (product names, descriptions, etc.)
5. **Unchanged regions** (cross-step): `NAV: "Primary" (unchanged, 3 links)` — single line instead of re-serializing all elements
6. **Orphan elements**: Elements not contained in any landmark are grouped under `(ungrouped):` at the end

### Method Signature

```python
@staticmethod
def serialize_outline_tree(
    node: SimplifiedNode | None,
    include_attributes: list[str],
    previous_landmarks: list[LandmarkRegion] | None = None,
    depth: int = 0,
) -> str:
```

The `previous_landmarks` parameter enables cross-step region collapsing. If `None`, all regions are fully serialized (first step or disabled).

### Token Budget Estimation

For a typical e-commerce page:
- **Current format**: ~8,000 tokens (flat list, all elements every step)
- **Outline format (first step)**: ~6,500 tokens (same elements, but landmark headers replace some indentation overhead)
- **Outline format (subsequent steps)**: ~3,000–4,500 tokens (unchanged nav/header/footer collapsed)
- **Expected savings**: 20–45% token reduction per step after step 1

---

## Step 3: Wire the Outline Mode Through the Stack

### `browser_use/agent/views.py` — AgentSettings

Add to `AgentSettings`:
```python
outline_mode: bool = False
"""Use hierarchical landmark-grouped outline for DOM serialization instead of flat element list.
Experimental: may improve navigation on complex pages, especially with smaller models."""
```

Default `False` — opt-in to preserve backward compatibility.

### `browser_use/dom/views.py` — SerializedDOMState

Modify `llm_representation()`:
```python
def llm_representation(
    self,
    include_attributes: list[str] | None = None,
    outline_mode: bool = False,
    previous_landmarks: list[LandmarkRegion] | None = None,
) -> str:
    if outline_mode:
        from browser_use.dom.serializer.serializer import DOMTreeSerializer
        return DOMTreeSerializer.serialize_outline_tree(
            self._root, include_attributes or DEFAULT_INCLUDE_ATTRIBUTES,
            previous_landmarks=previous_landmarks,
        )
    # existing code path unchanged
    ...
```

### `browser_use/agent/prompts.py` — Prompt Construction

In `_get_browser_state_description()`, pass `outline_mode` through:
```python
elements_text = self.browser_state.dom_state.llm_representation(
    include_attributes=self.include_attributes,
    outline_mode=self.settings.outline_mode,
    previous_landmarks=self._previous_landmarks,  # stored on the prompt builder
)
```

### `browser_use/agent/service.py` — Landmark State Tracking

The `Agent` needs to store the previous step's landmarks for cross-step collapsing:
```python
# In the step loop, after serialization:
self._previous_landmarks = current_landmarks
```

This is lightweight — it's just a list of `LandmarkRegion` dataclasses from the current step, passed to the next step's serialization call.

---

## Step 4: Update System Prompt

Add to the `<browser_state>` section of `system_prompt.md`:

```markdown
When outline mode is active, interactive elements are grouped by page regions:
- BANNER, NAV, MAIN, COMPLEMENTARY, CONTENTINFO, SEARCH, FORM, REGION — these are landmark regions
- Headings (## , ### ) show the content hierarchy within regions
- Regions marked "(unchanged)" have the same elements as the previous step
- Use region names to navigate efficiently: "I need to search → look in NAV/SEARCH region"
```

This is instruction-level — no architectural change. The system prompt already documents the element format; this adds documentation for the new grouping.

---

## Step 5: Region-Level Actions (Screen Reader Primitives)

This is the optional but high-value extension. Screen readers provide ~20 single-key shortcuts for structural traversal. The outline format enables analogous **region-level actions** for the agent.

### New Actions (added to `browser_use/tools/service.py`)

**`focus_region`** — Expand a collapsed region to show its full element list:
```python
@action("Focus on a specific page region to see all its interactive elements")
async def focus_region(region: str) -> ActionResult:
    """
    Args:
        region: Region identifier, e.g., "MAIN", "NAV:Primary", "SEARCH"
    """
```

When a region is collapsed (e.g., `NAV: "Primary" (unchanged, 3 links)`), the agent can request its full contents without re-serializing the entire page. This is the direct analogue of a screen reader's landmark jump (`D` key).

**Why this matters**: Without `focus_region`, the agent must either:
- Accept the collapsed view (may miss changed elements within an "unchanged" region)
- Or request a full page re-serialization (wastes tokens)

With `focus_region`, the agent can selectively expand only the region it needs — O(1) structural navigation instead of O(n) scanning.

**Implementation**: The serializer stores the full `LandmarkRegion` objects. `focus_region` looks up the requested region and serializes just that subtree, returning it as `ActionResult.extracted_content`.

### Optional: `list_headings` action

Returns the heading hierarchy as a table of contents:
```
# Product Catalog (MAIN)
## Featured Products (MAIN, 4 elements)
## Categories (MAIN, 12 elements)
### Electronics (MAIN, 6 elements)
### Clothing (MAIN, 6 elements)
## Customer Reviews (MAIN, 8 elements)
```

The agent can use this to decide which section to scroll to or focus on. This is the analogue of a screen reader's heading list (`Insert+F7` in JAWS, `Rotor > Headings` in VoiceOver).

---

## Step 6: Tests

### Unit Tests — Outline Serializer

**`tests/ci/test_outline_serializer.py`**

Tests using pytest-httpserver with HTML fixtures designed to exercise landmark detection:

1. **Landmark detection**: Page with `<header>`, `<nav>`, `<main>`, `<aside>`, `<footer>` → verify each produces a `LandmarkRegion` with correct role
2. **Heading hierarchy**: Page with `h1 > h2 > h3` nesting → verify `HeadingNode` tree structure
3. **Named vs unnamed regions**: `<nav aria-label="Primary">` produces named landmark; bare `<nav>` produces unnamed landmark; `<div role="region">` (no name) is excluded
4. **Outline format correctness**: Full page → serialize → verify output matches expected outline format with `MAIN:`, `NAV:`, etc.
5. **Implicit landmark tags**: `<header>` → `banner`, `<footer>` → `contentinfo`, `<search>` → `search`
6. **Nested landmarks**: `<nav>` inside `<main>` → nav is sub-region, not sibling
7. **Orphan elements**: Elements outside any landmark → grouped under `(ungrouped):`
8. **Region collapsing**: Same page serialized twice → second time marks unchanged regions
9. **Backward compatibility**: `outline_mode=False` produces identical output to current serializer

### Integration Tests — Agent with Outline Mode

**`tests/ci/test_outline_agent.py`**

1. **Agent completes task with outline mode**: Mock LLM + httpserver page with landmarks → agent navigates using outline-formatted DOM → succeeds
2. **`focus_region` action**: Agent requests expanded view of a collapsed region → receives only that region's elements
3. **Token reduction verification**: Compare `len(outline_representation)` vs `len(flat_representation)` for a complex page with many landmarks → assert outline is shorter
4. **No regression**: Same tasks pass with both `outline_mode=True` and `outline_mode=False`

---

## Step 7: Benchmark Integration (Ties to Plan 1)

The outline mode is the first feature that Plan 1's benchmark infrastructure is designed to measure.

### A/B Comparison Protocol

Using the Plan 1 benchmark runner:

```bash
# Baseline: flat serialization
python -m benchmarks --model gpt-4o-mini --trials 5 --output reports/flat/

# Experiment: outline serialization
python -m benchmarks --model gpt-4o-mini --trials 5 --outline-mode --output reports/outline/
```

**Metrics compared** (all from existing `AgentHistoryList`):
- **Step count**: Does outline reduce steps to completion?
- **Token usage**: Does outline reduce total tokens per task?
- **Pass rate**: Does outline improve success rate?
- **Action distribution**: Does outline shift behavior (fewer scrolls? more targeted clicks?)

### Model Size Sweep

The key experiment from REPORT.md §8:

```bash
for model in mock gpt-4o-mini gpt-4o claude-3-haiku claude-3.5-sonnet; do
    python -m benchmarks --model $model --trials 5 --output reports/flat_$model/
    python -m benchmarks --model $model --trials 5 --outline-mode --output reports/outline_$model/
done
```

**Hypothesis**: `pass_rate(outline, small_model) ≥ pass_rate(flat, large_model)` for at least some task categories. If true, the outline format is a **model size multiplier** — you can use a cheaper model with better input structure.

---

## Implementation Order

1. **`browser_use/dom/serializer/outline.py`** — Landmark detection + heading hierarchy extraction. Pure functions, no side effects, independently testable.
2. **`tests/ci/test_outline_serializer.py`** — Tests for step 1. Run, confirm they fail (functions not yet wired into serializer).
3. **`serialize_outline_tree()` in serializer.py** — The new serialization method. Wire in landmark/heading data from step 1.
4. **Run outline serializer tests** — Confirm they pass.
5. **Wire through stack** — `AgentSettings.outline_mode`, `SerializedDOMState.llm_representation(outline_mode=...)`, `prompts.py`, `service.py` landmark state tracking.
6. **Update system prompt** — Document the outline format for the LLM.
7. **`tests/ci/test_outline_agent.py`** — Integration tests with mock LLM.
8. **`focus_region` action** — Optional region-level action in tools service.
9. **Benchmark comparison** — Run Plan 1 suite with `--outline-mode` flag, compare against flat baseline.
10. **Run full `tests/ci` suite** — Confirm no regressions with `outline_mode=False` (default).

---

## Key Design Decisions

### 1. Additive, not replacing

The outline serializer is a **new code path** alongside the existing `serialize_tree()`. The existing serializer is untouched. `outline_mode=False` (default) preserves 100% backward compatibility. This means:
- No risk to existing users
- A/B testing is trivial (just flip the flag)
- Can be reverted by removing the flag

### 2. Landmark detection from AX roles, not HTML tag parsing

Using `ax_node.role` from the accessibility tree is more reliable than parsing HTML tags:
- `<div role="navigation">` has no `<nav>` tag but is a navigation landmark
- `<header>` inside `<article>` is NOT a `banner` landmark (scoping rules) — the AX tree handles this correctly
- Custom elements with ARIA roles work automatically

### 3. Region collapsing is conservative

A region is only collapsed when ALL of these are true:
- Same landmark role + name as previous step
- Same set of interactive element `backend_node_id`s
- URL hasn't changed

This prevents false collapses. The cost of re-serializing a "changed" region that was actually unchanged is low (a few hundred tokens). The cost of collapsing a region that actually changed is high (agent misses new elements).

### 4. Heading hierarchy respects landmark boundaries

Headings are scoped to their containing landmark. An `h2` in `MAIN` and an `h2` in `COMPLEMENTARY` are separate hierarchies. This prevents cross-region heading confusion.

### 5. Format chosen for LLM compatibility

The outline uses markdown-style headings (`##`, `###`) because:
- LLMs are extensively trained on markdown
- 15% fewer tokens than JSON equivalent
- Natural nesting via indentation (already used by browser-use)
- Landmark headers in UPPERCASE to visually distinguish from content

---

## Risk Assessment

### Low Risk
- **Backward compatibility**: `outline_mode=False` is default, existing serializer unchanged
- **Data availability**: All required data (`ax_node.role`, heading tags, parent-child tree) already extracted by CDP pipeline
- **Test infrastructure**: Plan 1's benchmark suite provides measurement framework

### Medium Risk
- **LLM comprehension of new format**: The outline format is more structured, which benefits reasoning — but LLMs may need system prompt guidance to use landmarks effectively. Mitigation: detailed system prompt update + few-shot examples in prompt.
- **Region collapsing correctness**: False "unchanged" detection could hide new elements. Mitigation: conservative detection (require exact element ID match), plus `focus_region` action as escape hatch.

### Higher Risk
- **Performance of smaller models**: The thesis (smaller models benefit disproportionately from structure) is supported by literature (ScribeAgent, D2Snap, YAML format studies) but unverified for browser-use's specific architecture. This is precisely what the benchmark comparison in Step 7 validates.
- **Complex SPA pages**: Single-page apps with dynamic content may have poorly-defined landmark structure. React/Angular apps often wrap everything in a single `<div id="root">` with no semantic landmarks. Mitigation: the outline gracefully degrades — if no landmarks detected, everything falls under `(ungrouped):` which is equivalent to the current flat format.

---

## What This Delivers

| Signal | Mechanism | Expected Impact |
|--------|-----------|-----------------|
| Structural navigation | Landmark grouping | Agent can reason "go to MAIN" instead of scanning all elements |
| Token reduction | Region collapsing between steps | 20–45% fewer tokens per step after step 1 |
| Heading-based orientation | `##` section headers in output | Agent understands page sections without vision |
| Small model enablement | Structured input reduces task complexity | Testable: outline + 7B ≥ flat + 70B? |
| Screen reader parity | `focus_region`, `list_headings` actions | O(1) structural navigation vs O(n) scanning |

## What This Does NOT Do

- Does not change CDP data collection (already comprehensive)
- Does not change the enhanced DOM tree construction
- Does not change action execution (click, type, etc.)
- Does not implement a PRM or partial credit scoring (→ Plan 1/2 Level 3)
- Does not implement action caching or workflow memory
- Does not handle canvas/WebGL content (orthogonal problem)

---

## Relationship to Plans 1 and 2

| Dependency | Direction | Detail |
|-----------|-----------|--------|
| Plan 1 → Plan 3 | Plan 1 provides measurement | Plan 1's benchmark suite is how you measure whether the outline actually helps (A/B comparison, model size sweep) |
| Plan 3 → Plan 1 | Plan 3 provides feature to measure | The outline mode is the first non-trivial feature the benchmark suite evaluates |
| Plan 2 → Plan 3 | Plan 2 provides external validation | WebArena Verified's 812 tasks provide the scale and external comparability to validate the outline thesis beyond toy benchmarks |
| Plan 3 → REPORT.md §8 | Plan 3 validates the thesis | If `task_success(outline, 7B) ≥ task_success(flat, 70B)`, the hierarchical outlining thesis is confirmed |

The three plans form a pipeline: **Plan 1 builds the measurement instrument → Plan 3 builds the feature → Plans 1+2 measure the feature.**
