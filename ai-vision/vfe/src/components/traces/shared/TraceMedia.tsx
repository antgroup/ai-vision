import {
  fetchTraceHtml,
  readTraceMediaText,
  TRACE_HTML_PREVIEW_MAX_BYTES,
} from "./traceMediaText"
import { lazy, Suspense, useContext, useEffect, useRef, useState } from "react"
import { useTranslation } from "react-i18next"
import {
  AudioLines,
  ExternalLink,
  FileText,
  Film,
  Image as ImageIcon,
  Maximize2,
  X,
  type LucideIcon,
} from "lucide-react"
import { Button } from "@/components/ui/button"
import { Dialog, DialogTitle } from "@/components/ui/dialog"
import { DialogContent } from "@/components/dialogs/DialogContent"
import { PrivacyMarkedText } from "@/labs/privacy/PrivacyMarkedText"
import { PrivacyMarkdown } from "@/labs/privacy/PrivacyMarkdown"
import remarkGfm from "remark-gfm"
import { fetchWithAuth } from "@/lib/api"
import {
  traceMediaParts,
  traceMediaKind,
  type TraceMediaPart,
} from "./traceMediaProjection"

import { TraceEnhancedVisibility } from "./traceEnhancedVisibility"
import { TraceDocumentPreviews } from "./TraceDocumentPreviews"
import { TraceFencedText } from "./TraceCodeSnippet"
import { traceCodeFences } from "./traceCodeFences"
import { traceMediaCodeParts } from "./traceCodeTextParts"
import { cn } from "@/lib/utils"

const PdfPreview = lazy(() => import("./TracePdfPreview"))
const HtmlPreview = lazy(() => import("./TraceHtmlPreview"))
type MediaPart = Extract<TraceMediaPart, { kind: "media" }>
const mediaIcons: Record<string, LucideIcon> = {
  image: ImageIcon,
  audio: AudioLines,
  video: Film,
  pdf: FileText,
  html: FileText,
  text: FileText,
}

function RenderText({
  value,
  mime,
  expanded = false,
}: {
  value: string
  mime: string
  expanded?: boolean
}) {
  const { t } = useTranslation()
  if (mime === "text/html")
    return (
      <Suspense fallback={<span>{t("common.loading")}</span>}>
        <HtmlPreview
          source={value}
          className={cn(
            "block w-full bg-white",
            expanded ? "h-[65vh]" : "h-64"
          )}
        />
      </Suspense>
    )
  if (mime !== "text/markdown")
    return (
      <>
        {!expanded &&
          mime === "text/plain" &&
          value.length <= TRACE_HTML_PREVIEW_MAX_BYTES &&
          value.trimStart().startsWith("<") && (
            <TraceDocumentPreviews value={value} />
          )}
        <pre className="break-words whitespace-pre-wrap">
          <PrivacyMarkedText text={value} />
        </pre>
      </>
    )
  return (
    <PrivacyMarkdown
      remarkPlugins={[remarkGfm]}
      components={{
        img: () => null,
        a: ({ children }) => <span>{children}</span>,
      }}
    >
      {value}
    </PrivacyMarkdown>
  )
}

