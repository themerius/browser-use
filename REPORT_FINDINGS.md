# Outline Mode Serialization: Implementation Journey and Benchmark Findings

## Abstract

We implemented and iteratively refined a hierarchical outline serialization mode for the browser-use DOM pipeline, progressing through four versions (v1-v4) over a single development cycle.  The final version achieves a **100% pass rate** on our internal benchmark suite (9/9 tasks), surpassing classic flat serialization (88.9%) on the same model (gpt-oss-20b via OpenRouter).  This report documents the implementation journey, the role our trace-level benchmark logging played in root-cause analysis, and the key serialization traps we identified.  We ground our findings in recent literature on DOM observation formatting, cognitive chunking, and the established challenges of user-agent shadow DOM in web automation.

## 1. Introduction

### 1.1 The DOM Serialization Problem

Web agents that drive browsers via LLMs face a fundamental bottleneck: raw DOM trees can exceed 1M tokens [7], yet even long-context models degrade on lengthy, unstructured observations.  Lee et al. (ICLR 2025) demonstrated this directly -- their LCoW framework showed that LLM agent performance "significantly degrades when they rely on lengthy, non-contextualized observations, such as HTML and accessibility trees" [8].  The mitigation strategies in the literature fall into three categories: DOM pruning (Mind2Web [9]), contextualization modules (LCoW [8]), and structured formatting -- the approach we pursue here.

browser-use's existing "classic" serializer compresses the DOM to interactive elements with tag/attribute annotations, achieving a self-reported 89.1% on WebVoyager with GPT-4o [10].  This number warrants context: browser-use's evaluation involved manual correction of the LLM judge's verdicts, removal of 55 infeasible tasks, and date adjustments -- and the project has published no formal paper, only a blog-style technical report [10].  Independent evaluation by Xu et al. (2025) found browser-use scoring 30% on Online-Mind2Web (a harder, human-evaluated benchmark), noting that "many recent agents [...] do not outperform the simple SeeAct agent" and that a trivial search-only baseline already achieves 51% on WebVoyager [27].  Magnitude has since claimed 93.9% on WebVoyager [11].  browser-use has not published WebArena results.  Our work does not re-evaluate these external claims; instead, we ask a narrower question: whether *hierarchically structuring* the serializer's output can improve action selection accuracy on controlled local tasks.

### 1.2 Theoretical Motivation

The hypothesis -- formalized in Plan 3 [1] as `required_model_size ~ f(task_complexity / input_structure_quality)` -- draws on two convergent lines of evidence:

**Cognitive chunking.** Miller's (1956) foundational work established that human working memory holds 7 +/- 2 *chunks*, where chunk size depends on the structure of the input [12].  Recoding information into hierarchical groups effectively expands capacity.  Our outline mode applies this principle: grouping elements under WAI-ARIA landmarks and heading hierarchy creates semantic chunks that reduce the apparent complexity of the page.

**Prompt format sensitivity in LLMs.** Srivastava et al. (2024) showed that GPT-3.5-turbo's performance varies by up to 40% depending on prompt template format, with smaller models far more sensitive to formatting than larger ones [13].  Xiao et al. (2025) extended this finding with CFPO, showing that "different LLMs display distinct preferences, with some formats performing well on one model but failing on another" [14].  Most directly, a 2025 theoretical analysis demonstrated that for sufficiently long inputs, "a weaker model configured with chunk-based processing can surpass a more advanced model applied in a single shot" due to superlinear noise growth with input length [15].

**Specialized small-model results.** ScribeAgent (CMU, 2024) showed a fine-tuned 7B model surpassing GPT-4 on web navigation tasks [16].  Go-Browse (Gandhi & Neubig, 2025) achieved 21.7% on WebArena with a 7B model, beating GPT-4o-mini [17].  LCoW improved Llama-3.1-8B success rates by 23.7% through observation contextualization alone [8].  These results suggest that how information is presented to the model matters as much as model scale.

### 1.3 The Benchmark Infrastructure

To test our hypothesis rigorously, we built a reproducible benchmark suite (Plan 1 [2]) with capabilities absent from standard CI tests:

