import { useTranslation } from "react-i18next"
import { Link, useLocation } from "react-router-dom"
import { Database, History } from "lucide-react"

import { formatRelativeTime } from "@/components/labs/shared/labsFormat"
import { TraceTextContent } from "@/components/traces/shared/TraceTextContent"
import { LabsLoadMore } from "@/components/labs/shared/LabsLoadMore"
import { McpServerDrawer } from "../mcp/McpServerDrawer"
import type { McpDrawerTab, McpServerConfig } from "../mcp/types"
import { memoryMcpDetail } from "./memoryMcpPresentation"
import { ActivityTable } from "./MemoryActivityTable"
import type {
  MemoryEntryRow,
  MemoryListSnapshot,
  MemoryObservationResult,
  MemoryServer,
} from "./types"

const CELL = "px-3 py-2 text-left align-top"
const LINK =
  "rounded text-[var(--color-labs)] underline decoration-[var(--color-border-labs)] underline-offset-4 hover:decoration-current focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--color-labs)]"

function useTraceHref(window?: MemoryObservationResult["window"]) {
  const location = useLocation()
  return (traceId: string, spanId: string, agent?: string) => {
    const params = new URLSearchParams(location.search)
    params.delete("server")
    params.delete("tab")
    params.delete("query_traceid")
    if (window) {
      params.set("from", window.from)
      params.set("to", window.to)
      params.set("trace_from", window.from)
      params.set("trace_to", window.to)
    } else if (params.get("traceId") !== traceId) {
      params.delete("trace_from")
      params.delete("trace_to")
    }
    params.set("traceId", traceId)
    params.set("focus_span", spanId)
    if (agent) params.set("agent", agent)
    return `/trace?${params}`
  }
}

