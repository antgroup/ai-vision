import { writeClipboardText } from "@/lib/clipboard"
import {
  Children,
  useContext,
  useEffect,
  useId,
  useLayoutEffect,
  useRef,
  useState,
  type ComponentPropsWithoutRef,
  type ReactNode,
} from "react"
import type { ExtraProps } from "react-markdown"
import { useTranslation } from "react-i18next"
import { Check, Copy } from "lucide-react"
import { toast } from "sonner"
import { Button } from "@/components/ui/button"
import { cn } from "@/lib/utils"
import { PrivacyMarkedText } from "@/labs/privacy/PrivacyMarkedText"
import {
  TraceEnhancedVisibility,
  TraceHtmlPreviewSources,
} from "./traceEnhancedVisibility"
import { isTraceHtmlPreviewLink } from "./traceHtmlLinkUrl"

/** Copy the original value even when its display is collapsed or formatted. */
export function TraceCopyButton({
  value,
  label,
  className,
}: {
  value: string
  label: string
  className?: string
}) {
  const { t } = useTranslation()
  const [copiedValue, setCopiedValue] = useState<string | null>(null)
  const copied = copiedValue === value
  const copy = async () => {
    try {
      await writeClipboardText(value)
      setCopiedValue(value)
    } catch {
      setCopiedValue(null)
      toast.error(t("traceUserInput.copyFailed"))
    }
  }
  return (
    <Button
      type="button"
      variant="ghost"
      size="sm"
      className={cn("size-7 shrink-0 p-0", className)}
      title={copied ? `${label}: ${t("traceUserInput.copied")}` : label}
      aria-label={label}
      onClick={(event) => {
        event.stopPropagation()
        void copy()
      }}
      onKeyDown={(event) => {
        if (event.key === "Enter" || event.key === " ") event.stopPropagation()
      }}
    >
      {copied ? <Check className="size-3.5" /> : <Copy className="size-3.5" />}
      <span className="sr-only" role="status">
        {copied ? t("traceUserInput.copied") : ""}
      </span>
    </Button>
  )
}

export function TraceLinkAddress({ address }: { address: string }) {
  const { t } = useTranslation()
  return (
    <span
      data-trace-document-link-fallback
      className="inline-flex max-w-full items-center gap-1 align-middle"
    >
      <span className="min-w-0 font-mono text-xs [overflow-wrap:anywhere]">
        {address}
      </span>
      <TraceCopyButton value={address} label={t("share.copyLink")} />
    </span>
  )
}

export function TraceMarkdownLink({
  children,
  href,
  node: _node,
  ...props
}: ComponentPropsWithoutRef<"a"> & ExtraProps) {
  void _node
  const active = useContext(TraceEnhancedVisibility) !== false
  const previews = useContext(TraceHtmlPreviewSources)
  if (
    active &&
    previews !== undefined &&
    href &&
    isTraceHtmlPreviewLink(href)
  ) {
    if (previews?.has(href)) return <span>{children}</span>
    const label = Children.toArray(children)
    return (
      <span>
        {(label.length !== 1 || label[0] !== href) && <>{children} </>}
        <TraceLinkAddress address={href} />
      </span>
    )
  }
  return (
    <a {...props} href={href}>
      {children}
    </a>
  )
}

