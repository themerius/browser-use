"""CLI entry point for the benchmark suite.

Usage:
    # Run benchmarks with mock LLM (fast, deterministic)
    python -m benchmarks

    # Run with a real model via OpenRouter
    python -m benchmarks --model openai/gpt-oss-20b --no-vision

    # Run with outline mode
    python -m benchmarks --model openai/gpt-oss-20b --outline-mode --no-vision

    # Write per-step trace files for debugging (see benchmarks/trace.py)
    python -m benchmarks --model openai/gpt-oss-20b --trace

    # Compare classic vs outline DOM serialization (no agent run needed)
    python -m benchmarks --compare-dom

    # Full options
    python -m benchmarks --model mock --trials 3 --tasks benchmarks/tasks/*.yaml \\
        --output benchmarks/reports/ --outline-mode --trace --save-baseline
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
	parser.add_argument(
		'--no-vision',
		action='store_true',
		default=False,
		help='Disable screenshot/vision input (for text-only models)',
	)
	parser.add_argument(
		'--trace',
		action='store_true',
		default=False,
		help='Write per-step JSONL trace files for each trial (see benchmarks/trace.py for schema)',
	)
	parser.add_argument(
		'--compare-dom',
		action='store_true',
		default=False,
		help='Compare classic vs outline DOM serialization side-by-side (no agent run)',
	)

	args = parser.parse_args()

	task_paths = [Path(p) for p in args.tasks] if args.tasks else None

	# --compare-dom is a standalone diagnostic mode — no benchmark run
	if args.compare_dom:
		from benchmarks.compare_dom import run_dom_comparison
		report_path = asyncio.run(
			run_dom_comparison(task_paths=task_paths, output_dir=args.output)
		)
		print(f'\nDOM comparison report: {report_path}')
		return

	results = asyncio.run(
		run_benchmark(
			model=args.model,
			trials=args.trials,
			task_paths=task_paths,
			output_dir=args.output,
			save_baseline_flag=args.save_baseline,
			compare_baseline_flag=args.compare_baseline,
			outline_mode=args.outline_mode,
			use_vision=not args.no_vision,
			trace=args.trace,
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
		print(f'Avg score: {agg.get("avg_score", 0):.2f}')
		print(f'Avg steps: {agg.get("avg_steps", 0):.1f}')
		print(f'Avg tokens: {agg.get("avg_tokens", 0):,.0f}')
		print(f'Avg cost: ${agg.get("avg_cost", 0):.4f}')

	# Print failure summary for any failed tasks
	tasks = results.get('tasks', {})
	failed = {name: data for name, data in tasks.items() if data.get('pass_rate', 1.0) < 1.0}
	if failed:
		print('\n=== Failed Tasks ===')
		for name, data in failed.items():
			print(f'\n  {name} (pass_rate={data["pass_rate"]:.0%}, score={data.get("avg_score", 0):.2f})')
			errors = data.get('error_messages', [])
			if errors:
				for e in errors[:3]:  # Show first 3 errors
					print(f'    Error: {e[:120]}')
			diagnostics = data.get('trial_diagnostics', [])
			for i, d in enumerate(diagnostics):
				if not d.get('success'):
					reasoning = d.get('last_model_reasoning')
					if reasoning:
						print(f'    Trial {i+1} last reasoning:')
						if reasoning.get('next_goal'):
							print(f'      Goal: {reasoning["next_goal"][:100]}')
						if reasoning.get('memory'):
							print(f'      Memory: {reasoning["memory"][:100]}')


if __name__ == '__main__':
	main()
