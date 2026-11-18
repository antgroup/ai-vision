import type { ReactNode } from "react"
import { useTranslation } from "react-i18next"
import { TraceBadge } from "@/components/traces-otel/shared/TraceBadge"
import { SpanTypedContent } from "@/components/traces-otel/SpanTypedContent"
import { TraceTextContent } from "@/components/traces/shared/TraceTextContent"
import { LabsLoadMore } from "@/components/labs/shared/LabsLoadMore"
import { memoryResultFromOutput, type MemoryResultEntry } from "./memoryResult"
import type { MemoryToolCategory } from "./memoryDisplay"

export function MemoryResultEntries({
  entries,
}: {
  entries: MemoryResultEntry[]
}) {
  const { t } = useTranslation()
  return (
    <LabsLoadMore items={entries}>
      {(visible) => (
        <ul className="divide-y divide-border" data-memory-result-entries>
          {visible.map((item, index) => (
            <li
              key={index}
              className="min-w-0 space-y-2 py-3 first:pt-0 last:pb-0"
            >
              {(item.state || item.score !== undefined) && (
                <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-muted-foreground">
                  {item.state && (
                    <span title={item.state}>
                      {t("labs.memory.result.state")}:{" "}
                      {t(
                        `labs.memory.result.states.${item.state.toLowerCase()}`,
                        { defaultValue: item.state }
                      )}
                    </span>
                  )}
                  {item.score !== undefined && (
                    <span title={t("labs.memory.result.scoreHelp")}>
                      {t("labs.memory.result.score")}:{" "}
                      <span className="font-stats text-foreground">
                        {item.score}
                      </span>
                    </span>
                  )}
                </div>
              )}
              <TraceTextContent
                content={item.content}
                previewLines={4}
                textClassName="font-sans text-sm leading-6 text-[var(--color-text-primary)]"
                copyLabel={`${t("common.copy")}: ${t("labs.memory.activity.content")}`}
              />
            </li>
          ))}
        </ul>
      )}
    </LabsLoadMore>
  )
}

/** The same returned-memory presentation in a Trace and in its comparison. */
export function MemoryResultContent({
  value,
  outcome,
  category,
  fallback,
}: {
  value: unknown
  outcome: "ok" | "error" | "unknown"
  category?: MemoryToolCategory | null
  fallback?: ReactNode
}) {
  const { t } = useTranslation()
  const result =
    outcome === "error" ? null : memoryResultFromOutput(value, category)
  const title = t(`labs.memory.result.${result?.type ?? "output"}`)
  return (
    <section
      className="min-w-0 overflow-hidden rounded-lg border border-[var(--color-border-labs)] bg-[var(--color-bg-page)]"
      data-content-result-outcome={outcome}
      data-memory-result
    >
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-[var(--color-border-labs)] bg-[var(--color-bg-labs)] px-3 py-2">
        <h4 className="text-xs font-medium">{title}</h4>
        <TraceBadge
          tone={
            outcome === "error"
              ? "error"
              : outcome === "ok"
                ? "success"
                : "default"
          }
        >
          {t(`labs.memory.result.outcomes.${outcome}`)}
        </TraceBadge>
      </div>
      <div className="space-y-3 p-3">
        {result?.count !== null && result?.count !== undefined && (
          <p className="text-sm font-medium">
            {t(
              result.count === 1
                ? "labs.memory.result.countOne"
                : "labs.memory.result.count",
              { count: result.count }
            )}
            {result.partial && (
              <span className="ml-2 text-xs font-normal text-muted-foreground">
                {t("labs.memory.result.partial")}
              </span>
            )}
          </p>
        )}
        {result && result.entries.length > 0 && (
          <MemoryResultEntries entries={result.entries} />
        )}
        {result &&
          result.count !== null &&
          result.count > result.entries.length && (
            <p className="text-xs text-muted-foreground">
              {t("labs.memory.result.incomplete")}
            </p>
          )}
        {result?.type === "list" && (
          <p className="text-xs text-muted-foreground">
            {t("labs.memory.result.listHelp")}
          </p>
        )}
        {result && (result.entries.length > 0 || result.count === 0) ? (
          <details className="border-t border-border pt-2 text-xs">
            <summary className="w-fit cursor-pointer rounded text-[var(--color-labs)] focus-visible:outline-2 focus-visible:outline-offset-2">
              {t("labs.memory.result.original")}
            </summary>
            <div className="mt-2">
              <SpanTypedContent
                value={value}
                copyLabel={t("labs.memory.result.copyOriginal")}
              />
            </div>
          </details>
        ) : (
          <>
            {outcome !== "error" && (
              <p className="text-xs text-muted-foreground">
                {t(
                  result
                    ? "labs.memory.result.acknowledgement"
                    : "labs.memory.result.unparsed"
                )}
              </p>
            )}
            {fallback ?? (
              <SpanTypedContent
                value={value}
                copyLabel={t("labs.memory.result.copyOriginal")}
              />
            )}
          </>
        )}
      </div>
    </section>
  )
}
