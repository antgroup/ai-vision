import { useTranslation } from "react-i18next"
import { Button } from "@/components/ui/button"
import { mediaOutputValue } from "./traceMediaOutput"
import { TraceMediaPartsContent } from "./TraceMedia"
import { traceMediaCodeParts } from "./traceCodeTextParts"
import {
  collectTraceMedia,
  traceMediaParts,
  type TraceMediaPart,
} from "./traceMediaProjection"

export function TraceMediaSummary({
  sources,
  onViewSource,
  primaryParts,
}: {
  sources: Array<{ id: string; name: string; parts: TraceMediaPart[] }>
  onViewSource?: (id: string) => void
  primaryParts?: TraceMediaPart[]
}) {
  const { t } = useTranslation()
  const entries = collectTraceMedia(sources)
  if (!entries.length) return null
  const remaining = [...entries]
  const ordered: Array<
    (typeof entries)[number] | { part: TraceMediaPart; sources: [] }
  > = []
  for (const part of traceMediaCodeParts(primaryParts ?? [])) {
    const index = remaining.findIndex(
      (entry) =>
        entry.part === part ||
        (entry.part.kind === "invalid" &&
          part.kind === "invalid" &&
          entry.part.reason === part.reason) ||
        (entry.part.kind === "media" &&
          part.kind === "media" &&
          (entry.part.source === part.source ||
            (entry.part.sha256 !== undefined &&
              entry.part.sha256 === part.sha256 &&
              entry.part.mime === part.mime)))
    )
    if (index >= 0) ordered.push(...remaining.splice(index, 1))
    else if (part.kind !== "media" && part.kind !== "invalid")
      ordered.push({ part, sources: [] })
  }
  ordered.push(...remaining)
  return (
    <div
      className="min-w-0 space-y-2"
      data-trace-media-summary
      data-trace-media-content
    >
      <TraceMediaPartsContent parts={ordered.map(({ part }) => part)} />
      {onViewSource && sources.some((source) => source.id) && (
        <details className="text-xs">
          <summary className="cursor-pointer rounded-sm text-muted-foreground focus-visible:outline-2 focus-visible:outline-ring">
            {t("traceUserInput.viewSource")}
          </summary>
          <div className="mt-1 flex flex-wrap gap-1">
            {sources
              .filter((source) => source.id)
              .map((source) => (
                <Button
                  key={source.id}
                  type="button"
                  variant="ghost"
                  size="sm"
                  className="h-auto min-h-7 max-w-full text-xs break-all whitespace-normal"
                  onClick={() => onViewSource(source.id)}
                >
                  {source.name || t("traceUserInput.viewSource")}
                </Button>
              ))}
          </div>
        </details>
      )}
    </div>
  )
}

type OutputSource = { id: string; name: string; output: unknown }

export function TraceMediaOutputs({
  sources,
  onViewSource,
  exclude,
}: {
  sources: OutputSource[]
  onViewSource?: (id: string) => void
  exclude?: unknown
}) {
  const { t } = useTranslation()
  const displayed = traceMediaParts(mediaOutputValue(exclude)) ?? []
  const matches = sources
    .map((source) => ({
      ...source,
      parts: (traceMediaParts(mediaOutputValue(source.output)) ?? []).filter(
        (part) => {
          if (part.kind === "invalid")
            return !displayed.some(
              (shown) =>
                shown.kind === "invalid" && shown.reason === part.reason
            )
          if (part.kind !== "media") return true
          return !displayed.some(
            (shown) =>
              shown.kind === "media" &&
              shown.mime === part.mime &&
              (shown.source === part.source ||
                (shown.sha256 !== undefined && shown.sha256 === part.sha256))
          )
        }
      ),
    }))
    .filter((source) =>
      source.parts.some(
        (part) => part.kind === "media" || part.kind === "invalid"
      )
    )
  if (!matches.length) return null
  return (
    <section
      className="min-w-0 space-y-2 rounded-md border p-3"
      data-testid="trace-media-outputs"
    >
      <h3 className="text-sm font-medium">{t("traceMedia.outputSources")}</h3>
      <TraceMediaSummary sources={matches} onViewSource={onViewSource} />
    </section>
  )
}
