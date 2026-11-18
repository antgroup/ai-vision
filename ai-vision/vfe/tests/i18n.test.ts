import assert from "node:assert/strict"
import { readFileSync } from "node:fs"
import { createRequire } from "node:module"
import { dirname, resolve } from "node:path"
import test from "node:test"
import { fileURLToPath } from "node:url"
import { runInNewContext } from "node:vm"
import { createInstance } from "i18next"
import ts from "typescript"
import { nativeUiHarness } from "./nativeUiHarness.ts"
import { resolveAgentDisplayName } from "../src/agents/lib/agent-display.ts"
import { isUserSharedSpace } from "../src/components/diagnose/resourceScopes.ts"
import type * as Adapters from "../src/components/diagnose/diagnoseAdapters.ts"
import type { DiagnoserDTO } from "../src/components/diagnose/types.ts"
import type { DiagnosisRunOption } from "../src/components/diagnose/types.ts"
import { preferredLocale } from "../src/i18n/locales.ts"

const require = createRequire(import.meta.url)
const sourceRoot = fileURLToPath(new URL("../src/", import.meta.url))

test("legacy Claude Code aliases use the public label and keep saved display names", () => {
  const seededType = {
    agent_name: "ANT_CLAUDE_CODE",
    agent_product: "Ant Claude Code",
    category: "BUILT_IN",
    description: "",
    icon: null,
    capabilities: null,
  }
  for (const alias of ["ANT_CLAUDE_CODE", "Ant Claude Code", "AntCC"]) {
    assert.equal(resolveAgentDisplayName(alias, [seededType]), "Claude Code")
  }
  assert.equal(
    resolveAgentDisplayName(seededType.agent_product, [
      { ...seededType, display_name: "Saved team label" },
    ]),
    "Saved team label"
  )
})

test("trace diagnosis localizes known specialist defaults while preserving renamed and future definitions", () => {
  const { diagnosisRunLabels } = nativeUiHarness().load<
    typeof import("../src/components/diagnose/diagnosisRunLabels.ts")
  >("components/diagnose/diagnosisRunLabels.ts")
  const item = {
    type: "builtin",
    mode: "rule",
    version: "1.0.0",
    is_specialist: true,
    builtin_key: "numeric_consistency",
    canonical_variant: "decimal_binary",
    name: "数值计算一致性",
    description:
      "只核对明确的有限Decimal二元运算，不验证数值来源或日期、单位换算。",
  } as DiagnosisRunOption
  for (const [language, expected] of [
    ["en-US", "Numeric consistency"],
    ["ja-JP", "数値計算の整合性"],
    ["zh-CN", "数值计算一致性"],
  ]) {
    const i18n = createInstance()
    i18n.init({
      lng: language,
      resources: { [language]: { translation: resource(language) } },
      initImmediate: false,
    })
    assert.equal(diagnosisRunLabels(item, i18n.t).name, expected)
    assert.equal(
      diagnosisRunLabels({ ...item, name: "My saved check" }, i18n.t).name,
      "My saved check"
    )
    assert.equal(
      diagnosisRunLabels({ ...item, description: "My scope" }, i18n.t)
        .description,
      "My scope"
    )
    assert.equal(
      diagnosisRunLabels({ ...item, version: "2.0.0" }, i18n.t).name,
      item.name
    )
    assert.equal(
      diagnosisRunLabels({ ...item, type: "custom" }, i18n.t).name,
      item.name
    )
  }
})

function resource(locale: string) {
  const path = resolve(sourceRoot, "i18n/locales", `${locale}.json`)
  const source = readFileSync(path, "utf8")
  // JSON.parse silently accepts duplicate keys. Inspect the original tree first.
  function check(node: ts.Node) {
    if (ts.isObjectLiteralExpression(node)) {
      const keys = new Set<string>()
      for (const property of node.properties) {
        assert.ok(ts.isPropertyAssignment(property))
        assert.ok(ts.isStringLiteral(property.name))
        const key = property.name.text
        assert.ok(!keys.has(key), `${locale}: duplicate key ${key}`)
        keys.add(key)
      }
    }
    ts.forEachChild(node, check)
  }
  check(ts.parseJsonText(path, source))
  return JSON.parse(source)
}

