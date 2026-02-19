# Plan 4: Tool-Mediated DOM Querying — Let the LLM Search Instead of Read

## Goal

Replace (or supplement) the "dump full serialized DOM into context" pattern with **LLM-callable query tools that operate on the pre-extracted DOM/outline data**. The LLM receives a compact page summary and then calls search/filter/query functions to selectively retrieve the DOM elements it needs for its current goal — instead of scanning a flat 5–40K character element list every step.

**The thesis**: `tokens_needed ≈ f(task_specificity × page_complexity)`. Most tasks at any given step require information from a small fraction of the page. Giving the LLM tools to query that fraction — rather than forcing it to attend over everything — reduces token cost, improves grounding accuracy, and makes small models viable.

**Relationship to Plan 3**: Plan 3 restructures *how* the DOM is presented (hierarchical outline). Plan 4 restructures *when and how much* DOM is presented (on-demand via tool calls). These are complementary — Plan 3's outline can serve as the "summary view" that helps the LLM formulate better queries in Plan 4. Combined: the LLM sees a landmark outline, then drills into specific regions via tool calls.

---

## Literature Foundation

### Why This Approach Has Strong Empirical Support

| Evidence | Source | Finding |
|---|---|---|
| Simplified tool outputs beat full context | "How Good Are LLMs at Processing Tool Outputs?" (arXiv:2510.15955, Oct 2025) | +8 to +38 accuracy points; full responses 12x larger than needed |
| DOM pruning doubles grounding accuracy | Prune4Web (arXiv:2511.21398, Nov 2025) | 46.8% → 88.3% via programmatic tree pruning |
| Two-stage filter→select is SOTA for web | Mind2Web / MindAct (NeurIPS 2023 Spotlight) | Small model filters → LLM selects from filtered set |
| Constant-memory agents beat 2x-larger full-context | MEM1 (arXiv:2506.15841, Jun 2025) | 3.5x performance, 3.7x memory reduction, 27% peak tokens |
| ReAct progressive gathering outperforms dump-all | ReAct (Yao et al., ICLR 2023) | Only method to combine web interaction with correct reasoning |
| 1–7B models match GPT-4 at function calling | xLAM-1B (Salesforce), Octopus v2 (2B) | 78.9–99.5% function calling accuracy |
| RAG beats full context on fragmented data | "Long Context vs. RAG" (arXiv:2501.01880, Dec 2024) | RAG wins when relevant info is scattered across large documents |
| SLMs excel at tool calling with fine-tuning | SLM Tool Calling (arXiv:2512.15943, Dec 2025) | 350M model: 77.6% vs ChatGPT-CoT 26.0% on ToolBench |

### The Core Insight

The literature converges on a single principle: **when a data source is larger than what the current task step requires, programmatic filtering before LLM consumption beats dumping everything into context**. This holds across model sizes, with the benefit increasing as models get smaller (because smaller models have less capacity to ignore irrelevant context).

browser-use already partially validates this — `search_page` and `find_elements` exist as zero-LLM-cost query tools. But they execute fresh JavaScript on the live page, bypassing the rich pre-extracted DOM data (accessibility tree, element indices, bounding boxes, paint order, computed styles). The proposal here is to build query tools that operate on the *already-extracted* data, avoiding redundant CDP calls and leveraging the full enrichment pipeline.

---

## Current State

### What the LLM Receives Today

Every step, the LLM gets the full serialized DOM in context:

```
Interactive elements:
[Start of page]
[1]<a>Home</a>
[2]<a>Products</a>
...
[247]<button>Add to Cart</button>
[248]<a>Next Page</a>
[End of page]
```

For a 248-element page, this is ~8,000–15,000 tokens. The LLM must attend to all of it, even if the current step only needs "find the search box" (1 element) or "what buttons are in the product section?" (3–5 elements).

### What Already Exists

1. **`search_page`** (tools/service.py): Text/regex search via live JS `Runtime.evaluate`. Works on the live DOM, not the pre-extracted data. Returns matches with context.
2. **`find_elements`** (tools/service.py): CSS selector query via live JS `querySelectorAll`. Same pattern — re-queries the live DOM.
3. **`extract`** (tools/service.py): LLM-powered extraction from clean markdown. Uses pre-serialized DOM but requires an LLM call.
4. **`SerializedDOMState.selector_map`**: Maps element indices → `EnhancedDOMTreeNode` with full accessibility data. **This is the pre-extracted data that query tools should operate on.**
5. **`SimplifiedNode` tree**: The pruned DOM tree with parent-child relationships preserved. Available for structural queries.
6. **`EnhancedDOMTreeNode.ax_node`**: Accessibility node with role, name, description, states. Available for semantic queries.

