import type { TFunction } from "i18next"
import type {
  ConfigGuideV2,
  GuideSection,
  PlatformConfig,
  SetupStepsSection,
} from "./types"

const BUILTIN_PLUGIN_KEYS = new Set([
  "claude-code",
  "codex",
  "kimi",
  "opencode",
  "openclaw",
  "pi",
  "dsh",
])

function installPlatforms(guide: ConfigGuideV2, section: SetupStepsSection) {
  const platforms = Object.entries(section.platforms)
  if (!BUILTIN_PLUGIN_KEYS.has(guide.plugin_key ?? "")) return platforms

  return platforms.flatMap(([name, config]) => {
    if (name !== "Windows") return [[name, config] as const]

    // Remove unsupported CLI steps without discarding other saved instructions.
    const modes = Object.entries(config.modes)
      .map(([key, mode]) => {
        const steps = mode.steps.filter(({ command }) => {
          const [cli, action, plugin] = command.trimStart().split(/\s+/, 3)
          return !(
            cli === "aiv" &&
            action === "connect" &&
            plugin === guide.plugin_key
          )
        })
        return [key, { ...mode, steps }] as const
      })
      .filter(([, mode]) => mode.steps.length > 0)
    return modes.length
      ? [[name, { modes: Object.fromEntries(modes) }] as const]
      : []
  })
}

function hasInstallSteps({ modes }: PlatformConfig) {
  return Object.values(modes).some(({ steps }) => steps.length > 0)
}

export function onboardingPlatforms(guide?: ConfigGuideV2) {
  const unixTargets = new Set<string>()
  for (const section of guide?.sections ?? []) {
    if (section.type !== "setup_steps") continue
    for (const [name, config] of Object.entries(section.platforms)) {
      if (name !== "Windows" && hasInstallSteps(config)) unixTargets.add(name)
    }
  }
  if (!unixTargets.size) unixTargets.add("macOS/Linux")
  return [...unixTargets, "Windows"]
}

export function supportsPluginInstallation(
  guide: ConfigGuideV2,
  platform: string
) {
  return guide.sections.some(
    (section) =>
      section.type === "setup_steps" &&
      installPlatforms(guide, section).some(
        ([name, config]) => name === platform && hasInstallSteps(config)
      )
  )
}

const GUIDE_TEXT_KEYS = [
  "prerequisitesTitle",
  "verificationTitle",
  "requirements.cli",
  "requirements.agent",
  "requirements.python",
  "requirements.node",
  "requirements.hermes",
  "requirements.sourceLabel",
  "requirements.source",
  "verification.restart",
  "verification.restartCodex",
  "verification.checkData",
  "verification.configDir",
  "verification.restartHermes",
  "verification.checkHermes",
  "setup.installOnHost",
  "setup.nativeMode",
  "setup.runCommand",
  "setup.installProduction",
  "setup.sourceMode",
  "setup.hermesEnvironment",
  "setup.hermesEndpoints",
] as const

// Backend-seeded and previously saved guides still contain these published
// strings. Keep exact aliases when presentation copy changes; edited/custom
// instructions must never be translated by a fuzzy match.
const PUBLISHED_SOURCE_ALIASES = [
  [
    "重启 {{name}}，输入 /hooks 检查并信任新增的 AI Vision hooks，执行一次包含模型调用和工具调用的任务",
    "agentAccess.guide.verification.restartCodex",
  ],
  [
    "按同目录 README 设置 observ-hermes.json 的 OTLP 三信号端点。使用当前空间的 SpaceKey 申请短期 ingest token，仅将 Bearer token 配置为上报授权；不要把 SpaceKey 当作 gateway token。",
    "agentAccess.guide.setup.hermesEndpoints",
  ],
] as const

export function localizeGuideSections(
  guide: ConfigGuideV2,
  t: TFunction,
  agentName?: string,
  agentProduct?: string
): GuideSection[] {
  const builtinPlugin = BUILTIN_PLUGIN_KEYS.has(guide.plugin_key ?? "")
  if (!builtinPlugin && !(!guide.plugin_key && agentProduct === "Hermes")) {
    return guide.sections
  }

  // Saved v2 guides contain Chinese copy without translation IDs. Match only
  // published source text so customized instructions remain authoritative.
  const keysBySource = new Map(
    GUIDE_TEXT_KEYS.map((suffix) => {
      const key = `agentAccess.guide.${suffix}`
      return [t(key, { lng: "zh-CN", name: agentName ?? "" }), key]
    })
  )
  for (const [source, key] of PUBLISHED_SOURCE_ALIASES) {
    keysBySource.set(source.replace("{{name}}", agentName ?? ""), key)
  }
  const translate = (text: string) => {
    const key = keysBySource.get(text)
    return key ? t(key, { name: agentName ?? "", defaultValue: text }) : text
  }

  return guide.sections.map((section): GuideSection => {
    switch (section.type) {
      case "prerequisites":
        return {
          ...section,
          title: translate(section.title),
          items: section.items.map((item) => ({
            ...item,
            label: translate(item.label),
            value: translate(item.value),
          })),
        }
      case "verification":
        return {
          ...section,
          title: translate(section.title),
          steps: section.steps.map(translate),
        }
      case "setup_steps":
        return {
          ...section,
          title: translate(section.title),
          platforms: Object.fromEntries(
            installPlatforms(guide, section).map(([platform, config]) => [
              platform,
              {
                ...config,
                modes: Object.fromEntries(
                  Object.entries(config.modes).map(([mode, setup]) => [
                    mode,
                    {
                      ...setup,
                      label: translate(setup.label),
                      steps: setup.steps.map((step) => ({
                        ...step,
                        title: translate(step.title),
                      })),
                    },
                  ])
                ),
              },
            ])
          ),
        }
      default:
        return section
    }
  })
}
