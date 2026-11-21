import { writeClipboardText } from "@/lib/clipboard"
import { type ReactNode, useState } from "react"
import { useQuery } from "@tanstack/react-query"
import { QueryStateNotice } from "@/components/bizcharts/QueryStateNotice"
import { Blocks, Check, Copy, FileText, X } from "lucide-react"
import ReactMarkdown from "react-markdown"
import { useTranslation } from "react-i18next"
import { Link } from "react-router-dom"
import remarkGfm from "remark-gfm"

import {
  formatMs,
  formatRelativeTime,
} from "@/components/labs/shared/labsFormat"
import { Button } from "@/components/ui/button"
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetTitle,
} from "@/components/ui/sheet"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { cn } from "@/lib/utils"

import { HEALTH_TAG_CLASS, healthFromErrorRate } from "@/labs/mcp/mcpDisplay"

import {
  skillProvenanceCounts,
  skillQueryErrorCode,
  splitSkillFrontmatter,
  stripRepeatedTitleHeading,
} from "./skillDisplay"
import type {
  SkillCallRef,
  SkillConfig,
  SkillStat,
  SkillLoadState,
  SkillStatsState,
  SkillQueryParams,
  SkillHistoryPage,
  SkillHistoryFilters,
} from "./types"

interface SkillDrawerProps {
  skillName: string | null
  stat: SkillStat | null
  config?: SkillConfig
  statState?: SkillStatsState
  configState?: SkillLoadState
  statsError?: boolean
  configError?: boolean
  onRetryStats?: () => void
  onRetryConfig?: () => void
  deletePending?: boolean
  deleteError?: string
  history?: {
    params: SkillQueryParams
    load: (
      params: SkillQueryParams,
      name: string,
      filters: SkillHistoryFilters
    ) => Promise<SkillHistoryPage>
    onNarrowWindow?: () => void
  }
  onClose: () => void
  onDeleteConfig?: (name: string) => void
  /** Overview page: calls from other traces deep-link to the trace detail. */
  traceHref?: (traceId: string, spanId: string, call?: SkillCallRef) => string
  /** Opens the import dialog from the no-config guide (trace page hosts it). */
  onImport?: () => void
  /** Trace page: in-trace calls locate their span instead of navigating. */
  currentTraceId?: string
  onSelectSpan?: (spanId: string) => void
  onNavigateToTrace?: (traceId: string, spanId?: string) => void
  /** Trace-page hosts: no reimport on an imported config — config
   * management writes stay on the overview page. The no-config import
   * guide still uses onImport. */
  readOnly?: boolean
}

/** Same structure as the MCP server drawer: usage header, a config tab
 * (metadata + SKILL.md text) and a call-history tab with the config actions
 * on the tab row; no metrics strip and no relation list. */
