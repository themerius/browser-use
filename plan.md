# Implementation Plan: Level 0 Benchmark Suite

## Goal

Build a dedicated benchmark suite that answers **"how well does it work?"** — distinct from the CI suite which answers **"does it still work?"**. The benchmark harvests metrics already tracked by `AgentHistoryList` and `UsageSummary` but currently never aggregated: step count, token usage, cost, duration, error rate, and action distribution.

The suite lives in `benchmarks/` alongside `tests/`, uses reproducible local HTTP fixtures (no live URLs), supports multi-trial runs for stochastic variance, and outputs a structured report with baseline comparison for regression detection.

---

## Architecture

```
benchmarks/
├── __init__.py
├── runner.py          # Orchestrator: loads tasks, runs trials, collects histories
├── metrics.py         # Extracts metrics from AgentHistoryList into structured data
├── report.py          # Generates Markdown + JSON reports from collected metrics
├── baseline.py        # Stores/loads/compares baseline metrics
├── conftest.py        # Shared pytest fixtures (browser session, HTTP servers)
├── tasks/             # Benchmark task definitions (YAML)
│   ├── search_product.yaml
│   ├── fill_form.yaml
│   ├── navigate_multi_page.yaml
│   ├── extract_structured_data.yaml
│   └── dropdown_interaction.yaml
├── fixtures/          # HTML fixture templates for pytest-httpserver
│   └── __init__.py    # Functions returning HTML strings for each task scenario
├── baselines/         # Stored baseline JSON files (one per model)
│   └── .gitkeep
└── reports/           # Generated reports (gitignored)
    └── .gitkeep
```

---

## Step-by-Step Implementation

### Step 1: Create `benchmarks/metrics.py` — Metric Extraction

**What**: A `TaskRunMetrics` pydantic model and an `extract_metrics(history: AgentHistoryList) -> TaskRunMetrics` function.

**Fields extracted from existing APIs** (zero new instrumentation):

| Metric | Source | API |
|--------|--------|-----|
| `steps` | `AgentHistoryList` | `history.number_of_steps()` |
| `total_tokens` | `UsageSummary` | `history.usage.total_tokens` (if not None, else 0) |
| `prompt_tokens` | `UsageSummary` | `history.usage.total_prompt_tokens` |
| `completion_tokens` | `UsageSummary` | `history.usage.total_completion_tokens` |
| `cached_tokens` | `UsageSummary` | `history.usage.total_prompt_cached_tokens` |
| `total_cost` | `UsageSummary` | `history.usage.total_cost` |
| `duration_seconds` | `AgentHistoryList` | `history.total_duration_seconds()` |
| `success` | `AgentHistoryList` | `history.is_successful()` |
| `is_done` | `AgentHistoryList` | `history.is_done()` |
| `error_count` | `AgentHistoryList` | `len([e for e in history.errors() if e is not None])` |
| `action_names` | `AgentHistoryList` | `history.action_names()` |
| `action_distribution` | derived | `Counter(history.action_names())` |
| `urls_visited` | `AgentHistoryList` | `history.urls()` |
| `final_result` | `AgentHistoryList` | `history.final_result()` |

**Aggregate model** `TaskAggregateMetrics`: computed over N trials of the same task.

| Aggregate | Computation |
|-----------|-------------|
| `pass_rate` | `sum(success) / n_trials` |
| `avg_steps` | `mean(steps)` |
| `std_steps` | `stdev(steps)` |
| `avg_tokens` | `mean(total_tokens)` |
| `std_tokens` | `stdev(total_tokens)` |
| `avg_cost` | `mean(total_cost)` |
| `avg_duration` | `mean(duration_seconds)` |
| `avg_error_rate` | `mean(error_count / steps)` for each trial |
| `action_distribution` | merged Counter across all trials |

**Pydantic models** with `model_config = ConfigDict(extra='forbid')`. Use `from statistics import mean, stdev`.