### The Gap

The existing query tools (`search_page`, `find_elements`) go back to CDP for fresh JavaScript execution. This is:
- **Redundant**: The DOM data was already extracted and enriched in the pipeline
- **Missing enrichments**: Live JS queries don't have access to computed paint order, bounding box analysis, visibility filtering, or accessibility tree enrichment that the pipeline already performed
- **Index-disconnected**: Results from live JS queries don't reference the element indices that the agent uses for click/type actions — creating a grounding gap

The proposed tools operate on the *pre-extracted* `SerializedDOMState` and `SimplifiedNode` tree, preserving element indices and all enrichment data.

---

## Architecture

### Two Operating Modes

**Mode 1: Hybrid (Recommended Default)**
- LLM receives a **compact page summary** (landmark outline from Plan 3, or a statistics-only summary) + full query tool access
- LLM calls query tools to retrieve specific elements as needed
- Fallback: LLM can request the full serialized DOM via a `get_full_dom` tool if queries aren't sufficient

**Mode 2: Query-Only (Experimental, SLM-Optimized)**
- LLM receives **only** the page summary (URL, title, landmark list, element counts per region) — no element-level DOM in context
- All element access is through query tools
- Maximizes token savings, ideal for models with small context windows (4K–8K)
- Higher risk: if the LLM formulates poor queries, it misses elements entirely

### Query Tool Suite

All tools operate on the pre-extracted `SerializedDOMState` and `SimplifiedNode` tree. No CDP calls needed.

#### 1. `query_elements` — The Primary Search Tool

```python
class QueryElementsAction(BaseModel):
	"""Search for interactive elements matching criteria. Returns elements with their
	indices for use in click/type actions. Operates on pre-extracted page data — instant,
	zero cost."""

	text: str | None = Field(
		default=None,
		description='Text content to search for (case-insensitive substring match)',
	)
	role: str | None = Field(
		default=None,
		description='ARIA role or HTML element type: button, link, textbox, checkbox, '
		'combobox, heading, navigation, main, img, etc.',
	)
	name: str | None = Field(
		default=None,
		description='Accessible name (aria-label, alt text, or visible label)',
	)
	attributes: dict[str, str] | None = Field(
		default=None,
		description='Match specific HTML attributes, e.g. {"type": "submit", "href": "/login"}',
	)
	within_landmark: str | None = Field(
		default=None,
		description='Restrict search to a landmark region: MAIN, NAV, BANNER, SEARCH, '
		'COMPLEMENTARY, CONTENTINFO, FORM, REGION',
	)
	near_heading: str | None = Field(
		default=None,
		description='Find elements near (under) a heading matching this text',
	)
	max_results: int = Field(default=20, description='Maximum elements to return')
```

**Why this design**:
- Each field maps to data already available in `EnhancedDOMTreeNode` + `ax_node`
- Fields are independently optional — combine for precision (`role="button", text="Submit"`) or use one for broad search (`text="cart"`)
- `within_landmark` enables Plan 3–style structural navigation without requiring full outline serialization
- `near_heading` enables heading-based scoping (find elements under "Checkout" heading)
- Returns elements with their **existing index numbers** — direct bridge to `click_element`, `input_text`, etc.

**Return format**:
```
Found 3 elements matching role="button", within MAIN:
  [47]<button>Add to Cart — $29.99</button>  (under "Featured Products")
  [48]<button>Add to Cart — $49.99</button>  (under "Featured Products")
  [62]<button>Checkout (2 items)</button>  (under "Your Cart")
```

#### 2. `get_page_summary` — Structural Overview

```python
class GetPageSummaryAction(BaseModel):
	"""Get a structural overview of the current page: landmark regions, heading
	hierarchy, and element counts. Use this to orient yourself before querying
	for specific elements."""

	include_headings: bool = Field(
		default=True,
		description='Include heading hierarchy (h1-h6) as table of contents',
	)
	include_landmarks: bool = Field(
		default=True,
		description='Include landmark regions with element counts',
	)
```

