import { useEffect, useRef, useState } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useTranslation } from "react-i18next"
import { Loader2, MoreHorizontal, Pencil, Trash2 } from "lucide-react"
import { toast } from "sonner"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { DialogContent } from "@/components/dialogs/DialogContent"
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog"
import { AlertDialogContent } from "@/components/dialogs/AlertDialogContent"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import {
  deleteModelConnection,
  getModelConnectionTestErrorReason,
  getModelValidationErrorReason,
  listModelConnectionReferences,
  testModelConnection,
  validateModelConnection,
} from "./api"
import { ModelConnectionForm } from "./ModelConnectionForm"
import { ModelValidationDialog } from "./DiagnosticValidationDialog"
import type {
  ModelConnection,
  ModelConnectionTestErrorReason,
  ModelValidationOptions,
} from "./types"

export function ConnectionActions({
  connection,
  spaceId,
  canManage,
  compact = false,
  detail = false,
  onDeleted,
  onTestResult,
}: {
  connection: ModelConnection
  spaceId: string
  canManage: boolean
  compact?: boolean
  detail?: boolean
  onDeleted?: () => void
  onTestResult?: (error: ModelConnectionTestErrorReason | null) => void
}) {
  const { t } = useTranslation()
  const client = useQueryClient()
  const [editing, setEditing] = useState(false)
  const [deleting, setDeleting] = useState(false)
  const [probing, setProbing] = useState(false)
  const [validating, setValidating] = useState(false)
  const [probeError, setProbeError] =
    useState<ModelConnectionTestErrorReason | null>(null)
  const [validationError, setValidationError] = useState<string>()
  const [deleteError, setDeleteError] = useState(false)
  const mounted = useRef(true)
  useEffect(() => {
    mounted.current = true
    return () => {
      mounted.current = false
    }
  }, [])
  const refresh = () => {
    void client.invalidateQueries({ queryKey: ["model-connections", spaceId] })
    void client.invalidateQueries({
      queryKey: ["model-connection-references", spaceId, connection.uuid],
    })
    void client.invalidateQueries({ queryKey: ["diagnosers", spaceId] })
  }
  const saveResult = (result: ModelConnection) => {
    client.setQueryData<{ items: ModelConnection[]; total: number }>(
      ["model-connections", spaceId],
      (current) =>
        current
          ? {
              ...current,
              items: current.items.map((item) =>
                item.uuid === result.uuid ? result : item
              ),
            }
          : current
    )
    refresh()
  }
  const references = useQuery({
    queryKey: ["model-connection-references", spaceId, connection.uuid],
    queryFn: () => listModelConnectionReferences(spaceId, connection.uuid),
    enabled: deleting,
  })
  const probeMutation = useMutation({
    mutationFn: () => testModelConnection(spaceId, connection.uuid),
    onMutate: () => setProbeError(null),
    onSuccess: (result) => {
      if (!mounted.current) return
      saveResult(result)
      const reason =
        result.testError ??
        (result.status === "unavailable" ? "unavailable" : null)
      setProbeError(reason)
      onTestResult?.(reason)
      if (!reason) {
        toast.success(t("modelConnections.connected"))
        setProbing(false)
      }
    },
    onError: (error) => {
      if (!mounted.current) return
      const reason = getModelConnectionTestErrorReason(error)
      setProbeError(reason)
      onTestResult?.(reason)
      if (reason === "configuration_changed") refresh()
    },
  })
  const validationMutation = useMutation({
    mutationFn: (options: ModelValidationOptions) =>
      validateModelConnection(spaceId, connection.uuid, options),
    onMutate: () => setValidationError(undefined),
    onSuccess: (result) => {
      if (!mounted.current) return
      saveResult(result)
      if (!result.diagnosticValidation)
        setValidationError("invalid_validation_result")
      else if (result.diagnosticValidation.status === "passed")
        toast.success(t("modelConnections.validation.passed"))
      else if (result.diagnosticValidation.reason)
        toast.error(
          t(
            `modelConnections.validation.reasons.${result.diagnosticValidation.reason}`
          )
        )
    },
    onError: (error) => {
      if (!mounted.current) return
      const reason = getModelValidationErrorReason(error)
      setValidationError(reason)
      toast.error(t(`modelConnections.validation.reasons.${reason}`))
      if (reason === "connection_changed") refresh()
    },
  })
  const deleteMutation = useMutation({
    mutationFn: () => deleteModelConnection(spaceId, connection.uuid),
    onSuccess: () => {
      if (!mounted.current) return
      toast.success(t("modelConnections.deleted"))
      setDeleting(false)
      refresh()
      onDeleted?.()
    },
    onError: () => {
      if (!mounted.current) return
      setDeleteError(true)
      void references.refetch()
    },
  })
  const busy = probeMutation.isPending || validationMutation.isPending
  const actionItems = [
    { label: t("modelConnections.edit"), run: () => setEditing(true) },
    {
      label: t("modelConnections.connectionTest"),
      run: () => {
        setProbeError(null)
        setProbing(true)
      },
    },
    { label: t("modelConnections.test"), run: () => setValidating(true) },
    {
      label: t("modelConnections.delete"),
      run: () => {
        setDeleteError(false)
        setDeleting(true)
      },
    },
  ]
  return (
    <>
      {compact ? (
        <div className="flex items-center justify-end gap-1">
          <Button
            variant="outline"
            size="sm"
            className="h-7 px-2 text-xs"
            aria-label={t("modelConnections.test")}
            disabled={!canManage || busy}
            onClick={() => setValidating(true)}
          >
            {t("modelConnections.test")}
          </Button>
          <Button
            variant="ghost"
            size="icon"
            className="size-7"
            disabled={!canManage || busy}
            aria-label={t("modelConnections.editAria", {
              name: connection.name,
            })}
            onClick={() => setEditing(true)}
          >
            <Pencil className="size-3.5" />
          </Button>
          <Button
            variant="ghost"
            size="icon"
            className="size-7"
            disabled={!canManage || busy}
            aria-label={t("modelConnections.deleteAria", {
              name: connection.name,
            })}
            onClick={() => {
              setDeleteError(false)
              setDeleting(true)
            }}
          >
            <Trash2 className="size-3.5 text-destructive" />
          </Button>
        </div>
      ) : detail ? (
        <div className="flex flex-wrap gap-2">
          <Button
            variant="outline"
            disabled={!canManage || busy}
            onClick={() => {
              setProbeError(null)
              setProbing(true)
            }}
          >
            {t("modelConnections.connectionTest")}
          </Button>
          <Button
            variant="outline"
            disabled={!canManage || busy}
            onClick={() => setValidating(true)}
          >
            {t("modelConnections.test")}
          </Button>
          <Button
            disabled={!canManage || busy}
            onClick={() => setEditing(true)}
          >
            {t("modelConnections.edit")}
          </Button>
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button
                variant="outline"
                size="icon"
                disabled={!canManage || busy}
                aria-label={t("modelConnections.actionsAria", {
                  name: connection.name,
                })}
              >
                <MoreHorizontal className="size-4" />
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end">
              <DropdownMenuItem
                onSelect={() => {
                  setDeleteError(false)
                  setDeleting(true)
                }}
              >
                {t("modelConnections.delete")}
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        </div>
      ) : (
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button
              variant="ghost"
              size="icon"
              disabled={!canManage || busy}
              aria-label={t("modelConnections.actionsAria", {
                name: connection.name,
              })}
            >
              <MoreHorizontal className="size-4" />
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end">
            {actionItems.map((action) => (
              <DropdownMenuItem key={action.label} onSelect={action.run}>
                {action.label}
              </DropdownMenuItem>
            ))}
          </DropdownMenuContent>
        </DropdownMenu>
      )}
      <Dialog open={editing} onOpenChange={setEditing}>
        <DialogContent
          size="form"
          aria-describedby={undefined}
          className="overflow-y-auto"
        >
          {editing ? (
            <ModelConnectionForm
              spaceId={spaceId}
              editing={connection}
              onCancel={() => setEditing(false)}
              onSaved={(result) => {
                setEditing(false)
                setProbeError(null)
                setValidationError(undefined)
                onTestResult?.(null)
                saveResult(result)
              }}
            />
          ) : null}
        </DialogContent>
      </Dialog>
      {validating ? (
        <ModelValidationDialog
          connection={connection}
          pending={validationMutation.isPending}
          error={validationError}
          onRun={(options) => validationMutation.mutate(options)}
          onClose={() => setValidating(false)}
        />
      ) : null}
      <Dialog open={probing} onOpenChange={setProbing}>
        <DialogContent size="compact">
          <DialogHeader>
            <DialogTitle>{t("modelConnections.connectionTest")}</DialogTitle>
            <DialogDescription>
              {t("modelConnections.connectionTestDescription")}
            </DialogDescription>
          </DialogHeader>
          <p className="text-sm break-all">
            {connection.name} · {connection.modelName}
          </p>
          {probeError ? (
            <p role="alert" className="text-sm text-destructive">
              {t(`modelConnections.testErrors.${probeError}`)}
            </p>
          ) : null}
          <DialogFooter>
            <Button variant="outline" onClick={() => setProbing(false)}>
              {t("common.cancel")}
            </Button>
            <Button
              disabled={!canManage || probeMutation.isPending}
              onClick={() => probeMutation.mutate()}
            >
              {probeMutation.isPending ? (
                <Loader2 className="mr-2 size-4 animate-spin" />
              ) : null}
              {t("modelConnections.runConnectionTest")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
      <AlertDialog open={deleting} onOpenChange={setDeleting}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>
              {t(
                references.data?.length
                  ? "modelConnections.cannotDelete"
                  : "modelConnections.deleteTitle"
              )}
            </AlertDialogTitle>
            <AlertDialogDescription>
              {references.isLoading || references.isFetching
                ? t("modelConnections.checkingReferences")
                : references.isError
                  ? t("modelConnections.referencesFailed")
                  : references.data?.length
                    ? t("modelConnections.referenced", {
                        name: connection.name,
                      })
                    : t("modelConnections.deleteDescription", {
                        name: connection.name,
                      })}
            </AlertDialogDescription>
          </AlertDialogHeader>
          {references.data?.length ? (
            <ul className="max-h-40 space-y-2 overflow-y-auto rounded-lg border p-3 text-xs">
              {references.data.map((reference) => (
                <li key={reference.uuid} className="break-all">
                  {reference.name}
                </li>
              ))}
            </ul>
          ) : null}
          {deleteError ? (
            <p role="alert" className="text-sm text-destructive">
              {t("modelConnections.deleteFailed")}
            </p>
          ) : null}
          <AlertDialogFooter>
            <AlertDialogCancel>{t("common.cancel")}</AlertDialogCancel>
            {references.isError ? (
              <Button
                variant="outline"
                onClick={() => void references.refetch()}
              >
                {t("common.retry")}
              </Button>
            ) : !references.isLoading &&
              !references.isFetching &&
              references.data &&
              !references.data.length ? (
              <AlertDialogAction
                variant="destructive"
                disabled={!canManage || deleteMutation.isPending}
                onClick={(event) => {
                  event.preventDefault()
                  deleteMutation.mutate()
                }}
              >
                {t("spaceSettings.keys.confirmDelete")}
              </AlertDialogAction>
            ) : null}
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  )
}