---

### Step 2: Create `benchmarks/fixtures/__init__.py` — HTML Fixtures

**What**: Functions that return HTML strings for each benchmark scenario, served by `pytest-httpserver`. No live URLs.

**Scenarios** (start with 5, designed to exercise different agent capabilities):

1. **`search_product()`** — E-commerce product search page
   - Index page with a search input + submit button
   - Results page with 3 product cards (name, price, "Add to Cart" button)
   - Tests: navigation, text input, form submission, element identification

2. **`fill_form()`** — Multi-field contact form
   - Fields: name, email, message textarea, submit button
   - Confirmation page after POST
   - Tests: multi-field text input, form submission, result verification

3. **`navigate_multi_page()`** — Three-page navigation chain
   - Page 1 → link to Page 2 → link to Page 3 (which contains target info)
   - Tests: sequential navigation, link clicking, multi-step traversal

4. **`extract_structured_data()`** — Table with structured data
   - HTML table with 5 rows × 3 columns (product, price, stock)
   - Tests: structured data extraction, table parsing

5. **`dropdown_interaction()`** — Form with native `<select>` dropdown
   - Select element with 4 options, submit button, confirmation page
   - Tests: dropdown detection, option selection, compound component handling

Each function returns a `dict[str, str]` mapping URL paths to HTML content, so the runner can register them with httpserver.

---

### Step 3: Create benchmark task YAML format — `benchmarks/tasks/*.yaml`

**Extended schema** (superset of existing `tests/agent_tasks/` format):

```yaml
name: Search Product                     # Human-readable name
task: |                                  # Agent instruction (same as existing)
  Navigate to the store, search for "laptop",
  and return the name and price of the first result.
fixture: search_product                  # Key into fixtures module
max_steps: 10                            # Step limit
expected_result:                         # Programmatic success criteria (replaces LLM judge)
  contains:
    - "laptop"                           # final_result must contain these strings
    - "$"
  max_steps: 8                           # Efficiency ceiling (optional)
judge_context:                           # Kept for optional LLM judge comparison
  - The agent must search for 'laptop'
  - The agent must return the name and price
```

**Key design decision**: `expected_result.contains` enables **deterministic programmatic evaluation** (WebArena Verified approach) instead of relying on an LLM judge. The LLM judge fields are kept optional for comparison experiments, but the primary success signal is programmatic.

---

### Step 4: Create `benchmarks/runner.py` — Benchmark Orchestrator

**Interface**:
```
python -m benchmarks.runner [--model MODEL] [--trials N] [--tasks GLOB] [--output DIR]
```

**Defaults**: `--model mock` (uses mock LLM from conftest), `--trials 3`, `--tasks benchmarks/tasks/*.yaml`, `--output benchmarks/reports/`

**Flow**:
1. Parse args, load task YAML files matching `--tasks` glob
2. For each task:
   a. Look up the fixture function from `benchmarks.fixtures` using the `fixture` key
   b. Start a `pytest-httpserver` (or use `aiohttp` test server), register the fixture HTML
   c. Create `BrowserSession(BrowserProfile(headless=True))`
   d. Create `Agent(task=task_str, llm=llm, browser_session=session)`
   e. Run `agent.run(max_steps=max_steps)` → get `AgentHistoryList`
   f. Extract `TaskRunMetrics` via `metrics.extract_metrics(history)`
   g. Evaluate programmatic success: check `expected_result.contains` against `history.final_result()`
   h. Repeat steps c–g for `--trials` iterations
   i. Aggregate into `TaskAggregateMetrics`
   j. Cleanup: `await session.kill()`
3. Collect all `TaskAggregateMetrics`, compute suite-wide aggregates
4. Load baseline (if exists) for comparison
5. Generate report via `report.generate_report()`