export function SkillDrawer({
  skillName,
  stat,
  config,
  onClose,
  onDeleteConfig,
  traceHref,
  onImport,
  currentTraceId,
  onSelectSpan,
  onNavigateToTrace,
  readOnly,
  statState = "ready",
  configState = "ready",
  statsError,
  configError,
  onRetryStats,
  onRetryConfig,
  deletePending,
  deleteError,
  history,
}: SkillDrawerProps) {
  const { t } = useTranslation()
  const open = !!skillName
  const provenance = stat ? skillProvenanceCounts(stat) : null
  // Same four-state health as the MCP server drawer; unknown stays hidden.
  const health =
    !stat || stat.calls === 0
      ? "unknown"
      : healthFromErrorRate(stat.errorRate, stat.errors)
  // Config actions sit on the tab row; without a config the empty-state
  // import button replaces them.
  const reimportAction = config && !readOnly ? onImport : undefined
  const deleteAction =
    config && onDeleteConfig ? () => onDeleteConfig(config.name) : undefined

  return (
    <Sheet open={open} onOpenChange={(next) => (!next ? onClose() : undefined)}>
      <SheetContent
        showCloseButton={false}
        className="flex !w-full !max-w-full flex-col gap-0 overflow-hidden p-0 sm:!max-w-[70vw] xl:!max-w-[960px]"
      >
        <header className="flex shrink-0 items-start justify-between gap-3 border-b border-[var(--color-border-labs)] bg-[var(--color-bg-labs)] px-4 py-3">
          <div className="flex min-w-0 flex-1 flex-col gap-1">
            <div className="flex min-w-0 flex-wrap items-center gap-2">
              <span className="flex size-8 shrink-0 items-center justify-center rounded-lg bg-[var(--color-bg-labs-tag)] text-[var(--color-labs)]">
                <Blocks className="size-4" aria-hidden="true" />
              </span>
              <SheetTitle className="min-w-0 text-xl leading-7 font-medium [overflow-wrap:anywhere] break-words text-[var(--color-text-primary)]">
                {stat?.name ?? skillName ?? ""}
              </SheetTitle>
              {health !== "unknown" ? (
                <span
                  className={cn(
                    "inline-flex h-6 shrink-0 items-center rounded-[6px] px-2 text-xs leading-5 whitespace-nowrap",
                    HEALTH_TAG_CLASS[health]
                  )}
                >
                  {t(`labs.skill.health.${health}`)}
                </span>
              ) : null}
            </div>
            <SheetDescription className="text-sm leading-[22px] text-[var(--color-text-description)] empty:hidden">
              {stat
                ? t("labs.skill.drawer.usage", {
                    agents: stat.sourceAgents.join(", "),
                    time: stat.lastSeen
                      ? formatRelativeTime(stat.lastSeen, t)
                      : t("labs.skill.topic.timeUnknown"),
                  })
                : statState === "ready" &&
                    !statsError &&
                    config &&
                    configState === "ready"
                  ? t("labs.skill.drawer.usageImported")
                  : null}
            </SheetDescription>
          </div>
          <Button
            variant="ghost"
            size="icon"
            className="size-8 shrink-0 rounded-[10px]"
            onClick={onClose}
            aria-label={t("labs.common.close")}
          >
            <X className="size-4" />
          </Button>
        </header>

        {deleteError ? (
          <div
            role="alert"
            className="flex flex-wrap items-center gap-2 px-4 py-2 text-xs text-[var(--color-error)]"
          >
            <span>{t("labs.skill.topic.removeFailed")}</span>
            <span className="break-all">{deleteError}</span>
            <button
              type="button"
              onClick={deleteAction}
              disabled={deletePending}
              className="rounded underline disabled:opacity-50"
            >
              {t("common.retry")}
            </button>
          </div>
        ) : null}
        <Tabs
          key={skillName}
          defaultValue="skillmd"
          className="flex min-h-0 flex-1 flex-col gap-0"
        >
          <div className="flex min-w-0 shrink-0 flex-wrap items-center justify-between gap-x-3 gap-y-2 px-4 pt-2">
            <TabsList variant="line" className="max-w-full min-w-0 shrink-0">
              <TabsTrigger
                value="skillmd"
                className="focus-visible:outline-[var(--color-labs)] data-[state=active]:text-[var(--color-labs)] data-[state=active]:after:bg-[var(--color-labs)] data-[state=active]:after:opacity-100 dark:data-[state=active]:text-[var(--color-labs)]"
              >
                {config?.source === "manifest"
                  ? t("labs.skill.topic.definition")
                  : "SKILL.md"}
              </TabsTrigger>
              <TabsTrigger
                value="calls"
                className="focus-visible:outline-[var(--color-labs)] data-[state=active]:text-[var(--color-labs)] data-[state=active]:after:bg-[var(--color-labs)] data-[state=active]:after:opacity-100 dark:data-[state=active]:text-[var(--color-labs)]"
              >
                {t("labs.skill.drawer.callsTab")}
              </TabsTrigger>
            </TabsList>
            {reimportAction || deleteAction ? (
              <div className="flex min-w-0 flex-wrap items-center gap-2">
                {reimportAction ? (
                  <Button
                    variant="outline"
                    size="sm"
                    className="rounded-[8px] border-[var(--color-border-labs)] text-[var(--color-labs)] hover:bg-[var(--color-bg-labs-tag)] hover:text-[var(--color-labs)] dark:hover:bg-[var(--color-bg-labs-tag)]"
                    onClick={reimportAction}
                  >
                    <FileText className="size-4" />
                    {t("labs.skill.drawer.reimport")}
                  </Button>
                ) : null}
                {deleteAction ? (
                  <Button
                    variant="ghost"
                    size="sm"
                    className="rounded-[8px] text-[var(--color-text-secondary)]"
                    onClick={deleteAction}
                    disabled={deletePending}
                  >
                    {t(
                      deletePending
                        ? "labs.skill.topic.removing"
                        : "labs.skill.drawer.removeConfig"
                    )}
                  </Button>
                ) : null}
              </div>
            ) : null}
          </div>
          <div className="min-h-0 flex-1 overflow-y-auto px-4 py-3">
            <TabsContent value="skillmd" className="space-y-3">
              {configError ? (
                <QueryStateNotice
                  error={t("labs.skill.configsFailed")}
                  inlineError={!config}
                  hasData={!!config}
                  onRetry={onRetryConfig}
                />
              ) : null}
              {config ? (
                <SkillConfigPanel config={config} />
              ) : !configError ? (
                <div className="flex flex-col items-start gap-3 rounded-[12px] border border-dashed border-[var(--color-border-labs)] bg-[var(--color-bg-labs)] p-4">
                  <p className="text-xs leading-5 text-[var(--color-text-description)]">
                    {t(
                      configState === "ready"
                        ? "labs.skill.drawer.noConfig"
                        : configState === "loading"
                          ? "labs.common.loading"
                          : "metrics.loadFailed"
                    )}
                  </p>
                  {onImport && configState === "ready" ? (
                    <Button
                      variant="outline"
                      size="sm"
                      className="rounded-[8px] border-[var(--color-border-labs)] text-[var(--color-labs)] hover:bg-[var(--color-bg-labs-tag)] hover:text-[var(--color-labs)] dark:hover:bg-[var(--color-bg-labs-tag)]"
                      onClick={onImport}
                    >
                      <FileText className="size-4" />
                      {t("labs.skill.import.open")}
                    </Button>
                  ) : null}
                </div>
              ) : null}
            </TabsContent>

            <TabsContent value="calls" className="space-y-3">
              {statsError ? (
                <QueryStateNotice
                  error={t("labs.skill.scanFailed")}
                  inlineError={!stat}
                  hasData={!!stat}
                  onRetry={onRetryStats}
                />
              ) : null}
              {history ? (
                <p className="text-xs text-muted-foreground">
                  {t("labs.skill.topic.windowScope", {
                    agent: history.params.agentProduct,
                    from: history.params.from || history.params.timeRange,
                    to: history.params.to || "",
                  })}
                </p>
              ) : null}
              {config && (history || (stat && stat.calls > 0)) ? (
                <p className="text-xs text-muted-foreground">
                  {t("labs.skill.topic.unlinkedDefinition")}
                </p>
              ) : null}
              {history || (stat && stat.calls > 0) ? (
                <p className="text-xs leading-5 text-[var(--color-text-description)]">
                  {t("labs.skill.provenance.help")}
                </p>
              ) : null}
              {stat && stat.calls > 0 ? (
                <p className="text-xs leading-5 text-[var(--color-text-description)]">
                  {provenance
                    ? t("labs.skill.provenance.counts", provenance)
                    : t("labs.skill.provenance.unknownTotals")}
                  {typeof stat.measuredCalls === "number"
                    ? ` · ${t("labs.skill.topic.measured", {
                        measured: stat.measuredCalls,
                        total: stat.calls,
                      })}`
                    : null}
                </p>
              ) : null}
              {statState === "idle" ? (
                <p role="status" className="text-sm text-muted-foreground">
                  {t("dashboard.noAgentDesc")}
                </p>
              ) : history && skillName ? (
                <SkillCallHistory
                  key={`${skillName}:${JSON.stringify(history.params)}`}
                  name={skillName}
                  history={history}
                  currentTraceId={currentTraceId}
                  onSelectSpan={onSelectSpan}
                  onNavigateToTrace={onNavigateToTrace}
                  traceHref={traceHref}
                />
              ) : statsError ? null : statState !== "ready" ? (
                <p className="text-sm text-muted-foreground">
                  {t(
                    statState === "loading"
                      ? "labs.common.loading"
                      : "metrics.loadFailed"
                  )}
                </p>
              ) : (
                <RecentCalls
                  calls={stat?.recentCalls ?? []}
                  currentTraceId={currentTraceId}
                  onSelectSpan={onSelectSpan}
                  onNavigateToTrace={onNavigateToTrace}
                  traceHref={traceHref}
                />
              )}
            </TabsContent>
          </div>
        </Tabs>
      </SheetContent>
    </Sheet>
  )
}

