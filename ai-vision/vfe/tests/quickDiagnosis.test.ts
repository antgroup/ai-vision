import assert from "node:assert/strict"
import test from "node:test"
import { nativeUiHarness, uiText, uiElements } from "./nativeUiHarness.ts"
import type {
  DiagnosisDefinition,
  DiagnosisRecordDTO,
  TaskDTO,
  TaskStats,
  TaskMetricCounters,
  DiagnosisRunInput,
  DiagnosisRunSelection,
} from "../src/components/diagnose/types.ts"
const definition: DiagnosisDefinition = {
  uuid: "judge",
  key: "task_completion",
  name: "Frozen result",
  category: "outcome",
  score_role: "quality",
  weight: 1,
  version: "1",
  revision: "1.3.0",
  digest: "a",
  metrics: [{ name: "result", desc: "Frozen metric" }],
}
const record: DiagnosisRecordDTO = {
  uuid: "r",
  task_uuid: "t",
  job_uuid: "j",
  diagnoser_uuid: "judge",
  diagnoser_version: "1",
  target_type: "trace",
  target_id: "trace-1",
  owner_id: null,
  exp_ver_id: "",
  verdict: "bad",
  summary: "No test was run",
  metrics: [
    {
      name: "result",
      verdict: "bad",
      reason: "A success claim has no execution evidence",
      evidence: { bad_spans: [{ span_id: "span-1" }] },
    },
  ],
  execution: { status: "succeeded", code: "", msg: "", evidence: {} },
  trace_snapshot: {},
  review_status: "pending",
  feedback: null,
  reviewed_by: null,
  reviewed_at: null,
  diagnosed_at: null,
  target_created_at: null,
  definition_source: "task_snapshot",
  definition,
  can_feedback: true,
}
test("record labels remain frozen after catalog rename", () => {
  const adapters = nativeUiHarness({
    "./resourceScopes": { isUserSharedSpace: () => false },
  }).load<typeof import("../src/components/diagnose/diagnoseAdapters.ts")>(
    "components/diagnose/diagnoseAdapters.ts"
  )
  const catalog = [
    {
      uuid: "judge",
      name: "Renamed now",
      spec: { metrics: [{ name: "result", desc: "Changed now" }] },
    },
  ]
  assert.equal(
    adapters.toRecord(record, catalog).diagnoserName,
    "Frozen result"
  )
  assert.equal(
    adapters.toDetail(record, catalog).evidence.metricLabels?.result,
    "Frozen metric"
  )
  assert.equal(
    adapters.toRecord(
      {
        ...record,
        definition: null,
        definition_source: "legacy_current_definition",
      },
      catalog
    ).diagnoserName,
    "judge"
  )
})
test("retained Record list and detail use frozen names after Task retention", () => {
  const adapters = nativeUiHarness({
    "./resourceScopes": { isUserSharedSpace: () => false },
  }).load<typeof import("../src/components/diagnose/diagnoseAdapters.ts")>(
    "components/diagnose/diagnoseAdapters.ts"
  )
  const retained = { ...record, definition_source: "record_snapshot" as const }
  assert.equal(adapters.toRecord(retained, []).diagnoserName, "Frozen result")
  assert.equal(
    adapters.toDetail(retained, []).evidence.metricLabels?.result,
    "Frozen metric"
  )
})
const model = () =>
  nativeUiHarness().load<
    typeof import("../src/components/diagnose/quickDiagnosis.ts")
  >("components/diagnose/quickDiagnosis.ts")
test("diagnosis input rejects legacy-only requests and any extra configuration", () => {
  const { canonicalQuickInput } = model()
  for (const input of [
    { trace_ids: ["a"], builtin_keys: ["rule"] },
    { trace_ids: ["a"], before_task_uuid: "previous-task" },
    { trace_ids: ["a"] },
    { trace_ids: ["a"], diagnoser_uuid: "saved-rule", prompt: "override" },
    { trace_ids: ["a"], diagnoser_uuid: "saved-rule", scene_key: undefined },
  ])
    assert.throws(
      () => canonicalQuickInput(input as DiagnosisRunSelection),
      /QUICK_INPUT_INVALID/
    )
})
const task = (role: "quality" | "signal", counters: TaskMetricCounters) =>
  ({
    uuid: "t",
    status: "succeeded",
    definition_source: "task_snapshot",
    diagnoser_definitions: { judge: { ...definition, score_role: role } },
    stats: {
      schema_version: 3,
      source: { total: 2 },
      source_complete: true,
      diagnosers: { judge: { result: counters } },
    },
  }) as unknown as TaskDTO & {
    stats: TaskStats
    diagnoser_definitions: Record<string, DiagnosisDefinition>
  }