**Mock LLM mode**: For deterministic benchmarking without API keys, the runner supports `--model mock`. In this mode, each task YAML includes an optional `mock_actions` field — a list of JSON action strings (same format as `conftest.create_mock_llm`). This enables fully reproducible, zero-cost runs that still exercise the full browser + DOM pipeline.

**Real LLM mode**: When `--model` is set to a real model name (e.g., `gpt-4o-mini`), the runner instantiates the corresponding `BaseChatModel` implementation. This mode captures stochastic variance across trials and real token/cost metrics.

**Error handling**: Each trial is wrapped in try/except. Failures are recorded as `TaskRunMetrics(success=False, error_count=1, ...)` rather than crashing the suite. The runner continues to the next trial/task.

---

### Step 5: Create `benchmarks/baseline.py` — Baseline Storage & Comparison

**What**: Store benchmark results as JSON baselines, compare new runs against them.

**Baseline file format** (`benchmarks/baselines/{model_name}.json`):
```json
{
  "timestamp": "2026-02-16T14:30:00Z",
  "model": "gpt-4o-mini",
  "trials_per_task": 3,
  "tasks": {
    "search_product": {
      "pass_rate": 1.0,
      "avg_steps": 4.3,
      "avg_tokens": 12841,
      "avg_cost": 0.0032,
      "avg_duration": 8.2
    }
  },
  "aggregate": {
    "pass_rate": 0.667,
    "avg_steps": 6.7,
    "avg_tokens": 20867,
    "avg_cost": 0.0052
  }
}
```

**Comparison logic**:
- `load_baseline(model: str) -> dict | None`
- `save_baseline(model: str, results: dict) -> None`
- `compare(current: dict, baseline: dict) -> dict` — returns per-metric deltas with direction indicators (improved/regressed/unchanged)
- Optional threshold-based regression flag: if `avg_steps` increases >20% or `pass_rate` drops >10pp, emit a warning

---

### Step 6: Create `benchmarks/report.py` — Report Generation

**Output format**: Markdown table + JSON sidecar.

**Markdown report** (written to `benchmarks/reports/report_{timestamp}.md`):
```
## browser-use Benchmark Report — 2026-02-16

Model: gpt-4o-mini | Trials per task: 3

| Task                      | Pass Rate | Avg Steps | Avg Tokens | Avg Cost  | Avg Duration |
|---------------------------|-----------|-----------|------------|-----------|--------------|
| search_product            | 3/3       | 4.3       | 12,841     | $0.0032   | 8.2s         |
| fill_form                 | 2/3       | 6.7       | 18,203     | $0.0045   | 12.1s        |
| navigate_multi_page       | 3/3       | 3.0       | 8,922      | $0.0022   | 5.4s         |
| extract_structured_data   | 2/3       | 5.3       | 15,100     | $0.0038   | 9.8s         |
| dropdown_interaction      | 1/3       | 9.0       | 31,556     | $0.0079   | 21.4s        |

### Aggregate
| Metric     | Current | Baseline | Delta    |
|------------|---------|----------|----------|
| Pass Rate  | 73.3%   | 66.7%    | +6.7pp   |
| Avg Steps  | 5.7     | 6.2      | -0.5     |
| Avg Tokens | 17,324  | 18,100   | -776     |
| Avg Cost   | $0.0043 | $0.0048  | -$0.0005 |

### Action Distribution
click: 45% | type: 25% | navigate: 15% | done: 10% | scroll: 5%
```

**JSON report** (written alongside, for programmatic consumption):
Same data as markdown but structured as JSON with full per-trial breakdowns.

---

### Step 7: Create `benchmarks/conftest.py` — Shared Fixtures

**Reuse from `tests/ci/conftest.py`**:
- `create_mock_llm(actions)` — import directly
- `BrowserSession` / `BrowserProfile` setup pattern

**New fixtures**:
- `benchmark_httpserver()` — a function-scoped HTTP server factory that takes a fixture dict and registers all routes
- `benchmark_browser_session()` — per-task browser session (not module-scoped like CI; each task gets a clean session)

