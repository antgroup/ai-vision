import { useEffect, useRef, useState, type ReactNode } from "react"
import { ArrowLeft, ExternalLink, Loader2 } from "lucide-react"
import { useTranslation } from "react-i18next"

import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { importDialogStyles } from "../../components/labs/shared/importDialogStyles"
import { ImportLookupStatus } from "../../components/labs/shared/ImportLookupStatus"
import { ImportRegistrySearch } from "../../components/labs/shared/ImportRegistrySearch"
import {
  getMcpRegistryDetail,
  searchMcpRegistry,
  type McpRegistryDetail,
  type McpRegistryOption,
  type McpRegistryServer,
} from "./mcpApi"
import {
  appendRegistryServers,
  buildRegistryImport,
  registryServerName,
  safeRegistryUrl,
  SOURCES,
  type RegistryImportPreview,
} from "./mcpRegistryImport"

function registryDefaults(option?: McpRegistryOption): Record<string, string> {
  return Object.fromEntries(
    (option?.fields ?? []).map((field) => [field.key, field.default ?? ""])
  )
}

interface SourceState {
  servers: McpRegistryServer[]
  cursor: string | null
  loading: boolean
  error: boolean
  loaded: boolean
}

export function McpImportRegistryPanel({
  spaceId,
  onPreview,
}: {
  spaceId: string
  onPreview: (preview: RegistryImportPreview) => void
}) {
  // Remount all request/form state on a Space change, including secrets.
  return (
    <RegistrySearch key={spaceId} spaceId={spaceId} onPreview={onPreview} />
  )
}

