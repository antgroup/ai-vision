import assert from "node:assert/strict"
import { existsSync, readFileSync } from "node:fs"
import { createRequire } from "node:module"
import { dirname, resolve } from "node:path"
import test from "node:test"
import { fileURLToPath } from "node:url"
import { act, createElement, type ComponentType } from "react"
import { createRoot } from "react-dom/client"
import { JSDOM } from "jsdom"
import ts from "typescript"

const require = createRequire(import.meta.url)
const sourceRoot = fileURLToPath(new URL("../src/", import.meta.url))
const jsonStringLabel = require("../src/i18n/locales/en-US.json").traceLabels
  .jsonString
type Span = {
  trace_id: string
  span_id: string
  name: string
  attributes: Record<string, unknown>
  status?: { code: string; message?: string }
  resource?: Record<string, unknown>
}

/** Real parent, cards, parsers and content renderers; only service/context and
 * unrelated drawers are boundaries. In particular, disabled queries may still
 * return cached values, just as a shared query cache can in the application. */
function components() {
  const queries: { queryKey: unknown[]; enabled?: boolean }[] = []
  const privateSkill = {
    name: "version-research",
    description: "PRIVATE_SKILL_DESCRIPTION",
    allowedTools: ["PRIVATE_ALLOWED_TOOL"],
    license: "PRIVATE_LICENSE",
    compatibility: "PRIVATE_COMPATIBILITY",
  }
  const privateMcp = {
    serverId: "docs",
    description: "PRIVATE_SERVER_DESCRIPTION",
    importedAt: "2026-10-28T00:00:00Z",
  }
  const cache = new Map<string, Record<string, unknown>>()
  const slot = ({ children }: { children?: unknown }) => children
  const boundaries: Record<string, unknown> = {
    "react-i18next": {
      useTranslation: () => ({
        t: (key: string) =>
          key === "traceLabels.jsonString" ? jsonStringLabel : key,
        i18n: { language: "en-US", resolvedLanguage: "en-US" },
      }),
    },
    "@tanstack/react-query": {
      useQuery: (options: { queryKey: unknown[]; enabled?: boolean }) => {
        queries.push(options)
        const key = options.queryKey.join("/")
        const data = key.includes("skill/configs")
          ? [privateSkill]
          : key.includes("mcp/configs")
            ? [privateMcp]
            : key.includes("mcp/config-tools")
              ? {
                  tools: [
                    {
                      name: "lookup",
                      description: "PRIVATE_TOOL_DESCRIPTION",
                      inputSchema: {
                        properties: {
                          PRIVATE_SCHEMA_FIELD: { type: "string" },
                        },
                      },
                    },
                  ],
                }
              : undefined
        return { data, isLoading: false, isError: false, refetch: () => {} }
      },
    },
    "react-router-dom": {
      useLocation: () => ({ search: "" }),
      Link: ({ to, children }: { to: string; children: unknown }) =>
        createElement("a", { href: to }, children as never),
    },
    "@/contexts/AgentContext": {
      useAgent: () => ({ currentAgent: null }),
    },
    "@/contexts/SpaceContext": {
      useSpace: () => ({ currentSpace: { can_manage: false } }),
    },
    "@/contexts/TimeFilterContext": {
      useTimeFilter: () => ({ timeRange: "1h" }),
    },
    "@/components/labs/decision/DecisionSpanCard": {
      DecisionSpanCard: () => null,
    },
    "@/features/model-pricing/CostSummary": { CostSummary: () => null },
    "@/components/ui/popover": {
      Popover: slot,
      PopoverTrigger: slot,
      PopoverContent: () => null,
    },
    "@/lib/api": {
      fetchWithSession: () => {
        throw new Error("Unexpected network request in trace detail test")
      },
    },
    "./api": {},
    "./skillApi": {},
    "./mcpApi": { getMcpServerDetail: () => null },
    "./SkillDrawer": { SkillDrawer: () => null },
    "./SkillConfigImportDialog": { SkillConfigImportDialog: () => null },
    "./McpServerDrawer": { McpServerDrawer: () => null },
    "./McpConfigImportDialog": { McpConfigImportDialog: () => null },
  }
  function load(path: string): Record<string, unknown> {
    const file = resolve(sourceRoot, path)
    if (cache.has(file)) return cache.get(file)!
    const exports: Record<string, unknown> = {}
    cache.set(file, exports)
    const { outputText } = ts.transpileModule(readFileSync(file, "utf8"), {
      fileName: file,
      compilerOptions: {
        module: ts.ModuleKind.CommonJS,
        target: ts.ScriptTarget.ES2022,
        jsx: ts.JsxEmit.ReactJSX,
        esModuleInterop: true,
      },
    })
    new Function("require", "exports", outputText)((name: string) => {
      if (Object.hasOwn(boundaries, name)) return boundaries[name]
      if (name.startsWith("@/") || name.startsWith(".")) {
        const candidate = name.startsWith("@/")
          ? resolve(sourceRoot, name.slice(2))
          : resolve(dirname(file), name)
        const actual = [candidate, candidate + ".ts", candidate + ".tsx"].find(
          existsSync
        )
        if (!actual) throw Error(`Missing ${name} from ${file}`)
        return load(actual)
      }
      return require(name)
    }, exports)
    return exports
  }
  return {
    Panel: load("components/traces-otel/SpanDetailPanel.tsx")
      .SpanDetailPanel as ComponentType<Record<string, unknown>>,
    queries,
  }
}