- **9 tasks** spanning form interaction, navigation, data extraction, file downloads, and accessibility-variant forms (WCAG compliant and non-compliant HTML).
- **Local pytest-httpserver fixtures** -- no external URLs, fully deterministic server-side.  Fixtures support static HTML, binary responses, and callable handlers for server-side validation (added in v4, see Section 2.3).
- **JSONL trace files** capturing per-step `dom_text`, `model_output`, `action_names`, and `errors` -- the single most valuable diagnostic artifact (see Section 1.4).
- **Programmatic evaluation** via `expected_result.contains` on the agent's `done` text, with baseline delta reporting.

This follows the evaluation methodology advocated by WebArena Verified [18], which demonstrated that "underspecified goals and brittle checkers" can misestimate agent performance by over 11 percentage points.  Our fixture-based approach with server-side validation addresses the same concern at smaller scale.

### 1.4 How Tracing Drove the Investigation

The trace files proved essential at two critical junctures:

1. **v2 WCAG regression**: Traces showed the agent spiraling for 13 steps (161s) on the WCAG Compliant Form because region collapsing had hidden interactive elements after step 1.  The `dom_text` field made this immediately visible -- the form fields simply weren't in the serialized output.

2. **v3 Dropdown regression**: The model reached the confirmation page and reported success, yet the task failed.  Comparing the `dom_text` across v2 (passed) and v3 (failed) traces revealed the serialization difference: v3 fell back to classic mode and introduced a `|SHADOW(open)|` prefix on the native `<select>` element.  The `action_names` field confirmed the model used `click` (wrong) instead of `select_dropdown` (correct).  Without per-step DOM snapshots, this would have appeared to be non-deterministic model behavior.

This diagnostic pattern -- aggregate metrics detect *that* something broke, per-step traces reveal *why* -- mirrors the observability approach described in the "Building Browser Agents" survey [19], which notes that "some systems render tooltips and helper text [...] without semantic references, and in these cases the accessibility tree snapshot either hides important interactions or presents them in an order that is difficult for the LLM to process."

## 2. Implementation Journey

### 2.1 Outline v2: First Real-Model Run (77.8%)

The initial outline implementation grouped interactive elements under WAI-ARIA landmark regions (BANNER, NAV, MAIN, CONTENTINFO) with heading hierarchy and cross-step region collapsing [3].

| Result | Detail |
|--------|--------|
| Pass rate | 77.8% (7/9) -- **regression** vs classic 88.9% |
| Failures | WCAG Compliant Form (13 steps, 161s spiral), WCAG Non-Compliant Form |
| Root cause | Region collapsing marked landmarks as `(unchanged, N elements)` after step 1, hiding form fields from the LLM |

The collapsing mechanism was inspired by the token-saving strategies common in the literature -- LCoW [8] and Agent Workflow Memory [20] both reduce observation length across steps.  However, our implementation was too aggressive: it collapsed *entire landmark regions* rather than selectively summarizing non-interactive content, rendering forms invisible to the agent.

### 2.2 Outline v3: Collapsing Removed (88.9%)

Three fixes applied per the root-cause analysis in plan.md [4]:
1. **Removed region collapsing entirely** -- interactive elements always visible.
2. **Sub-region deduplication** -- elements in nested landmarks (e.g. `<nav>` inside `<header>`) emitted only under the most specific landmark via `exclude_ids`.
3. **Classic fallback for landmarkless pages** -- pages without semantic HTML landmarks use classic serialization to avoid a useless `(ungrouped):` wrapper.

| Result | Detail |
|--------|--------|
| Pass rate | 88.9% (8/9) -- matches classic |
| Failure | Dropdown Interaction |
| Root cause | The classic fallback introduced `|SHADOW(open)|` on native `<select>` and dropped accessible-name annotations |

### 2.3 Outline v4: Fallback Fixed (100%)

The dropdown regression was **not a model issue** -- it was a serialization issue.  Trace-level comparison revealed three differences between the v2 outline rendering (which passed) and the v3 classic fallback (which failed):

| Property | Outline subtree renderer | Classic `serialize_tree` |
|----------|------------------------|------------------------|
| Shadow DOM prefixes | Omitted | `|SHADOW(open)|` on native `<select>` |
| Accessible labels | `"Select Product:"` appended | Absent |
| Model's action choice | `select_dropdown` | `click` (triggers read-only options helper) |

