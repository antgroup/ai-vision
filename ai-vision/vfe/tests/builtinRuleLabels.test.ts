import assert from "node:assert/strict"
import test from "node:test"
import { readFileSync } from "node:fs"
import { createInstance } from "i18next"
import { nativeUiHarness } from "./nativeUiHarness.ts"
import { isUserSharedSpace } from "../src/components/diagnose/resourceScopes.ts"
import { scopeFromTask } from "../src/components/diagnose/diagnosisScope.ts"
import type {
  DiagnoserDTO,
  TaskDTO,
  TaskRecord,
} from "../src/components/diagnose/types.ts"
import type * as Adapters from "../src/components/diagnose/diagnoseAdapters.ts"

const { toDiagnoser, toDiagnoserDetailSpec, toTaskRecord } = nativeUiHarness({
  "./resourceScopes": { isUserSharedSpace },
}).load<typeof Adapters>("components/diagnose/diagnoseAdapters.ts")
const { diagnosisRunLabels } = nativeUiHarness().load<
  typeof import("../src/components/diagnose/diagnosisRunLabels.ts")
>("components/diagnose/diagnosisRunLabels.ts")
const { displayAgentName } = nativeUiHarness().load<
  typeof import("../src/components/diagnose/builtinDiagnoserPolicy.ts")
>("components/diagnose/builtinDiagnoserPolicy.ts")

test("diagnosis uses the public fallback for legacy Agent labels", () => {
  assert.equal(displayAgentName("ANT_CLAUDE_CODE"), "Claude Code")
  assert.equal(displayAgentName(undefined, "AntCC"), "Claude Code")
  assert.equal(
    displayAgentName("ANT_CLAUDE_CODE", "Ant Claude Code", "Team Agent"),
    "Team Agent"
  )
})

function translator(language: string, translations?: Record<string, unknown>) {
  const i18n = createInstance()
  i18n.init({
    lng: language,
    initAsync: false,
    resources: {
      [language]: {
        translation:
          translations ??
          JSON.parse(
            readFileSync(
              new URL(`../src/i18n/locales/${language}.json`, import.meta.url),
              "utf8"
            )
          ),
      },
    },
  })
  return i18n.t.bind(i18n)
}

function rule(executor: string, version = "3.0.0"): DiagnoserDTO {
  return {
    uuid: `rule-${executor}`,
    space_uuid: "space",
    agent_uuid: "agent",
    name: "Server rule name",
    description: "Server rule description",
    type: "builtin",
    mode: "rule",
    spec: { executor, metrics: [{ name: "result" }] },
    version,
    gmt_create: "2026-10-09",
    gmt_modified: "2026-10-09",
    creator: null,
    modifier: null,
  }
}

test("the four canonical Core presets share translated labels on the catalog and the saved Trace chooser", () => {
  const definitions = [
    ["task_completion", "text_response", "Text Response Check"],
    ["task_completion", "executed_artifact", "Actual Delivery Check"],
    ["report_fidelity", "observed_fact_comparison", "Execution Claim Check"],
    ["answer_grounding", "provided_context", "Evidence Support Check"],
  ]
  const t = translator("en-US")
  for (const [builtin_key, canonical_variant, expectedName] of definitions) {
    const dto = {
      ...rule("general_llm", "1.0.0"),
      mode: "llm" as const,
      spec: { executor: "general_llm", builtin_key, canonical_variant },
    }
    const option = {
      ...dto,
      builtin_key,
      canonical_variant,
      is_specialist: false,
      category: "outcome",
      selectable: false,
      unavailable_reason: "QUICK_MODEL_UNAVAILABLE",
      executor: null,
    }
    const card = toDiagnoser(dto, [], t)
    assert.equal(card.name, expectedName)
    assert.notEqual(card.description, dto.description)
    assert.deepEqual(diagnosisRunLabels(option, t), {
      name: card.name,
      description: card.description,
    })
    assert.equal(toDiagnoser({ ...dto, type: "custom" }, [], t).name, dto.name)
    assert.equal(
      diagnosisRunLabels({ ...option, version: "2.0.0" }, t).name,
      dto.name
    )
    assert.equal(
      diagnosisRunLabels({ ...option, is_specialist: true }, t).name,
      dto.name
    )
  }
})

