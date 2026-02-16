"""Benchmark orchestrator: loads tasks, runs trials, collects metrics."""

import logging
import os
import tempfile
from collections.abc import Sequence
from pathlib import Path

import yaml
from pytest_httpserver import HTTPServer

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
) -> bool:
	"""Evaluate whether a run meets the programmatic success criteria.

	In mock mode, files_downloaded is tracked as a metric but not used for
	pass/fail since mock actions can't trigger real DOM interactions reliably.
	With real LLMs, files_downloaded is enforced.
	"""
	if expected_result is None:
		# No criteria specified, use agent's own success assessment
		return history.is_successful() is True

	final = history.final_result() or ''

	# Check contains criteria
	contains = expected_result.get('contains', [])
	for needle in contains:
		if needle not in final:
			logger.info(f'  FAIL: expected result to contain {needle!r}, got: {final[:200]}')
			return False

	# Check files_downloaded (enforced only with real LLMs)
	expected_downloads = expected_result.get('files_downloaded')
	if expected_downloads is not None and downloads_path is not None and model != 'mock':
		actual = _count_downloads(downloads_path)
		if actual < expected_downloads:
			logger.info(f'  FAIL: expected {expected_downloads} downloads, found {actual}')
			return False

	return True


async def _run_single_trial(
	task: dict,
	fixture_dict: dict,
	model: str,
	server: HTTPServer,
	trial_num: int,
) -> tuple[TaskRunMetrics, int]:
	"""Run a single trial of a benchmark task.

	Returns:
		Tuple of (metrics, actual_download_count).
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
		else:
			# Real LLM mode — use ChatOpenAI as default provider.
			# For other providers, extend with provider-prefixed model names.
			from browser_use.llm import ChatOpenAI

			llm = ChatOpenAI(model=model)

		max_steps = task.get('max_steps', 10)

		agent = Agent(
			task=task_str,
			llm=llm,
			browser_session=session,
		)

		history = await agent.run(max_steps=max_steps)

		# Extract metrics
		metrics = extract_metrics(history)

		# Override success based on programmatic evaluation
		expected_result = task.get('expected_result')
		programmatic_success = _evaluate_result(history, expected_result, downloads_dir, model=model)
		actual_downloads = _count_downloads(downloads_dir)
		metrics = metrics.model_copy(update={'success': programmatic_success})

		return metrics, actual_downloads

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
		), 0
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
) -> dict:
	"""Run the full benchmark suite.

	Args:
		model: Model name or 'mock' for deterministic mock LLM.
		trials: Number of trials per task.
		task_paths: Paths to YAML task files. If None, loads all from benchmarks/tasks/.
		output_dir: Directory for report output.
		save_baseline_flag: If True, save results as baseline.
		compare_baseline_flag: If True, compare against stored baseline.

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
				metrics, download_count = await _run_single_trial(task, fixture_dict, model, server, trial_num)
				trial_metrics.append(metrics)
				trial_download_counts.append(download_count)

			# Aggregate
			agg = aggregate_metrics(trial_metrics)
			all_aggregates.append(agg)

			# Track download metrics for download tasks
			expected_result = task.get('expected_result', {}) or {}
			files_downloaded_expected = expected_result.get('files_downloaded')

			task_result = {
				'n_trials': agg.n_trials,
				'pass_rate': agg.pass_rate,
				'avg_steps': agg.avg_steps,
				'std_steps': agg.std_steps,
				'avg_tokens': agg.avg_tokens,
				'std_tokens': agg.std_tokens,
				'avg_cost': agg.avg_cost,
				'avg_duration': agg.avg_duration,
				'avg_error_rate': agg.avg_error_rate,
				'action_distribution': agg.action_distribution,
			}
			if files_downloaded_expected is not None:
				task_result['files_downloaded_expected'] = files_downloaded_expected
				avg_downloads = sum(trial_download_counts) / len(trial_download_counts) if trial_download_counts else 0
				task_result['avg_files_downloaded'] = avg_downloads

			all_task_results[task_name] = task_result

			logger.info(
				f'  {task_name}: pass_rate={agg.pass_rate:.0%}, avg_steps={agg.avg_steps:.1f}, avg_tokens={agg.avg_tokens:.0f}'
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
			'avg_steps': sum(a.avg_steps for a in all_aggregates) / len(all_aggregates),
			'avg_tokens': sum(a.avg_tokens for a in all_aggregates) / len(all_aggregates),
			'avg_cost': sum(a.avg_cost for a in all_aggregates) / len(all_aggregates),
			'avg_duration': sum(a.avg_duration for a in all_aggregates) / len(all_aggregates),
		}
	else:
		suite_aggregate = {}

	results = {
		'model': model,
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
