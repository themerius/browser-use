"""Metric extraction from AgentHistoryList and aggregation across trials.

Each ``TaskRunMetrics`` captures a single trial: pass/fail, token counts,
action distribution, and — importantly — failure diagnostics (error messages,
the model's last reasoning output).  ``TaskAggregateMetrics`` rolls these up
across N trials of the same task.

Intended consumers:
    - ``benchmarks/runner.py``  — calls ``extract_metrics`` after each trial
    - ``benchmarks/report.py``  — reads aggregate + per-trial data for Markdown/JSON
    - ``benchmarks/trace.py``   — uses ``AgentHistoryList`` directly (not metrics)

When adding fields, keep ``extra='forbid'`` so any typo is caught immediately.
"""

from collections import Counter
from statistics import mean, stdev

from pydantic import BaseModel, ConfigDict, Field

from browser_use.agent.views import AgentHistoryList


class TaskRunMetrics(BaseModel):
	"""Metrics extracted from a single agent run.

	Fields added for failure diagnosis (see ``extract_metrics`` for how they
	are populated):
	- ``error_messages``: actual error strings from each step (not just a count)
	- ``last_model_reasoning``: the model's thinking/eval/memory/goal on the
	  final step — the most useful signal when a task fails
	- ``dom_text_chars``: character count of the serialized DOM text sent to
	  the LLM on the final step — proxy for DOM token cost
	"""

	model_config = ConfigDict(extra='forbid')

	steps: int
	total_tokens: int
	prompt_tokens: int
	completion_tokens: int
	cached_tokens: int
	total_cost: float
	duration_seconds: float
	success: bool | None
	score: float = 0.0  # Partial score 0.0–1.0 (fraction of criteria met)
	criteria_met: dict[str, bool] = Field(default_factory=dict)  # Per-criterion breakdown
	is_done: bool
	error_count: int
	action_names: list[str]
	action_distribution: dict[str, int]
	urls_visited: list[str | None]
	final_result: str | None

	# --- Failure diagnostics (new) ---
	error_messages: list[str] = Field(default_factory=list)
	last_model_reasoning: dict[str, str | None] | None = None
	dom_text_chars: int | None = None


class TaskAggregateMetrics(BaseModel):
	"""Aggregate metrics computed over N trials of the same task."""

	model_config = ConfigDict(extra='forbid')

	n_trials: int
	pass_rate: float
	avg_score: float = 0.0  # Average partial score across trials
	avg_steps: float
	std_steps: float
	avg_tokens: float
	std_tokens: float
	avg_cost: float
	avg_duration: float
	avg_error_rate: float
	action_distribution: dict[str, int]
	# Per-trial metrics preserved for detailed reporting
	trials: list[TaskRunMetrics] = Field(default_factory=list)


def extract_metrics(history: AgentHistoryList) -> TaskRunMetrics:
	"""Extract structured metrics from an AgentHistoryList.

	Populates both aggregate numbers (tokens, steps) and failure diagnostics
	(error messages, last model reasoning, DOM size).
	"""
	usage = history.usage
	action_names = history.action_names()

	# --- Failure diagnostics ---
	# Collect all non-None error strings across all steps
	error_messages = [e for e in history.errors() if e is not None]

	# Last step's model reasoning — the most useful signal on failure
	last_model_reasoning = None
	if history.history:
		last = history.history[-1]
		if last.model_output:
			last_model_reasoning = {
				'thinking': last.model_output.thinking,
				'evaluation_previous_goal': last.model_output.evaluation_previous_goal,
				'memory': last.model_output.memory,
				'next_goal': last.model_output.next_goal,
			}

	# DOM text size on the last step — proxy for token cost of the DOM
	dom_text_chars = None
	if history.history:
		last_msg = history.history[-1].state_message
		if last_msg:
			dom_text_chars = len(last_msg)

	return TaskRunMetrics(
		steps=history.number_of_steps(),
		total_tokens=usage.total_tokens if usage else 0,
		prompt_tokens=usage.total_prompt_tokens if usage else 0,
		completion_tokens=usage.total_completion_tokens if usage else 0,
		cached_tokens=usage.total_prompt_cached_tokens if usage else 0,
		total_cost=usage.total_cost if usage else 0.0,
		duration_seconds=history.total_duration_seconds(),
		success=history.is_successful(),
		is_done=history.is_done(),
		error_count=len(error_messages),
		action_names=action_names,
		action_distribution=dict(Counter(action_names)),
		urls_visited=history.urls(),
		final_result=history.final_result(),
		error_messages=error_messages,
		last_model_reasoning=last_model_reasoning,
		dom_text_chars=dom_text_chars,
	)


def aggregate_metrics(trials: list[TaskRunMetrics]) -> TaskAggregateMetrics:
	"""Compute aggregate statistics over multiple trial runs."""
	assert len(trials) > 0, 'Cannot aggregate zero trials'

	n = len(trials)
	successes = sum(1 for t in trials if t.success is True)
	steps_list = [t.steps for t in trials]
	tokens_list = [t.total_tokens for t in trials]
	cost_list = [t.total_cost for t in trials]
	duration_list = [t.duration_seconds for t in trials]
	score_list = [t.score for t in trials]

	# Error rate per trial: errors / steps (avoid division by zero)
	error_rates = [t.error_count / t.steps if t.steps > 0 else 0.0 for t in trials]

	# Merge action distributions across all trials
	merged_actions: Counter[str] = Counter()
	for t in trials:
		merged_actions.update(t.action_distribution)

	return TaskAggregateMetrics(
		n_trials=n,
		pass_rate=successes / n,
		avg_score=mean(score_list),
		avg_steps=mean(steps_list),
		std_steps=stdev(steps_list) if n > 1 else 0.0,
		avg_tokens=mean(tokens_list),
		std_tokens=stdev(tokens_list) if n > 1 else 0.0,
		avg_cost=mean(cost_list),
		avg_duration=mean(duration_list),
		avg_error_rate=mean(error_rates),
		action_distribution=dict(merged_actions),
		trials=trials,
	)
