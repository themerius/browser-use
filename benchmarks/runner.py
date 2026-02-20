"""Benchmark orchestrator: loads tasks, runs trials, collects metrics.

CLI flags relevant to this module:
    --trace         Write per-step JSONL trace files (see ``benchmarks/trace.py``)
    --compare-dom   Skip agent runs; just compare classic vs outline serialization
                    (see ``benchmarks/compare_dom.py``)

The runner returns a results dict consumed by ``benchmarks/report.py`` for
Markdown/JSON report generation.
"""

from __future__ import annotations

import logging
import os
import tempfile
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING

import yaml
from pytest_httpserver import HTTPServer

if TYPE_CHECKING:
	from browser_use.agent.views import AgentHistoryList

import benchmarks.fixtures as fixtures_module
from benchmarks.baseline import compare, load_baseline, save_baseline
from benchmarks.conftest import create_benchmark_browser_session, register_fixture_routes
from benchmarks.metrics import TaskAggregateMetrics, TaskRunMetrics, aggregate_metrics, extract_metrics
from benchmarks.report import generate_report

# Ensure mock LLM works without API keys
os.environ['SKIP_LLM_API_KEY_VERIFICATION'] = 'true'
os.environ['ANONYMIZED_TELEMETRY'] = 'false'

logger = logging.getLogger('benchmarks.runner')


def _load_task(path: str | Path) -> dict:
	"""Load a benchmark task from a YAML file."""
	with open(path, encoding='utf-8') as f:
		return yaml.safe_load(f)


def _get_fixture(name: str) -> dict:
	"""Look up a fixture function by name from the fixtures module."""
	fn = getattr(fixtures_module, name, None)
	assert fn is not None, f'Fixture function {name!r} not found in benchmarks.fixtures'
	return fn()


def _create_mock_llm(actions: list[str] | None = None):
	"""Create a mock LLM using the shared test utility."""
	# Import here to avoid pulling in test dependencies at module level
	from tests.ci.conftest import create_mock_llm

	return create_mock_llm(actions=actions)


def _count_downloads(downloads_path: str | None) -> int:
	"""Count actual files in the downloads directory."""
	if downloads_path is None:
		return 0
	dl_path = Path(downloads_path)
	if not dl_path.exists():
		return 0
	return len([f for f in dl_path.iterdir() if f.is_file()])


def _evaluate_result(
	history,
	expected_result: dict | None,
	downloads_path: str | None,
	model: str = 'mock',
) -> tuple[bool, float, dict[str, bool]]:
	"""Evaluate whether a run meets the programmatic success criteria.

	Returns:
		Tuple of (passed, score, criteria_met) where:
		- passed: True only if ALL criteria are satisfied
		- score: 0.0–1.0 fraction of criteria met (partial credit)
		- criteria_met: per-criterion breakdown {name: bool}

	In mock mode, files_downloaded is tracked as a metric but not used for
	pass/fail since mock actions can't trigger real DOM interactions reliably.
	With real LLMs, files_downloaded is enforced.

	**Pitfall — ``contains`` checks the agent's ``done`` text, not the page**:

	  The ``contains`` criterion is matched against the text the LLM passes to
	  the ``done(text=...)`` action.  This means pass/fail depends on *how the
	  model phrases its summary*, not on what the page actually displayed.

	  Example: Dropdown Interaction's confirm page shows ``"Order Confirmed!"``
	  but the model might only quote the body paragraph
	  (``"Your order for the Pro Plan has been placed successfully."``),
	  omitting the heading.  If ``expected_result.contains`` is
	  ``["Order Confirmed"]``, the task fails despite the page being correct.

	  Mitigations:
	    1. Make fixtures validate server-side (use callable handlers that
	       check POST/query values — see ``benchmarks/conftest.py`` docstring).
	    2. Choose ``contains`` needles that are likely to appear in any
	       reasonable summary (e.g. a product name rather than a heading).
	    3. Consider adding a ``page_contains`` criterion that checks the
	       actual page HTML/text at the final URL, not the model's summary.
	"""
	if expected_result is None:
		# No criteria specified, use agent's own success assessment
		passed = history.is_successful() is True
		return passed, 1.0 if passed else 0.0, {'agent_self_assessment': passed}

	criteria_met: dict[str, bool] = {}
	final = history.final_result() or ''
	final_lower = final.lower()

	# Check contains criteria (case-insensitive to handle LLM phrasing variation)
	contains = expected_result.get('contains', [])
	for needle in contains:
		key = f'contains:{needle}'
		found = needle.lower() in final_lower
		criteria_met[key] = found
		if not found:
			logger.info(f'  FAIL: expected result to contain {needle!r}, got: {final[:200]}')

	# Check files_downloaded (enforced only with real LLMs)
	expected_downloads = expected_result.get('files_downloaded')
	if expected_downloads is not None and downloads_path is not None and model != 'mock':
		actual = _count_downloads(downloads_path)
		met = actual >= expected_downloads
		criteria_met[f'files_downloaded>={expected_downloads}'] = met
		if not met:
			logger.info(f'  FAIL: expected {expected_downloads} downloads, found {actual}')

	# Compute partial score
	if criteria_met:
		n_met = sum(1 for v in criteria_met.values() if v)
		score = n_met / len(criteria_met)
	else:
		score = 1.0  # No criteria = vacuously satisfied

	passed = all(criteria_met.values()) if criteria_met else True
	return passed, score, criteria_met


