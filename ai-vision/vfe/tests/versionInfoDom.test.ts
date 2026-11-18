import assert from "node:assert/strict"
import { existsSync, readFileSync } from "node:fs"
import { createRequire } from "node:module"
import { dirname, resolve } from "node:path"
import { fileURLToPath } from "node:url"
import test from "node:test"
import { JSDOM } from "jsdom"
import { act, createElement, type ComponentType } from "react"
import { createRoot } from "react-dom/client"
import { createInstance } from "i18next"
import ts from "typescript"

const require = createRequire(import.meta.url)
// Use the same package entrypoints as the transpiled source so context
// providers are shared (react-i18next exposes separate ESM/CJS modules).
const { I18nextProvider } =
  require("react-i18next") as typeof import("react-i18next")
const { MemoryRouter } =
  require("react-router-dom") as typeof import("react-router-dom")
const sourceRoot = fileURLToPath(new URL("../src/", import.meta.url))
const sha = "a".repeat(40)
const frontend = {
  schema_version: 1,
  component: "aivision-fe",
  product_version: "0.0.1",
  component_version: "0.0.1",
  source_sha: sha,
  build_id: "frontend-build-17",
  built_at: "2026-10-16T08:00:00Z",
  target_channel: "stable",
  dirty: false,
}
const backend = {
  ...frontend,
  component: "aivision-be",
  build_id: "backend-build-17",
}

function installedRelease(overrides: Record<string, unknown> = {}) {
  return {
    product_version: "0.0.1",
    channel: "stable",
    source_sha: "c".repeat(40),
    build_id: "release-build-18",
    components: { "aivision-fe": frontend, "aivision-be": backend },
    ...overrides,
  }
}

function response(
  info: unknown = backend,
  observedAt = "2026-10-16T09:00:00Z",
  expected: unknown = installedRelease()
) {
  return new Response(
    JSON.stringify({
      code: 0,
      message: "success",
      data: {
        backend: info,
        scope: "responding_instance",
        observed_at: observedAt,
        expected,
        private_url: "https://private.example/secret",
      },
    }),
    { headers: { "content-type": "application/json" } }
  )
}

const stableRelease = {
  version: "0.0.1",
  tag_name: "v0.0.1",
  name: "AI Vision 0.0.1",
  body: "## Improvements\n\n- Faster trace search",
  url: "https://github.com/antgroup/ai-vision/releases/tag/v0.0.1",
  published_at: "2026-10-16T09:00:00Z",
}

function releaseResponse(
  latest: Record<string, unknown> | null = stableRelease,
  status = "available"
) {
  return new Response(
    JSON.stringify({
      code: 0,
      message: "success",
      data: { status, latest, checked_at: "2026-10-16T10:00:00Z" },
    }),
    { headers: { "content-type": "application/json" } }
  )
}

function newerRelease(body = "## Improvements\n\n- Faster trace search") {
  return releaseResponse({
    version: "0.1.0",
    tag_name: "v0.1.0",
    name: "AI Vision 0.1.0",
    body,
    url: "https://github.com/antgroup/ai-vision/releases/tag/v0.1.0",
    published_at: "2026-10-31T09:00:00Z",
  })
}

// Load the real page, its helpers and design-system components. Only browser
// boundaries (fetch/clipboard) and the compile-time build constant are supplied.
function load(
  file: string,
  build: unknown,
  cache = new Map<string, unknown>()
) {
  const path = resolve(sourceRoot, file)
  if (cache.has(path)) return cache.get(path) as Record<string, unknown>
  const exports: Record<string, unknown> = {}
  cache.set(path, exports)
  const code = ts.transpileModule(readFileSync(path, "utf8"), {
    fileName: path,
    compilerOptions: {
      module: ts.ModuleKind.CommonJS,
      target: ts.ScriptTarget.ES2022,
      jsx: ts.JsxEmit.ReactJSX,
    },
  }).outputText
  new Function("require", "exports", "__AIVISION_BUILD_INFO__", code)(
    (name: string) => {
      if (name.startsWith("@/") || name.startsWith(".")) {
        const candidate = name.startsWith("@/")
          ? resolve(sourceRoot, name.slice(2))
          : resolve(dirname(path), name)
        const actual = [candidate, candidate + ".ts", candidate + ".tsx"].find(
          (p) => existsSync(p)
        )
        if (!actual) throw Error(`Missing source module: ${name}`)
        return load(actual, build, cache)
      }
      return require(name)
    },
    exports,
    build
  )
  return exports
}