/** A visual-line preview; the original text remains available on expand and copy. */
export function TraceTextContent({
  content,
  previewLines = 4,
  className,
  textClassName,
  children,
  copyLabel,
  copyOnlyWhenOverflowing = false,
  copyValue = content,
  expandLabel,
  collapseLabel,
  id,
  "data-testid": testId,
}: {
  content: string
  previewLines?: number
  className?: string
  textClassName?: string
  children?: ReactNode
  copyLabel?: string
  copyOnlyWhenOverflowing?: boolean
  copyValue?: string
  expandLabel?: string
  collapseLabel?: string
  id?: string
  "data-testid"?: string
}) {
  const { t } = useTranslation()
  const generatedId = useId()
  const contentId = id ?? generatedId
  const contentRef = useRef<HTMLPreElement>(null)
  const collapsedButtonRef = useRef<HTMLButtonElement | null>(null)
  const [expandedContent, setExpandedContent] = useState<string | null>(null)
  const expanded = expandedContent === content
  const lines = Math.max(1, previewLines)
  const [measurement, setMeasurement] = useState<{
    content: string
    lines: number
    overflowing: boolean
  } | null>(null)
  const overflowing =
    measurement?.content === content && measurement.lines === lines
      ? measurement.overflowing
      : (content.match(/\r\n|[\r\n]/g)?.length ?? 0) >= lines
  const showCopy =
    copyLabel !== undefined &&
    (!copyOnlyWhenOverflowing || overflowing || expanded)

  useLayoutEffect(() => {
    const button = collapsedButtonRef.current
    collapsedButtonRef.current = null
    if (button?.isConnected && button.ownerDocument.activeElement === button) {
      button.scrollIntoView({ block: "nearest", inline: "nearest" })
    }
  }, [expanded])

  useEffect(() => {
    const element = contentRef.current
    if (!element) return
    const view = element.ownerDocument.defaultView
    const measure = () => {
      // jsdom and hidden ancestors have no layout. Keep the logical-line
      // fallback until a visible browser can measure wrapped visual lines.
      if (element.scrollHeight === 0) return
      const computed = view?.getComputedStyle(element)
      const lineHeight = Number.parseFloat(computed?.lineHeight ?? "")
      const fontSize = Number.parseFloat(computed?.fontSize ?? "")
      const height = Number.isFinite(lineHeight)
        ? lineHeight
        : Number.isFinite(fontSize)
          ? fontSize * 1.5
          : 24
      const overflowing = element.scrollHeight > height * lines + 1
      setMeasurement((previous) =>
        previous?.content === content &&
        previous.lines === lines &&
        previous.overflowing === overflowing
          ? previous
          : { content, lines, overflowing }
      )
    }
    measure()
    const Observer = view?.ResizeObserver ?? globalThis.ResizeObserver
    const observer = Observer ? new Observer(measure) : undefined
    observer?.observe(element)
    view?.addEventListener("resize", measure)
    return () => {
      observer?.disconnect()
      view?.removeEventListener("resize", measure)
    }
  }, [content, lines])

  return (
    <div className={cn("relative min-w-0", className)} data-trace-text-content>
      {showCopy && (
        <TraceCopyButton
          value={copyValue}
          label={copyLabel}
          className="absolute top-0 right-0"
        />
      )}
      <pre
        ref={contentRef}
        id={contentId}
        data-testid={testId}
        className={cn(
          "font-mono text-xs leading-6 [overflow-wrap:anywhere] break-words whitespace-pre-wrap",
          !expanded && "overflow-hidden",
          showCopy && "pr-9",
          textClassName
        )}
        style={expanded ? undefined : { maxHeight: `${lines}lh` }}
      >
        {children ?? <PrivacyMarkedText text={content} />}
      </pre>
      {(overflowing || expanded) && (
        <Button
          type="button"
          variant="ghost"
          size="sm"
          className="mt-1 h-7 px-2 text-xs"
          aria-controls={contentId}
          aria-expanded={expanded}
          onClick={(event) => {
            event.stopPropagation()
            if (expanded) collapsedButtonRef.current = event.currentTarget
            setExpandedContent(expanded ? null : content)
          }}
          onKeyDown={(event) => {
            if (event.key === "Enter" || event.key === " ")
              event.stopPropagation()
          }}
        >
          {expanded
            ? (collapseLabel ?? t("spanContent.collapseText"))
            : (expandLabel ?? t("spanContent.expandText"))}
        </Button>
      )}
    </div>
  )
}
