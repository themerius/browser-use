# Plan 1: Internal Benchmark Suite

## Goal

A dedicated benchmark suite (separate from CI) that harvests metrics already tracked by `AgentHistoryList` and `UsageSummary` but never aggregated. Runs against reproducible local HTTP fixtures, supports multi-trial runs for stochastic variance, outputs structured reports with baseline comparison.

**CI tests** answer: "does it still work?" (binary, deterministic, fast, mocked LLM)
**This benchmark** answers: "how well does it work?" (continuous, stochastic, slow, real or mock LLMs)

---

## Architecture

```
benchmarks/
├── __init__.py
├── runner.py              # Orchestrator: loads tasks, runs trials, collects histories
├── metrics.py             # Extracts metrics from AgentHistoryList → structured data
├── report.py              # Generates Markdown + JSON reports
├── baseline.py            # Stores/loads/compares baseline metrics
├── conftest.py            # Shared pytest fixtures (browser session, HTTP servers)
├── tasks/                 # Benchmark task definitions (YAML)
│   ├── search_product.yaml
│   ├── fill_form.yaml
│   ├── navigate_multi_page.yaml
│   ├── extract_structured_data.yaml
│   ├── dropdown_interaction.yaml
│   ├── download_newest_invoice.yaml
│   └── download_all_invoices.yaml
├── fixtures/              # HTML fixture templates for pytest-httpserver
│   └── __init__.py        # Functions returning HTML strings per scenario
├── baselines/             # Stored baseline JSON files (one per model)
│   └── .gitkeep
└── reports/               # Generated reports (gitignored)
    └── .gitkeep
```

---

## Step 1: `benchmarks/metrics.py` — Metric Extraction

A `TaskRunMetrics` pydantic model and `extract_metrics(history: AgentHistoryList) -> TaskRunMetrics`.

**Fields extracted from existing APIs** (zero new instrumentation):

| Metric | Source | API |
|--------|--------|-----|
| `steps` | AgentHistoryList | `history.number_of_steps()` |
| `total_tokens` | UsageSummary | `history.usage.total_tokens` |
| `prompt_tokens` | UsageSummary | `history.usage.total_prompt_tokens` |
| `completion_tokens` | UsageSummary | `history.usage.total_completion_tokens` |
| `cached_tokens` | UsageSummary | `history.usage.total_prompt_cached_tokens` |
| `total_cost` | UsageSummary | `history.usage.total_cost` |
| `duration_seconds` | AgentHistoryList | `history.total_duration_seconds()` |
| `success` | AgentHistoryList | `history.is_successful()` |
| `is_done` | AgentHistoryList | `history.is_done()` |
| `error_count` | AgentHistoryList | `len([e for e in history.errors() if e is not None])` |
| `action_names` | AgentHistoryList | `history.action_names()` |
| `action_distribution` | derived | `Counter(history.action_names())` |
| `urls_visited` | AgentHistoryList | `history.urls()` |
| `final_result` | AgentHistoryList | `history.final_result()` |

**Aggregate model** `TaskAggregateMetrics`: computed over N trials of the same task.

| Aggregate | Computation |
|-----------|-------------|
| `pass_rate` | `sum(success) / n_trials` |
| `avg_steps` / `std_steps` | `mean(steps)` / `stdev(steps)` |
| `avg_tokens` / `std_tokens` | `mean(total_tokens)` / `stdev(total_tokens)` |
| `avg_cost` | `mean(total_cost)` |
| `avg_duration` | `mean(duration_seconds)` |
| `avg_error_rate` | `mean(error_count / steps)` per trial |
| `action_distribution` | merged Counter across all trials |

Pydantic v2 models with `model_config = ConfigDict(extra='forbid')`. Use `from statistics import mean, stdev`.

---

## Step 2: `benchmarks/fixtures/__init__.py` — HTML Fixtures

Functions returning `dict[str, str]` mapping URL paths to HTML content, registered with `pytest-httpserver`. No live URLs.

### Scenario 1: `search_product()`
- Index page with search input + submit button
- Results page with 3 product cards (name, price, "Add to Cart")
- Exercises: navigation, text input, form submission, element identification

### Scenario 2: `fill_form()`
- Multi-field contact form (name, email, message textarea, submit)
- Confirmation page after POST
- Exercises: multi-field text input, form submission, result verification

### Scenario 3: `navigate_multi_page()`
- Page 1 → link → Page 2 → link → Page 3 (target info)
- Exercises: sequential navigation, link clicking, multi-step traversal

