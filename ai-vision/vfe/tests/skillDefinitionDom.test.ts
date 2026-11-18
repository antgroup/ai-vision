import assert from "node:assert/strict"
import { existsSync, readFileSync } from "node:fs"
import { createRequire } from "node:module"
import test from "node:test"
import { JSDOM } from "jsdom"
import { act, createElement, type ComponentType, type ReactNode } from "react"
import ts from "typescript"
import type { SkillConfig } from "../src/labs/skill/types.ts"

const require = createRequire(import.meta.url)

async function definitionFixture(configs: unknown[] = []) {
  const dom = new JSDOM("<!doctype html><div id='root'></div>", {
    pretendToBeVisual: true,
  })
  const originals = new Map<string, PropertyDescriptor | undefined>()
  for (const [key, value] of Object.entries({
    window: dom.window,
    document: dom.window.document,
    navigator: dom.window.navigator,
    HTMLElement: dom.window.HTMLElement,
    HTMLInputElement: dom.window.HTMLInputElement,
    HTMLTextAreaElement: dom.window.HTMLTextAreaElement,
    Element: dom.window.Element,
    Node: dom.window.Node,
    NodeFilter: dom.window.NodeFilter,
    DocumentFragment: dom.window.DocumentFragment,
    MutationObserver: dom.window.MutationObserver,
    CustomEvent: dom.window.CustomEvent,
    Event: dom.window.Event,
    getComputedStyle: dom.window.getComputedStyle.bind(dom.window),
    requestAnimationFrame: dom.window.requestAnimationFrame.bind(dom.window),
    cancelAnimationFrame: dom.window.cancelAnimationFrame.bind(dom.window),
    IS_REACT_ACT_ENVIRONMENT: true,
  })) {
    originals.set(key, Object.getOwnPropertyDescriptor(globalThis, key))
    Object.defineProperty(globalThis, key, {
      configurable: true,
      writable: true,
      value,
    })
  }
  const { createRoot } = await import("react-dom/client")
  const modules = new Map<string, unknown>()
  function load<T>(path: string | URL): T {
    const file =
      typeof path === "string"
        ? new URL(`../src/${path}`, import.meta.url)
        : path
    if (modules.has(file.href)) return modules.get(file.href) as T
    const exports = {}
    modules.set(file.href, exports)
    const { outputText } = ts.transpileModule(readFileSync(file, "utf8"), {
      compilerOptions: {
        module: ts.ModuleKind.CommonJS,
        target: ts.ScriptTarget.ES2022,
        jsx: ts.JsxEmit.ReactJSX,
        esModuleInterop: true,
      },
    })
    new Function("require", "exports", outputText)((name: string) => {
      if (name === "react-i18next")
        return { useTranslation: () => ({ t: (key: string) => key }) }
      if (name === "@tanstack/react-query")
        return { useQuery: () => ({ data: configs }) }
      if (name === "./skillApi") return {}
      if (name === "./SkillConfigImportDialog")
        return { SkillConfigImportDialog: () => null }
      if (name === "./SkillDrawer") return { SkillDrawer: () => null }
      if (name.startsWith("@/contexts/")) return {}
      if (name === "@/components/labs/shared/LabsSection")
        return {
          LabsSection: ({ children }: { children: ReactNode }) =>
            createElement("section", null, children),
        }
      if (name.startsWith(".") || name.startsWith("@/")) {
        const base = name.startsWith("@/")
          ? new URL(`../src/${name.slice(2)}`, import.meta.url)
          : new URL(name, file)
        const resolved = ["", ".ts", ".tsx"]
          .map((ext) => new URL(base.href + ext))
          .find(existsSync)
        assert.ok(resolved, `Missing dependency ${name}`)
        return load(resolved)
      }
      return require(name)
    }, exports)
    return exports as T
  }
  const root = createRoot(dom.window.document.getElementById("root")!)
  return {
    document: dom.window.document,
    load,
    async renderNode(node: ReactNode) {
      await act(async () => root.render(node))
    },
    async cleanup() {
      await act(async () => root.unmount())
      await act(async () => {
        await new Promise((resolve) => {
          setTimeout(resolve, 0)
        })
      })
      dom.window.close()
      for (const [name, original] of originals) {
        if (original) Object.defineProperty(globalThis, name, original)
        else delete (globalThis as Record<string, unknown>)[name]
      }
    },
  }
}