**Return format**:
```
Page: "Acme Store — Products" (https://acme.com/products)
Viewport: 0.0 above, 3.2 pages below

Landmarks:
  BANNER: "Acme Store" (4 elements)
  NAV: "Primary" (6 links)
  NAV: "Search" (1 input)
  MAIN: (47 elements)
  COMPLEMENTARY: "Sidebar Filters" (12 elements)
  CONTENTINFO: (5 elements)

Headings:
  # Acme Store (BANNER)
  ## Product Catalog (MAIN)
    ### Featured Products (MAIN, 8 elements)
    ### Categories (MAIN, 12 elements)
    ### Recently Viewed (MAIN, 4 elements)
  ## Filter by Price (COMPLEMENTARY)
```

This is the "table of contents" that Plan 3's §8 describes — but delivered on-demand via a tool call rather than prepended to every step's DOM dump.

#### 3. `get_region` — Expand a Landmark Region

```python
class GetRegionAction(BaseModel):
	"""Get all interactive elements within a specific landmark region. Use after
	get_page_summary to drill into a region of interest."""

	landmark: str = Field(
		description='Landmark to expand: "MAIN", "NAV:Primary", "SEARCH", "BANNER", '
		'"COMPLEMENTARY:Sidebar Filters", etc.',
	)
	heading_scope: str | None = Field(
		default=None,
		description='Further restrict to elements under a specific heading within the landmark',
	)
```

**Return format**:
```
MAIN > "Featured Products" (8 elements):
  ## Featured Products
    [41]<img alt="Wireless Headphones" />
    [42]<a>Wireless Headphones — $29.99</a>
    [43]<select>Quantity: 1</select>
    [44]<button>Add to Cart</button>
    [45]<img alt="USB-C Hub" />
    [46]<a>USB-C Hub — $49.99</a>
    [47]<select>Quantity: 1</select>
    [48]<button>Add to Cart</button>
```

This is Plan 3's `focus_region` action, but with heading-level scoping added.

#### 4. `get_element_details` — Deep Inspect a Single Element

```python
class GetElementDetailsAction(BaseModel):
	"""Get detailed information about a specific element by index. Returns all
	attributes, accessibility properties, bounding box, and surrounding context."""

	index: int = Field(description='Element index from a previous query result')
```

**Return format**:
```
Element [47]:
  Tag: <select>
  Role: combobox
  Name: "Quantity"
  Value: "1"
  Options: ["1", "2", "3", "4", "5", "10"]
  Attributes: name="qty", id="qty-select-2"
  State: enabled, not expanded
  Bounding box: (450, 320) — 120×32px
  Parent: <div class="product-card">
  Context: Under "Featured Products" > "USB-C Hub — $49.99"
```

This exposes the full `EnhancedDOMTreeNode` data that's normally hidden behind the serialized text. Useful when the LLM needs to understand a complex widget (dropdown options, slider range, form validation state).

---

## Implementation

### No New Files Required for Core Implementation

The change is primarily in:
1. **`browser_use/tools/service.py`** — Register four new actions that operate on pre-extracted DOM data
2. **`browser_use/tools/views.py`** — Pydantic models for the four action parameter types
3. **`browser_use/agent/views.py`** — `AgentSettings` gains a `query_mode` field
4. **`browser_use/agent/prompts.py`** — Conditional DOM inclusion based on `query_mode`
5. **`browser_use/agent/service.py`** — Pass `SerializedDOMState` to tools for query execution
6. **`browser_use/agent/system_prompt*.md`** — Updated instructions for query-based navigation

### Supporting Module

```
browser_use/dom/
├── serializer/
│   ├── serializer.py        # unchanged
│   └── ...
├── views.py                 # SerializedDOMState gains query methods
└── query.py                 # NEW: query engine operating on SimplifiedNode tree
```

`query.py` is a pure function module (~200 lines) that implements the search/filter logic over `SimplifiedNode` and `EnhancedDOMTreeNode` data. No CDP calls, no side effects.

---

## Step 1: `browser_use/dom/query.py` — DOM Query Engine

A pure function module that operates on the pre-extracted `SimplifiedNode` tree and `DOMSelectorMap`.

### Core Query Function

```python
def query_elements(
	root: SimplifiedNode | None,
	selector_map: DOMSelectorMap,
	*,
	text: str | None = None,
	role: str | None = None,
	name: str | None = None,
	attributes: dict[str, str] | None = None,
	within_landmark: str | None = None,
	near_heading: str | None = None,
	max_results: int = 20,
) -> list[QueryResult]:
```

