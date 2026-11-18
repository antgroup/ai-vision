import { useState } from "react"
import { useTranslation } from "react-i18next"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { Loader2, Plus } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { Dialog } from "@/components/ui/dialog"
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
import { SandboxConnectionForm } from "./SandboxConnectionForm"
import {
  deleteSandboxConnection,
  getSandboxBinding,
  listSandboxConnections,
  testSandboxConnection,
  updateSandboxBinding,
  updateSandboxConnection,
} from "./api"
import type { SandboxBinding, SandboxConnection } from "./types"

function BindingEditor({
  spaceId,
  binding,
  connections,
  canManage,
}: {
  spaceId: string
  binding: SandboxBinding
  connections: SandboxConnection[]
  canManage: boolean
}) {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const [connectionUuid, setConnectionUuid] = useState(
    binding.connection_uuid ?? ""
  )
  const [required, setRequired] = useState(binding.require_sandbox)
  const mutation = useMutation({
    mutationFn: () =>
      updateSandboxBinding(spaceId, {
        connection_uuid: connectionUuid || null,
        require_sandbox: required,
      }),
    onSuccess: () =>
      queryClient.invalidateQueries({ queryKey: ["sandbox-binding", spaceId] }),
  })
  const selected = connections.find(
    (connection) => connection.uuid === connectionUuid
  )
  return (
    <form
      className="space-y-4"
      onSubmit={(event) => {
        event.preventDefault()
        mutation.mutate()
      }}
    >
      <div className="space-y-1.5">
        <label htmlFor="sandbox-default" className="text-sm font-medium">
          {t("sandbox.defaultConnection")}
        </label>
        <select
          id="sandbox-default"
          className="h-9 w-full rounded-md border bg-background px-3 text-sm focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
          value={connectionUuid}
          disabled={!canManage || mutation.isPending}
          onChange={(event) => setConnectionUuid(event.target.value)}
        >
          <option value="">{t("sandbox.noDefault")}</option>
          {connectionUuid && !selected && (
            <option value={connectionUuid}>
              {t("sandbox.missingConnection")}
            </option>
          )}
          {connections.map((connection) => (
            <option key={connection.uuid} value={connection.uuid}>
              {connection.name} · {t(`sandbox.status.${connection.status}`)}
            </option>
          ))}
        </select>
      </div>
      <p className="text-xs leading-relaxed text-muted-foreground">
        {t("sandbox.bindingHelp")}
      </p>
      {selected && selected.status !== "ready" && (
        <p role="status" className="text-sm text-muted-foreground">
          {t("sandbox.selectedUnavailable")}
        </p>
      )}
      <label className="flex items-start gap-2 text-sm">
        <input
          className="mt-0.5 size-4 accent-primary"
          type="checkbox"
          checked={required}
          disabled={!canManage || mutation.isPending}
          onChange={(event) => setRequired(event.target.checked)}
        />
        <span>
          {t("sandbox.requireSandbox")}
          <span className="mt-1 block text-xs leading-relaxed text-muted-foreground">
            {t("sandbox.requireHelp")}
          </span>
        </span>
      </label>
      {mutation.isError && (
        <p role="alert" className="text-sm text-destructive">
          {t("sandbox.saveError")}
        </p>
      )}
      {canManage && (
        <Button
          type="submit"
          size="sm"
          disabled={
            mutation.isPending ||
            (connectionUuid === (binding.connection_uuid ?? "") &&
              required === binding.require_sandbox)
          }
        >
          {t(mutation.isPending ? "sandbox.saving" : "sandbox.saveBinding")}
        </Button>
      )}
    </form>
  )
}

export function SandboxBindingSection({
  spaceId,
  canManage,
}: {
  spaceId: string
  canManage: boolean
}) {
  const { t } = useTranslation()
  const connections = useQuery({
    queryKey: ["sandbox-connections", spaceId],
    queryFn: () => listSandboxConnections(spaceId),
  })
  const binding = useQuery({
    queryKey: ["sandbox-binding", spaceId],
    queryFn: () => getSandboxBinding(spaceId),
  })
  return (
    <section className="space-y-5" data-testid="sandbox-binding">
      <h2 className="text-base font-semibold">{t("plugins.policyTitle")}</h2>
      {(binding.isLoading || connections.isLoading) && (
        <p role="status" className="text-sm text-muted-foreground">
          {t("common.loading")}
        </p>
      )}
      {binding.isError && (
        <ErrorState
          onRetry={() => void binding.refetch()}
          text={t("sandbox.bindingError")}
        />
      )}
      {connections.isError && (
        <ErrorState
          onRetry={() => void connections.refetch()}
          text={t("sandbox.loadError")}
        />
      )}
      {binding.data && connections.data && (
        <BindingEditor
          key={`${spaceId}:${binding.data.connection_uuid}:${binding.data.require_sandbox}`}
          spaceId={spaceId}
          binding={binding.data}
          connections={connections.data.items}
          canManage={canManage}
        />
      )}
    </section>
  )
}