/** Drawer-side config presentation, mirroring the MCP config card: metadata
 * rows, then the stored SKILL.md full text; the reimport / remove actions
 * live in the drawer header. */
function SkillConfigPanel({ config }: { config: SkillConfig }) {
  const { t } = useTranslation()
  const content = skillContent(config)
  const rawImportSource = config.raw.importSource
  const importSource =
    rawImportSource && typeof rawImportSource === "object"
      ? (rawImportSource as Record<string, unknown>)
      : undefined
  return (
    <div className="flex flex-col gap-4">
      <p className="flex flex-wrap items-baseline gap-x-2 gap-y-1 text-xs text-muted-foreground">
        <span className="font-medium text-foreground">
          {t("labs.skill.topic.currentImportCopy")}
        </span>
        <span>{t("labs.skill.topic.unlinkedDefinition")}</span>
      </p>
      <section className="rounded-[12px] border border-[var(--color-border)] px-4">
        <dl>
          {config.path ? (
            <Row label={t("labs.skill.topic.path")}>
              <span className="font-mono break-all">{config.path}</span>
            </Row>
          ) : null}
          <Row label={t("labs.skill.topic.definitionSource")}>
            <span>{config.source}</span>
          </Row>
          <Row label={t("labs.skill.topic.configScope")}>
            <span>{t("labs.skill.topic.currentSpaceConfig")}</span>
          </Row>
          {config.description ? (
            <Row label={t("labs.skill.span.description")}>
              <span className="whitespace-pre-wrap">{config.description}</span>
            </Row>
          ) : null}
          {config.allowedTools.length ? (
            <Row label={t("labs.skill.span.allowedTools")}>
              <div className="flex flex-wrap items-center gap-2">
                {config.allowedTools.map((tool) => (
                  <span
                    key={tool}
                    className="inline-flex h-6 items-center rounded-[6px] border border-[var(--color-border)] px-2 font-mono text-xs leading-5 text-[var(--color-text-secondary)]"
                  >
                    {tool}
                  </span>
                ))}
              </div>
            </Row>
          ) : null}
          {config.license ? (
            <Row label={t("labs.skill.span.license")}>
              <span className="font-mono text-sm">{config.license}</span>
            </Row>
          ) : null}
          {config.compatibility ? (
            <Row label={t("labs.skill.span.compatibility")}>
              <span>{config.compatibility}</span>
            </Row>
          ) : null}
          {typeof importSource?.repository === "string" ? (
            <Row label={t("labs.skill.drawer.repository")}>
              <span className="font-mono break-all">
                {importSource.repository}
              </span>
            </Row>
          ) : null}
          {typeof importSource?.ref === "string" ? (
            <Row label={t("labs.skill.drawer.requestedRef")}>
              <div className="flex min-w-0 flex-col gap-1">
                <span className="font-mono break-all">{importSource.ref}</span>
                <span className="text-xs text-muted-foreground">
                  {t("labs.skill.topic.requestedRefMeaning")}
                </span>
              </div>
            </Row>
          ) : null}
          {typeof importSource?.path === "string" ? (
            <Row label={t("labs.skill.drawer.repositoryPath")}>
              <span className="font-mono break-all">{importSource.path}</span>
            </Row>
          ) : null}
          <Row label={t("labs.skill.drawer.importedAt")}>
            {config.importedAt ? (
              <time dateTime={config.importedAt} className="break-all">
                {config.importedAt}
              </time>
            ) : (
              <span>{t("labs.skill.topic.importTimeUnknown")}</span>
            )}
          </Row>
        </dl>
      </section>

      {content ? <SkillMarkdownView content={content} /> : null}
      <RawSkillDefinition raw={config.raw} />
    </div>
  )
}

