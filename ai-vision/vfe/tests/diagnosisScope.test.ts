import assert from "node:assert/strict"
import test from "node:test"
import { createInstance } from "i18next"
import { readFileSync } from "node:fs"
import { nativeUiHarness, uiElements, uiText } from "./nativeUiHarness.ts"
import {
  scopeFromTask,
  scopeFromQuality,
  skipReasonLabelKey,
} from "../src/components/diagnose/diagnosisScope.ts"
import type {
  TaskStats,
  DiagnosisStatsTask,
} from "../src/components/diagnose/types.ts"

const definition = {
  uuid: "judge",
  key: "test",
  name: "Frozen check",
  category: "outcome",
  version: "1",
  revision: "1.0.0",
  digest: "abc",
  score_role: "quality" as const,
  weight: 2,
  metrics: [{ name: "result", desc: "Frozen metric" }],
}

test("missing-input reasons accept complete semantic suffixes without matching similar names", () => {
  for (const reason of [
    "query_missing_or_blank",
    "query_input_missing_or_blank",
    "legacy_query_input_missing_or_blank",
    "vendor_agent_query_input_missing_or_blank",
  ]) {
    assert.equal(
      skipReasonLabelKey(reason),
      "diagnose.scope.reasonLabels.query_missing_or_blank"
    )
  }
  for (const reason of [
    "legacy_query_input_missing_or_blank_extra",
    "legacyquery_input_missing_or_blank",
    "missing_required_evidence",
    "future_reason",
  ]) {
    assert.equal(
      skipReasonLabelKey(reason),
      `diagnose.scope.reasonLabels.${reason}`
    )
  }
})
function stats(
  counters = { total: 3, good: 1, bad: 1, failed: 1, skipped: 1 }
): TaskStats {
  return {
    source: { total: 4 },
    schema_version: 3,
    diagnoser_definitions: { judge: structuredClone(definition) },
    diagnosers: { judge: { result: counters } },
  }
}
test("coverage uses total plus skipped and keeps execution failures separate", () => {
  const scope = scopeFromTask(stats())
  assert.equal(scope.effective, 2)
  assert.equal(scope.checks, 4)
  assert.equal(scope.failed, 1)
  assert.equal(scope.skipped, 1)
  assert.equal(scope.state, "evaluated")
})
test("source total is selected Trace count, never an invented scanned count", () => {
  assert.equal(scopeFromTask(stats()).selected, 4)
  assert.equal(scopeFromTask(stats()).scanned, null)
  assert.equal(
    scopeFromTask({ ...stats(), selector: { scanned_targets: 8 } }).scanned,
    8
  )
  assert.equal(scopeFromTask(null).effective, null)
  assert.equal(scopeFromTask(null).selected, null)
  assert.equal(scopeFromTask(null).state, "unknown")
})
test("all skipped or failed is not evaluated success; explicit zero source is no sample", () => {
  for (const counters of [
    { total: 0, good: 0, bad: 0, failed: 0, skipped: 4 },
    { total: 4, good: 0, bad: 0, failed: 4, skipped: 0 },
  ])
    assert.equal(scopeFromTask(stats(counters)).state, "no_evaluation")
  assert.equal(
    scopeFromTask({ source: { total: 0 }, diagnosers: {} }).state,
    "no_sample"
  )
})
test("multiple metrics count checks and retain distinct Trace count", () => {
  const input = stats()
  input.diagnosers.judge.extra = {
    total: 4,
    good: 4,
    bad: 0,
    failed: 0,
    skipped: 0,
  }
  const scope = scopeFromTask(input)
  assert.equal(scope.selected, 4)
  assert.equal(scope.checks, 8)
  assert.equal(scope.effective, 6)
})
test("legacy unknown skipped coverage is not replaced by a false zero or fabricated definition", () => {
  const scope = scopeFromTask({
    source: { total: 3 },
    diagnosers: { judge: { result: { total: 3, good: 2, bad: 0, failed: 1 } } },
  })
  assert.equal(scope.effective, 2)
  assert.equal(scope.checks, null)
  assert.equal(scope.skipped, null)
  assert.equal(scope.definitionSource, "legacy_current_definition")
  assert.deepEqual(scope.definitions, [])
})
test("frozen labels survive catalog edits and scope models never mutate the response", () => {
  const input = stats()
  const original = structuredClone(input)
  const currentCatalog = {
    judge: { ...definition, name: "Renamed now", weight: 9 },
  }
  const scope = scopeFromTask(input)
  currentCatalog.judge.name = "Deleted now"
  assert.equal(scope.definitions[0].name, "Frozen check")
  assert.equal(scope.definitions[0].weight, 2)
  assert.deepEqual(input, original)
})
test("quality and task coverage agree while null service scores remain untouched", () => {
  const input: DiagnosisStatsTask = {
    task_uuid: "task",
    time: "2026-10-09",
    target_type: "trace",
    target_count: 4,
    schema_version: 3,
    definition_source: "task_snapshot",
    score_policy_version: "lightweight-v1",
    global: {
      score: null,
      categories: [
        {
          id: "outcome",
          name: "Outcome",
          score: null,
          diagnosers: [
            {
              ...definition,
              score: null,
              metrics: [
                {
                  id: "result",
                  name: "Frozen metric",
                  score: null,
                  total: 3,
                  good: 1,
                  bad: 1,
                  failed: 1,
                  skipped: 1,
                },
              ],
            },
          ],
        },
      ],
    },
  }
  const scope = scopeFromQuality(input)
  assert.equal(scope.effective, scopeFromTask(stats()).effective)
  assert.equal(scope.checks, 4)
  assert.equal(scope.scanned, null)
  assert.equal(scope.definitions[0].name, "Frozen check")
  assert.equal(input.global.score, null)
})