async function fixture() {
  const dom = new JSDOM("<div id='root'></div>")
  const originals = new Map<string, PropertyDescriptor | undefined>()
  const pendingTimers = new Set<ReturnType<typeof setTimeout>>()
  const originalSetTimeout = globalThis.setTimeout
  const trackedSetTimeout = ((
    callback: Parameters<typeof setTimeout>[0],
    delay?: number,
    ...args: unknown[]
  ) => {
    const timer = originalSetTimeout(callback, delay, ...args)
    pendingTimers.add(timer)
    return timer
  }) as typeof setTimeout
  let copied = ""
  for (const [key, value] of Object.entries({
    window: dom.window,
    document: dom.window.document,
    navigator: {
      clipboard: {
        writeText: async (value: string) => {
          copied = value
        },
      },
    },
    HTMLElement: dom.window.HTMLElement,
    setTimeout: trackedSetTimeout,
    IS_REACT_ACT_ENVIRONMENT: true,
  })) {
    originals.set(key, Object.getOwnPropertyDescriptor(globalThis, key))
    Object.defineProperty(globalThis, key, { configurable: true, value })
  }
  const { Panel, queries } = components()
  const root = createRoot(dom.window.document.getElementById("root")!)
  return {
    doc: dom.window.document,
    queries,
    copied: () => copied,
    render: async (span: Span, props: Record<string, unknown> = {}) => {
      await act(async () =>
        root.render(
          createElement(Panel, {
            spaceId: "space",
            traceId: span.trace_id,
            spanId: span.span_id,
            spanData: span,
            contentMode: "enhanced",
            labsApi: false,
            ...props,
          })
        )
      )
    },
    close: async () => {
      await act(async () => root.unmount())
      for (const timer of pendingTimers) clearTimeout(timer)
      dom.window.close()
      for (const [key, value] of originals) {
        if (value) Object.defineProperty(globalThis, key, value)
        else Reflect.deleteProperty(globalThis, key)
      }
    },
  }
}

function call(kind: "mcp" | "skill", attrs = {}): Span {
  return {
    trace_id: "trace",
    span_id: `${kind}-call`,
    name: `${kind} native call`,
    attributes: {
      "gen_ai.operation.name": "execute_tool",
      "agentic.tool.type": kind,
      ...(kind === "mcp"
        ? { "agentic.tool.raw_name": "mcp__docs__lookup" }
        : {
            "agentic.skill.name": "version-research",
            "agentic.skill.source": "skills/version-research/SKILL.md",
            "agentic.tool.raw_name": "read_file",
          }),
      "gen_ai.tool.call.arguments": "unique call input",
      "gen_ai.tool.call.result": "unique call output",
      ...attrs,
    },
  }
}

function enhancement(doc: Document) {
  const nodes = doc.querySelectorAll('[data-testid="span-content-enhancement"]')
  assert.equal(
    nodes.length,
    1,
    "one shared IO renderer is embedded in the card"
  )
  return nodes[0]
}

test("MCP identity composes the real shared IO once and keeps original IO reversible", async () => {
  const ui = await fixture()
  try {
    await ui.render(call("mcp"))
    const section = enhancement(ui.doc).closest("section")!
    assert.match(section.textContent!, /labs.mcp.span.title/)
    assert.match(section.textContent!, /docs/)
    assert.match(section.textContent!, /lookup/)
    assert.equal(section.textContent!.split("unique call input").length - 1, 1)
    assert.equal(section.textContent!.split("unique call output").length - 1, 1)
    assert.doesNotMatch(section.textContent!, /spanContent.toolTitle/)
    const original = ui.doc.querySelector(
      '[data-section-title="traceLabels.originalIO"]'
    )!
    assert.equal(
      original.querySelector("button")?.getAttribute("aria-expanded"),
      "false"
    )
    assert.equal(original.querySelector('[data-testid="trace-io-input"]'), null)
    await act(async () =>
      original.querySelector<HTMLButtonElement>("button")!.click()
    )
    assert.match(original.textContent!, /unique call input/)
    assert.match(original.textContent!, /unique call output/)
  } finally {
    await ui.close()
  }
})

test("Skill identity delegates matching IO to the same renderer without repeating input summaries", async () => {
  const ui = await fixture()
  try {
    await ui.render(
      call("skill", {
        "agentic.skill.input": "unique call input",
        "agentic.skill.output": "unique call output",
        "agentic.tool.input_summary": "OBSOLETE_INPUT_SUMMARY",
      })
    )
    const section = enhancement(ui.doc).closest("section")!
    assert.match(section.textContent!, /labs.skill.span.title/)
    assert.match(section.textContent!, /version-research/)
    assert.match(section.textContent!, /read_file/)
    assert.equal(section.textContent!.split("unique call input").length - 1, 1)
    assert.equal(section.textContent!.split("unique call output").length - 1, 1)
    assert.doesNotMatch(section.textContent!, /OBSOLETE_INPUT_SUMMARY/)
    assert.doesNotMatch(section.textContent!, /spanContent.additionalEvidence/)
  } finally {
    await ui.close()
  }
})