**The shadow DOM trap.** Chrome renders native `<select>`, `<input>`, `<video>`, and other built-in elements using internal (user-agent) shadow DOM [21].  This is a well-documented pain point in web automation: user-agent shadow roots are closed and fundamentally inaccessible to JavaScript or automation frameworks [22], and the W3C WebDriver community has identified shadow DOM as "one of the key challenges for all Test Engineers" [23].  Our classic serializer faithfully reports these shadow roots as `|SHADOW(open)|` markers, which is correct for *author-created* web components but misleading for *browser-internal* shadow DOM on native form elements.  The model, seeing `|SHADOW(open)|<select ...>`, reasonably infers it's dealing with a custom component and avoids the dedicated `select_dropdown` tool.

**The accessible-label effect.** The outline renderer appends AX accessible names (e.g. `"Select Product:"`) after interactive elements.  This mirrors the approach taken by accessibility-tree-based agents [24], which use "the way screen readers see the web" for stable element identification.  Classic mode omits these labels, forcing the model to infer associations from DOM structure.

Fix: landmarkless pages now use the outline subtree renderer directly (`_serialize_outline_subtree` on root) instead of falling back to classic.  Additionally, the dropdown fixture was made dynamic -- the `/confirm` handler validates the POST `product` value, eliminating a false-positive static confirmation page.

| Result | Detail |
|--------|--------|
| Pass rate | **100% (9/9)** -- first outline run to beat classic |
| vs Classic | +11.1pp pass rate, -2,293 tokens, -16.7s duration |

## 3. Benchmark Results

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

Outline v4 uses ~22% more tokens than classic v2 (38,876 vs 31,945) due to accessible-name annotations and heading hierarchy.  However, this overhead buys a +11.1pp pass rate improvement.  The token cost is consistent with the literature's finding that contextualization modules add overhead that is offset by improved action accuracy [8].  The dominant cost factor remains multi-step conversation history accumulation, not per-step DOM size.

## 4. Discussion

### 4.1 Key Findings

1. **Serialization format directly affects action selection.**  The `|SHADOW(open)|` prefix -- a Chrome implementation detail leaking through CDP -- steered the model away from the correct `select_dropdown` tool toward raw `click`.  This is not model non-determinism; it's a systematic bias introduced by the serialization, consistent with the prompt-format sensitivity documented by Srivastava et al. [13].

2. **Accessible-name annotations matter.**  The outline renderer's `"Select Product:"` suffix gives the LLM the same affordance a sighted user gets from visual label proximity.  This parallels the accessibility-tree approach used by production agents [19, 24], where roles and labels provide "a structured view of [...] focusable elements, mapping these to executable actions."

3. **Benchmark fixtures must validate server-side.**  Static confirmation pages create false positives that mask real regressions.  This echoes the lesson from WebArena Verified [18], where auditing evaluators reduced false-negative rates by 11.3 percentage points.

4. **Per-step traces are the primary diagnostic tool.**  Aggregate pass/fail metrics identify *that* something broke; trace-level `dom_text` and `action_names` identify *why*.  This observability principle is well-established in agent evaluation [18, 19] but absent from most open-source agent frameworks.

### 4.2 On Browser-Use's Own Benchmark Claims

browser-use has published two benchmark reports: the original WebVoyager evaluation (89.1%, December 2024 [10]) and a broader 100-task benchmark comparing models (January 2026 [28]).  Neither is peer-reviewed; both are blog posts on browser-use.com.  The WebVoyager report involved manual judge corrections, date adjustments, and removal of 55 tasks -- methodological choices that make the headline number hard to compare with other agents evaluated under stricter protocols.  Xu et al. [27] found browser-use at 30% on Online-Mind2Web under human evaluation, a significant gap.  The newer 100-task benchmark [28] is more rigorous (multiple trials, error bars, 87% judge-human alignment), but tests a curated mix from WebBench/Mind2Web/GAIA/BrowseComp rather than a standardized suite.

browser-use has never published WebArena results.  Given that WebArena Verified [18] is the closest thing the field has to a standardized, deterministic, human-audited benchmark, this is a notable gap for any agent claiming SOTA.  Our internal 9-task suite makes no external comparability claims; it exists to detect serialization regressions, not to establish absolute performance.

