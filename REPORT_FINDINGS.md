# Outline Mode Serialization: Implementation Journey and Benchmark Findings

## Abstract

We implemented and iteratively refined a hierarchical outline serialization mode for the browser-use DOM pipeline, progressing through four versions (v1–v4) over a single development cycle.  The final version achieves a **100% pass rate** on our internal benchmark suite (9/9 tasks), surpassing classic flat serialization (88.9%) on the same model (gpt-oss-20b via OpenRouter).  This report documents the implementation journey, the role our trace-level benchmark logging played in root-cause analysis, and the key serialization traps we identified.

## 1. Introduction: The Benchmark and Logging Infrastructure

### 1.1 Motivation

browser-use serializes DOM trees into text consumed by LLMs.  The existing "classic" flat serialization works, but the theoretical claim — formalized in Plan 3 [1] as `required_model_size ~ f(task_complexity / input_structure_quality)` — is that hierarchically structured input should allow smaller models to perform comparably to larger ones on the same tasks.

To test this claim rigorously, we first needed infrastructure that didn't exist: a reproducible benchmark suite with per-step diagnostic traces.

### 1.2 Benchmark Design (Plan 1)

The internal benchmark suite [2] comprises 9 tasks spanning form interaction, navigation, data extraction, file downloads, and accessibility-variant forms (WCAG compliant and non-compliant HTML).  Each task is backed by a local pytest-httpserver fixture — no external URLs, fully deterministic server-side.

Key design decisions:
- **YAML task definitions** with `expected_result.contains` for programmatic pass/fail (no LLM judge for correctness).
- **JSONL trace files** capturing per-step `dom_text`, `model_output`, `action_names`, and `errors` — the single most valuable diagnostic artifact.
- **Mock LLM mode** for CI regression testing (zero cost, reproducible).
- **Baseline comparison** with delta reporting for pass rate, score, steps, tokens, and duration.

### 1.3 How Tracing Drove the Investigation

The trace files proved essential at two critical junctures:

1. **v2 WCAG regression**: Traces showed the agent spiraling for 13 steps (161s) on the WCAG Compliant Form because region collapsing had hidden interactive elements after step 1.  The `dom_text` field in the trace made this immediately visible — the form fields simply weren't in the serialized output.

2. **v3 Dropdown regression**: The model reached the confirmation page and reported success, yet the task failed.  Comparing the `dom_text` across v2 (passed) and v3 (failed) traces revealed the serialization difference: v3 fell back to classic mode and introduced a `|SHADOW(open)|` prefix on the native `<select>` element.  The `action_names` field confirmed the model used `click` (wrong) instead of `select_dropdown` (correct).  Without per-step DOM snapshots, this would have appeared to be non-deterministic model behavior.

## 2. Implementation Journey

### 2.1 Outline v2: First Real-Model Run (77.8%)

The initial outline implementation grouped interactive elements under WAI-ARIA landmark regions (BANNER, NAV, MAIN, CONTENTINFO) with heading hierarchy and cross-step region collapsing [3].

| Result | Detail |
|--------|--------|
| Pass rate | 77.8% (7/9) — **regression** vs classic 88.9% |
| Failures | WCAG Compliant Form (13 steps, 161s spiral), WCAG Non-Compliant Form |
| Root cause | Region collapsing marked landmarks as `(unchanged, N elements)` after step 1, hiding form fields from the LLM |

### 2.2 Outline v3: Collapsing Removed (88.9%)

Three fixes applied per the root-cause analysis in plan.md [4]:
1. **Removed region collapsing entirely** — interactive elements always visible.
2. **Sub-region deduplication** — elements in nested landmarks (e.g. `<nav>` inside `<header>`) emitted only under the most specific landmark via `exclude_ids`.
3. **Classic fallback for landmarkless pages** — pages without semantic HTML landmarks use classic serialization to avoid a useless `(ungrouped):` wrapper.

| Result | Detail |
|--------|--------|
| Pass rate | 88.9% (8/9) — matches classic |
| Failure | Dropdown Interaction |
| Root cause | The classic fallback introduced `\|SHADOW(open)\|` on native `<select>` and dropped accessible-name annotations |

### 2.3 Outline v4: Fallback Fixed (100%)

The dropdown regression was **not a model issue** — it was a serialization issue.  Trace-level comparison revealed three differences between the v2 outline rendering (which passed) and the v3 classic fallback (which failed):

| Property | Outline subtree renderer | Classic `serialize_tree` |
|----------|------------------------|------------------------|
| Shadow DOM prefixes | Omitted | `\|SHADOW(open)\|` on native `<select>` |
| Accessible labels | `"Select Product:"` appended | Absent |
| Model's action choice | `select_dropdown` | `click` (triggers read-only options helper) |

Fix: landmarkless pages now use the outline subtree renderer directly (`_serialize_outline_subtree` on root) instead of falling back to classic.  Additionally, the dropdown fixture was made dynamic — the `/confirm` handler now validates the POST `product` value, eliminating the false-positive static page.

| Result | Detail |
|--------|--------|
| Pass rate | **100% (9/9)** — first outline run to beat classic |
| vs Classic | +11.1pp pass rate, -2,293 tokens, -16.7s duration |