test("conflicting Skill evidence stays expandable and exact-copy preserves falsy and whitespace values", async () => {
  const ui = await fixture()
  try {
    for (const value of [false, 0, null, "  verbatim evidence\n\n  "]) {
      const span = call("skill", {
        "agentic.skill.input": value,
        "agentic.skill.output": value,
      })
      await ui.render(span)
      const shared = enhancement(ui.doc)
      assert.match(shared.textContent!, /unique call input/)
      assert.match(shared.textContent!, /unique call output/)
      const details = [...ui.doc.querySelectorAll("details")].find(
        (node) =>
          node.querySelector("summary")?.textContent ===
          "spanContent.additionalEvidence"
      )!
      assert.ok(details, "conflicting source material is available separately")
      assert.equal(details.open, false)
      await act(async () => {
        details.open = true
        details.dispatchEvent(new ui.doc.defaultView!.Event("toggle"))
      })
      const sources = details.querySelectorAll("dd")
      assert.equal(sources.length, 2)
      for (const source of sources) {
        await act(async () =>
          source
            .querySelector<HTMLButtonElement>(
              'button[aria-label="spanContent.copyContent"]'
            )!
            .click()
        )
        assert.equal(
          ui.copied(),
          typeof value === "string" ? value : JSON.stringify(value)
        )
      }
      assert.equal(
        ui.doc.querySelector('[data-testid="span-missing-content"]'),
        null
      )
      details.open = false
    }
  } finally {
    await ui.close()
  }
})

test("public MCP and Skill details ignore private cached configuration even when disabled queries return it", async () => {
  const ui = await fixture()
  try {
    for (const kind of ["mcp", "skill"] as const) {
      await ui.render(call(kind), { labsApi: true })
      assert.match(
        ui.doc.body.textContent!,
        /PRIVATE_/,
        "fixture seeds a readable private configuration"
      )
      ui.queries.length = 0
      await ui.render(call(kind), { labsApi: false })
      assert.doesNotMatch(ui.doc.body.textContent!, /PRIVATE_/)
      assert.doesNotMatch(
        ui.doc.body.textContent!,
        /labs\.(mcp|skill)\.span\.viewConfig/
      )
      const privateQueries = ui.queries.filter(
        (query) => query.queryKey[0] === "labs"
      )
      assert.ok(privateQueries.length > 0)
      assert.equal(
        privateQueries.every((query) => query.enabled === false),
        true
      )
      enhancement(ui.doc)
    }
  } finally {
    await ui.close()
  }
})

test("Original mode preserves competing attributes and exact transport text through an enhanced round trip", async () => {
  const ui = await fixture()
  const input =
    '  {"query":"literal **markdown**","nested":"{\\"flag\\":false}"}\n  '
  const output = ' {"content":"unchanged output","exit_code":0} '
  const span = call("mcp", {
    "gen_ai.tool.call.arguments": input,
    "gen_ai.tool.call.result": output,
    "aivision.request.input": "competing recorded source",
    "custom.falsy": { flag: false, zero: 0, empty: "", nothing: null },
  })
  span.resource = { source: "native", literal: '{"do_not_decode":true}' }
  const before = structuredClone(span)
  try {
    await ui.render(span)
    await ui.render(span, { contentMode: "raw" })
    const enhancedSection = ui.doc
      .querySelector('[data-testid="span-content-enhancement"]')
      ?.closest("section")
    assert.equal(
      enhancedSection === undefined ||
        enhancedSection === null ||
        enhancedSection.style.display === "none",
      true,
      "Original mode hides the preserved enhancement subtree"
    )
    for (const [direction, expected] of [
      ["input", input],
      ["output", output],
    ]) {
      const io = ui.doc.querySelector(`[data-testid="trace-io-${direction}"]`)!
      const content = io.querySelector('[data-testid="trace-io-content"]')!
      assert.ok(content.textContent?.includes(jsonStringLabel))
      assert.doesNotMatch(content.textContent ?? "", /traceLabels\.jsonString/)
      const disclosures = content.querySelectorAll<HTMLButtonElement>(
        "button[aria-expanded]"
      )
      assert.equal(disclosures.length, direction === "input" ? 2 : 1)
      const expectedValue =
        direction === "input"
          ? /"query": "literal \*\*markdown\*\*"/
          : /"content": "unchanged output"/
      assert.match(content.textContent ?? "", expectedValue)
      assert.match(
        content.textContent ?? "",
        direction === "input" ? /"flag": false/ : /"exit_code": 0/
      )
      await act(async () => disclosures[0].click())
      assert.equal(disclosures[0].getAttribute("aria-expanded"), "false")
      assert.doesNotMatch(content.textContent ?? "", expectedValue)
      await act(async () => disclosures[0].click())
      assert.equal(disclosures[0].getAttribute("aria-expanded"), "true")
      assert.match(content.textContent ?? "", expectedValue)
      await act(async () =>
        io
          .querySelector<HTMLButtonElement>(
            ':scope > div:first-child button[title="common.copy"]'
          )!
          .click()
      )
      assert.equal(ui.copied(), expected)
    }
    for (const key of ["traceLabels.attributes", "traceLabels.resource"]) {
      const section = ui.doc.querySelector(`[data-section-title="${key}"]`)!
      await act(async () =>
        section.querySelector<HTMLButtonElement>("button")!.click()
      )
    }
    assert.match(ui.doc.body.textContent!, /aivision.request.input/)
    assert.match(ui.doc.body.textContent!, /competing recorded source/)
    assert.match(ui.doc.body.textContent!, /do_not_decode/)
    assert.equal(
      ui.doc.querySelector('[data-testid="trace-io-input"] strong'),
      null
    )
    assert.deepEqual(
      span,
      before,
      "neither content mode rewrites the original span"
    )
  } finally {
    await ui.close()
  }
})