async function fixture(
  options: {
    build?: unknown
    fetch?: typeof fetch
    releaseFetch?: typeof fetch
    locale?: string
    copy?: (text: string) => Promise<void>
  } = {}
) {
  const dom = new JSDOM("<div id='root'></div>", {
    url: "https://aivision.example/about",
  })
  const originals = new Map<string, PropertyDescriptor | undefined>()
  for (const [name, value] of Object.entries({
    window: dom.window,
    document: dom.window.document,
    navigator: dom.window.navigator,
    HTMLElement: dom.window.HTMLElement,
    IS_REACT_ACT_ENVIRONMENT: true,
    fetch: (input: RequestInfo | URL, init?: RequestInit) =>
      String(input) === "/api/v1/releases"
        ? (options.releaseFetch ?? (async () => releaseResponse()))(input, init)
        : (options.fetch ?? (async () => response()))(input, init),
  })) {
    originals.set(name, Object.getOwnPropertyDescriptor(globalThis, name))
    Object.defineProperty(globalThis, name, {
      configurable: true,
      writable: true,
      value,
    })
  }
  Object.defineProperty(dom.window.navigator, "clipboard", {
    configurable: true,
    value: { writeText: options.copy ?? (async () => {}) },
  })
  const i18n = createInstance()
  const locale = options.locale ?? "en-US"
  await i18n.init({
    lng: locale,
    fallbackLng: locale,
    resources: {
      [locale]: {
        translation: JSON.parse(
          readFileSync(
            resolve(sourceRoot, `i18n/locales/${locale}.json`),
            "utf8"
          )
        ),
      },
    },
    interpolation: { escapeValue: false },
  })
  const page = load("pages/about/AboutPage.tsx", options.build ?? frontend)
    .default as ComponentType
  const element = dom.window.document.getElementById("root")!
  const root = createRoot(element)
  await act(async () => {
    root.render(
      createElement(
        I18nextProvider,
        { i18n },
        createElement(MemoryRouter, null, createElement(page))
      )
    )
  })
  return {
    element,
    text: () => element.textContent ?? "",
    region: (label: string) => element.querySelector(`[aria-label="${label}"]`),
    async click(label: string) {
      const button = [...element.querySelectorAll("button")].find(
        (candidate) => candidate.textContent?.trim() === label
      )
      assert.ok(button, `Missing action: ${label}`)
      await act(async () => button.click())
    },
    async cleanup() {
      await act(async () => root.unmount())
      dom.window.close()
      for (const [name, descriptor] of originals) {
        if (descriptor) Object.defineProperty(globalThis, name, descriptor)
        else Reflect.deleteProperty(globalThis, name)
      }
    },
  }
}

test("build identities stay in collapsed diagnostics and preserve report copy", async () => {
  const requests: { url: string; options?: RequestInit }[] = []
  const f = await fixture({
    fetch: async (input, options) => {
      requests.push({ url: String(input), options })
      return response()
    },
  })
  try {
    assert.match(
      f.region("Browser frontend")?.textContent ?? "",
      /frontend-build-17/
    )
    assert.match(
      f.region("Responding backend")?.textContent ?? "",
      /backend-build-17/
    )
    assert.match(
      f.region("Version comparison")?.textContent ?? "",
      /Sampled builds match the installed release/
    )
    const details = f.element.querySelector("details")
    assert.ok(details)
    assert.equal(details.open, false)
    assert.match(
      details.querySelector("summary")?.textContent ?? "",
      /Diagnostic information/
    )
    assert.doesNotMatch(f.text(), /AgentLens|Release manifest not connected/)
    assert.deepEqual(
      requests.map((request) => request.url),
      ["/api/v1/version"]
    )
    assert.equal(requests[0].options?.credentials, "same-origin")
    assert.equal(requests[0].options?.cache, "no-store")
  } finally {
    await f.cleanup()
  }
})