test("outcome separates nine states and never treats BAD signals as BAD quality", () => {
  const { quickOutcome } = model()
  assert.equal(quickOutcome(null), "not_run")
  assert.equal(
    quickOutcome({
      ...task("quality", { total: 0, good: 0, bad: 0, failed: 0 }),
      stats: { source: { total: 0 }, diagnosers: {} },
    }),
    "no_sample"
  )
  for (const [role, count, expected] of [
    ["quality", { total: 2, good: 2, bad: 0, failed: 0, skipped: 0 }, "good"],
    ["quality", { total: 2, good: 0, bad: 2, failed: 0, skipped: 0 }, "bad"],
    ["signal", { total: 2, good: 0, bad: 2, failed: 0, skipped: 0 }, "signals"],
    [
      "quality",
      { total: 0, good: 0, bad: 0, failed: 0, skipped: 2 },
      "skipped",
    ],
    ["quality", { total: 2, good: 0, bad: 0, failed: 2, skipped: 0 }, "failed"],
    ["quality", { total: 1, good: 1, bad: 0, failed: 0, skipped: 1 }, "mixed"],
  ])
    assert.equal(
      quickOutcome(
        task(role as "quality" | "signal", count as TaskMetricCounters)
      ),
      expected
    )
  const mixed = task("quality", {
    total: 2,
    good: 2,
    bad: 0,
    failed: 0,
    skipped: 0,
  })
  assert.equal(quickOutcome(mixed, true), "filter_empty")
  mixed.diagnoser_definitions.signal = { ...definition, score_role: "signal" }
  mixed.stats.diagnosers.signal = {
    result: { total: 2, good: 0, bad: 2, failed: 0, skipped: 0 },
  }
  assert.equal(quickOutcome(mixed), "good")
  assert.equal(
    quickOutcome({
      ...mixed,
      stats: { ...mixed.stats, source_complete: undefined },
    }),
    "unknown"
  )
  mixed.stats.diagnosers.judge.result = {
    total: 2,
    good: 0,
    bad: 2,
    failed: 0,
    skipped: 0,
  }
  mixed.stats.diagnosers.signal.result = {
    total: 2,
    good: 2,
    bad: 0,
    failed: 0,
    skipped: 0,
  }
  assert.equal(quickOutcome(mixed), "bad")
  assert.equal(
    quickOutcome(
      task("future" as "signal", { total: 2, good: 2, bad: 0, failed: 0 })
    ),
    "unknown"
  )
  assert.equal(
    quickOutcome({ ...mixed, definition_source: "unsupported_snapshot" }),
    "unknown"
  )
})
test("frozen failures remain failures when trace projection is incomplete", () => {
  const { quickOutcome } = model()
  const failed = task("signal", {
    total: 1,
    good: 0,
    bad: 0,
    failed: 1,
    skipped: 0,
  })
  failed.stats.source_complete = false
  failed.stats.source = { ...failed.stats.source, projection_failed: 1 }
  assert.equal(quickOutcome(failed), "failed")
  failed.diagnoser_definitions.judge.metrics = [
    { name: "result" },
    { name: "other" },
  ]
  failed.stats.diagnosers.judge.other = { total: 1, good: 0, bad: 0, failed: 1 }
  assert.equal(quickOutcome(failed), "failed")
  const observed = task("signal", {
    total: 1,
    good: 1,
    bad: 0,
    failed: 0,
    skipped: 0,
  })
  observed.stats.source_complete = false
  assert.equal(quickOutcome(observed), "mixed")
  for (const invalid of [
    { ...failed, definition_source: "legacy_current_definition" },
    { ...failed, diagnoser_definitions: {} },
    {
      ...failed,
      stats: {
        ...failed.stats,
        diagnosers: {
          judge: { result: { total: 2, good: 0, bad: 0, failed: 1 } },
        },
      },
    },
  ])
    assert.notEqual(quickOutcome(invalid as TaskDTO), "failed")
})

