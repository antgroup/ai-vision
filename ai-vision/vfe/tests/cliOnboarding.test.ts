import assert from "node:assert/strict"
import { existsSync, readFileSync } from "node:fs"
import { createRequire } from "node:module"
import test from "node:test"
import { JSDOM } from "jsdom"
import i18next from "i18next"
import { act, createElement, type ComponentType } from "react"
import { createRoot } from "react-dom/client"
import { I18nextProvider, useTranslation } from "react-i18next"
import ts from "typescript"
import type { ConfigGuideV2 } from "../src/components/agents/guidance/types.ts"

const require = createRequire(import.meta.url)
const connectCommand =
  "aiv connect codex --base-url 'https://vision.example.test' --space-id 'space-test' --agent-uuid 'agent-test' --installation-token 'test-ticket'"
const unixCheck =
  'export PATH="$HOME/.local/bin:$PATH"\ncommand -v aiv && aiv --version'
const checkCommand = (host: string) =>
  `${unixCheck}\ncommand -v ${host} && ${host} --version`
const windowsCheck = (host?: string) =>
  [
    "Get-Command aiv",
    "aiv --version",
    ...(host ? [`Get-Command ${host}`, `${host} --version`] : []),
  ].join("\n")

// An already saved guide has no new CLI installation metadata.
const savedGuide: ConfigGuideV2 = {
  schema: "v2",
  plugin_key: "codex",
  sections: [
    {
      type: "prerequisites",
      title: "已有接入前提",
      items: [{ label: "Codex", value: "已安装并能正常运行" }],
    },
    {
      type: "setup_steps",
      title: "在 Agent 所在机器安装",
      platforms: {
        "macOS/Linux": {
          modes: {
            cli: {
              label: "原生安装",
              steps: [{ title: "安装插件", command: connectCommand }],
            },
          },
        },
        Windows: {
          modes: {
            cli: {
              label: "原生安装",
              steps: [{ title: "安装插件", command: connectCommand }],
            },
          },
        },
      },
    },
    {
      type: "verification",
      title: "查看采集结果",
      steps: ["重启 Codex 并执行任务"],
    },
  ],
}

const knownPlugins = [
  ["claude-code", "Claude Code", "claude"],
  ["codex", "Codex", "codex"],
  ["kimi", "Kimi CLI", "kimi"],
  ["opencode", "OpenCode", "opencode"],
  ["openclaw", "OpenClaw", "openclaw"],
  ["pi", "Pi", "pi"],
  ["dsh", "DSH", "dsh"],
] as const

// Published Chinese guide fields, as saved before the UI supported other locales.
function builtinGuide(pluginKey = "codex", agentName = "Codex"): ConfigGuideV2 {
  return {
    schema: "v2",
    plugin_key: pluginKey,
    sections: [
      {
        type: "prerequisites",
        title: "接入前提",
        items: [
          {
            label: "aivision-cli",
            value: "请确保本机已安装好 aivision-cli（aiv）；接入命令由它执行",
          },
          { label: agentName, value: "已安装并配置好，能够调用模型和执行工具" },
          {
            label: "Python",
            value: "3.11 或以上；安装器会查找 PATH 或 uv 中已安装的兼容版本",
          },
          { label: "Node.js", value: "已安装" },
        ],
      },
      {
        type: "setup_steps",
        title: "在 Agent 所在机器安装",
        platforms: {
          "macOS/Linux": {
            modes: {
              cli: {
                label: "原生安装",
                steps: [
                  {
                    title: "复制并执行安装命令，授权和上报配置将自动完成",
                    command: connectCommand.replace(
                      "connect codex",
                      `connect ${pluginKey}`
                    ),
                  },
                ],
              },
              // A second mode makes the mode label visible and checks custom copy.
              custom: { label: "Custom installation", steps: [] },
            },
          },
        },
      },
      {
        type: "verification",
        title: "查看采集结果",
        steps: [
          `重启 ${agentName}${pluginKey === "codex" ? "，输入 /hooks 检查并信任新增的 AI Vision hooks" : ""}，执行一次包含模型调用和工具调用的任务`,
          "打开当前空间的数据看板和任务轨迹，确认数据已上报",
          '使用自定义配置目录时，安装命令追加 --config-dir "目录路径"，Agent 启动时也使用该目录',
        ],
      },
    ],
  }
}

