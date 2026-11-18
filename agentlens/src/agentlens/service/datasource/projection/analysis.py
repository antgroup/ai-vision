"""Pure queries over shared Projection facts."""

from __future__ import annotations

from agentlens.service.datasource.projection.models import ExecutionStep, Projection


def collapse_seq(seq: tuple[str, ...] | list[str]) -> tuple[str, ...]:
    """Collapse consecutive duplicate node names."""
    collapsed: list[str] = []
    for node in seq:
        if not collapsed or collapsed[-1] != node:
            collapsed.append(node)
    return tuple(collapsed)


def _is_subsequence(reference: tuple[str, ...], sequence: tuple[str, ...]) -> bool:
    iterator = iter(sequence)
    return all(node in iterator for node in reference)


def matches(
    trace: Projection,
    reference: list[str] | tuple[str, ...],
    mode: str = "superset",
) -> bool:
    """Return whether the observed path matches the requested baseline mode."""
    normalized_reference = tuple(reference)
    if mode == "strict":
        return _is_subsequence(normalized_reference, collapse_seq(trace.node_seq))
    return set(normalized_reference) <= set(trace.node_seq)


def steps_count(trace: Projection) -> int:
    """Return the collapsed path length."""
    return len(collapse_seq(trace.node_seq))


def loop_count(trace: Projection) -> int:
    """Return the longest consecutive run with identical node I/O."""
    if not trace.steps:
        return 0
    max_run = current_run = 1
    for previous, current in zip(trace.steps, trace.steps[1:], strict=False):
        if previous.name != current.name:
            current_run = 1
            continue
        if not isinstance(previous, ExecutionStep) or not isinstance(current, ExecutionStep):
            current_run = 1
            continue
        if previous.input is None and previous.output is None:
            current_run = 1
            continue
        if previous.input == current.input and previous.output == current.output:
            current_run += 1
            max_run = max(max_run, current_run)
        else:
            current_run = 1
    return max_run
