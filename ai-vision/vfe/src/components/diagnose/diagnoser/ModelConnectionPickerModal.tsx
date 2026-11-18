import { useEffect, useMemo, useState } from "react"
import { useTranslation } from "react-i18next"
import { useMutation, useQueryClient } from "@tanstack/react-query"
import { Loader2, Settings2 } from "lucide-react"
import { toast } from "sonner"

import {
  Dialog,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { DialogContent } from "@/components/dialogs/DialogContent"
import { Button } from "@/components/ui/button"
import { ModelConnectionSelect } from "@/features/model-connections/ModelConnectionSelect"
import { useUser } from "@/contexts/UserContext"

import { ModelValidationSummary } from "../ModelValidationSummary"
import { updateDiagnoser } from "../diagnoseApi"
import type { ModelConnection } from "../types"
import { ModelConnectionManagerDialog } from "@/features/model-connections/ModelConnectionsSection"

interface ModelConnectionPickerTarget {
  uuid: string
  name: string
  spec: Record<string, unknown>
}

interface ModelConnectionPickerModalProps {
  open: boolean
  spaceId: string
  agentUuid: string
  target: ModelConnectionPickerTarget | null
  modelConnections: ModelConnection[]
  availableModelsOnly?: boolean
  onClose: () => void
}

function isUnavailable(connection: ModelConnection) {
  return connection.status !== "available" || !connection.apiKeyConfigured
}

export function ModelConnectionPickerModal({
  open,
  spaceId,
  agentUuid,
  target,
  modelConnections,
  availableModelsOnly = false,
  onClose,
}: ModelConnectionPickerModalProps) {
  const { t } = useTranslation()
  const statusLabel = (connection: ModelConnection) =>
    t(`spaceSettings.models.${connection.status}`)
  const queryClient = useQueryClient()
  const { user } = useUser()
  const [modelConnectionUuid, setModelConnectionUuid] = useState("")
  const [modelManagerOpen, setModelManagerOpen] = useState(false)

  /* eslint-disable react-hooks/set-state-in-effect -- 弹窗打开时同步当前诊断器的模型连接 */
  useEffect(() => {
    if (!open || !target) return
    setModelConnectionUuid(
      typeof target.spec.model_connection_uuid === "string"
        ? target.spec.model_connection_uuid
        : ""
    )
  }, [open, target])
  /* eslint-enable react-hooks/set-state-in-effect */

  const selectedConnection = useMemo(
    () =>
      modelConnections.find(
        (connection) => connection.uuid === modelConnectionUuid
      ) ?? null,
    [modelConnectionUuid, modelConnections]
  )
  const invalidSelection =
    availableModelsOnly &&
    !!modelConnectionUuid &&
    (!selectedConnection || isUnavailable(selectedConnection))

  const mutation = useMutation({
    mutationFn: () => {
      if (!target) throw new Error(t("spaceSettings.models.picker.notLoaded"))
      if (invalidSelection)
        throw new Error(t("spaceSettings.models.picker.availableOnly"))
      // Built-in 更新接口用 null 表示清除当前运行模型。
      return updateDiagnoser(
        spaceId,
        target.uuid,
        { spec: { model_connection_uuid: modelConnectionUuid || null } },
        agentUuid
      )
    },
    onSuccess: () => {
      toast.success(t("spaceSettings.models.picker.updated"))
      queryClient.invalidateQueries({ queryKey: ["diagnosers"] })
      queryClient.invalidateQueries({ queryKey: ["diagnoser"] })
      if (user?.user_uuid) {
        queryClient.invalidateQueries({
          queryKey: [
            "quick-options",
            `${user.user_uuid}:${spaceId}:${agentUuid}`,
          ],
          exact: true,
        })
      }
      onClose()
    },
    onError: () => toast.error(t("spaceSettings.models.picker.saveFailed")),
  })

  return (
    <>
      <Dialog open={open} onOpenChange={(next) => !next && onClose()}>
        <DialogContent size="form">
          <DialogHeader>
            <DialogTitle>{t("spaceSettings.models.picker.title")}</DialogTitle>
            <DialogDescription>
              {t("spaceSettings.models.picker.description", {
                name:
                  target?.name ?? t("spaceSettings.models.picker.fallbackName"),
              })}
            </DialogDescription>
          </DialogHeader>
          <div className="flex flex-col gap-3 py-2">
            <div className="flex items-center gap-2">
              <ModelConnectionSelect
                connections={modelConnections}
                value={modelConnectionUuid}
                onValueChange={setModelConnectionUuid}
                placeholder={t("spaceSettings.models.picker.choose")}
                noneLabel={t("spaceSettings.models.picker.none")}
                diagnosticValidation
                isConnectionDisabled={
                  availableModelsOnly ? isUnavailable : undefined
                }
              />
              <Button
                type="button"
                variant="outline"
                size="sm"
                className="h-8 shrink-0 gap-1 px-2 text-xs"
                onClick={() => setModelManagerOpen(true)}
                aria-label={t("spaceSettings.models.picker.manageAria")}
              >
                <Settings2 className="size-3.5" />
                {t("spaceSettings.models.picker.manage")}
              </Button>
            </div>
            {availableModelsOnly && (
              <p className="text-xs text-muted-foreground">
                {t("spaceSettings.models.picker.availableOnly")}
              </p>
            )}
            {selectedConnection ? (
              <p className="text-xs text-muted-foreground">
                {selectedConnection.endpoint} · {selectedConnection.modelName} ·{" "}
                {statusLabel(selectedConnection)}
              </p>
            ) : null}
            {selectedConnection ? (
              <ModelValidationSummary
                report={selectedConnection.diagnosticValidation}
                currentConfiguration
              />
            ) : null}
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={onClose}>
              {t("common.cancel")}
            </Button>
            <Button
              disabled={!target || invalidSelection || mutation.isPending}
              onClick={() => mutation.mutate()}
            >
              {mutation.isPending ? (
                <Loader2 className="mr-1.5 size-3.5 animate-spin" />
              ) : null}
              {t("common.save")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
      <ModelConnectionManagerDialog
        open={modelManagerOpen}
        spaceId={spaceId}
        onOpenChange={setModelManagerOpen}
        onConnectionCreated={(connection) => {
          if (!availableModelsOnly || !isUnavailable(connection))
            setModelConnectionUuid(connection.uuid)
        }}
      />
    </>
  )
}
