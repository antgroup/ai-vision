import assert from "node:assert/strict"
import { existsSync, readFileSync } from "node:fs"
import { createRequire } from "node:module"
import test from "node:test"
import { JSDOM } from "jsdom"
import { act, createElement, type ComponentType } from "react"
import ts from "typescript"

const require = createRequire(import.meta.url)
const skillText =
  "---\nname: deploy\ndescription: Branch-specific skill\n---\n\n# Deploy"
const entry = {
  path: "skills/deploy/SKILL.md",
  name: "deploy",
  dirname: "deploy",
  description: "",
}

async function fixture(
  content = skillText,
  repositoryRef = "feature/skills",
  registrySource = "owner/repo",
  registryId = `${registrySource}/deploy`,
  saveGate?: Promise<void>,
  resolveMode?: "snapshot" | "unresolved" | "listing"
) {
  const dom = new JSDOM("<!doctype html><div id='root'></div>", {
    url: "https://vision.example.test",
  })
  const originals = new Map<string, PropertyDescriptor | undefined>()
  for (const [key, value] of Object.entries({
    window: dom.window,
    document: dom.window.document,
    navigator: dom.window.navigator,
    HTMLElement: dom.window.HTMLElement,
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
  const { QueryClient, QueryClientProvider } = require("@tanstack/react-query")
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: Infinity } },
  })
  const fetches: Array<{ source: string; ref?: string; path: string }> = []
  const resolves: unknown[] = []
  const saves: unknown[] = []
  const api = {
    resolveSkillRepo: async (_space: string, payload: { skillId?: string }) => {
      resolves.push(payload)
      if (resolveMode === "unresolved")
        throw Object.assign(new Error("Cannot locate the selected file"), {
          code: 3123,
        })
      return {
        source: "owner/repo",
        branch: repositoryRef,
        subdir: "skills",
        skills: [
          resolveMode === "snapshot"
            ? { ...entry, path: "SKILL.md", content, registrySnapshot: true }
            : payload.skillId && resolveMode !== "listing"
              ? { ...entry, content }
              : entry,
        ],
      }
    },
    fetchRegistrySkill: async (
      _space: string,
      payload: (typeof fetches)[number]
    ) => {
      fetches.push(payload)
      return { ...entry, content }
    },
    saveSkillConfigBatch: async (_space: string, configs: { name: string }[]) => {
      saves.push(...configs)
      await saveGate
      return configs.map(({ name }) => ({ requestedName: name, name }))
    },
    searchSkillRegistry: async () => ({
      skills: [
        {
          id: registryId,
          source: registrySource,
          skillId: "deploy",
          name: "deploy",
          installs: 1,
          url: `https://skills.sh/${registrySource}/deploy`,
        },
      ],
    }),
  }
  const modules = new Map<string, unknown>()
  function load<T>(path: string | URL): T {
    const file =
      typeof path === "string"
        ? new URL(`../src/labs/skill/${path}`, import.meta.url)
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
      if (name === "./skillApi") return api
      if (name === "@/components/labs/shared/localImportBatchCollision")
        return load(
          new URL(
            "../src/components/labs/shared/localImportBatchCollision.ts",
            import.meta.url
          )
        )
      if (name === "react-i18next")
        return { useTranslation: () => ({ t: (key: string) => key }) }
      if (name === "@/components/ui/button")
        return {
          Button: ({ asChild, ...props }: { asChild?: boolean }) =>
            createElement(asChild ? "div" : "button", props),
        }
      if (name === "@/components/ui/input") return { Input: "input" }
      if (name === "@/components/ui/checkbox")
        return {
          Checkbox: ({ checked }: { checked: boolean }) =>
            createElement("input", {
              type: "checkbox",
              checked,
              readOnly: true,
            }),
        }
      if (name.startsWith(".")) {
        const base = new URL(name, file)
        const resolved = ["", ".ts", ".tsx"]
          .map((ext) => new URL(base.href + ext))
          .find(existsSync)
        assert.ok(resolved, `Missing ${name}`)
        return load(resolved)
      }
      return require(name)
    }, exports)
    return exports as T
  }
  const root = createRoot(dom.window.document.getElementById("root")!)
  async function flush() {
    for (let i = 0; i < 6; i++)
      await act(async () => {
        await new Promise((resolve) => {
          setTimeout(resolve, 0)
        })
      })
  }
  return {
    load,
    fetches,
    flush,
    resolves,
    saves,
    document: dom.window.document,
    async render(component: ReturnType<typeof createElement>) {
      await act(async () =>
        root.render(createElement(QueryClientProvider, { client }, component))
      )
      await flush()
    },
    async click(label: string) {
      const element = [...dom.window.document.querySelectorAll("button")].find(
        (button) => button.textContent === label
      )
      assert.ok(element, `Missing button ${label}`)
      await act(async () =>
        element.dispatchEvent(
          new dom.window.MouseEvent("click", { bubbles: true })
        )
      )
      await flush()
    },
    async input(value: string) {
      const element = dom.window.document.querySelector("input")!
      await act(async () => {
        Object.getOwnPropertyDescriptor(
          dom.window.HTMLInputElement.prototype,
          "value"
        )!.set!.call(element, value)
        element.dispatchEvent(new dom.window.Event("input", { bubbles: true }))
      })
      await flush()
    },
    async cleanup() {
      await act(async () => root.unmount())
      client.clear()
      dom.window.close()
      for (const [name, original] of originals) {
        if (original) Object.defineProperty(globalThis, name, original)
        else delete (globalThis as Record<string, unknown>)[name]
      }
    },
  }
}

