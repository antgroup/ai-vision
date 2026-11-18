import type { NativeTraceDetailResponse } from "../../src/components/traces-otel/types"
import { compactionEvent, compactionSpan } from "../fixtures/contextCompaction"

/** Protocol-shaped UI fixture only; never presented as a captured Hermes run. */
export const compactionTrace: NativeTraceDetailResponse = {
  traceId: "compaction-trace",
  meta: { complete: true, expectedSpanCount: 1 },
  spans: [
    compactionSpan([
      compactionEvent(),
      compactionEvent(
        {
          outcome: "failed",
          attempt_id: "attempt-failed",
          tokens_after: null,
          tokens_reclaimed: null,
          failure_class: "summarize_failed",
          split_status: "aborted",
          in_place: null,
          session_rotated: null,
          cache_break: false,
        },
        "compaction.attempt",
        "mark-failed"
      ),
      compactionEvent(
        {
          outcome: "skipped",
          attempt_id: "attempt-skipped",
          method: "none",
          tokens_before: 0,
          tokens_after: null,
          tokens_reclaimed: null,
          duration_ms: 0,
          messages_before: 0,
          messages_after: null,
          items_dropped: null,
          failure_class: "empty_transcript",
          split_status: "not_applicable",
          cache_break: false,
        },
        "compaction.attempt",
        "mark-skipped"
      ),
      compactionEvent(), // A repeated projection must not inflate the attempts.
    ]),
  ],
  semantics: {
    rootObservationId: "turn-span",
    rootSelectionReason: "trace_root:explicit",
    missingParentIds: [],
    input: { task: "Review context compaction evidence" },
    output: "Recorded context compaction attempts",
    outputState: "available",
  },
}