**Implementation approach**:
1. Walk the `SimplifiedNode` tree depth-first
2. For each node with an element index (interactive element), check all filter criteria:
   - `text`: case-insensitive substring match on `node.text` and all child text content
   - `role`: match against `node.original_node.ax_node.role` or HTML tag mapping (button, a→link, input→textbox, etc.)
   - `name`: match against `node.original_node.ax_node.name` (accessible name)
   - `attributes`: match against `node.original_node.attributes` dict
   - `within_landmark`: check if node is a descendant of a landmark node with the specified role (uses `ax_node.role` on ancestors)
   - `near_heading`: find heading elements matching the text, then return interactive elements between that heading and the next heading of same or higher level
3. All criteria are AND-combined — each additional filter narrows the result
4. Return `QueryResult` objects preserving the element index

```python
@dataclass
class QueryResult:
	index: int                    # The element index (for click/type actions)
	tag: str                      # HTML tag
	text: str                     # Visible text content
	role: str | None              # AX role
	name: str | None              # Accessible name
	landmark: str | None          # Containing landmark role
	heading_context: str | None   # Nearest heading above this element
	attributes: dict[str, str]    # Subset of relevant attributes
```

### Page Summary Function

```python
def get_page_summary(
	root: SimplifiedNode | None,
	selector_map: DOMSelectorMap,
	include_headings: bool = True,
	include_landmarks: bool = True,
) -> PageSummary:
```

Reuses landmark detection from Plan 3's `outline.py` (if implemented) or implements a lightweight version. Walks the tree once, collecting:
- Landmark regions with element counts
- Heading hierarchy
- Total interactive element count

### Region Expansion Function

```python
def get_region_elements(
	root: SimplifiedNode | None,
	selector_map: DOMSelectorMap,
	landmark: str,
	heading_scope: str | None = None,
	include_attributes: list[str] | None = None,
) -> str:
```

Finds the landmark subtree, optionally scopes to a heading section, then serializes using the existing `DOMTreeSerializer.serialize_tree()` on that subtree. Returns the same format the LLM already understands — just a smaller slice.

### Element Detail Function

```python
def get_element_details(
	selector_map: DOMSelectorMap,
	index: int,
) -> ElementDetails:
```

Looks up the `EnhancedDOMTreeNode` from `selector_map[index]`, extracts all available data (attributes, AX properties, bounding box, parent chain, dropdown options via `original_node.children_nodes`).

---

## Step 2: Register Query Actions in `browser_use/tools/service.py`

Register the four tools using the existing `@self.registry.action()` decorator pattern. Each action:
1. Receives the pre-extracted DOM data via a new special parameter (`dom_state: SerializedDOMState`)
2. Calls the corresponding function from `browser_use/dom/query.py`
3. Returns `ActionResult(extracted_content=formatted_result)`

### Special Parameter Injection

The existing `SpecialActionParameters` mechanism (`tools/registry/views.py`) already supports injecting `browser_session`, `cdp_client`, `page_extraction_llm`, etc. We add `dom_state`:

```python
class SpecialActionParameters(BaseModel):
	# ... existing fields ...
	dom_state: SerializedDOMState | None = None  # Pre-extracted DOM for query tools
```

This is populated in `Agent._execute_actions()` from the current step's `BrowserStateSummary.dom_state`, requiring no CDP re-query.

### Action Registration

```python
@self.registry.action(
	'Search for interactive elements by text, role, name, attributes, landmark region, '
	'or heading context. Returns matching elements with indices for click/type actions. '
	'Instant, zero cost. Combine criteria for precision.',
	param_model=QueryElementsAction,
)
async def query_elements(params: QueryElementsAction, dom_state: SerializedDOMState):
	from browser_use.dom.query import query_elements as _query
	results = _query(
		root=dom_state._root,
		selector_map=dom_state.selector_map,
		text=params.text,
		role=params.role,
		name=params.name,
		attributes=params.attributes,
		within_landmark=params.within_landmark,
		near_heading=params.near_heading,
		max_results=params.max_results,
	)
	return ActionResult(extracted_content=_format_query_results(results))
```

Same pattern for `get_page_summary`, `get_region`, `get_element_details`.

---

## Step 3: Agent Integration — Conditional DOM Inclusion

### `browser_use/agent/views.py` — AgentSettings

```python
class QueryMode(str, Enum):
	"""Controls how the LLM accesses DOM data."""
	OFF = 'off'           # Default: full DOM in context every step (current behavior)
	HYBRID = 'hybrid'     # Compact summary + query tools available
	QUERY_ONLY = 'query_only'  # No DOM in context, everything via query tools

# In AgentSettings:
query_mode: QueryMode = QueryMode.OFF
```