const hermesGuide: ConfigGuideV2 = {
  schema: "v2",
  sections: [
    {
      type: "prerequisites",
      title: "接入前提",
      items: [
        {
          label: "Hermes Agent",
          value: "已安装官方 hermes-agent 0.18.2，能够调用模型和执行工具",
        },
        {
          label: "源码",
          value: "获取 AI Vision 源码，并在 Hermes 的 Python 环境中安装插件",
        },
      ],
    },
    {
      type: "setup_steps",
      title: "安装生产插件",
      platforms: {
        "macOS/Linux": {
          modes: {
            source: {
              label: "源码安装",
              steps: [
                {
                  title:
                    "将 <repo> 替换为仓库路径，按 Hermes README 配置宿主 Python 环境",
                  command:
                    "make -C <repo>/ai-vision/agent-plugins/hermes install",
                },
                {
                  title:
                    "按同目录 README 设置 observ-hermes.json 的 OTLP 三信号端点。使用当前空间的 SpaceKey 申请短期 ingest token，仅将 Bearer token 配置为上报授权；不要把 SpaceKey 当作 gateway token。",
                  command: "",
                },
              ],
            },
            custom: { label: "Custom source setup", steps: [] },
          },
        },
      },
    },
    {
      type: "verification",
      title: "查看采集结果",
      steps: [
        "重启 Hermes，执行包含模型调用和 read_file 工具调用的任务",
        "在当前 Agent 的数据看板和任务轨迹中核对模型、工具输入输出、用量与未知标记",
      ],
    },
  ],
}

async function fixture(language = "zh-CN", platform = "Linux") {
  const dom = new JSDOM("<!doctype html><div id='root'></div>", {
    pretendToBeVisual: true,
    url: "https://vision.example.test/access",
  })
  Object.defineProperty(dom.window.navigator, "userAgent", { value: platform })
  const copied: string[] = []
  Object.defineProperty(dom.window.navigator, "clipboard", {
    value: {
      writeText: async (text: string) => {
        copied.push(text)
      },
    },
  })
  const timers = new Set<ReturnType<typeof setTimeout>>()
  const nativeSetTimeout = globalThis.setTimeout
  const nativeClearTimeout = globalThis.clearTimeout
  const originals = new Map<string, PropertyDescriptor | undefined>()
  const globals: Record<string, unknown> = {
    window: dom.window,
    document: dom.window.document,
    navigator: dom.window.navigator,
    HTMLElement: dom.window.HTMLElement,
    Element: dom.window.Element,
    Node: dom.window.Node,
    DocumentFragment: dom.window.DocumentFragment,
    MutationObserver: dom.window.MutationObserver,
    CustomEvent: dom.window.CustomEvent,
    getComputedStyle: dom.window.getComputedStyle.bind(dom.window),
    requestAnimationFrame: dom.window.requestAnimationFrame.bind(dom.window),
    cancelAnimationFrame: dom.window.cancelAnimationFrame.bind(dom.window),
    setTimeout: (callback: () => void, delay?: number) => {
      const timer = nativeSetTimeout(callback, delay)
      timers.add(timer)
      return timer
    },
    IS_REACT_ACT_ENVIRONMENT: true,
  }
  for (const [name, value] of Object.entries(globals)) {
    originals.set(name, Object.getOwnPropertyDescriptor(globalThis, name))
    Object.defineProperty(globalThis, name, {
      configurable: true,
      writable: true,
      value,
    })
  }
  const i18n = i18next.createInstance()
  await i18n.init({
    lng: language,
    fallbackLng: "zh-CN",
    resources: Object.fromEntries(
      ["zh-CN", "en-US", "ja-JP"].map((locale) => [
        locale,
        {
          translation: JSON.parse(
            readFileSync(
              new URL(`../src/i18n/locales/${locale}.json`, import.meta.url),
              "utf8"
            )
          ),
        },
      ])
    ),
  })
  const modules = new Map<string, unknown>()
  function load(file: URL): unknown {
    if (modules.has(file.href)) return modules.get(file.href)
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
      // Use the provider's module instance; ESM and CJS have separate contexts.
      if (name === "react-i18next") return { useTranslation }
      if (name === "@/i18n")
        return {
          default: i18n,
          getCurrentLocale: () => i18n.language,
          isChineseLocale: () => i18n.language.startsWith("zh"),
        }
      if (name.startsWith(".") || name.startsWith("@/")) {
        const base = name.startsWith("@/")
          ? new URL(`../src/${name.slice(2)}`, import.meta.url)
          : new URL(name, file)
        const resolved = ["", ".tsx", ".ts"]
          .map((extension) => new URL(base.href + extension))
          .find(existsSync)
        assert.ok(resolved, `Unresolved component dependency: ${name}`)
        return load(resolved)
      }
      return require(name)
    }, exports)
    return exports
  }
  const { OnboardingGuideSections } = load(
    new URL(
      "../src/components/agents/guidance/OnboardingGuideSections.tsx",
      import.meta.url
    )
  ) as {
    OnboardingGuideSections: ComponentType<{
      guide: ConfigGuideV2
      hasInstallationCommand: boolean
      agentName?: string
      agentProduct?: string
    }>
  }
  const element = dom.window.document.getElementById("root")!
  const root = createRoot(element)
  return {
    element,
    copied,
    window: dom.window,
    t: i18n.t.bind(i18n),
    async render(
      hasInstallationCommand: boolean,
      guide = savedGuide,
      agent: { agentName?: string; agentProduct?: string } = {}
    ) {
      await act(async () =>
        root.render(
          createElement(
            I18nextProvider,
            { i18n },
            createElement(OnboardingGuideSections, {
              guide,
              hasInstallationCommand,
              ...agent,
            })
          )
        )
      )
    },
    async changeLanguage(nextLanguage: string) {
      await act(async () => {
        await i18n.changeLanguage(nextLanguage)
      })
    },
    async cleanup() {
      await act(async () => root.unmount())
      for (const timer of timers) nativeClearTimeout(timer)
      dom.window.close()
      for (const [name, descriptor] of originals) {
        if (descriptor) Object.defineProperty(globalThis, name, descriptor)
        else Reflect.deleteProperty(globalThis, name)
      }
    },
  }
}