test("failed execution renders partial stats instead of hiding them behind its message", () => {
  const harness = nativeUiHarness({
    "../DiagnosisScopeSummary": { DiagnosisScopeSummary: "scope-summary" },
    "../diagnosisScope": { scopeFromTask },
  })
  const { TaskOutcomeContent, TaskStatsContent } = harness.load<{
    TaskOutcomeContent: (props: Record<string, unknown>) => unknown
    TaskStatsContent: (props: Record<string, unknown>) => unknown
  }>("components/diagnose/job/TaskOutcomeContent.tsx")
  const tree = TaskOutcomeContent({
    status: "failed",
    message: "retrieval failed",
    stats: stats(),
  })
  assert.match(uiText(tree), /retrieval failed/)
  const detail = uiElements(tree, "TaskStatsContent")[0]
  assert.ok(detail)
  const rendered = TaskStatsContent(detail.props)
  const scope = uiElements(rendered, "scope-summary")[0].props.scope
  assert.equal((scope as { effective: number }).effective, 2)
})

test("summary displays localized counts and frozen safe fields without exposing execution specs", () => {
  for (const lang of ["zh-CN", "en-US"]) {
    const i18n = createInstance()
    i18n.init({
      lng: lang,
      initAsync: false,
      resources: {
        [lang]: {
          translation: JSON.parse(
            readFileSync(
              new URL(`../src/i18n/locales/${lang}.json`, import.meta.url),
              "utf8"
            )
          ),
        },
      },
    })
    const harness = nativeUiHarness({
      "react-i18next": { useTranslation: () => ({ t: i18n.t.bind(i18n) }) },
    })
    const { DiagnosisScopeSummary } = harness.load<{
      DiagnosisScopeSummary: (props: Record<string, unknown>) => unknown
    }>("components/diagnose/DiagnosisScopeSummary.tsx")
    const input = stats()
    input.skip_reasons = { judge: { legacy_query_input_missing_or_blank: 1 } }
    const originalReasons = structuredClone(input.skip_reasons)
    Object.assign(input.diagnoser_definitions!.judge, {
      spec: { prompt: "SECRET PROMPT", api_key: "SECRET KEY" },
    })
    const tree = DiagnosisScopeSummary({
      scope: scopeFromTask(input),
      scoreUnavailable: true,
    })
    const text = uiText(tree)
    assert.match(text, /2 \/ 4/)
    assert.match(text, /Frozen check/)
    assert.match(text, /1.0.0/)
    assert.match(text, lang === "zh-CN" ? /未记录/ : /not recorded/i)
    assert.match(text, lang === "zh-CN" ? /暂无汇总分/ : /No aggregate score/)
    assert.match(
      text,
      lang === "zh-CN" ? /缺少用户输入/ : /User input unavailable/
    )
    assert.deepEqual(input.skip_reasons, originalReasons)
    assert.doesNotMatch(text, /SECRET|judge|diagnoser_definitions/)
    assert.equal(uiElements(tree, "table").length, 1)
  }
})

test("quality without selector preserves explicit incomplete source rather than assuming completeness", () => {
  const task: DiagnosisStatsTask & { source_complete: boolean } = {
    task_uuid: "partial",
    time: "2026-10-09",
    target_type: "trace",
    target_count: 3,
    schema_version: 3,
    definition_source: "task_snapshot",
    source_complete: false,
    global: { score: null, categories: [] },
  }
  assert.equal(scopeFromQuality(task).sourceIncomplete, true)
  assert.equal(scopeFromQuality(task).scanned, null)
  assert.equal(
    scopeFromQuality({ ...task, source_complete: true }).sourceIncomplete,
    false
  )
})