test("Relay ToolMessage displays its body and explicit outcome while Original mode copies the exact DTO", async () => {
  const ui = await fixture()
  const dto = {
    __nv_pydantic__: "langchain_core.messages.tool.ToolMessage",
    __nv_runtime_type__: "langchain_core.messages.tool.ToolMessage:4366620608",
    data: {
      content: "Saved **synthetic receipt**",
      additional_kwargs: {},
      response_metadata: { elapsed: 0 },
      type: "tool",
      name: "write_receipt",
      id: "message-1",
      tool_call_id: "call-1",
      artifact: { receipt: "synthetic-1" },
      status: "success",
    },
  }
  const output = `  ${JSON.stringify(dto)}\n`
  const span: Span = {
    trace_id: "trace",
    span_id: "relay-tool-message",
    name: "write_receipt",
    attributes: {
      "gen_ai.operation.name": "execute_tool",
      "input.value": '{"contact":"[REDACTED]"}',
      "output.value": output,
    },
  }
  const before = structuredClone(span)
  try {
    await ui.render(span)
    const result = enhancement(ui.doc).querySelector(
      '[data-content-result-outcome="ok"]'
    )!
    assert.ok(result, "the reported ToolMessage success is understood")
    assert.match(result.textContent!, /Saved synthetic receipt/)
    assert.doesNotMatch(
      result.querySelector("dl")!.textContent!,
      /__nv_pydantic__/,
      "the readable body does not start with its DTO transport metadata"
    )
    const details = result.querySelector("details")!
    assert.ok(details, "all remaining DTO metadata is available on demand")
    assert.equal(details.open, false)
    await act(async () => details.querySelector("summary")!.click())
    assert.equal(details.open, true)
    assert.match(details.textContent!, /response_metadata/)
    assert.match(details.textContent!, /synthetic-1/)
    assert.match(details.textContent!, /call-1/)
    assert.match(details.textContent!, /__nv_pydantic__/)

    await ui.render({ ...span, status: { code: "ERROR" } })
    const failed = enhancement(ui.doc).querySelector(
      '[data-content-result-outcome="error"]'
    )!
    assert.ok(failed, "an explicit Span failure wins over DTO success")
    assert.ok(
      [...failed.querySelectorAll("dt")].some(
        (label) => label.textContent === "status"
      ),
      "the conflicting received success remains visible"
    )

    await ui.render(span, { contentMode: "raw" })
    const io = ui.doc.querySelector('[data-testid="trace-io-output"]')!
    assert.match(io.textContent!, /__nv_pydantic__/)
    await act(async () =>
      io
        .querySelector<HTMLButtonElement>(
          ':scope > div:first-child button[title="common.copy"]'
        )!
        .click()
    )
    assert.equal(ui.copied(), output)
    assert.deepEqual(span, before)
  } finally {
    await ui.close()
  }
})

test("structured failures override OK while successful-looking prose without status remains unknown", async () => {
  const ui = await fixture()
  try {
    for (const kind of ["mcp", "skill"] as const) {
      const failed = call(kind, {
        "gen_ai.tool.call.result": JSON.stringify({
          exit_code: 1,
          stdout: "command failed",
        }),
      })
      failed.status = { code: "OK" }
      const before = structuredClone(failed)
      await ui.render(failed)
      const result = enhancement(ui.doc).querySelector(
        '[data-content-result-outcome="error"]'
      )!
      assert.ok(result)
      assert.match(result.textContent!, /spanContent.statusError/)
      assert.doesNotMatch(result.textContent!, /spanContent.statusOk/)
      assert.deepEqual(
        failed,
        before,
        "inferred outcome does not overwrite the raw SDK status"
      )
      const unknown = call(kind, {
        "gen_ai.tool.call.result": "Success: operation completed successfully",
      })
      await ui.render(unknown)
      const neutral = enhancement(ui.doc).querySelector(
        '[data-content-result-outcome="unknown"]'
      )!
      assert.ok(neutral)
      assert.match(neutral.textContent!, /spanContent.statusUnknown/)
      assert.doesNotMatch(
        neutral.textContent!,
        /spanContent.statusOk|labs\.(skill|mcp)\.span\.succeeded/
      )
      assert.equal(unknown.status, undefined)
    }
  } finally {
    await ui.close()
  }
})

test("MCP text blocks expose ordered body excerpts with reversible metadata and progressive records", async () => {
  const ui = await fixture()
  const first = "  **Official evidence**\n\nKeep original spaces.  "
  const json = ' {"matched":false,"count":0,"literal":"{\\"nested\\":true}"} '
  const blocks = [
    { type: "text", text: first, annotations: { audience: ["user"] } },
    {
      type: "image",
      url: "https://example.invalid/private.png",
      mimeType: "image/png",
    },
    { type: "text", text: json, source: { id: "official-v2" } },
    { type: "error", message: "opaque block error", recoverable: false },
    { type: "text", text: "fifth visible block" },
    { type: "text", text: "sixth deferred block" },
    { type: "vendor-custom", payload: { flag: false, zero: 0, nothing: null } },
  ]
  const before = structuredClone(blocks)
  try {
    await ui.render(
      call("mcp", {
        "gen_ai.tool.call.result": { content: blocks },
      })
    )
    const result = enhancement(ui.doc).querySelector(
      "[data-content-result-outcome]"
    )!
    assert.match(result.textContent!, /Official evidence/)
    assert.match(result.textContent!, /fifth visible block/)
    assert.doesNotMatch(result.textContent!, /sixth deferred block/)
    assert.match(result.textContent!, /opaque block error/)
    assert.match(result.textContent!, /private\.png/)
    assert.equal(result.querySelector("img,video,audio"), null)
    const rendered = result.querySelectorAll("[data-tool-result-block]")
    assert.equal(rendered.length, 5)
    assert.match(rendered[0].textContent!, /Official evidence/)
    assert.match(rendered[1].textContent!, /private\.png/)
    assert.match(rendered[2].textContent!, /matched/)
    assert.match(rendered[3].textContent!, /opaque block error/)
    const metadata = rendered[0].querySelector("details")!
    assert.equal(metadata.open, false)
    assert.match(metadata.textContent!, /annotations/)
    for (const [index, expected] of [
      [0, first],
      [2, json],
    ] as const) {
      await act(async () =>
        rendered[index]
          .querySelector<HTMLButtonElement>(
            'button[aria-label="spanContent.copyContent"]'
          )!
          .click()
      )
      assert.equal(ui.copied(), expected)
    }
    const controls = result.querySelector("[data-trace-record-controls]")!
    assert.equal(controls.getAttribute("data-shown"), "5")
    assert.equal(controls.getAttribute("data-total"), "7")
    await act(async () =>
      [...controls.querySelectorAll<HTMLButtonElement>("button")]
        .find((button) => button.textContent === "traceRecords.all")!
        .click()
    )
    assert.equal(result.querySelectorAll("[data-tool-result-block]").length, 7)
    assert.match(result.textContent!, /sixth deferred block/)
    assert.match(result.textContent!, /vendor-custom/)
    await act(async () =>
      [...result.querySelectorAll<HTMLButtonElement>("button")]
        .find((button) => button.textContent === "traceRecords.collapse")!
        .click()
    )
    assert.equal(result.querySelectorAll("[data-tool-result-block]").length, 5)
    assert.deepEqual(blocks, before)
  } finally {
    await ui.close()
  }
})