test("intent locks double clicks, retains exact body after response loss and ignores stale scope", async () => {
  const { QuickRunIntent } = model()
  let id = 0,
    calls = 0
  const intent = new QuickRunIntent("user:space:agent", () => `id-${++id}`)
  const input = { trace_ids: ["b", "a"], diagnoser_uuid: "saved-rule" }
  let resolve: (value: unknown) => void = () => {}
  const transport = () => {
    calls++
    return new Promise((r) => {
      resolve = r
    })
  }
  const a = intent.submit(input, transport),
    b = intent.submit(input, transport)
  assert.equal(calls, 1)
  resolve({ state: "provisioning", request_id: "id-1", retry_after_seconds: 2 })
  await Promise.all([a, b])
  const frozen = JSON.stringify(intent.body)
  await assert.rejects(
    intent.submit(input, () => Promise.reject(new Error("lost")))
  )
  assert.equal(JSON.stringify(intent.body), frozen)
  await intent.submit(
    { ...input, trace_ids: ["a", "b"] },
    async (body: DiagnosisRunInput) => ({
      state: "accepted",
      request_id: body.request_id,
      dataset_uuid: "d",
      job_uuid: "j",
      task_uuid: "t",
    })
  )
  assert.equal(id, 1)
  const late = intent.submit({ ...input, trace_ids: ["c"] }, transport)
  intent.invalidate()
  resolve({
    state: "accepted",
    request_id: "id-2",
    dataset_uuid: "d",
    job_uuid: "j",
    task_uuid: "old",
  })
  assert.equal(await late, null)
})
test("HTTP diagnosis submits a valid UUID using secure random bytes when randomUUID is absent", async () => {
  const original = Object.getOwnPropertyDescriptor(globalThis, "crypto")
  Object.defineProperty(globalThis, "crypto", {
    configurable: true,
    value: {
      getRandomValues: (bytes: Uint8Array) => {
        bytes.forEach((_, index) => (bytes[index] = index))
        return bytes
      },
    },
  })
  try {
    const { QuickRunIntent } = model()
    const intent = new QuickRunIntent("user:space:agent")
    let submitted: DiagnosisRunInput | null = null
    await intent.submit(
      { trace_ids: ["trace-a"], diagnoser_uuid: "saved-rule" },
      async (body) => {
        submitted = body
        return {
          state: "accepted",
          request_id: body.request_id,
          dataset_uuid: "d",
          job_uuid: "j",
          task_uuid: "t",
        }
      }
    )
    assert.equal(
      (submitted as DiagnosisRunInput | null)?.request_id,
      "00010203-0405-4607-8809-0a0b0c0d0e0f"
    )
  } finally {
    if (original) Object.defineProperty(globalThis, "crypto", original)
    else Reflect.deleteProperty(globalThis, "crypto")
  }
})

test("request ID generation failure does not block submitting the same selection again", async () => {
  const { QuickRunIntent } = model()
  let attempts = 0,
    calls = 0
  const intent = new QuickRunIntent("user:space:agent", () => {
    if (++attempts === 1) throw new Error("random source unavailable")
    return "31f9d053-e2a6-4fd6-a233-74c239f60cd6"
  })
  const input = { trace_ids: ["trace-a"], diagnoser_uuid: "saved-rule" }
  const transport = async (body: DiagnosisRunInput) => {
    calls++
    return {
      state: "accepted" as const,
      request_id: body.request_id,
      dataset_uuid: "d",
      job_uuid: "j",
      task_uuid: "t",
    }
  }
  assert.throws(
    () => intent.submit(input, transport),
    /random source unavailable/
  )
  assert.equal(calls, 0)
  assert.equal(intent.body, null)
  const result = await intent.submit(input, transport)
  assert.equal(result?.state, "accepted")
  assert.equal(calls, 1)
})

