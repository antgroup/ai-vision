import { useTranslation } from "react-i18next"
import { ChevronDown, Link2 } from "lucide-react"
import type { NativeIOSource } from "./types"

function Source({
  source,
  field,
  shared,
}: {
  source?: NativeIOSource | null
  field: string
  shared: boolean
}) {
  const { t } = useTranslation()
  if (!source) {
    return (
      <span
        data-source-unknown
        className="inline-flex flex-wrap gap-1.5 text-xs text-muted-foreground"
      >
        {!shared && <span>{field}</span>}
        <span>{t("traceLabels.sourceUnknown")}</span>
      </span>
    )
  }
  const node = source.spanId
  const shortNode =
    node.length > 14 ? `${node.slice(0, 8)}…${node.slice(-4)}` : node
  return (
    <details className="group min-w-0 open:basis-full" data-session-source>
      <summary
        className="flex w-fit max-w-full cursor-pointer list-none items-center gap-1.5 rounded-md border border-border/60 bg-background/60 px-2 py-0.5 text-xs text-muted-foreground transition-colors hover:border-border hover:bg-muted/70 hover:text-foreground focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring [&::-webkit-details-marker]:hidden"
        aria-label={t("sessionContent.sourceDetails", { field })}
        title={t("traceLabels.source", {
          traceId: source.traceId,
          spanId: source.spanId,
        })}
      >
        {!shared && <span className="shrink-0">{field}</span>}
        <span className="truncate font-mono text-[11px]">
          {shortNode || t("traceLabels.unknown")}
        </span>
        <ChevronDown
          aria-hidden="true"
          className="size-3 shrink-0 transition-transform group-open:rotate-180 motion-reduce:transition-none"
        />
      </summary>
      <dl className="mt-2 grid min-w-0 grid-cols-1 gap-x-4 gap-y-1.5 rounded-md border border-border/60 bg-background/60 px-3 py-2.5 text-xs sm:grid-cols-[5.5rem_minmax(0,1fr)]">
        <dt className="text-muted-foreground">
          {t("sessionContent.sourceTrace")}
        </dt>
        <dd className="min-w-0 font-mono [overflow-wrap:anywhere] text-foreground select-text">
          {source.traceId}
        </dd>
        <dt className="text-muted-foreground">
          {t("sessionContent.sourceSpan")}
        </dt>
        <dd className="min-w-0 font-mono [overflow-wrap:anywhere] text-foreground select-text">
          {source.spanId}
        </dd>
        <dt className="text-muted-foreground">
          {t("sessionContent.sourceRule")}
        </dt>
        <dd className="min-w-0 font-mono [overflow-wrap:anywhere] text-muted-foreground select-text">
          {source.rule}
        </dd>
      </dl>
    </details>
  )
}

/** Coalesce only identical evidence; a missing side never inherits a source. */
export function SessionIOSources({
  inputSource,
  outputSource,
}: {
  inputSource?: NativeIOSource | null
  outputSource?: NativeIOSource | null
}) {
  const { t } = useTranslation()
  const shared =
    (!inputSource && !outputSource) ||
    Boolean(
      inputSource &&
      outputSource &&
      inputSource.traceId === outputSource.traceId &&
      inputSource.spanId === outputSource.spanId &&
      inputSource.rule === outputSource.rule
    )
  return (
    <div
      data-session-io-sources
      className="flex min-w-0 flex-wrap items-start gap-x-2.5 gap-y-2 border-t border-border/60 bg-muted/15 px-4 py-2"
    >
      <span className="inline-flex shrink-0 items-center gap-1.5 py-1 text-[11px] text-muted-foreground">
        <Link2 aria-hidden="true" className="size-3" />
        {t("sessionContent.sourceNode")}
      </span>
      <div className="flex min-w-0 flex-1 flex-wrap items-start gap-x-2 gap-y-2">
        {shared ? (
          <Source
            source={inputSource}
            field={t("sessionContent.sharedSource")}
            shared
          />
        ) : (
          <>
            <Source
              source={inputSource}
              field={t("nativeSpanUsage.input")}
              shared={false}
            />
            <Source
              source={outputSource}
              field={t("nativeSpanUsage.output")}
              shared={false}
            />
          </>
        )}
      </div>
    </div>
  )
}
