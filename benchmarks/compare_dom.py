"""Side-by-side comparison of classic vs outline DOM serialization.

Navigates to each benchmark fixture page once, serializes the DOM in both
classic and outline mode, and writes a human-readable diff report.  No agent
run is needed — this is a pure serialization comparison tool.

Usage (CLI):
    python -m benchmarks --compare-dom

    # Optionally restrict to specific tasks:
    python -m benchmarks --compare-dom --tasks benchmarks/tasks/dropdown_interaction.yaml

Output lands in ``benchmarks/reports/dom_comparison_<timestamp>.md`` and shows,
for each task:
    1. Character counts for both modes
    2. Full text of both representations (for quick visual diffing)
    3. Key structural differences (landmarks found, compound_components present, etc.)

This is a *diagnostic* tool, not a benchmark.  Use it to answer:
    - "Does outline mode lose any information vs classic for this page?"
    - "Why does the model fail on dropdown interaction in outline mode?"
    - "How much larger/smaller is the outline representation?"

Implementation notes for future developers:
    - Each fixture function returns ``dict[str, str|bytes|dict]`` mapping
      URL paths to content.  We serve the first path (the entry page) and
      serialize.
    - We reuse ``create_benchmark_browser_session`` and
      ``register_fixture_routes`` from ``conftest.py``.
    - The comparison report is Markdown for easy reading in editors/PRs.
"""

import asyncio
import logging
from collections.abc import Sequence
from datetime import datetime, timezone
from pathlib import Path

import yaml
from pytest_httpserver import HTTPServer

import benchmarks.fixtures as fixtures_module
from benchmarks.conftest import create_benchmark_browser_session, register_fixture_routes

logger = logging.getLogger('benchmarks.compare_dom')


async def _get_dom_representations(
	fixture_dict: dict,
	server: HTTPServer,
) -> tuple[str, str]:
	"""Navigate to the fixture's entry page and return (classic, outline) DOM text.

	Returns:
		Tuple of (classic_text, outline_text) — the serialized DOM as the LLM
		would receive it in each mode.
	"""
	session = await create_benchmark_browser_session()
	try:
		entry_path = list(fixture_dict.keys())[0]
		url = f'http://{server.host}:{server.port}{entry_path}'

		await session.navigate_to(url)
		await asyncio.sleep(1.5)  # let DOM watchdog process

		state = await session.get_browser_state_summary()
		dom = state.dom_state

		classic = dom.llm_representation(outline_mode=False)
		outline = dom.llm_representation(outline_mode=True)

		return classic, outline
	finally:
		await session.kill()
		await session.event_bus.stop(clear=True, timeout=5)


def _structural_notes(text: str, mode: str) -> list[str]:
	"""Extract structural features from serialized DOM text for quick comparison."""
	notes: list[str] = []
	if 'compound_components' in text:
		count = text.count('compound_components')
		notes.append(f'{count} compound_components attribute(s)')
	else:
		notes.append('NO compound_components found')

	if '=== PAGE OUTLINE ===' in text:
		notes.append('Has outline markers')
	if '(ungrouped)' in text.lower():
		notes.append('Has (ungrouped) section')

	# Count landmarks
	for landmark in ['BANNER', 'NAVIGATION', 'MAIN', 'COMPLEMENTARY', 'CONTENTINFO']:
		if f'{landmark}:' in text or f'{landmark} ' in text:
			notes.append(f'Landmark: {landmark}')

	# Count interactive elements
	import re
	interactive = re.findall(r'\[\d+\]', text)
	notes.append(f'{len(interactive)} interactive element(s)')

	return notes


def _load_tasks(task_paths: Sequence[str | Path] | None) -> list[dict]:
	"""Load YAML task definitions (sync, called before async work)."""
	if task_paths is None:
		tasks_dir = Path(__file__).parent / 'tasks'
		task_paths = sorted(tasks_dir.glob('*.yaml'))

	tasks = []
	for p in task_paths:
		tasks.append(yaml.safe_load(Path(p).read_text(encoding='utf-8')))
	return tasks


async def run_dom_comparison(
	task_paths: Sequence[str | Path] | None = None,
	output_dir: str | Path = 'benchmarks/reports',
) -> Path:
	"""Run DOM comparison for all (or selected) benchmark tasks.

	Args:
		task_paths: YAML task files to compare.  None = all tasks.
		output_dir: Where to write the comparison report.

	Returns:
		Path to the generated Markdown report.
	"""
	import socketserver

	socketserver.ThreadingMixIn.block_on_close = False
	socketserver.ThreadingMixIn.daemon_threads = True

	tasks = _load_tasks(task_paths)

	logger.info(f'Comparing DOM serialization for {len(tasks)} tasks')

	lines: list[str] = []
	timestamp = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')
	lines.append('# DOM Serialization Comparison — Classic vs Outline')
	lines.append(f'Generated: {timestamp}')
	lines.append('')
	lines.append('This report shows the exact serialized DOM text that the LLM receives')
	lines.append('in classic mode vs outline mode for each benchmark fixture page.')
	lines.append('Use it to diagnose information loss or format differences between modes.')
	lines.append('')

	for task in tasks:
		task_name = task['name']
		fixture_name = task['fixture']
		logger.info(f'  Comparing: {task_name}')

		fixture_dict = getattr(fixtures_module, fixture_name)()

		server = HTTPServer()
		server.start()

		try:
			register_fixture_routes(server, fixture_dict)
			classic, outline = await _get_dom_representations(fixture_dict, server)

			lines.append('---')
			lines.append(f'## {task_name}')
			lines.append(f'Fixture: `{fixture_name}`')
			lines.append('')

			# Size comparison
			lines.append('| Metric | Classic | Outline | Delta |')
			lines.append('|--------|---------|---------|-------|')
			c_len = len(classic)
			o_len = len(outline)
			delta_pct = ((o_len - c_len) / c_len * 100) if c_len > 0 else 0
			lines.append(f'| Characters | {c_len:,} | {o_len:,} | {delta_pct:+.1f}% |')
			lines.append('')

			# Structural notes
			lines.append('### Structural Features')
			lines.append('')
			lines.append('**Classic:**')
			for note in _structural_notes(classic, 'classic'):
				lines.append(f'- {note}')
			lines.append('')
			lines.append('**Outline:**')
			for note in _structural_notes(outline, 'outline'):
				lines.append(f'- {note}')
			lines.append('')

			# Full text
			lines.append('<details><summary>Classic DOM (click to expand)</summary>')
			lines.append('')
			lines.append('```')
			lines.append(classic)
			lines.append('```')
			lines.append('</details>')
			lines.append('')
			lines.append('<details><summary>Outline DOM (click to expand)</summary>')
			lines.append('')
			lines.append('```')
			lines.append(outline)
			lines.append('```')
			lines.append('</details>')
			lines.append('')

		finally:
			server.clear()
			if server.is_running():
				server.stop()

	output_dir = Path(output_dir)
	output_dir.mkdir(parents=True, exist_ok=True)
	ts = datetime.now(timezone.utc).strftime('%Y-%m-%d_%H%M%S')
	report_path = output_dir / f'dom_comparison_{ts}.md'
	report_path.write_text('\n'.join(lines), encoding='utf-8')

	logger.info(f'DOM comparison written to {report_path}')
	return report_path
