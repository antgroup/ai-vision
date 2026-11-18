import assert from "node:assert/strict"
import { readFileSync } from "node:fs"
import { createRequire } from "node:module"
import test from "node:test"
import ts from "typescript"
import { createInstance } from "i18next"
import { isValidatedTrial } from "../src/components/diagnose/diagnoser/trialResult.ts"
import { uiElements, uiText } from "./nativeUiHarness.ts"
import type { TrialResponse } from "../src/components/diagnose/types.ts"

const require = createRequire(import.meta.url)
function loadRenderer(file: string, name: string, t = (key: string) => key) {
  const source = readFileSync(new URL(file, import.meta.url), "utf8")
  const { outputText } = ts.transpileModule(`${source}\nexport { ${name} }`, {
    compilerOptions: {
      module: ts.ModuleKind.CommonJS,
      jsx: ts.JsxEmit.ReactJSX,
    },
  })
  const exports: Record<string, (props: { result: TrialResponse }) => unknown> =
    {}
  new Function("require", "exports", outputText)((dependency: string) => {
    if (dependency === "react-i18next") return { useTranslation: () => ({ t }) }
    if (dependency === "react" || dependency === "react/jsx-runtime")
      return require(dependency)
    if (dependency === "./TrialResultView")
      return loadRenderer(
        "../src/components/diagnose/diagnoser/TrialResultView.tsx",
        "TrialResultView",
        t
      )
    if (dependency === "./trialResult")
      return require("../src/components/diagnose/diagnoser/trialResult.ts")
    if (dependency === "../executionMessage")
      return require("../src/components/diagnose/executionMessage.ts")
    return {}
  }, exports)
  return exports
}
const baseline = {
  verdict: "good",
  summary: "",
  metrics: [],
  execution: { status: "succeeded", code: "", msg: "", evidence: {} },
} satisfies TrialResponse
const renderers = [
  [
    "Rule",
    "../src/components/diagnose/diagnoser/CreateRuleDiagnoserForm.tsx",
    "TrialResultView",
  ],
  [
    "LLM",
    "../src/components/diagnose/diagnoser/CreateLlmDiagnoserModal.tsx",
    "LlmTrialResultView",
  ],
] as const
for (const [mode, file, name] of renderers) {
  const render = (overrides: Record<string, unknown>) =>
    loadRenderer(file, name)[name]({
      result: { ...baseline, ...overrides } as TrialResponse,
    })
  test(`${mode} empty metrics never produce a green or validated result`, () => {
    const tree = render({})
    assert.match(uiText(tree), /trialNoMetrics/)
    assert.doesNotMatch(uiText(tree), /Good|validated/)
    assert.equal(
      uiElements(tree, "span").some((node) =>
        String(node.props.className).includes("text-primary")
      ),
      false
    )
  })
  test(`${mode} skipped results show each stable reason and do not render stale good metrics`, () => {
    for (const reason of [
      "not_applicable",
      "missing_required_evidence",
      "privacy_redacted",
      "budget_exhausted",
    ]) {
      const tree = render({
        skipped: true,
        skip_reason: reason,
        skip_reason_detail: "agent_output",
        verdict: null,
        metrics: [{ name: "stale", verdict: "good", reason: "", evidence: {} }],
      })
      assert.match(uiText(tree), /trialSkipped/)
      assert.match(uiText(tree), new RegExp(`trialSkipReasons.${reason}`))
      assert.match(uiText(tree), /agent_output/)
      assert.doesNotMatch(uiText(tree), /Good/)
    }
  })
  test(`${mode} unknown skip reasons stay skipped and preserve their code`, () => {
    const tree = render({
      skipped: true,
      skip_reason: "future_reason",
      verdict: null,
    })
    assert.match(uiText(tree), /trialSkipped/)
    assert.match(uiText(tree), /trialSkipUnknown/)
    assert.match(uiText(tree), /future_reason/)
    assert.doesNotMatch(uiText(tree), /Good|trialNoMetrics/)
  })
  test(`${mode} execution failures stay distinct from skip and empty results`, () => {
    const tree = render({
      skipped: true,
      execution: {
        ...baseline.execution,
        status: "failed",
        code: "projection_failed",
        msg: "Cannot project",
      },
    })
    assert.match(uiText(tree), /Cannot project/)
    assert.match(uiText(tree), /projection_failed/)
    assert.doesNotMatch(uiText(tree), /trialSkipped|trialNoMetrics|Good/)
  })
  test(`${mode} overall failed verdict overrides skipped, empty and stale good metrics`, () => {
    for (const overrides of [
      { skipped: true, skip_reason: "not_applicable", metrics: [] },
      { skipped: false, metrics: [] },
      {
        skipped: false,
        metrics: [{ name: "stale", verdict: "good", reason: "", evidence: {} }],
      },
    ]) {
      const result = {
        ...baseline,
        ...overrides,
        verdict: "failed",
      } as TrialResponse
      const tree = render(result)
      assert.match(uiText(tree), /trialFailed/)
      assert.doesNotMatch(uiText(tree), /trialSkipped|trialNoMetrics|Good/)
      assert.equal(isValidatedTrial(result), false)
    }
  })
  test(`${mode} null and unknown metric verdicts stay neutral rather than Good or Failed`, () => {
    for (const verdict of [null, undefined, "future_verdict"]) {
      const tree = render({
        verdict: null,
        metrics: [{ name: "result", verdict, reason: "", evidence: {} }],
      })
      assert.match(uiText(tree), /trialUnknown/)
      assert.doesNotMatch(uiText(tree), /Good|Failed/)
      assert.equal(
        uiElements(tree, "span").some((node) =>
          String(node.props.className).includes("text-primary")
        ),
        false
      )
    }
  })
  test(`${mode} legacy good, bad and failed metrics retain their labels and reasons`, () => {
    for (const [verdict, label] of [
      ["good", "Good"],
      ["bad", "badResult"],
      ["failed", "trialFailed"],
    ]) {
      const tree = render({
        metrics: [
          { name: "legacy", verdict, reason: "Fixture reason", evidence: {} },
        ],
      })
      assert.match(uiText(tree), new RegExp(label))
      assert.match(uiText(tree), /Fixture reason/)
    }
  })
}