test("link imports fetch and save the selected repository branch", async () => {
  const ui = await fixture()
  const { SkillImportLinkPanel } = ui.load<{
    SkillImportLinkPanel: ComponentType<Record<string, unknown>>
  }>("SkillImportLinkPanel.tsx")
  let closed = false
  try {
    await ui.render(
      createElement(SkillImportLinkPanel, {
        spaceId: "space-a",
        onClose() {
          closed = true
        },
      })
    )
    await ui.input("https://github.com/owner/repo/tree/feature/skills")
    await ui.click("labs.skill.import.link.resolve")
    await ui.click("labs.skill.import.link.install")
    assert.deepEqual(ui.fetches, [
      { source: "owner/repo", ref: "feature/skills", path: entry.path },
    ])
    assert.equal(ui.saves.length, 1)
    assert.deepEqual((ui.saves[0] as { raw: unknown }).raw, {
      content: skillText,
      importSource: {
        repository: "owner/repo",
        ref: "feature/skills",
        path: entry.path,
      },
    })
    assert.equal((ui.saves[0] as { source: string }).source, "skillmd")
    assert.equal(closed, true)
  } finally {
    await ui.cleanup()
  }
})

test("unparseable link imports stay open and report the skipped file", async () => {
  const ui = await fixture("# No skill metadata")
  const { SkillImportLinkPanel } = ui.load<{
    SkillImportLinkPanel: ComponentType<Record<string, unknown>>
  }>("SkillImportLinkPanel.tsx")
  let closed = false
  try {
    await ui.render(
      createElement(SkillImportLinkPanel, {
        spaceId: "space-a",
        onClose() {
          closed = true
        },
      })
    )
    await ui.input("https://github.com/owner/repo")
    await ui.click("labs.skill.import.link.resolve")
    await ui.click("labs.skill.import.link.install")
    assert.equal(closed, false)
    assert.equal(ui.saves.length, 0)
    assert.equal(
      ui.document.querySelector('[role="alert"]')?.textContent,
      "labs.skill.topic.installResult"
    )
  } finally {
    await ui.cleanup()
  }
})

for (const valid of [true, false]) {
  test(`registry imports ${valid ? "retain the resolved branch" : "do not claim an invalid file was installed"}`, async () => {
    const ui = await fixture(valid ? skillText : "# No skill metadata")
    const { SkillImportRegistryPanel } = ui.load<{
      SkillImportRegistryPanel: ComponentType<Record<string, unknown>>
    }>("SkillImportRegistryPanel.tsx")
    try {
      await ui.render(
        createElement(SkillImportRegistryPanel, {
          spaceId: "space-a",
        })
      )
      await ui.input("deploy")
      await ui.click("labs.skill.import.registry.searchButton")
      await ui.click("labs.skill.import.registry.install")
      assert.equal(ui.fetches.length, 0)
      assert.equal(ui.saves.length, valid ? 1 : 0)
      if (valid)
        assert.deepEqual((ui.saves[0] as { raw: unknown }).raw, {
          content: skillText,
          importSource: {
            repository: "owner/repo",
            ref: "feature/skills",
            path: entry.path,
          },
        })
      assert.equal(
        ui.document.body.textContent?.includes(
          "labs.skill.import.registry.installed"
        ),
        valid
      )
      assert.equal(!!ui.document.querySelector('[role="alert"]'), !valid)
    } finally {
      await ui.cleanup()
    }
  })
}

test("registry imports preserve HEAD as the requested ref", async () => {
  const ui = await fixture(skillText, "HEAD")
  const { SkillImportRegistryPanel } = ui.load<{
    SkillImportRegistryPanel: ComponentType<Record<string, unknown>>
  }>("SkillImportRegistryPanel.tsx")
  try {
    await ui.render(
      createElement(SkillImportRegistryPanel, {
        spaceId: "space-a",
      })
    )
    await ui.input("deploy")
    await ui.click("labs.skill.import.registry.searchButton")
    await ui.click("labs.skill.import.registry.install")
    assert.equal(
      (ui.saves[0] as { raw: { importSource: { ref: string } } }).raw
        .importSource.ref,
      "HEAD"
    )
  } finally {
    await ui.cleanup()
  }
})

