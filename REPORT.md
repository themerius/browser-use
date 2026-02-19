# How LLMs Navigate the Web — Analysis of browser-use

## 1. Core Concept: How LLMs "See" Websites

- **The fundamental problem**: raw HTML is too large, noisy, and structurally complex for LLMs to reason about efficiently
- **browser-use's answer**: a multi-stage pipeline that distills a full webpage into an indexed, text-based interactive element tree — essentially an annotated accessibility tree on steroids
- **The LLM never sees raw HTML** — it sees a compressed, numbered representation like:
  ```
  [1]<button>Submit</button>
  [2]<a href="/about">About Us</a>
  [3]<input type="text" placeholder="Search..." />
  ```
- The LLM picks actions by referencing element indices (e.g., "click [2]") — reducing the grounding problem to integer selection

## 2. Data Preparation Pipeline

- **Stage 1 — CDP data collection** (parallel, ~50–150ms)
  - DOM tree (`DOM.getDocument`, depth=-1, pierces shadow DOM)
  - DOM snapshot (layout, bounding boxes, paint order, computed styles)
  - Accessibility tree (`Accessibility.getFullAXTree` per frame)
  - JS event listeners (detects React/Vue/Angular click handlers via `getEventListeners`)
  - Viewport metrics + iframe scroll positions
- **Stage 2 — Enhanced DOM tree construction**
  - Each node enriched with: DOM attributes + AX role/name/description + snapshot data (bounds, styles, clickability, paint order)
  - Multi-layer visibility check: CSS rules → viewport threshold (default 1000px off-screen) → cross-iframe intersection
  - Device pixel → CSS pixel coordinate normalization
  - Shadow DOM and cross-origin iframe recursion (configurable depth)
- **Stage 3 — Filtering and optimization**
  - Strip non-interactive/invisible nodes (scripts, styles, SVG internals, `display:none`)
  - **Paint order filtering**: remove elements visually obscured by higher-z elements (modal covers dropdown → dropdown excluded)
  - **Bounding box filtering**: remove children 99%+ contained by interactive parent (prevents link-in-link duplication)
  - **Tree pruning**: remove leaf nodes with no meaningful content
- **Stage 4 — Interactive indexing and serialization**
  - Assign sequential `[index]` to each interactive+visible element
  - Serialize to indented text with: tag, curated attributes (26 default: id, role, aria-*, placeholder, value, etc.), text content
  - Compound component detection (selects → dropdown+listbox, sliders → min/max/value, file inputs → browse button state)
  - New element markers (`*[n]`), scroll hints, hidden content hints ("3 more elements below — scroll to reveal")
  - **Output**: ~5–40K chars of structured text per page (vs ~500K+ raw HTML)

- **Is it "throw more tokens at it"?**
  - **No** — the pipeline is carefully engineered to compress. A 500K HTML page becomes ~10–20K of relevant interactive elements
  - **But also yes** — there's no aggressive token budgeting pre-request; truncation is a hard cap (40K chars), not a smart summarization; screenshots are sent as full base64 PNGs; message compaction only kicks in after ~15 steps
  - The philosophy: **make the representation good enough that frontier LLMs can reason about it without needing extreme compression**

## 3. Core Architecture Decisions

- **DOM-first, vision-optional**
  - Text-based DOM tree is the primary representation; screenshots are supplementary
  - Vision can be `True` (always), `False` (never), or `'auto'` (on-demand)
  - This is the single most consequential design choice — it determines cost, speed, and failure modes
- **Structured output over function calling**
  - LLM returns a single JSON object (thinking + evaluation + memory + next_goal + actions[])
  - Pydantic schema validation, not free-form text parsing
  - Provider-agnostic: OpenAI uses `response_format`, Anthropic uses forced tool call, Gemini uses native structured output
- **Event-driven watchdog architecture**
  - Central `bubus` event bus coordinates ~12 decoupled watchdogs (DOM, security, downloads, popups, screenshots, etc.)
  - Auto-handler registration via method naming convention (`on_EventName`)
  - Clean separation: no watchdog directly calls another; all communication via events
- **Multi-action per step**
  - LLM can return up to 5 actions per step (configurable)
  - Safety: actions marked `terminates_sequence=True` (navigate, search) abort remaining queued actions
  - Runtime guard: URL/target change detection aborts stale actions
