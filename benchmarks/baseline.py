"""Baseline storage, loading, and comparison for benchmark results."""

import json
from datetime import datetime, timezone
from pathlib import Path

BASELINES_DIR = Path(__file__).parent / 'baselines'


def load_baseline(model: str) -> dict | None:
	"""Load a stored baseline for the given model. Returns None if not found."""
	path = BASELINES_DIR / f'{model}.json'
	if not path.exists():
		return None
	with open(path, encoding='utf-8') as f:
		return json.load(f)


def save_baseline(model: str, results: dict) -> None:
	"""Save benchmark results as a baseline for the given model."""
	BASELINES_DIR.mkdir(parents=True, exist_ok=True)
	path = BASELINES_DIR / f'{model}.json'

	results['timestamp'] = datetime.now(timezone.utc).isoformat()
	results['model'] = model

	with open(path, 'w', encoding='utf-8') as f:
		json.dump(results, f, indent=2)


def compare(current: dict, baseline: dict) -> dict:
	"""Compare current results against a baseline.

	Returns a dict of per-metric deltas with direction indicators.
	Flags regressions when:
	  - avg_steps increases by >20%
	  - pass_rate decreases by >10 percentage points
	"""
	deltas: dict[str, dict] = {}

	current_agg = current.get('aggregate', {})
	baseline_agg = baseline.get('aggregate', {})

	metrics_config = {
		'pass_rate': {'format': 'pp', 'better': 'higher'},
		'avg_score': {'format': 'float', 'better': 'higher'},
		'avg_steps': {'format': 'float', 'better': 'lower'},
		'avg_tokens': {'format': 'int', 'better': 'lower'},
		'avg_cost': {'format': 'float', 'better': 'lower'},
		'avg_duration': {'format': 'float', 'better': 'lower'},
	}

	for metric, config in metrics_config.items():
		cur_val = current_agg.get(metric)
		base_val = baseline_agg.get(metric)

		if cur_val is None or base_val is None:
			continue

		delta = cur_val - base_val
		regression = False

		if metric == 'pass_rate' and delta < -0.10:
			regression = True
		elif metric == 'avg_steps' and base_val > 0 and delta / base_val > 0.20:
			regression = True

		# Direction indicator
		if config['better'] == 'lower':
			direction = 'improved' if delta < 0 else ('regressed' if delta > 0 else 'unchanged')
		else:
			direction = 'improved' if delta > 0 else ('regressed' if delta < 0 else 'unchanged')

		deltas[metric] = {
			'current': cur_val,
			'baseline': base_val,
			'delta': delta,
			'direction': direction,
			'regression': regression,
		}

	# Per-task comparison
	task_deltas: dict[str, dict] = {}
	current_tasks = current.get('tasks', {})
	baseline_tasks = baseline.get('tasks', {})

	for task_name in current_tasks:
		if task_name not in baseline_tasks:
			continue
		cur_task = current_tasks[task_name]
		base_task = baseline_tasks[task_name]
		task_deltas[task_name] = {
			'pass_rate_delta': cur_task.get('pass_rate', 0) - base_task.get('pass_rate', 0),
			'avg_score_delta': cur_task.get('avg_score', 0) - base_task.get('avg_score', 0),
			'avg_steps_delta': cur_task.get('avg_steps', 0) - base_task.get('avg_steps', 0),
			'avg_tokens_delta': cur_task.get('avg_tokens', 0) - base_task.get('avg_tokens', 0),
		}

	return {
		'aggregate': deltas,
		'tasks': task_deltas,
		'has_regressions': any(d.get('regression', False) for d in deltas.values()),
	}