/** Rendered SKILL.md view: frontmatter keys surface as a compact metadata
 * card and the body renders as Markdown (clickable links, GFM tables), both
 * framed in one bordered card under the raw-source copy button row. */
function SkillMarkdownView({ content }: { content: string }) {
  const { t } = useTranslation()
  const [copied, setCopied] = useState(false)
  const [copyFailed, setCopyFailed] = useState(false)
  const { meta, body } = splitSkillFrontmatter(content)
  // The drawer title and metadata card already carry the skill name; a
  // leading `# <name>` H1 would repeat it as an oversized heading.
  const markdownBody = stripRepeatedTitleHeading(
    body,
    meta.find((item) => item.key === "name")?.value ?? ""
  )

  const copy = async () => {
    try {
      await writeClipboardText(content)
      setCopied(true)
      setCopyFailed(false)
      window.setTimeout(() => setCopied(false), 1500)
    } catch {
      setCopied(false)
      setCopyFailed(true)
    }
  }

  return (
    <section className="flex flex-col gap-3">
      <div className="flex items-center gap-2">
        <p className="text-sm leading-[22px] font-semibold text-[var(--color-text-primary)]">
          {t("labs.skill.drawer.fullText")}
        </p>
        <button
          type="button"
          onClick={copy}
          aria-label={t("labs.common.copy")}
          className="ml-auto shrink-0 rounded-[6px] text-[var(--color-icon-normal)] hover:text-[var(--color-icon-primary)] focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--color-text-primary)]"
        >
          {copied ? (
            <Check className="size-4 text-[var(--color-success)]" />
          ) : (
            <Copy className="size-4" />
          )}
        </button>
      </div>
      {copyFailed ? (
        <p role="alert" className="text-xs text-destructive">
          {t("labs.skill.topic.copyFailed")}
        </p>
      ) : null}
      <div className="flex flex-col gap-4 rounded-[12px] border border-[var(--color-border)] px-4 py-4">
        {meta.length ? (
          <div className="rounded-[12px] bg-[var(--color-bg-subtle)] px-4 py-3">
            <p className="text-sm leading-[22px] text-[var(--color-text-description)]">
              {t("labs.skill.drawer.metadata")}
            </p>
            <dl className="mt-2 flex flex-col gap-1">
              {meta.map((item) => (
                <div
                  key={item.key}
                  className="grid grid-cols-[112px_minmax(0,1fr)] gap-4"
                >
                  <dt className="font-mono text-sm leading-[22px] break-words text-[var(--color-text-secondary)]">
                    {item.key}
                  </dt>
                  <dd className="font-mono text-sm leading-[22px] break-words whitespace-pre-wrap text-[var(--color-text-primary)]">
                    {item.value}
                  </dd>
                </div>
              ))}
            </dl>
          </div>
        ) : null}
        <div className="text-sm leading-6 break-words text-[var(--color-text-primary)] [&_:not(pre)>code]:rounded-[6px] [&_:not(pre)>code]:bg-[var(--color-bg-subtle)] [&_:not(pre)>code]:px-1 [&_:not(pre)>code]:font-mono [&_:not(pre)>code]:text-xs">
          <ReactMarkdown
            remarkPlugins={[remarkGfm]}
            components={{
              a: ({ children, href }) =>
                href && /^https?:\/\//i.test(href) ? (
                  <a
                    href={href}
                    target="_blank"
                    rel="noreferrer"
                    className="break-all text-blue-600 underline underline-offset-2 dark:text-blue-400"
                  >
                    {children}
                  </a>
                ) : (
                  <span
                    title={t("labs.skill.drawer.localReference", {
                      path: href ?? "",
                    })}
                    className="break-all"
                  >
                    {children}
                  </span>
                ),
              h1: ({ children }) => (
                <h1 className="mt-5 mb-2 text-lg leading-7 font-semibold">
                  {children}
                </h1>
              ),
              h2: ({ children }) => (
                <h2 className="mt-5 mb-2 text-base leading-6 font-semibold">
                  {children}
                </h2>
              ),
              h3: ({ children }) => (
                <h3 className="mt-4 mb-2 text-sm leading-[22px] font-semibold">
                  {children}
                </h3>
              ),
              h4: ({ children }) => (
                <h4 className="mt-4 mb-2 text-sm leading-[22px] font-medium">
                  {children}
                </h4>
              ),
              p: ({ children }) => <p className="my-3">{children}</p>,
              ul: ({ children }) => (
                <ul className="my-3 list-disc space-y-1 pl-6">{children}</ul>
              ),
              ol: ({ children }) => (
                <ol className="my-3 list-decimal space-y-1 pl-6">{children}</ol>
              ),
              li: ({ children }) => (
                <li className="marker:text-[var(--color-text-description)]">
                  {children}
                </li>
              ),
              blockquote: ({ children }) => (
                <blockquote className="my-3 border-l-2 border-[var(--color-border)] pl-3 text-[var(--color-text-secondary)]">
                  {children}
                </blockquote>
              ),
              hr: () => <hr className="my-4 border-[var(--color-border)]" />,
              pre: ({ children }) => (
                <pre className="my-3 overflow-x-auto rounded-[10px] border border-[var(--color-border)] bg-[var(--color-bg-subtle)] p-3 font-mono text-xs leading-5">
                  {children}
                </pre>
              ),
              table: ({ children }) => (
                <table className="my-3 w-full border-collapse">
                  {children}
                </table>
              ),
              th: ({ children }) => (
                <th className="border-b border-[var(--color-border)] px-3 py-2 text-left font-semibold">
                  {children}
                </th>
              ),
              td: ({ children }) => (
                <td className="border-b border-[var(--color-border)] px-3 py-2 align-top">
                  {children}
                </td>
              ),
            }}
          >
            {markdownBody}
          </ReactMarkdown>
        </div>
      </div>
    </section>
  )
}