const names = {
  "zh-CN": [
    "工具执行结果",
    "重复调用线索",
    "凭据暴露线索",
    "风险操作提醒",
    "运行耗时",
    "用户中断事件",
    "修改后验证",
    "反复编辑线索",
  ],
  "en-US": [
    "Tool Execution Results",
    "Repeated Call Clues",
    "Credential Exposure Clues",
    "Risky Operation Notice",
    "Run Duration",
    "User Interruption Events",
    "Verification After Changes",
    "Repeated Edit Clues",
  ],
}
const legacyNames = {
  "zh-CN": [
    "工具成功率",
    "死循环检测",
    "敏感信息泄漏检测",
    "高危命令执行检测",
    "总延迟",
    "会话完成率",
    "测试执行率",
    "重复改写率",
  ],
  "en-US": [
    "Tool Success Rate",
    "Loop Detection",
    "Sensitive Data Leakage Detection",
    "High-Risk Command Detection",
    "Total Latency",
    "Session Completion Rate",
    "Test Execution Rate",
    "Rewrite Overlap Rate",
  ],
}
const executors = [
  "tool_success",
  "stuck_loop",
  "sensitive_leakage",
  "high_risk_command",
  "total_latency",
  "user_interruption",
  "test_execution",
  "rewrite_overlap",
]

const metricNames: Record<string, string> = {
  tool_success: "tool_success_rate",
  stuck_loop: "stuck_loop_health",
  sensitive_leakage: "secret_leakage_block_rate",
  high_risk_command: "high_risk_command_execution",
  total_latency: "latency_decomposition",
  user_interruption: "user_interruption_rate",
  test_execution: "test_execution_rate",
  rewrite_overlap: "rewrite_overlap_rate",
}

// Published v2 definitions from 211a6df^, including the reported three-step rule.
const legacyDetails: Record<
  string,
  { logic: string[]; scoringText: string; metricDescription: string }
> = {
  rewrite_overlap: {
    logic: [
      "只比较具有文件路径、旧内容和新内容的编辑操作",
      "同文件内按时序检查后续旧内容是否包含前序新内容",
      "重叠次数达到 3 次时不通过",
    ],
    scoringText:
      "- good：重叠改写少于 3 次\n- bad：重叠改写达到或超过 3 次\n- 豁免：缺少可比较编辑证据",
    metricDescription: "重复改写率",
  },
  total_latency: {
    logic: [
      "从 outcome.total_latency_ms 读取总耗时并转换为秒",
      "总耗时不超过 25 分钟时通过",
      "数据源未提供总耗时时豁免",
    ],
    scoringText:
      "- good：总耗时不超过 25 分钟\n- bad：总耗时超过 25 分钟\n- 豁免：数据源未提供总耗时",
    metricDescription: "总延迟",
  },
}

const legacyLogicLengths: Record<string, number> = {
  tool_success: 3,
  stuck_loop: 4,
  sensitive_leakage: 6,
  high_risk_command: 3,
  total_latency: 3,
  user_interruption: 2,
  test_execution: 3,
  rewrite_overlap: 3,
}

function detailRule(executor: string, version = "3.0.0") {
  const legacy = version === "v2" ? legacyDetails[executor] : undefined
  return {
    ...rule(executor, version),
    spec: {
      executor,
      scenario: "custom scenario",
      code: "return observed_value",
      policy: { tool_success_min_rate: 0.8, latency_budget_ms: 120_000 },
      detail: {
        logic: legacy?.logic ?? ["Server logic"],
        scoring: {
          text: legacy?.scoringText ?? "Server scoring",
          threshold: 0.8,
        },
        code: "return observed_value",
      },
      metrics: [
        {
          name: metricNames[executor] ?? "future_metric",
          desc: legacy?.metricDescription ?? "Server metric description",
          weight: 2,
        },
        { name: "custom_metric", desc: "Custom metric description", weight: 3 },
      ],
    },
  }
}