test("Skill text result body is readable before opening its complete metadata", async () => {
  const ui = await fixture()
  const text = "  # Research skill\n\nUse official versioned evidence.\n  "
  const output = {
    text,
    status: "provider-defined",
    metadata: { zero: 0, flag: false, empty: "", nothing: null },
  }
  const before = structuredClone(output)
  try {
    await ui.render(call("skill", { "gen_ai.tool.call.result": output }))
    const result = enhancement(ui.doc).querySelector(
      "[data-content-result-outcome]"
    )!
    assert.equal(result.querySelector("h1")?.textContent, "Research skill")
    assert.equal(result.querySelectorAll("[data-tool-result-text]").length, 1)
    const metadata = result.querySelector("details")!
    assert.equal(metadata.open, false)
    assert.match(metadata.textContent!, /provider-defined/)
    assert.match(metadata.textContent!, /zero/)
    assert.match(metadata.textContent!, /false/)
    assert.match(metadata.textContent!, /null/)
    assert.doesNotMatch(
      metadata.textContent!,
      /Use official versioned evidence/
    )
    await act(async () =>
      result
        .querySelector<HTMLButtonElement>(
          '[data-tool-result-text] button[aria-label="spanContent.copyContent"]'
        )!
        .click()
    )
    assert.equal(ui.copied(), text)
    assert.deepEqual(output, before)
  } finally {
    await ui.close()
  }
})

test("compact parameters keep short query, path and zero copyable while long commands retain accessible wrap controls", async () => {
  const ui = await fixture()
  try {
    const args = {
      query: "short query",
      file_path: "/tmp/config.json",
      offset: 0,
    }
    await ui.render(
      call("mcp", {
        "gen_ai.tool.call.arguments": JSON.stringify(args),
      })
    )
    for (const [key, value] of Object.entries(args)) {
      const label = [...enhancement(ui.doc).querySelectorAll("dt")].find(
        (node) => node.textContent === key
      )!
      assert.ok(label, `recorded ${key} remains visible`)
      const field = label.parentElement!
      const buttons = field.querySelectorAll<HTMLButtonElement>("button")
      assert.equal(buttons.length, 1, `${key} needs only its exact-copy action`)
      assert.equal(
        buttons[0].getAttribute("aria-label"),
        "spanContent.copyContent"
      )
      await act(async () => buttons[0].click())
      assert.equal(ui.copied(), String(value))
    }
    const rawArgs = "{'source_ids': ['pydantic-v2'], 'target_major': 2}"
    const generic = call("mcp", {
      "gen_ai.tool.call.arguments": rawArgs,
    })
    delete generic.attributes["agentic.tool.type"]
    delete generic.attributes["agentic.tool.raw_name"]
    await ui.render(generic)
    const rawInput = enhancement(ui.doc).querySelector("section")!
    assert.equal(rawInput.querySelectorAll("button").length, 1)
    await act(async () =>
      rawInput.querySelector<HTMLButtonElement>("button")!.click()
    )
    assert.equal(ui.copied(), rawArgs)
    const command = Array.from(
      { length: 8 },
      (_, index) =>
        `printf 'line ${index}: retain all command syntax and whitespace exactly  '\n`
    ).join("")
    assert.ok(command.length > 360)
    const longCall = call("mcp", {
      "gen_ai.tool.call.arguments": JSON.stringify({ command }),
    })
    longCall.span_id = "long-command-call"
    await ui.render(longCall)
    const field = [...enhancement(ui.doc).querySelectorAll("dt")].find(
      (node) => node.textContent === "command"
    )!.parentElement!
    const wrap = field.querySelector<HTMLButtonElement>(
      'button[aria-label="spanContent.noWrapText"]'
    )!
    assert.ok(wrap)
    assert.equal(wrap.title, "spanContent.noWrapText")
    assert.equal(wrap.textContent, "", "wrap is an accessible icon action")
    assert.equal(wrap.getAttribute("aria-pressed"), "true")
    await act(async () => wrap.click())
    const restoreWrap = field.querySelector<HTMLButtonElement>(
      'button[aria-label="spanContent.wrapText"]'
    )!
    assert.ok(restoreWrap)
    assert.equal(restoreWrap.title, "spanContent.wrapText")
    assert.equal(restoreWrap.getAttribute("aria-pressed"), "false")
    await act(async () =>
      field
        .querySelector<HTMLButtonElement>(
          'button[aria-label="spanContent.copyContent"]'
        )!
        .click()
    )
    assert.equal(ui.copied(), command)
    await act(async () => restoreWrap.click())
    assert.equal(
      field
        .querySelector('button[aria-label="spanContent.noWrapText"]')
        ?.getAttribute("aria-pressed"),
      "true"
    )
  } finally {
    await ui.close()
  }
})