export function MemoryEntries({
  entries,
  snapshot,
  window,
}: {
  entries: MemoryEntryRow[]
  snapshot?: MemoryListSnapshot | null
  window?: MemoryObservationResult["window"]
}) {
  const { t } = useTranslation()
  const traceHref = useTraceHref(window)
  return (
    <section className="flex min-w-0 flex-col gap-3">
      <p className="text-xs leading-5 text-[var(--color-text-secondary)]">
        {t("labs.memory.activity.entriesHelp")}
      </p>
      {snapshot && (
        <section
          data-memory-snapshot
          className="min-w-0 rounded-lg border border-[var(--color-border-labs)] bg-[var(--color-bg-page)] text-xs leading-5"
        >
          <div className="flex flex-wrap items-center justify-between gap-2 rounded-t-lg border-b border-[var(--color-border-labs)] bg-[var(--color-bg-labs)] px-3 py-2.5">
            <h3 className="flex min-w-0 items-center gap-2 font-medium text-[var(--color-text-primary)]">
              <History
                className="size-3.5 shrink-0 text-[var(--color-labs)]"
                aria-hidden="true"
              />
              {t("labs.memory.activity.snapshotTitle")}
            </h3>
            <span className="rounded border border-[var(--color-border)] bg-[var(--color-bg-page)] px-1.5 text-[var(--color-text-secondary)]">
              {t(
                snapshot.partial
                  ? "labs.memory.activity.snapshotPartial"
                  : "labs.memory.activity.snapshotComplete"
              )}
            </span>
          </div>
          <div className="space-y-3 p-3">
            <p className="text-[var(--color-text-description)]">
              {t("labs.memory.activity.snapshotHelp")}
            </p>
            <dl className="grid grid-cols-2 gap-3">
              <div>
                <dt className="text-[var(--color-text-description)]">
                  {t("labs.memory.activity.snapshotReturned")}
                </dt>
                <dd className="font-stats text-lg leading-7 font-medium text-[var(--color-text-primary)]">
                  {snapshot.returnedCount.toLocaleString()}
                </dd>
              </div>
              <div>
                <dt className="text-[var(--color-text-description)]">
                  {t("labs.memory.activity.snapshotActive")}
                </dt>
                <dd className="font-stats text-lg leading-7 font-medium text-[var(--color-text-primary)]">
                  {snapshot.activeCount?.toLocaleString() ?? "—"}
                </dd>
              </div>
            </dl>
            <div className="space-y-2 border-t border-[var(--color-border)] pt-3">
              <dl className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-3 gap-y-1">
                <dt className="text-[var(--color-text-description)]">
                  {t("labs.memory.span.tool")}
                </dt>
                <dd className="min-w-0 font-mono [overflow-wrap:anywhere] text-[var(--color-text-secondary)]">
                  {snapshot.toolName}
                </dd>
              </dl>
              {snapshot.argsSummary && (
                <TraceTextContent
                  content={snapshot.argsSummary}
                  previewLines={2}
                  className="min-w-0 text-[var(--color-text-description)]"
                  copyLabel={`${t("common.copy")}: ${t("labs.memory.trace.args")}`}
                />
              )}
              <div className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1 text-[var(--color-text-description)]">
                <span title={snapshot.timestamp}>
                  {snapshot.timestamp
                    ? formatRelativeTime(snapshot.timestamp, t)
                    : "—"}
                </span>
                <Link
                  className={LINK}
                  to={traceHref(snapshot.traceId, snapshot.spanId)}
                >
                  {t("labs.memory.activity.viewTrace")}
                </Link>
              </div>
            </div>
          </div>
        </section>
      )}
      {entries.length ? (
        <LabsLoadMore items={entries}>
          {(visibleEntries) => (
            <ActivityTable
              headers={[
                t("labs.memory.activity.content"),
                {
                  label: t("labs.memory.activity.hits"),
                  help: t("labs.memory.activity.hitsHelp"),
                },
                {
                  label: t("labs.memory.activity.lastSeen"),
                  help: t("labs.memory.activity.lastSeenHelp"),
                },
                t("labs.memory.activity.trace"),
              ]}
            >
              {visibleEntries.map((entry) => (
                <tr
                  key={JSON.stringify([
                    entry.lastTraceId,
                    entry.lastSpanId,
                    entry.content,
                  ])}
                >
                  <td className={`${CELL} w-3/5 max-w-0`}>
                    <TraceTextContent
                      content={entry.content}
                      previewLines={2}
                      textClassName="font-sans leading-5 text-[var(--color-text-primary)]"
                      copyLabel={`${t("common.copy")}: ${t("labs.memory.activity.content")}`}
                    />
                  </td>
                  <td className={`${CELL} font-stats`}>{entry.reuses}</td>
                  <td className={`${CELL} whitespace-nowrap`}>
                    {entry.lastUsedAt
                      ? formatRelativeTime(entry.lastUsedAt, t)
                      : "—"}
                  </td>
                  <td className={`${CELL} whitespace-nowrap`}>
                    <Link
                      className={LINK}
                      to={traceHref(entry.lastTraceId, entry.lastSpanId)}
                    >
                      {t("labs.memory.activity.viewTrace")}
                    </Link>
                  </td>
                </tr>
              ))}
            </ActivityTable>
          )}
        </LabsLoadMore>
      ) : !snapshot ? (
        <div className="flex items-start gap-3 rounded-lg border border-dashed border-[var(--color-border-labs)] bg-[var(--color-bg-labs)] p-4">
          <span className="flex size-8 shrink-0 items-center justify-center rounded-lg bg-[var(--color-bg-labs-tag)] text-[var(--color-labs)]">
            <Database className="size-4" aria-hidden="true" />
          </span>
          <p className="self-center text-xs leading-5 text-[var(--color-text-secondary)]">
            {t("labs.memory.activity.noEntries")}
          </p>
        </div>
      ) : null}
    </section>
  )
}

export function MemoryServerDrawer({
  server,
  config,
  statsAvailable,
  onClose,
  onImportConfig,
  onDeleteConfig,
  initialTab,
  onTabChange,
  callsLimit,
  window,
}: {
  server: MemoryServer | null
  config?: McpServerConfig
  statsAvailable: boolean
  onClose: () => void
  onImportConfig: (serverId: string) => void
  onDeleteConfig: (serverId: string) => void
  initialTab?: McpDrawerTab
  onTabChange: (tab: string) => void
  callsLimit: number
  window?: MemoryObservationResult["window"]
}) {
  const { t } = useTranslation()
  return (
    <McpServerDrawer
      feature="memory"
      serverId={server?.serverId ?? null}
      detail={server ? memoryMcpDetail(server, statsAvailable, t) : null}
      config={config}
      tab={initialTab ?? "entries"}
      onTabChange={onTabChange}
      onClose={onClose}
      onImport={() => server && onImportConfig(server.serverId)}
      onDeleteConfig={onDeleteConfig}
      queryWindow={window}
      recentCallsNote={
        <p className="rounded-r-md border-l-2 border-[var(--color-border-labs)] bg-[var(--color-bg-labs)] px-3 py-2 text-xs leading-5 text-[var(--color-text-secondary)]">
          {t("labs.memory.activity.callsHelp", { limit: callsLimit })}
        </p>
      }
      leadingTab={{
        value: "entries",
        label: t("labs.memory.activity.tabEntries"),
        content: server ? (
          <MemoryEntries
            entries={server.entries}
            snapshot={server.entrySnapshot}
            window={window}
          />
        ) : null,
      }}
    />
  )
}