for (const version of ["latest", "00.0.1", "0.1.0-rc..1", "0.0.1\n"]) {
  test(`invalid versions become unknown in both cards and copied report: ${JSON.stringify(version)}`, async () => {
    let copied = ""
    const invalid = { product_version: version, component_version: version }
    const f = await fixture({
      build: { ...frontend, ...invalid },
      fetch: async () => response({ ...backend, ...invalid }),
      copy: async (text) => {
        copied = text
      },
    })
    try {
      assert.match(
        f.region("Version comparison")?.textContent ?? "",
        /Insufficient information/
      )
      for (const region of ["Browser frontend", "Responding backend"]) {
        assert.match(f.region(region)?.textContent ?? "", /Unknown/)
      }
      await f.click("Copy version report")
      const report = JSON.parse(copied)
      for (const component of [report.frontend, report.backend]) {
        assert.equal(component.product_version, null)
        assert.equal(component.component_version, null)
      }
    } finally {
      await f.cleanup()
    }
  })
}

for (const builtAt of [
  "2026-02-30T00:00:00Z",
  "1900-02-29T00:00:00Z",
  "0000-01-01T00:00:00Z",
  "2026-10-16T24:00:00Z",
]) {
  test(`invalid calendar timestamps become unknown in both cards and report: ${builtAt}`, async () => {
    let copied = ""
    const f = await fixture({
      build: { ...frontend, built_at: builtAt },
      fetch: async () => response({ ...backend, built_at: builtAt }),
      copy: async (text) => {
        copied = text
      },
    })
    try {
      assert.ok(!f.text().includes(builtAt))
      await f.click("Copy version report")
      const report = JSON.parse(copied)
      assert.equal(report.frontend.built_at, null)
      assert.equal(report.backend.built_at, null)
    } finally {
      await f.cleanup()
    }
  })
}

test("an invalid observation calendar time cannot claim a backend observation", async () => {
  const f = await fixture({
    fetch: async () => response(backend, "2026-02-30T00:00:00Z"),
  })
  try {
    assert.match(
      f.region("Responding backend")?.textContent ?? "",
      /Unavailable/
    )
  } finally {
    await f.cleanup()
  }
})

test("valid leap date and prerelease identity survive rendering and copying", async () => {
  let copied = ""
  const fields = {
    product_version: "0.1.0-dev.20261016.1",
    component_version: "0.1.0-dev.20261016.1",
    built_at: "2024-02-29T23:59:59.123456+08:00",
  }
  const f = await fixture({
    build: { ...frontend, ...fields },
    fetch: async () =>
      response(
        { ...backend, ...fields },
        undefined,
        installedRelease({
          components: {
            "aivision-fe": { ...frontend, ...fields },
            "aivision-be": { ...backend, ...fields },
          },
        })
      ),
    copy: async (text) => {
      copied = text
    },
  })
  try {
    assert.match(
      f.region("Version comparison")?.textContent ?? "",
      /Sampled builds match the installed release/
    )
    assert.ok(f.text().includes(fields.built_at))
    await f.click("Copy version report")
    const report = JSON.parse(copied)
    for (const component of [report.frontend, report.backend]) {
      assert.equal(component.product_version, fields.product_version)
      assert.equal(component.component_version, fields.component_version)
      assert.equal(component.built_at, fields.built_at)
    }
  } finally {
    await f.cleanup()
  }
})

