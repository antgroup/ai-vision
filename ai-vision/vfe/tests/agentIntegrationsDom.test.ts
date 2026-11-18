import assert from "node:assert/strict"
import { existsSync, readFileSync } from "node:fs"
import { createRequire } from "node:module"
import path from "node:path"
import { fileURLToPath } from "node:url"
import test from "node:test"
import { JSDOM } from "jsdom"
import { createInstance } from "i18next"
import { createElement, type ReactNode } from "react"
import { renderToStaticMarkup } from "react-dom/server"
import ts from "typescript"

const require = createRequire(import.meta.url)
const sourceRoot = fileURLToPath(new URL("../src", import.meta.url))
const translations = {
  "langfuseOnboarding.fleetGroupTitle": "Self-built agents",
  "langfuseOnboarding.marketGroupTitle": "Self-built agents",
  "langfuseOnboarding.marketCardTitle": "Langfuse SDK",
  "langfuseOnboarding.relayCardTitle": "NVIDIA NeMo Relay",
  "agentAccess.thirdPartyGroup": "Agent applications",
  "agentAccess.connected": "Connected",
  "agentAccess.connectedVia": "Channel: {{channel}}",
  "langfuseOnboarding.frameworkNotConnected": "Awaiting trace",
  "integrationKinds.observability_sdk": "Observability SDK",
  "integrationKinds.partner_runtime": "Partner runtime",
}

const futureSdk = {
  agent_name: "future-observer",
  agent_product: "future-observer",
  category: "CUSTOM",
  display_name: "Future Observer",
  description: "Connect supported frameworks",
  icon: null,
  capabilities: { supports_trace: true },
  config_guide: {
    integration: {
      kind: "observability_sdk",
      onboardingPath: "/access/future-observer",
      docsUrl: "https://observer.example/docs",
      verifiedVersions: [],
    },
  },
}

function renderPage(
  tab: string,
  agentTypes: unknown[],
  agents: unknown[],
  locale?: string
) {
  const i18n = createInstance()
  if (locale) {
    void i18n.init({
      lng: locale,
      resources: Object.fromEntries(
        ["zh-CN", "en-US", "ja-JP"].map((language) => [
          language,
          {
            translation: JSON.parse(
              readFileSync(
                path.join(sourceRoot, "i18n/locales", `${language}.json`),
                "utf8"
              )
            ),
          },
        ])
      ),
    })
  }
  const dependencies: Record<string, unknown> = {
    "react-i18next": {
      useTranslation: () => ({
        t: locale
          ? i18n.t.bind(i18n)
          : (key: string, options?: { channel?: string }) =>
              (translations[key as keyof typeof translations] || key).replace(
                "{{channel}}",
                options?.channel ?? ""
              ),
      }),
    },
    "react-router-dom": {
      useNavigate: () => () => {},
      useSearchParams: () => [new URLSearchParams({ tab }), () => {}],
    },
    "@tanstack/react-query": {
      useQueryClient: () => ({}),
      useMutation: () => ({}),
    },
    "@/contexts/AgentContext": {
      useAgent: () => ({
        agents,
        agentTypes,
        loading: false,
        disabledAgents: [],
        refreshAgents: () => {},
      }),
    },
    "@/contexts/SpaceContext": {
      useSpace: () => ({ currentSpace: { id: "space-1", permissions: [] } }),
    },
    "@/contexts/UserContext": {},
    "@/lib/api": {},
    "@/components/error/Hint": {},
    "@/components/error/SpaceAgentGuard": {
      SpaceGuard: ({ children }: { children: ReactNode }) => children,
    },
    "@/components/agents/guidance": {},
    "@/components/agents/guidance/OnboardingGuideSections": {},
    "@/components/agents/celebration": {},
  }
  const cache = new Map<string, Record<string, unknown>>()
  function loadSource(filename: string): Record<string, unknown> {
    const fullPath = [filename, `${filename}.ts`, `${filename}.tsx`].find(
      existsSync
    )
    assert.ok(fullPath, `Module exists: ${filename}`)
    if (cache.has(fullPath)) return cache.get(fullPath)!
    const output = ts.transpileModule(readFileSync(fullPath, "utf8"), {
      compilerOptions: {
        module: ts.ModuleKind.CommonJS,
        jsx: ts.JsxEmit.ReactJSX,
      },
    }).outputText
    const exports: Record<string, unknown> = {}
    cache.set(fullPath, exports)
    new Function("require", "exports", output)((name: string) => {
      if (name in dependencies) return dependencies[name]
      if (name.startsWith("@/"))
        return loadSource(path.join(sourceRoot, name.slice(2)))
      if (name.startsWith("."))
        return loadSource(path.resolve(path.dirname(fullPath), name))
      return require(name)
    }, exports)
    return exports
  }
  const { default: AgentsPage } = loadSource(
    path.join(sourceRoot, "pages/AgentsPage.tsx")
  )
  return new JSDOM(
    renderToStaticMarkup(createElement(AgentsPage as () => ReactNode))
  )
}