function Media({
  part,
  collection = false,
  label,
}: {
  part: MediaPart
  collection?: boolean
  label?: string
}) {
  const { t } = useTranslation()
  const mime = part.mime.toLowerCase()
  const html = mime === "text/html"
  const kind = traceMediaKind(part.mime)
  const Icon = mediaIcons[kind]
  const gallery = collection && kind === "image"
  const fileName = /^https?:\/\//i.test(part.source)
    ? part.source.split(/[?#]/)[0].split("/").at(-1)
    : undefined
  const effectiveSource = part.source
  const [loadedSource, setLoadedSource] = useState<string | null>(null)
  const [open, setOpen] = useState(false)
  const [error, setError] = useState(false)
  const [zoom, setZoom] = useState(1)
  const [position, setPosition] = useState({ x: 0, y: 0 })
  const dialogTitle = useRef<HTMLHeadingElement>(null)
  const drag = useRef<{
    x: number
    y: number
    left: number
    top: number
  } | null>(null)
  useEffect(() => {
    if (!open) return
    const close = () => setOpen(false)
    document.addEventListener("trace-media-close-preview", close)
    return () =>
      document.removeEventListener("trace-media-close-preview", close)
  }, [open])
  const loaded = loadedSource === effectiveSource
  const [attachmentBlob, setAttachmentBlob] = useState<string | null>(null)
  useEffect(() => {
    if (!loaded || !effectiveSource.startsWith("aivision-attachment:")) return
    const controller = new AbortController()
    let objectUrl: string | undefined
    const [attachmentId, scope] = effectiveSource
      .slice("aivision-attachment:".length)
      .split("?")
    fetchWithAuth(
      `/trace-attachments/${attachmentId}/content${scope ? `?${scope}` : ""}`,
      { signal: controller.signal, skipAuthRedirect: true }
    )
      .then(async (response) => {
        if (
          !response.ok ||
          response.headers.get("content-type")?.split(";")[0].toLowerCase() !==
            part.mime.toLowerCase()
        )
          throw new Error()
        const blob = await response.blob()
        if (controller.signal.aborted) return
        objectUrl = URL.createObjectURL(blob)
        setAttachmentBlob(objectUrl)
      })
      .catch(() => {
        if (!controller.signal.aborted) setError(true)
      })
    return () => {
      controller.abort()
      if (objectUrl) URL.revokeObjectURL(objectUrl)
      setAttachmentBlob(null)
    }
  }, [loaded, effectiveSource, part.mime])
  const mediaSource = effectiveSource.startsWith("aivision-attachment:")
    ? attachmentBlob
    : effectiveSource
  const text = mime.startsWith("text/") || mime === "application/json"
  const [remoteText, setRemoteText] = useState<string | null>(null)
  useEffect(() => {
    if (
      !loaded ||
      !text ||
      !mediaSource ||
      !/^(https?:|blob:)/i.test(mediaSource)
    )
      return
    const controller = new AbortController()
    const request = html
      ? fetchTraceHtml(mediaSource, controller.signal)
      : fetch(mediaSource, { signal: controller.signal }).then(
          readTraceMediaText
        )
    request
      .then((value) => {
        if (!controller.signal.aborted) setRemoteText(value)
      })
      .catch(() => {
        if (!controller.signal.aborted) setError(true)
      })
    return () => controller.abort()
  }, [loaded, text, mediaSource, html])
  let decoded = remoteText ?? ""
  let decodeError = false
  if (loaded && text && effectiveSource.startsWith("data:")) {
    try {
      const binary = atob(part.source.split(",")[1])
      if (html && binary.length > TRACE_HTML_PREVIEW_MAX_BYTES)
        throw new Error("HTML preview too large")
      const bytes = Uint8Array.from(binary, (c) => c.charCodeAt(0))
      decoded = new TextDecoder("utf-8", { fatal: true }).decode(bytes)
    } catch {
      decodeError = true
    }
  }
  return (
    <div
      className={cn(
        "min-w-0 rounded-md border p-2 text-sm",
        gallery && "relative flex flex-col",
        gallery && !error && !decodeError && "aspect-square overflow-hidden"
      )}
      data-trace-media
      data-trace-media-entry
      role={label ? "group" : undefined}
      aria-label={label}
    >
      <div
        className={cn(
          "flex flex-wrap items-center gap-1",
          gallery && !loaded && "min-h-0 flex-1",
          gallery && loaded && "shrink-0 justify-end"
        )}
      >
        {!collection ? (
          <span className="mr-1 font-medium">
            {t(`traceMedia.type.${kind}`)}
          </span>
        ) : (
          !gallery && (
            <Icon
              aria-hidden="true"
              className="mr-1 size-4 text-muted-foreground"
            />
          )
        )}
        {collection && !gallery && fileName && (
          <span className="min-w-0 flex-1 truncate" title={fileName}>
            {fileName}
          </span>
        )}
        {!loaded && (
          <Button
            type="button"
            variant="ghost"
            size="sm"
            className={cn(
              "h-7 px-2 text-xs",
              gallery && "h-full w-full flex-col gap-2 whitespace-normal"
            )}
            aria-label={t("spanContent.loadMedia")}
            onClick={() => {
              setError(false)
              setRemoteText(null)
              setLoadedSource(effectiveSource)
            }}
          >
            {gallery && (
              <Icon
                aria-hidden="true"
                className="size-6 text-muted-foreground"
              />
            )}
            {t(collection ? "traceMedia.load" : "spanContent.loadMedia")}
          </Button>
        )}
        {loaded && (
          <Button
            type="button"
            variant="ghost"
            size="sm"
            className={collection ? "size-7 p-0" : "h-7 px-2 text-xs"}
            aria-label={t("traceMedia.close")}
            title={t("traceMedia.close")}
            onClick={() => {
              setOpen(false)
              setLoadedSource(null)
            }}
          >
            {collection ? (
              <X aria-hidden="true" className="size-4" />
            ) : (
              t("traceMedia.close")
            )}
          </Button>
        )}
        {loaded &&
          mediaSource &&
          !gallery &&
          (!html ||
            (!error &&
              !decodeError &&
              (remoteText !== null ||
                effectiveSource.startsWith("data:")))) && (
            <Button
              type="button"
              variant="ghost"
              size="sm"
              className={collection ? "size-7 p-0" : "h-7 px-2 text-xs"}
              aria-label={t("traceMedia.preview")}
              title={t("traceMedia.preview")}
              onClick={() => {
                setZoom(1)
                setPosition({ x: 0, y: 0 })
                setOpen(true)
              }}
            >
              {collection ? (
                <Maximize2 aria-hidden="true" className="size-4" />
              ) : (
                t("traceMedia.preview")
              )}
            </Button>
          )}
        {!html &&
          (/^https?:\/\//i.test(part.source) ||
            (loaded && !text && mediaSource)) && (
            <a
              href={mediaSource ?? undefined}
              target="_blank"
              rel="noopener noreferrer"
              download={
                part.source.startsWith("data:") ? "attachment" : undefined
              }
              className={cn(
                "rounded-md text-xs text-primary focus-visible:outline-2 focus-visible:outline-ring",
                gallery && !loaded && "absolute top-1 right-1",
                collection
                  ? "inline-flex size-7 items-center justify-center hover:bg-accent"
                  : "px-2 py-1 hover:underline"
              )}
              aria-label={t("traceMedia.open")}
              title={
                /^https?:\/\//i.test(part.source) ? part.source : undefined
              }
            >
              {collection ? (
                <ExternalLink aria-hidden="true" className="size-4" />
              ) : (
                t("traceMedia.open")
              )}
              {!collection && /^https?:\/\//i.test(part.source) && (
                <span className="ml-1 inline-block max-w-40 truncate align-bottom">
                  {fileName}
                </span>
              )}
            </a>
          )}
      </div>
      {loaded && !error && !mediaSource && <span>{t("common.loading")}</span>}
      {loaded &&
        (!open || kind === "image") &&
        !error &&
        mediaSource &&
        (part.mime.startsWith("image/") ? (
          <button
            type="button"
            onClick={() => setOpen(true)}
            className={cn(
              "rounded-md focus-visible:outline-2 focus-visible:outline-ring",
              gallery && "flex min-h-0 flex-1 items-center justify-center"
            )}
            aria-label={t("traceMedia.enlarge")}
          >
            <img
              src={mediaSource}
              alt={t("traceMedia.image")}
              onError={() => setError(true)}
              className={
                gallery
                  ? "h-full w-full object-contain"
                  : "mt-2 max-h-48 max-w-full rounded-md"
              }
            />
          </button>
        ) : part.mime.startsWith("audio/") ? (
          <audio
            className="mt-2 w-full max-w-md"
            controls
            preload="metadata"
            src={mediaSource}
            onError={() => setError(true)}
          />
        ) : part.mime.startsWith("video/") ? (
          <video
            className="mt-2 aspect-video max-h-96 w-full max-w-md rounded bg-muted object-contain"
            controls
            preload="metadata"
            src={mediaSource}
            onError={() => setError(true)}
          />
        ) : part.mime === "application/pdf" ? (
          <Suspense fallback={<span>{t("common.loading")}</span>}>
            <PdfPreview source={mediaSource} onError={() => setError(true)} />
          </Suspense>
        ) : text &&
          !decodeError &&
          (remoteText !== null || part.source.startsWith("data:")) ? (
          <div className="mt-2 max-h-64 overflow-auto">
            <RenderText value={decoded} mime={mime} />
          </div>
        ) : text && !decodeError ? (
          <span>{t("common.loading")}</span>
        ) : (
          <p role="alert">{t("traceMedia.error")}</p>
        ))}
      {(error || decodeError) && <p role="alert">{t("traceMedia.error")}</p>}
      <Dialog open={open && loaded} onOpenChange={setOpen}>
        <DialogContent
          data-trace-media-dialog
          size="workspace"
          onOpenAutoFocus={
            html
              ? (event) => {
                  if (!dialogTitle.current) return
                  // Start in the host dialog so Escape is available immediately.
                  event.preventDefault()
                  dialogTitle.current.focus({ preventScroll: true })
                }
              : undefined
          }
        >
          <DialogTitle ref={dialogTitle} tabIndex={html ? -1 : undefined}>
            {label ?? t(`traceMedia.type.${kind}`)}
          </DialogTitle>
          {mediaSource && part.mime.startsWith("image/") ? (
            <>
              <div className="flex flex-wrap items-center gap-2">
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  onClick={() => setZoom((z) => Math.max(1, z - 0.25))}
                >
                  {t("traceMedia.zoomOut")}
                </Button>
                <span>{Math.round(zoom * 100)}%</span>
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  onClick={() => setZoom((z) => Math.min(4, z + 0.25))}
                >
                  {t("traceMedia.zoomIn")}
                </Button>
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  onClick={() => {
                    setZoom(1)
                    setPosition({ x: 0, y: 0 })
                  }}
                >
                  {t("traceMedia.resetZoom")}
                </Button>
              </div>
              <div
                className="flex h-[65vh] touch-none items-center justify-center overflow-hidden rounded bg-muted/30 focus-visible:outline-2 focus-visible:outline-ring"
                tabIndex={0}
                aria-label={t("traceMedia.panHint")}
                onPointerDown={(e) => {
                  drag.current = {
                    x: e.clientX,
                    y: e.clientY,
                    left: position.x,
                    top: position.y,
                  }
                  e.currentTarget.setPointerCapture(e.pointerId)
                }}
                onPointerMove={(e) => {
                  if (drag.current)
                    setPosition({
                      x: drag.current.left + e.clientX - drag.current.x,
                      y: drag.current.top + e.clientY - drag.current.y,
                    })
                }}
                onPointerUp={() => {
                  drag.current = null
                }}
                onPointerCancel={() => {
                  drag.current = null
                }}
                onKeyDown={(e) => {
                  const d: Record<string, [number, number]> = {
                    ArrowLeft: [-20, 0],
                    ArrowRight: [20, 0],
                    ArrowUp: [0, -20],
                    ArrowDown: [0, 20],
                  }
                  if (d[e.key]) {
                    e.preventDefault()
                    setPosition((p) => ({
                      x: p.x + d[e.key][0],
                      y: p.y + d[e.key][1],
                    }))
                  }
                }}
              >
                <img
                  src={mediaSource}
                  alt={t("traceMedia.image")}
                  draggable={false}
                  className="max-h-full max-w-full object-contain"
                  style={{
                    transform: `translate(${position.x}px, ${position.y}px) scale(${zoom})`,
                  }}
                />
              </div>
            </>
          ) : mediaSource && part.mime.startsWith("audio/") ? (
            <audio className="w-full" controls src={mediaSource} />
          ) : mediaSource && part.mime.startsWith("video/") ? (
            <video
              controls
              className="aspect-video max-h-[70vh] w-full object-contain"
              src={mediaSource}
            />
          ) : mediaSource && part.mime === "application/pdf" ? (
            <Suspense fallback={<span>{t("common.loading")}</span>}>
              <PdfPreview source={mediaSource} onError={() => setError(true)} />
            </Suspense>
          ) : text ? (
            <div className="max-h-[70vh] overflow-auto">
              <RenderText value={decoded} mime={mime} expanded />
            </div>
          ) : null}
        </DialogContent>
      </Dialog>
    </div>
  )
}

export function TraceMediaContent({ value }: { value: unknown }) {
  const parts = traceMediaParts(value)
  if (!parts) return null
  const resultSummary =
    value &&
    typeof value === "object" &&
    !Array.isArray(value) &&
    typeof (value as Record<string, unknown>).summary === "string"
      ? ((value as Record<string, unknown>).summary as string)
      : null
  return (
    <div className="min-w-0 space-y-2" data-trace-media-content>
      {resultSummary && (
        <p className="break-words whitespace-pre-wrap">
          <PrivacyMarkedText text={resultSummary} />
        </p>
      )}
      <TraceMediaPartsContent parts={traceMediaCodeParts(parts)} />
    </div>
  )
}

export function TraceMediaPartsContent({ parts }: { parts: TraceMediaPart[] }) {
  const { t } = useTranslation()
  const active = useContext(TraceEnhancedVisibility)
  if (!active) return null
  const groups: Array<MediaPart[] | Exclude<TraceMediaPart, MediaPart>> = []
  for (const part of parts) {
    const previous = groups.at(-1)
    if (
      part.kind === "invalid" &&
      !Array.isArray(previous) &&
      previous?.kind === "invalid" &&
      previous.reason === part.reason
    )
      continue
    if (part.kind !== "media") groups.push(part)
    else if (
      Array.isArray(previous) &&
      traceMediaKind(previous[0].mime) === traceMediaKind(part.mime)
    )
      previous.push(part)
    else groups.push([part])
  }
  const positions = new Map<string, number>()
  return (
    <div className="min-w-0 space-y-2">
      {groups.map((part, index) =>
        Array.isArray(part) ? (
          <div
            key={index}
            data-trace-media-group={traceMediaKind(part[0].mime)}
            className={
              part.length > 1 && traceMediaKind(part[0].mime) === "image"
                ? "grid grid-cols-[repeat(auto-fill,minmax(min(100%,8rem),1fr))] items-start gap-2"
                : "space-y-2"
            }
          >
            {part.map((media, mediaIndex) => {
              const kind = traceMediaKind(media.mime)
              const position = (positions.get(kind) ?? 0) + 1
              positions.set(kind, position)
              return (
                <Media
                  key={`${mediaIndex}:${media.source}`}
                  part={media}
                  collection={part.length > 1}
                  label={
                    part.length > 1
                      ? `${t(`traceMedia.type.${kind}`)} ${position}`
                      : undefined
                  }
                />
              )
            })}
          </div>
        ) : part.kind === "text" ? (
          <TraceFencedText
            key={index}
            value={part.text}
            fences={traceCodeFences(part.text)}
            renderText={(text) => (
              <p className="break-words whitespace-pre-wrap">
                <PrivacyMarkedText text={text} />
              </p>
            )}
          />
        ) : part.kind === "invalid" ? (
          <p key={index} role="alert" data-trace-media-entry>
            {t(`traceMedia.${part.reason}`)}
          </p>
        ) : (
          <pre
            key={index}
            className="max-h-40 overflow-auto break-all whitespace-pre-wrap"
          >
            {JSON.stringify(part.value, null, 2)}
          </pre>
        )
      )}
    </div>
  )
}