test("only evaluated good/bad metrics validate a Trial; all skip, empty, failed and unknown stay unvalidated", () => {
  const good = { name: "result", verdict: "good", reason: "", evidence: {} }
  assert.equal(isValidatedTrial(null), false)
  assert.equal(isValidatedTrial(baseline), false)
  for (const overrides of [
    {
      skipped: true,
      skip_reason: "missing_required_evidence",
      verdict: null,
      metrics: [],
    },
    { skipped: true, metrics: [good] },
    { metrics: [{ ...good, verdict: null }] },
    { metrics: [{ ...good, verdict: "future_verdict" }] },
    { metrics: [{ ...good, verdict: "failed" }] },
    { verdict: "failed", metrics: [good] },
    { execution: { ...baseline.execution, status: "failed" }, metrics: [good] },
  ]) {
    assert.equal(
      isValidatedTrial({ ...baseline, ...overrides } as TrialResponse),
      false
    )
  }
  for (const verdict of ["good", "bad"]) {
    assert.equal(
      isValidatedTrial({
        ...baseline,
        verdict,
        metrics: [{ ...good, verdict }],
      } as TrialResponse),
      true
    )
  }
})

test("null, absent and future overall verdicts cannot validate known good metrics", () => {
  for (const verdict of [null, undefined, "future_verdict"]) {
    assert.equal(
      isValidatedTrial({
        ...baseline,
        skipped: false,
        verdict,
        metrics: [
          { name: "result", verdict: "good", reason: "", evidence: {} },
        ],
      } as TrialResponse),
      false
    )
  }
})

test("Trial skip, unknown and empty states have Chinese and English translations", async () => {
  for (const language of ["zh-CN", "en-US"]) {
    const instance = createInstance()
    const resources = JSON.parse(
      readFileSync(
        new URL(`../src/i18n/locales/${language}.json`, import.meta.url),
        "utf8"
      )
    )
    await instance.init({
      lng: language,
      resources: { [language]: { translation: resources } },
    })
    for (const result of [
      {
        ...baseline,
        skipped: true,
        skip_reason: "missing_required_evidence",
        verdict: null,
      },
      { ...baseline, skipped: true, skip_reason: null, verdict: null },
      baseline,
    ]) {
      const render = loadRenderer(
        "../src/components/diagnose/diagnoser/TrialResultView.tsx",
        "TrialResultView",
        (key) => instance.t(key)
      ).TrialResultView
      const text = uiText(render({ result } as { result: TrialResponse }))
      assert.doesNotMatch(text, /diagnose\.diagnoser\.form/)
      assert.doesNotMatch(text, /Good/)
      assert.match(
        text,
        language === "zh-CN" ? /跳过|无可判定/ : /Skipped|No evaluated/
      )
    }
  }
})

test("sandbox input failure shows measured bytes and action instead of a connection error", () => {
  const render = loadRenderer(
    "../src/components/diagnose/diagnoser/TrialResultView.tsx",
    "TrialResultView",
    (key: string, options?: Record<string, unknown>) =>
      key === "enhancedRule.inputLimitDetails"
        ? `输入过大：${options?.actual} 字节，上限 ${options?.allowed} 字节；请减少测试材料`
        : key
  ).TrialResultView
  const text = uiText(
    render({
      result: {
        ...baseline,
        verdict: "failed",
        execution: {
          status: "failed",
          code: "sandbox_input_limit",
          msg: "raw provider error",
          evidence: {
            sandbox_input: {
              stage: "request",
              actual_bytes: 2200000,
              allowed_bytes: 2097152,
            },
          },
        },
      },
    })
  )
  assert.match(text, /输入过大.*2200000.*2097152.*减少测试材料/)
  assert.doesNotMatch(text, /raw provider error/)
})