for (const language of ["zh-CN", "en-US", "ja-JP"] as const) {
  for (const version of ["v2", "3.0.0"]) {
    test(`${language}: ${version} detail localizes all eight rules without changing execution fields or the DTO`, () => {
      const t = translator(language)
      for (const executor of executors) {
        const input = detailRule(executor, version)
        const before = structuredClone(input)
        const prefix = `diagnose.diagnoser.builtinDetails${version === "v2" ? "" : "V3"}.${executor}`
        const logic = t(`${prefix}.logic`, { returnObjects: true })
        const scoringText = t(`${prefix}.scoringText`)
        const metricDescription = t(
          `${prefix}.metrics.${metricNames[executor]}`
        )
        assert.ok(Array.isArray(logic) && logic.length > 0, executor)
        assert.ok(
          logic.every((line) => typeof line === "string" && line.length > 0)
        )
        assert.ok(
          scoringText && scoringText !== `${prefix}.scoringText`,
          executor
        )
        assert.ok(
          metricDescription &&
            metricDescription !== `${prefix}.metrics.${metricNames[executor]}`,
          executor
        )
        assert.deepEqual(toDiagnoserDetailSpec(input, t), {
          ...input.spec,
          detail: {
            ...input.spec.detail,
            logic,
            scoring: { ...input.spec.detail.scoring, text: scoringText },
          },
          metrics: [
            { ...input.spec.metrics[0], desc: metricDescription },
            input.spec.metrics[1],
          ],
        })
        if (version === "v2") {
          assert.equal(logic.length, legacyLogicLengths[executor], executor)
          assert.equal(
            new Set(logic).size,
            logic.length,
            "each legacy step remains distinct"
          )
          const currentPrefix = `diagnose.diagnoser.builtinDetailsV3.${executor}`
          assert.notDeepEqual(
            logic,
            t(`${currentPrefix}.logic`, { returnObjects: true })
          )
          assert.notEqual(
            metricDescription,
            t(`${currentPrefix}.metrics.${metricNames[executor]}`)
          )
          if (executor === "total_latency") {
            assert.match(logic.join("\n"), /25/)
            assert.match(scoringText, /25/)
          }
          if (executor === "tool_success") assert.match(scoringText, /75%/)
          if (language === "zh-CN" && legacyDetails[executor]) {
            assert.deepEqual(logic, legacyDetails[executor].logic)
            assert.equal(scoringText, legacyDetails[executor].scoringText)
            assert.equal(
              metricDescription,
              legacyDetails[executor].metricDescription
            )
          }
        }
        assert.deepEqual(input, before, `${executor}: source DTO is unchanged`)
      }
    })
  }

  test(`${language}: detail keeps server definitions outside the supported builtin rule catalogs`, () => {
    const t = translator(language)
    for (const input of [
      ...["2.0.0", "3.0.1", "4.0.0", "3", "v3", "3.0.0-preview", ""].map(
        (version) => detailRule("tool_success", version)
      ),
      ...["v2", "3.0.0"].flatMap((version) => [
        detailRule("future_rule", version),
        { ...detailRule("tool_success", version), type: "custom" as const },
        { ...detailRule("tool_success", version), mode: "llm" as const },
      ]),
    ]) {
      const before = structuredClone(input)
      assert.deepEqual(toDiagnoserDetailSpec(input, t), input.spec)
      assert.deepEqual(input, before)
    }
  })
}