function RegistrySearch({
  spaceId,
  onPreview,
}: {
  spaceId: string
  onPreview: (preview: RegistryImportPreview) => void
}) {
  const { t } = useTranslation()
  const tr = (key: string) => t(`labs.mcp.import.registry.${key}`)
  const [keyword, setKeyword] = useState("")
  const [sourceFilter, setSourceFilter] = useState("all")
  const visibleSources = SOURCES.filter(
    (source) => sourceFilter === "all" || source.id === sourceFilter
  )
  const query = useRef("")
  const [sources, setSources] = useState<Record<string, SourceState>>({})
  const [selected, setSelected] = useState<{
    source: string
    server: McpRegistryServer
  } | null>(null)
  const [detail, setDetail] = useState<McpRegistryDetail | null>(null)
  const [detailLoading, setDetailLoading] = useState(false)
  const [detailError, setDetailError] = useState(false)
  const generation = useRef(0)
  const detailSequence = useRef(0)
  const controllers = useRef(new Set<AbortController>())
  const detailController = useRef<AbortController | null>(null)
  const searchInput = useRef<HTMLInputElement>(null)

  useEffect(
    () => () => {
      generation.current++
      detailSequence.current++
      controllers.current.forEach((controller) => controller.abort())
      detailController.current?.abort()
    },
    []
  )

  const loadSources = async (ids: string[], reset: boolean) => {
    if (reset) {
      generation.current++
      controllers.current.forEach((controller) => controller.abort())
      controllers.current.clear()
      query.current = keyword.trim()
      setSources({})
    }
    const version = generation.current
    const q = query.current
    // Each source publishes independently. A failed provider never hides a
    // successful provider or blocks its pagination/retry controls.
    await Promise.allSettled(
      ids.map(async (source) => {
        const cursor = reset ? "" : (sources[source]?.cursor ?? "")
        const controller = new AbortController()
        controllers.current.add(controller)
        setSources((current) => ({
          ...current,
          [source]: {
            servers: current[source]?.servers ?? [],
            cursor: cursor || null,
            loaded: current[source]?.loaded ?? false,
            loading: true,
            error: false,
          },
        }))
        try {
          const data = await searchMcpRegistry(
            spaceId,
            source,
            q,
            cursor,
            controller.signal
          )
          if (generation.current !== version || controller.signal.aborted)
            return
          setSources((current) => ({
            ...current,
            [source]: {
              servers: appendRegistryServers(
                current[source]?.servers ?? [],
                data.servers
              ),
              cursor: data.nextCursor === cursor ? null : data.nextCursor,
              loading: false,
              error: false,
              loaded: true,
            },
          }))
        } catch {
          if (generation.current !== version || controller.signal.aborted)
            return
          setSources((current) => ({
            ...current,
            [source]: { ...current[source], loading: false, error: true },
          }))
        } finally {
          controllers.current.delete(controller)
        }
      })
    )
  }

  const openDetail = async (source: string, server: McpRegistryServer) => {
    const sequence = ++detailSequence.current
    detailController.current?.abort()
    const controller = new AbortController()
    detailController.current = controller
    setSelected({ source, server })
    setDetail(null)
    setDetailError(false)
    setDetailLoading(true)
    try {
      const data = await getMcpRegistryDetail(
        spaceId,
        source,
        server.id,
        controller.signal
      )
      if (sequence === detailSequence.current && !controller.signal.aborted)
        setDetail(data)
    } catch {
      if (sequence === detailSequence.current && !controller.signal.aborted)
        setDetailError(true)
    } finally {
      if (sequence === detailSequence.current && !controller.signal.aborted)
        setDetailLoading(false)
    }
  }

  const dismissDetail = () => {
    detailSequence.current++
    detailController.current?.abort()
    setSelected(null)
    setDetail(null)
    requestAnimationFrame(() => searchInput.current?.focus())
  }

  if (selected) {
    return (
      <div className={importDialogStyles.panel}>
        <div className="flex shrink-0 flex-col gap-3 border-b border-[var(--color-border)] pb-4">
          <Button
            variant="ghost"
            size="sm"
            className={`${importDialogStyles.ghost} self-start`}
            onClick={dismissDetail}
          >
            <ArrowLeft aria-hidden="true" className="size-4" />
            {tr("back")}
          </Button>
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div className="min-w-0 flex-1 space-y-1">
              <h3
                title={selected.server.name}
                className="line-clamp-2 text-sm leading-[22px] font-medium [overflow-wrap:anywhere] text-[var(--color-text-primary)]"
              >
                {selected.server.name}
              </h3>
              <p className="text-xs leading-5 text-[var(--color-text-description)]">
                {SOURCES.find((source) => source.id === selected.source)?.label}
              </p>
            </div>
            <RegistryLink url={selected.server.url} />
          </div>
        </div>
        <div className={importDialogStyles.results}>
          {detailLoading ? (
            <p
              role="status"
              className="flex items-center gap-2 py-4 text-sm text-[var(--color-text-secondary)]"
            >
              <Loader2 aria-hidden="true" className="size-4 animate-spin" />
              {tr("loading")}
            </p>
          ) : null}
          {detailError ? (
            <div
              role="alert"
              className="space-y-2 rounded-[12px] border border-[var(--color-border)] p-4 text-sm text-[var(--color-error)]"
            >
              <p>{tr("detailFailed")}</p>
              <Button
                variant="outline"
                className={importDialogStyles.button}
                onClick={() =>
                  void openDetail(selected.source, selected.server)
                }
              >
                {tr("retry")}
              </Button>
            </div>
          ) : null}
          {detail ? (
            <RegistryDetail
              key={JSON.stringify([selected.source, selected.server.id])}
              detail={detail}
              onPreview={onPreview}
            />
          ) : null}
        </div>
      </div>
    )
  }

  const sourceResults = visibleSources.flatMap((source) => {
    const state = sources[source.id]
    return state && (state.servers.length || state.cursor || state.error)
      ? [{ ...source, state }]
      : []
  })
  const searching = visibleSources.some(({ id }) => {
    const state = sources[id]
    return state?.loading && !state.loaded
  })
  const allLoaded = visibleSources.every(({ id }) => sources[id]?.loaded)
  const searchStatus = searching
    ? tr("searching")
    : sourceResults.length
      ? null
      : tr(allLoaded ? "empty" : "start")
  return (
    <div className={importDialogStyles.panel}>
      <div
        className={importDialogStyles.toolbar}
        data-product-guide="mcp-registry-search"
      >
        <ImportRegistrySearch
          keyword={keyword}
          onKeywordChange={setKeyword}
          placeholder={tr("searchPlaceholder")}
          inputRef={searchInput}
          source={sourceFilter}
          sources={[{ id: "all", label: tr("allSources") }, ...SOURCES]}
          sourceLabel={t("labs.common.registrySource")}
          onSourceChange={setSourceFilter}
          searchLabel={tr(keyword.trim() ? "search" : "browse")}
          onSearch={() =>
            void loadSources(
              visibleSources.map((source) => source.id),
              true
            )
          }
        />
      </div>
      <div className={importDialogStyles.results}>
        <div className="space-y-5">
          {sourceResults.map(({ id, label, state }) => (
            <section
              key={id}
              className="space-y-3"
              aria-label={label}
              aria-busy={state.loading}
            >
              <div className="flex flex-wrap items-center justify-between gap-2">
                <div className="flex flex-wrap items-baseline gap-x-2">
                  <h3 className="text-sm leading-[22px] font-medium text-[var(--color-text-primary)]">
                    {label}
                  </h3>
                  {id === "mcpservers" && state.servers.length > 0 ? (
                    <span className="text-xs leading-5 text-[var(--color-text-description)]">
                      {tr("directorySummary")}
                    </span>
                  ) : null}
                </div>
                {state.servers.length > 0 ? (
                  <span className="text-xs leading-5 text-[var(--color-text-description)]">
                    {t("labs.mcp.import.registry.loadedCount", {
                      count: state.servers.length,
                    })}
                  </span>
                ) : null}
              </div>
              {state.error ? (
                <div
                  role="alert"
                  className="space-y-2 rounded-[12px] border border-[var(--color-border)] p-3 text-xs text-[var(--color-error)]"
                >
                  <p>{tr("failed")}</p>
                  <Button
                    variant="outline"
                    className={importDialogStyles.button}
                    disabled={state.loading}
                    onClick={() => void loadSources([id], false)}
                  >
                    {tr("retry")}
                  </Button>
                </div>
              ) : null}
              {state.servers.length > 0 ? (
                <div className={importDialogStyles.cardGrid}>
                  {state.servers.map((server) => (
                    <article
                      key={server.id}
                      className={importDialogStyles.card}
                    >
                      <div className={importDialogStyles.cardBody}>
                        <h4
                          title={server.name}
                          className="line-clamp-2 text-sm leading-[22px] font-medium [overflow-wrap:anywhere] text-[var(--color-text-primary)]"
                        >
                          {server.name}
                        </h4>
                        {id === "official" && server.id !== server.name ? (
                          <p
                            title={server.id}
                            className="truncate font-mono text-xs leading-5 text-[var(--color-text-description)]"
                          >
                            {server.id}
                          </p>
                        ) : null}
                        <p
                          title={server.description}
                          className="line-clamp-3 text-xs leading-5 [overflow-wrap:anywhere] text-[var(--color-text-secondary)]"
                        >
                          {server.description}
                        </p>
                      </div>
                      <div className={importDialogStyles.cardFooter}>
                        <RegistryLink url={server.url} stretch />
                        {id === "official" ? (
                          <Button
                            className={`${importDialogStyles.compactAction} flex-1`}
                            data-product-guide="mcp-registry-select"
                            size="sm"
                            onClick={() => void openDetail(id, server)}
                          >
                            {tr("configure")}
                          </Button>
                        ) : null}
                      </div>
                    </article>
                  ))}
                </div>
              ) : null}
              {state.cursor && !state.error ? (
                <Button
                  variant="outline"
                  className={importDialogStyles.button}
                  disabled={state.loading}
                  aria-busy={state.loading}
                  onClick={() => void loadSources([id], false)}
                >
                  {state.loading ? (
                    <Loader2
                      aria-hidden="true"
                      className="size-4 motion-safe:animate-spin"
                    />
                  ) : null}
                  {tr("loadMore")}
                </Button>
              ) : null}
            </section>
          ))}
        </div>
        <ImportLookupStatus
          message={searchStatus}
          busy={searching}
          className={sourceResults.length ? "mt-3" : undefined}
        />
      </div>
    </div>
  )
}