function flatten(value: unknown, prefix = ""): Record<string, string> {
  if (typeof value === "string") return { [prefix]: value }
  assert.ok(value && typeof value === "object", `Non-text resource: ${prefix}`)
  return Object.assign(
    {},
    ...Object.entries(value).map(([key, child]) =>
      flatten(child, prefix ? `${prefix}.${key}` : key)
    )
  )
}

function runtime(
  saved?: string,
  blockedStorage = false,
  languages: string[] = []
) {
  const i18n = createInstance()
  const values = new Map(saved === undefined ? [] : [["language", saved]])
  values.set("selectedSpaceId", "test-space")
  const localStorage = {
    getItem(key: string) {
      if (blockedStorage) throw new Error("Storage unavailable")
      return values.get(key) ?? null
    },
    setItem(key: string, value: string) {
      if (blockedStorage) throw new Error("Storage unavailable")
      values.set(key, value)
    },
  }
  const document = { documentElement: { lang: "zh-CN" } }
  const cache = new Map<string, { exports: Record<string, unknown> }>()
  function load(path: string): Record<string, unknown> {
    const filename = resolve(sourceRoot, path)
    const cached = cache.get(filename)
    if (cached) return cached.exports
    const module = { exports: {} }
    cache.set(filename, module)
    const { outputText } = ts.transpileModule(readFileSync(filename, "utf8"), {
      compilerOptions: {
        module: ts.ModuleKind.CommonJS,
        target: ts.ScriptTarget.ES2022,
        esModuleInterop: true,
      },
    })
    runInNewContext(outputText, {
      module,
      exports: module.exports,
      document,
      localStorage,
      navigator: { languages },
      require(name: string) {
        if (name === "i18next") return i18n
        if (name === "@/i18n") return load("i18n/index.ts")
        if (name.endsWith(".json"))
          return require(resolve(dirname(filename), name))
        if (name.startsWith(".")) {
          return load(resolve(dirname(filename), `${name}.ts`))
        }
        return require(name)
      },
    })
    return module.exports
  }
  const api = load("i18n/index.ts") as {
    getCurrentLocale(): string
    isChineseLocale(): boolean
    changeLanguage(locale: string): Promise<void>
    languageOptions: { locale: string; labelKey: string }[]
  }
  return { api, i18n, values, document, load }
}

test("explicit supported preferences override browser languages", () => {
  for (const locale of ["zh-CN", "en-US", "ja-JP"] as const)
    assert.equal(preferredLocale(locale, ["fr-FR", "ja-JP", "en-US"]), locale)
})

test("first visit follows the first supported browser language across regional variants", () => {
  for (const [languages, expected] of [
    [["fr-FR", "en-GB", "ja-JP"], "en-US"],
    [["de-DE", "JA_jp", "en-US"], "ja-JP"],
    [["zh-TW", "en-US"], "zh-CN"],
    [["en"], "en-US"],
    [["ja"], "ja-JP"],
    [["fr-FR"], "zh-CN"],
    [[], "zh-CN"],
  ] as const) {
    assert.equal(preferredLocale(null, languages), expected)
    assert.equal(preferredLocale("unsupported", languages), expected)
  }
})

test("startup applies browser language even when preference storage is unavailable", () => {
  for (const blocked of [false, true]) {
    const { api, i18n, document } = runtime(undefined, blocked, [
      "fr-FR",
      "en-GB",
    ])
    assert.equal(api.getCurrentLocale(), "en-US")
    assert.equal(document.documentElement.lang, "en-US")
    assert.equal(i18n.t("localAuth.titles.login"), "Sign in to AI Vision")
  }
  assert.equal(
    runtime("ja-JP", false, ["en-US"]).api.getCurrentLocale(),
    "ja-JP"
  )
})