test("Skill current copy stays distinct from an older call after same-name reimport", async () => {
  const ui = await definitionFixture()
  const { SkillDrawer } = ui.load<{
    SkillDrawer: ComponentType<Record<string, unknown>>
  }>("labs/skill/SkillDrawer.tsx")
  const originalCall = {
    name: "deploy",
    derived: false,
    traceId: "old-trace",
    spanId: "old-span",
    agentProduct: "Codex",
    status: "ok",
    durationMs: 7,
    timestamp: "2026-10-20T01:00:00Z",
  }
  const config: SkillConfig = {
    name: "deploy",
    description: "current definition",
    path: "skills/deploy/SKILL.md",
    source: "skillmd",
    allowedTools: [],
    license: "",
    compatibility: "",
    importedAt: "2026-10-26T12:34:56.123456Z",
    raw: {
      content: "# Current definition",
      importSource: {
        repository: "owner/repo",
        ref: "feature/current-copy",
        path: "skills/deploy/SKILL.md",
      },
      customEvidence: { keep: true },
    },
  }
  const before = structuredClone({ config, originalCall })
  const props = {
    skillName: "deploy",
    stat: {
      name: "deploy",
      calls: 1,
      derived: false,
      errors: 0,
      errorRate: 0,
      totalMs: 7,
      p50Ms: 7,
      p95Ms: 7,
      lastSeen: originalCall.timestamp,
      sourceAgents: ["Codex"],
      recentCalls: [originalCall],
    },
    config,
    currentTraceId: "old-trace",
    onSelectSpan() {},
    onClose() {},
  }
  try {
    await ui.renderNode(createElement(SkillDrawer, props))
    assert.match(
      ui.document.body.textContent ?? "",
      /labs\.skill\.topic\.currentImportCopy/
    )
    assert.match(
      ui.document.body.textContent ?? "",
      /labs\.skill\.topic\.unlinkedDefinition/
    )
    assert.match(
      ui.document.body.textContent ?? "",
      /labs\.skill\.topic\.requestedRefMeaning/
    )
    assert.equal(
      ui.document.querySelector('time[datetime="2026-10-26T12:34:56.123456Z"]')
        ?.textContent,
      config.importedAt
    )
    const nextImport = "2026-10-27T13:14:15.654321Z"
    await ui.renderNode(
      createElement(SkillDrawer, {
        ...props,
        config: {
          ...config,
          importedAt: nextImport,
          raw: { ...config.raw, content: "# Reimported definition" },
        },
      })
    )
    assert.equal(
      ui.document.querySelector(`time[datetime="${nextImport}"]`)?.textContent,
      nextImport
    )
    assert.equal(
      ui.document.querySelector(`time[datetime="${config.importedAt}"]`),
      null
    )
    const history = [...ui.document.querySelectorAll('[role="tab"]')].find(
      (node) => node.textContent === "labs.skill.drawer.callsTab"
    ) as HTMLElement
    assert.ok(history)
    await act(async () => history.focus())
    assert.match(
      ui.document.body.textContent ?? "",
      /labs\.skill\.topic\.unlinkedDefinition/
    )
    assert.match(ui.document.body.textContent ?? "", /2026-10-20T01:00:00Z/)
    assert.deepEqual({ config, originalCall }, before)
  } finally {
    await ui.cleanup()
  }
})

test("Skill local definitions do not fabricate repository revisions or import timestamps", async () => {
  const ui = await definitionFixture()
  const { SkillDrawer } = ui.load<{
    SkillDrawer: ComponentType<Record<string, unknown>>
  }>("labs/skill/SkillDrawer.tsx")
  try {
    await ui.renderNode(
      createElement(SkillDrawer, {
        skillName: "legacy",
        stat: null,
        config: {
          name: "legacy",
          source: "manifest",
          path: "/skills/legacy",
          description: "",
          allowedTools: [],
          license: "",
          compatibility: "",
          importedAt: "",
          raw: { entry: { custom: "retained" } },
        },
        onClose() {},
      })
    )
    assert.match(
      ui.document.body.textContent ?? "",
      /labs\.skill\.topic\.currentImportCopy/
    )
    assert.match(
      ui.document.body.textContent ?? "",
      /labs\.skill\.topic\.importTimeUnknown/
    )
    assert.doesNotMatch(
      ui.document.body.textContent ?? "",
      /labs\.skill\.drawer\.requestedRef|labs\.skill\.drawer\.repository|labs\.skill\.topic\.requestedRefMeaning/
    )
    assert.equal(ui.document.querySelector("time"), null)
    assert.match(ui.document.body.textContent ?? "", /retained/)
  } finally {
    await ui.cleanup()
  }
})
