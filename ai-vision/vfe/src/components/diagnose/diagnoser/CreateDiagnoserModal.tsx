import { useState } from "react"
import { useTranslation } from "react-i18next"
import { Sparkles, Code2 } from "lucide-react"

import { Sheet, SheetContent, SheetTitle } from "@/components/ui/sheet"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import { SpecialistConfigurationForm } from "./SpecialistConfigurationForm"
import { CreateLlmDiagnoserModal } from "./CreateLlmDiagnoserModal"
import { CreateRuleDiagnoserForm } from "./CreateRuleDiagnoserForm"
import type { ModelConnection } from "../types"

export type DiagnoserEditTarget = {
  mode: "llm" | "rule"
  uuid: string
  name: string
  description: string
  spec: Record<string, unknown>
  writePython?: boolean
}

interface CreateDiagnoserModalProps {
  open: boolean
  onClose: () => void
  spaceId: string
  agentUuid: string
  existingScenarios: string[]
  modelConnections: ModelConnection[]
  canCreateRuleDiagnoser: boolean
  editing?: DiagnoserEditTarget | null
}

export function CreateDiagnoserModal({
  open,
  onClose,
  spaceId,
  agentUuid,
  existingScenarios,
  modelConnections,
  canCreateRuleDiagnoser,
  editing,
}: CreateDiagnoserModalProps) {
  const { t } = useTranslation()
  const [activeTab, setActiveTab] = useState<"llm" | "rule" | "specialist">(
    editing?.mode ?? "llm"
  )

  return (
    <Sheet
      open={open}
      onOpenChange={(next) => {
        if (next) return
        setActiveTab("llm")
        onClose()
      }}
    >
      <SheetContent
        aria-describedby={undefined}
        className="flex w-[calc(100vw-24px)] max-w-[calc(100vw-24px)] flex-col gap-0 overflow-hidden p-0 data-[side=right]:!w-[calc(100vw-24px)] data-[side=right]:!max-w-[calc(100vw-24px)] lg:w-[clamp(800px,52vw,1080px)] lg:max-w-[clamp(800px,52vw,1080px)] lg:data-[side=right]:!w-[clamp(800px,52vw,1080px)] lg:data-[side=right]:!max-w-[clamp(800px,52vw,1080px)]"
      >
        <div className="shrink-0 border-b px-6 py-4 pr-14">
          <SheetTitle>
            {editing?.writePython
              ? t("diagnose.diagnoser.ruleManagement.writePython")
              : editing
                ? t("diagnose.diagnoser.form.editTitle")
                : t("diagnose.diagnoser.createTitle")}
          </SheetTitle>
        </div>
        <Tabs
          value={editing?.mode ?? activeTab}
          onValueChange={(value) => {
            setActiveTab(value as "llm" | "rule" | "specialist")
          }}
          className="flex min-h-0 min-w-0 flex-1 flex-col gap-0"
        >
          <TabsList className="mx-6 mt-5 h-9 w-fit shrink-0 gap-0.5 rounded-xl p-0.5 group-data-horizontal/tabs:h-9">
            <TabsTrigger
              value="llm"
              disabled={!!editing && editing.mode !== "llm"}
              className="h-8 min-w-32 flex-none gap-1.5 rounded-lg px-4 py-0 data-active:border-border! data-active:shadow-xs"
            >
              <Sparkles className="size-3.5" />
              {t("diagnose.diagnoser.createTab.llm", {
                defaultValue: "LLM 诊断器",
              })}
            </TabsTrigger>
            {!editing && !canCreateRuleDiagnoser ? (
              <Tooltip>
                <TooltipTrigger asChild>
                  <span className="inline-flex cursor-not-allowed">
                    <TabsTrigger
                      value="rule"
                      disabled
                      className="h-8 min-w-32 flex-none gap-1.5 rounded-lg px-4 py-0 data-active:border-border! data-active:shadow-xs"
                    >
                      <Code2 className="size-3.5" />
                      {t("diagnose.diagnoser.createTab.rule", {
                        defaultValue: "规则诊断器",
                      })}
                    </TabsTrigger>
                  </span>
                </TooltipTrigger>
                <TooltipContent>
                  {t("diagnose.diagnoser.form.ruleUnavailable")}
                </TooltipContent>
              </Tooltip>
            ) : (
              <TabsTrigger
                value="rule"
                disabled={!!editing && editing.mode !== "rule"}
                className="h-8 min-w-32 flex-none gap-1.5 rounded-lg px-4 py-0 data-active:border-border! data-active:shadow-xs"
              >
                <Code2 className="size-3.5" />
                {t("diagnose.diagnoser.createTab.rule", {
                  defaultValue: "规则诊断器",
                })}
              </TabsTrigger>
            )}
            {!editing && (
              <TabsTrigger
                value="specialist"
                className="h-8 min-w-32 flex-none rounded-lg px-4 py-0"
              >
                {t("diagnose.specialist.tab")}
              </TabsTrigger>
            )}
          </TabsList>
          <TabsContent
            value="llm"
            className="m-0 flex min-h-0 min-w-0 flex-1 flex-col"
          >
            <CreateLlmDiagnoserModal
              inline
              open={open}
              spaceId={spaceId}
              agentUuid={agentUuid}
              editing={editing?.mode === "llm" ? editing : null}
              existingScenarios={existingScenarios}
              modelConnections={modelConnections}
              onClose={onClose}
            />
          </TabsContent>
          <TabsContent
            value="rule"
            className="m-0 flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden"
          >
            <CreateRuleDiagnoserForm
              open={open}
              spaceId={spaceId}
              agentUuid={agentUuid}
              editing={editing?.mode === "rule" ? editing : null}
              existingScenarios={existingScenarios}
              onClose={onClose}
            />
          </TabsContent>
          {!editing && (
            <TabsContent
              value="specialist"
              className="m-0 flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden"
            >
              <SpecialistConfigurationForm
                open={open && activeTab === "specialist"}
                spaceId={spaceId}
                agentUuid={agentUuid}
                modelConnections={modelConnections}
              />
            </TabsContent>
          )}
        </Tabs>
      </SheetContent>
    </Sheet>
  )
}
