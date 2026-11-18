import { Fragment, lazy, Suspense, useState, type ReactNode } from "react"
import { useTranslation } from "react-i18next"
import { Button } from "@/components/ui/button"
import { PrivacyMarkedText } from "@/labs/privacy/PrivacyMarkedText"
import { hasPrivacyMarker } from "@/labs/privacy/privacyMarkers"
import { cn } from "@/lib/utils"
import {
  normalizeTraceCodeLanguage,
  TRACE_CODE_PREVIEW_PAGE_SIZE,
  type TraceCodeFence,
} from "./traceCodeFences"
import { TraceTextContent } from "./TraceTextContent"

const TraceCodeHighlight = lazy(() => import("./TraceCodeHighlight"))
const HIGHLIGHT_LIMIT = 16 * 1024

export function TraceCodeSnippet({
  value,
  language: recordedLanguage = "",
  previewLines = 4,
  copyLabel,
  showCopy = true,
  wrap = true,
}: {
  value: string
  language?: string
  previewLines?: number
  copyLabel?: string
  showCopy?: boolean
  wrap?: boolean
}) {
  const { t } = useTranslation()
  const language = normalizeTraceCodeLanguage(recordedLanguage)
  const source = <PrivacyMarkedText text={value} />
  return (
    <div
      className="min-w-0 rounded-md border bg-muted/30 px-2 py-1"
      data-trace-code
      data-language={language || undefined}
    >
      <TraceTextContent
        content={value}
        previewLines={previewLines}
        copyLabel={
          showCopy ? (copyLabel ?? t("spanContent.copyContent")) : undefined
        }
        textClassName={cn(
          "min-h-7 [&_.tok-atom]:text-amber-700 dark:[&_.tok-atom]:text-amber-300 [&_.tok-bool]:text-amber-700 dark:[&_.tok-bool]:text-amber-300 [&_.tok-comment]:text-muted-foreground [&_.tok-keyword]:text-violet-700 dark:[&_.tok-keyword]:text-violet-300 [&_.tok-meta]:text-sky-700 dark:[&_.tok-meta]:text-sky-300 [&_.tok-number]:text-amber-700 dark:[&_.tok-number]:text-amber-300 [&_.tok-propertyName]:text-sky-700 dark:[&_.tok-propertyName]:text-sky-300 [&_.tok-string]:text-emerald-700 dark:[&_.tok-string]:text-emerald-300 [&_.tok-string2]:text-emerald-700 dark:[&_.tok-string2]:text-emerald-300 [&_.tok-tagName]:text-violet-700 dark:[&_.tok-tagName]:text-violet-300 [&_.tok-typeName]:text-sky-700 dark:[&_.tok-typeName]:text-sky-300",
          !wrap && "overflow-x-auto wrap-normal whitespace-pre"
        )}
      >
        <code>
          {language &&
          value.length <= HIGHLIGHT_LIMIT &&
          !hasPrivacyMarker(value) ? (
            <Suspense fallback={source}>
              <TraceCodeHighlight value={value} language={language} />
            </Suspense>
          ) : (
            source
          )}
        </code>
      </TraceTextContent>
    </div>
  )
}

export function TraceFencedText({
  value,
  fences,
  renderText,
  previewLines = 4,
}: {
  value: string
  fences: readonly TraceCodeFence[]
  renderText: (text: string) => ReactNode
  previewLines?: number
}) {
  const { t } = useTranslation()
  const [pageSize, setPageSize] = useState(TRACE_CODE_PREVIEW_PAGE_SIZE)
  if (!fences.length) return renderText(value)
  const visible = fences.slice(0, pageSize)
  const hasMore = visible.length < fences.length
  const trailing = hasMore ? "" : value.slice(visible[visible.length - 1].end)
  return (
    <div className="min-w-0 space-y-2">
      {visible.map(({ start, bodyStart, bodyEnd, language }, index) => {
        const text = value.slice(visible[index - 1]?.end ?? 0, start)
        return (
          <Fragment key={start}>
            {text.trim() && renderText(text)}
            <TraceCodeSnippet
              value={value.slice(bodyStart, bodyEnd)}
              language={language}
              previewLines={previewLines}
            />
          </Fragment>
        )
      })}
      {trailing.trim() && renderText(trailing)}
      {hasMore && (
        <Button
          type="button"
          variant="ghost"
          size="sm"
          className="h-7 px-2 text-xs"
          onClick={() => setPageSize(pageSize + TRACE_CODE_PREVIEW_PAGE_SIZE)}
        >
          {t("traceRecords.more", {
            count: Math.min(
              TRACE_CODE_PREVIEW_PAGE_SIZE,
              fences.length - visible.length
            ),
          })}
        </Button>
      )}
    </div>
  )
}