function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="grid grid-cols-1 gap-x-3 gap-y-1 py-2.5 not-last:border-b not-last:border-[var(--color-border)] sm:grid-cols-[96px_minmax(0,1fr)]">
      <dt className="text-sm leading-[22px] text-[var(--color-text-description)]">
        {label}
      </dt>
      <dd className="flex min-w-0 flex-col gap-1 text-sm leading-[22px] text-[var(--color-text-primary)]">
        {children}
      </dd>
    </div>
  )
}

const CALL_LINK_CLASS =
  "shrink-0 rounded-[6px] text-xs leading-5 text-[var(--color-labs)] underline decoration-[var(--color-border-labs)] underline-offset-4 hover:decoration-current focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--color-labs)]"

function RecentCalls({
  calls,
  emptyMessage,
  currentTraceId,
  onSelectSpan,
  onNavigateToTrace,
  traceHref,
}: {
  calls: SkillCallRef[]
  emptyMessage?: string
  currentTraceId?: string
  onSelectSpan?: (spanId: string) => void
  onNavigateToTrace?: (traceId: string, spanId?: string) => void
  traceHref?: (traceId: string, spanId: string, call?: SkillCallRef) => string
}) {
  const { t } = useTranslation()
  if (!calls.length) {
    return (
      <p className="rounded-[12px] border border-dashed border-[var(--color-border)] px-3 py-6 text-center text-xs leading-5 text-[var(--color-text-description)]">
        {emptyMessage ?? t("labs.skill.drawer.noCalls")}
      </p>
    )
  }
  return (
    <ul className="flex flex-col divide-y divide-[var(--color-border)] rounded-[12px] border border-[var(--color-border)]">
      {calls.map((call) => (
        <li
          key={`${call.traceId}-${call.spanId}`}
          className="grid grid-cols-[auto_minmax(0,1fr)] items-start gap-x-3 gap-y-2 px-3 py-2.5 sm:flex sm:flex-wrap"
        >
          <span
            className={cn(
              "inline-flex h-6 shrink-0 items-center rounded-[6px] px-2 text-xs leading-5 whitespace-nowrap",
              call.status === "ok"
                ? "bg-[var(--color-bg-success)] text-[var(--color-success)]"
                : call.status === "error"
                  ? "bg-[var(--color-bg-error)] text-[var(--color-error)]"
                  : "bg-[var(--color-bg-hover)] text-[var(--color-text-secondary)]"
            )}
          >
            {t(
              call.status === "unknown"
                ? "spanContent.statusUnknown"
                : `labs.skill.provenance.${call.derived ? "read" : "node"}${call.status === "ok" ? "Ok" : "Error"}`
            )}
          </span>
          <div className="flex min-w-0 flex-1 flex-col">
            <span className="font-mono text-xs leading-5 break-all text-[var(--color-text-primary)]">
              {call.name}
            </span>
            <span className="text-xs leading-5 text-[var(--color-text-description)]">
              {t(
                call.derived
                  ? "labs.skill.provenance.inferred"
                  : "labs.skill.provenance.reported"
              )}
              {" · "}
              {call.agentProduct},{" "}
              {call.timestamp
                ? formatRelativeTime(call.timestamp, t)
                : t("labs.skill.topic.timeUnknown")}
            </span>
          </div>
          <div className="col-start-2 flex flex-wrap items-center justify-between gap-3 sm:contents">
            <span className="shrink-0 text-xs leading-5 text-[var(--color-text-description)] sm:text-right">
              <span className="block">
                {t(
                  call.derived
                    ? "labs.skill.provenance.readDuration"
                    : "labs.skill.provenance.nodeDuration"
                )}
              </span>
              <span className="font-stats text-sm text-[var(--color-text-secondary)]">
                {call.durationMs === null
                  ? t("labs.skill.topic.notMeasured")
                  : formatMs(call.durationMs)}
              </span>
            </span>
            <CallExit
              call={call}
              currentTraceId={currentTraceId}
              onSelectSpan={onSelectSpan}
              onNavigateToTrace={onNavigateToTrace}
              traceHref={traceHref}
            />
          </div>
          <details className="col-span-2 min-w-0 basis-full text-xs text-muted-foreground">
            <summary className="cursor-pointer rounded focus-visible:outline-2">
              {t("labs.skill.topic.callDetails")}
            </summary>
            <dl className="mt-2 space-y-1 break-all">
              <div>
                <dt>{t("labs.skill.topic.exactTime")}</dt>
                <dd>{call.timestamp ?? t("labs.skill.topic.timeUnknown")}</dd>
              </div>
              <div>
                <dt>Trace ID / Span ID</dt>
                <dd>
                  {call.traceId} / {call.spanId}
                </dd>
              </div>
            </dl>
            {call.lastError ? (
              <RawSkillDefinition raw={{ error: call.lastError }} />
            ) : null}
          </details>
        </li>
      ))}
    </ul>
  )
}

