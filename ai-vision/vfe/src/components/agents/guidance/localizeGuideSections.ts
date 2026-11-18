import type { TFunction } from "i18next"
import type { ConfigGuideV2, GuideSection } from "./types"

const BUILTIN_PLUGIN_KEYS = new Set([
  "claude-code",
  "codex",
  "kimi",
  "opencode",
  "openclaw",
  "pi",
  "dsh",
])

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
  if (
    !BUILTIN_PLUGIN_KEYS.has(guide.plugin_key ?? "") &&
    !(!guide.plugin_key && agentProduct === "Hermes")
  ) {
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
            Object.entries(section.platforms).map(([platform, config]) => [
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