test("a cached frontend retains its own version when the backend is newer", async () => {
  const f = await fixture({
    fetch: async () =>
      response({
        ...backend,
        product_version: "0.1.0",
        component_version: "0.1.0",
      }),
  })
  try {
    assert.match(
      f.region("Version comparison")?.textContent ?? "",
      /Different versions/
    )
    assert.match(f.region("Browser frontend")?.textContent ?? "", /0\.0\.1/)
    assert.doesNotMatch(
      f.region("Browser frontend")?.textContent ?? "",
      /0\.1\.0/
    )
    assert.match(f.region("Responding backend")?.textContent ?? "", /0\.1\.0/)
  } finally {
    await f.cleanup()
  }
})

test("different source commits are mixed even when product versions match", async () => {
  const f = await fixture({
    fetch: async () => response({ ...backend, source_sha: "b".repeat(64) }),
  })
  try {
    assert.match(
      f.region("Version comparison")?.textContent ?? "",
      /Different versions/
    )
    assert.match(
      f.region("Responding backend")?.textContent ?? "",
      new RegExp("b".repeat(64))
    )
  } finally {
    await f.cleanup()
  }
})

test("unknown frontend metadata cannot borrow the backend identity", async () => {
  const f = await fixture({ build: {} })
  try {
    assert.match(
      f.region("Version comparison")?.textContent ?? "",
      /Insufficient information/
    )
    assert.match(f.region("Browser frontend")?.textContent ?? "", /Unknown/)
    assert.doesNotMatch(
      f.region("Browser frontend")?.textContent ?? "",
      /backend-build|0\.0\.1/
    )
    assert.match(
      f.region("Responding backend")?.textContent ?? "",
      /backend-build-17/
    )
  } finally {
    await f.cleanup()
  }
})

test("dirty metadata is visible and does not imply identical release artifacts", async () => {
  const f = await fixture({ build: { ...frontend, dirty: true } })
  try {
    assert.match(
      f.region("Version comparison")?.textContent ?? "",
      /Local or modified build/
    )
    assert.match(
      f.region("Browser frontend")?.textContent ?? "",
      /Uncommitted changes/
    )
  } finally {
    await f.cleanup()
  }
})

test("loading preserves frontend identity and resolves the backend independently", async () => {
  let resolveResponse!: (value: Response) => void
  const pending = new Promise<Response>((resolve) => {
    resolveResponse = resolve
  })
  const f = await fixture({ fetch: () => pending })
  try {
    assert.match(f.region("Responding backend")?.textContent ?? "", /Loading/)
    assert.match(
      f.region("Browser frontend")?.textContent ?? "",
      /frontend-build-17/
    )
    await act(async () => resolveResponse(response()))
    assert.match(
      f.region("Responding backend")?.textContent ?? "",
      /backend-build-17/
    )
  } finally {
    await f.cleanup()
  }
})

test("failed fetch keeps the frontend visible and refresh recovers", async () => {
  let attempt = 0
  const f = await fixture({
    fetch: async () =>
      ++attempt === 1
        ? new Response("private server detail", { status: 503 })
        : response(),
  })
  try {
    assert.match(
      f.region("Responding backend")?.textContent ?? "",
      /Unavailable/
    )
    assert.match(
      f.region("Browser frontend")?.textContent ?? "",
      /frontend-build-17/
    )
    assert.doesNotMatch(f.text(), /private server detail/)
    await f.click("Refresh backend")
    assert.match(
      f.region("Responding backend")?.textContent ?? "",
      /backend-build-17/
    )
  } finally {
    await f.cleanup()
  }
})

test("invalid API metadata is not accepted as an observed backend build", async () => {
  const f = await fixture({
    fetch: async () => response({ ...backend, component: "another-service" }),
  })
  try {
    assert.match(
      f.region("Responding backend")?.textContent ?? "",
      /Unavailable/
    )
    assert.doesNotMatch(f.text(), /another-service/)
  } finally {
    await f.cleanup()
  }
})