test("Skill frontmatter remains document metadata with exact copy and only identical source paths deduplicate", async () => {
  const ui = await fixture()
  const path = "/skills/version-research/SKILL.md"
  const text =
    "---\nname: version-research\ndescription: Versioned evidence\n---\n\n# Research skill\n\nUse official documents.  \n\n  "
  const output = { text, meta: { flag: false, zero: 0, nothing: null } }
  try {
    for (const source of [path, ` ${path} `]) {
      const span = call("skill", {
        "gen_ai.tool.call.arguments": JSON.stringify({
          file_path: path,
          source,
        }),
        "gen_ai.tool.call.result": output,
      })
      const before = structuredClone(span)
      await ui.render(span)
      const shared = enhancement(ui.doc)
      const card = shared.closest("section")!
      const result = shared.querySelector("[data-content-result-outcome]")!
      assert.equal(result.querySelector("h1")?.textContent, "Research skill")
      assert.equal(
        result.querySelector("h2"),
        null,
        "frontmatter is not a Markdown heading"
      )
      const documentMetadata = [...result.querySelectorAll("details")].find(
        (node) =>
          node.querySelector("summary")?.textContent ===
          "spanContent.documentMetadata"
      )!
      assert.ok(documentMetadata)
      assert.equal(documentMetadata.open, false)
      assert.equal(
        documentMetadata.querySelector("pre")?.textContent,
        "name: version-research\ndescription: Versioned evidence"
      )
      const toolMetadata = [...result.querySelectorAll("details")].find(
        (node) =>
          node.querySelector("summary")?.textContent ===
          "spanContent.otherResults"
      )!
      assert.ok(toolMetadata)
      assert.equal(toolMetadata.open, false)
      assert.match(toolMetadata.textContent!, /false/)
      assert.match(toolMetadata.textContent!, /null/)
      await act(async () =>
        result
          .querySelector<HTMLButtonElement>(
            '[data-tool-result-text] button[aria-label="spanContent.copyContent"]'
          )!
          .click()
      )
      assert.equal(
        ui.copied(),
        text,
        "copy retains the full frontmatter and trailing whitespace"
      )
      const sourceLabel = [...card.querySelectorAll("dt")].find(
        (node) => node.textContent === "labs.skill.span.source"
      )
      if (source === path) {
        assert.equal(
          sourceLabel,
          undefined,
          "identical file_path already identifies the source"
        )
      } else {
        assert.ok(
          sourceLabel,
          "whitespace-different source is not treated as identical"
        )
        assert.equal(sourceLabel.nextElementSibling?.textContent, source)
      }
      const filePath = [...shared.querySelectorAll("dt")].find(
        (node) => node.textContent === "file_path"
      )!
      assert.ok(filePath)
      await act(async () =>
        filePath
          .parentElement!.querySelector<HTMLButtonElement>(
            'button[aria-label="spanContent.copyContent"]'
          )!
          .click()
      )
      assert.equal(ui.copied(), path)
      assert.deepEqual(span, before)
    }
  } finally {
    await ui.close()
  }
})

test("a compact long logical line keeps its wrap action after measured height drops to one line", async () => {
  const ui = await fixture()
  const command = `printf '%s' '${"literal command argument ".repeat(24)}  '`
  let measuredHeight = 144
  Object.defineProperty(
    ui.doc.defaultView!.HTMLElement.prototype,
    "scrollHeight",
    {
      configurable: true,
      get() {
        return this.hasAttribute("data-content-body") &&
          this.querySelector("pre")?.textContent === command
          ? measuredHeight
          : 0
      },
    }
  )
  try {
    assert.equal(command.includes("\n"), false)
    assert.ok(command.length > 360)
    await ui.render(
      call("mcp", {
        "gen_ai.tool.call.arguments": JSON.stringify({ command }),
      })
    )
    const field = [...enhancement(ui.doc).querySelectorAll("dt")].find(
      (node) => node.textContent === "command"
    )!.parentElement!
    const noWrap = field.querySelector<HTMLButtonElement>(
      'button[aria-label="spanContent.noWrapText"]'
    )!
    assert.ok(noWrap, "wrapped layout initially exceeds the four-line preview")
    measuredHeight = 24
    await act(async () => noWrap.click())
    const wrap = field.querySelector<HTMLButtonElement>(
      'button[aria-label="spanContent.wrapText"]'
    )
    assert.ok(
      wrap,
      "the action remains available when unwrapped height becomes one line"
    )
    assert.equal(wrap.title, "spanContent.wrapText")
    assert.equal(wrap.textContent, "")
    assert.equal(wrap.getAttribute("aria-pressed"), "false")
    await act(async () =>
      field
        .querySelector<HTMLButtonElement>(
          'button[aria-label="spanContent.copyContent"]'
        )!
        .click()
    )
    assert.equal(ui.copied(), command)
    measuredHeight = 144
    await act(async () => wrap.click())
    assert.equal(
      field
        .querySelector('button[aria-label="spanContent.noWrapText"]')
        ?.getAttribute("aria-pressed"),
      "true"
    )
  } finally {
    await ui.close()
  }
})

