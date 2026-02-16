"""Report generation for benchmark results — Markdown + JSON output."""

import json
from datetime import datetime, timezone
from pathlib import Path

from benchmarks.baseline import compare


def _fmt_cost(val: float) -> str:
	return f'${val:.4f}'


def _fmt_float(val: float, decimals: int = 1) -> str:
	return f'{val:.{decimals}f}'


def _fmt_int(val: float) -> str:
	return f'{val:,.0f}'


def _fmt_pass_rate(passes: int, total: int) -> str:
	return f'{passes}/{total}'


def generate_report(
	results: dict,
	baseline: dict | None = None,
	output_dir: str | Path = 'benchmarks/reports',
) -> tuple[Path, Path]:
	"""Generate Markdown and JSON reports from benchmark results.

	Args:
		results: Full benchmark results dict with 'tasks' and 'aggregate' keys.
		baseline: Optional baseline dict for comparison.
		output_dir: Directory to write reports into.

	Returns:
		Tuple of (markdown_path, json_path).
	"""
	output_dir = Path(output_dir)
	output_dir.mkdir(parents=True, exist_ok=True)

	timestamp = datetime.now(timezone.utc).strftime('%Y-%m-%d_%H%M%S')
	model = results.get('model', 'unknown')
	trials = results.get('trials_per_task', 1)

	# Comparison data
	comparison = compare(results, baseline) if baseline else None

	# --- Markdown Report ---
	lines: list[str] = []
	lines.append(f'## browser-use Benchmark Report — {datetime.now(timezone.utc).strftime("%Y-%m-%d")}')
	lines.append(f'Model: {model} | Trials per task: {trials}')
	lines.append('')

	# Task table
	lines.append('| Task | Pass Rate | Avg Steps | Avg Tokens | Avg Cost | Avg Duration | Downloads |')
	lines.append('|------|-----------|-----------|------------|----------|--------------|-----------|')

	tasks = results.get('tasks', {})
	for task_name, task_data in tasks.items():
		n_trials = task_data.get('n_trials', trials)
		passes = int(task_data.get('pass_rate', 0) * n_trials)
		downloads_expected = task_data.get('files_downloaded_expected')
		downloads_actual = task_data.get('avg_files_downloaded')

		if downloads_expected is not None and downloads_actual is not None:
			downloads_str = f'{_fmt_float(downloads_actual)}/{downloads_expected}'
		else:
			downloads_str = '\u2014'

		lines.append(
			f'| {task_name:<25} '
			f'| {_fmt_pass_rate(passes, n_trials):<9} '
			f'| {_fmt_float(task_data.get("avg_steps", 0)):<9} '
			f'| {_fmt_int(task_data.get("avg_tokens", 0)):<10} '
			f'| {_fmt_cost(task_data.get("avg_cost", 0)):<8} '
			f'| {_fmt_float(task_data.get("avg_duration", 0))}s{"":>9} '
			f'| {downloads_str:<9} |'
		)

	lines.append('')

	# Aggregate comparison table
	if comparison:
		lines.append('### Aggregate')
		lines.append('| Metric | Current | Baseline | Delta |')
		lines.append('|--------|---------|----------|-------|')

		agg_deltas = comparison.get('aggregate', {})

		format_map = {
			'pass_rate': lambda v: f'{v * 100:.1f}%',
			'avg_steps': lambda v: _fmt_float(v),
			'avg_tokens': lambda v: _fmt_int(v),
			'avg_cost': lambda v: _fmt_cost(v),
			'avg_duration': lambda v: f'{_fmt_float(v)}s',
		}

		delta_format_map = {
			'pass_rate': lambda d: f'{d * 100:+.1f}pp',
			'avg_steps': lambda d: f'{d:+.1f}',
			'avg_tokens': lambda d: f'{d:+,.0f}',
			'avg_cost': lambda d: f'{d:+.4f}',
			'avg_duration': lambda d: f'{d:+.1f}s',
		}

		metric_labels = {
			'pass_rate': 'Pass Rate',
			'avg_steps': 'Avg Steps',
			'avg_tokens': 'Avg Tokens',
			'avg_cost': 'Avg Cost',
			'avg_duration': 'Avg Duration',
		}

		for metric, label in metric_labels.items():
			if metric in agg_deltas:
				d = agg_deltas[metric]
				cur_str = format_map[metric](d['current'])
				base_str = format_map[metric](d['baseline'])
				delta_str = delta_format_map[metric](d['delta'])
				flag = ' \u26a0\ufe0f' if d.get('regression') else ''
				lines.append(f'| {label:<10} | {cur_str:<7} | {base_str:<8} | {delta_str}{flag} |')

		lines.append('')

		if comparison.get('has_regressions'):
			lines.append('> **Warning**: Regressions detected in one or more metrics.')
			lines.append('')
	else:
		# Just show aggregate without comparison
		agg = results.get('aggregate', {})
		if agg:
			lines.append('### Aggregate')
			lines.append(f'- Pass Rate: {agg.get("pass_rate", 0) * 100:.1f}%')
			lines.append(f'- Avg Steps: {_fmt_float(agg.get("avg_steps", 0))}')
			lines.append(f'- Avg Tokens: {_fmt_int(agg.get("avg_tokens", 0))}')
			lines.append(f'- Avg Cost: {_fmt_cost(agg.get("avg_cost", 0))}')
			lines.append(f'- Avg Duration: {_fmt_float(agg.get("avg_duration", 0))}s')
			lines.append('')

	md_content = '\n'.join(lines)

	# Write files
	md_path = output_dir / f'report_{timestamp}.md'
	json_path = output_dir / f'report_{timestamp}.json'

	md_path.write_text(md_content, encoding='utf-8')

	# JSON sidecar with full per-trial breakdowns
	json_output = {
		'timestamp': timestamp,
		'model': model,
		'trials_per_task': trials,
		'tasks': tasks,
		'aggregate': results.get('aggregate', {}),
	}
	if comparison:
		json_output['comparison'] = comparison

	with open(json_path, 'w', encoding='utf-8') as f:
		json.dump(json_output, f, indent=2, default=str)

	return md_path, json_path
