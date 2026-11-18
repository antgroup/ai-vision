import { useState, type ReactNode } from "react"
import {
  ChevronDown,
  ChevronRight,
  FileJson,
  Loader2,
  Lock,
  RefreshCw,
  Wrench,
} from "lucide-react"
import { useTranslation } from "react-i18next"

import { Button } from "@/components/ui/button"
import { CopyableCode } from "@/components/labs/shared/CopyableCode"
import { formatRelativeTime } from "@/components/labs/shared/labsFormat"

import { MASK } from "./mcpConfigImport"
import type { McpServerConfig, McpToolInfo } from "./types"

function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="grid grid-cols-1 gap-1 py-2 not-last:border-b not-last:border-[var(--color-border)] sm:grid-cols-[96px_minmax(0,1fr)] sm:gap-4">
      <dt className="text-sm leading-[22px] text-[var(--color-text-description)]">
        {label}
      </dt>
      <dd className="flex min-w-0 flex-col gap-1 text-sm leading-[22px] text-[var(--color-text-primary)]">
        {children}
      </dd>
    </div>
  )
}

function KeyChips({ keys }: { keys: string[] }) {
  const { t } = useTranslation()
  return (
    <div className="flex flex-wrap items-center gap-2">
      {keys.map((key) => (
        <span
          key={key}
          className="inline-flex h-6 items-center gap-1 rounded-[6px] bg-[var(--color-bg-hover)] px-2 font-mono text-xs leading-5"
        >
          {key}
          <span className="text-[var(--color-text-description)]">= {MASK}</span>
        </span>
      ))}
      <span className="inline-flex items-center gap-1 text-xs leading-5 text-[var(--color-text-description)]">
        <Lock className="size-3" />
        {t("labs.mcp.config.valuesHidden")}
      </span>
    </div>
  )
}

interface McpConfigCardProps {
  feature?: "mcp" | "memory"
  config: McpServerConfig
  /** Stored tools/list snapshot and its sync state, managed by the Drawer. */
  tools: McpToolInfo[]
  syncedAt: string | null
  toolsStatus: "loading" | "error" | "ready"
  onRetryTools: () => void
  isSyncing: boolean
  syncError: string | null
  /** Opens the auth dialog when the config declares headers, else syncs. */
  onRequestSync?: () => void
  onReimport?: () => void
  onDelete?: () => void
  deleting?: boolean
  deleteError?: string | null
  /** Read-only mode for contexts without config management (trace cards):
   * hides the sync/reimport/delete actions. */
  readOnly?: boolean
}

