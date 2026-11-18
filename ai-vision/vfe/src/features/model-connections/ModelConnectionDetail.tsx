import { useLayoutEffect, useRef, useState } from "react"
import { useQuery } from "@tanstack/react-query"
import { useTranslation } from "react-i18next"
import { useLocation, useNavigate, useSearchParams } from "react-router-dom"
import { ArrowLeft, Loader2 } from "lucide-react"
import { Button } from "@/components/ui/button"
import { ModelValidationSummary } from "@/components/diagnose/ModelValidationSummary"
import { listModelConnectionReferences, listModelConnections } from "./api"
import { ConnectionActions } from "./ConnectionActions"
import { ConnectionStatus } from "./ConnectionStatus"
import { CopyValue } from "./CopyValue"
import { inferModelProvider } from "./providers"
import {
  diagnoserReferencePath,
  formatConnectionDate,
} from "./connectionPresentation"
import { useModelConnectionPermissions } from "./useModelConnectionPermissions"
import { modelConnectionsPath } from "./navigation"
import type { ModelConnectionTestErrorReason } from "./types"

export function ModelConnectionDetail({
  spaceId,
  connectionUuid,
}: {
  spaceId: string
  connectionUuid: string
}) {
  const { t, i18n } = useTranslation()
  const [params, setParams] = useSearchParams()
  const navigate = useNavigate()
  const location = useLocation()
  const pageRef = useRef<HTMLDivElement>(null)
  useLayoutEffect(() => {
    const scroller = pageRef.current?.closest("main")
    if (scroller) scroller.scrollTop = 0
  }, [])
  const tab = ["overview", "validationResults", "references"].includes(
    params.get("tab") ?? ""
  )
    ? params.get("tab")!
    : "overview"
  const [sessionError, setSessionError] =
    useState<ModelConnectionTestErrorReason | null>(null)
  const { canManage } = useModelConnectionPermissions(spaceId)
  const query = useQuery({
    queryKey: ["model-connections", spaceId],
    queryFn: () => listModelConnections(spaceId),
  })
  const references = useQuery({
    queryKey: ["model-connection-references", spaceId, connectionUuid],
    queryFn: () => listModelConnectionReferences(spaceId, connectionUuid),
    enabled: !!query.data?.items.some((item) => item.uuid === connectionUuid),
  })
  const connection = query.data?.items.find(
    (item) => item.uuid === connectionUuid
  )
  const backParams = new URLSearchParams({ spaceId })
  for (const key of ["q", "status"])
    if (params.get(key)) backParams.set(key, params.get(key)!)
  const backPath = `${modelConnectionsPath(spaceId).split("?")[0]}?${backParams.toString()}`
  const back = (
    <a
      href={backPath}
      className="mb-7 inline-flex items-center gap-2 text-xs text-muted-foreground hover:text-foreground focus-visible:outline-2 focus-visible:outline-ring"
      onClick={(event) => {
        if (
          event.button !== 0 ||
          event.metaKey ||
          event.ctrlKey ||
          event.shiftKey ||
          event.altKey
        )
          return
        const state = location.state as {
          modelConnectionReturn?: { spaceId?: unknown }
        } | null
        if (state?.modelConnectionReturn?.spaceId === spaceId) {
          event.preventDefault()
          navigate(-1)
        }
      }}
    >
      <ArrowLeft className="size-3.5" />
      {t("modelConnections.back")}
    </a>
  )
  const retry = (
    <Button variant="outline" onClick={() => void query.refetch()}>
      {t("common.retry")}
    </Button>
  )
  if (query.isLoading)
    return (
      <div ref={pageRef} className="p-8">
        {back}
        <p className="flex items-center gap-2 text-sm text-muted-foreground">
          <Loader2 className="size-4 animate-spin" />
          {t("modelConnections.loading")}
        </p>
      </div>
    )
  if (!connection)
    return (
      <div ref={pageRef} className="p-8">
        {back}
        <p role={query.isError ? "alert" : "status"} className="mb-4 text-sm">
          {t(
            query.isError
              ? "modelConnections.loadFailed"
              : "modelConnections.notFound"
          )}
        </p>
        {query.isError ? retry : null}
      </div>
    )
  const preset = inferModelProvider(connection.endpoint)
  return (
    <div
      ref={pageRef}
      className="mx-auto w-full max-w-[100rem] min-w-0 px-4 py-7 sm:px-8 sm:py-8"
    >
      {back}
      <header className="flex flex-wrap items-start justify-between gap-5">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-4">
            <h1 className="max-w-full text-2xl font-semibold tracking-tight break-all">
              {connection.name}
            </h1>
            <ConnectionStatus connection={connection} />
          </div>
          <div className="mt-3">
            <CopyValue
              value={connection.modelName}
              label={t("modelConnections.model")}
            />
          </div>
        </div>
        <ConnectionActions
          key={`${spaceId}:${connectionUuid}`}
          detail
          connection={connection}
          spaceId={spaceId}
          canManage={canManage}
          onDeleted={() => navigate(backPath, { replace: true })}
          onTestResult={setSessionError}
        />
      </header>
      {!canManage ? (
        <p className="mt-4 text-xs leading-relaxed text-muted-foreground">
          {t("modelConnections.permissionHint")}
        </p>
      ) : null}
      {query.isError ? (
        <div className="mt-5 flex flex-wrap items-center gap-3">
          <p role="alert" className="text-sm text-destructive">
            {t("modelConnections.loadFailed")}
          </p>
          {retry}
        </div>
      ) : null}
      <div
        role="tablist"
        aria-label={t("modelConnections.details")}
        className="mt-8 flex gap-5 overflow-x-auto border-b sm:gap-8"
      >
        {(["overview", "validationResults", "references"] as const).map(
          (name) => (
            <button
              key={name}
              id={`model-tab-${name}`}
              type="button"
              role="tab"
              aria-selected={tab === name}
              aria-controls={`model-panel-${name}`}
              tabIndex={tab === name ? 0 : -1}
              onKeyDown={(event) => {
                if (
                  !["ArrowLeft", "ArrowRight", "Home", "End"].includes(
                    event.key
                  )
                )
                  return
                event.preventDefault()
                const tabs = Array.from(
                  event.currentTarget.parentElement?.querySelectorAll<HTMLButtonElement>(
                    '[role="tab"]'
                  ) ?? []
                )
                const index = tabs.indexOf(event.currentTarget)
                const next =
                  event.key === "Home"
                    ? 0
                    : event.key === "End"
                      ? tabs.length - 1
                      : (index +
                          (event.key === "ArrowRight" ? 1 : -1) +
                          tabs.length) %
                        tabs.length
                tabs[next]?.focus()
                tabs[next]?.click()
              }}
              onClick={() =>
                setParams(
                  (current) => {
                    const next = new URLSearchParams(current)
                    if (name === "overview") next.delete("tab")
                    else next.set("tab", name)
                    return next
                  },
                  { replace: true, state: location.state }
                )
              }
              className={`shrink-0 border-b-2 py-3 text-sm focus-visible:outline-2 focus-visible:outline-ring ${tab === name ? "border-foreground font-medium" : "border-transparent text-muted-foreground hover:text-foreground"}`}
            >
              {t(`modelConnections.${name}`)}
            </button>
          )
        )}
      </div>
      <section
        role="tabpanel"
        id={`model-panel-${tab}`}
        aria-labelledby={`model-tab-${tab}`}
        className="max-w-6xl py-7 sm:py-8"
      >
        {tab === "overview" ? (
          <div className="grid items-start gap-9 lg:grid-cols-[1.15fr_1fr] lg:gap-16">
            <section>
              <dl className="grid grid-cols-[minmax(0,7rem)_minmax(0,1fr)] gap-x-5 gap-y-5 text-sm">
                <dt className="text-xs text-muted-foreground">
                  {t("modelConnections.provider")}
                </dt>
                <dd className="break-all">
                  {preset
                    ? `${t(preset.provider.nameKey)} · ${t(preset.region.labelKey)}`
                    : t("modelConnections.providers.custom")}
                </dd>
                <dt className="text-xs text-muted-foreground">
                  {t("modelConnections.baseUrl")}
                </dt>
                <dd className="min-w-0">
                  <CopyValue
                    value={connection.endpoint}
                    label={t("modelConnections.baseUrl")}
                  />
                </dd>
                <dt className="text-xs text-muted-foreground">
                  {t("modelConnections.model")}
                </dt>
                <dd className="min-w-0">
                  <CopyValue
                    value={connection.modelName}
                    label={t("modelConnections.model")}
                  />
                </dd>
                <dt className="text-xs text-muted-foreground">
                  {t("modelConnections.apiKey")}
                </dt>
                <dd className="text-xs break-words">
                  {t(
                    connection.apiKeyConfigured
                      ? "modelConnections.credentialConfigured"
                      : "modelConnections.credentialMissing"
                  )}
                </dd>
                <dt className="text-xs text-muted-foreground">
                  {t("modelConnections.updatedAt")}
                </dt>
                <dd className="text-xs">
                  {formatConnectionDate(
                    connection.updatedAt,
                    i18n.resolvedLanguage ?? i18n.language
                  ) ?? t("modelConnections.notRecorded")}
                </dd>
              </dl>
            </section>
            <section className="border-t pt-6 lg:border-t-0 lg:pt-0">
              <h2 className="mb-6 text-sm font-semibold">
                {t("modelConnections.connectivityEvidence")}
              </h2>
              <dl className="grid grid-cols-[minmax(0,7rem)_minmax(0,1fr)] gap-5 text-sm">
                <dt className="text-xs text-muted-foreground">
                  {t("modelConnections.connectionColumn")}
                </dt>
                <dd>
                  <ConnectionStatus connection={connection} />
                </dd>
                <dt className="text-xs text-muted-foreground">
                  {t("modelConnections.checkedAt")}
                </dt>
                <dd className="text-xs">{t("modelConnections.notRecorded")}</dd>
              </dl>
              <p className="mt-5 text-xs leading-relaxed text-muted-foreground">
                {t("modelConnections.storedStatusHint")}
              </p>
              {sessionError ? (
                <p role="alert" className="mt-4 text-xs text-destructive">
                  {t("modelConnections.currentSessionFailure")}:{" "}
                  {t(`modelConnections.testErrors.${sessionError}`)}
                </p>
              ) : connection.status === "unavailable" ? (
                <p className="mt-4 text-xs text-muted-foreground">
                  {t("modelConnections.errorNotRetained")}
                </p>
              ) : null}
            </section>
          </div>
        ) : tab === "validationResults" ? (
          <div className="max-w-4xl">
            <h2 className="text-sm font-semibold">
              {t("modelConnections.test")}
            </h2>
            <p className="mt-2 mb-6 max-w-3xl text-xs leading-relaxed text-muted-foreground">
              {t("modelConnections.currentReportHint")}
            </p>
            <ModelValidationSummary
              report={connection.diagnosticValidation}
              details
              currentConfiguration
            />
          </div>
        ) : (
          <div>
            <p className="mb-6 text-xs leading-relaxed text-muted-foreground">
              {t("modelConnections.referencesHint")}
            </p>
            {references.isLoading ? (
              <p
                role="status"
                className="flex items-center gap-2 text-sm text-muted-foreground"
              >
                <Loader2 className="size-4 animate-spin" />
                {t("modelConnections.checkingReferences")}
              </p>
            ) : references.isError ? (
              <div>
                <p role="alert" className="mb-3 text-sm text-destructive">
                  {t("modelConnections.referencesFailed")}
                </p>
                <Button
                  variant="outline"
                  onClick={() => void references.refetch()}
                >
                  {t("common.retry")}
                </Button>
              </div>
            ) : references.data?.length ? (
              <ul className="divide-y border-y">
                {references.data.map((reference) => (
                  <li
                    key={reference.uuid}
                    className="flex flex-wrap items-center justify-between gap-3 py-4"
                  >
                    <div className="min-w-0">
                      <p className="text-sm font-medium break-all">
                        {reference.name}
                      </p>
                      <p className="mt-1 text-xs text-muted-foreground">
                        {t("modelConnections.referenceKind")} ·{" "}
                        {reference.mode.toUpperCase()}
                      </p>
                    </div>
                    <a
                      href={diagnoserReferencePath(spaceId, reference)}
                      className="text-xs underline underline-offset-4 focus-visible:outline-2 focus-visible:outline-ring"
                    >
                      {t("modelConnections.openReference")}
                    </a>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="text-sm text-muted-foreground">
                {t("modelConnections.noReferences")}
              </p>
            )}
          </div>
        )}
      </section>
    </div>
  )
}