/** Call history exits: in-trace calls locate their span in place, other
 * traces switch the page in place (same-URL link navigation would not
 * remount the detail state), hosts without handlers fall back to a deep
 * link. Pages without any exit render no action. */
function CallExit({
  call,
  currentTraceId,
  onSelectSpan,
  onNavigateToTrace,
  traceHref,
}: {
  call: SkillCallRef
  currentTraceId?: string
  onSelectSpan?: (spanId: string) => void
  onNavigateToTrace?: (traceId: string, spanId?: string) => void
  traceHref?: (traceId: string, spanId: string, call?: SkillCallRef) => string
}) {
  const { t } = useTranslation()
  if (onSelectSpan && call.traceId === currentTraceId) {
    return (
      <button
        type="button"
        onClick={() => onSelectSpan(call.spanId)}
        className={CALL_LINK_CLASS}
      >
        {t("labs.skill.drawer.viewTrace")}
      </button>
    )
  }
  if (onNavigateToTrace) {
    return (
      <button
        type="button"
        onClick={() => onNavigateToTrace(call.traceId, call.spanId)}
        className={CALL_LINK_CLASS}
      >
        {t("labs.skill.drawer.viewTrace")}
      </button>
    )
  }
  if (traceHref) {
    return (
      <Link
        to={traceHref(call.traceId, call.spanId, call)}
        className={CALL_LINK_CLASS}
      >
        {t("labs.skill.drawer.viewTrace")}
      </Link>
    )
  }
  return null
}