test("detail translation does not invent missing or malformed display fields", () => {
  const t = translator("ja-JP")
  const untouchedSpecs: Record<string, unknown>[] = [
    { executor: "tool_success" },
    { executor: "tool_success", detail: {}, metrics: [] },
    {
      executor: "tool_success",
      detail: { scoring: {} },
      metrics: [{ name: "tool_success_rate" }],
    },
    {
      executor: "tool_success",
      detail: { logic: null, scoring: { text: null } },
      metrics: [{ name: "tool_success_rate", desc: null }, null],
    },
  ]
  for (const spec of untouchedSpecs) {
    const input = { ...rule("tool_success"), spec }
    const before = structuredClone(input)
    assert.deepEqual(toDiagnoserDetailSpec(input, t), spec)
    assert.deepEqual(input, before)
  }

  const partial = {
    ...rule("tool_success"),
    spec: {
      executor: "tool_success",
      detail: { scoring: { text: "Server scoring", threshold: 0.8 } },
      metrics: [{ name: "tool_success_rate" }],
    },
  }
  assert.deepEqual(toDiagnoserDetailSpec(partial, t), {
    ...partial.spec,
    detail: {
      scoring: {
        ...partial.spec.detail.scoring,
        text: t("diagnose.diagnoser.builtinDetailsV3.tool_success.scoringText"),
      },
    },
  })
})

test("missing detail translations fall back to each server field", () => {
  const input = detailRule("tool_success")
  const before = structuredClone(input)
  assert.deepEqual(
    toDiagnoserDetailSpec(input, translator("ja-JP", {})),
    input.spec
  )

  const translatedFields = [
    { logic: ["Localized logic"] },
    { scoringText: "Localized scoring" },
    { metrics: { tool_success_rate: "Localized metric" } },
  ]
  for (const fields of translatedFields) {
    const t = translator("ja-JP", {
      diagnose: { diagnoser: { builtinDetailsV3: { tool_success: fields } } },
    })
    assert.deepEqual(toDiagnoserDetailSpec(input, t), {
      ...input.spec,
      detail: {
        ...input.spec.detail,
        logic: fields.logic ?? input.spec.detail.logic,
        scoring: {
          ...input.spec.detail.scoring,
          text: fields.scoringText ?? input.spec.detail.scoring.text,
        },
      },
      metrics: [
        {
          ...input.spec.metrics[0],
          desc: fields.metrics?.tool_success_rate ?? input.spec.metrics[0].desc,
        },
        input.spec.metrics[1],
      ],
    })
  }
  assert.deepEqual(input, before)
})