### Scenario 4: `extract_structured_data()`
- HTML table with 5 rows × 3 columns (product, price, stock)
- Exercises: structured data extraction, table parsing

### Scenario 5: `dropdown_interaction()`
- Form with native `<select>` (4 options), submit, confirmation page
- Exercises: dropdown detection, option selection, compound interaction

### Scenario 6: `download_newest_invoice()` — Single Invoice PDF Download

A portal-style page that exercises the full download pipeline:

**Pages:**
- `/portal` — Login/landing page with "Invoices" navigation link
- `/portal/invoices` — Invoice list table with columns: Invoice #, Date, Amount, Status, Download (link/button). 5 invoices, sorted newest-first. Each row has a download link pointing to `/portal/invoices/{id}/download`
- `/portal/invoices/{id}/download` — Serves a PDF file (generated inline as a minimal valid PDF binary, or a static test PDF stored in fixtures)

**HTML structure of invoice list:**
```html
<table class="invoices">
  <thead><tr><th>Invoice #</th><th>Date</th><th>Amount</th><th>Status</th><th></th></tr></thead>
  <tbody>
    <tr><td>INV-2026-005</td><td>2026-02-15</td><td>$1,249.00</td><td>Due</td>
        <td><a href="/portal/invoices/5/download" class="download-btn">Download PDF</a></td></tr>
    <tr><td>INV-2026-004</td><td>2026-01-20</td><td>$890.50</td><td>Paid</td>
        <td><a href="/portal/invoices/4/download" class="download-btn">Download PDF</a></td></tr>
    <!-- ... 3 more rows ... -->
  </tbody>
</table>
```

**What the agent must do:**
1. Navigate to `/portal`
2. Find and click "Invoices" link
3. Identify the newest invoice (first row, INV-2026-005)
4. Click its "Download PDF" link
5. Report: invoice number, date, amount, and confirm file downloaded

**Success criteria (programmatic):**
- `expected_result.contains: ["INV-2026-005", "2026-02-15", "$1,249.00"]`
- File exists in `downloads_path` (verified via `FileDownloadedEvent` or filesystem check)

**What this exercises:**
- Page navigation through a portal
- Table comprehension (identifying "newest" = first row by date)
- PDF download via `DownloadsWatchdog` (auto-download or click-triggered)
- Integration of `FileDownloadedEvent` with agent reporting

**pytest-httpserver setup:**
- PDF response: `server.expect_request('/portal/invoices/5/download').respond_with_data(pdf_bytes, content_type='application/pdf', headers={'Content-Disposition': 'attachment; filename="INV-2026-005.pdf"'})`
- The `Content-Disposition: attachment` header triggers the network-based download detection in `DownloadsWatchdog._setup_network_monitoring()`

### Scenario 7: `download_all_invoices()` — Multi-Page Invoice Download

A harder variant that requires paginated browsing:

**Pages:**
- `/portal/invoices?page=1` — First page: 3 invoices + "Next Page" link
- `/portal/invoices?page=2` — Second page: 3 invoices + "Next Page" link
- `/portal/invoices?page=3` — Third page: 2 invoices (last page, no "Next Page")
- `/portal/invoices/{id}/download` — PDF download for each of the 8 invoices

**HTML pagination:**
```html
<div class="pagination">
  <span class="current">Page 1 of 3</span>
  <a href="/portal/invoices?page=2" class="next-page">Next Page →</a>
</div>
```

**What the agent must do:**
1. Navigate to `/portal/invoices?page=1`
2. Download all 3 PDFs on page 1
3. Click "Next Page" to reach page 2
4. Download all 3 PDFs on page 2
5. Click "Next Page" to reach page 3
6. Download all 2 PDFs on page 3
7. Report: total count of downloaded files (8), list of invoice numbers

**Success criteria (programmatic):**
- `expected_result.contains: ["8"]` (total download count)
- All 8 files exist in `downloads_path`

