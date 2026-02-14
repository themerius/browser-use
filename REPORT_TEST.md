# Test Concept & Evaluation Analysis — browser-use

## 1. Current Test Architecture

- **Scale**: 689 test functions across 69 files, ~10K lines of test code
- **Location**: `tests/ci/` is the canonical CI suite, auto-discovered on every commit
- **Runtime**: pytest with `asyncio_mode="auto"`, 5-minute timeout per test, `--dist=loadscope` for parallel execution

- **Three-layer strategy**
  - **Real browser**: headless Chromium via `BrowserSession(BrowserProfile(headless=True))`, module-scoped (reused across tests in a module)
  - **Mocked LLM**: `AsyncMock(spec=BaseChatModel)` with closure-based action sequencing — deterministic, no API keys needed
  - **Local HTTP server**: `pytest-httpserver` serves static HTML fixtures per test — no real URLs, fully reproducible

- **Mock LLM design** (`conftest.py`)
  - Accepts a list of JSON action strings, replays them sequentially
  - Falls back to a default `done` action after the sequence is exhausted
  - Handles both raw string and structured `output_format` parsing (mimics real provider behavior)
  - No reasoning quality simulation — `thinking`, `evaluation_previous_goal`, `memory` fields are typically `"null"` or placeholder strings
  - Closure state tracks action index — tests that reuse the fixture see sequence continuation

- **What's covered**
  - Click (index + coordinate), text input, navigation, go_back, search_page, find_elements
  - Dropdown selection (native `<select>`, ARIA menus, custom JS implementations)
  - Structured extraction with JSON schemas
  - Scrolling, tab management, wait actions, screenshots
  - File handling (PDF, DOCX, images)
  - Planning with step advancement
  - Action loop detection (hash-based cycle detection)
  - Security: domain filtering, IP blocking, data sanitization
  - LLM fallback logic (primary → fallback provider switch)
  - Registry: action registration, parameter injection, validation

- **What's NOT covered**
  - Agent reasoning quality (thinking/memory/evaluation content never asserted)
  - DOM serialization accuracy (no precision/recall for element detection)
  - Token cost or efficiency tracking
  - Step-to-completion ratio
  - Partial success measurement
  - Cross-origin iframe scenarios (limited)
  - Complex JS-heavy SPA behavior
  - Extension functionality (enabled but untested)
  - Performance under load / stress testing

## 2. Assertion Philosophy: Binary Pass/Fail

- **Dominant pattern**: `assert result.success is True`, `assert 'expected' in result.extracted_content`
- **No gradual metrics**: no scores between 0 and 1, no quality ratings, no partial credit
- **No efficiency measurement**: tests don't track how many steps the agent took, only that it finished
- **Exception**: timing assertions for wait actions use 50% tolerance windows — the only "fuzzy" assertion in the suite
- **DOM verification**: uses CDP `Runtime.evaluate` to check post-action state (e.g., dropdown value changed), but doesn't measure detection accuracy
- **Implication**: the test suite can tell you "it works" or "it doesn't work", but cannot tell you "it works well" or "it's getting better/worse"

## 3. Benchmark Evaluation (`evaluate_tasks.py`)

- **Mechanism**: reads YAML task files from `tests/agent_tasks/`, runs each in a subprocess with real LLM (ChatBrowserUse)
- **Judging**: a separate LLM (Gemini Flash Lite) evaluates success against `judge_context` criteria from YAML
- **Output**: binary pass/fail per task, aggregate pass rate, debug info (steps taken, output length)
- **No multi-trial**: each task runs once — no statistical aggregation over stochastic variance
- **No efficiency tracking**: doesn't record steps, tokens, cost, or time per task
- **No partial credit**: a task either passes the judge or fails entirely
- **Task format example**:
  ```yaml
  name: Amazon Laptop Search
  task: Go to amazon.com, search for 'laptop', return first result
  judge_context:
    - The agent must navigate to amazon.com
    - The agent must search for 'laptop'
    - The agent must return name of the first laptop
  max_steps: 10
  ```
- **Key weakness**: uses live URLs (amazon.com, etc.) — non-reproducible, violates the project's own test guidelines

## 4. Accepted Standards in the Literature

- **WebArena** (ICLR 2024): the gold standard — 812 tasks across self-hosted web apps, functional correctness via programmatic checkers, binary task success rate. Human baseline: 78%, current SOTA: ~62%
  - **WebArena Verified** (NeurIPS 2025): removed LLM-as-judge entirely, deterministic scoring with type/normalization-aware comparators, JSON schema for structured results, template-level macro averages with 95% confidence intervals