async def _run_single_trial(
	task: dict,
	fixture_dict: dict,
	model: str,
	server: HTTPServer,
	trial_num: int,
	outline_mode: bool = False,
	use_vision: bool = True,
) -> tuple[TaskRunMetrics, int, AgentHistoryList | None]:
	"""Run a single trial of a benchmark task.

	Returns:
		Tuple of (metrics, actual_download_count, history).
		``history`` is the full ``AgentHistoryList`` — used by ``--trace`` to
		write per-step JSONL logs.  It is ``None`` when the trial errors out
		before the agent produces any history.
	"""
	from browser_use import Agent

	task_name = task['name']
	logger.info(f'  Trial {trial_num} of {task_name!r}')

	downloads_dir = tempfile.mkdtemp(prefix=f'bench_{task_name.replace(" ", "_")}_')

	# Create browser session
	session = await create_benchmark_browser_session(downloads_path=downloads_dir)

	try:
		base_url = f'http://{server.host}:{server.port}'

		# Construct the task string with the base URL injected
		task_str = task['task']
		# Replace any relative URL references with full URLs for the agent
		# The fixture's first path is the entry point
		fixture_paths = list(fixture_dict.keys())
		entry_path = fixture_paths[0]
		task_str = f'{task_str}\n\nStart at: {base_url}{entry_path}'

		# Create LLM
		if model == 'mock':
			mock_actions = task.get('mock_actions')
			llm = _create_mock_llm(actions=mock_actions)
		elif '/' in model:
			# Model names containing '/' are routed through OpenRouter
			# e.g. 'openai/gpt-oss-20b', 'anthropic/claude-3.5-sonnet'
			from browser_use.llm import ChatOpenRouter

			api_key = os.environ.get('OPENROUTER_API_KEY', '').strip()
			assert api_key, 'OPENROUTER_API_KEY must be set for OpenRouter models'
			llm = ChatOpenRouter(model=model, api_key=api_key)
		else:
			# Real LLM mode — use ChatOpenAI as default provider.
			from browser_use.llm import ChatOpenAI

			llm = ChatOpenAI(model=model)

		max_steps = task.get('max_steps', 10)

		agent = Agent(
			task=task_str,
			llm=llm,
			browser_session=session,
			outline_mode=outline_mode,
			use_vision=use_vision,
		)

		history = await agent.run(max_steps=max_steps)

		# Extract metrics
		metrics = extract_metrics(history)

		# Override success based on programmatic evaluation
		expected_result = task.get('expected_result')
		passed, score, criteria_met = _evaluate_result(history, expected_result, downloads_dir, model=model)
		actual_downloads = _count_downloads(downloads_dir)
		metrics = metrics.model_copy(update={
			'success': passed,
			'score': score,
			'criteria_met': criteria_met,
		})

		return metrics, actual_downloads, history

	except Exception as e:
		logger.error(f'  Trial {trial_num} of {task_name!r} failed: {e}')
		return TaskRunMetrics(
			steps=0,
			total_tokens=0,
			prompt_tokens=0,
			completion_tokens=0,
			cached_tokens=0,
			total_cost=0.0,
			duration_seconds=0.0,
			success=False,
			is_done=False,
			error_count=1,
			action_names=[],
			action_distribution={},
			urls_visited=[],
			final_result=str(e),
			error_messages=[str(e)],
		), 0, None
	finally:
		await session.kill()
		await session.event_bus.stop(clear=True, timeout=5)


