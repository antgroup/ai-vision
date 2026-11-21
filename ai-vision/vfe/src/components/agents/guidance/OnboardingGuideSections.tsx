import { useTranslation } from "react-i18next"
import type { ConfigGuideV2 } from "./types"
import { SectionRenderer } from "./SectionRenderer"
import { SetupStepsSection } from "./SetupStepsSection"
import { localizeGuideSections } from "./localizeGuideSections"

interface Props {
  guide: ConfigGuideV2
  hasInstallationCommand: boolean
  agentName?: string
  agentProduct?: string
  readOnly?: boolean
}

export function OnboardingGuideSections({
  guide,
  hasInstallationCommand,
  agentName,
  agentProduct,
  readOnly = false,
}: Props) {
  const { t, i18n } = useTranslation()
  const docsLanguage = i18n.language.startsWith("zh") ? "zh-CN" : "en"
  const sections = localizeGuideSections(guide, t, agentName, agentProduct)

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
            section={{
              type: "setup_steps",
              title: t("agentAccess.cliInstall.title"),
              platforms: {
                "macOS/Linux": {
                  modes: {
                    cli: {
                      label: "CLI",
                      steps: [
                        {
                          title: t("agentAccess.cliInstall.check"),
                          command:
                            'export PATH="$HOME/.local/bin:$PATH"\ncommand -v aiv && aiv --version',
                        },
                      ],
                    },
                  },
                },
                Windows: {
                  modes: {
                    cli: {
                      label: "PowerShell",
                      steps: [
                        {
                          title: t("agentAccess.cliInstall.checkWindows"),
                          command: "Get-Command aiv\naiv --version",
                        },
                      ],
                    },
                  },
                },
              },
            }}
          />
          <p className="text-xs text-muted-foreground">
            {t("agentAccess.cliInstall.pathHint")}
          </p>
          {!readOnly && (
            <p className="text-sm text-muted-foreground">
              {t("agentAccess.cliInstall.ready")}
            </p>
          )}
        </div>
      )}
      {sections
        .filter(
          (section) =>
            !guide.plugin_key ||
            section.type !== "setup_steps" ||
            hasInstallationCommand
        )
        .map((section, index) => (
          <SectionRenderer key={`${section.type}-${index}`} section={section} />
        ))}
    </>
  )
}