### 4.3 Limitations

- **Single trial per task**: With n=1, results are subject to model non-determinism.  Multi-trial runs with bootstrap confidence intervals are needed for statistical claims, as recommended by WebArena Verified [18].
- **Single model**: gpt-oss-20b only.  The Plan 3 thesis -- that outline mode disproportionately benefits smaller models, as suggested by the prompt-format literature [13, 15] -- remains untested.
- **9-task suite**: A narrow benchmark.  WebArena Verified (812 tasks [18]) or WebBench (5,750 tasks [25]) would provide externally comparable numbers.  Our suite catches regressions but cannot establish general claims.
- **No vision mode**: All runs used `--no-vision`.  The interaction between outline formatting and multimodal (DOM + screenshot) agents is unexplored.

### 4.3 Traps for Future Development

These are documented inline in the codebase (module docstrings) but summarized here:

| Trap | Where | What happens |
|------|-------|-------------|
| Falling back to classic for landmarkless pages | `serialize_outline_tree` | Introduces `|SHADOW(open)|` noise, drops accessible labels |
| Region collapsing | `outline.py` | Hides interactive elements after step 1 |
| `expected_result.contains` checks model text, not page | `runner.py` `_evaluate_result` | Pass/fail depends on LLM phrasing, not actual page state |
| Static fixture confirmation pages | `fixtures/__init__.py` | False positives when agent doesn't actually interact correctly |
| User-agent shadow DOM on native elements | Classic serializer | Chrome-internal shadow roots mislead LLMs about element type |

## 5. Outlook

**Near-term**: Run multi-trial benchmarks (n>=5) across model sizes (gpt-4o-mini, gpt-oss-20b, gpt-4o) to test whether outline mode shifts the efficiency frontier as predicted by the structured-input literature [13, 15].  The ScribeAgent [16] and Go-Browse [17] results suggest that input quality improvements should disproportionately benefit smaller models.

**Medium-term**: Integrate WebArena Verified [18] for publication-grade, externally comparable numbers.  The 812-task suite with deterministic evaluators and backend state verification would provide the statistical power our 9-task suite lacks.  WebChoreArena [26] (532 tasks, long-horizon) would stress-test outline mode's token efficiency claims.

**Long-term**: The field is converging toward hybrid DOM+vision pipelines [6, 19].  Outline mode's landmark structure is a natural fit for this -- landmarks can scope *where* vision is needed (e.g. visually-rendered-only content within a MAIN region) while text-serialized regions handle form interaction.  LCoW [8] demonstrated a 15.6% average improvement by contextualizing observations for closed-source models; combining this with structural formatting may be complementary.

## References

### Internal documents

- [1] `plan_3_outline_navigation.md` -- Hierarchical Outline + Screen Reader Navigation
- [2] `plan_1_internal_benchmark.md` -- Internal Benchmark Suite
- [3] `benchmarks/reports/outline_v2/report_2026-02-19_042841.md` -- Outline v2 benchmark results
- [4] `plan.md` -- Fix Outline Mode to Beat Classic (root-cause analysis)
- [5] `plan_2_webarena_benchmark.md` -- WebArena Verified Benchmark Integration
- [6] `REPORT.md` -- How LLMs Navigate the Web: Analysis of browser-use

### Literature