test("unsupported execution snapshot retains its own source and never displays current-definition fallback", () => {
  const scope = scopeFromTask(null, {
    definition_source: "unsupported_snapshot",
    diagnoser_definitions: {},
  })
  assert.equal(scope.definitionSource, "unsupported_snapshot")
  assert.deepEqual(scope.definitions, [])
  const input = stats()
  Object.assign(input.diagnoser_definitions!.judge, {
    spec: { prompt: "SECRET PROMPT", api_key: "SECRET KEY" },
  })
  const unsupported = scopeFromTask(input, {
    definition_source: "unsupported_snapshot",
  })
  assert.deepEqual(unsupported.definitions, [])
  assert.equal(unsupported.effective, 2)
  for (const lang of ["zh-CN", "en-US"]) {
    const i18n = createInstance()
    i18n.init({
      lng: lang,
      initAsync: false,
      resources: {
        [lang]: {
          translation: JSON.parse(
            readFileSync(
              new URL(`../src/i18n/locales/${lang}.json`, import.meta.url),
              "utf8"
            )
          ),
        },
      },
    })
    const harness = nativeUiHarness({
      "react-i18next": { useTranslation: () => ({ t: i18n.t.bind(i18n) }) },
    })
    const { DiagnosisScopeSummary } = harness.load<{
      DiagnosisScopeSummary: (props: Record<string, unknown>) => unknown
    }>("components/diagnose/DiagnosisScopeSummary.tsx")
    const text = uiText(DiagnosisScopeSummary({ scope }))
    assert.match(
      text,
      lang === "zh-CN"
        ? /当前服务不支持该执行定义/
        : /This service does not support this execution definition/
    )
    assert.doesNotMatch(
      text,
      /历史定义不完整|来自当前定义|Historical definition incomplete|comes from current definitions/
    )
    assert.doesNotMatch(text, /spec|Prompt|api_key/)
    const compactText = uiText(
      DiagnosisScopeSummary({ scope: unsupported, compact: true })
    )
    assert.match(
      compactText,
      lang === "zh-CN"
        ? /当前服务不支持该执行定义/
        : /unsupported by this service/
    )
    assert.doesNotMatch(compactText, /SECRET|spec|Prompt|api_key|Frozen check/)
    assert.equal(
      uiElements(DiagnosisScopeSummary({ scope }), "table").length,
      0
    )
  }
})

test("history tooltip retains the same unsupported source as Task details and preserves failure messages", () => {
  const adapterHarness = nativeUiHarness({ "./resourceScopes": {} })
  const { toTaskRecord } = adapterHarness.load<{
    toTaskRecord: (dto: Record<string, unknown>) => {
      msg: string
      stats: TaskStats
      definitionMetadata?: Record<string, unknown>
    }
  }>("components/diagnose/diagnoseAdapters.ts")
  const metadata = {
    definition_source: "unsupported_snapshot",
    diagnoser_definitions: {},
  }
  const dto = {
    uuid: "unsupported",
    status: "failed",
    msg: "Unsupported execution revision",
    stats: stats(),
    ...metadata,
  }
  const record = toTaskRecord(dto)
  assert.deepEqual(record.definitionMetadata, metadata)
  const harness = nativeUiHarness({
    "../DiagnosisScopeSummary": { DiagnosisScopeSummary: "scope-summary" },
    "../diagnosisScope": { scopeFromTask },
  })
  const { TaskOutcomeContent, TaskStatsContent } = harness.load<{
    TaskOutcomeContent: (props: Record<string, unknown>) => unknown
    TaskStatsContent: (props: Record<string, unknown>) => unknown
  }>("components/diagnose/job/TaskOutcomeContent.tsx")
  const tree = TaskOutcomeContent({
    status: dto.status,
    message: record.msg,
    stats: record.stats,
    metadata: record.definitionMetadata,
  })
  assert.match(uiText(tree), /Unsupported execution revision/)
  const tooltip = uiElements(
    TaskStatsContent(uiElements(tree, "TaskStatsContent")[0].props),
    "scope-summary"
  )[0].props.scope
  const details = uiElements(
    TaskStatsContent({ stats: dto.stats, metadata }),
    "scope-summary"
  )[0].props.scope
  assert.deepEqual(tooltip, details)
  assert.deepEqual((tooltip as { definitions: unknown[] }).definitions, [])
})