test("missing secure randomness fails before transport with a local request ID error", () => {
  const original = Object.getOwnPropertyDescriptor(globalThis, "crypto")
  Object.defineProperty(globalThis, "crypto", {
    configurable: true,
    value: {},
  })
  try {
    const { QuickRunIntent } = model()
    const intent = new QuickRunIntent("user:space:agent")
    let calls = 0
    assert.throws(
      () =>
        intent.submit(
          { trace_ids: ["trace-a"], diagnoser_uuid: "saved-rule" },
          async () => {
            calls++
            throw new Error("must not send")
          }
        ),
      /DIAGNOSIS_RUN_REQUEST_ID_UNAVAILABLE/
    )
    assert.equal(calls, 0)
    assert.equal(intent.body, null)
  } finally {
    if (original) Object.defineProperty(globalThis, "crypto", original)
    else Reflect.deleteProperty(globalThis, "crypto")
  }
})
test("saved diagnosis selection rejects empty, duplicate, malformed or too many IDs", () => {
  const { canonicalQuickInput } = model()
  for (const trace_ids of [
    [],
    ["a", "a"],
    [""],
    ["a b"],
    Array.from({ length: 11 }, (_, n) => `t${n}`),
  ])
    assert.throws(() =>
      canonicalQuickInput({ trace_ids, diagnoser_uuid: "saved-rule" })
    )
})

test("saved diagnosis input keeps one UUID and rejects legacy configuration overrides", () => {
  const { canonicalQuickInput } = model()
  assert.deepEqual(
    canonicalQuickInput({
      trace_ids: ["b", "a"],
      diagnoser_uuid: "saved-rule",
    }),
    { trace_ids: ["a", "b"], diagnoser_uuid: "saved-rule" }
  )
  for (const overrides of [
    { builtin_keys: ["tool_success"] },
    { scene_key: "quick_runtime" },
    { model_connection_uuid: "another-model" },
    { before_task_uuid: "previous-task" },
  ])
    assert.throws(() =>
      canonicalQuickInput({
        trace_ids: ["a"],
        diagnoser_uuid: "saved-rule",
        ...overrides,
      })
    )
  for (const uuid of ["", " ", "saved rule"])
    assert.throws(() =>
      canonicalQuickInput({ trace_ids: ["a"], diagnoser_uuid: uuid })
    )
})
test("actual budget view keeps unknown totals and partial coverage, with errors a subset", () => {
  const { JudgeRequestSummaryView } = nativeUiHarness().load<
    typeof import("../src/components/diagnose/JudgeRequestSummaryView.tsx")
  >("components/diagnose/JudgeRequestSummaryView.tsx")
  const summary = {
    reserved_slots: 3,
    response_confirmed_attempts: 1,
    unknown_attempts: 2,
    request_error_attempts: 1,
    total_cap: 30,
    per_case_cap: 3,
    input_tokens: null,
    output_tokens: 0,
    total_tokens: null,
    usage_known_attempts: {
      input_tokens: 1,
      output_tokens: 3,
      total_tokens: 0,
    },
    known_token_totals: { input_tokens: 12, output_tokens: 0, total_tokens: 0 },
  }
  const tree = JudgeRequestSummaryView({ summary, state: "available" })
  assert.match(uiText(tree), /diagnose.quick.budget.unknown/)
  assert.match(uiText(tree), /12/)
  assert.match(uiText(tree), /1\/3/)
  assert.match(
    uiText(JudgeRequestSummaryView({ summary: null, state: "unavailable" })),
    /unavailable/
  )
  assert.equal(
    uiElements(tree, "dd").filter(
      (e) => e.props["data-budget-field"] === "unknown_attempts"
    )[0].props.children,
    2
  )
})
test("actual result exposes SIGNAL boundary, frozen evidence source and immutable reviewed feedback", () => {
  const { TraceDiagnosisResult } = nativeUiHarness({
    "./quickDiagnosis": model(),
    "./DiagnosisScopeSummary": {
      DiagnosisScopeSummary: "DiagnosisScopeSummary",
    },
    "./diagnosisScope": { scopeFromTask: () => ({}) },
    "./JudgeRequestSummaryView": {
      JudgeRequestSummaryView: "JudgeRequestSummaryView",
    },
  }).load<typeof import("../src/components/diagnose/TraceDiagnosisResult.tsx")>(
    "components/diagnose/TraceDiagnosisResult.tsx"
  )
  const tree = TraceDiagnosisResult({
    task: {
      ...task("signal", { total: 2, good: 0, bad: 2, failed: 0, skipped: 0 }),
      quick_run: {
        request_id: "old-request",
        agent_uuid: "agent",
        trace_ids: ["trace-1"],
        can_feedback: true,
        can_retest: true,
        retest_denial_reason: null,
        comparison: {
          before_task_uuid: "old-task",
          before_trace_ids: ["old-trace"],
          kind: "new_traces",
          overlap_count: 0,
          new_count: 1,
          before_count: 1,
          checking_conditions: "same",
          sampling_conditions: "changed_selection",
        },
      },
    },
    records: [
      {
        ...record,
        definition: { ...definition, score_role: "signal" },
        review_status: "confirmed",
      },
    ],
    total: 1,
    onOpenRecord: () => {},
  })
  assert.match(uiText(tree), /diagnose.quick.states.signals/)
  assert.match(uiText(tree), /Frozen result/)
  assert.match(uiText(tree), /A success claim/)
  assert.doesNotMatch(uiText(tree), /diagnose.quick.(comparison|retest)/)
  assert.equal(
    uiElements(tree, "button").some((e) => e.props["data-feedback-action"]),
    false
  )
  assert.equal(
    uiElements(tree, "button").find(
      (e) => e.props["data-source-span"] === "span-1"
    ) !== undefined,
    true
  )
})

