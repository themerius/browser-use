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

## 7. Field Outlook (2026 and Beyond)

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