test("copy exports only the sanitized version report and confirms success", async () => {
  let copied = ""
  const f = await fixture({
    build: { ...frontend, secret: "frontend-secret" },
    fetch: async () =>
      response({
        ...backend,
        token: "backend-secret",
        env: { PASSWORD: "private" },
      }),
    copy: async (text) => {
      copied = text
    },
  })
  try {
    await f.click("Copy version report")
    const report = JSON.parse(copied)
    assert.equal(report.frontend.build_id, "frontend-build-17")
    assert.equal(report.backend.build_id, "backend-build-17")
    assert.deepEqual(report.expected, installedRelease())
    assert.equal(report.scope, "responding_instance")
    assert.deepEqual(
      Object.keys(report.frontend).sort(),
      [
        "schema_version",
        "component",
        "product_version",
        "component_version",
        "source_sha",
        "build_id",
        "built_at",
        "target_channel",
        "dirty",
      ].sort()
    )
    assert.doesNotMatch(
      copied,
      /frontend-secret|backend-secret|PASSWORD|private\.example/
    )
    assert.match(
      f.element.querySelector('[role="status"]')?.textContent ?? "",
      /Version report copied/
    )
  } finally {
    await f.cleanup()
  }
})

test("clipboard rejection is surfaced without a false success message", async () => {
  const f = await fixture({
    copy: async () => {
      throw Error("denied")
    },
  })
  try {
    await f.click("Copy version report")
    assert.match(
      f.element.querySelector('[role="status"]')?.textContent ?? "",
      /Could not copy/
    )
    assert.doesNotMatch(f.text(), /Version report copied/)
  } finally {
    await f.cleanup()
  }
})

test("the page renders Chinese status and actions", async () => {
  const f = await fixture({ locale: "zh-CN" })
  try {
    assert.match(
      f.element.querySelector("h1")?.textContent ?? "",
      /关于 AI Vision/
    )
    assert.match(f.text(), /当前版本/)
    await f.click("检查更新")
    await f.click("复制版本报告")
    assert.match(
      f.element.querySelector('[role="status"]')?.textContent ?? "",
      /版本报告已复制/
    )
  } finally {
    await f.cleanup()
  }
})

test("opening the page checks releases independently of a pending backend", async () => {
  const requests: { url: string; options?: RequestInit }[] = []
  const f = await fixture({
    fetch: () => new Promise<Response>(() => {}),
    releaseFetch: async (input, options) => {
      requests.push({ url: String(input), options })
      return newerRelease()
    },
  })
  try {
    assert.match(f.region("Current version")?.textContent ?? "", /Unknown/)
    assert.match(
      f.region("Update status")?.textContent ?? "",
      /could not be determined/
    )
    assert.match(
      f.region("Release notes")?.textContent ?? "",
      /Faster trace search/
    )
    assert.equal(requests[0].url, "/api/v1/releases")
    assert.equal(requests[0].options?.credentials, "same-origin")
    assert.equal(requests[0].options?.cache, "no-store")
  } finally {
    await f.cleanup()
  }
})

test("a clean stable build matching the latest release is up to date", async () => {
  const f = await fixture()
  try {
    assert.match(f.region("Update status")?.textContent ?? "", /Up to date/)
    assert.match(
      f.region("Release notes")?.textContent ?? "",
      /Faster trace search/
    )
    assert.ok(f.element.querySelector("li"))
    const fullNotes = [...f.element.querySelectorAll("a")].find(
      (a) => a.textContent === "View full release notes"
    )
    assert.equal(
      fullNotes?.getAttribute("href"),
      "https://github.com/antgroup/ai-vision/releases/tag/v0.0.1"
    )
  } finally {
    await f.cleanup()
  }
})