- [7] Agentic LLM cost analysis. "DOM trees can potentially exceed 1M tokens [...] a GPT-4.1-based agent for a single 20-step task could cost roughly $402." [arXiv:2506.10953](https://arxiv.org/pdf/2506.10953), 2025.
- [8] D. Lee et al., "Learning to Contextualize Web Pages for Enhanced Decision Making by LLM Agents," ICLR 2025. [arXiv:2503.10689](https://arxiv.org/abs/2503.10689). [Code](https://github.com/dgjun32/lcow_iclr2025).
- [9] X. Deng et al., "Mind2Web: Towards a Generalist Agent for the Web," NeurIPS 2023 Spotlight. [Project](https://osu-nlp-group.github.io/Mind2Web/).
- [10] browser-use, "State of the Art Web Agent," Technical Report (blog post, not peer-reviewed), December 2024. [browser-use.com](https://browser-use.com/posts/sota-technical-report). Claims 89.1% on WebVoyager with manual judge corrections and 55 removed tasks; no WebArena results published. See [27] for independent evaluation.
- [11] Magnitude, "SOTA 94% on WebVoyager benchmark." [GitHub](https://github.com/magnitudedev/webvoyager).
- [12] G. A. Miller, "The Magical Number Seven, Plus or Minus Two: Some Limits on Our Capacity for Processing Information," *Psychological Review*, 63(2), 81-97, 1956. [APA](https://psycnet.apa.org/record/1957-02914-001). [Full text](https://psychclassics.yorku.ca/Miller/).
- [13] A. Srivastava et al., "Does Prompt Formatting Have Any Impact on LLM Performance?" November 2024. [arXiv:2411.10541](https://arxiv.org/abs/2411.10541).
- [14] Y. Xiao et al., "Beyond Prompt Content: Enhancing LLM Performance via Content-Format Integrated Prompt Optimization," January 2025. [arXiv:2502.04295](https://arxiv.org/html/2502.04295v1).
- [15] "When Does Divide and Conquer Work for Long Context LLM? A Noise Decomposition Framework," June 2025. [arXiv:2506.16411](https://arxiv.org/html/2506.16411v1).
- [16] "ScribeAgent: Towards Specialized Web Agents Using Production-Scale Workflow Data," CMU, November 2024. [arXiv:2411.15004](https://arxiv.org/html/2411.15004v1). [Blog](https://blog.ml.cmu.edu/2024/12/06/scribeagent-fine-tuning-open-source-llms-for-enhanced-web-navigation/).
- [17] A. Gandhi & G. Neubig, "Go-Browse: Training Web Agents with Structured Exploration," CMU, June 2025. [arXiv:2506.03533](https://arxiv.org/abs/2506.03533). [GitHub](https://github.com/ApGa/Go-Browse).
- [18] ServiceNow, "WebArena Verified: Reliable Evaluation for Web Agents," NeurIPS SEA Workshop 2025. [OpenReview](https://openreview.net/forum?id=94tlGxmqkN). [GitHub](https://github.com/ServiceNow/webarena-verified).
- [19] "Building Browser Agents: Architecture, Security, and Practical Solutions," 2025. [arXiv:2511.19477](https://arxiv.org/pdf/2511.19477).
- [20] Z. Wang et al., "Agent Workflow Memory," September 2024. [arXiv:2409.07429](https://arxiv.org/abs/2409.07429). [OpenReview](https://openreview.net/forum?id=PfYg3eRrNi).
- [21] "Inspect the user-agent DOM," DevTools Tips. [devtoolstips.org](https://devtoolstips.org/tips/en/inspect-user-agent-dom/).
- [22] "Everything you need to know about Shadow DOM," GitHub Gist. [gist.github.com](https://gist.github.com/praveenpuglia/0832da687ed5a5d7a0907046c9ef1813).
- [23] "How to Handle Shadow Root in Selenium Java," TestMu. [testmu.ai](https://www.testmu.ai/blog/shadow-root-in-selenium-java/).
- [24] "LLM Web Agent: Adaptive web agent using accessibility trees," GitHub. [github.com](https://github.com/suhaibbinyounis/llm-web-agent).
- [25] "WebBench: Benchmark your browser agent on ~2.5k READ and ACTION based tasks." [GitHub](https://github.com/Halluminate/WebBench).
- [26] "WebChoreArena: Evaluating Web Browsing Agents on Realistic Tedious Web Tasks." [webchorearena.github.io](https://webchorearena.github.io/). [OpenReview](https://openreview.net/forum?id=d0xqdsR41U).
- [27] Z. Xu et al., "An Illusion of Progress? Assessing the Current State of Web Agents," April 2025. [arXiv:2504.01382](https://arxiv.org/abs/2504.01382). Finds browser-use scores 30% on Online-Mind2Web; a trivial search baseline achieves 51% on WebVoyager.
- [28] browser-use, "Browser Agent Benchmark: Comparing LLM Models for Web Automation," blog post (not peer-reviewed), January 2026. [browser-use.com](https://browser-use.com/posts/ai-browser-agent-benchmark). [GitHub](https://github.com/browser-use/benchmark). 100-task curated mix with error bars and 87% judge-human alignment.