test("Japanese covers English resources, arrays, placeholders and rich-text tags", () => {
  const english = flatten(resource("en-US"))
  const japanese = flatten(resource("ja-JP"))
  const chinese = resource("zh-CN")
  assert.deepEqual(Object.keys(japanese).sort(), Object.keys(english).sort())
  const variables = (value: string) =>
    [...value.matchAll(/{{(.*?)}}/g)].map((m) => m[1]).sort()
  const tags = (value: string) =>
    [...value.matchAll(/<\/?[a-z][a-z0-9]*(?:\s[^<>]*?)?\/?>/gi)]
      .map((m) => m[0])
      .sort()
  for (const [key, value] of Object.entries(japanese)) {
    assert.ok(value.trim(), `Empty translation: ${key}`)
    assert.doesNotMatch(value, /\b(?:TODO|FIXME|TBD)\b/, key)
    assert.deepEqual(variables(value), variables(english[key]), key)
    assert.deepEqual(tags(value), tags(english[key]), key)
  }
  assert.equal(chinese.language.ja, "日本語")
  assert.equal(resource("en-US").language.ja, "日本語")
  assert.equal(resource("ja-JP").language.ja, "日本語")
})

test("saved languages and unsupported storage values initialize deterministically", () => {
  for (const [saved, expected] of [
    [undefined, "zh-CN"],
    ["", "zh-CN"],
    ["ja", "zh-CN"],
    ["unsupported", "zh-CN"],
    ["zh-CN", "zh-CN"],
    ["en-US", "en-US"],
    ["ja-JP", "ja-JP"],
  ]) {
    const { api, document } = runtime(saved)
    assert.equal(api.getCurrentLocale(), expected)
    assert.equal(document.documentElement.lang, expected)
  }
})

test("language switching updates actual translations, document language and storage", async () => {
  const { api, i18n, values, document } = runtime()
  for (const [locale, title] of [
    ["ja-JP", "AI Vision にログイン"],
    ["en-US", "Sign in to AI Vision"],
    ["zh-CN", "登录 AI Vision"],
  ]) {
    await api.changeLanguage(locale)
    assert.equal(i18n.t("localAuth.titles.login"), title)
    assert.equal(api.getCurrentLocale(), locale)
    assert.equal(document.documentElement.lang, locale)
    assert.equal(values.get("language"), locale)
    assert.equal(values.get("selectedSpaceId"), "test-space")
    assert.equal(runtime(values.get("language")).api.getCurrentLocale(), locale)
  }
  assert.equal(api.languageOptions.length, 3)
})

test("blocked browser storage still permits language selection in the current session", async () => {
  const { api, i18n, document } = runtime("ja-JP", true)
  assert.equal(api.getCurrentLocale(), "zh-CN")
  await api.changeLanguage("ja-JP")
  assert.equal(i18n.t("nav.settings"), "スペース設定")
  assert.equal(document.documentElement.lang, "ja-JP")
})

test("Japanese falls back to English then Chinese; existing English fallback stays Chinese", async () => {
  const { i18n } = runtime("ja-JP")
  i18n.addResource(
    "en-US",
    "translation",
    "fallbackTest.english",
    "English fallback"
  )
  i18n.addResource("zh-CN", "translation", "fallbackTest.english", "中文兜底")
  i18n.addResource("zh-CN", "translation", "fallbackTest.chinese", "中文兜底")
  assert.equal(i18n.t("fallbackTest.english"), "English fallback")
  assert.equal(i18n.t("fallbackTest.chinese"), "中文兜底")
  await i18n.changeLanguage("en-US")
  assert.equal(i18n.t("fallbackTest.chinese"), "中文兜底")
})

test("all Japanese leaves resolve without fallback, with real interpolation and arrays", () => {
  const { i18n } = runtime("ja-JP")
  const leaves = flatten(resource("ja-JP"))
  for (const [key, value] of Object.entries(leaves)) {
    const variables = Object.fromEntries(
      [...value.matchAll(/{{(.*?)}}/g)].map((m) => [m[1], `VALUE_${m[1]}`])
    )
    const result = i18n.t(key, {
      ...variables,
      nsSeparator: false,
      returnDetails: true,
    })
    assert.equal(result.usedLng, "ja-JP", key)
    assert.equal(
      result.res,
      value.replace(/{{(.*?)}}/g, (_, name) => `VALUE_${name}`),
      key
    )
  }
  const logicKey = "diagnose.diagnoser.builtinDetailsV3.tool_success.logic"
  const logic = i18n.t(logicKey, {
    returnObjects: true,
  })
  assert.ok(Array.isArray(logic))
  assert.deepEqual(
    logic,
    resource("ja-JP").diagnose.diagnoser.builtinDetailsV3.tool_success.logic
  )
  assert.equal(
    i18n.t("diagnose.quick.errors.QUICK_PERMISSION_DENIED"),
    "このチェックを実行する権限がありません。"
  )
  assert.match(
    i18n.t("diagnose.diagnoser.builtinDescriptionsV3.tool_success"),
    /品質を直接判定するものではありません/
  )
  assert.match(
    i18n.t("diagnose.diagnoser.form.trialNoMetricsHelp"),
    /合格とはみなせません/
  )
})