test("a future SDK catalog entry renders a market card without a product-wide connection badge", () => {
  const page = renderPage(
    "market",
    [futureSdk],
    [
      {
        id: 1,
        uuid: "project-1",
        agent_product: "future-observer",
        name: "Finance Assistant",
        status: 1,
      },
    ]
  )
  try {
    const card = page.window.document.querySelector(
      '[data-testid="future-observer-integration-card"]'
    )
    assert.ok(card)
    assert.match(card.textContent || "", /Future Observer/)
    assert.match(card.textContent || "", /Observability SDK/)
    assert.doesNotMatch(card.textContent || "", /Connected/)
  } finally {
    page.window.close()
  }
})

test("a source-installed Hermes plugin remains visible in the application market", () => {
  const page = renderPage(
    "market",
    [
      {
        agent_name: "Hermes",
        agent_product: "Hermes",
        display_name: "Hermes Agent",
        category: "CUSTOM",
        setup_mode: "OTHER",
        description: "Install the Hermes source plugin",
        config_guide: {
          schema: "v2",
          sections: [
            {
              type: "verification",
              title: "Verify",
              steps: ["Run a new Hermes task"],
            },
          ],
        },
      },
    ],
    []
  )
  try {
    const headings = [...page.window.document.querySelectorAll("h3")].map(
      (node) => node.textContent
    )
    assert.deepEqual(headings, ["Agent applications", "Hermes Agent"])
    assert.equal(
      [...page.window.document.querySelectorAll("button")].filter(
        (button) => button.textContent === "agentAccess.viewGuide"
      ).length,
      1
    )
  } finally {
    page.window.close()
  }
})

test("SDK and Relay fleet cards use business project names and remain in the self-built group", () => {
  const page = renderPage(
    "fleet",
    [
      futureSdk,
      {
        ...futureSdk,
        agent_name: "nvidia-nemo-relay",
        agent_product: "nvidia-nemo-relay",
        display_name: "NVIDIA NeMo Relay",
        config_guide: {
          frameworks: [
            { id: "deepagents", label: "Deep Agents", logo: "/deepagents.svg" },
          ],
        },
      },
    ],
    [
      {
        id: 1,
        uuid: "project-1",
        agent_product: "future-observer",
        name: "Finance Assistant",
        status: 2,
        metadata: { framework: "LangGraph" },
      },
      {
        id: 2,
        uuid: "project-2",
        agent_product: "nvidia-nemo-relay",
        name: "Support Assistant",
        status: 1,
        icon: "/assets/agents/relay.svg",
        metadata: { framework: "deepagents", logo: "/support-assistant.svg" },
      },
    ]
  )
  try {
    const headings = [...page.window.document.querySelectorAll("h3")].map(
      (node) => node.textContent
    )
    assert.deepEqual(headings, [
      "Self-built agents",
      "Finance Assistant",
      "Support Assistant",
    ])
    const text = page.window.document.body.textContent || ""
    assert.match(text, /Channel: Future Observer/)
    assert.match(text, /Channel: NVIDIA NeMo Relay/)
    assert.deepEqual(
      [...page.window.document.querySelectorAll("img")].map((image) =>
        image.getAttribute("src")
      ),
      ["/support-assistant.svg", "/assets/agents/relay.svg"]
    )
    assert.match(text, /Deep Agents/)
    assert.match(text, /Awaiting trace/)
    assert.match(text, /Connected/)
    assert.doesNotMatch(text, /Agent applications/)
  } finally {
    page.window.close()
  }
})

