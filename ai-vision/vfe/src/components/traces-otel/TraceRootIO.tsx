import { useId, useLayoutEffect, useRef, useState, type ReactNode } from "react"
import { useTranslation } from "react-i18next"
import { ChevronDown } from "lucide-react"
import { TraceCopyButton } from "@/components/traces/shared/TraceTextContent"
import { SpanTypedContent } from "./SpanTypedContent"
import { contentPreview } from "./spanContent"

/** Root call payloads remain exact, independently disclosed and in page flow. */
export function TraceRootIO({
  value,
  variant,
  initiallyOpen = false,
}: {
  value: unknown
  variant: "input" | "output"
  initiallyOpen?: boolean
}) {
  const { t } = useTranslation()
  const [open, setOpen] = useState(initiallyOpen)
  const id = useId()
  const button = useRef<HTMLButtonElement>(null)
  const restoreFocus = useRef(initiallyOpen)
  useLayoutEffect(() => {
    if (restoreFocus.current) button.current?.focus({ preventScroll: true })
    restoreFocus.current = false
  }, [open])
  const header = (controls: ReactNode = null) => (
    <div className="flex flex-wrap items-center justify-between gap-2 px-3 py-2">
      <button
        ref={button}
        type="button"
        className="flex items-center gap-2 rounded text-sm font-medium focus-visible:outline-2 focus-visible:outline-ring"
        aria-expanded={open}
        aria-controls={id}
        onClick={() => {
          restoreFocus.current = true
          setOpen(!open)
        }}
      >
        <ChevronDown
          className={`size-3.5 ${open ? "" : "-rotate-90"}`}
          aria-hidden
        />
        {t(`nativeSpanUsage.${variant}`)}
      </button>
      <div className="flex flex-wrap items-center gap-1">
        {controls}
        <TraceCopyButton
          value={contentPreview(value, Infinity).text}
          label={t("spanContent.copyContent")}
          className="size-6"
        />
      </div>
    </div>
  )
  return (
    <section className="min-w-0 rounded-lg border" data-root-io={variant}>
      {open ? (
        <SpanTypedContent
          value={value}
          treeLayout="outline"
          paginateTree={variant === "output"}
          showCopy={false}
          previewLines={4}
          renderFrame={(controls, content) => (
            <>
              {header(controls)}
              <div id={id} className="min-w-0 border-t p-3">
                {content}
              </div>
            </>
          )}
        />
      ) : (
        header()
      )}
    </section>
  )
}