test("a span with MCP and Skill identities renders both identities and only one shared IO owner", async () => {
  const ui = await fixture()
  const span = call("mcp", {
    "agentic.skill.name": "version-research",
    "agentic.skill.provider": "native-skill-provider",
    "agentic.skill.input": "distinct skill evidence",
  })
  const before = structuredClone(span)
  try {
    await ui.render(span)
    const cards = [...ui.doc.querySelectorAll("section")].filter((section) =>
      [...section.querySelectorAll("h3")].some((heading) =>
        ["labs.mcp.span.title", "labs.skill.span.title"].includes(
          heading.textContent ?? ""
        )
      )
    )
    assert.equal(cards.length, 2, "both observed identities remain available")
    const mcp = cards.find(
      (card) => card.querySelector("h3")?.textContent === "labs.mcp.span.title"
    )!
    const skill = cards.find(
      (card) =>
        card.querySelector("h3")?.textContent === "labs.skill.span.title"
    )!
    assert.match(mcp.textContent!, /docs/)
    assert.match(mcp.textContent!, /lookup/)
    assert.match(skill.textContent!, /version-research/)
    assert.match(skill.textContent!, /native-skill-provider/)
    const shared = enhancement(ui.doc)
    assert.equal(shared.closest("section"), mcp)
    assert.match(shared.textContent!, /unique call input/)
    assert.match(shared.textContent!, /unique call output/)
    assert.match(mcp.textContent!, /distinct skill evidence/)
    assert.doesNotMatch(
      skill.textContent!,
      /unique call input|unique call output|distinct skill evidence/
    )
    assert.equal(
      skill.querySelector("[data-content-excerpt], [data-trace-text-content]"),
      null,
      "the second identity card does not fall back to a second IO renderer"
    )
    assert.deepEqual(span, before)
  } finally {
    await ui.close()
  }
})

test("the parent gives explicit span errors precedence over successful output without changing raw telemetry", async () => {
  const ui = await fixture()
  try {
    for (const kind of ["mcp", "skill", "generic"] as const) {
      const span = call(kind === "generic" ? "mcp" : kind, {
        "gen_ai.tool.call.result": { exit_code: 0, status: "success" },
      })
      if (kind === "generic") {
        delete span.attributes["agentic.tool.type"]
        delete span.attributes["agentic.tool.raw_name"]
      }
      span.status = { code: "ERROR", message: "reported native failure" }
      const before = structuredClone(span)
      await ui.render(span)
      const result = enhancement(ui.doc).querySelector(
        "[data-content-result-outcome]"
      )!
      assert.equal(
        result.getAttribute("data-content-result-outcome"),
        "error",
        kind
      )
      assert.match(result.textContent!, /spanContent.statusError/)
      assert.doesNotMatch(result.textContent!, /spanContent.statusOk/)
      assert.deepEqual(span, before)
    }
  } finally {
    await ui.close()
  }
})

test("recognized native cards use explicit OK fallback while generic output-only outcomes remain unknown", async () => {
  const ui = await fixture()
  try {
    for (const kind of ["mcp", "skill", "generic"] as const) {
      const span = call(kind === "generic" ? "mcp" : kind, {
        "gen_ai.tool.call.result": "ordinary recorded output",
      })
      if (kind === "generic") {
        delete span.attributes["agentic.tool.type"]
        delete span.attributes["agentic.tool.raw_name"]
      }
      span.status = { code: "OK" }
      const before = structuredClone(span)
      await ui.render(span)
      const result = enhancement(ui.doc).querySelector(
        "[data-content-result-outcome]"
      )!
      const expected = kind === "generic" ? "unknown" : "ok"
      assert.equal(
        result.getAttribute("data-content-result-outcome"),
        expected,
        kind
      )
      assert.match(
        result.textContent!,
        kind === "generic"
          ? /spanContent.statusUnknown/
          : /spanContent.statusOk/
      )
      assert.deepEqual(span, before)
    }
  } finally {
    await ui.close()
  }
})

test("Skill argument aliases remain recorded input with exact-copy values when shared results own the card", async () => {
  const ui = await fixture()
  const values = [
    {
      value: '  {"skill":"version-research","invocation":"cli"}\n  ',
      copy: '  {"skill":"version-research","invocation":"cli"}\n  ',
    },
    { value: false, copy: "false" },
    { value: 0, copy: "0" },
    { value: null, copy: "null" },
    { value: "", copy: "" },
  ]
  try {
    for (const key of [
      "agentic.tool.call.arguments",
      "gen_ai.tool.call.input",
    ]) {
      for (const { value, copy } of values) {
        const span = call("skill", { [key]: value })
        delete span.attributes["gen_ai.tool.call.arguments"]
        const before = structuredClone(span)
        await ui.render(span)
        const input = [...enhancement(ui.doc).querySelectorAll("section")].find(
          (section) =>
            section.querySelector("h4")?.textContent === "spanContent.arguments"
        )
        assert.ok(
          input,
          `${key} is recorded input even when its value is ${copy}`
        )
        assert.equal(
          ui.doc.querySelector('[data-testid="span-missing-content"]'),
          null
        )
        await act(async () =>
          input
            .querySelector<HTMLButtonElement>(
              'button[aria-label="spanContent.copyContent"]'
            )!
            .click()
        )
        assert.equal(ui.copied(), copy)
        assert.deepEqual(span, before)
      }
    }
  } finally {
    await ui.close()
  }
})