- **Explicit reflection loop**
  - System prompt forces structured reasoning: `thinking` → `evaluation_previous_goal` → `memory` → `next_goal` → `action`
  - Memory field preserved across steps (short-term working memory)
  - Optional planning system with `PlanItem` tracking (pending/current/done/skipped)
- **Loop detection and adaptive nudging**
  - Rolling window of 20 action hashes; escalating warnings at 5/8/12 repetitions
  - Page fingerprint stagnation detection (same DOM hash for 5+ actions)
  - Replan nudges after 3 consecutive failures; exploration nudges after 5 steps without plan progress
- **Provider-agnostic LLM layer**
  - 15+ provider implementations (OpenAI, Anthropic, Google, Groq, Ollama, DeepSeek, AWS Bedrock, etc.)
  - Common `BaseChatModel` protocol with `ainvoke(messages, output_format)`
  - Automatic fallback LLM on rate limits

## 4. Evaluation of Architecture Decisions

- **Strengths**
  - **Token efficiency**: DOM serialization is 10–50x smaller than raw HTML, and 5–10x cheaper than sending screenshots to vision models
  - **Speed**: DOM extraction (~100–300ms) is faster than screenshot rendering + vision inference
  - **Deterministic grounding**: element indices provide unambiguous click targets (vs. pixel coordinate estimation in vision-only agents)
  - **Compound component detection**: smart handling of selects, sliders, file inputs — avoids common LLM confusion points
  - **Paint order awareness**: a genuinely sophisticated optimization that most competitors lack — correctly handles overlapping elements, modals, dropdowns
  - **Provider flexibility**: swapping LLMs is trivial; no vendor lock-in
  - **Event-driven decoupling**: watchdog system makes it straightforward to add new capabilities without touching core loop
  - **Reflection forcing**: structured thinking/evaluation/memory fields measurably improve multi-step task completion
- **Weaknesses**
  - **Visually-rendered-only content is invisible**: canvas elements, WebGL, complex CSS-only visual states, image-embedded text — all missed by DOM parsing
  - **No proactive token management**: no pre-request token counting; relies on hard char truncation + provider context limits. On complex pages this can silently lose elements
  - **Screenshot handling is naive**: full base64 PNG sent without smart cropping, region-of-interest extraction, or resolution optimization based on task
  - **Message compaction uses an LLM call**: summarizing history costs additional tokens and latency — a design that burns money to save context window
  - **No action caching or learning**: every page visit starts from scratch; no reuse of previously successful action sequences
  - **Cross-origin iframe support is limited**: disabled by default, recursive, and expensive when enabled
  - **Accessibility tree quality varies**: sites with poor semantic HTML produce poor element representations — garbage in, garbage out

## 5. Comparison with Similar Solutions

| Dimension | browser-use | Stagehand | Skyvern | Playwright MCP | Anthropic Computer Use |
|---|---|---|---|---|---|
| **Primary input** | DOM tree (text) | DOM tree (text) | Screenshots (vision) | Accessibility tree | Screenshots (vision) |
| **Agent autonomy** | Fully autonomous | Developer-scripted with NL primitives | Fully autonomous (YAML workflows) | Developer-scripted | Fully autonomous |
| **Language** | Python | TypeScript | Python | TypeScript | Python |
| **Cost per step** | Low (text tokens) | Low (text tokens) | High (~$0.05/step + vision tokens) | Low (text tokens) | High (vision tokens) |
| **Dynamic UI handling** | Good (JS listener detection) | Good (self-healing selectors) | Best (vision-based, UI-agnostic) | Moderate (AX tree only) | Best (raw pixel reasoning) |
| **Canvas/WebGL** | Blind | Blind | Handles via vision | Blind | Handles via vision |
| **Action caching** | None | Yes (auto-cache + replay) | None | None | None |
| **Enterprise features** | Basic security model | None | CAPTCHA/2FA, proxy rotation | None | Sandboxed VM |
| **GitHub stars** | ~70–78K | ~19K | ~20K | Part of MS ecosystem | Part of Anthropic API |
| **Backing** | YC W25, $17M | Browserbase | YC | Microsoft | Anthropic |