function RegistryLink({
  url,
  stretch = false,
}: {
  url?: string
  stretch?: boolean
}) {
  const { t } = useTranslation()
  const safe = safeRegistryUrl(url)
  return safe ? (
    <Button
      asChild
      variant="ghost"
      size="sm"
      className={`${importDialogStyles.compactGhost} ${stretch ? "min-w-0 flex-1" : "shrink-0"}`}
    >
      <a href={safe} target="_blank" rel="noopener noreferrer">
        <ExternalLink aria-hidden="true" className="size-4" />
        {t("labs.mcp.import.registry.view")}
      </a>
    </Button>
  ) : null
}

function RegistryDetail({
  detail,
  onPreview,
}: {
  detail: McpRegistryDetail
  onPreview: (preview: RegistryImportPreview) => void
}) {
  const { t } = useTranslation()
  const tr = (key: string) => t(`labs.mcp.import.registry.${key}`)
  const [optionIndex, setOptionIndex] = useState(0)
  const [values, setValues] = useState(() =>
    registryDefaults(detail.options[0])
  )
  const [name, setName] = useState(() => registryServerName(detail.id))
  const option = detail.options[optionIndex]
  const heading = useRef<HTMLHeadingElement>(null)
  useEffect(() => {
    heading.current?.focus()
  }, [])
  return (
    <section className="space-y-3" data-product-guide="mcp-registry-configure">
      <h3 ref={heading} tabIndex={-1} className="text-sm font-medium">
        {tr("configuration")}
      </h3>
      <p className="text-xs leading-5 break-words text-[var(--color-text-secondary)]">
        {detail.description}
      </p>
      {detail.unsupportedReason || !option ? (
        <div role="status" className="space-y-2 text-sm">
          <p>{tr("unsupported")}</p>
          {detail.unsupportedReason ? <p>{detail.unsupportedReason}</p> : null}
          <RegistryLink url={detail.repositoryUrl || detail.url} />
        </div>
      ) : (
        <RegistryOption
          option={option}
          name={name}
          values={values}
          onValueChange={(key, value) =>
            setValues((current) => ({ ...current, [key]: value }))
          }
          onPreview={onPreview}
        >
          <label className="flex flex-col gap-1 text-sm">
            {tr("option")}
            <select
              value={optionIndex}
              onChange={(event) => {
                const next = Number(event.target.value)
                setOptionIndex(next)
                setValues(registryDefaults(detail.options[next]))
              }}
              className={`${importDialogStyles.field} w-full min-w-0 border px-3 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--color-labs)]`}
            >
              {detail.options.map((entry, index) => (
                <option key={entry.id} value={index}>
                  {entry.label}
                </option>
              ))}
            </select>
          </label>
          <label className="flex flex-col gap-1 text-sm">
            {tr("name")}
            <Input
              className={importDialogStyles.field}
              value={name}
              onChange={(event) => setName(event.target.value)}
              aria-describedby="mcp-registry-name-hint"
              autoComplete="off"
            />
          </label>
          <p
            id="mcp-registry-name-hint"
            className="text-xs leading-5 text-[var(--color-text-description)]"
          >
            {tr("nameHint")}
          </p>
        </RegistryOption>
      )}
    </section>
  )
}