test("Skill argument fallbacks retain standard IO and explicit Skill input priority", async () => {
  const ui = await fixture()
  try {
    for (const { attrs, copy } of [
      { attrs: {}, copy: "first alias" },
      { attrs: { "agentic.skill.input": false }, copy: "false" },
      {
        attrs: {
          "gen_ai.tool.call.arguments": "standard input",
          "agentic.skill.input": false,
        },
        copy: "standard input",
      },
    ]) {
      const span = call("skill", {
        "agentic.tool.call.arguments": "first alias",
        "gen_ai.tool.call.input": "second alias",
      })
      delete span.attributes["gen_ai.tool.call.arguments"]
      Object.assign(span.attributes, attrs)
      await ui.render(span)
      const input = [...enhancement(ui.doc).querySelectorAll("section")].find(
        (section) =>
          section.querySelector("h4")?.textContent === "spanContent.arguments"
      )!
      assert.ok(input)
      await act(async () =>
        input
          .querySelector<HTMLButtonElement>(
            'button[aria-label="spanContent.copyContent"]'
          )!
          .click()
      )
      assert.equal(ui.copied(), copy)
    }
  } finally {
    await ui.close()
  }
})

test("native cards retain reported status when output is absent from input-only and empty calls", async () => {
  const ui = await fixture()
  try {
    for (const kind of ["mcp", "skill"] as const) {
      for (const withInput of [true, false]) {
        for (const code of ["OK", "UNSET"]) {
          const span = call(kind)
          delete span.attributes["gen_ai.tool.call.result"]
          if (!withInput) delete span.attributes["gen_ai.tool.call.arguments"]
          span.status = { code }
          await ui.render(span)
          const card = [...ui.doc.querySelectorAll("section")].find(
            (section) =>
              section.querySelector("h3")?.textContent ===
              `labs.${kind}.span.title`
          )!
          assert.ok(card)
          assert.match(
            card.textContent!,
            new RegExp(
              code === "OK"
                ? `labs\\.${kind}\\.span\\.succeeded`
                : "spanContent\\.statusUnknown"
            )
          )
          assert.doesNotMatch(
            card.textContent!,
            new RegExp(
              code === "OK"
                ? "spanContent\\.statusUnknown"
                : `labs\\.${kind}\\.span\\.succeeded`
            )
          )
        }
      }
    }
  } finally {
    await ui.close()
  }
})

test("Memory retains enhanced formatted IO and exact copying, including MCP isError failures", async () => {
  const ui = await fixture()
  try {
    const output = JSON.stringify({
      isError: true,
      content: [{ type: "text", text: "memory write rejected" }],
    })
    await ui.render(
      call("mcp", {
        "agentic.tool.raw_name": "mcp__powercontext__remember_memory",
        "gen_ai.tool.call.result": output,
      })
    )
    const section = enhancement(ui.doc).closest("section")!
    assert.match(section.textContent!, /labs.memory.span.title/)
    assert.doesNotMatch(section.textContent!, /labs.memory.span.succeeded/)
    assert.match(section.textContent!, /unique call input/)
    assert.match(section.textContent!, /memory write rejected/)
    const copyResult = section.querySelector<HTMLButtonElement>(
      '[data-tool-result-text] button[aria-label="spanContent.copyContent"]'
    )!
    assert.ok(copyResult)
    await act(async () => copyResult.click())
    assert.equal(ui.copied(), "memory write rejected")
    const original = ui.doc.querySelector(
      '[data-section-title="traceLabels.originalIO"]'
    )!
    await act(async () =>
      original.querySelector<HTMLButtonElement>("button")!.click()
    )
    assert.match(original.textContent!, /isError/)
    assert.match(original.textContent!, /memory write rejected/)
  } finally {
    await ui.close()
  }
})

test("Memory separates returned entries from call outcome and copies exact source values", async () => {
  const ui = await fixture()
  try {
    const content =
      "  A saved review constraint\n\nwith an exact second paragraph  "
    const output = JSON.stringify({
      content: [
        {
          type: "text",
          text: JSON.stringify({
            hits: [
              { memory: { text: content, state: "active" }, score: 0 },
              {
                memory: { text: "An archived constraint", state: "archived" },
                score: 0.8,
              },
            ],
          }),
        },
      ],
    })
    await ui.render(
      call("mcp", {
        "agentic.tool.raw_name": "mcp__powercontext__search_memory",
        "gen_ai.tool.call.result": output,
      })
    )
    const result = ui.doc.querySelector("[data-memory-result]")!
    assert.ok(result)
    assert.equal(result.getAttribute("data-content-result-outcome"), "unknown")
    const entries = result.querySelectorAll("[data-memory-result-entries] > li")
    assert.equal(entries.length, 2)
    assert.equal(entries[0].querySelector("pre")!.textContent, content)
    assert.match(entries[0].textContent!, /labs.memory.result.score: 0/)
    assert.match(entries[1].textContent!, /labs.memory.result.states.archived/)
    await act(async () =>
      entries[0]
        .querySelector<HTMLButtonElement>(
          'button[aria-label="common.copy: labs.memory.activity.content"]'
        )!
        .click()
    )
    assert.equal(ui.copied(), content)
    await act(async () =>
      result
        .querySelector<HTMLButtonElement>(
          'button[aria-label="labs.memory.result.copyOriginal"]'
        )!
        .click()
    )
    assert.equal(ui.copied(), output)
  } finally {
    await ui.close()
  }
})