function skillContent(config?: SkillConfig): string {
  const content = config?.raw?.content
  return typeof content === "string" ? content : ""
}

function RawSkillDefinition({ raw }: { raw: unknown }) {
  const { t } = useTranslation()
  const [copied, setCopied] = useState(false)
  const [failed, setFailed] = useState(false)
  const value = typeof raw === "string" ? raw : JSON.stringify(raw, null, 2)
  const copy = async () => {
    try {
      await writeClipboardText(value)
      setCopied(true)
      setFailed(false)
    } catch {
      setCopied(false)
      setFailed(true)
    }
  }
  return (
    <details className="rounded-lg border border-[var(--color-border)] p-3">
      <summary className="cursor-pointer rounded text-xs font-medium focus-visible:outline-2">
        {t("labs.skill.topic.rawDefinition")}
      </summary>
      <div className="mt-2 flex items-center justify-end gap-2">
        <button
          type="button"
          onClick={() => void copy()}
          className="inline-flex items-center gap-1 rounded px-2 py-1 text-xs hover:bg-muted focus-visible:outline-2"
        >
          {copied ? <Check className="size-3" /> : <Copy className="size-3" />}
          {t(copied ? "labs.skill.topic.copied" : "labs.common.copy")}
        </button>
        {failed ? (
          <span role="alert" className="text-xs text-destructive">
            {t("labs.skill.topic.copyFailed")}
          </span>
        ) : null}
      </div>
      <pre className="max-h-96 overflow-auto font-mono text-xs leading-5 break-all whitespace-pre-wrap">
        {value}
      </pre>
    </details>
  )
}

