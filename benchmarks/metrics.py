"""Metric extraction from AgentHistoryList and aggregation across trials."""

from collections import Counter
from statistics import mean, stdev

from pydantic import BaseModel, ConfigDict, Field

from browser_use.agent.views import AgentHistoryList


class TaskRunMetrics(BaseModel):
	"""Metrics extracted from a single agent run."""

	model_config = ConfigDict(extra='forbid')

	steps: int
	total_tokens: int
	prompt_tokens: int
	completion_tokens: int
	cached_tokens: int
	total_cost: float
	duration_seconds: float
	success: bool | None
	is_done: bool
	error_count: int
	action_names: list[str]
	action_distribution: dict[str, int]
	urls_visited: list[str | None]
	final_result: str | None


class TaskAggregateMetrics(BaseModel):
	"""Aggregate metrics computed over N trials of the same task."""

	model_config = ConfigDict(extra='forbid')

	n_trials: int
	pass_rate: float
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

	Uses only existing public APIs on AgentHistoryList — zero new instrumentation.
	"""
	usage = history.usage
	action_names = history.action_names()

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
		error_count=len([e for e in history.errors() if e is not None]),
		action_names=action_names,
		action_distribution=dict(Counter(action_names)),
		urls_visited=history.urls(),
		final_result=history.final_result(),
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

	# Error rate per trial: errors / steps (avoid division by zero)
	error_rates = [t.error_count / t.steps if t.steps > 0 else 0.0 for t in trials]

	# Merge action distributions across all trials
	merged_actions: Counter[str] = Counter()
	for t in trials:
		merged_actions.update(t.action_distribution)

	return TaskAggregateMetrics(
		n_trials=n,
		pass_rate=successes / n,
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