test("the historical release URL falls back to public release history", async () => {
  const url = "https://github.com/ob-labs/ai-vision-ob/releases/tag/v0.0.1"
  const f = await fixture({
    releaseFetch: async () => releaseResponse({ ...stableRelease, url }),
  })
  try {
    assert.match(
      f.region("Update status")?.textContent ?? "",
      /Unable to check for updates/
    )
    assert.equal(f.region("Release notes"), null)
    assert.equal(f.element.querySelector(`a[href="${url}"]`), null)
    assert.ok(
      f.element.querySelector(
        'a[href="https://github.com/antgroup/ai-vision/releases"]'
      )
    )
  } finally {
    await f.cleanup()
  }
})

test("version ordering compares numeric Pride version parts", async () => {
  const f = await fixture({
    fetch: async () =>
      response(
        backend,
        undefined,
        installedRelease({ product_version: "0.10.0" })
      ),
    releaseFetch: async () =>
      releaseResponse({
        version: "0.2.0",
        tag_name: "v0.2.0",
        name: "AI Vision 0.2.0",
        body: "Changes",
        url: "https://github.com/antgroup/ai-vision/releases/tag/v0.2.0",
        published_at: "2026-11-30T09:00:00Z",
      }),
  })
  try {
    assert.match(
      f.region("Update status")?.textContent ?? "",
      /ahead of the latest release/
    )
    assert.doesNotMatch(
      f.region("Update status")?.textContent ?? "",
      /Update available|Up to date/
    )
  } finally {
    await f.cleanup()
  }
})

for (const fields of [
  { channel: "dev" },
  { channel: "daily" },
  { channel: "rc" },
  { product_version: "0.0.1-rc.1", channel: "rc" },
]) {
  test(`preview installed releases do not claim a matching stable release: ${JSON.stringify(fields)}`, async () => {
    const f = await fixture({
      fetch: async () => response(backend, undefined, installedRelease(fields)),
    })
    try {
      assert.match(
        f.region("Update status")?.textContent ?? "",
        /Development or preview build/
      )
      assert.doesNotMatch(
        f.region("Update status")?.textContent ?? "",
        /Up to date/
      )
    } finally {
      await f.cleanup()
    }
  })
}

test("an unknown frontend identity does not erase the installed release", async () => {
  const f = await fixture({ build: {} })
  try {
    assert.match(f.region("Current version")?.textContent ?? "", /0\.0\.1/)
    assert.match(
      f.region("Version comparison")?.textContent ?? "",
      /Insufficient information/
    )
    assert.match(f.region("Browser frontend")?.textContent ?? "", /Unknown/)
    assert.match(
      f.region("Update status")?.textContent ?? "",
      /could not be determined/
    )
    assert.match(f.text(), /The installed release could not be verified/)
    assert.doesNotMatch(
      f.region("Update status")?.textContent ?? "",
      /Up to date/
    )
  } finally {
    await f.cleanup()
  }
})

test("a failed release check can recover through Check for updates", async () => {
  let attempt = 0
  const f = await fixture({
    releaseFetch: async () =>
      ++attempt === 1
        ? new Response("private provider detail", { status: 503 })
        : newerRelease(),
  })
  try {
    assert.match(
      f.region("Update status")?.textContent ?? "",
      /Unable to check for updates/
    )
    assert.doesNotMatch(f.text(), /private provider detail/)
    await f.click("Check for updates")
    assert.match(
      f.region("Update status")?.textContent ?? "",
      /Update available/
    )
    assert.match(f.region("Release notes")?.textContent ?? "", /0\.1\.0/)
  } finally {
    await f.cleanup()
  }
})