export function SandboxConnectionsSection({
  spaceId,
  canManage,
  createPrefill,
  onCreateClosed,
}: {
  spaceId: string
  canManage: boolean
  createPrefill?: { name: string; image: string } | null
  onCreateClosed?: () => void
}) {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const connections = useQuery({
    queryKey: ["sandbox-connections", spaceId],
    queryFn: () => listSandboxConnections(spaceId),
  })
  const binding = useQuery({
    queryKey: ["sandbox-binding", spaceId],
    queryFn: () => getSandboxBinding(spaceId),
  })
  const [form, setForm] = useState<SandboxConnection | "new" | null>(null)
  const [deleteTarget, setDeleteTarget] = useState<SandboxConnection | null>(
    null
  )
  const [testResult, setTestResult] = useState<SandboxConnection | null>(null)
  const refresh = async () => {
    await Promise.allSettled([
      queryClient.invalidateQueries({
        queryKey: ["sandbox-connections", spaceId],
      }),
      queryClient.invalidateQueries({ queryKey: ["sandbox-binding", spaceId] }),
    ])
  }
  const mutation = useMutation({
    mutationFn: async ({
      action,
      connection,
    }: {
      action: "test" | "disable" | "enable" | "delete"
      connection: SandboxConnection
    }) => {
      if (action === "test")
        return testSandboxConnection(spaceId, connection.uuid)
      if (action === "delete")
        return deleteSandboxConnection(spaceId, connection.uuid)
      return updateSandboxConnection(spaceId, connection.uuid, {
        status: action === "disable" ? "disabled" : "untested",
      })
    },
    onSuccess: async (result, variables) => {
      if (variables.action === "test" && result) setTestResult(result)
      if (variables.action === "delete") setDeleteTarget(null)
      await refresh()
    },
  })
  return (
    <div className="space-y-6" data-testid="sandbox-integrations">
      <section className="space-y-5">
        <header className="flex flex-wrap items-center justify-between gap-3 border-b pb-4">
          <h2 className="text-base font-semibold">
            {t("sandbox.connections")}
          </h2>
          {canManage && (
            <Button
              size="sm"
              onClick={() => setForm("new")}
              disabled={connections.isLoading}
            >
              <Plus className="size-4" />
              {t("sandbox.addConnection")}
            </Button>
          )}
        </header>
        <div className="space-y-4">
          <p className="max-w-3xl text-sm leading-relaxed text-muted-foreground">
            {t("sandbox.connectionHelp")}
          </p>
          {!canManage && (
            <p className="text-xs text-muted-foreground">
              {t("sandbox.readOnly")}
            </p>
          )}
          {connections.isLoading && (
            <p
              role="status"
              className="flex items-center gap-2 text-sm text-muted-foreground"
            >
              <Loader2 className="size-4 animate-spin" />
              {t("common.loading")}
            </p>
          )}
          {connections.isError && (
            <ErrorState
              onRetry={() => void connections.refetch()}
              text={t("sandbox.loadError")}
            />
          )}
          {binding.isError && (
            <ErrorState
              onRetry={() => void binding.refetch()}
              text={t("sandbox.bindingError")}
            />
          )}
          {connections.data?.items.length === 0 && (
            <p className="rounded-md border border-dashed p-5 text-sm text-muted-foreground">
              {t("sandbox.empty")}
            </p>
          )}
          {connections.data?.items.map((connection) => (
            <article
              key={connection.uuid}
              className="space-y-4 rounded-lg border px-5 py-5 sm:px-6"
              data-testid="sandbox-connection"
            >
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div className="min-w-0 space-y-1">
                  <h3 className="text-sm font-medium break-words">
                    {connection.name}
                  </h3>
                  <p className="text-xs break-all text-muted-foreground">
                    {connection.endpoint}
                  </p>
                </div>
                <div className="flex flex-wrap gap-2">
                  <Badge
                    variant={
                      connection.status === "ready" ? "secondary" : "outline"
                    }
                  >
                    {t(`sandbox.status.${connection.status}`)}
                  </Badge>
                  {binding.data?.connection_uuid === connection.uuid && (
                    <Badge variant="outline">{t("sandbox.bound")}</Badge>
                  )}
                </div>
              </div>
              <dl className="grid grid-cols-2 gap-x-5 gap-y-2 text-xs sm:grid-cols-4">
                <div className="col-span-2">
                  <dt className="text-muted-foreground">
                    {t("sandbox.image")}
                  </dt>
                  <dd className="mt-1 break-all">{connection.image}</dd>
                </div>
                <div>
                  <dt className="text-muted-foreground">
                    {t("sandbox.resources")}
                  </dt>
                  <dd className="mt-1">
                    {connection.cpu} CPU · {connection.memory}
                  </dd>
                </div>
                <div>
                  <dt className="text-muted-foreground">
                    {t("sandbox.timeout_seconds")}
                  </dt>
                  <dd className="mt-1">{connection.timeout_seconds}</dd>
                </div>
                <div>
                  <dt className="text-muted-foreground">
                    {t("sandbox.apiKey")}
                  </dt>
                  <dd className="mt-1">
                    {t(
                      connection.api_key_configured
                        ? "sandbox.keyConfigured"
                        : "sandbox.keyMissing"
                    )}
                  </dd>
                </div>
                <div>
                  <dt className="text-muted-foreground">
                    {t("sandbox.revision")}
                  </dt>
                  <dd className="mt-1">{connection.config_revision}</dd>
                </div>
              </dl>
              {canManage && (
                <div className="flex flex-wrap gap-2">
                  <Button
                    size="sm"
                    variant="outline"
                    disabled={
                      mutation.isPending || connection.status === "disabled"
                    }
                    onClick={() => {
                      setTestResult(null)
                      mutation.mutate({ action: "test", connection })
                    }}
                  >
                    {t("sandbox.test")}
                  </Button>
                  <Button
                    size="sm"
                    variant="outline"
                    disabled={mutation.isPending}
                    onClick={() => setForm(connection)}
                  >
                    {t("common.edit")}
                  </Button>
                  <Button
                    size="sm"
                    variant="ghost"
                    disabled={mutation.isPending}
                    onClick={() =>
                      mutation.mutate({
                        action:
                          connection.status === "disabled"
                            ? "enable"
                            : "disable",
                        connection,
                      })
                    }
                  >
                    {t(
                      connection.status === "disabled"
                        ? "sandbox.enable"
                        : "sandbox.disable"
                    )}
                  </Button>
                  <Button
                    size="sm"
                    variant="ghost"
                    disabled={mutation.isPending}
                    onClick={() => setDeleteTarget(connection)}
                  >
                    {t("common.delete")}
                  </Button>
                </div>
              )}
              {testResult?.uuid === connection.uuid &&
                testResult.config_revision === connection.config_revision &&
                testResult.status === connection.status && (
                  <p role="status" className="text-sm text-muted-foreground">
                    {t(
                      testResult.status === "ready"
                        ? "sandbox.testPassed"
                        : "sandbox.testFailed"
                    )}
                  </p>
                )}
            </article>
          ))}
          {mutation.isPending && (
            <p role="status" className="text-sm text-muted-foreground">
              {t("sandbox.working")}
            </p>
          )}
          {mutation.isError && (
            <p role="alert" className="text-sm text-destructive">
              {t("sandbox.actionError")}
            </p>
          )}
        </div>
      </section>

      <Dialog
        open={form !== null || (!!createPrefill && canManage)}
        onOpenChange={(open) => {
          if (!open) {
            setForm(null)
            onCreateClosed?.()
          }
        }}
      >
        {(form !== null || (!!createPrefill && canManage)) && (
          <SandboxConnectionForm
            key={!form || form === "new" ? "new" : form.uuid}
            spaceId={spaceId}
            connection={!form || form === "new" ? undefined : form}
            prefill={createPrefill ?? undefined}
            onCancel={() => {
              setForm(null)
              onCreateClosed?.()
            }}
            onSaved={() => {
              setForm(null)
              onCreateClosed?.()
              void refresh()
            }}
          />
        )}
      </Dialog>
      <AlertDialog
        open={!!deleteTarget}
        onOpenChange={(open) => {
          if (!open) setDeleteTarget(null)
        }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>
              {t("sandbox.deleteTitle", { name: deleteTarget?.name })}
            </AlertDialogTitle>
            <AlertDialogDescription>
              {t("sandbox.deleteHelp")}
            </AlertDialogDescription>
          </AlertDialogHeader>
          {mutation.isError && (
            <p role="alert" className="text-sm text-destructive">
              {t("sandbox.actionError")}
            </p>
          )}
          <AlertDialogFooter>
            <AlertDialogCancel disabled={mutation.isPending}>
              {t("common.cancel")}
            </AlertDialogCancel>
            <AlertDialogAction
              disabled={mutation.isPending}
              onClick={(event) => {
                event.preventDefault()
                if (deleteTarget)
                  mutation.mutate({
                    action: "delete",
                    connection: deleteTarget,
                  })
              }}
            >
              {t("common.delete")}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  )
}

function ErrorState({ text, onRetry }: { text: string; onRetry: () => void }) {
  const { t } = useTranslation()
  return (
    <div
      role="alert"
      className="flex flex-wrap items-center gap-3 text-sm text-destructive"
    >
      <span>{text}</span>
      <Button type="button" variant="outline" size="sm" onClick={onRetry}>
        {t("common.retry")}
      </Button>
    </div>
  )
}