for (const locale of ["en-US", "ja-JP"] as const) {
  test(`${locale} localizes the seeded description of manual v2 plugins and preserves custom descriptions`, () => {
    const resource = (language: string) =>
      JSON.parse(
        readFileSync(
          path.join(sourceRoot, "i18n/locales", `${language}.json`),
          "utf8"
        )
      )
    const seededDescription = resource("zh-CN").agentAccess.pluginDescription
    const customDescription = "Custom partner setup instructions"
    const agentType = (product: string, description: string) => ({
      agent_name: product,
      agent_product: product,
      display_name: product,
      category: "CUSTOM",
      setup_mode: "OTHER",
      description,
      config_guide: {
        schema: "v2",
        sections: [
          { type: "verification", title: "Verify", steps: ["Run a task"] },
        ],
      },
    })
    const page = renderPage(
      "market",
      [
        agentType("Hermes", seededDescription),
        agentType("Custom Plugin", customDescription),
        {
          ...agentType("Legacy Plugin", seededDescription),
          config_guide: { plugin_key: "legacy-plugin" },
        },
      ],
      [],
      locale
    )
    try {
      const cardText = (name: string) =>
        [...page.window.document.querySelectorAll("h3")].find(
          (node) => node.textContent === name
        )!.parentElement!.textContent || ""
      assert.ok(
        cardText("Hermes").includes(
          resource(locale).agentAccess.pluginDescription
        )
      )
      assert.ok(!cardText("Hermes").includes(seededDescription))
      assert.ok(cardText("Custom Plugin").includes(customDescription))
      assert.ok(cardText("Legacy Plugin").includes(seededDescription))
    } finally {
      page.window.close()
    }
  })
}

test("market framework marks show the Eino logo and exclude unillustrated runtime entries", () => {
  const page = renderPage(
    "market",
    [
      {
        agent_product: "langfuse",
        agent_name: "langfuse",
        category: "CUSTOM",
        config_guide: {
          frameworks: [
            {
              id: "eino",
              label: "Eino",
              marketLabel: "Go / Eino",
              logo: "/assets/frameworks/eino.png",
              logoLayout: "wide",
              marketPreview: true,
            },
            { id: "nodejs", label: "Node.js SDK", marketPreview: true },
          ],
        },
      },
    ],
    [],
    "zh-CN"
  )
  try {
    const card = page.window.document.querySelector(
      '[data-testid="langfuse-compatible-market-card"]'
    )!
    const mark = card.querySelector('[aria-label="Go / Eino"]')!
    assert.equal(
      mark.querySelector("img")?.getAttribute("src"),
      "/assets/frameworks/eino.png"
    )
    assert.equal(mark.textContent, "")
    assert.equal(card.querySelector('[aria-label="Node.js SDK"]'), null)
    assert.doesNotMatch(card.textContent || "", /Node\.js SDK/)
    assert.match(card.textContent || "", /Go（Eino）/)
    assert.ok(
      card.querySelector(
        '[data-testid="langfuse-compatible-market-card-guide"]'
      )
    )
  } finally {
    page.window.close()
  }
})