- **Stagehand** is not a competing agent but a framework — it adds `act()`, `extract()`, `observe()` NL primitives to Playwright scripts. Its self-healing selector caching is a feature browser-use lacks.
- **Skyvern** is the closest autonomous competitor. Its vision-first approach handles non-standard UIs better but costs 10–20x more per step. It includes enterprise features (CAPTCHA, 2FA) that browser-use doesn't.
- **Playwright MCP** targets a different use case (AI-assisted testing, not autonomous browsing) but its accessibility-tree approach is architecturally similar to browser-use's DOM serialization.
- **Anthropic Computer Use / OpenAI CUA** operate at the OS level (screenshot + mouse/keyboard), making them more general but far less efficient for web-specific tasks.

## 6. Benchmarks (State of the Art, early 2026)

- **WebArena** (realistic web tasks): SOTA ~62% (IBM CUGA), up from 14% in 2023. Human baseline: 78%
- **WebVoyager** (live web navigation): browser-use reports 89.1%, Skyvern 2.0 reports 85.8% — but dataset/methodology differences make direct comparison unreliable
- **WebChoreArena** (tedium-heavy tasks): even Gemini 2.5 Pro only achieves 37.8%
- **Key insight from COLM 2025 "Illusion of Progress" paper**: high benchmark scores don't yet translate to reliable real-world automation on diverse, live websites
- **Remaining gap**: deep visual understanding, common-sense reasoning, policy compliance

## 7. Parallel to Screen Reader Navigation

- **The deep structural analogy**
  - Screen readers and LLM web agents solve the same fundamental problem: making websites navigable without relying on visual rendering
  - Both consume the **accessibility tree** as their primary representation — screen readers via platform APIs (MSAA/UIA, ATK, NSAccessibility), browser-use via CDP's `Accessibility.getFullAXTree`
  - Both read the same four properties per element: **role**, **name**, **state**, **value**
  - Both struggle with the same failure modes: non-semantic HTML, missing ARIA, canvas/WebGL, CAPTCHAs

- **How screen readers represent a page**
  - Build a **virtual buffer** (linearized text document) from the accessibility tree
  - Each element announced as: role → name → state → value (e.g., "Heading level 2, Product Features")
  - **Landmark roles** provide structural scaffolding: `<main>`, `<nav>`, `<aside>`, `<footer>` → direct jump targets
  - **Three interaction modes**: browse mode (virtual cursor, read-only), focus mode (typing into form fields), application mode (custom widget keyboard handling)

- **What browser-use shares with screen readers**
  - Both filter/compress thousands of DOM nodes into a navigable subset
  - Both face the "what's interactive?" detection problem — a `<div onclick="...">` without `role="button"` is invisible to both
  - Both degrade on poorly-structured HTML: no headings → no structural outline; no landmarks → no region-based navigation
  - Both are blind to canvas/WebGL content, custom widgets without ARIA, and visual-only state indicators
  - WCAG compliance benefits both equally — semantic HTML is the shared API

- **Where they diverge**
  - **Navigation model**: screen readers offer ~20 single-key shortcuts (H=heading, D=landmark, F=form field, K=link, T=table, B=button) for O(1) structural traversal; LLM agents have no such primitives — they scan the full serialized representation every step
  - **Sequential vs. snapshot**: screen readers present content one element at a time (~150 words/min via speech); LLM agents receive the entire page state in a single text block
  - **Human-driven vs. autonomous**: screen readers are instruments that amplify human agency; LLM agents are both the perceiver and the decision-maker
  - **Dynamic content**: screen readers handle updates via ARIA live regions (`aria-live="polite"` / `"assertive"`); LLM agents re-capture the entire page state — no incremental update mechanism

- **What LLM agents could learn from screen reader design**
  - **Landmark-based skip navigation**: collapse repeated nav/header/footer regions between steps instead of re-serializing them ("NAV: 12 links, same as previous step")
  - **Heading hierarchy as table of contents**: present a structural outline at the top of each snapshot before the full element list
  - **Browse/focus mode split**: two-phase approach — first a compressed structural overview (~500 tokens), then detailed view of a specific region on demand. Would dramatically reduce per-step token consumption
  - **Live region semantics**: use `aria-live` and `aria-busy` to detect when content is still loading or has changed, rather than blindly re-snapshotting
  - **Richer state announcements**: more consistent exposure of `aria-invalid`, `aria-busy`, `aria-errormessage` to help agents understand form validation failures and loading states