---

### Step 8: Write 5 initial benchmark tasks

Each task YAML includes:
- `name`, `task`, `fixture`, `max_steps`, `expected_result`
- `mock_actions` — deterministic action sequence for mock LLM mode

The `mock_actions` field enables the suite to run end-to-end (real browser, real DOM pipeline, mock LLM) without API keys, making it suitable for CI-adjacent use without the flakiness of stochastic LLM responses.

The `expected_result.contains` field enables programmatic evaluation — no LLM judge required for the core signal.

---

### Step 9: Integration and CLI

- `benchmarks/__main__.py` — enables `python -m benchmarks` invocation
- Arg parsing: `--model`, `--trials`, `--tasks`, `--output`, `--save-baseline`, `--compare-baseline`
- Add a `pyproject.toml` script entry (optional): `[project.scripts] benchmark = "benchmarks.runner:main"`

---

## What This Delivers (Immediately, Without New Instrumentation)

| Signal | Source | What It Tells You |
|--------|--------|-------------------|
| **Step efficiency** | `history.number_of_steps()` | Are we solving tasks in fewer steps over time? |
| **Token cost** | `history.usage.total_tokens / total_cost` | Is a code change making prompts more/less expensive? |
| **Duration** | `history.total_duration_seconds()` | Is the agent getting faster or slower? |
| **Error rate** | `errors() / number_of_steps()` | What fraction of steps produce errors? |
| **Action distribution** | `Counter(action_names())` | Is the agent clicking more, scrolling more, extracting more? |
| **Pass rate with variance** | N trials × programmatic check | Statistical confidence instead of single-shot binary |
| **Regression detection** | Baseline comparison | Flag if step count or token cost increases beyond threshold |

---

## What This Does NOT Do (Left to Level 1+)

- No step-level grounding metrics (which element was clicked vs. which should have been)
- No DOM serialization quality measurement (precision/recall of element detection)
- No checklist-based partial credit (PRM)
- No model-size sweep infrastructure
- No WebArena Verified integration
- No Web-Shepherd PRM integration

These are explicitly scoped out. Level 0 harvests what already exists. Level 1+ builds new measurement capabilities on top of this foundation.

---

## File Dependencies

```
benchmarks/metrics.py      → browser_use.agent.views (AgentHistoryList, UsageSummary)
benchmarks/fixtures/       → standalone (returns HTML strings)
benchmarks/runner.py       → metrics.py, fixtures/, baseline.py, report.py
                           → browser_use (Agent, BrowserSession, BrowserProfile)
                           → tests/ci/conftest.py (create_mock_llm)
benchmarks/baseline.py     → standalone (JSON I/O)
benchmarks/report.py       → metrics.py (uses TaskAggregateMetrics model)
```

---

## Implementation Order

1. `benchmarks/metrics.py` — no dependencies, pure data extraction
2. `benchmarks/fixtures/__init__.py` — no dependencies, pure HTML generation
3. `benchmarks/tasks/*.yaml` — depends on fixture names from step 2
4. `benchmarks/baseline.py` — no dependencies, pure JSON I/O
5. `benchmarks/report.py` — depends on metrics models from step 1
6. `benchmarks/conftest.py` — depends on fixtures from step 2
7. `benchmarks/runner.py` — ties everything together
8. `benchmarks/__main__.py` — CLI wrapper around runner
9. Validation: run the suite in mock mode, verify report output

---

## Validation Criteria

The plan is complete when:
1. `python -m benchmarks --model mock --trials 3` runs without errors
2. All 5 tasks execute against real headless Chromium with mock LLM
3. A Markdown report is generated with per-task and aggregate metrics
4. A JSON baseline is saved and can be loaded for comparison
5. A second run produces a delta comparison against the baseline
6. No live URLs are used anywhere in the benchmark tasks
7. The `tests/ci` suite still passes (no regressions)
