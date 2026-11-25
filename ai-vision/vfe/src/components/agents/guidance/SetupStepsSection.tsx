import { toast } from "sonner"
import { writeClipboardText } from "@/lib/clipboard"
import { useState, useCallback } from "react"
import { useTranslation } from "react-i18next"
import { Check, Copy, Monitor, Terminal } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { detectPlatform } from "./types"
import type {
  SetupStepsSection as SetupStepsSectionType,
  SetupStepItem,
} from "./types"

interface Props {
  section: SetupStepsSectionType
}

function StepCommand({ step, index }: { step: SetupStepItem; index: number }) {
  const { t } = useTranslation()
  const [copied, setCopied] = useState(false)

  const handleCopy = useCallback(async () => {
    try {
      await writeClipboardText(step.command)
    } catch {
      setCopied(false)
      toast.error(t("common.copyFailed"))
      return
    }
    setCopied(true)
    setTimeout(() => setCopied(false), 2000)
  }, [step.command, t])

  return (
    <div className="space-y-1.5">
      <p className="text-sm font-normal text-foreground">
        {index + 1}. {step.title}
      </p>
      <div className="flex items-start gap-2 rounded-lg border bg-muted p-3">
        <code className="flex-1 text-sm break-all whitespace-pre-wrap text-foreground">
          {step.command}
        </code>
        <Button
          variant="ghost"
          size="icon"
          className="size-7 shrink-0"
          onClick={handleCopy}
        >
          {copied ? (
            <Check className="size-3.5 text-emerald-500" />
          ) : (
            <Copy className="size-3.5" />
          )}
          <span className="sr-only">
            {copied ? t("common.copied") : t("agentAccess.copyCommand")}
          </span>
        </Button>
      </div>
    </div>
  )
}

export function SetupStepsSection({ section }: Props) {
  const { t } = useTranslation()
  const platformNames = Object.keys(section.platforms)
  const defaultPlatform =
    platformNames.find((platform) => platform === detectPlatform()) ||
    platformNames[0]
  const [activePlatform, setActivePlatform] = useState(defaultPlatform)

  const platformConfig = section.platforms[activePlatform]
  const modeNames = platformConfig ? Object.keys(platformConfig.modes) : []
  const [activeMode, setActiveMode] = useState(
    modeNames.includes("cli") ? "cli" : modeNames[0] || ""
  )

  const handlePlatformChange = (platform: string) => {
    setActivePlatform(platform)
    const newModes = Object.keys(section.platforms[platform]?.modes || {})
    if (!newModes.includes(activeMode)) {
      setActiveMode(newModes.includes("cli") ? "cli" : newModes[0] || "")
    }
  }

  return (
    <div className="space-y-3">
      <h4 className="flex items-center gap-1.5 text-sm font-medium text-foreground">
        <Terminal className="size-4" />
        {section.title || t("agentAccess.guide.setupTitle")}
      </h4>

      <Tabs value={activePlatform} onValueChange={handlePlatformChange}>
        <TabsList className="h-9 w-full items-stretch rounded-lg p-1">
          {platformNames.map((platform) => (
            <TabsTrigger
              key={platform}
              value={platform}
              className="h-7 flex-1 gap-1.5 rounded-md py-0"
            >
              <Monitor className="size-3.5" />
              {platform}
            </TabsTrigger>
          ))}
        </TabsList>

        {platformNames.map((platform) => (
          <TabsContent
            key={platform}
            value={platform}
            className="mt-3 space-y-3"
          >
            {modeNames.length > 1 && (
              <Tabs value={activeMode} onValueChange={setActiveMode}>
                <TabsList className="h-9 w-full items-stretch rounded-lg p-1">
                  {Object.entries(section.platforms[platform]?.modes || {})
                    .sort(([keyA], [keyB]) => keyA.localeCompare(keyB))
                    .map(([modeName, mode]) => (
                      <TabsTrigger
                        key={modeName}
                        value={modeName}
                        className="h-7 flex-1 rounded-md py-0"
                      >
                        {mode.label}
                      </TabsTrigger>
                    ))}
                </TabsList>
              </Tabs>
            )}

            <div className="space-y-3">
              {(
                section.platforms[platform]?.modes[activeMode]?.steps || []
              ).map((step, stepIndex) => (
                <StepCommand
                  key={`${platform}-${activeMode}-${stepIndex}`}
                  step={step}
                  index={stepIndex}
                />
              ))}
            </div>
          </TabsContent>
        ))}
      </Tabs>
    </div>
  )
}