- **The curb cut effect**
  - Accessibility improvements designed for disabled users directly benefit AI agents — and vice versa
  - WCAG-compliant sites show ~23% higher organic traffic (SEMrush), partly because crawlers and AI agents reward clean semantic structure
  - Agents using accessibility tree data complete tasks at ~85% success rate vs. significantly lower for vision-only approaches (Agent-E benchmark data)
  - Emerging term: **AIO (Artificial Intelligence Optimization)** — the recognition that accessibility work is simultaneously AI agent infrastructure
  - LLMs are also being used to *improve* accessibility (arXiv 2502.18701): restructuring HTML for better heading hierarchy and labeling, which benefits both screen readers and agents

## 8. Hierarchical Outlining as a Navigation Primitive

- **The cognitive science foundation**
  - Miller's "magical number 7±2" (1956, revised to ~4 for novel items): working memory holds a fixed number of **chunks**, not individual items
  - Hierarchical structure enables recursive chunking — 5 landmarks × 5 sections × 5 elements = 125 items navigable through three levels of ~5 choices each, instead of one flat list of 125
  - Cognitive load theory: hierarchical representations reduce extraneous load by providing structure "for free" — the outline itself is a navigation aid, not just a container
  - Key property: a broken link in a linear chain makes all subsequent items inaccessible; in a hierarchy, higher-level nodes maintain access to all subtrees even if one branch fails

- **Why hierarchy matters more than compression alone**
  - **D2Snap** (DOM Downsampling for Web Agents, 2025): tested three DOM features and found **hierarchy is the strongest feature** — element extraction (flattening) that discards parent-child relationships performs worse than downsampled trees that preserve them
  - **UIFormer**: warns explicitly that "flattening approaches discard parent-child relationships essential for understanding UI structure" — even when flattening reduces token count, it hurts accuracy
  - **Prune4Web** (2025): achieved **25–50x reduction** in candidate elements with accuracy jumping from 46.8% → 88.3% through structured tree pruning — proving that structured compression beats brute-force truncation
  - The distinction: browser-use already compresses (500K HTML → 10–20K text), but its serialization partially flattens the hierarchy into an indexed list with indentation hints rather than explicit structural grouping

- **What a hierarchical outline representation would look like**
  - Current browser-use output (simplified):
    ```
    [1]<a>Home</a>
    [2]<a>Products</a>
    [3]<a>Contact</a>
    [4]<input placeholder="Search..." />
    [5]<h2>Featured Products</h2>
    [6]<button>Add to Cart</button>
    [7]<button>Add to Cart</button>
    [8]<a>Next Page</a>
    ```
  - Hypothetical outline representation:
    ```
    NAV (banner):
      [1]<a>Home</a>  [2]<a>Products</a>  [3]<a>Contact</a>
    NAV (search):
      [4]<input placeholder="Search..." />
    MAIN:
      ## Featured Products
        Product 1: [6]<button>Add to Cart</button>
        Product 2: [7]<button>Add to Cart</button>
      [8]<a>Next Page</a>
    ```
  - The outline version: same elements, same indices, but **the LLM knows where things are** — "Add to Cart" is in MAIN under "Featured Products", not just element [6] somewhere in a list
  - Enables region-level reasoning: "I need to search → go to NAV (search)" without scanning all elements

- **Impact on structured reasoning**
  - **"Read Before You Think"** (2025): demonstrated that "a structured reading process is more fundamental than a structured reasoning process" — if the model can't parse the input structure, no chain-of-thought will compensate
  - Tree-of-Thought (Yao et al., 2023): hierarchical reasoning outperforms linear chain-of-thought across model sizes; on creative writing, GPT-3.5 + ToT matches GPT-4 + CoT — **structured reasoning can compensate for model scale**
  - Natural isomorphism between page outline levels and action planning levels:
    - Landmark level → "which page region?" (navigation, main content, sidebar)
    - Section/heading level → "which section?" (product list, checkout form, reviews)
    - Element level → "which specific control?" (this button, that input field)
  - **HEAP** (Hierarchical Policies for Web Actions, NeurIPS 2024) and **Agent-E** (two-tier planner/navigator) both achieve SOTA with this exact decomposition — the hierarchy is not cosmetic, it's architecturally load-bearing

