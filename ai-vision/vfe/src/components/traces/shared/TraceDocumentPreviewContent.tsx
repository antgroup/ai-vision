import { useEffect, useLayoutEffect, useMemo, useState } from "react"
import { useTranslation } from "react-i18next"
import { FileText, X } from "lucide-react"
import remarkGfm from "remark-gfm"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogClose,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog"
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group"
import { DialogContent } from "@/components/dialogs/DialogContent"
import { PrivacyMarkdown } from "@/labs/privacy/PrivacyMarkdown"
import { PrivacyMarkedText } from "@/labs/privacy/PrivacyMarkedText"
import { TraceCopyButton, TraceLinkAddress } from "./TraceTextContent"
import TraceHtmlPreview from "./TraceHtmlPreview"
import { findTraceDocuments, type TraceDocument } from "./traceDocuments"
import { findTraceHtmlLinks } from "./traceHtmlLinks"
import { fetchTraceHtml } from "./traceMediaText"
import type { TraceHtmlPreviewSelection } from "./traceEnhancedVisibility"

type PreviewEntry = TraceDocument & { remote?: true }
type RemoteResult = { source: string } | { error: true }

function DocumentDialog({
  entry,
  qualifier,
}: {
  entry: PreviewEntry
  qualifier?: string
}) {
  const { t } = useTranslation()
  const [open, setOpen] = useState(false)
  const [mode, setMode] = useState("preview")
  const [result, setResult] = useState<RemoteResult | null>(null)
  const [attempt, setAttempt] = useState(0)
  const label = t(`traceDocuments.${entry.format}`)
  const format = entry.format === "html" ? "HTML" : "Markdown"
  const name = qualifier ? `${label} · ${qualifier}` : label
  const source = entry.remote
    ? result && "source" in result
      ? result.source
      : null
    : entry.source
  useEffect(() => {
    if (!open || !entry.remote) return
    const controller = new AbortController()
    fetchTraceHtml(entry.source, controller.signal, "error").then(
      (source) => {
        if (!controller.signal.aborted) setResult({ source })
      },
      () => {
        if (!controller.signal.aborted) setResult({ error: true })
      }
    )
    return () => controller.abort()
  }, [attempt, entry.remote, entry.source, open])
  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        setOpen(next)
        if (!next) {
          setMode("preview")
          setResult(null)
        }
      }}
    >
      <DialogTrigger asChild>
        <Button
          type="button"
          variant="outline"
          size="sm"
          className="h-7 max-w-full gap-1.5 px-2 text-xs"
          title={entry.path || name}
          data-trace-document={entry.format}
          data-trace-document-link={entry.remote ? "" : undefined}
        >
          <FileText
            className="size-3.5 shrink-0 text-muted-foreground"
            aria-hidden
          />
          <span className="truncate">{name}</span>
        </Button>
      </DialogTrigger>
      <DialogContent
        size="workspace"
        fillHeight
        showCloseButton={false}
        aria-describedby={undefined}
        className="grid-rows-[auto_minmax(0,1fr)] gap-3 overflow-hidden p-3 sm:p-4"
      >
        <div className="flex min-w-0 flex-wrap items-center justify-between gap-x-3 gap-y-2">
          <DialogTitle className="text-sm" title={entry.path || undefined}>
            {format}
          </DialogTitle>
          <div className="ml-auto flex min-w-0 items-center gap-1.5">
            {source !== null && (
              <>
                <ToggleGroup
                  type="single"
                  value={mode}
                  onValueChange={(value) => value && setMode(value)}
                  className="gap-0 rounded-md border p-0.5"
                  aria-label={t("traceView.contentMode")}
                >
                  {(["preview", "source"] as const).map((value) => (
                    <ToggleGroupItem
                      key={value}
                      value={value}
                      className="h-7 !rounded-sm border-0 px-2 text-xs data-[state=on]:bg-foreground data-[state=on]:text-background"
                    >
                      {t(`traceDocuments.${value}`)}
                    </ToggleGroupItem>
                  ))}
                </ToggleGroup>
                <TraceCopyButton
                  value={source}
                  label={t("spanContent.copyContent")}
                  className="size-8"
                />
              </>
            )}
            <DialogClose asChild>
              <Button
                type="button"
                variant="ghost"
                size="icon-sm"
                className="size-8 shrink-0 bg-secondary"
                aria-label={t("common.close")}
                title={t("common.close")}
              >
                <X aria-hidden />
              </Button>
            </DialogClose>
          </div>
        </div>
        <div className="min-h-0 min-w-0 overflow-auto overscroll-contain rounded-md border bg-background">
          {source === null ? (
            <div className="flex h-full flex-col items-center justify-center gap-2 p-4 text-center text-sm text-muted-foreground">
              {result && "error" in result ? (
                <>
                  <p role="alert">{t("traceMedia.error")}</p>
                  <TraceLinkAddress address={entry.source} />
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    onClick={() => {
                      setResult(null)
                      setAttempt((value) => value + 1)
                    }}
                  >
                    {t("common.retry")}
                  </Button>
                </>
              ) : (
                <p role="status">{t("common.loading")}</p>
              )}
            </div>
          ) : mode === "source" ? (
            <pre className="p-3 font-mono text-xs [overflow-wrap:anywhere] whitespace-pre-wrap">
              <PrivacyMarkedText text={source} />
            </pre>
          ) : entry.format === "html" ? (
            <TraceHtmlPreview source={source} />
          ) : (
            <div className="p-4 text-sm leading-6 [overflow-wrap:anywhere] [&_blockquote]:border-l-2 [&_blockquote]:pl-3 [&_code]:font-mono [&_h1]:my-3 [&_h1]:text-xl [&_h2]:my-3 [&_h2]:text-lg [&_h3]:my-2 [&_h3]:text-base [&_li]:ml-5 [&_ol]:list-decimal [&_p]:my-2 [&_pre]:overflow-auto [&_pre]:rounded [&_pre]:bg-muted [&_pre]:p-3 [&_table]:border-collapse [&_td]:border [&_td]:px-2 [&_th]:border [&_th]:px-2 [&_ul]:list-disc">
              <PrivacyMarkdown
                remarkPlugins={[remarkGfm]}
                components={{
                  img: ({ alt }) => (alt ? <span>{alt}</span> : null),
                  a: ({ children }) => <span>{children}</span>,
                }}
              >
                {source.replace(
                  /^---\r?\n[\s\S]{1,16384}?\r?\n---(?:\r?\n|$)/,
                  ""
                )}
              </PrivacyMarkdown>
            </div>
          )}
        </div>
      </DialogContent>
    </Dialog>
  )
}

