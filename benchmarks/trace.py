"""Per-step trace logging for benchmark debugging.

When ``--trace`` is passed to the benchmark CLI, each trial writes a JSONL file
containing one JSON object per agent step.  This gives full visibility into what
the LLM saw (serialized DOM), what it decided (thinking / actions), and what
happened (action results / errors).

Usage (CLI):
    python -m benchmarks --model openai/gpt-oss-20b --trace

The trace files land next to the report::

    benchmarks/reports/
      report_2026-02-18_120000.md          <- summary report
      report_2026-02-18_120000.json        <- full JSON results
      traces/                              <- only created when --trace is on
        Dropdown_Interaction_trial1.jsonl
        Fill_Contact_Form_trial1.jsonl
        ...

Reading a trace (Python)::

    import json
    with open("traces/Dropdown_Interaction_trial1.jsonl") as f:
        for line in f:
            step = json.loads(line)
            print(f"Step {step['step']}: {step['url']}")
            print(f"  Actions: {step['action_names']}")
            print(f"  Errors:  {step['errors']}")
            # step['dom_text'] has the full serialized DOM sent to the LLM

Each JSONL line has this schema::

    {
        "step":          int,       # 1-based step number
        "url":           str,       # page URL at this step
        "title":         str,       # page title
        "dom_text":      str,       # full serialized DOM / browser state text sent to LLM
        "model_output": {           # LLM's structured response (null if LLM failed)
            "thinking":                 str | null,
            "evaluation_previous_goal": str | null,
            "memory":                   str | null,
            "next_goal":                str | null,
            "action_names":             list[str],   # e.g. ["click", "input_text"]
        },
        "action_names":  list[str], # flattened action names for quick scanning
        "errors":        list[str], # error messages from action results (empty = success)
        "tokens": {                 # token counts for this step (null if unavailable)
            "prompt":     int,
            "completion": int,
            "total":      int,
        }
    }
"""

import json
from pathlib import Path

from browser_use.agent.views import AgentHistoryList


def write_trace(
	history: AgentHistoryList,
	trace_dir: Path,
	task_name: str,
	trial_num: int,
) -> Path:
	"""Write a per-step JSONL trace file for one trial.

	Args:
		history: The agent's full run history.
		trace_dir: Directory to write into (created if missing).
		task_name: Human-readable task name (spaces replaced with underscores).
		trial_num: 1-based trial number.

	Returns:
		Path to the written JSONL file.
	"""
	trace_dir.mkdir(parents=True, exist_ok=True)
	safe_name = task_name.replace(' ', '_')
	path = trace_dir / f'{safe_name}_trial{trial_num}.jsonl'

	with open(path, 'w', encoding='utf-8') as f:
		for i, h in enumerate(history.history):
			# --- Model output summary ---
			model_out = None
			action_names: list[str] = []
			if h.model_output:
				raw_actions = list(h.model_output.action) if h.model_output.action else []
				action_names = []
				for a in raw_actions:
					keys = list(a.model_dump(exclude_none=True).keys())
					action_names.extend(keys)

				model_out = {
					'thinking': h.model_output.thinking,
					'evaluation_previous_goal': h.model_output.evaluation_previous_goal,
					'memory': h.model_output.memory,
					'next_goal': h.model_output.next_goal,
					'action_names': action_names,
				}

			# --- Errors ---
			errors = [r.error for r in h.result if r.error]

			# --- Token info ---
			tokens = None
			if h.metadata:
				# StepMetadata doesn't carry per-step tokens directly; we leave
				# this null for now — the aggregate is in metrics.  Future: wire
				# per-step token counts from the LLM response metadata.
				pass

			entry = {
				'step': i + 1,
				'url': h.state.url if h.state else None,
				'title': h.state.title if h.state else None,
				'dom_text': h.state_message,  # the full browser state text sent to LLM
				'model_output': model_out,
				'action_names': action_names,
				'errors': errors,
				'tokens': tokens,
			}
			f.write(json.dumps(entry, default=str) + '\n')

	return path