export function McpConfigCard({
  feature = "mcp",
  config,
  tools,
  syncedAt,
  toolsStatus,
  onRetryTools,
  isSyncing,
  syncError,
  onRequestSync,
  onReimport,
  onDelete,
  deleting = false,
  deleteError,
  readOnly = false,
}: McpConfigCardProps) {
  const { t } = useTranslation()
  const [showRaw, setShowRaw] = useState(false)
  const [toolLimit, setToolLimit] = useState(10)
  const rawJson = JSON.stringify({ [config.serverId]: config.raw }, null, 2)

  return (
    <div className="flex flex-col gap-4">
      <section className="rounded-[12px] border border-[var(--color-border)] px-4">
        <dl>
          <Row label={t("labs.mcp.config.transport")}>
            <span className="font-mono text-sm">{config.transport}</span>
            <span className="text-xs leading-5 text-[var(--color-text-description)]">
              {t(`labs.mcp.transportHint.${config.transport}`)}
            </span>
          </Row>

          {config.transport === "stdio" ? (
            <>
              <Row label={t("labs.mcp.config.command")}>
                <CopyableCode value={config.command ?? ""} />
              </Row>
              {config.args?.length ? (
                <Row label={t("labs.mcp.config.args")}>
                  <ol className="flex flex-col gap-1">
                    {config.args.map((arg, index) => (
                      <li
                        key={`${arg}-${index}`}
                        className="flex items-center gap-2"
                      >
                        <span className="w-4 shrink-0 font-stats text-xs text-[var(--color-text-description)]">
                          {index + 1}
                        </span>
                        <code className="min-w-0 truncate font-mono text-xs leading-5">
                          {arg}
                        </code>
                      </li>
                    ))}
                  </ol>
                </Row>
              ) : null}
            </>
          ) : (
            <Row label={t("labs.mcp.config.url")}>
              <CopyableCode value={config.url ?? ""} />
            </Row>
          )}

          {config.envKeys.length ? (
            <Row label={t("labs.mcp.config.env")}>
              <KeyChips keys={config.envKeys} />
            </Row>
          ) : null}
          {config.headerKeys.length ? (
            <Row label={t("labs.mcp.config.headers")}>
              <KeyChips keys={config.headerKeys} />
            </Row>
          ) : null}
          <Row label={t("labs.mcp.config.importedAt")}>
            <span>{formatRelativeTime(config.importedAt, t)}</span>
          </Row>
        </dl>
      </section>

      <section className="flex flex-col gap-2 rounded-[12px] border border-[var(--color-border)] px-4 py-3">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div className="flex min-w-0 flex-wrap items-center gap-2">
            <h3 className="flex items-center gap-2 text-sm leading-[22px] font-medium text-[var(--color-text-primary)]">
              <span className="flex size-6 shrink-0 items-center justify-center rounded-md bg-[var(--color-bg-labs-tag)] text-[var(--color-labs)]">
                <Wrench className="size-3.5" aria-hidden="true" />
              </span>
              {toolsStatus === "ready"
                ? t(`labs.${feature}.config.toolsTitle`, {
                    count: tools.length,
                  })
                : t(`labs.${feature}.config.toolsHeading`)}
            </h3>
            {syncedAt ? (
              <span className="text-xs leading-5 text-[var(--color-text-description)]">
                {t("labs.mcp.config.syncedAt", {
                  time: formatRelativeTime(syncedAt, t),
                })}
              </span>
            ) : null}
          </div>
          {readOnly ? null : (
            <Button
              variant="outline"
              size="sm"
              className="rounded-[8px] border-[var(--color-border-labs)] text-[var(--color-labs)] hover:bg-[var(--color-bg-labs-tag)] hover:text-[var(--color-labs)] dark:hover:bg-[var(--color-bg-labs-tag)]"
              disabled={isSyncing}
              onClick={onRequestSync}
            >
              {isSyncing ? (
                <Loader2 className="size-4 animate-spin" />
              ) : (
                <RefreshCw className="size-4" />
              )}
              {t("labs.mcp.config.syncTools")}
            </Button>
          )}
        </div>

        {syncError ? (
          <p
            role="alert"
            className="text-xs leading-5 break-all text-[var(--color-error)]"
          >
            {t("labs.mcp.config.syncFailed")}: {syncError}
          </p>
        ) : null}

        {toolsStatus === "loading" ? (
          <p
            role="status"
            className="flex items-center gap-2 text-xs leading-5 text-[var(--color-text-description)]"
          >
            <Loader2 className="size-3 animate-spin" />
            {t("labs.common.loading")}
          </p>
        ) : toolsStatus === "error" ? (
          <div className="flex items-center justify-between gap-3">
            <p
              role="alert"
              className="text-xs leading-5 text-[var(--color-error)]"
            >
              {t(`labs.${feature}.config.toolsLoadFailed`)}
            </p>
            <Button variant="outline" size="sm" onClick={onRetryTools}>
              {t("common.retry")}
            </Button>
          </div>
        ) : tools.length ? (
          <ul className="flex flex-col divide-y divide-[var(--color-border)] rounded-[8px] border border-[var(--color-border)]">
            {tools.slice(0, toolLimit).map((tool) => (
              <li key={tool.name} className="min-w-0 px-3 py-2">
                <details data-mcp-tool-definition>
                  <summary className="cursor-pointer rounded font-mono text-xs leading-5 break-all text-[var(--color-labs)] focus-visible:outline-2 focus-visible:outline-[var(--color-labs)]">
                    {tool.title ? `${tool.name} · ${tool.title}` : tool.name}
                  </summary>
                  {tool.description ? (
                    <p className="mt-2 text-xs leading-5 break-words whitespace-pre-wrap">
                      {tool.description}
                    </p>
                  ) : null}
                  <p className="mt-2 text-xs text-muted-foreground">
                    {t("labs.mcp.topic.definition")}
                  </p>
                  <CopyableCode
                    value={JSON.stringify(tool, null, 2)}
                    multiline
                    className="mt-1 max-h-96 overflow-auto"
                  />
                </details>
                {tool.description ? (
                  <p className="line-clamp-2 text-xs leading-5 text-muted-foreground">
                    {tool.description}
                  </p>
                ) : null}
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-xs leading-5 text-[var(--color-text-description)]">
            {t("labs.mcp.config.toolsEmpty")}
            {readOnly ? null : t(`labs.${feature}.config.toolsEmptyDesc`)}
          </p>
        )}
        {toolsStatus === "ready" && tools.length > 10 ? (
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-xs text-muted-foreground">
              {t("labs.mcp.topic.toolsShown", {
                shown: Math.min(toolLimit, tools.length),
                total: tools.length,
              })}
            </span>
            {toolLimit < tools.length ? (
              <Button
                variant="outline"
                size="sm"
                onClick={() => setToolLimit((value) => value + 10)}
              >
                {t("labs.mcp.topic.more")}
              </Button>
            ) : null}
            {toolLimit > 10 ? (
              <Button
                variant="ghost"
                size="sm"
                onClick={() => setToolLimit(10)}
              >
                {t("labs.mcp.topic.less")}
              </Button>
            ) : null}
          </div>
        ) : null}
      </section>

      <section className="flex flex-col gap-2">
        <button
          type="button"
          onClick={() => setShowRaw((value) => !value)}
          aria-expanded={showRaw}
          className="inline-flex w-fit items-center gap-1 rounded-[6px] text-sm leading-[22px] text-[var(--color-text-secondary)] hover:text-[var(--color-text-primary)] focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--color-text-primary)]"
        >
          {showRaw ? (
            <ChevronDown className="size-4" />
          ) : (
            <ChevronRight className="size-4" />
          )}
          {t("labs.mcp.config.raw")}
        </button>
        {showRaw ? (
          <CopyableCode value={rawJson} multiline className="py-3" />
        ) : null}
      </section>

      {readOnly ? null : (
        <p className="text-xs text-muted-foreground">
          {t("labs.mcp.topic.removeScope")}
        </p>
      )}
      {deleteError ? (
        <p role="alert" className="text-xs break-all text-destructive">
          {t("labs.mcp.topic.removeFailed")}: {deleteError}
        </p>
      ) : null}
      {readOnly ? null : (
        <div className="flex flex-wrap items-center gap-2">
          <Button
            variant="outline"
            size="sm"
            className="rounded-[8px] border-[var(--color-border-labs)] text-[var(--color-labs)] hover:bg-[var(--color-bg-labs-tag)] hover:text-[var(--color-labs)] dark:hover:bg-[var(--color-bg-labs-tag)]"
            onClick={onReimport}
            disabled={deleting}
          >
            <FileJson className="size-4" />
            {t("labs.mcp.config.reimport")}
          </Button>
          <Button
            variant="ghost"
            size="sm"
            className="rounded-[8px] text-[var(--color-text-secondary)]"
            onClick={onDelete}
            disabled={deleting}
          >
            {t(deleting ? "labs.mcp.topic.removing" : "labs.mcp.config.remove")}
          </Button>
        </div>
      )}
    </div>
  )
}

export function McpConfigEmpty({
  onImport,
  feature = "mcp",
}: {
  onImport?: () => void
  feature?: "mcp" | "memory"
}) {
  const { t } = useTranslation()
  return (
    <div className="flex flex-col items-start gap-3 rounded-[12px] border border-dashed border-[var(--color-border-labs)] bg-[var(--color-bg-labs)] p-4">
      <div className="flex flex-col gap-1">
        <p className="text-sm leading-[22px] font-medium text-[var(--color-text-primary)]">
          {t(`labs.${feature}.config.emptyTitle`)}
        </p>
        <p className="max-w-[72ch] text-xs leading-5 text-[var(--color-text-description)]">
          {t(`labs.${feature}.config.emptyDesc`)}
        </p>
      </div>
      {onImport ? (
        <Button
          variant="outline"
          size="sm"
          className="rounded-[8px] border-[var(--color-border-labs)] text-[var(--color-labs)] hover:bg-[var(--color-bg-labs-tag)] hover:text-[var(--color-labs)] dark:hover:bg-[var(--color-bg-labs-tag)]"
          onClick={onImport}
        >
          <FileJson className="size-4" />
          {t("labs.mcp.import.open")}
        </Button>
      ) : (
        <p className="text-xs leading-5 text-[var(--color-text-description)]">
          {t(`labs.${feature}.adminOnly`)}
        </p>
      )}
    </div>
  )
}