### `browser_use/agent/prompts.py` — Conditional State Construction

In `AgentMessagePrompt._get_browser_state_description()`:

```python
if self.settings.query_mode == QueryMode.OFF:
	# Current behavior — full serialized DOM
	elements_text = self.browser_state.dom_state.llm_representation(...)
elif self.settings.query_mode == QueryMode.HYBRID:
	# Compact summary: landmarks + headings + element counts
	# Full query tools available for drilling in
	elements_text = _get_compact_summary(self.browser_state.dom_state)
elif self.settings.query_mode == QueryMode.QUERY_ONLY:
	# Minimal: just URL, title, landmark names, total count
	elements_text = _get_minimal_summary(self.browser_state.dom_state)
```

**Compact summary** (~500–1500 tokens) includes:
- Page URL + title
- Landmark regions with element counts
- Heading hierarchy as table of contents
- Scroll position and viewport info
- Note: "Use query_elements, get_region, etc. to access specific elements"

**Minimal summary** (~100–300 tokens) includes:
- Page URL + title
- Total interactive elements count
- Landmark names only
- "Use get_page_summary for structural overview, then query_elements to find elements"

### `browser_use/agent/system_prompt*.md` — Updated Instructions

Add to the `<browser_state>` documentation:

```markdown
## Query Mode (when active)

You see a compact page summary instead of the full element list. Use these tools to
find and inspect elements:

- **query_elements**: Search by text, role, name, attributes, landmark, or heading.
  Returns elements with [index] numbers for click/type actions.
- **get_page_summary**: Get landmark regions and heading hierarchy. Use to orient.
- **get_region**: Expand a landmark region to see all its elements.
- **get_element_details**: Inspect a single element's full properties.

Strategy: get_page_summary → identify relevant region → query_elements or get_region
→ act on elements by index.
```

---

## Step 4: Query Tool Behavior Within the Action Sequence

### Query Tools Are Non-Terminating

Query tools return information but don't change page state. They should:
- NOT have `terminates_sequence=True` — the LLM can query then act in the same step
- Return results via `extracted_content` — visible in the current step's feedback
- Be callable multiple times per step (the LLM might query, refine, then act)

### Multi-Action Sequences With Queries

A typical step in hybrid mode:

```json
{
  "thinking": "I need to add the wireless headphones to cart. Let me find the right button.",
  "next_goal": "Click Add to Cart for wireless headphones",
  "action": [
    {"query_elements": {"text": "headphones", "role": "button"}},
    {"click_element": {"index": 47}}
  ]
}
```

But there's a subtlety: the LLM doesn't know the index (47) until the query returns. In the current multi-action model, all actions are specified upfront.

### Two Solutions to the Query→Act Dependency

**Solution A: Two-Step Pattern (Conservative)**
- Step 1: LLM issues query actions only, receives results
- Step 2: LLM issues act actions using indices from step 1's results
- Cost: one additional LLM round-trip per query
- Benefit: simple, no architectural change

**Solution B: Query-Then-Act in Single Step (Optimized)**
- Actions are executed sequentially (already the case in `multi_act`)
- Query tool results are accumulated and available to subsequent actions
- The LLM would need to predict the index based on the query description — this is fragile
- Alternative: add a `click_query_result` action that references query result position instead of element index

**Solution C: Intra-Step Replanning (Most Powerful)**
- After query tools execute, re-invoke the LLM with query results before executing remaining actions
- Essentially: split the action list at query boundaries, interleave LLM calls
- This is a mini ReAct loop within a single step
- Cost: additional LLM call per query, but with minimal context (just the query result)

**Recommendation**: Start with **Solution A** (two-step). It requires zero architectural changes — the LLM naturally learns to query first, act second. The overhead is one extra step per query, but each step is cheaper (compact summary instead of full DOM). Solution C is the eventual target but requires changes to the `multi_act` loop.

---

## Step 5: Performance Characteristics

### Token Cost Comparison

For a 250-element e-commerce page:

| Mode | Tokens per step | Steps for "find and click Add to Cart" | Total tokens |
|---|---|---|---|
| Current (full DOM) | ~12,000 | 1 (scan list, click) | ~12,000 |
| Hybrid (summary + 1 query) | ~2,000 + ~500 | 2 (query, then click) | ~5,000 |
| Query-only (minimal + 2 queries) | ~300 + ~800 | 3 (summary, query, click) | ~4,300 |