for (const [status, message] of [
  ["no_release", /No stable release information is available yet/],
  ["disabled", /Update checks are disabled/],
  ["unavailable", /Unable to check for updates/],
] as const) {
  test(`${status} does not claim that the current version is latest`, async () => {
    const f = await fixture({
      releaseFetch: async () => releaseResponse(null, status),
    })
    try {
      assert.match(f.region("Update status")?.textContent ?? "", message)
      assert.doesNotMatch(
        f.region("Update status")?.textContent ?? "",
        /Up to date/
      )
      assert.ok(
        f.element.querySelector(
          'a[href="https://github.com/antgroup/ai-vision/releases"]'
        )
      )
    } finally {
      await f.cleanup()
    }
  })
}

test("bilingual release notes and contributor links render without raw HTML, images, or unsafe links", async () => {
  const f = await fixture({
    releaseFetch: async () =>
      newerRelease(
        '## English\n\n### Component updates\n\n- **AgentLens** diagnostics\n\n## 中文\n\n### 组件更新\n\n- **AgentLens** 诊断功能\n\n## Contributors / 贡献者\n\n- [Example contributor](https://github.com/example-contributor)\n\n[Docs](https://example.com/docs)\n\n[Unsafe](javascript:alert%281%29)\n\n![tracking](https://example.com/pixel.png)\n\n<script>window.releaseInjection = true</script>\n\n<img src="https://example.com/hidden.png">'
      ),
  })
  try {
    const notes = f.region("Release notes")!
    assert.match(notes.textContent ?? "", /English/)
    assert.match(notes.textContent ?? "", /Component updates/)
    assert.match(notes.textContent ?? "", /AgentLens diagnostics/)
    assert.match(notes.textContent ?? "", /中文/)
    assert.match(notes.textContent ?? "", /组件更新/)
    assert.match(notes.textContent ?? "", /AgentLens 诊断功能/)
    assert.match(notes.textContent ?? "", /Contributors \/ 贡献者/)
    const contributors = notes.querySelectorAll(
      'a[href="https://github.com/example-contributor"]'
    )
    assert.equal(contributors.length, 1)
    assert.equal(contributors[0]?.textContent, "Example contributor")
    assert.equal(contributors[0]?.getAttribute("target"), "_blank")
    assert.match(contributors[0]?.getAttribute("rel") ?? "", /noreferrer/)
    assert.equal(notes.querySelectorAll("img, script").length, 0)
    const docs = [...notes.querySelectorAll("a")].find(
      (a) => a.textContent === "Docs"
    )
    assert.equal(docs?.getAttribute("href"), "https://example.com/docs")
    assert.equal(docs?.getAttribute("target"), "_blank")
    assert.match(docs?.getAttribute("rel") ?? "", /noreferrer/)
    assert.ok(
      ![...notes.querySelectorAll("a")].some((a) =>
        /^javascript:/i.test(a.getAttribute("href") ?? "")
      )
    )
  } finally {
    await f.cleanup()
  }
})

for (const fields of [
  { version: "0.1.0-rc.1", tag_name: "v0.1.0-rc.1" },
  {
    url: "https://github.com.evil.example/antgroup/ai-vision/releases/tag/v0.1.0",
  },
  { url: "https://github.com/antgroup/ai-vision/releases/tag/v0.2.0" },
  { url: "javascript:alert(1)" },
  { published_at: "2026-02-30T10:00:00Z" },
]) {
  test(`invalid release metadata cannot supply update links or notes: ${JSON.stringify(fields)}`, async () => {
    const f = await fixture({
      releaseFetch: async () =>
        releaseResponse({
          version: "0.1.0",
          tag_name: "v0.1.0",
          name: "AI Vision 0.1.0",
          body: "unverified release notes",
          url: "https://github.com/antgroup/ai-vision/releases/tag/v0.1.0",
          published_at: "2026-10-31T09:00:00Z",
          ...fields,
        }),
    })
    try {
      assert.match(
        f.region("Update status")?.textContent ?? "",
        /Unable to check for updates/
      )
      assert.equal(f.region("Release notes"), null)
      assert.doesNotMatch(f.text(), /unverified release notes/)
    } finally {
      await f.cleanup()
    }
  })
}

