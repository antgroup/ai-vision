import { readTraceMediaText } from "./traceMediaText"
import { hasPrivacyMarker } from "@/labs/privacy/privacyMarkers"
import { useTranslation } from "react-i18next"
/* eslint-disable react-refresh/only-export-components */
import { useEffect, useState } from "react"
import { Dialog, DialogTitle } from "@/components/ui/dialog"
import { DialogContent } from "@/components/dialogs/DialogContent"
import {
  HoverCard,
  HoverCardContent,
  HoverCardTrigger,
} from "@/components/ui/hover-card"

type MediaType = "image" | "video" | "audio" | "file"
type ShowType = "embedded" | "popup" | "hover"

interface ParsedMedia {
  type: MediaType | null
  showType: ShowType
  alt: string
}

const MEDIA_TYPES: MediaType[] = ["image", "video", "audio", "file"]
const SHOW_TYPES: ShowType[] = ["embedded", "popup", "hover"]

const PREVIEWABLE_EXTENSIONS =
  /\.(md|markdown|txt|log|json|yaml|yml|toml|xml|csv|js|ts|tsx|jsx|py|go|java|rs|rb|sh|bash|zsh|fish|sql|html|css|scss|less|c|cpp|h|hpp|swift|kt|lua|r|pl|conf|ini|env|gitignore|dockerfile)(\?|$)/i

function parseMediaAlt(raw?: string): ParsedMedia {
  if (!raw) return { type: null, showType: "embedded", alt: "" }
  const parts = raw.split(":")
  const type = MEDIA_TYPES.includes(parts[0] as MediaType)
    ? (parts[0] as MediaType)
    : null
  if (!type) return { type: null, showType: "embedded", alt: raw }
  const showType = SHOW_TYPES.includes(parts[1] as ShowType)
    ? (parts[1] as ShowType)
    : null
  const alt = showType ? parts.slice(2).join(":") : parts.slice(1).join(":")
  return { type, showType: showType ?? "embedded", alt }
}

function isVideoUrl(url?: string): boolean {
  if (!url) return false
  return /\/video\//.test(url) || /\.(mp4|webm|mov)(\?|$)/i.test(url)
}

function isPreviewableFile(url?: string): boolean {
  if (!url) return false
  return PREVIEWABLE_EXTENSIONS.test(url)
}

function resolveType(parsed: ParsedMedia, src?: string): MediaType {
  if (parsed.type) return parsed.type
  if (isVideoUrl(src)) return "video"
  return "image"
}

function FilePreview({ src, alt }: { src?: string; alt: string }) {
  const { t } = useTranslation()
  const [content, setContent] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(false)

  useEffect(() => {
    if (!src) return
    let cancelled = false
    const controller = new AbortController()
    fetch(src, { signal: controller.signal })
      .then(readTraceMediaText)
      .then((text) => {
        if (!cancelled) {
          setContent(text)
          setLoading(false)
        }
      })
      .catch(() => {
        if (!cancelled) {
          setError(true)
          setLoading(false)
        }
      })
    return () => {
      cancelled = true
      controller.abort()
    }
  }, [src])

  if (src && loading)
    return (
      <span className="text-xs text-muted-foreground">
        {t("common.loading")}
      </span>
    )
  if (!src || error || content === null) {
    return (
      <a
        href={src}
        target="_blank"
        rel="noopener noreferrer"
        className="inline-flex items-center gap-1 text-primary underline underline-offset-4"
      >
        {alt || src}
      </a>
    )
  }

  return (
    <pre className="max-h-96 max-w-full overflow-auto rounded-lg bg-muted p-3 font-mono text-xs">
      <code>{content}</code>
    </pre>
  )
}

function FileElement({ src, alt }: { src?: string; alt: string }) {
  if (isPreviewableFile(src)) {
    return <FilePreview key={src} src={src} alt={alt} />
  }
  return (
    <a
      href={src}
      target="_blank"
      rel="noopener noreferrer"
      className="inline-flex items-center gap-1 text-primary underline underline-offset-4"
    >
      {alt || src}
    </a>
  )
}

