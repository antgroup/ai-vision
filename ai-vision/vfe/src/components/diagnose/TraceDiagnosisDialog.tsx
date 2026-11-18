import { useState } from "react"
import { useQuery } from "@tanstack/react-query"
import { Link, useNavigate } from "react-router-dom"
import { useTranslation } from "react-i18next"
import { X } from "lucide-react"
import { Dialog, DialogTitle } from "@/components/ui/dialog"
import { DialogContent } from "@/components/dialogs/DialogContent"
import { Button } from "@/components/ui/button"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { useProductGuide } from "@/components/product-guide/useProductGuide"
import { setAgentProductInSearch } from "@/agents/lib/agentDashboardRoute"
import { listModelConnections } from "@/features/model-connections/api"
import { SpecialistConfigurationForm } from "./diagnoser/SpecialistConfigurationForm"
import { TraceDiagnosisPanel } from "./TraceDiagnosisPanel"
import type { QuickRunResponse } from "./types"

/** Executes only the selection supplied by the trace surface; never lists candidates. */
export function TraceDiagnosisDialog({
  spaceId,
  agentUuid,
  agentProduct,
  userUuid,
  traceIds,
  onClose,
  onOpenTrace,
}: {
  spaceId: string
  agentUuid: string
  agentProduct: string
  userUuid: string
  traceIds: readonly string[]
  onClose: () => void
  onOpenTrace: (
    traceId: string,
    spanId?: string,
    createdAt?: string | null
  ) => void
}) {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const guide = useProductGuide()
  const [tab, setTab] = useState("quick")
  const [specialistOpened, setSpecialistOpened] = useState(false)
  const modelsQuery = useQuery({
    queryKey: ["model-connections", spaceId, "trace-specialist", userUuid],
    queryFn: () => listModelConnections(spaceId),
    enabled: specialistOpened,
    staleTime: 30_000,
    retry: false,
  })
  const [accepted, setAccepted] = useState<Extract<
    QuickRunResponse,
    { state: "accepted" }
  > | null>(null)
  const [denied, setDenied] = useState(false)
  const resultParams = new URLSearchParams({
    spaceId,
    agent: agentProduct,
    agent_uuid: agentUuid,
    view: "overview",
  })
  if (accepted) {
    resultParams.set("job", accepted.job_uuid)
    resultParams.set("task", accepted.task_uuid)
  }
  const resultSearch = (params: URLSearchParams) =>
    setAgentProductInSearch(params.toString(), agentProduct, { agentUuid })
  const resultsUrl = `/badcase?${resultSearch(resultParams)}`
  return (
    <Dialog
      open
      onOpenChange={(open) => {
        if (!open) onClose()
      }}
    >
      <DialogContent
        size="workspace"
        showCloseButton={false}
        aria-describedby={undefined}
        className="gap-3 overflow-y-auto rounded-xl p-4 sm:p-5"
      >
        <div className="flex items-center justify-between gap-3">
          <DialogTitle className="text-base leading-6">
            {t("diagnose.quick.title")}
          </DialogTitle>
          <Button
            variant="ghost"
            size="icon"
            className="size-8"
            aria-label={t("common.close")}
            onClick={onClose}
          >
            <X className="size-4" />
          </Button>
        </div>
        <Tabs
          value={tab}
          onValueChange={(value) => {
            setTab(value)
            if (value === "specialist") setSpecialistOpened(true)
          }}
          activationMode="manual"
          className="min-w-0 gap-3"
        >
          <TabsList aria-label={t("diagnose.quick.title")}>
            <TabsTrigger value="quick">
              {t("diagnose.quick.quickTab")}
            </TabsTrigger>
            <TabsTrigger value="specialist">
              {t("diagnose.quick.specialistTab")}
            </TabsTrigger>
          </TabsList>
          <TabsContent value="quick" forceMount hidden={tab !== "quick"}>
            <TraceDiagnosisPanel
              embedded
              spaceId={spaceId}
              agentUuid={agentUuid}
              agentProduct={agentProduct}
              userUuid={userUuid}
              traceIds={traceIds}
              taskUuid={accepted?.task_uuid}
              onAccepted={setAccepted}
              authorizationRevoked={denied}
              onDenied={() => setDenied(true)}
              onOpenRecord={(record, spanId, target, completedRuleSource) => {
                if (target === "trace") {
                  if (completedRuleSource && spanId)
                    guide?.openRuleEvidence(
                      record.diagnoser_uuid,
                      record.target_id,
                      spanId
                    )
                  onClose()
                  onOpenTrace(
                    record.target_id,
                    spanId,
                    record.target_created_at
                  )
                } else {
                  // Record links retain the immutable task and exact Agent instance.
                  const params = new URLSearchParams(resultParams)
                  params.set("detail", record.uuid)
                  navigate(`/badcase?${resultSearch(params)}`)
                }
              }}
            />
            {accepted && !denied ? (
              <div className="mt-3 text-right">
                <Link
                  className="text-xs text-primary underline underline-offset-4"
                  to={resultsUrl}
                >
                  {t("diagnose.quick.openQualityResults")}
                </Link>
              </div>
            ) : null}
          </TabsContent>
          <TabsContent
            value="specialist"
            forceMount
            hidden={tab !== "specialist"}
          >
            {specialistOpened ? (
              <>
                {modelsQuery.isPending && (
                  <p className="py-2 text-muted-foreground" role="status">
                    {t("modelConnections.loading")}
                  </p>
                )}
                {modelsQuery.isError && (
                  <div
                    className="flex flex-wrap items-center gap-2 text-muted-foreground"
                    role="status"
                  >
                    {t("modelConnections.loadFailed")}
                    <Button
                      variant="outline"
                      size="sm"
                      disabled={modelsQuery.isFetching}
                      onClick={() => void modelsQuery.refetch()}
                    >
                      {t("common.retry")}
                    </Button>
                  </div>
                )}
                <SpecialistConfigurationForm
                  open
                  spaceId={spaceId}
                  agentUuid={agentUuid}
                  traceIds={traceIds}
                  modelConnections={
                    modelsQuery.isError ? [] : (modelsQuery.data?.items ?? [])
                  }
                  onOpenSource={(traceId, spanId) => {
                    if (!traceIds.includes(traceId)) return
                    onClose()
                    onOpenTrace(traceId, spanId)
                  }}
                />
              </>
            ) : null}
          </TabsContent>
        </Tabs>
      </DialogContent>
    </Dialog>
  )
}