function SkillCallHistory({
  name,
  history,
  ...exits
}: {
  name: string
  history: NonNullable<SkillDrawerProps["history"]>
  currentTraceId?: string
  onSelectSpan?: (id: string) => void
  onNavigateToTrace?: (trace: string, span?: string) => void
  traceHref?: (trace: string, span: string, call?: SkillCallRef) => string
}) {
  const { t } = useTranslation()
  const [page, setPage] = useState(1)
  const [limit, setLimit] = useState(5)
  const [status, setStatus] = useState<SkillHistoryFilters["status"]>("all")
  const [provenance, setProvenance] =
    useState<SkillHistoryFilters["provenance"]>("all")
  const [sort, setSort] = useState<SkillHistoryFilters["sort"]>("recent")
  const filters = { page, limit, status, provenance, sort }
  const query = useQuery({
    queryKey: ["labs", "skill", "history", history.params, name, filters],
    queryFn: () => history.load(history.params, name, filters),
    retry: false,
    staleTime: 15000,
  })
  const result = query.data
  const field =
    "h-8 max-w-full rounded-lg border border-[var(--color-border)] bg-[var(--color-bg-page)] px-2 text-xs"
  return (
    <div className="flex min-w-0 flex-col gap-3">
      <div className="flex flex-wrap gap-2">
        <select
          value={status}
          aria-label={t("labs.skill.topic.historyStatus")}
          className={field}
          onChange={(event) => {
            setStatus(event.target.value as SkillHistoryFilters["status"])
            setPage(1)
          }}
        >
          {["all", "ok", "error", "unknown"].map((value) => (
            <option key={value} value={value}>
              {t(
                value === "unknown"
                  ? "spanContent.statusUnknown"
                  : `labs.skill.topic.history${value}`
              )}
            </option>
          ))}
        </select>
        <select
          value={provenance}
          aria-label={t("labs.skill.topic.historySource")}
          className={field}
          onChange={(event) => {
            setProvenance(
              event.target.value as SkillHistoryFilters["provenance"]
            )
            setPage(1)
          }}
        >
          {["all", "inferred", "reported"].map((value) => (
            <option key={value} value={value}>
              {t(`labs.skill.topic.source${value}`)}
            </option>
          ))}
        </select>
        <select
          value={sort}
          aria-label={t("labs.skill.topic.historySort")}
          className={field}
          onChange={(event) => {
            setSort(event.target.value as SkillHistoryFilters["sort"])
            setPage(1)
          }}
        >
          {["recent", "oldest", "duration_desc"].map((value) => (
            <option key={value} value={value}>
              {t(`labs.skill.topic.history${value}`)}
            </option>
          ))}
        </select>
        <select
          value={limit}
          aria-label={t("labs.skill.topic.pageSize")}
          className={field}
          onChange={(event) => {
            setLimit(Number(event.target.value))
            setPage(1)
          }}
        >
          {[5, 20, 50, 100].map((value) => (
            <option key={value} value={value}>
              {t("labs.skill.topic.perPage", { count: value })}
            </option>
          ))}
        </select>
      </div>
      <QueryStateNotice
        error={
          query.error instanceof Error
            ? query.error.message
            : query.isError
              ? t("metrics.loadFailed")
              : undefined
        }
        loading={query.isFetching}
        hasData={!!result}
        updatedAt={query.dataUpdatedAt}
        onRetry={() => void query.refetch()}
      />
      {skillQueryErrorCode(query.error) === 3027 && history.onNarrowWindow ? (
        <button
          type="button"
          onClick={history.onNarrowWindow}
          className="self-start rounded text-xs underline"
        >
          {t("labs.skill.topic.narrowWindow")}
        </button>
      ) : null}
      {query.isLoading ? (
        <p className="text-sm text-muted-foreground">
          {t("labs.common.loading")}
        </p>
      ) : result ? (
        <>
          <p className="text-xs text-muted-foreground">
            {t("labs.skill.topic.historyCount", {
              shown: result.calls?.length ?? 0,
              total: result.total,
              all: result.unfilteredTotal,
            })}
          </p>
          <p className="text-xs text-muted-foreground">
            {t(
              result.complete
                ? "labs.skill.topic.historyComplete"
                : "labs.skill.topic.historyPartial",
              { count: result.spanCount }
            )}
          </p>
          <RecentCalls
            calls={result.calls ?? []}
            emptyMessage={
              result.total === 0 && result.unfilteredTotal > 0
                ? t("labs.skill.drawer.noMatchingCalls")
                : undefined
            }
            {...exits}
          />
          <div className="flex flex-wrap items-center justify-between gap-2 text-xs">
            <Button
              size="sm"
              variant="outline"
              disabled={page <= 1 || query.isFetching}
              onClick={() => setPage((value) => value - 1)}
            >
              {t("labs.skill.topic.previous")}
            </Button>
            <span>
              {t("labs.skill.topic.page", {
                page: result.page,
                pages: result.totalPages,
              })}
            </span>
            <Button
              size="sm"
              variant="outline"
              disabled={page >= result.totalPages || query.isFetching}
              onClick={() => setPage((value) => value + 1)}
            >
              {t("labs.skill.topic.next")}
            </Button>
          </div>
        </>
      ) : null}
    </div>
  )
}
