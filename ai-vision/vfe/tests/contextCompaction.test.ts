import assert from "node:assert/strict"
import test from "node:test"
import { collectContextCompactions } from "../src/labs/context-compaction/contextCompaction.ts"
import {
  compactionEvent,
  compactionSpan,
} from "./fixtures/contextCompaction.ts"

test("Relay projected compaction retains exact measurements and event provenance", () => {
  const [fact] = collectContextCompactions([compactionSpan()])
  assert.equal(fact.outcome, "committed")
  assert.equal(fact.data.tokens_before, 12000)
  assert.equal(fact.data.token_count_method, "estimate_rough")
  assert.deepEqual(fact.sources, [
    {
      traceId: "compaction-trace",
      spanId: "turn-span",
      eventIndex: 0,
      eventName: "compaction",
      markId: "mark-committed",
      time: "2026-11-02T02:00:01Z",
      categoryProfile: undefined,
    },
  ])
  assert.equal(Object.hasOwn(fact, "schemaVersion"), false)
})

test("reported false, zero and null remain distinct without deriving missing token savings", () => {
  const [fact] = collectContextCompactions([
    compactionSpan([
      compactionEvent({
        tokens_before: 0,
        tokens_after: null,
        tokens_reclaimed: null,
        queue_wait_ms: 0,
        in_place: false,
        session_rotated: false,
      }),
    ]),
  ])
  assert.equal(fact.data.tokens_before, 0)
  assert.equal(fact.data.tokens_after, null)
  assert.equal(fact.data.tokens_reclaimed, null)
  assert.equal(fact.data.queue_wait_ms, 0)
  assert.equal(fact.data.in_place, false)
  assert.equal(fact.data.session_rotated, false)
})

test("duplicate attempts count once while preserving every distinct source event", () => {
  const event = compactionEvent()
  const facts = collectContextCompactions([
    compactionSpan([event, { name: "ordinary", attributes: {} }, event]),
    compactionSpan([event], "session-span"),
  ])
  assert.equal(facts.length, 1)
  assert.deepEqual(
    facts[0].sources.map((s) => [s.spanId, s.eventIndex]),
    [
      ["turn-span", 0],
      ["turn-span", 2],
      ["session-span", 0],
    ]
  )
})

test("failed, skipped, aborted and blocked attempts never become committed rewrites", () => {
  const facts = collectContextCompactions([
    compactionSpan(
      ["failed", "skipped", "aborted", "blocked"].map((outcome, index) =>
        compactionEvent(
          { outcome, attempt_id: `attempt-${index}`, tokens_after: null },
          "compaction.attempt",
          `mark-${index}`
        )
      )
    ),
  ])
  assert.deepEqual(
    facts.map((f) => f.outcome),
    ["failed", "skipped", "aborted", "blocked"]
  )
})

test("conflicting projections cannot turn an attempt into an unqualified success", () => {
  const facts = collectContextCompactions([
    compactionSpan([
      compactionEvent(),
      compactionEvent(
        { outcome: "failed", tokens_after: 9000 },
        "compaction.attempt",
        "mark-failed"
      ),
    ]),
  ])
  assert.equal(facts.length, 1)
  assert.equal(facts[0].outcome, "conflict")
  assert.equal(facts[0].data.tokens_after, null)
  assert.equal(facts[0].sources.length, 2)
})

test("attempt identity is scoped to trace and session; absent IDs use the Relay mark", () => {
  const facts = collectContextCompactions([
    compactionSpan([
      compactionEvent(),
      compactionEvent({ session_id: "session-2" }, "compaction", "mark-2"),
      compactionEvent({ attempt_id: null }, "compaction", "mark-3"),
      compactionEvent({ attempt_id: null }, "compaction", "mark-4"),
    ]),
  ])
  assert.equal(facts.length, 4)
})

test("contradictory measurements on committed projections disclose a conflict", () => {
  const [fact] = collectContextCompactions([
    compactionSpan([
      compactionEvent(),
      compactionEvent(
        { tokens_after: 9000 },
        "compaction",
        "different-measurement"
      ),
    ]),
  ])
  assert.equal(fact.outcome, "conflict")
  assert.equal(fact.data.tokens_after, null)
  assert.equal(fact.sources.length, 2)
})

test("ordinary names, raw log payloads, missing mark identity and non-structured data stay out", () => {
  const valid = compactionEvent()
  const missingIdentity = {
    ...valid,
    attributes: { ...valid.attributes, "nemo_relay.mark.uuid": null },
  }
  const malformed = compactionEvent({ kind: null, outcome: null })
  const facts = collectContextCompactions([
    compactionSpan([
      { name: "compaction" },
      {
        name: "compaction.attempt",
        attributes: { body: JSON.stringify(valid) },
      },
      { ...valid, name: "some compaction" },
      missingIdentity,
      malformed,
    ]),
  ])
  assert.equal(facts.length, 0)
})

test("unknown outcomes remain unknown and category profiles are optional provenance", () => {
  const event = compactionEvent({ outcome: null })
  event.attributes!["nemo_relay.mark.category_profile"] =
    '{"operation":"compact"}'
  const [fact] = collectContextCompactions([compactionSpan([event])])
  assert.equal(fact.outcome, "unknown")
  assert.equal(fact.sources[0].categoryProfile, '{"operation":"compact"}')
})

test("fact ordering follows reported event time before limiting records, retaining source indexes", () => {
  const later = compactionEvent(
    { attempt_id: "later" },
    "compaction",
    "later-mark"
  )
  later.time_unix_nano = "2026-11-02T02:00:03Z"
  const earlier = compactionEvent(
    { attempt_id: "earlier" },
    "compaction",
    "earlier-mark"
  )
  const facts = collectContextCompactions([compactionSpan([later, earlier])])
  assert.deepEqual(
    facts.map((f) => f.data.attempt_id),
    ["earlier", "later"]
  )
  assert.deepEqual(
    facts.map((f) => f.sources[0].eventIndex),
    [1, 0]
  )
})