- **The small-model enabler**
  - Small models benefit **disproportionately** from structured input:
    - **ScribeAgent** (Qwen2 7B, fine-tuned on structured web data): 51.3% on WebArena, surpassing GPT-4's score — a 7B model outperforming a >1T parameter model
    - **YAML outperforms XML by 17.7 percentage points** on small models (GPT-5 Nano, Gemini Flash Lite) — token-efficient structure matters more as model size decreases
    - **Phi model family** (1–3B parameters): achieves performance competitive with 10x larger models when trained on structured "textbook-style" data
    - NVIDIA research (2025): SLMs (1–10B) can match last-generation LLMs on tool-use benchmarks at 10–30x lower cost when inputs are well-structured
  - The scaling law implication: `required_model_size ≈ f(task_complexity / input_structure_quality)` — better-structured input reduces apparent task complexity, shifting the frontier toward smaller models
  - **Concrete prediction**: a hierarchical page outline could enable 7B-class models to navigate pages that currently require GPT-4, because the outline eliminates the need for the model to infer page structure from a flat element list

- **Format efficiency**
  - Markdown uses ~15% fewer tokens than JSON for equivalent structured data
  - XML requires ~80% more tokens than Markdown (~2x inference cost)
  - Tab-indented outlines (which browser-use already partially uses) align with the most token-efficient structured format
  - GPT-4 achieves 81.2% accuracy with Markdown prompts vs. 73.9% with JSON on reasoning tasks — a 7.3pp gap from format alone
  - The outline format is simultaneously the most human-readable, the most token-efficient, and the best-performing for LLM reasoning

- **What browser-use would need to change**
  - The serializer (`dom/serializer/serializer.py`) already preserves tree depth via indentation — the structural information exists but isn't semantically labeled
  - Missing: explicit landmark grouping headers (NAV, MAIN, ASIDE, FOOTER) derived from the accessibility tree's landmark roles (already available in `EnhancedAXNode`)
  - Missing: heading hierarchy extraction as a table-of-contents preamble before the full element list
  - Missing: region-level collapsing for repeated/unchanged structural sections between steps (e.g., "NAV: unchanged, 12 links" instead of re-serializing all navigation elements)
  - These are serializer-level changes — no architectural overhaul needed, the data is already collected

## 9. Field Outlook (2026 and Beyond)

- **Hybrid DOM+vision is converging as the winning approach**
  - Pure text misses visual context; pure vision is too expensive and imprecise for grounding
  - Emerging: "DOM downsampling" augmented with scoped screenshots for specific regions
  - OmniParser (Microsoft) demonstrates vision→structured-representation pipelines can match DOM quality
- **Agent-first web design is emerging**
  - W3C discussions on WebMCP: giving site developers control over how agents interact with their pages
  - Shift from SEO to AEO (Answer Engine Optimization) as agents mediate information retrieval
- **Multi-agent orchestration replacing monolithic agents**
  - 1,445% surge in multi-agent inquiries (Q1 2024 → Q2 2025)
  - Specialized sub-agents (navigator, extractor, validator) coordinated by an orchestrator
- **MCP as universal integration standard**
  - Donated to Linux Foundation (Dec 2025), backed by Anthropic + OpenAI + Google
  - Becoming the "USB-C of AI" for tool/service integration
- **Action caching and learned workflows**
  - Agent Workflow Memory (+51% on WebArena), SkillWeaver (+31.8%)
  - The biggest unexploited opportunity for browser-use: reusing successful action sequences
- **Specialized grounding models replacing expensive frontier-LLM-per-screenshot**
  - OmniParser V2: 60% latency reduction with YOLO+Florence-2
  - Trend toward small, fast specialist models for element detection/description
- **Task complexity doubling every ~7 months** (METR data)
  - Current: ~1 hour human tasks reliably automated
  - Late 2026 projection: 8+ hour autonomous workstreams
- **Security is the unsolved problem**
  - Prompt injection via web content, tool permission escalation, visual dark pattern susceptibility
  - Enterprise adoption blocked until governance/audit tooling matures

## 10. Tool-Mediated DOM Querying: Let the LLM Search Instead of Read

- **The problem with "dump everything into context"**
  - browser-use currently serializes the full interactive DOM into every prompt — 5–40K characters (~3–15K tokens) per step
  - Most tasks at any given step need information from a small fraction of the page: "find the login button" requires ~1 element, not all 250
  - The LLM must attend to the full serialized DOM even when 95% of it is irrelevant to the current action
  - For small language models (SLMs) with 4–8K context windows, the full DOM physically cannot fit, making them unable to operate at all

