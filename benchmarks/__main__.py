"""CLI entry point for the benchmark suite.

Usage:
    python -m benchmarks [--model mock] [--trials 3] [--tasks benchmarks/tasks/*.yaml] [--output benchmarks/reports/]
    python -m benchmarks --outline-mode  # Run with hierarchical outline serialization
"""

import argparse
import asyncio
import logging
from pathlib import Path

from benchmarks.runner import run_benchmark

logging.basicConfig(
	level=logging.INFO,
	format='%(levelname)-8s [%(name)s] %(message)s',
)


def main() -> None:
	parser = argparse.ArgumentParser(
		description='browser-use internal benchmark suite',
		prog='python -m benchmarks',
	)
	parser.add_argument(
		'--model',
		default='mock',
		help='Model name or "mock" for deterministic mock LLM (default: mock)',
	)
	parser.add_argument(
		'--trials',
		type=int,
		default=1,
		help='Number of trials per task (default: 1)',
	)
	parser.add_argument(
		'--tasks',
		nargs='*',
		default=None,
		help='Paths to YAML task files (default: all in benchmarks/tasks/)',
	)
	parser.add_argument(
		'--output',
		default='benchmarks/reports',
		help='Output directory for reports (default: benchmarks/reports/)',
	)
	parser.add_argument(
		'--save-baseline',
		action='store_true',
		help='Save results as the new baseline for this model',
	)
	parser.add_argument(
		'--compare-baseline',
		action='store_true',
		default=True,
		help='Compare against stored baseline (default: True)',
	)
	parser.add_argument(
		'--no-compare-baseline',
		action='store_false',
		dest='compare_baseline',
		help='Skip baseline comparison',
	)
	parser.add_argument(
		'--outline-mode',
		action='store_true',
		default=False,
		help='Use hierarchical landmark-grouped outline serialization instead of flat element list',
	)

	args = parser.parse_args()

	task_paths = [Path(p) for p in args.tasks] if args.tasks else None

	results = asyncio.run(
		run_benchmark(
			model=args.model,
			trials=args.trials,
			task_paths=task_paths,
			output_dir=args.output,
			save_baseline_flag=args.save_baseline,
			compare_baseline_flag=args.compare_baseline,
			outline_mode=args.outline_mode,
		)
	)

	# Print summary to stdout
	agg = results.get('aggregate', {})
	print('\n=== Benchmark Summary ===')
	print(f'Model: {results["model"]}')
	print(f'Outline mode: {results.get("outline_mode", False)}')
	print(f'Tasks: {len(results["tasks"])}')
	print(f'Trials per task: {results["trials_per_task"]}')
	if agg:
		print(f'Overall pass rate: {agg.get("pass_rate", 0) * 100:.1f}%')
		print(f'Avg steps: {agg.get("avg_steps", 0):.1f}')
		print(f'Avg tokens: {agg.get("avg_tokens", 0):,.0f}')
		print(f'Avg cost: ${agg.get("avg_cost", 0):.4f}')


if __name__ == '__main__':
	main()