Token savings: **58–64%** per task on a medium-complexity page. On complex pages (500+ elements, ~25K tokens for full DOM), savings grow to **75–85%**.

### Latency Characteristics

| Mode | LLM calls | Per-call latency | Total latency |
|---|---|---|---|
| Current | 1 (large context) | High (large prompt) | 1 × high |
| Hybrid | 2 (small contexts) | Low (small prompts) | 2 × low |
| Query-only | 2–3 (minimal contexts) | Very low | 2–3 × very low |

LLM inference time scales super-linearly with prompt size (attention is O(n²) for input, O(n) for output in most implementations). Two calls with 2K tokens each are faster than one call with 12K tokens.

### Accuracy Implications

| Factor | Effect | Direction |
|---|---|---|
| Less noise in context | Better attention focus | ↑ Accuracy |
| Query formulation errors | May miss elements | ↓ Accuracy |
| Explicit structural reasoning | LLM must articulate what it's looking for | ↑ Accuracy |
| Extra steps | More opportunities for error/recovery | Neutral |
| Index stability across queries | Same indices throughout step | ↑ Grounding |

Literature predicts net positive: Prune4Web showed 46.8% → 88.3% from programmatic filtering, and the "Tool Outputs" paper showed +8 to +38 points from response simplification.

---

## Step 6: Tests

### Unit Tests — DOM Query Engine

**`tests/ci/test_dom_query.py`**

Tests using pytest-httpserver with HTML fixtures:

1. **Text search**: Page with buttons → `query_elements(text="Submit")` → returns the Submit button with correct index
2. **Role filtering**: Page with mixed elements → `query_elements(role="link")` → returns only links
3. **Attribute matching**: Form with inputs → `query_elements(attributes={"type": "email"})` → returns email input
4. **Landmark scoping**: Page with nav + main → `query_elements(text="Home", within_landmark="NAV")` → returns nav link, not main content "Home" section
5. **Heading scoping**: Page with multiple sections → `query_elements(near_heading="Featured Products")` → returns only elements under that heading
6. **Combined criteria**: `query_elements(role="button", text="Cart", within_landmark="MAIN")` → intersection of all criteria
7. **Empty results**: `query_elements(text="nonexistent")` → empty result with helpful message
8. **Max results**: Page with 100 links → `query_elements(role="link", max_results=5)` → exactly 5 results
9. **Page summary**: Complex page → `get_page_summary()` → correct landmark list, heading hierarchy, element counts
10. **Region expansion**: `get_region(landmark="MAIN", heading_scope="Featured")` → serialized subtree matching existing format
11. **Element details**: `get_element_details(index=5)` → full accessibility properties, bounding box, parent context
12. **Index consistency**: Element indices from query results match `selector_map` entries usable by `click_element`

### Integration Tests — Agent with Query Mode

**`tests/ci/test_query_mode_agent.py`**

1. **Hybrid mode task completion**: Mock LLM configured to use query→act pattern → agent completes task via queries
2. **Query-only mode task completion**: Minimal summary + query tools → agent navigates successfully
3. **Fallback to full DOM**: If `get_full_dom` is available in hybrid mode, LLM can request full serialization when queries insufficient
4. **Token reduction**: Compare total tokens used in query mode vs full DOM mode for same task → assert reduction
5. **No regression**: Same tasks pass with `query_mode=OFF` (default behavior unchanged)

---

## Step 7: Relationship to Plans 1, 2, 3

### Dependency Graph

```
Plan 1 (Benchmark) ←──measures──→ Plan 4 (Query Tools)
                                      ↑
Plan 3 (Outline)  ──summary-for──→ Plan 4 (Query Tools)
                                      ↑
Plan 2 (WebArena) ←──validates──→ Plan 4 (Query Tools)
```

| Dependency | Direction | Detail |
|---|---|---|
| Plan 3 → Plan 4 | Plan 3's outline is Plan 4's summary view | The landmark/heading summary that Plan 4's hybrid mode shows is exactly Plan 3's outline output. If Plan 3 is implemented, Plan 4 reuses it. If not, Plan 4 implements a lightweight version. |
| Plan 1 → Plan 4 | Plan 1 measures Plan 4's impact | A/B comparisons: `query_mode=OFF` vs `HYBRID` vs `QUERY_ONLY`. Token usage, step count, pass rate, action distribution. |
| Plan 2 → Plan 4 | Plan 2 provides scale validation | WebArena's 812 tasks test whether query mode holds up across diverse real-world sites. |
| Plan 4 → SLMs | Query mode enables small models | The primary SLM-enablement mechanism: a 7B model with 8K context can't fit 12K tokens of DOM, but can call `query_elements` and receive 500 tokens. |