function RegistryOption({
  option,
  name,
  onPreview,
  children,
  values,
  onValueChange,
}: {
  option: McpRegistryOption
  name: string
  children: ReactNode
  values: Record<string, string>
  onValueChange: (key: string, value: string) => void
  onPreview: (preview: RegistryImportPreview) => void
}) {
  const { t } = useTranslation()
  const tr = (key: string) => t(`labs.mcp.import.registry.${key}`)
  const result = buildRegistryImport(option, name, values)
  const oauthUnsupported = option.authType?.toLowerCase() === "oauth2"
  return (
    <div className="grid items-start gap-5 lg:grid-cols-2">
      <div className="min-w-0 space-y-4 rounded-[12px] border border-[var(--color-border)] p-4">
        {children}
        {!oauthUnsupported &&
          option.fields.map((field, index) => (
            <label key={field.key} className="flex flex-col gap-1 text-sm">
              {field.label}
              {field.required ? ` (${tr("requiredLabel")})` : ""}
              <Input
                className={importDialogStyles.field}
                type={field.secret ? "password" : "text"}
                value={values[field.key] ?? ""}
                onChange={(event) =>
                  onValueChange(field.key, event.target.value)
                }
                required={field.required}
                autoComplete="off"
                spellCheck={false}
                aria-describedby={
                  field.description ? `mcp-registry-field-${index}` : undefined
                }
              />
              {field.description ? (
                <span
                  id={`mcp-registry-field-${index}`}
                  className="text-xs text-[var(--color-text-description)]"
                >
                  {field.description}
                </span>
              ) : null}
            </label>
          ))}
        {result.error ? (
          <p role="status" className="text-xs text-[var(--color-error)]">
            {tr(result.error)}
          </p>
        ) : null}
      </div>
      {!oauthUnsupported ? (
        <section className="min-w-0 space-y-3 rounded-[12px] border border-[var(--color-border)] bg-[var(--color-bg-subtle)] p-4">
          <h4 className="text-sm leading-[22px] font-medium text-[var(--color-text-primary)]">
            {tr("rawPreview")}
          </h4>
          <pre className="max-h-[360px] overflow-auto rounded-[10px] border border-[var(--color-border)] bg-[var(--color-bg-page)] p-3 font-mono text-xs leading-5 [overflow-wrap:anywhere] whitespace-pre-wrap">
            {result.json}
          </pre>
          <Button
            className={`${importDialogStyles.primary} w-full`}
            data-product-guide="mcp-registry-preview"
            disabled={!!result.error}
            onClick={() => {
              if (!result.error)
                onPreview({
                  json: result.json,
                  headerValuesByServerId: result.headerValuesByServerId,
                })
            }}
          >
            {tr("preview")}
          </Button>
        </section>
      ) : null}
    </div>
  )
}
