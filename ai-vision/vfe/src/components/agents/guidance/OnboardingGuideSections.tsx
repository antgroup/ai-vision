import { useTranslation } from "react-i18next"
import type { ConfigGuideV2 } from "./types"
import { SectionRenderer } from "./SectionRenderer"
import { SetupStepsSection } from "./SetupStepsSection"
import {
  localizeGuideSections,
  onboardingPlatforms,
  supportsPluginInstallation,
} from "./localizeGuideSections"
import { PLUGIN_HOSTS } from "./pluginHosts"

const AIV_CHECK =
  'export PATH="$HOME/.local/bin:$PATH"\ncommand -v aiv && aiv --version'
const WINDOWS_AIV_CHECK = ["Get-Command aiv", "aiv --version"].join("\n")
const HOST_PATTERN = /^[A-Za-z0-9][A-Za-z0-9._-]*$/

interface Props {
  guide: ConfigGuideV2
  hasInstallationCommand: boolean
  platform: string
  onPlatformChange: (platform: string) => void
  agentName?: string
  agentProduct?: string
  readOnly?: boolean
}

function resolveHost(guide: ConfigGuideV2): string {
  const configured = guide.host?.trim() ?? ""
  const candidate = configured || PLUGIN_HOSTS[guide.plugin_key ?? ""] || ""
  return HOST_PATTERN.test(candidate) ? candidate : ""
}

export function OnboardingGuideSections({
  guide,
  hasInstallationCommand,
  platform,
  onPlatformChange,
  agentName,
  agentProduct,
  readOnly = false,
}: Props) {
  const { t, i18n } = useTranslation()
  const docsLanguage = i18n.language.startsWith("zh") ? "zh-CN" : "en"
  const host = resolveHost(guide)
  const isWindows = platform === "Windows"
  const canInstall = supportsPluginInstallation(guide, platform)
  const sections = localizeGuideSections(guide, t, agentName, agentProduct)
  const unixCheckCommand = host
    ? `${AIV_CHECK}\ncommand -v ${host} && ${host} --version`
    : AIV_CHECK
  const windowsCheckCommand = host
    ? `${WINDOWS_AIV_CHECK}\nGet-Command ${host}\n${host} --version`
    : WINDOWS_AIV_CHECK
  const agentLabel = agentName || agentProduct || guide.plugin_key || host
  const readyText = host
    ? isWindows
      ? t("agentAccess.cliInstall.readyWithHostWindows", { host })
      : t("agentAccess.cliInstall.readyWithHost", { host })
    : isWindows
      ? t("agentAccess.cliInstall.readyWindows")
      : t("agentAccess.cliInstall.ready")

  return (
    <>
      {guide.plugin_key && (
        <div className="space-y-3 rounded-lg border bg-muted/30 p-4">
          <p className="text-sm text-muted-foreground">
            {t("agentAccess.cliInstall.description")}
          </p>
          <a
            href={`https://aivision.mintlify.site/${docsLanguage}/guides/install-cli`}
            target="_blank"
            rel="noopener noreferrer"
            className="text-sm font-medium text-primary underline underline-offset-4"
          >
            {t("agentAccess.cliInstall.guide")}
          </a>
          <SetupStepsSection
            platform={platform}
            onPlatformChange={onPlatformChange}
            section={{
              type: "setup_steps",
              title: t("agentAccess.cliInstall.title"),
              platforms: Object.fromEntries(
                onboardingPlatforms(guide).map((name) => [
                  name,
                  {
                    modes: {
                      cli: {
                        label: "CLI",
                        steps: [
                          {
                            title: t(
                              name === "Windows" && isWindows && !canInstall
                                ? "agentAccess.cliInstall.checkWindowsRemote"
                                : "agentAccess.cliInstall.check"
                            ),
                            command:
                              name === "Windows"
                                ? windowsCheckCommand
                                : unixCheckCommand,
                          },
                        ],
                      },
                    },
                  },
                ])
              ),
            }}
          />
          <p className="text-xs text-muted-foreground">
            {isWindows
              ? t("agentAccess.cliInstall.pathHintWindows")
              : t("agentAccess.cliInstall.pathHint")}
          </p>
          {isWindows && !canInstall && (
            <p className="text-sm text-muted-foreground">
              {t("agentAccess.cliInstall.windowsRemoteOnly")}
            </p>
          )}
          {!readOnly && canInstall && (
            <p className="text-sm text-muted-foreground">{readyText}</p>
          )}
          {!readOnly && canInstall && host && (
            <p className="text-sm text-muted-foreground">
              {isWindows
                ? t("agentAccess.cliInstall.hostNotFoundWindows", {
                    host,
                    agent: agentLabel,
                  })
                : t("agentAccess.cliInstall.hostNotFound", {
                    host,
                    agent: agentLabel,
                  })}
            </p>
          )}
        </div>
      )}
      {sections.map((section, index) => {
        if (guide.plugin_key && section.type === "setup_steps") {
          const setup = section.platforms[platform]
          if (!hasInstallationCommand || !canInstall || !setup) return null
          return (
            <SectionRenderer
              key={`${section.type}-${index}-${platform}`}
              section={{ ...section, platforms: { [platform]: setup } }}
            />
          )
        }
        return (
          <SectionRenderer key={`${section.type}-${index}`} section={section} />
        )
      })}
    </>
  )
}