test("registry keeps non-GitHub results viewable without offering an invalid import", async () => {
  const ui = await fixture(skillText, "HEAD", "smithery.ai")
  const { SkillImportRegistryPanel } = ui.load<{
    SkillImportRegistryPanel: ComponentType<Record<string, unknown>>
  }>("SkillImportRegistryPanel.tsx")
  try {
    await ui.render(
      createElement(SkillImportRegistryPanel, {
        spaceId: "space-a",
      })
    )
    await ui.input("deploy")
    await ui.click("labs.skill.import.registry.searchButton")
    const link = ui.document.querySelector("a")!
    assert.equal(link.href, "https://skills.sh/smithery.ai/deploy")
    const importButton = [...ui.document.querySelectorAll("button")].find(
      (button) => button.textContent === "labs.skill.import.registry.install"
    )!
    assert.equal(importButton.disabled, true)
    assert.ok(
      ui.document.body.textContent?.includes(
        "labs.skill.import.registry.githubOnly"
      )
    )
    await ui.click("labs.skill.import.registry.install")
    assert.equal(ui.resolves.length, 0)
    assert.equal(ui.fetches.length, 0)
    assert.equal(ui.saves.length, 0)
  } finally {
    await ui.cleanup()
  }
})

test("registry imports only the selected file, without a second fetch", async () => {
  const source = "owner-name/repo.name_v1"
  const ui = await fixture(skillText, "HEAD", source, `${source}/text/deploy`)
  const { SkillImportRegistryPanel } = ui.load<{
    SkillImportRegistryPanel: ComponentType<Record<string, unknown>>
  }>("SkillImportRegistryPanel.tsx")
  try {
    await ui.render(
      createElement(SkillImportRegistryPanel, {
        spaceId: "space-a",
      })
    )
    await ui.input("deploy")
    await ui.click("labs.skill.import.registry.searchButton")
    await ui.click("labs.skill.import.registry.install")
    assert.deepEqual(ui.resolves, [{ source, skillId: "deploy" }])
    assert.equal(ui.saves.length, 1)
    assert.equal(ui.fetches.length, 0)
    assert.equal((ui.saves[0] as { name: string }).name, "deploy")
  } finally {
    await ui.cleanup()
  }
})

for (const mode of ["unresolved", "listing"] as const) {
  test(`registry response ${mode} shows a file-link hint without saving`, async () => {
    const ui = await fixture(
      skillText,
      "HEAD",
      "owner/repo",
      "owner/repo/deploy",
      undefined,
      mode
    )
    const { SkillImportRegistryPanel } = ui.load<{
      SkillImportRegistryPanel: ComponentType<Record<string, unknown>>
    }>("SkillImportRegistryPanel.tsx")
    try {
      await ui.render(
        createElement(SkillImportRegistryPanel, { spaceId: "space-a" })
      )
      await ui.input("deploy")
      await ui.click("labs.skill.import.registry.searchButton")
      await ui.click("labs.skill.import.registry.install")
      assert.equal(
        ui.document.querySelector('[role="alert"]')?.textContent,
        "labs.skill.import.resolveErrors.unresolved"
      )
      assert.equal(ui.resolves.length, 1)
      assert.equal(ui.fetches.length, 0)
      assert.equal(ui.saves.length, 0)
    } finally {
      await ui.cleanup()
    }
  })
}

test("registry snapshots do not save an unverified Git path or ref", async () => {
  const ui = await fixture(
    skillText,
    "",
    "owner/repo",
    "owner/repo/deploy",
    undefined,
    "snapshot"
  )
  const { SkillImportRegistryPanel } = ui.load<{
    SkillImportRegistryPanel: ComponentType<Record<string, unknown>>
  }>("SkillImportRegistryPanel.tsx")
  try {
    await ui.render(
      createElement(SkillImportRegistryPanel, { spaceId: "space-a" })
    )
    await ui.input("deploy")
    await ui.click("labs.skill.import.registry.searchButton")
    await ui.click("labs.skill.import.registry.install")
    assert.equal(ui.fetches.length, 0)
    assert.equal(ui.saves.length, 1)
    const config = ui.saves[0] as {
      path?: string
      raw: { importSource: unknown }
    }
    assert.ok(!config.path)
    assert.deepEqual(config.raw.importSource, {
      repository: "owner/repo",
      registry: "skills.sh",
    })
  } finally {
    await ui.cleanup()
  }
})

test("a completed unmounted remote import cannot close a newly opened Space dialog", async () => {
  let resolveSave: () => void = () => {}
  const gate = new Promise<void>((resolve) => {
    resolveSave = resolve
  })
  const ui = await fixture(
    skillText,
    "main",
    "owner/repo",
    "owner/repo/deploy",
    gate
  )
  const { SkillImportLinkPanel } = ui.load<{
    SkillImportLinkPanel: ComponentType<Record<string, unknown>>
  }>("SkillImportLinkPanel.tsx")
  let closes = 0
  const panel = (spaceId: string) =>
    createElement(SkillImportLinkPanel, {
      key: spaceId,
      spaceId,
      onClose: () => {
        closes++
      },
    })
  try {
    await ui.render(panel("space-a"))
    await ui.input("https://github.com/owner/repo")
    await ui.click("labs.skill.import.link.resolve")
    await ui.click("labs.skill.import.link.install")
    await ui.render(panel("space-b"))
    resolveSave()
    await ui.flush()
    assert.equal(closes, 0)
    assert.equal(ui.document.querySelector("[role=alert]"), null)
  } finally {
    resolveSave()
    await ui.cleanup()
  }
})
