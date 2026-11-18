import { useEffect, useRef, useState } from "react"
import { useTranslation } from "react-i18next"
import { Button } from "@/components/ui/button"
import * as pdfjs from "pdfjs-dist"
import workerUrl from "pdfjs-dist/build/pdf.worker.min.mjs?url"

pdfjs.GlobalWorkerOptions.workerSrc = workerUrl

export default function TracePdfPreview({
  source,
  onError,
}: {
  source: string
  onError: () => void
}) {
  const { t } = useTranslation()
  const canvas = useRef<HTMLCanvasElement>(null)
  const errorHandler = useRef(onError)
  useEffect(() => {
    errorHandler.current = onError
  }, [onError])
  const [selection, setSelection] = useState({ source, page: 1 })
  const [loaded, setLoaded] = useState<{
    source: string
    document: pdfjs.PDFDocumentProxy
  } | null>(null)
  const document = loaded?.source === source ? loaded.document : null
  const page = selection.source === source ? selection.page : 1
  const count = document?.numPages ?? 0

  useEffect(() => {
    const task = pdfjs.getDocument({ url: source })
    let cancelled = false
    task.promise
      .then((document) => {
        if (!cancelled) setLoaded({ source, document })
      })
      .catch(() => {
        if (!cancelled) errorHandler.current()
      })
    return () => {
      cancelled = true
      void task.destroy()
    }
  }, [source])

  useEffect(() => {
    if (!document) return
    let render: pdfjs.RenderTask | undefined
    let cancelled = false
    document
      .getPage(page)
      .then(async (pdfPage) => {
        if (cancelled || !canvas.current) return
        const viewport = pdfPage.getViewport({ scale: 1.2 })
        const context = canvas.current.getContext("2d")
        if (!context) throw new Error("Canvas unavailable")
        canvas.current.width = viewport.width
        canvas.current.height = viewport.height
        render = pdfPage.render({
          canvas: canvas.current,
          canvasContext: context,
          viewport,
        })
        await render.promise
      })
      .catch(() => {
        if (!cancelled) errorHandler.current()
      })
    return () => {
      cancelled = true
      render?.cancel()
    }
  }, [document, page])

  return (
    <div className="mt-2">
      <div className="flex items-center gap-2">
        <Button
          type="button"
          variant="outline"
          size="sm"
          disabled={page <= 1}
          onClick={() => setSelection({ source, page: page - 1 })}
        >
          {t("traceMedia.previous")}
        </Button>
        <span className="text-xs tabular-nums" aria-live="polite">
          {page} / {count || "…"}
        </span>
        <Button
          type="button"
          variant="outline"
          size="sm"
          disabled={page >= count}
          onClick={() => setSelection({ source, page: page + 1 })}
        >
          {t("traceMedia.next")}
        </Button>
      </div>
      <canvas ref={canvas} className="mt-2 max-w-full" />
    </div>
  )
}
