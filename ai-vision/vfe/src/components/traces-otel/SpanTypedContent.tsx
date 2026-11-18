import {
  useContext,
  useEffect,
  useId,
  useMemo,
  useRef,
  useState,
  type ComponentProps,
  type ComponentPropsWithoutRef,
  type ReactNode,
} from "react"
import type { ExtraProps } from "react-markdown"
import { useTranslation } from "react-i18next"
import { PrivacyMarkdown } from "@/labs/privacy/PrivacyMarkdown"
import { PrivacyMarkedText } from "@/labs/privacy/PrivacyMarkedText"
import remarkGfm from "remark-gfm"
import { ArrowRight, WrapText } from "lucide-react"
import { Button } from "@/components/ui/button"
import {
  TraceCopyButton,
  TraceMarkdownLink,
} from "@/components/traces/shared/TraceTextContent"
import { TraceCodeSnippet } from "@/components/traces/shared/TraceCodeSnippet"
import {
  inferTraceCodeLanguage,
  isTraceCodeField,
  TRACE_CODE_PREVIEW_PAGE_SIZE,
  traceCodeFences,
} from "@/components/traces/shared/traceCodeFences"
import { parseStructuredTraceJson } from "../traces/shared/traceJson"
import { TraceEnhancedVisibility } from "@/components/traces/shared/traceEnhancedVisibility"
import { MetadataTree } from "./shared/MetadataTree"
import { markdownMediaComponents } from "./shared/MarkdownMedia"
import { TraceMediaContent } from "@/components/traces/shared/TraceMedia"
import { traceMediaParts } from "@/components/traces/shared/traceMediaProjection"
import { mediaOutputValue } from "@/components/traces/shared/traceMediaOutput"
import { TraceDocumentPreviews } from "@/components/traces/shared/TraceDocumentPreviews"
import { contentPreview } from "./spanContent"

type ContentFormat = "markdown" | "code" | "json"

function TraceMarkdownLinkInNewTab(
  props: ComponentProps<typeof TraceMarkdownLink>
) {
  return (
    <TraceMarkdownLink {...props} target="_blank" rel="noopener noreferrer" />
  )
}

function isLiteralField(field?: string): boolean {
  return (
    isTraceCodeField(field) ||
    (!!field &&
      /^(stdout|stderr|log|logs|stack|stack_trace|traceback|diff|unified_diff|old_text|new_text|before|after|path|file_path|query|pattern|url)$/.test(
        field
      ))
  )
}

function formatFor(value: unknown, field?: string): ContentFormat {
  if (typeof value !== "string") return "json"
  if (isLiteralField(field)) return "code"
  if (
    /^#!\//.test(value) ||
    /^(?:const |let |def |import |from \w+ import |function |export (?:default |const |function ))/m.test(
      value
    )
  )
    return "code"
  return "markdown"
}

/** Enhanced presentation only: one bounded JSON layer; copy retains the source. */
function structuredPreview(
  value: unknown,
  field?: string,
  format?: ContentFormat
): unknown {
  if (
    typeof value !== "string" ||
    value.length > 65536 ||
    format === "code" ||
    formatFor(value, field) === "code"
  )
    return value
  if (!value.trimStart().startsWith("{") && !value.trimStart().startsWith("["))
    return value
  return parseStructuredTraceJson(value) ?? value
}