test("frozen score consumers hide SIGNAL and unsupported values without inventing scores", () => {
  const { globalTrend, diagnoserTrend, diagnoserForTask } =
    nativeUiHarness().load<
      typeof import("../src/components/diagnose/badcase/overview/diagnosisStats.ts")
    >("components/diagnose/badcase/overview/diagnosisStats.ts")
  const quality = {
    uuid: "q",
    name: "Quality",
    score_role: "quality" as const,
    score: null,
    metrics: [],
    weight: 1,
  }
  const signal = {
    ...quality,
    uuid: "s",
    score_role: "signal" as const,
    score: 100,
  }
  const frozen = {
    task_uuid: "t",
    time: "2026-01-01",
    target_type: "trace" as const,
    target_count: 1,
    definition_source: "task_snapshot" as const,
    global: {
      score: null,
      categories: [
        {
          id: "process",
          name: "Checks",
          score: null,
          diagnosers: [quality, signal],
        },
      ],
    },
  }
  assert.equal(globalTrend([frozen])[0].score, null)
  assert.equal(diagnoserTrend([frozen], "s")[0].score, null)
  assert.equal(diagnoserForTask(frozen, "s")?.score, null)
  assert.equal(diagnoserForTask(frozen, "q")?.score, null)
  const good = {
    ...frozen,
    global: {
      score: 77,
      categories: [
        {
          ...frozen.global.categories[0],
          diagnosers: [{ ...quality, score: 77 }, signal],
        },
      ],
    },
  }
  assert.equal(globalTrend([good])[0].score, 77)
  assert.equal(diagnoserTrend([good], "q")[0].score, 77)
  assert.equal(
    globalTrend([{ ...good, definition_source: "unsupported_snapshot" }])[0]
      .score,
    null
  )
  assert.equal(
    diagnoserForTask(
      { ...good, definition_source: "legacy_current_definition" },
      "q"
    )?.score,
    null
  )
})

test("409 and late failures keep original request identity and cannot overwrite a new intent", async () => {
  const { QuickRunIntent } = model()
  let count = 0
  const intent = new QuickRunIntent("owner:space:agent", () => `id-${++count}`)
  const selection = { trace_ids: ["a"], diagnoser_uuid: "saved-rule" }
  for (let n = 0; n < 2; n++)
    await assert.rejects(
      intent.submit(selection, () =>
        Promise.reject(new Error("QUICK_REQUEST_CONFLICT"))
      )
    )
  assert.equal(intent.body?.request_id, "id-1")
  let reject: (reason: unknown) => void = () => {}
  const old = intent.submit(
    selection,
    () =>
      new Promise((_, fail) => {
        reject = fail
      })
  )
  intent.invalidate()
  reject(new Error("old transport error"))
  assert.equal(await old, null)
  await intent.submit({ ...selection, trace_ids: ["b"] }, async (body) => ({
    state: "accepted",
    request_id: body.request_id,
    dataset_uuid: "d",
    job_uuid: "j",
    task_uuid: "t",
  }))
  assert.equal(intent.body?.request_id, "id-2")
})