### Combined Validation Protocol

```bash
# Tier 1: Query mode helps same model
python -m benchmarks --model gpt-4o-mini --query-mode hybrid --output reports/hybrid/
python -m benchmarks --model gpt-4o-mini --query-mode off --output reports/off/

# Tier 2: Query + small model matches full + large model
python -m benchmarks --model gpt-4o-mini --query-mode hybrid --output reports/mini_hybrid/
python -m benchmarks --model gpt-4o --query-mode off --output reports/4o_off/

# Tier 3: Query + outline + SLM
python -m benchmarks --model qwen2-7b --query-mode hybrid --outline-mode --output reports/slm/
python -m benchmarks --model gpt-4o --query-mode off --output reports/4o_baseline/

# Plan 3 + Plan 4 combined
python -m benchmarks --model gpt-4o-mini --query-mode hybrid --outline-mode --output reports/combined/
```

---

## Step 8: SLM Enablement Analysis

### Why Query Mode Is Necessary for SLMs

| Model | Context Window | Typical Page DOM Tokens | Fits? | With Query Mode? |
|---|---|---|---|---|
| GPT-4o | 128K | 8–25K | Yes | Not needed for fit, but improves accuracy |
| GPT-4o-mini | 128K | 8–25K | Yes | Reduces cost significantly |
| Claude 3.5 Haiku | 200K | 8–25K | Yes | Cost reduction |
| Qwen2.5-7B | 32K | 8–25K | Tight | Query mode makes complex pages viable |
| Llama 3.1-8B | 128K | 8–25K | Yes | Accuracy improvement |
| Phi-3-mini (3.8B) | 128K | 8–25K | Yes | Accuracy improvement (weaker reasoning) |
| Gemma-2B | 8K | 8–25K | **No** | **Query mode required** |
| Octopus v2 (2B) | 4K | 8–25K | **No** | **Query mode required** |

For sub-4B models with short context windows, query mode isn't an optimization — it's a **prerequisite**. These models physically cannot receive the full serialized DOM.

### SLM Tool-Calling Capability

Literature shows SLMs are surprisingly capable at function calling:
- **xLAM-1B**: 78.9% on Berkeley Function Calling Leaderboard (beats GPT-3.5-Turbo)
- **Octopus v2 (2B)**: 99.5% function calling accuracy, 0.38s latency
- **SLM fine-tuned 350M**: 77.6% on ToolBench (3x better than ChatGPT-CoT)

The bottleneck for SLMs is not tool calling — it's context processing. Query mode addresses exactly this bottleneck.

### Required Tool-Calling Infrastructure

For SLMs that don't support structured output via JSON schema:
1. **Function-calling format**: Many SLMs support OpenAI-compatible function calling (Gorilla, xLAM, Octopus)
2. **Regex-constrained generation**: Tools like Outlines (dottxt-ai/outlines) enforce schema compliance via regex-guided sampling
3. **Fine-tuning on query patterns**: A small dataset of (page_summary → query_tool_call → action) trajectories would be sufficient. The training signal is clear: did the query return the right element?

---

## Implementation Order

1. **`browser_use/dom/query.py`** — Query engine: pure functions over `SimplifiedNode` + `DOMSelectorMap`. Independently testable.
2. **`tests/ci/test_dom_query.py`** — Unit tests for query functions. Run, confirm they work in isolation.
3. **`browser_use/tools/views.py`** — Pydantic models for query action parameters.
4. **`browser_use/tools/service.py`** — Register four query actions. Wire to `dom/query.py`.
5. **`browser_use/tools/registry/views.py`** — Add `dom_state` to `SpecialActionParameters`.
6. **`browser_use/agent/views.py`** — Add `QueryMode` enum and `query_mode` setting.
7. **`browser_use/agent/prompts.py`** — Conditional DOM inclusion based on query_mode.
8. **`browser_use/agent/service.py`** — Pass `dom_state` to tool execution, handle query mode.
9. **`browser_use/agent/system_prompt*.md`** — Document query tools.
10. **`tests/ci/test_query_mode_agent.py`** — Integration tests with mock LLM.
11. **Run full `tests/ci` suite** — Confirm no regressions with `query_mode=OFF`.

---

## Key Design Decisions

### 1. Query tools operate on pre-extracted data, not live DOM

