import { useState } from "react"
import { useTranslation } from "react-i18next"
import { Layers } from "lucide-react"
import { PrivacyMarkedText } from "@/labs/privacy/PrivacyMarkedText"
import { TraceRecordControls } from "@/components/traces/shared/TraceRecordControls"
import { SpanTypedContent } from "./SpanTypedContent"
import { TraceBadge } from "./shared/TraceBadge"
import type { ParsedPowerContextContent } from "./powerContextContent"

/** Readable historical evidence; the complete received value stays reversible. */
export function PowerContextMemoryContent({
  content,
  embedded = false,
  compact = false,
}: {
  content: ParsedPowerContextContent
  embedded?: boolean
  compact?: boolean
}) {
  const { t } = useTranslation()
  const [visible, setVisible] = useState(5)
  const [originalOpen, setOriginalOpen] = useState(false)
  return (
    <section
      className={`min-w-0 space-y-3 ${embedded ? "" : "rounded-lg border border-border bg-background p-3"}`}
      data-powercontext-content
    >
      {!embedded && (
        <header className="flex flex-wrap items-center justify-between gap-2">
          <h4 className="inline-flex items-center gap-2 text-sm font-medium">
            <Layers
              className="size-4 text-muted-foreground"
              aria-hidden="true"
            />
            {t("powerContextContent.title")}
          </h4>
          <span className="text-xs text-muted-foreground">
            {t("powerContextContent.count", { count: content.items.length })}
          </span>
        </header>
      )}
      <p className="text-xs leading-5 text-muted-foreground">
        {t("powerContextContent.historyNote")}
      </p>
      {content.items.length === 0 && (
        <p className="text-sm text-muted-foreground">
          {t("powerContextContent.empty")}
        </p>
      )}
      <div className="space-y-3">
        {content.items.slice(0, visible).map((item, index) => {
          const kind =
            compact && item.kind === "atomic-memory" ? null : item.kind
          return (
            <article
              key={index}
              className={`min-w-0 space-y-2 ${index > 0 ? "border-t border-border pt-3" : ""}`}
              data-powercontext-item
            >
              {(kind || item.truncated) && (
                <div className="flex min-w-0 flex-wrap items-center gap-2">
                  {kind && (
                    <TraceBadge>
                      {kind === "atomic-memory"
                        ? t("powerContextContent.atomicMemory")
                        : kind}
                    </TraceBadge>
                  )}
                  {item.truncated === true && (
                    <TraceBadge>
                      {t("powerContextContent.truncated")}
                    </TraceBadge>
                  )}
                </div>
              )}
              <SpanTypedContent value={item.content} compact={compact} />
              {item.citation && !compact && (
                <dl className="flex min-w-0 flex-wrap items-baseline gap-x-2 gap-y-1 text-xs text-muted-foreground">
                  <dt>{t("powerContextContent.source")}</dt>
                  <dd className="min-w-0 font-mono [overflow-wrap:anywhere]">
                    <PrivacyMarkedText text={item.citation.artifactId} />
                  </dd>
                  <dt className="sr-only">{t("powerContextContent.family")}</dt>
                  <dd>{item.citation.family}</dd>
                  <dt className="sr-only">
                    {t("powerContextContent.revisionLabel")}
                  </dt>
                  <dd>
                    {t("powerContextContent.revision", {
                      revision: item.citation.revision,
                    })}
                  </dd>
                </dl>
              )}
            </article>
          )
        })}
      </div>
      <TraceRecordControls
        shown={Math.min(visible, content.items.length)}
        total={content.items.length}
        onMore={() => setVisible((count) => count + 5)}
        onAll={() => setVisible(content.items.length)}
        onCollapse={() => setVisible(5)}
      />
      {!compact && (
        <details
          className="border-t border-border pt-2 text-xs"
          data-powercontext-original
          onToggle={(event) => setOriginalOpen(event.currentTarget.open)}
        >
          <summary className="cursor-pointer rounded-sm text-muted-foreground focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring">
            {t("powerContextContent.original")}
          </summary>
          {originalOpen && (
            <div className="mt-2 min-w-0 rounded-md bg-muted/40 p-2">
              <SpanTypedContent value={content.raw} />
            </div>
          )}
        </details>
      )}
    </section>
  )
}