- **The proposal: query tools over pre-extracted data**
  - Instead of (or in addition to) dumping the full serialized DOM, give the LLM tool-call functions that query the already-extracted DOM data: `query_elements(role="button", text="Submit")`, `get_page_summary()`, `get_region(landmark="MAIN")`, `get_element_details(index=47)`
  - These tools operate on the pre-extracted `SerializedDOMState` and `SimplifiedNode` tree — no redundant CDP calls, full access to accessibility enrichments, element indices consistent with click/type actions
  - The LLM receives a compact page summary (landmark structure, heading hierarchy, element counts) and drills into specific regions on demand
  - This is conceptually a **RAG pattern applied to DOM navigation** — retrieve relevant elements instead of reading everything

- **Literature support — with extractable implementation patterns**
  - **Prune4Web** (arXiv:2511.21398, Nov 2025): Programmatic DOM pruning improved grounding accuracy from 46.8% to 88.3%. The key mechanism: the LLM generates a `keyword_weights` dictionary (not arbitrary code), which feeds a fixed scoring template with **three-tier attribute matching** (visible text > aria/accessibility attrs > class/id) × **four match types** (exact > phrase > word > fuzzy via `rapidfuzz`) + `PorterStemmer` normalization. Achieves 97.6% Recall@20.
  - **Mind2Web / MindAct** (NeurIPS 2023 Spotlight): The two-stage filter→select pipeline uses a DeBERTa cross-encoder (~86M params) to rank all DOM elements against a task query, then presents **top candidates in batches of 5 as multi-choice questions** via a single-elimination tournament. The cross-encoder scores `[element_repr, query]` pairs where element_repr includes ancestor path + subtree with only salient attributes (aria-label, role, placeholder, etc.). Recall@50 ≈ 85%.
  - **Agent-E** (Emergence AI, arXiv:2407.13032): Two-tier planner/navigator architecture where the planner **never sees raw DOM** (only natural language success/failure reports). The navigator chooses between three **adaptive DOM representation modes**: `text_only` (for reading), `input_fields` (for form interaction), `all_fields` (for layout understanding). Achieves 73.2% on WebVoyager.
  - **MEM1** (arXiv:2506.15841, Jun 2025, NeurIPS Workshop Oral): Uses **architectural constraint (hard context truncation) + outcome-only RL** to teach a 7B model what to keep in a constant-size internal state. 3.5x performance, 3.7x memory reduction vs. full-context 14B baseline.
  - **Octopus v2** (arXiv:2404.01744): Adds **functional tokens** (new vocabulary entries representing functions) to a 2B model. Function selection becomes single-token classification. 99.5% accuracy, 0.38s latency, 95% context reduction. Training requires only 500–1000 examples per function.
  - **Salesforce xLAM / APIGen**: Three-stage training data verification — format check → actual execution → LLM semantic judge. A 1B model trained on 60K verified examples achieves 78.9% on Berkeley Function Calling Leaderboard, beating GPT-3.5-Turbo.
  - **ScribeAgent** (arXiv:2411.15004): Includes mandatory **natural language rationale** in output format (Description field). This CoT-in-training-data approach lets a 32B model outperform o1-preview on Mind2Web.
  - **LASER** (NeurIPS 2023): Models web navigation as a **finite state machine** with state-constrained action spaces. Invalid actions are structurally impossible per state, eliminating entire error categories.
  - **Plan-and-Act / Narada Operator** (ICML 2025): Separating planner from executor reaches 64.16% on WebArena — vs. 14% for vanilla ReAct. The planner handles strategy; the executor handles DOM grounding. This is the emerging consensus architecture.

- **Why this is distinct from existing browser-use tools**
  - `search_page` and `find_elements` already exist as query tools — but they execute fresh JavaScript via CDP `Runtime.evaluate`, bypassing the pre-extracted DOM data entirely
  - They don't have access to accessibility tree enrichments (roles, names, states), paint order analysis, or bounding box data from the extraction pipeline
  - Their results don't reference element indices that the agent uses for click/type actions — creating a grounding gap
  - The proposed query tools operate on the *already-extracted* data, preserving all enrichments and maintaining index consistency