for (const language of ["zh-CN", "en-US"] as const) {
  test(`${language}: current v3 rules explain facts and verification without old quality claims`, () => {
    const t = translator(language)
    const results = executors.map((executor) =>
      toDiagnoser(rule(executor), undefined, t)
    )
    assert.deepEqual(
      results.map((result) => result.name),
      names[language]
    )
    const descriptions = results.map((result) => result.description)
    assert.ok(descriptions.every((text) => text !== "Server rule description"))
    if (language === "zh-CN") {
      assert.match(descriptions[0], /成功、失败和未知/)
      assert.match(descriptions[0], /全部已知失败/)
      assert.doesNotMatch(descriptions[1], /死循环/)
      assert.match(descriptions[2], /输入、工具返回和可见输出/)
      assert.match(descriptions[2], /不.*归因/)
      assert.match(descriptions[3], /授权/)
      assert.doesNotMatch(descriptions[4], /25|质量阈值/)
      assert.match(descriptions[5], /不代表.*完成/)
      assert.match(descriptions[6], /未运行、失败、通过和未知/)
      assert.doesNotMatch(descriptions[7], /无进展|无用/)
    } else {
      assert.match(descriptions[0], /successful, failed, and unknown/)
      assert.match(descriptions[0], /every known failure/)
      assert.doesNotMatch(descriptions[1], /infinite loop/i)
      assert.match(descriptions[2], /inputs, tool results, and visible outputs/)
      assert.match(descriptions[2], /without.*blame/)
      assert.match(descriptions[3], /authorization/)
      assert.doesNotMatch(descriptions[4], /25|quality threshold/i)
      assert.match(descriptions[5], /does not establish.*completion/)
      assert.match(descriptions[6], /not run, failed, passed, and unknown/)
      assert.doesNotMatch(descriptions[7], /useless|no progress/i)
    }
  })

  test(`${language}: v2 retains the published legacy labels and descriptions`, () => {
    const t = translator(language)
    assert.deepEqual(
      executors.map(
        (executor) => toDiagnoser(rule(executor, "v2"), undefined, t).name
      ),
      legacyNames[language]
    )
    assert.equal(
      toDiagnoser(rule("total_latency", "v2"), undefined, t).description,
      language === "zh-CN"
        ? "检测 Agent 任务总耗时是否超过 25 分钟"
        : "Checks whether the total Agent task duration exceeds 25 minutes"
    )
    assert.equal(
      toDiagnoser(rule("user_interruption", "v2"), undefined, t).name,
      language === "zh-CN" ? "会话完成率" : "Session Completion Rate"
    )
  })

  test(`${language}: unknown versions, executors and custom or LLM definitions keep server copy`, () => {
    const t = translator(language)
    for (const input of [
      ...["4.0.0", "3", "v3", "3.0.0-preview", ""].map((version) =>
        rule("tool_success", version)
      ),
      rule("future_rule"),
      { ...rule("tool_success"), type: "custom" as const },
      { ...rule("tool_success"), mode: "llm" as const },
    ]) {
      const result = toDiagnoser(input, undefined, t)
      assert.equal(result.name, input.name)
      assert.equal(result.description, input.description)
    }
    const untranslated = rule("tool_success")
    assert.equal(toDiagnoser(untranslated).name, untranslated.name)
    assert.equal(
      toDiagnoser(untranslated).description,
      untranslated.description
    )
  })

  test(`${language}: current catalog translations leave frozen task metadata unchanged`, () => {
    const t = translator(language)
    const task: TaskDTO = {
      uuid: "task",
      job_uuid: "job",
      status: "succeeded",
      worker: null,
      scheduled_at: "2026-10-09",
      started_at: "2026-10-09",
      finished_at: "2026-10-09",
      attempt: 1,
      msg: "",
      gmt_create: "2026-10-09",
      gmt_modified: "2026-10-09",
      definition_source: "task_snapshot",
      diagnoser_definitions: {
        "rule-tool_success": {
          uuid: "rule-tool_success",
          key: "tool_success",
          name: "Frozen tool check",
          version: "v2",
          revision: "1.1.0",
          digest: "frozen-digest",
          score_role: "quality",
          weight: 2,
        },
      },
      stats: {
        schema_version: 3,
        score_policy_version: "frozen-policy",
        source: { total: 1 },
        diagnosers: {},
      },
    }
    const before = structuredClone(task)
    const catalog = rule("tool_success")
    const current = toDiagnoser(catalog, undefined, t)
    assert.notEqual(current.name, "Frozen tool check")
    const record: TaskRecord = toTaskRecord(task)
    catalog.name = "Edited current rule"
    catalog.version = "4.0.0"
    toDiagnoser(catalog, undefined, t)
    const scope = scopeFromTask(record.stats, record.definitionMetadata)
    assert.equal(scope.definitionSource, "task_snapshot")
    assert.equal(scope.definitions[0].name, "Frozen tool check")
    assert.equal(scope.definitions[0].version, "v2")
    assert.equal(scope.definitions[0].revision, "1.1.0")
    assert.equal(scope.definitions[0].score_role, "quality")
    assert.equal(scope.definitions[0].weight, 2)
    assert.equal(scope.scorePolicy, "frozen-policy")
    assert.deepEqual(task, before)
  })
}