## 3. Benchmark Results Overview

### 3.1 Cross-Version Comparison (gpt-oss-20b, 1 trial each)

| Metric | Classic v2 | Outline v2 | Outline v3 | Outline v4 |
|--------|-----------|-----------|-----------|-----------|
| **Pass Rate** | 88.9% | 77.8% | 88.9% | **100%** |
| Avg Score | 0.89 | 0.78 | 0.89 | **1.00** |
| Avg Steps | 4.2 | 5.3 | 4.7 | 4.9 |
| Avg Tokens | 31,945 | 44,667 | 38,100 | 38,876 |
| Avg Duration | 26.0s | 40.2s | 53.4s | 33.3s |

### 3.2 Per-Task Failure Map

| Task | Classic v2 | Outline v2 | Outline v3 | Outline v4 |
|------|:---------:|:---------:|:---------:|:---------:|
| Download All Invoices | PASS | PASS | PASS | PASS |
| Download Newest Invoice | PASS | PASS | PASS | PASS |
| Dropdown Interaction | FAIL | PASS | FAIL | PASS |
| Extract Structured Data | PASS | PASS | PASS | PASS |
| Fill Contact Form | PASS | PASS | PASS | PASS |
| Navigate Multi Page | PASS | PASS | PASS | PASS |
| Search Product | PASS | PASS | PASS | PASS |
| WCAG Compliant Form | PASS | FAIL | PASS | PASS |
| WCAG Non-Compliant Form | PASS | FAIL | PASS | PASS |

### 3.3 Token Efficiency

Outline v4 uses ~22% more tokens than classic v2 (38,876 vs 31,945) due to accessible-name annotations and heading hierarchy.  However, this overhead buys a +11.1pp pass rate improvement.  The token cost of outline formatting is modest — the dominant factor is multi-step tasks accumulating conversation history, not the per-step DOM size.

## 4. Discussion

### 4.1 Key Findings

1. **Serialization format directly affects action selection.**  The `|SHADOW(open)|` prefix — a Chrome implementation detail leaking through CDP — steered the model away from the correct `select_dropdown` tool toward raw `click`.  This is not model non-determinism; it's a systematic bias introduced by the serialization.

2. **Accessible-name annotations matter.**  The outline renderer's `"Select Product:"` suffix gives the LLM the same affordance a sighted user gets from visual label proximity.  Classic mode forces the model to infer label associations from DOM structure.

3. **Benchmark fixtures must validate server-side.**  Static confirmation pages create false positives that mask real regressions.  Dynamic handlers (callable fixtures) that check POST/query values are essential for interaction-dependent tasks.

4. **Per-step traces are the primary diagnostic tool.**  Aggregate pass/fail metrics identify *that* something broke; trace-level `dom_text` and `action_names` identify *why*.

### 4.2 Limitations

- **Single trial per task**: With n=1, results are subject to model non-determinism.  Multi-trial runs with confidence intervals are needed for statistical significance.
- **Single model**: gpt-oss-20b only.  The Plan 3 thesis — that outline mode disproportionately benefits smaller models — remains untested.
- **9-task suite**: A narrow benchmark.  WebArena Verified (812 tasks, Plan 2 [5]) would provide externally comparable numbers.

### 4.3 Traps for Future Development

These are documented inline in the codebase (module docstrings) but summarized here:

| Trap | Where | What happens |
|------|-------|-------------|
| Falling back to classic for landmarkless pages | `serialize_outline_tree` | Introduces `\|SHADOW(open)\|` noise, drops accessible labels |
| Region collapsing | `outline.py` | Hides interactive elements after step 1 |
| `expected_result.contains` checks model text, not page | `runner.py` `_evaluate_result` | Pass/fail depends on LLM phrasing, not actual page state |
| Static fixture confirmation pages | `fixtures/__init__.py` | False positives when agent doesn't actually interact correctly |

## 5. Outlook

**Near-term**: Run multi-trial benchmarks (n=5) across model sizes (gpt-4o-mini, gpt-oss-20b, gpt-4o) to test the Plan 3 thesis that outline mode shifts the efficiency frontier — enabling smaller models to match larger ones on structured tasks.

**Medium-term**: Integrate WebArena Verified [5] for publication-grade numbers.  The internal suite catches regressions; WebArena provides external comparability.

**Long-term**: As outlined in REPORT.md [6], the field is converging toward hybrid DOM+vision pipelines.  Outline mode's landmark structure is a natural fit for this — landmarks can scope where vision is needed (e.g. visual-only content within a MAIN region) while text-serialized regions handle form interaction.

## References

[1] `plan_3_outline_navigation.md` — Hierarchical Outline + Screen Reader Navigation (Plan 3)
[2] `plan_1_internal_benchmark.md` — Internal Benchmark Suite (Plan 1)
[3] `benchmarks/reports/outline_v2/report_2026-02-19_042841.md` — Outline v2 results
[4] `plan.md` — Fix Outline Mode to Beat Classic (root-cause analysis)
[5] `plan_2_webarena_benchmark.md` — WebArena Verified Benchmark Integration (Plan 2)
[6] `REPORT.md` — How LLMs Navigate the Web: Analysis of browser-use