Unlike `search_page` and `find_elements` (which execute fresh JS via CDP), the query tools operate on the already-extracted `SerializedDOMState`. This means:
- **No redundant CDP calls** — data was already collected in the DOM extraction pipeline
- **Enrichment-aware** — queries can filter by accessibility role, paint order, visibility — all data from the enrichment pipeline
- **Index-consistent** — results use the same element indices as the main DOM serialization, so `query_elements` results feed directly into `click_element`/`input_text`

### 2. Additive, not replacing

`query_mode=OFF` (default) preserves 100% backward compatibility. The existing full-DOM-in-context behavior is unchanged. Query mode is opt-in.

### 3. Hybrid mode is the safe default for early adoption

Hybrid mode still gives the LLM a structural overview. It can reason about the page before querying. Query-only mode is more aggressive and should be validated through benchmarks before recommending.

### 4. Two-step query→act pattern, not intra-step replanning

Starting with the simpler two-step pattern (query in step N, act in step N+1) avoids changing the `multi_act` loop. The LLM naturally learns this pattern. Intra-step replanning (Solution C) is a later optimization.

### 5. Query tools complement Plan 3, not compete with it

Plan 3 (outline) restructures the serialization format. Plan 4 (query tools) restructures the access pattern. They stack:
- Plan 3 alone: better structure, same context size (minus region collapsing)
- Plan 4 alone: on-demand access, but summary view needs implementation
- Plan 3 + Plan 4: outline as summary view + query tools for drilling in = maximum benefit

---

## Risk Assessment

### Low Risk
- **Backward compatibility**: `query_mode=OFF` is default, existing tools unchanged
- **Data availability**: All query fields map to existing `EnhancedDOMTreeNode` and `ax_node` properties
- **Index consistency**: Query results use the same `selector_map` indices as full serialization
- **Test infrastructure**: Plan 1's benchmark suite measures impact

### Medium Risk
- **Query formulation quality**: LLMs must formulate good queries. If the LLM searches for "login" but the button says "Sign In", it misses the element. Mitigation: fuzzy matching, synonym expansion, accessible name matching (which often normalizes labels).
- **Extra steps overhead**: Each query adds a step. For simple tasks on small pages, this overhead exceeds the token savings. Mitigation: hybrid mode provides a summary that may suffice for simple tasks, avoiding queries entirely. The agent can also request `get_full_dom` as fallback.
- **Unfamiliar pattern for LLMs**: Current LLMs are trained on web agents that see full DOM. Query-based navigation is a less common training distribution. Mitigation: clear system prompt instructions, few-shot examples. Long-term: fine-tuning on query-mode trajectories.

### Higher Risk
- **Complex page layouts**: SPAs with flat DOM structure (everything in `<div id="root">`) may have no useful landmarks for `within_landmark` queries. Mitigation: text search and heading scoping still work; graceful degradation to role/text matching.
- **Dynamic content timing**: If the DOM changes between query and action (SPA re-render), element indices may shift. Mitigation: this is the same problem the current system has between DOM capture and action execution — the existing URL/target change detection handles it.
- **SLM query quality**: Small models may generate syntactically valid but semantically poor queries. This is the "query formulation difficulty" risk from Mind2Web (GPT-3.5 achieved only ~20% element selection accuracy). Mitigation: provide examples in system prompt; use structured fields (role, landmark) instead of free-text where possible; consider fine-tuning on query trajectories.

---

## What This Delivers

| Signal | Mechanism | Expected Impact |
|---|---|---|
| Token reduction | Only retrieve needed elements | 58–85% fewer tokens per step |
| Cost reduction | Fewer tokens × cheaper per-token | 60–80% lower per-task cost |
| Latency reduction | Smaller prompts → faster LLM inference | 30–50% faster per step (net, including extra steps) |
| Accuracy improvement | Less noise → better attention | +8 to +38 points (literature range) |
| SLM enablement | Fits within small context windows | Makes sub-4B models viable for web automation |
| Grounding precision | Query results tied to element indices | Direct bridge from query → click/type |
| Structural reasoning | LLM must articulate what it seeks | More interpretable decision traces |

## What This Does NOT Do

- Does not change CDP data collection
- Does not change the enhanced DOM tree construction
- Does not change action execution (click, type, etc.)
- Does not replace `search_page` or `find_elements` (those query the live DOM, which is useful for content not in the serialized tree)
- Does not implement fine-tuning or training data for SLMs (orthogonal concern)
- Does not handle canvas/WebGL content
- Does not implement action caching or workflow memory
