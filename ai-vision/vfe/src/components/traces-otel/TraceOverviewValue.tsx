import { useId, useLayoutEffect, useRef, useState, type ReactNode } from "react"
import { useTranslation } from "react-i18next"
import { ArrowUpRight } from "lucide-react"
import { Button } from "@/components/ui/button"
import { TraceCopyButton } from "@/components/traces/shared/TraceTextContent"
import { TraceDocumentPreviews } from "@/components/traces/shared/TraceDocumentPreviews"
import { TraceOverviewExcerpt } from "./TraceOverviewExcerpt"
import { contentPreview } from "./spanContent"
import { SpanTypedContent } from "./SpanTypedContent"
import { TraceMediaContent } from "@/components/traces/shared/TraceMedia"
import { mediaOutputValue } from "@/components/traces/shared/traceMediaOutput"

/** A bounded overview excerpt; the recorded payload stays in its owning view. */
export function TraceOverviewValue({
  value,
  title,
  onViewSource,
}: {
  value: unknown
  title: string
  onViewSource?: () => void
}) {
  const { t } = useTranslation()
  const [expanded, setExpanded] = useState(false)
  const contentId = useId()
  const disclosure = useRef<HTMLButtonElement>(null)
  const restoreFocus = useRef(false)
  useLayoutEffect(() => {
    if (restoreFocus.current) disclosure.current?.focus({ preventScroll: true })
    restoreFocus.current = false
  }, [expanded])
  const text = contentPreview(value, Infinity).text
  const media = mediaOutputValue(value)
  const header = (controls: ReactNode = null) => (
    <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
      <h3 className="text-sm font-medium">{title}</h3>
      <div className="flex flex-wrap items-center gap-1">
        {controls}
        {value !== undefined && (
          <TraceCopyButton
            value={text}
            label={t("spanContent.copyContent")}
            className="size-6"
          />
        )}
        {value !== undefined &&
          (onViewSource ? (
            <Button
              variant="ghost"
              size="sm"
              className="h-7 gap-1 px-2 text-xs"
              onClick={onViewSource}
              data-overview-output-source
            >
              {t("traceProcess.viewOutput")}
              <ArrowUpRight className="size-3" />
            </Button>
          ) : (
            <Button
              ref={disclosure}
              variant="ghost"
              size="sm"
              className="h-7 px-2 text-xs"
              aria-controls={contentId}
              aria-expanded={expanded}
              onClick={() => {
                restoreFocus.current = true
                setExpanded(!expanded)
              }}
            >
              {t(
                expanded
                  ? "spanContent.collapseText"
                  : "traceProcess.fullOutput"
              )}
            </Button>
          ))}
      </div>
    </div>
  )
  return (
    <section
      className="min-w-0 rounded-lg border p-3"
      data-trace-overview-output
    >
      {expanded ? (
        <SpanTypedContent
          value={value}
          treeLayout="outline"
          showCopy={false}
          renderFrame={(controls, content) => (
            <>
              {header(controls)}
              <div id={contentId}>{content}</div>
            </>
          )}
        />
      ) : (
        <>
          {header()}
          <TraceDocumentPreviews value={value} />
          {media === null ? (
            <TraceOverviewExcerpt value={value} />
          ) : (
            <TraceMediaContent value={media} />
          )}
        </>
      )}
    </section>
  )
}