export default function TraceDocumentPreviewContent({
  value,
  allowHtmlLinks,
  onHtmlPreviewSelection,
}: {
  value: unknown
  allowHtmlLinks: boolean
  onHtmlPreviewSelection?: (selection: TraceHtmlPreviewSelection) => void
}) {
  const links = useMemo(
    () => (allowHtmlLinks ? findTraceHtmlLinks(value) : []),
    [allowHtmlLinks, value]
  )
  const entries = useMemo<PreviewEntry[]>(
    () => [
      ...findTraceDocuments(value),
      ...links.map((link) => ({
        ...link,
        format: "html" as const,
        remote: true as const,
      })),
    ],
    [links, value]
  )
  useLayoutEffect(() => {
    onHtmlPreviewSelection?.({
      value,
      sources: new Set(links.map((link) => link.source)),
    })
  }, [links, onHtmlPreviewSelection, value])
  if (!entries.length) return null
  return (
    <div className="mb-2 flex min-w-0 flex-wrap gap-1.5">
      {entries.map((entry, index) => {
        const siblings = entries.filter(
          (other) => other.format === entry.format
        )
        const ordinal = siblings.indexOf(entry) + 1
        const nameOf = (entry: PreviewEntry) =>
          entry.remote
            ? new URL(entry.source).pathname.split("/").at(-1)
            : entry.path
        const name = nameOf(entry)
        const uniqueName =
          name &&
          siblings.filter((other) => nameOf(other) === name).length === 1
        const qualifier =
          siblings.length === 1
            ? undefined
            : uniqueName
              ? name
              : String(ordinal)
        return (
          <DocumentDialog
            key={JSON.stringify([
              entry.format,
              entry.remote ? entry.source : entry.path,
              index,
            ])}
            entry={entry}
            qualifier={qualifier}
          />
        )
      })}
    </div>
  )
}
