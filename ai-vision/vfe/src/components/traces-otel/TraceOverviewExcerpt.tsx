import { useTranslation } from "react-i18next"
import { PrivacyMarkedText } from "@/labs/privacy/PrivacyMarkedText"
import { privacyPreviewBoundary } from "@/labs/privacy/privacyMarkers"
import { TraceFencedText } from "@/components/traces/shared/TraceCodeSnippet"
import { traceCodeFences } from "@/components/traces/shared/traceCodeFences"

/** A few literal fields, with paths and container counts; never an inferred summary. */
export function TraceOverviewExcerpt({ value }: { value: unknown }) {
  const { t } = useTranslation()
  const scalar = (item: unknown): string => {
    if (Array.isArray(item))
      return `${t("spanContent.jsonArray")} · ${item.length}`
    if (item && typeof item === "object")
      return `${t("spanContent.jsonObject")} · ${Object.keys(item).length}`
    return typeof item === "string"
      ? item || t("spanContent.emptyValue")
      : item === undefined
        ? t("traceProcess.unreportedContent")
        : String(item)
  }
  const excerpt = (text: string) => {
    const end = privacyPreviewBoundary(text, 220)
    return (
      <PrivacyMarkedText
        text={text.slice(0, end) + (end < text.length ? "…" : "")}
      />
    )
  }
  if (typeof value === "string") {
    const fences = traceCodeFences(value)
    if (fences.length) {
      return (
        <TraceFencedText
          value={value}
          fences={fences}
          previewLines={3}
          renderText={(text) => (
            <span className="line-clamp-3 leading-6 [overflow-wrap:anywhere] whitespace-pre-wrap">
              {excerpt(text)}
            </span>
          )}
        />
      )
    }
  }
  if (!value || typeof value !== "object" || Array.isArray(value))
    return (
      <span className="line-clamp-3 leading-6 [overflow-wrap:anywhere] whitespace-pre-wrap">
        {excerpt(scalar(value))}
      </span>
    )
  const fields = Object.entries(value).flatMap(([key, item]) =>
    item &&
    typeof item === "object" &&
    !Array.isArray(item) &&
    Object.keys(item).length
      ? Object.entries(item).map(([child, leaf]) => ({
          path: `${key}.${child}`,
          value: leaf,
        }))
      : [{ path: key, value: item }]
  )
  if (!fields.length) return <span>{scalar(value)}</span>
  return (
    <span className="block min-w-0 text-sm leading-6">
      {fields.slice(0, 3).map((field) => (
        <span
          key={field.path}
          className="line-clamp-1 [overflow-wrap:anywhere]"
        >
          <span className="mr-2 font-mono text-xs text-muted-foreground">
            {field.path}
          </span>
          {excerpt(scalar(field.value))}
        </span>
      ))}
    </span>
  )
}