test("calendar locale includes Japanese month, weekday and accessible navigation labels", () => {
  const { load } = runtime()
  const { getCalendarLocale } = load("i18n/calendar.ts") as {
    getCalendarLocale(language: string): {
      code: string
      localize: { month(n: number): string; day(n: number): string }
      labels?: { labelNext: string; labelPrevious: string }
    }
  }
  const japanese = getCalendarLocale("ja-JP")
  assert.equal(japanese.code, "ja")
  assert.equal(japanese.localize.month(9), "10月")
  assert.equal(japanese.localize.day(0), "日曜日")
  assert.equal(japanese.labels?.labelNext, "次の月へ")
  assert.equal(japanese.labels?.labelPrevious, "前の月へ")
  assert.equal(getCalendarLocale("zh-CN").code, "zh-CN")
  assert.equal(getCalendarLocale("en-US").code, "en-US")
})

test("Japanese uses English compact numeric and duration conventions without changing values", async () => {
  const { api, load } = runtime("ja-JP")
  const format = load("lib/formatters.ts") as {
    formatToken(n: number): string
    formatTokenAuto(n: number): { displayValue: string; unit: string }
    formatDurationFromSeconds(n: number): { displayValue: string; unit: string }
  }
  assert.equal(api.isChineseLocale(), false)
  assert.equal(format.formatToken(1234000), "1.23 M")
  assert.equal(format.formatTokenAuto(1000000000).unit, "B")
  assert.equal(format.formatDurationFromSeconds(86400).unit, "d")
  await api.changeLanguage("zh-CN")
  assert.equal(api.isChineseLocale(), true)
  assert.equal(format.formatToken(10000), "1 万")
  await api.changeLanguage("en-US")
  assert.equal(format.formatToken(10000), "10 K")
})

test("Japanese builtin labels preserve versioned meaning and leave user descriptions intact", () => {
  const { i18n } = runtime("ja-JP")
  const { toDiagnoser } = nativeUiHarness({
    "./resourceScopes": { isUserSharedSpace },
  }).load<typeof Adapters>("components/diagnose/diagnoseAdapters.ts")
  const rule: DiagnoserDTO = {
    uuid: "test-rule",
    space_uuid: "test-space",
    agent_uuid: "test-agent",
    name: "Original server name",
    description: "Original user description",
    type: "builtin",
    mode: "rule",
    spec: { executor: "stuck_loop" },
    version: "3.0.0",
    gmt_create: "2026-10-15",
    gmt_modified: "2026-10-15",
    creator: null,
    modifier: null,
  }
  const t = i18n.t.bind(i18n)
  const current = toDiagnoser(rule, undefined, t)
  assert.equal(current.name, "繰り返し呼び出しの手がかり")
  assert.match(current.description, /品質を直接判定するものではありません/)
  assert.equal(
    toDiagnoser({ ...rule, version: "v2" }, undefined, t).name,
    "ループ検出"
  )
  for (const input of [
    { ...rule, type: "custom" as const },
    { ...rule, mode: "llm" as const },
  ]) {
    const result = toDiagnoser(input, undefined, t)
    assert.equal(result.name, input.name)
    assert.equal(result.description, input.description)
  }
})

test("Japanese MCP and decision counts use Japanese grammar for zero, one and multiple items", () => {
  const { i18n } = runtime("ja-JP")
  for (const count of [0, 1, 2]) {
    assert.equal(
      i18n.t("labs.mcp.trace.failures", { count }),
      `${count} 件の失敗`
    )
    assert.equal(
      i18n.t("labs.decision.summary.askedUser", { count }),
      `${count} 回のユーザー確認`
    )
    assert.equal(
      i18n.t("labs.decision.summary.fallbacks", { count }),
      `${count} 回のフォールバック`
    )
  }
})