/** Typed presentation never changes the original value used for copy. */
export function SpanTypedContent({
  value,
  field,
  format,
  copyLabel,
  showCopy = true,
  showDocumentPreviews = true,
  compact = false,
  previewLines = 4,
  treeLayout = "table",
  renderFrame,
}: {
  value: unknown
  field?: string
  format?: ContentFormat
  copyLabel?: string
  showCopy?: boolean
  showDocumentPreviews?: boolean
  compact?: boolean
  previewLines?: number
  treeLayout?: "table" | "outline"
  renderFrame?: (controls: ReactNode, content: ReactNode) => ReactNode
}) {
  const { t } = useTranslation()
  const active = useContext(TraceEnhancedVisibility) !== false
  const displayedValue = structuredPreview(value, field, format)
  const fences = useMemo(() => {
    if (
      !active ||
      typeof displayedValue !== "string" ||
      isLiteralField(field) ||
      (format && format !== "markdown")
    )
      return []
    return traceCodeFences(displayedValue)
  }, [active, displayedValue, field, format])
  const kind =
    format ?? (fences.length ? "markdown" : formatFor(displayedValue, field))
  const structured =
    kind === "json" &&
    displayedValue !== null &&
    typeof displayedValue === "object"
  const text = contentPreview(value, Infinity).text
  const frontmatter =
    kind === "markdown"
      ? /^---\r?\n([\s\S]{0,16384}?)\r?\n---(?:\r?\n|$)/.exec(text)
      : null
  const markdownOffset = frontmatter?.[0].length ?? 0
  const markdownText = text.slice(markdownOffset)
  const renderCodeBlock = useMemo(() => {
    if (!fences.length) return undefined
    const byOffset = new Map(
      fences.slice(0, TRACE_CODE_PREVIEW_PAGE_SIZE).map((fence) => {
        const indent = /^ {0,3}/.exec(
          text.slice(fence.start, fence.start + 3)
        )![0].length
        return [fence.start + indent - markdownOffset, fence]
      })
    )
    return function MarkdownCodeBlock({
      node,
      children,
      ...props
    }: ComponentPropsWithoutRef<"pre"> & ExtraProps) {
      const offset = node?.position?.start.offset
      const fence = offset === undefined ? undefined : byOffset.get(offset)
      return fence ? (
        <TraceCodeSnippet
          value={text.slice(fence.bodyStart, fence.bodyEnd)}
          language={fence.language}
          previewLines={previewLines}
        />
      ) : (
        <pre {...props}>{children}</pre>
      )
    }
  }, [fences, markdownOffset, previewLines, text])
  const contentId = useId()
  const body = useRef<HTMLDivElement>(null)
  const [expanded, setExpanded] = useState(false)
  const [wrap, setWrap] = useState(true)
  const [json, setJson] = useState(false)
  const mediaValue = active ? mediaOutputValue(displayedValue) : null
  const media = active ? traceMediaParts(mediaValue ?? displayedValue) : null
  const isTree = structured && !json && !media
  const [treeExpansion, setTreeExpansion] = useState<"all" | "none">()
  const flowingTree = isTree && treeLayout === "outline"
  const language =
    active && kind === "code" && field !== "url"
      ? inferTraceCodeLanguage(text, field)
      : ""
  const code =
    !!text &&
    (!!language || (active && kind === "code" && isTraceCodeField(field)))
  const hasLimitedFences =
    fences.length > 0 && fences.length <= TRACE_CODE_PREVIEW_PAGE_SIZE
  const ownDisclosure = !media && !json && (code || hasLimitedFences)
  const externalCopy = showCopy && (!code || !!renderFrame)
  const [overflowing, setOverflowing] = useState(
    text.length > 360 || text.split("\n").length > previewLines
  )
  useEffect(() => {
    if (isTree || ownDisclosure) return
    const element = body.current
    if (!element) return
    const view = element.ownerDocument.defaultView
    const measure = () => {
      if (!element.scrollHeight) return
      const height =
        Number.parseFloat(view?.getComputedStyle(element).lineHeight ?? "") ||
        24
      setOverflowing(element.scrollHeight > height * previewLines + 1)
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
  }, [text, json, wrap, isTree, previewLines, ownDisclosure])
  const monospaced = kind !== "markdown"
  const showWrap =
    monospaced &&
    !isTree &&
    (typeof value === "string" || structured) &&
    (!compact || overflowing || !wrap)
  const textOverflowing = !isTree && !media && !ownDisclosure && overflowing
  const toolbar = structured || showWrap || textOverflowing
  const copy = externalCopy ? (
    <TraceCopyButton
      value={text}
      label={copyLabel ?? t("spanContent.copyContent")}
      className={toolbar ? "ml-auto size-6" : "absolute top-0 right-0 size-6"}
    />
  ) : null
  const controls = toolbar ? (
    <div
      data-content-toolbar
      className={`flex flex-wrap items-center gap-1 text-xs ${renderFrame ? "" : "mb-1"}`}
    >
      {structured && (
        <div className="inline-flex rounded border border-border">
          <Button
            type="button"
            variant="ghost"
            size="sm"
            className="h-6 px-2 text-xs aria-pressed:bg-muted"
            aria-pressed={!json}
            onClick={() => setJson(false)}
          >
            {t("spanContent.tree")}
          </Button>
          <Button
            type="button"
            variant="ghost"
            size="sm"
            className="h-6 px-2 text-xs aria-pressed:bg-muted"
            aria-pressed={json}
            onClick={() => setJson(true)}
          >
            {t("spanContent.json")}
          </Button>
        </div>
      )}
      {showWrap && (
        <Button
          type="button"
          variant="ghost"
          size="sm"
          className="size-6 px-1 text-muted-foreground hover:text-foreground"
          aria-label={t(
            wrap ? "spanContent.noWrapText" : "spanContent.wrapText"
          )}
          title={t(wrap ? "spanContent.noWrapText" : "spanContent.wrapText")}
          aria-pressed={wrap}
          onClick={() => setWrap(!wrap)}
        >
          {wrap ? (
            <ArrowRight className="size-3.5" aria-hidden="true" />
          ) : (
            <WrapText className="size-3.5" aria-hidden="true" />
          )}
        </Button>
      )}
      {flowingTree && (
        <Button
          type="button"
          variant="ghost"
          size="sm"
          className="h-6 px-2 text-xs"
          aria-controls={contentId}
          onClick={() =>
            setTreeExpansion(treeExpansion === "all" ? "none" : "all")
          }
        >
          {t(
            treeExpansion === "all"
              ? "traceLabels.collapseAll"
              : "traceLabels.expandAll"
          )}
        </Button>
      )}
      {textOverflowing && (
        <Button
          type="button"
          variant="ghost"
          size="sm"
          className="h-6 px-2 text-xs"
          aria-controls={contentId}
          aria-expanded={expanded}
          onClick={() => setExpanded(!expanded)}
        >
          {t(expanded ? "spanContent.collapseText" : "spanContent.expandText")}
        </Button>
      )}
      {copy}
    </div>
  ) : (
    copy
  )
  const markdown = () => (
    <div className="prose prose-sm dark:prose-invert max-w-none text-sm leading-6 [overflow-wrap:anywhere] [&_pre]:overflow-x-auto [&_pre]:font-mono [&_pre]:text-xs">
      <PrivacyMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          ...markdownMediaComponents,
          ...(renderCodeBlock && { pre: renderCodeBlock }),
          a: TraceMarkdownLinkInNewTab,
        }}
      >
        {markdownText === "" ? t("spanContent.emptyValue") : markdownText}
      </PrivacyMarkdown>
    </div>
  )
  const content = (
    <>
      <div
        ref={body}
        id={contentId}
        data-content-body
        data-content-tree={isTree ? "true" : undefined}
        data-content-structured-scroll={
          isTree && !flowingTree ? "true" : undefined
        }
        role={isTree ? "region" : undefined}
        aria-label={isTree ? t("spanContent.json") : undefined}
        tabIndex={isTree ? 0 : undefined}
        className={`min-w-0 text-xs leading-6 ${toolbar || !externalCopy ? "" : "pr-8"} ${flowingTree ? "" : isTree ? "overflow-auto" : ownDisclosure || (media && !json) || expanded ? "" : "overflow-hidden"}`}
        style={
          flowingTree || ownDisclosure || (media && !json)
            ? undefined
            : isTree
              ? { maxHeight: "min(24rem, 60vh)" }
              : expanded
                ? undefined
                : { maxHeight: `${previewLines}lh` }
        }
      >
        {media && !json ? (
          <TraceMediaContent value={mediaValue ?? displayedValue} />
        ) : isTree ? (
          <MetadataTree
            data={displayedValue}
            hideToolbar
            traceCode={active}
            layout={treeLayout}
            expansion={treeExpansion}
          />
        ) : code ? (
          <TraceCodeSnippet
            value={text}
            language={language}
            previewLines={previewLines}
            copyLabel={copyLabel}
            showCopy={showCopy && !renderFrame}
            wrap={wrap}
          />
        ) : kind === "markdown" ? (
          markdown()
        ) : (
          <pre
            className={`font-mono text-xs leading-6 ${wrap ? "[overflow-wrap:anywhere] break-words whitespace-pre-wrap" : "overflow-x-auto whitespace-pre"}`}
          >
            {text === "" ? (
              t("spanContent.emptyValue")
            ) : (
              <PrivacyMarkedText text={text} />
            )}
          </pre>
        )}
      </div>
      {frontmatter && (
        <details className="mt-2 text-xs">
          <summary className="cursor-pointer rounded-sm text-muted-foreground focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring">
            {t("spanContent.documentMetadata")}
          </summary>
          <pre className="mt-2 overflow-auto font-mono text-xs [overflow-wrap:anywhere] whitespace-pre-wrap">
            <PrivacyMarkedText text={frontmatter[1]} />
          </pre>
        </details>
      )}
    </>
  )
  const enhancedContent = showDocumentPreviews ? (
    <TraceDocumentPreviews
      value={value}
      enabled={!json}
      allowHtmlLinks={kind !== "code" || field === "url"}
    >
      {content}
    </TraceDocumentPreviews>
  ) : (
    content
  )
  return (
    <div
      className="relative min-w-0"
      data-content-excerpt
      data-content-format={kind}
    >
      {renderFrame ? (
        renderFrame(controls, enhancedContent)
      ) : (
        <>
          {controls}
          {enhancedContent}
        </>
      )}
    </div>
  )
}
