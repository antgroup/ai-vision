import { useLayoutEffect, useMemo, useRef, useState } from "react"
import { useQuery } from "@tanstack/react-query"
import { useTranslation } from "react-i18next"
import { Loader2, Plus, Search, X } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Dialog, DialogTitle } from "@/components/ui/dialog"
import { DialogContent } from "@/components/dialogs/DialogContent"
import { Input } from "@/components/ui/input"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { ModelValidationSummary } from "@/components/diagnose/ModelValidationSummary"
import { listModelConnectionReferences, listModelConnections } from "./api"
import { ModelConnectionForm } from "./ModelConnectionForm"
import { ConnectionActions } from "./ConnectionActions"
import { ConnectionStatus } from "./ConnectionStatus"
import { CopyValue } from "./CopyValue"
import { inferModelProvider } from "./providers"
import { formatConnectionDate } from "./connectionPresentation"
import { useModelConnectionPermissions } from "./useModelConnectionPermissions"
import { modelConnectionPath } from "./navigation"
import { InventoryDetailLink } from "./InventoryDetailLink"
import type { ModelConnection, ModelConnectionStatus } from "./types"

interface ModelConnectionsSectionProps {
  spaceId: string
  canManage: boolean
  onConnectionCreated?: (connection: ModelConnection) => void
  standalone?: boolean
  initialSearch?: string
  initialStatus?: string
  onFilterChange?: (search: string, status: string) => void
  detailSearch?: string
  restoreScrollTop?: number | null
}

export default function ModelConnectionsSection(
  props: ModelConnectionsSectionProps
) {
  return <ModelConnectionsContent key={props.spaceId} {...props} />
}

function ReferenceCount({
  spaceId,
  uuid,
  href,
}: {
  spaceId: string
  uuid: string
  href: string
}) {
  const { t } = useTranslation()
  const query = useQuery({
    queryKey: ["model-connection-references", spaceId, uuid],
    queryFn: () => listModelConnectionReferences(spaceId, uuid),
  })
  return (
    <InventoryDetailLink
      href={href}
      spaceId={spaceId}
      className="text-xs underline underline-offset-4 focus-visible:outline-2 focus-visible:outline-ring"
      ariaLabel={t("modelConnections.references")}
    >
      {query.isLoading ? (
        <Loader2 className="size-3 animate-spin" />
      ) : query.isError ? (
        t("modelConnections.unknownReferences")
      ) : (
        (query.data?.length ?? t("modelConnections.unknownReferences"))
      )}
    </InventoryDetailLink>
  )
}

