"""Shared execution ABI identity, independent of Git/documentation commits."""

# Bump when executor/projector/selector behavior changes; retain a compatible implementation
# or drain pending Tasks before removing a revision. Unknown revisions never fall forward.
EXECUTOR_REVISION = "1.6.0"
COPIED_RULE_EXECUTOR_REVISION = "1.7.0"
SUPPORTED_EXECUTOR_REVISIONS = frozenset(
    {"1.4.0", "1.5.0", EXECUTOR_REVISION, COPIED_RULE_EXECUTOR_REVISION}
)