test("saved plugin guides show CLI installation and copyable PATH checks before issuing a token", async () => {
  const ui = await fixture()
  try {
    await ui.render(false)
    const link = ui.element.querySelector(
      'a[href="https://aivision.mintlify.site/zh-CN/guides/install-cli"]'
    )
    assert.ok(
      link,
      "existing guides must expose CLI installation before token generation"
    )
    const check = [...ui.element.querySelectorAll("code")].find(
      (code) => code.textContent === checkCommand("codex")
    )
    assert.ok(
      check,
      "show a PATH-aware check that can be run in the current terminal"
    )
    assert.equal(ui.element.textContent?.includes(connectCommand), false)
    assert.match(ui.element.textContent || "", /已有接入前提/)
    const copy = check.parentElement?.querySelector("button")
    assert.ok(copy, "the check command must be copyable")
    await act(async () =>
      copy.dispatchEvent(new ui.window.MouseEvent("click", { bubbles: true }))
    )
    assert.deepEqual(ui.copied, [checkCommand("codex")])
  } finally {
    await ui.cleanup()
  }
})

for (const [pluginKey, agentName, host] of knownPlugins) {
  test(`${pluginKey} shows aiv + host checks, ready copy and troubleshooting`, async () => {
    const ui = await fixture()
    try {
      const command = checkCommand(host)
      await ui.render(false, builtinGuide(pluginKey, agentName), { agentName })
      const code = [...ui.element.querySelectorAll("code")].find(
        (element) => element.textContent === command
      )
      assert.ok(code, `${pluginKey}: aiv + ${host} check`)
      const ready = ui.t("agentAccess.cliInstall.readyWithHost", { host })
      assert.ok(
        [...ui.element.querySelectorAll("p")].some(
          (paragraph) => paragraph.textContent === ready
        ),
        `${pluginKey}: ready copy uses ${host}`
      )
      const troubleshooting = ui.t("agentAccess.cliInstall.hostNotFound", {
        host,
        agent: agentName,
      })
      assert.ok(
        [...ui.element.querySelectorAll("p")].some(
          (paragraph) => paragraph.textContent === troubleshooting
        ),
        `${pluginKey}: troubleshooting uses ${host}`
      )
      const copy = code.parentElement?.querySelector("button")
      assert.ok(copy)
      await act(async () =>
        copy.dispatchEvent(new ui.window.MouseEvent("click", { bubbles: true }))
      )
      assert.deepEqual(ui.copied, [command])
    } finally {
      await ui.cleanup()
    }
  })
}