- **Mind2Web** (NeurIPS 2023): three-level metrics — element accuracy, operation F1, step success rate, task success rate. Key finding: 52% step SR → 5.2% task SR — high per-step accuracy doesn't compound
- **VisualWebArena** (ACL 2024): extends WebArena for vision tasks, same functional correctness framework
- **WebVoyager** (ACL 2024): 643 tasks on 15 live websites, LLM-as-judge evaluation — widely used but heavily critiqued
- **ST-WebAgentBench** (ICML 2025): enterprise focus — Completion Under Policy (CuP), Partial CuP (pCuP), Risk Ratio across 6 safety dimensions
- **BrowserGym** (TMLR 2025): unified POMDP environment integrating MiniWoB++, WebArena, VisualWebArena, WorkArena — the emerging standard platform for cross-benchmark comparison
- **WebChoreArena** (2025): tedium-focused tasks, Gemini 2.5 Pro at only 37.8%

- **The "Illusion of Progress" critique** (COLM 2025)
  - WebVoyager scores inflated up to 59% — a naive Google Search agent solves 51% of its tasks
  - Most recent agents don't outperform SeeAct (early 2024) on diverse real-world tasks
  - 300 tasks across 136 websites (Online-Mind2Web) with WebJudge (85.7% agreement with humans) shows even Operator only reaches 61%
  - Bottom line: "We are still far away from solving web/computer use agents"

- **Benchmark contamination**: public benchmarks leak into training data — a 13B model can overfit to GPT-4-level performance on leaked test sets. WebArena Verified mitigates by using self-hosted environments and deterministic scoring

## 5. Fitness Functions: What the Literature Offers