function MediaElement({
  type,
  src,
  alt,
}: {
  type: MediaType
  src?: string
  alt: string
}) {
  switch (type) {
    case "video":
      return (
        <video
          src={src}
          controls
          preload="metadata"
          className="max-w-full rounded-lg"
        />
      )
    case "audio":
      return <audio src={src} controls preload="metadata" className="w-full" />
    case "file":
      return <FileElement src={src} alt={alt} />
    default:
      return <img src={src} alt={alt} className="max-w-full rounded-lg" />
  }
}

function MediaPopup({
  type,
  src,
  alt,
}: {
  type: MediaType
  src?: string
  alt: string
}) {
  const { t } = useTranslation()
  const [open, setOpen] = useState(false)
  const label =
    alt ||
    (type === "video"
      ? t("traceLabels.media.playVideo")
      : type === "audio"
        ? t("traceLabels.media.playAudio")
        : type === "file"
          ? t("traceLabels.media.openFile")
          : t("traceLabels.media.viewImage"))

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <button
        type="button"
        onClick={() => setOpen(true)}
        className="cursor-pointer text-primary underline underline-offset-4"
      >
        {label}
      </button>
      <DialogContent size="workspace" className="p-2">
        <DialogTitle className="sr-only">{label}</DialogTitle>
        <div className="flex items-center justify-center">
          <MediaElement type={type} src={src} alt={alt} />
        </div>
      </DialogContent>
    </Dialog>
  )
}

function MediaHover({
  type,
  src,
  alt,
}: {
  type: MediaType
  src?: string
  alt: string
}) {
  const { t } = useTranslation()
  const label =
    alt ||
    (type === "video"
      ? t("traceLabels.media.previewVideo")
      : type === "audio"
        ? t("traceLabels.media.previewAudio")
        : type === "file"
          ? t("traceLabels.media.previewFile")
          : t("traceLabels.media.previewImage"))

  return (
    <HoverCard openDelay={200} closeDelay={100}>
      <HoverCardTrigger asChild>
        <button
          type="button"
          className="cursor-pointer text-primary underline underline-offset-4"
        >
          {label}
        </button>
      </HoverCardTrigger>
      <HoverCardContent className="w-auto max-w-lg p-2" side="top">
        <MediaElement type={type} src={src} alt={alt} />
      </HoverCardContent>
    </HoverCard>
  )
}

export function DeferredMarkdownMedia({
  src,
  alt,
  active = true,
}: {
  src?: string
  alt?: string
  active?: boolean
}) {
  const { t } = useTranslation()
  const [loadedSource, setLoadedSource] = useState<string | null>(null)
  if (!active) return null
  if (src && (hasPrivacyMarker(src) || src.includes("[FILTERED_")))
    return <span>{t("traceMedia.filtered")}</span>
  if (!src || loadedSource !== src)
    return (
      <button
        type="button"
        className="text-primary underline"
        onClick={() => setLoadedSource(src ?? "")}
      >
        {t("spanContent.loadMedia")}{" "}
        {src && /^https?:\/\//i.test(src) && (
          <span className="break-all">{src}</span>
        )}
      </button>
    )
  return <ResolvedMarkdownMedia key={src} src={src} alt={alt} />
}

function ResolvedMarkdownMedia({ src, alt }: { src?: string; alt?: string }) {
  const parsed = parseMediaAlt(alt)
  const type = resolveType(parsed, src)

  switch (parsed.showType) {
    case "popup":
      return <MediaPopup type={type} src={src} alt={parsed.alt} />
    case "hover":
      return <MediaHover type={type} src={src} alt={parsed.alt} />
    default:
      return <MediaElement type={type} src={src} alt={parsed.alt} />
  }
}

export const markdownMediaComponents = { img: DeferredMarkdownMedia }