test("a generated guide host overrides the fallback map", async () => {
  const ui = await fixture()
  try {
    const guide = { ...builtinGuide("codex", "Codex"), host: "codex-custom" }
    await ui.render(false, guide)
    const command = checkCommand("codex-custom")
    assert.ok(
      [...ui.element.querySelectorAll("code")].some(
        (code) => code.textContent === command
      )
    )
    const ready = ui.t("agentAccess.cliInstall.readyWithHost", {
      host: "codex-custom",
    })
    assert.ok(ui.element.textContent?.includes(ready))
  } finally {
    await ui.cleanup()
  }
})

test("unknown plugin keys keep the aiv-only check and ready copy", async () => {
  const ui = await fixture()
  try {
    await ui.render(false, builtinGuide("unknown-agent", "Unknown Agent"))
    const commands = [...ui.element.querySelectorAll("code")].map(
      (code) => code.textContent
    )
    assert.ok(commands.includes(unixCheck))
    assert.equal(
      commands.some((command) => command?.includes("command -v unknown-agent")),
      false
    )
    assert.ok(
      ui.element.textContent?.includes(ui.t("agentAccess.cliInstall.ready"))
    )
    assert.equal(
      ui.element.textContent?.includes(
        ui.t("agentAccess.cliInstall.hostNotFound", {
          host: "unknown-agent",
          agent: "Unknown Agent",
        })
      ),
      false
    )
  } finally {
    await ui.cleanup()
  }
})

test("issuing a plugin command preserves both CLI preparation and the original connection command", async () => {
  const ui = await fixture()
  try {
    await ui.render(false)
    await ui.render(true)
    const commands = [...ui.element.querySelectorAll("code")].map(
      (code) => code.textContent
    )
    assert.ok(
      commands.includes(checkCommand("codex")),
      "CLI preparation stays visible after authorization"
    )
    assert.ok(
      commands.includes(connectCommand),
      "connection origin, scope and token are preserved"
    )
    assert.ok(
      commands.indexOf(checkCommand("codex")) < commands.indexOf(connectCommand)
    )
    assert.ok(
      ui.element.querySelector(
        'a[href="https://aivision.mintlify.site/zh-CN/guides/install-cli"]'
      )
    )
  } finally {
    await ui.cleanup()
  }
})

test("a Windows browser shows the PowerShell check even with a legacy guide", async () => {
  const ui = await fixture("en-US", "Windows")
  try {
    await ui.render(false)
    assert.equal(ui.element.textContent?.includes(connectCommand), false)
    await ui.render(true)
    const platforms = [...ui.element.querySelectorAll('[role="tab"]')].map(
      (tab) => tab.textContent?.trim()
    )
    assert.deepEqual(platforms, ["macOS/Linux", "Windows", "macOS/Linux"])
    assert.match(ui.element.textContent || "", /PowerShell 7/)
    const commands = [...ui.element.querySelectorAll("code")].map(
      (code) => code.textContent
    )
    assert.equal(commands.includes(checkCommand("codex")), false)
    assert.ok(commands.includes(windowsCheck("codex")))
    assert.ok(commands.includes(connectCommand))
    assert.ok(
      ui.element.querySelector(
        'a[href="https://aivision.mintlify.site/en/guides/install-cli"]'
      )
    )
  } finally {
    await ui.cleanup()
  }
})

test("a Windows browser uses PowerShell ready, path and troubleshooting copy", async () => {
  const ui = await fixture("en-US", "Windows")
  try {
    await ui.render(false, builtinGuide("codex", "Codex"), {
      agentName: "Codex",
    })
    const text = ui.element.textContent || ""
    assert.ok(
      text.includes(
        ui.t("agentAccess.cliInstall.readyWithHostWindows", { host: "codex" })
      )
    )
    assert.ok(text.includes(ui.t("agentAccess.cliInstall.pathHintWindows")))
    assert.ok(
      text.includes(
        ui.t("agentAccess.cliInstall.hostNotFoundWindows", {
          host: "codex",
          agent: "Codex",
        })
      )
    )
    assert.equal(
      text.includes(
        ui.t("agentAccess.cliInstall.readyWithHost", { host: "codex" })
      ),
      false
    )
  } finally {
    await ui.cleanup()
  }
})