async def run_benchmark(
	model: str = 'mock',
	trials: int = 1,
	task_paths: Sequence[str | Path] | None = None,
	output_dir: str | Path = 'benchmarks/reports',
	save_baseline_flag: bool = False,
	compare_baseline_flag: bool = True,
	outline_mode: bool = False,
	use_vision: bool = True,
	trace: bool = False,
) -> dict:
	"""Run the full benchmark suite.

	Args:
		model: Model name or 'mock' for deterministic mock LLM.
		trials: Number of trials per task.
		task_paths: Paths to YAML task files. If None, loads all from benchmarks/tasks/.
		output_dir: Directory for report output.
		save_baseline_flag: If True, save results as baseline.
		compare_baseline_flag: If True, compare against stored baseline.
		outline_mode: If True, use hierarchical outline DOM serialization.
		use_vision: If True, include screenshots in LLM context.
		trace: If True, write per-step JSONL trace files (see ``benchmarks/trace.py``).

	Returns:
		Full results dict.
	"""
	import socketserver

	# Fix for httpserver hanging
	socketserver.ThreadingMixIn.block_on_close = False
	socketserver.ThreadingMixIn.daemon_threads = True

	# Load tasks
	if task_paths is None:
		tasks_dir = Path(__file__).parent / 'tasks'
		task_paths = sorted(tasks_dir.glob('*.yaml'))

	tasks = [_load_task(p) for p in task_paths]
	logger.info(f'Loaded {len(tasks)} benchmark tasks')

	# Results accumulator
	all_task_results: dict[str, dict] = {}
	all_aggregates: list[TaskAggregateMetrics] = []

	for task in tasks:
		task_name = task['name']
		fixture_name = task['fixture']
		logger.info(f'Running task: {task_name!r} (fixture: {fixture_name})')

		fixture_dict = _get_fixture(fixture_name)

		# Start HTTP server for this task
		server = HTTPServer()
		server.start()

		try:
			register_fixture_routes(server, fixture_dict)

			trial_metrics: list[TaskRunMetrics] = []
			trial_download_counts: list[int] = []
			for trial_num in range(1, trials + 1):
				metrics, download_count, history = await _run_single_trial(task, fixture_dict, model, server, trial_num, outline_mode=outline_mode, use_vision=use_vision)
				trial_metrics.append(metrics)
				trial_download_counts.append(download_count)

				# Write per-step trace if requested
				if trace and history is not None:
					from benchmarks.trace import write_trace
					trace_dir = Path(output_dir) / 'traces'
					trace_path = write_trace(history, trace_dir, task_name, trial_num)
					logger.info(f'    Trace written to {trace_path}')

			# Aggregate
			agg = aggregate_metrics(trial_metrics)
			all_aggregates.append(agg)

			# Track download metrics for download tasks
			expected_result = task.get('expected_result', {}) or {}
			files_downloaded_expected = expected_result.get('files_downloaded')

			# Collect failure diagnostics from individual trials
			all_errors: list[str] = []
			per_trial_diagnostics: list[dict] = []
			dom_char_counts: list[int] = []
			for t in trial_metrics:
				all_errors.extend(t.error_messages)
				per_trial_diagnostics.append({
					'success': t.success,
					'final_result': t.final_result,
					'error_messages': t.error_messages,
					'last_model_reasoning': t.last_model_reasoning,
					'dom_text_chars': t.dom_text_chars,
				})
				if t.dom_text_chars is not None:
					dom_char_counts.append(t.dom_text_chars)

			task_result = {
				'n_trials': agg.n_trials,
				'pass_rate': agg.pass_rate,
				'avg_score': agg.avg_score,
				'avg_steps': agg.avg_steps,
				'std_steps': agg.std_steps,
				'avg_tokens': agg.avg_tokens,
				'std_tokens': agg.std_tokens,
				'avg_cost': agg.avg_cost,
				'avg_duration': agg.avg_duration,
				'avg_error_rate': agg.avg_error_rate,
				'action_distribution': agg.action_distribution,
				# Failure diagnostics — useful for debugging failed tasks
				'error_messages': all_errors,
				'trial_diagnostics': per_trial_diagnostics,
				'avg_dom_text_chars': int(sum(dom_char_counts) / len(dom_char_counts)) if dom_char_counts else None,
			}
			if files_downloaded_expected is not None:
				task_result['files_downloaded_expected'] = files_downloaded_expected
				avg_downloads = sum(trial_download_counts) / len(trial_download_counts) if trial_download_counts else 0
				task_result['avg_files_downloaded'] = avg_downloads

			all_task_results[task_name] = task_result

			logger.info(
				f'  {task_name}: pass_rate={agg.pass_rate:.0%}, avg_score={agg.avg_score:.2f}, avg_steps={agg.avg_steps:.1f}, avg_tokens={agg.avg_tokens:.0f}'
			)

		finally:
			server.clear()
			if server.is_running():
				server.stop()

	# Compute suite-wide aggregates
	if all_aggregates:
		total_trials = sum(a.n_trials for a in all_aggregates)
		total_passes = sum(a.pass_rate * a.n_trials for a in all_aggregates)
		suite_aggregate = {
			'pass_rate': total_passes / total_trials if total_trials > 0 else 0,
			'avg_score': sum(a.avg_score * a.n_trials for a in all_aggregates) / total_trials if total_trials > 0 else 0,
			'avg_steps': sum(a.avg_steps for a in all_aggregates) / len(all_aggregates),
			'avg_tokens': sum(a.avg_tokens for a in all_aggregates) / len(all_aggregates),
			'avg_cost': sum(a.avg_cost for a in all_aggregates) / len(all_aggregates),
			'avg_duration': sum(a.avg_duration for a in all_aggregates) / len(all_aggregates),
		}
	else:
		suite_aggregate = {}

	results = {
		'model': model,
		'outline_mode': outline_mode,
		'trials_per_task': trials,
		'tasks': all_task_results,
		'aggregate': suite_aggregate,
	}

	# Baseline comparison
	baseline = None
	if compare_baseline_flag:
		baseline = load_baseline(model)
		if baseline:
			comparison = compare(results, baseline)
			if comparison.get('has_regressions'):
				logger.warning('REGRESSIONS DETECTED — see report for details')
		else:
			logger.info(f'No baseline found for model {model!r}')

	# Save baseline if requested
	if save_baseline_flag:
		save_baseline(model, results.copy())
		logger.info(f'Baseline saved for model {model!r}')

	# Generate report
	md_path, json_path = generate_report(results, baseline=baseline, output_dir=output_dir)
	logger.info(f'Report written to {md_path}')
	logger.info(f'JSON data written to {json_path}')

	return results