function ModelConnectionsContent({
  spaceId,
  canManage,
  onConnectionCreated,
  standalone = false,
  initialSearch = "",
  initialStatus = "all",
  onFilterChange,
  detailSearch = "",
  restoreScrollTop,
}: ModelConnectionsSectionProps) {
  const { t, i18n } = useTranslation()
  const [search, setSearch] = useState(initialSearch)
  const [status, setStatus] = useState(initialStatus)
  const [creating, setCreating] = useState(false)
  const query = useQuery({
    queryKey: ["model-connections", spaceId],
    queryFn: () => listModelConnections(spaceId),
    enabled: !!spaceId,
  })
  const connections = query.data?.items
  const listRef = useRef<HTMLDivElement>(null)
  const restored = useRef(false)
  useLayoutEffect(() => {
    if (!standalone || !connections || restored.current) return
    restored.current = true
    const scroller = listRef.current?.closest("main")
    if (scroller && restoreScrollTop !== null && restoreScrollTop !== undefined)
      scroller.scrollTop = restoreScrollTop
  }, [connections, standalone, restoreScrollTop])
  const filtered = useMemo(
    () =>
      (connections ?? []).filter((connection) => {
        const term = search.trim().toLowerCase()
        return (
          (!term ||
            [connection.name, connection.modelName, connection.endpoint].some(
              (value) => value.toLowerCase().includes(term)
            )) &&
          (status === "all" || connection.status === status)
        )
      }),
    [connections, search, status]
  )
  const filter = (nextSearch: string, nextStatus: string) => {
    setSearch(nextSearch)
    setStatus(nextStatus)
    onFilterChange?.(nextSearch, nextStatus)
  }
  const detailHref = (connection: ModelConnection, tab?: string) => {
    const source = new URLSearchParams(detailSearch)
    const params = new URLSearchParams()
    for (const key of ["q", "status"])
      if (source.get(key)) params.set(key, source.get(key)!)
    params.set("spaceId", spaceId)
    if (tab) params.set("tab", tab)
    return `${modelConnectionPath(spaceId, connection.uuid).split("?")[0]}?${params.toString()}`
  }
  const createButton = (
    <Button
      disabled={!canManage}
      onClick={() => setCreating(true)}
      className="shrink-0"
    >
      <Plus className="mr-1 size-3.5" />
      {t("modelConnections.create")}
    </Button>
  )
  const content = (
    <>
      {!canManage ? (
        <p className="mb-4 text-xs leading-relaxed text-muted-foreground">
          {t("modelConnections.permissionHint")}
        </p>
      ) : null}
      <div className="mb-5 flex flex-wrap items-center justify-between gap-3">
        <div className="flex min-w-0 flex-1 flex-wrap gap-3">
          <div className="relative min-w-0 flex-[1_1_14rem] sm:max-w-80">
            <Search className="absolute top-1/2 left-3 size-4 -translate-y-1/2 text-muted-foreground" />
            <Input
              aria-label={t("modelConnections.searchPlaceholder")}
              placeholder={t("modelConnections.searchPlaceholder")}
              value={search}
              onChange={(event) => filter(event.target.value, status)}
              className="pr-8 pl-9"
            />
            {search ? (
              <button
                type="button"
                aria-label={t("modelConnections.clearSearch")}
                className="absolute top-1/2 right-3 -translate-y-1/2 rounded-sm text-muted-foreground focus-visible:outline-2 focus-visible:outline-ring"
                onClick={() => filter("", status)}
              >
                <X className="size-3.5" />
              </button>
            ) : null}
          </div>
          <Select
            value={status}
            onValueChange={(value) => filter(search, value)}
          >
            <SelectTrigger
              className="w-auto max-w-full min-w-40"
              aria-label={t("modelConnections.connectionColumn")}
            >
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">
                {t("modelConnections.allStatuses")}
              </SelectItem>
              {(
                [
                  "available",
                  "unavailable",
                  "untested",
                ] as ModelConnectionStatus[]
              ).map((value) => (
                <SelectItem key={value} value={value}>
                  {t(`modelConnections.${value}`)}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        {standalone ? createButton : null}
      </div>
      {query.isError ? (
        <div className="mb-4 flex flex-wrap items-center gap-3">
          <p role="alert" className="text-sm text-destructive">
            {t("modelConnections.loadFailed")}
          </p>
          <Button
            variant="outline"
            disabled={query.isFetching}
            onClick={() => void query.refetch()}
          >
            {t("common.retry")}
          </Button>
        </div>
      ) : null}
      {query.isLoading ? (
        <div className="flex items-center justify-center gap-2 py-16 text-sm text-muted-foreground">
          <Loader2 className="size-4 animate-spin" />
          {t("modelConnections.loading")}
        </div>
      ) : query.isError && !connections ? null : !connections?.length ? (
        <div className="py-16 text-center">
          <p className="text-sm font-medium">{t("modelConnections.empty")}</p>
          <p className="mt-2 text-xs text-muted-foreground">
            {t("modelConnections.emptyHint")}
          </p>
        </div>
      ) : !filtered.length ? (
        <p className="py-16 text-center text-sm text-muted-foreground">
          {t("modelConnections.noMatch")}
        </p>
      ) : (
        <>
          <div className={standalone ? "hidden md:block" : "overflow-x-auto"}>
            <table className="w-full table-fixed border-collapse text-left text-sm">
              <thead>
                <tr className="border-y bg-muted/40 text-xs text-muted-foreground">
                  <th className="w-[27%] px-3 py-3 font-medium">
                    {t("modelConnections.connectionModel")}
                  </th>
                  <th className="w-[25%] px-3 py-3 font-medium">
                    {t("modelConnections.providerAddress")}
                  </th>
                  <th className="w-[16%] px-3 py-3 font-medium">
                    {t("modelConnections.connectionColumn")}
                  </th>
                  {standalone ? (
                    <th className="w-[9%] px-3 py-3 font-medium">
                      {t("modelConnections.references")}
                    </th>
                  ) : null}
                  <th className="w-[14%] px-3 py-3 font-medium">
                    {t("modelConnections.updatedAt")}
                  </th>
                  <th className="px-3 py-3 text-right font-medium">
                    {t("modelConnections.actions")}
                  </th>
                </tr>
              </thead>
              <tbody>
                {filtered.map((connection) => {
                  const preset = inferModelProvider(connection.endpoint)
                  const href = detailHref(connection)
                  return (
                    <tr
                      key={connection.uuid}
                      className="border-b align-middle hover:bg-muted/30"
                    >
                      <td className="min-w-0 px-3 py-5">
                        {standalone ? (
                          <InventoryDetailLink
                            href={href}
                            spaceId={spaceId}
                            className="font-medium break-all hover:underline focus-visible:outline-2 focus-visible:outline-ring"
                          >
                            {connection.name}
                          </InventoryDetailLink>
                        ) : (
                          <span className="font-medium break-all">
                            {connection.name}
                          </span>
                        )}
                        <div className="mt-1.5">
                          <CopyValue
                            value={connection.modelName}
                            label={t("modelConnections.model")}
                            compact
                          />
                        </div>
                      </td>
                      <td className="min-w-0 px-3 py-5">
                        <p className="text-xs">
                          {preset
                            ? `${t(preset.provider.nameKey)} · ${t(preset.region.labelKey)}`
                            : t("modelConnections.providers.custom")}
                        </p>
                        <p
                          className="mt-1.5 truncate text-xs text-muted-foreground"
                          title={connection.endpoint}
                        >
                          {connection.endpoint}
                        </p>
                      </td>
                      <td className="px-3 py-5">
                        <ConnectionStatus connection={connection} />
                        {!standalone ? (
                          <div className="mt-2">
                            <ModelValidationSummary
                              report={connection.diagnosticValidation}
                            />
                          </div>
                        ) : null}
                      </td>
                      {standalone ? (
                        <td className="px-3 py-5">
                          <ReferenceCount
                            spaceId={spaceId}
                            uuid={connection.uuid}
                            href={detailHref(connection, "references")}
                          />
                        </td>
                      ) : null}
                      <td className="px-3 py-5 text-xs text-muted-foreground">
                        {formatConnectionDate(
                          connection.updatedAt,
                          i18n.resolvedLanguage ?? i18n.language
                        ) ?? t("modelConnections.notRecorded")}
                      </td>
                      <td className="px-3 py-5 text-right">
                        {standalone ? (
                          <InventoryDetailLink
                            href={href}
                            spaceId={spaceId}
                            className="mb-1 inline-flex text-xs hover:underline focus-visible:outline-2 focus-visible:outline-ring"
                          >
                            {t("modelConnections.details")}
                          </InventoryDetailLink>
                        ) : null}
                        <ConnectionActions
                          key={`${spaceId}:${connection.uuid}`}
                          connection={connection}
                          spaceId={spaceId}
                          canManage={canManage}
                          compact={!standalone}
                        />
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
          {standalone ? (
            <ul className="divide-y border-y md:hidden">
              {filtered.map((connection) => (
                <li key={connection.uuid} className="min-w-0 py-5">
                  <InventoryDetailLink
                    href={detailHref(connection)}
                    spaceId={spaceId}
                    className="text-sm font-medium break-all hover:underline focus-visible:outline-2 focus-visible:outline-ring"
                  >
                    {connection.name}
                  </InventoryDetailLink>
                  <div className="mt-2">
                    <CopyValue
                      value={connection.modelName}
                      label={t("modelConnections.model")}
                      compact
                    />
                  </div>
                  <div className="mt-4 flex items-center justify-between gap-3">
                    <ConnectionStatus connection={connection} />
                    <Button variant="outline" size="sm" asChild>
                      <InventoryDetailLink
                        href={detailHref(connection)}
                        spaceId={spaceId}
                      >
                        {t("modelConnections.details")}
                      </InventoryDetailLink>
                    </Button>
                  </div>
                </li>
              ))}
            </ul>
          ) : null}
        </>
      )}
      {standalone ? (
        <p className="mt-5 max-w-3xl text-xs leading-relaxed text-muted-foreground">
          {t("modelConnections.storedStatusHint")}
        </p>
      ) : null}
      <Dialog open={creating} onOpenChange={setCreating}>
        <DialogContent
          size="form"
          aria-describedby={undefined}
          className="overflow-y-auto"
        >
          {creating ? (
            <ModelConnectionForm
              spaceId={spaceId}
              editing={null}
              onCancel={() => setCreating(false)}
              onSaved={(connection) => {
                setCreating(false)
                onConnectionCreated?.(connection)
              }}
            />
          ) : null}
        </DialogContent>
      </Dialog>
    </>
  )
  return standalone ? (
    <div ref={listRef}>{content}</div>
  ) : (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between gap-4">
        <CardTitle>{t("modelConnections.title")}</CardTitle>
        {createButton}
      </CardHeader>
      <CardContent>{content}</CardContent>
    </Card>
  )
}

export function ModelConnectionManagerPanel({
  spaceId,
  onConnectionCreated,
}: {
  spaceId: string
  onConnectionCreated?: (connection: ModelConnection) => void
}) {
  const { canManage } = useModelConnectionPermissions(spaceId)
  return (
    <ModelConnectionsSection
      spaceId={spaceId}
      canManage={canManage}
      onConnectionCreated={onConnectionCreated}
    />
  )
}

export function ModelConnectionManagerDialog({
  open,
  spaceId,
  onOpenChange,
  onConnectionCreated,
}: {
  open: boolean
  spaceId: string
  onOpenChange: (open: boolean) => void
  onConnectionCreated?: (connection: ModelConnection) => void
}) {
  const { t } = useTranslation()
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent size="workspace" className="flex flex-col overflow-y-auto">
        <DialogTitle className="sr-only">
          {t("modelConnections.managerTitle")}
        </DialogTitle>
        <ModelConnectionManagerPanel
          key={spaceId}
          spaceId={spaceId}
          onConnectionCreated={onConnectionCreated}
        />
      </DialogContent>
    </Dialog>
  )
}