test("a Windows browser falls back to the aiv-only PowerShell check", async () => {
  const ui = await fixture("en-US", "Windows")
  try {
    await ui.render(false, builtinGuide("unknown-agent", "Unknown Agent"))
    const text = ui.element.textContent || ""
    assert.ok(text.includes(windowsCheck()))
    assert.ok(text.includes(ui.t("agentAccess.cliInstall.readyWindows")))
    assert.equal(text.includes("Get-Command unknown-agent"), false)
  } finally {
    await ui.cleanup()
  }
})

test("a generated guide host keeps its Windows install tab", async () => {
  const ui = await fixture()
  const guide = structuredClone(builtinGuide("codex", "Codex"))
  const setup = guide.sections.find((section) => section.type === "setup_steps")
  assert.ok(setup && setup.type === "setup_steps")
  setup.platforms.Windows = {
    modes: {
      cli: {
        label: "原生安装",
        steps: [{ title: "安装插件", command: connectCommand }],
      },
    },
  }
  const windowsTabCount = () =>
    [...ui.element.querySelectorAll('[role="tab"]')].filter(
      (tab) => tab.textContent?.trim() === "Windows"
    ).length
  try {
    await ui.render(true, guide)
    assert.equal(windowsTabCount(), 1)
    guide.host = "codex"
    await ui.render(true, guide)
    assert.equal(windowsTabCount(), 2)
  } finally {
    await ui.cleanup()
  }
})

test("non-plugin guides keep their original steps without adding CLI preparation", async () => {
  const ui = await fixture()
  const manualGuide = structuredClone(savedGuide)
  delete manualGuide.plugin_key
  try {
    await ui.render(false, manualGuide)
    assert.equal(
      ui.element.querySelector('a[href*="/guides/install-cli"]'),
      null
    )
    const commands = [...ui.element.querySelectorAll("code")].map(
      (code) => code.textContent
    )
    assert.deepEqual(commands, [connectCommand])
  } finally {
    await ui.cleanup()
  }
})

for (const language of ["en-US", "ja-JP"]) {
  test(`${language}: saved builtin plugin guides translate while commands and custom modes stay intact`, async () => {
    const ui = await fixture(language)
    try {
      for (const [pluginKey, agentName] of knownPlugins) {
        const guide = builtinGuide(pluginKey, agentName)
        const before = structuredClone(guide)
        await ui.render(true, guide, { agentName })
        const text = ui.element.textContent || ""
        for (const key of [
          "prerequisitesTitle",
          "requirements.cli",
          "requirements.agent",
          "requirements.python",
          "requirements.node",
          "setup.installOnHost",
          "setup.nativeMode",
          "setup.runCommand",
          "verificationTitle",
          "verification.checkData",
          "verification.configDir",
          `verification.${pluginKey === "codex" ? "restartCodex" : "restart"}`,
        ]) {
          const resourceKey = `agentAccess.guide.${key}`
          const translated = ui.t(resourceKey, { name: agentName })
          assert.notEqual(translated, resourceKey)
          assert.ok(text.includes(translated), `${pluginKey}: ${resourceKey}`)
        }
        assert.match(text, /Node\.js/)
        assert.match(text, /3\.11/)
        assert.match(text, /--config-dir/)
        assert.match(text, /Custom installation/)
        if (pluginKey === "codex") assert.match(text, /\/hooks/)

        const command = connectCommand.replace(
          "connect codex",
          `connect ${pluginKey}`
        )
        const code = [...ui.element.querySelectorAll("code")].find(
          (element) => element.textContent === command
        )
        assert.ok(code, `${pluginKey}: original command is rendered verbatim`)
        const copy = code.parentElement?.querySelector("button")
        assert.ok(copy)
        await act(async () =>
          copy.dispatchEvent(
            new ui.window.MouseEvent("click", { bubbles: true })
          )
        )
        assert.equal(ui.copied.at(-1), command)
        assert.deepEqual(
          guide,
          before,
          "rendering does not rewrite the saved guide"
        )
      }
    } finally {
      await ui.cleanup()
    }
  })

  test(`${language}: Hermes manual instructions translate long setup guidance without introducing CLI preparation`, async () => {
    const ui = await fixture(language)
    const before = structuredClone(hermesGuide)
    try {
      await ui.render(false, hermesGuide, {
        agentName: "Hermes Agent",
        agentProduct: "Hermes",
      })
      const text = ui.element.textContent || ""
      for (const key of [
        "prerequisitesTitle",
        "requirements.hermes",
        "requirements.sourceLabel",
        "requirements.source",
        "setup.installProduction",
        "setup.sourceMode",
        "setup.hermesEnvironment",
        "setup.hermesEndpoints",
        "verificationTitle",
        "verification.restartHermes",
        "verification.checkHermes",
      ]) {
        const resourceKey = `agentAccess.guide.${key}`
        const translated = ui.t(resourceKey)
        assert.notEqual(translated, resourceKey)
        assert.ok(text.includes(translated), resourceKey)
      }
      assert.match(text, /observ-hermes\.json/)
      assert.match(text, /SpaceKey/)
      assert.match(text, /Bearer token/)
      assert.match(text, /read_file/)
      assert.ok(
        [...ui.element.querySelectorAll("code")].some(
          (code) =>
            code.textContent ===
            "make -C <repo>/ai-vision/agent-plugins/hermes install"
        )
      )
      assert.equal(
        ui.element.querySelector('a[href*="/guides/install-cli"]'),
        null
      )
      assert.deepEqual(hermesGuide, before)
    } finally {
      await ui.cleanup()
    }
  })
}