- **Three operating modes (progressive adoption)**
  - **OFF** (default): Full serialized DOM in context every step — current behavior, 100% backward compatible
  - **HYBRID**: Compact page summary (~500–1500 tokens) in context + query tools available for drilling into specific regions. Recommended starting point.
  - **QUERY_ONLY**: Minimal page metadata (~100–300 tokens) + all element access via query tools. Maximum token savings, ideal for SLMs with small context windows. Higher risk if query formulation is poor.

- **Estimated impact**

  | Metric | Full DOM (current) | Hybrid Mode | Query-Only Mode |
  |---|---|---|---|
  | Tokens per step (250-element page) | ~12,000 | ~2,500 | ~1,100 |
  | Token reduction | — | ~58–64% | ~75–85% |
  | Steps for "find and click button" | 1 | 2 (query + act) | 2–3 |
  | Net token cost per task | Baseline | ~40–60% lower | ~60–80% lower |
  | SLM viable (sub-4B, 4–8K context) | No | Marginal | Yes |

- **SLM enablement is the strongest argument**
  - For frontier models (128K+ context), query mode is an optimization — useful but not essential
  - For SLMs (1–7B parameters, 4–32K context), query mode is a **prerequisite** — the full DOM simply doesn't fit
  - SLMs are surprisingly capable at function calling: xLAM-1B achieves 78.9% on Berkeley Function Calling Leaderboard (beating GPT-3.5-Turbo), Octopus v2 (2B) achieves 99.5% at 0.38s latency
  - The scaling law from §8 extends: `required_model_size ≈ f(task_complexity / (input_structure_quality × data_access_efficiency))` — better query tools reduce effective task complexity further than structure alone
  - **Concrete prediction**: query mode + Plan 3 outline could enable sub-3B models to navigate pages that currently require GPT-4o-mini, because the combined approach eliminates both the need to infer page structure (outline) and the need to process irrelevant elements (query filtering)

- **Relationship to Plan 3 (Hierarchical Outline)**
  - Plan 3 restructures *how* the DOM is presented (flat list → hierarchical outline). Plan 4 restructures *when and how much* DOM is presented (all-at-once → on-demand via tool calls).
  - They are **complementary, not competing**: Plan 3's outline serves as the compact summary in Plan 4's hybrid mode; Plan 4's query tools provide the drill-down mechanism that Plan 3's `focus_region` action hints at
  - Combined: the LLM sees a landmark-structured outline, then uses `query_elements` or `get_region` to access specific sections — the screen reader analogy taken to its logical conclusion
  - Individual validation: each plan has independent value. Plan 3 alone improves structure. Plan 4 alone reduces tokens. Together they multiply.

- **Key risks — with literature-informed mitigations**
  - **Query formulation quality**: If the button says "Sign In" but the LLM searches for "login", it misses. Mitigation: Prune4Web's tiered scoring with `rapidfuzz` fuzzy matching + `PorterStemmer` normalization catches these variants (97.6% Recall@20). Additionally, matching against `ax_node.name` (accessible name) normalizes labels across sites.
  - **Extra step overhead**: Each query adds an LLM round-trip. Mitigation: hybrid mode provides a structural summary sufficient for simple tasks without querying. LASER's state-machine pattern suggests nudging the natural flow (summary → query → act) without hard constraints.
  - **Unfamiliar pattern**: Current LLMs are trained on agents that see full DOM. Mitigation: APIGen's three-stage verification (format → execution → semantic) produces high-quality training data. ScribeAgent showed including rationales in training teaches models to reason about queries. Octopus v2's functional tokens approach could reduce the query interface to single-token classification for fine-tuned SLMs.
  - **Poor landmark structure**: SPAs with flat DOM may lack landmarks. Mitigation: text/role search and heading scoping still work. Agent-E's adaptive content-type modes (`text_only`, `input_fields`, `all_fields`) provide fallback representations when landmarks are absent.

- **Implementation feasibility**
  - The core query engine is ~200 lines of pure functions operating on `SimplifiedNode` + `DOMSelectorMap` — all data already extracted by the existing pipeline
  - Query actions register via the existing `@tools.registry.action()` decorator — same mechanism as all built-in actions
  - `dom_state` injection into tools uses the existing `SpecialActionParameters` mechanism
  - The agent prompt changes are conditional on `query_mode` setting — zero impact when disabled
  - No changes to CDP data collection, DOM extraction, or action execution