- **The gap**: most benchmarks (including browser-use's own) use binary outcome reward — task succeeded or failed. This creates severe credit assignment problems for multi-step tasks

- **Outcome Reward Models (ORMs)**
  - **WebRL** (ICLR 2025): trains a learned ORM to replace GPT-4 as evaluator — cheaper, more reliable, but still binary (succeeded/failed). Improved Llama-3.1-8B from 4.8% → 42.4% on WebArena-Lite
  - **WebAgent-R1** (EMNLP 2025): end-to-end multi-turn RL with only binary task rewards, no separate reward model. Llama-3.1-8B → 44.8% on WebArena-Lite

- **Process Reward Models (PRMs) — the key innovation**
  - **Web-Shepherd** (NeurIPS 2025 Spotlight): first PRM for web agents — decomposes tasks into a **checklist of subgoals**, estimates per-step reward as probability distribution over "Yes/No/In Progress" per checklist item
    - Why PRMs over ORMs for web: web actions are **irreversible** (you can't un-book a ticket), so decisions must be evaluated at the process level, not just outcome
    - 30 points better accuracy than GPT-4o on WebRewardBench
    - As a verifier: 10x cheaper than GPT-4o-mini, 100x cheaper than GPT-4o
  - **WebArbiter** (2025): reasoning-first PRM producing auditable step-level judgments with rationales
  - **AgentPRM** (2025): dual signal — **promise of success** (forward-looking) + **local progress** (backward-looking) — because in agentic tasks, unlike math proofs, a step can be "correct" yet make no progress

- **DOM state as reward signal**
  - No standard approach exists yet
  - WebArena Verified validates backend state via REST API / database queries for state-changing tasks
  - Web-Shepherd uses DOM observation content as input to its PRM
  - General pattern: DOM snapshots are the **observation**, not the reward — reward comes from learned models or programmatic checkers operating on those observations

## 6. Fitness Function Assessment for browser-use

- **Current state: no fitness function exists**
  - CI tests: binary pass/fail assertions
  - Benchmark eval: binary LLM-judge pass/fail
  - No continuous quality signal anywhere in the pipeline
  - No step-level reward, no partial credit, no efficiency tracking

- **What a fitness function would need to capture** (derived from REPORT.md findings + literature)
  - **Grounding accuracy**: did the agent click/type the correct element? (Mind2Web's element accuracy metric)
  - **Step efficiency**: steps taken vs. minimum steps required (WABER's efficiency metrics)
  - **Token efficiency**: tokens consumed per task (critical for the small-model story from Section 8 of REPORT.md)
  - **Structural reasoning quality**: did the agent reason about page regions or brute-force scan? (connects to hierarchical outlining thesis)
  - **Progress per step**: is the agent getting closer to the goal? (AgentPRM's promise + progress signal)
  - **Policy compliance**: did the agent violate safety constraints? (ST-WebAgentBench's CuP metric)
  - **Representation quality**: how good was the DOM serialization? (element detection precision/recall — currently unmeasured)

- **Why this matters for the hierarchical outlining thesis** (REPORT.md §8)
  - Without a fitness function, you can't measure whether a hierarchical outline representation **actually helps** vs. the current flat serialization
  - You can't measure whether smaller models improve with better-structured input
  - You can't run the `required_model_size ≈ f(task_complexity / input_structure_quality)` experiment
  - The missing fitness function is the missing feedback loop that would turn the outlining hypothesis into engineering decisions

## 7. Gap Analysis: browser-use vs. Literature Standards

| Dimension | Literature standard | browser-use status | Gap severity |
|---|---|---|---|
| **Deterministic CI tests** | Mock LLM + real browser + local HTTP | Implemented | None |
| **Binary task success** | WebArena-style functional correctness | Partially (LLM judge, not programmatic) | Medium |
| **Multi-trial evaluation** | Run N trials, report mean ± CI | Not implemented (single run) | High |
| **Step-level metrics** | Mind2Web element accuracy + operation F1 | Not implemented | High |
| **Efficiency tracking** | WABER cost/latency/token tracking | Not implemented | High |
| **Partial credit** | Web-Shepherd checklist PRM, ST-WebAgentBench pCuP | Not implemented | High |
| **Safety/policy compliance** | ST-WebAgentBench CuP + Risk Ratio | Basic domain filtering tests only | Medium |
| **Reproducibility** | Self-hosted environments (WebArena) | CI: yes. Benchmarks: no (uses live URLs) | Medium |
| **DOM quality metrics** | Prune4Web precision/recall for element detection | Not implemented | High |
| **Benchmark contamination defense** | Private test sets, rolling refresh | Not applicable (mock LLM) | Low |
| **Regression detection** | Statistical significance across runs | Not implemented | Medium |

## 8. What Would Close the Gaps

- **Level 1 — Low effort, high value**
  - Add step counting to CI tests: assert agent finishes in ≤ N steps (not just that it finishes)
  - Add token tracking to `evaluate_tasks.py`: record prompt + completion tokens per task
  - Replace live URLs in benchmark tasks with self-hosted fixtures or at minimum mark them as `@pytest.mark.slow` / non-CI
  - Add multi-trial runs: execute each benchmark task 3–5 times, report pass rate with variance

- **Level 2 — Medium effort, structural improvement**
  - **DOM serialization quality tests**: create HTML fixtures with known interactive elements, run the serializer, assert precision/recall of element detection
    - This directly measures the data preparation pipeline (REPORT.md §2) — the most critical and least tested component
  - **Step-level grounding tests**: for each action in a scripted sequence, verify the correct element was targeted (Mind2Web-style element accuracy)
  - **Efficiency regression tests**: establish baseline step counts for standard tasks, fail if step count regresses beyond a threshold
  - **Programmatic outcome checkers**: replace LLM judge in `evaluate_tasks.py` with deterministic state checks (WebArena Verified approach)

- **Level 3 — High effort, fitness function**
  - **Checklist-based PRM** (Web-Shepherd approach): decompose each benchmark task into subgoal checklist, score progress per step
    - Enables partial credit: "agent found the search box and typed query but clicked wrong result" → 0.6 instead of 0.0
    - Enables A/B testing of representations: does hierarchical outline serialization improve checklist completion rate?
  - **Dual reward signal** (AgentPRM approach): measure both promise (is this trajectory on track?) and progress (did this step move closer to goal?)
  - **Representation quality benchmark**: compare serialization strategies (flat vs. outline vs. landmark-grouped) on element detection accuracy + task success rate + token cost, across model sizes
    - This is the experiment that would validate or refute the hierarchical outlining hypothesis from REPORT.md §8
    - Required: a controlled comparison holding everything constant except the serializer output format

## 9. Connecting to the Hierarchical Outlining Thesis

- **The missing link**: REPORT.md §8 argues that hierarchical page outlines could enable smaller models to navigate complex pages — but there's no way to measure this with the current test infrastructure
- **What you'd need to run the experiment**:
  1. A DOM serialization quality benchmark (Level 2 above): measure element detection precision/recall for both flat and outline serializers
  2. A step-efficiency metric: does the outline representation reduce steps to completion?
  3. A token-efficiency metric: does the outline reduce tokens per step?
  4. A model-size sweep: run the same tasks with 7B, 13B, 70B models under both serializations — does the outline narrow the gap?
  5. A checklist-based PRM (Level 3): does the outline improve partial progress even when tasks fail?
- **The hypothesis, made testable**: if `task_success(outline, 7B) ≥ task_success(flat, 70B)` on a non-trivial benchmark, then input structure substitutes for model scale — and the outlining approach is validated
- **Prediction from the literature**: D2Snap's finding that hierarchy is the strongest DOM feature, combined with ScribeAgent's result that a 7B model can outperform GPT-4 with structured input, suggests the hypothesis is likely to hold — but it has not been tested in browser-use's specific architecture
