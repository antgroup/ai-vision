import type {
  OTelEvent,
  OTelSpan,
} from "../../src/components/traces-otel/types.ts"

/** Synthetic Relay OTLP projection of Hermes v0.22.5 compaction v1 fields.
 * Not a captured run: Relay does not export data_schema in span events. */
export function compactionEvent(
  data: Record<string, unknown> = {},
  name = "compaction",
  markId = "mark-committed"
): OTelEvent {
  const payload: Record<string, unknown> = {
    harness: "hermes",
    kind: "summarize",
    scope: "history",
    official: true,
    method: "llm_summary",
    trigger: "manual",
    trigger_class: "manual",
    overflow_reason: null,
    outcome: "committed",
    failure_class: "none",
    attempt_id: "attempt-1",
    session_id: "session-1",
    tokens_before: 12000,
    tokens_after: 4000,
    tokens_reclaimed: 8000,
    token_count_method: "estimate_rough",
    messages_before: 40,
    messages_after: 12,
    items_dropped: 28,
    context_limit: 32000,
    threshold_tokens: 24000,
    protected_head_tokens: null,
    middle_window_tokens: null,
    protected_tail_tokens: null,
    duration_ms: 850,
    summary_generation_ms: 700,
    aux_call_duration_ms: null,
    queue_wait_ms: 0,
    commit_ms: 3,
    summarizer_provider: "example-provider",
    summarizer_model: "example-model",
    tool_results_pruned: 0,
    reasoning_items_pruned: null,
    summary_input_omitted_chars: null,
    compression_count: 1,
    in_place: true,
    session_rotated: false,
    split_status: "in_place_committed",
    cache_break: true,
    has_focus_topic: false,
    ...data,
  }
  return {
    name,
    time_unix_nano: "2026-11-02T02:00:01Z",
    attributes: {
      "nemo_relay.mark.uuid": markId,
      "nemo_relay.mark.parent_uuid": "turn-scope",
      ...Object.fromEntries(
        Object.entries(payload).map(([key, value]) => [
          `nemo_relay.mark.data.${key}`,
          value,
        ])
      ),
    },
  }
}

export function compactionSpan(
  events: OTelEvent[] = [compactionEvent()],
  spanId = "turn-span"
): OTelSpan {
  return {
    trace_id: "compaction-trace",
    span_id: spanId,
    name: "Hermes turn",
    start_time_unix_nano: "2026-11-02T02:00:00Z",
    end_time_unix_nano: "2026-11-02T02:00:05Z",
    attributes: { "gen_ai.operation.name": "invoke_agent" },
    events,
  }
}