**What this exercises beyond Scenario 6:**
- Paginated navigation (detecting and following "Next Page" links)
- Repeated action patterns (download on each page)
- Multi-step state tracking (agent must remember it hasn't finished until all pages visited)
- Higher step count → better stress test for step efficiency metrics
- Download concurrency/sequencing (multiple downloads per page)

**Efficiency ceiling:** `expected_result.max_steps: 20` (3 pages × ~3 navigations + 8 downloads + done ≈ 15 steps optimal)

---

## Step 3: Benchmark Task YAML Format — `benchmarks/tasks/*.yaml`

Extended schema (superset of existing `tests/agent_tasks/` format):

```yaml
name: Download Newest Invoice
task: |
  Navigate to the invoice portal, find the most recent invoice,
  download it as PDF, and report the invoice number, date, and amount.
fixture: download_newest_invoice        # Key into fixtures module
max_steps: 10
expected_result:                        # Programmatic success criteria
  contains:
    - "INV-2026-005"
    - "2026-02-15"
    - "$1,249.00"
  max_steps: 7                          # Efficiency ceiling (optional)
  files_downloaded: 1                   # Expected download count (optional)
judge_context:                          # Optional LLM judge (for comparison)
  - The agent must navigate to the invoices page
  - The agent must identify the newest invoice
  - The agent must download the PDF
mock_actions:                           # Deterministic sequence for mock LLM mode
  - '{"thinking": "...", "action": [{"click_element": {"index": 3}}]}'
  - '{"thinking": "...", "action": [{"click_element": {"index": 7}}]}'
  - '{"thinking": "...", "action": [{"done": {"text": "INV-2026-005...", "success": true}}]}'
```

**Key design decisions:**
- `expected_result.contains` enables **deterministic programmatic evaluation** — no LLM judge needed
- `expected_result.files_downloaded` adds download verification for file-handling tasks
- `mock_actions` enables fully reproducible zero-cost runs that exercise the real browser + DOM + download pipeline
- `judge_context` kept optional for LLM judge comparison experiments

---

## Step 4: `benchmarks/runner.py` — Benchmark Orchestrator

**CLI:**
```
python -m benchmarks [--model mock] [--trials 3] [--tasks benchmarks/tasks/*.yaml] [--output benchmarks/reports/]
```

**Flow:**
1. Parse args, load task YAML files
2. For each task:
   a. Look up fixture function from `benchmarks.fixtures` by `fixture` key
   b. Start `pytest-httpserver`, register fixture HTML + PDF responses
   c. Create `BrowserSession(BrowserProfile(headless=True, downloads_path=tmp_dir))`
   d. Create `Agent(task=task_str, llm=llm, browser_session=session)`
   e. Run `agent.run(max_steps=max_steps)` → `AgentHistoryList`
   f. Extract `TaskRunMetrics` via `metrics.extract_metrics(history)`
   g. Evaluate programmatic success:
      - Check `expected_result.contains` against `history.final_result()`
      - Check `expected_result.files_downloaded` against actual files in `downloads_path`
   h. Repeat c–g for `--trials` iterations
   i. Aggregate into `TaskAggregateMetrics`
   j. Cleanup: `await session.kill()`
3. Collect all aggregates, compute suite-wide totals
4. Load baseline (if exists) for comparison
5. Generate report

**Mock LLM mode** (`--model mock`): Uses `create_mock_llm(actions)` from `tests/ci/conftest.py` with the `mock_actions` field from YAML. Fully reproducible, zero-cost, exercises real browser + DOM + download pipeline.

**Real LLM mode** (`--model gpt-4o-mini`): Instantiates real `BaseChatModel`. Captures stochastic variance across trials, real token/cost metrics.

**Error handling**: Each trial wrapped in try/except. Failures recorded as `TaskRunMetrics(success=False, ...)`, runner continues.

---

## Step 5: `benchmarks/baseline.py` — Baseline Storage & Comparison

**File format** (`benchmarks/baselines/{model_name}.json`):
```json
{
  "timestamp": "2026-02-16T14:30:00Z",
  "model": "gpt-4o-mini",
  "trials_per_task": 3,
  "tasks": {
    "download_newest_invoice": {
      "pass_rate": 1.0, "avg_steps": 5.3, "avg_tokens": 14200,
      "avg_cost": 0.0035, "avg_duration": 9.1
    }
  },
  "aggregate": { "pass_rate": 0.80, "avg_steps": 6.1, "avg_tokens": 18400, "avg_cost": 0.0046 }
}
```

**API:**
- `load_baseline(model: str) -> dict | None`
- `save_baseline(model: str, results: dict) -> None`
- `compare(current: dict, baseline: dict) -> dict` — per-metric deltas with direction indicators
- Threshold warnings: `avg_steps` +20% or `pass_rate` -10pp → regression flag

---

## Step 6: `benchmarks/report.py` — Report Generation

**Markdown output** (`benchmarks/reports/report_{timestamp}.md`):
```
## browser-use Benchmark Report — 2026-02-16
Model: gpt-4o-mini | Trials per task: 3

| Task                      | Pass Rate | Avg Steps | Avg Tokens | Avg Cost  | Avg Duration | Downloads |
|---------------------------|-----------|-----------|------------|-----------|--------------|-----------|
| search_product            | 3/3       | 4.3       | 12,841     | $0.0032   | 8.2s         | —         |
| fill_form                 | 2/3       | 6.7       | 18,203     | $0.0045   | 12.1s        | —         |
| navigate_multi_page       | 3/3       | 3.0       | 8,922      | $0.0022   | 5.4s         | —         |
| extract_structured_data   | 2/3       | 5.3       | 15,100     | $0.0038   | 9.8s         | —         |
| dropdown_interaction      | 3/3       | 4.0       | 11,200     | $0.0028   | 7.3s         | —         |
| download_newest_invoice   | 3/3       | 5.3       | 14,200     | $0.0035   | 9.1s         | 1/1       |
| download_all_invoices     | 1/3       | 17.0      | 52,300     | $0.0131   | 38.5s        | 6.7/8    |

### Aggregate
| Metric     | Current | Baseline | Delta    |
|------------|---------|----------|----------|
| Pass Rate  | 81.0%   | 73.3%    | +7.6pp   |
| Avg Steps  | 6.5     | 7.0      | -0.5     |
| Avg Tokens | 18,966  | 20,100   | -1,134   |
| Avg Cost   | $0.0047 | $0.0052  | -$0.0005 |
```

JSON sidecar written alongside with full per-trial breakdowns.

---

## Step 7: `benchmarks/conftest.py` — Shared Fixtures

**Reused** from `tests/ci/conftest.py`:
- `create_mock_llm(actions)` — import directly

**New fixtures:**
- `benchmark_httpserver(fixture_dict)` — factory that registers all routes from a fixture's path→HTML dict, including PDF binary responses with `Content-Disposition: attachment` headers
- `benchmark_browser_session(downloads_path)` — per-task session with `BrowserProfile(headless=True, downloads_path=downloads_path, auto_download_pdfs=True)`

---

## Step 8: CLI Wrapper

`benchmarks/__main__.py` — enables `python -m benchmarks` invocation.

Args: `--model`, `--trials`, `--tasks`, `--output`, `--save-baseline`, `--compare-baseline`

---

## Implementation Order

1. `benchmarks/metrics.py` — no dependencies, pure data extraction
2. `benchmarks/fixtures/__init__.py` — no dependencies, pure HTML/PDF generation (including invoice portal fixtures)
3. `benchmarks/tasks/*.yaml` — depends on fixture names from step 2
4. `benchmarks/baseline.py` — no dependencies, pure JSON I/O
5. `benchmarks/report.py` — depends on metrics models from step 1
6. `benchmarks/conftest.py` — depends on fixtures from step 2
7. `benchmarks/runner.py` — ties everything together
8. `benchmarks/__main__.py` — CLI wrapper
9. Validation: run in mock mode, verify report output including download scenarios

---

## Validation Criteria

1. `python -m benchmarks --model mock --trials 3` runs without errors
2. All 7 tasks execute against real headless Chromium with mock LLM
3. Invoice PDF download scenarios produce actual files in `downloads_path`
4. `FileDownloadedEvent` fires for download scenarios (verifiable via event bus)
5. Markdown report includes Downloads column for file-handling tasks
6. JSON baseline saved and loadable for comparison
7. Second run produces delta comparison against baseline
8. No live URLs used anywhere
9. `tests/ci` suite still passes (no regressions)

---

## What This Delivers (Without New Instrumentation)

| Signal | Source | What It Tells You |
|--------|--------|-------------------|
| Step efficiency | `history.number_of_steps()` | Fewer steps over time? |
| Token cost | `history.usage.total_tokens / total_cost` | Prompts more/less expensive? |
| Duration | `history.total_duration_seconds()` | Agent faster/slower? |
| Error rate | `errors() / number_of_steps()` | Fraction of steps with errors? |
| Action distribution | `Counter(action_names())` | Behavioral shifts? |
| Pass rate + variance | N trials × programmatic check | Statistical confidence |
| Download reliability | files in `downloads_path` | File handling pipeline working? |
| Regression detection | Baseline comparison | Flag step/token/cost regressions |

---

## What This Does NOT Do (Left to Plan 2 and Level 1+)

- No externally-comparable benchmark numbers (→ Plan 2: WebArena Verified)
- No step-level grounding metrics
- No DOM serialization quality measurement
- No checklist-based partial credit (PRM)
- No model-size sweep infrastructure