test("language changes update an already mounted guide without changing its command", async () => {
  const ui = await fixture()
  const guide = builtinGuide("codex", "Team Codex")
  try {
    await ui.render(true, guide, { agentName: "Team Codex" })
    let previousTitle = ""
    for (const language of ["zh-CN", "en-US", "ja-JP"]) {
      await ui.changeLanguage(language)
      const text = ui.element.textContent || ""
      const title = ui.t("agentAccess.guide.prerequisitesTitle")
      assert.ok(text.includes(title))
      if (previousTitle) assert.equal(text.includes(previousTitle), false)
      assert.ok(
        text.includes(
          ui.t("agentAccess.guide.verification.restartCodex", {
            name: "Team Codex",
          })
        )
      )
      assert.ok(
        [...ui.element.querySelectorAll("code")].some(
          (code) => code.textContent === connectCommand
        )
      )
      previousTitle = title
    }
  } finally {
    await ui.cleanup()
  }
})

test("unknown plugins, custom fields, and edited source instructions retain their saved text", async () => {
  const ui = await fixture("ja-JP")
  try {
    await ui.render(true, savedGuide, { agentName: "Codex" })
    for (const customText of [
      "已有接入前提",
      "已安装并能正常运行",
      "安装插件",
      "重启 Codex 并执行任务",
    ]) {
      assert.ok(ui.element.textContent?.includes(customText))
    }

    const unknown = builtinGuide("custom-plugin", "Custom Agent")
    await ui.render(true, unknown, { agentName: "Custom Agent" })
    assert.match(ui.element.textContent || "", /接入前提/)
    assert.match(
      ui.element.textContent || "",
      /已安装并配置好，能够调用模型和执行工具/
    )
    assert.match(
      ui.element.textContent || "",
      /重启 Custom Agent，执行一次包含模型调用和工具调用的任务/
    )
    assert.match(
      ui.element.textContent || "",
      /复制并执行安装命令，授权和上报配置将自动完成/
    )

    const edited = builtinGuide()
    const verification = edited.sections.find(
      (section) => section.type === "verification"
    )!
    const customStep =
      "打开当前空间的数据看板和任务轨迹，确认数据已上报；然后联系值班同事"
    verification.steps[1] = customStep
    await ui.render(true, edited, { agentName: "Codex" })
    assert.ok(ui.element.textContent?.includes(customStep))
    assert.ok(
      ui.element.textContent?.includes(
        ui.t("agentAccess.guide.prerequisitesTitle")
      )
    )

    await ui.render(false, hermesGuide, { agentProduct: "CustomHermes" })
    assert.match(
      ui.element.textContent || "",
      /已安装官方 hermes-agent 0\.18\.2，能够调用模型和执行工具/
    )
    assert.match(ui.element.textContent || "", /安装生产插件/)
  } finally {
    await ui.cleanup()
  }
})