test("the installed release is independent of unchanged component builds", async () => {
  let copied = ""
  const historicalBackend = {
    ...backend,
    product_version: "0.0.1-dev.20261015.2",
    source_sha: "b".repeat(40),
    target_channel: "dev",
  }
  const expected = installedRelease({
    product_version: "0.1.0",
    components: { "aivision-fe": frontend, "aivision-be": historicalBackend },
    secret: "release-secret",
  })
  const f = await fixture({
    fetch: async () => response(historicalBackend, undefined, expected),
    releaseFetch: async () => newerRelease(),
    copy: async (text) => {
      copied = text
    },
  })
  try {
    assert.match(f.region("Current version")?.textContent ?? "", /0\.1\.0/)
    assert.match(f.region("Update status")?.textContent ?? "", /Up to date/)
    assert.match(
      f.region("Version comparison")?.textContent ?? "",
      /Sampled builds match the installed release/
    )
    assert.doesNotMatch(f.text(), /Reload the page/)
    await f.click("Copy version report")
    const report = JSON.parse(copied)
    assert.equal(report.expected.product_version, "0.1.0")
    assert.equal(report.expected.source_sha, "c".repeat(40))
    assert.equal(report.frontend.product_version, "0.0.1")
    assert.equal(
      report.backend.product_version,
      historicalBackend.product_version
    )
    assert.equal(report.backend.source_sha, historicalBackend.source_sha)
    assert.doesNotMatch(copied, /release-secret|private\.example/)
  } finally {
    await f.cleanup()
  }
})

for (const expected of [null, undefined]) {
  test(`a missing installed release stays unknown despite matching builds: ${expected}`, async () => {
    const f = await fixture({
      fetch: async () => {
        const result = await response()
        const payload = await result.json()
        if (expected === undefined) delete payload.data.expected
        else payload.data.expected = expected
        return new Response(JSON.stringify(payload))
      },
    })
    try {
      assert.match(f.region("Current version")?.textContent ?? "", /Unknown/)
      assert.match(f.text(), /The installed release could not be verified/)
      assert.match(
        f.region("Version comparison")?.textContent ?? "",
        /Insufficient information/
      )
      assert.match(f.region("Browser frontend")?.textContent ?? "", /0\.0\.1/)
      assert.match(f.region("Responding backend")?.textContent ?? "", /0\.0\.1/)
      assert.doesNotMatch(
        f.region("Update status")?.textContent ?? "",
        /Up to date/
      )
    } finally {
      await f.cleanup()
    }
  })
}

for (const fields of [
  { build_id: "stale-browser-build" },
  { target_channel: "dev" },
  { component_version: "0.0.2" },
  { built_at: "2026-10-15T08:00:00Z" },
]) {
  test(`an observed component differing from its release identity is mixed: ${JSON.stringify(fields)}`, async () => {
    const f = await fixture({ build: { ...frontend, ...fields } })
    try {
      assert.match(
        f.region("Version comparison")?.textContent ?? "",
        /Different versions/
      )
      assert.match(f.text(), /does not match the installed release/)
      assert.doesNotMatch(
        f.region("Update status")?.textContent ?? "",
        /Up to date/
      )
    } finally {
      await f.cleanup()
    }
  })
}

test("unversioned independent components can match their recorded build identities", async () => {
  const fe = { ...frontend, component_version: null }
  const be = { ...backend, component_version: null }
  const f = await fixture({
    build: fe,
    fetch: async () =>
      response(
        be,
        undefined,
        installedRelease({
          components: { "aivision-fe": fe, "aivision-be": be },
        })
      ),
  })
  try {
    assert.match(
      f.region("Version comparison")?.textContent ?? "",
      /Sampled builds match the installed release/
    )
    assert.match(f.region("Update status")?.textContent ?? "", /Up to date/)
  } finally {
    await f.cleanup()
  }
})
