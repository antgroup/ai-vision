import { useTranslation } from "react-i18next"
import { useEffect, useRef, useState } from "react"
import { EditorState } from "@codemirror/state"
import {
  Decoration,
  EditorView,
  MatchDecorator,
  ViewPlugin,
  type DecorationSet,
} from "@codemirror/view"
import { basicSetup } from "codemirror"
import { markdown } from "@codemirror/lang-markdown"
import { python } from "@codemirror/lang-python"
import { json as jsonLang } from "@codemirror/lang-json"
import { Maximize2, Minimize2, Search } from "lucide-react"

import { Button } from "@/components/ui/button"
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import { Dialog, DialogDescription, DialogTitle } from "@/components/ui/dialog"
import { DialogContent } from "@/components/dialogs/DialogContent"

interface CodeMirrorEditorProps {
  value: string
  onChange?: (value: string) => void
  language: "python" | "json" | "markdown"
  readOnly?: boolean
  height?: string
  className?: string
  label?: string
  allowFullscreen?: boolean
  title?: string
  onBrowseTraceVariables?: () => void
  browseTraceVariablesDisabled?: boolean
  fillAvailableHeight?: boolean
  onRestoreSize?: () => void
}

function createMatchHighlight(matcher: MatchDecorator) {
  return ViewPlugin.fromClass(
    class {
      decorations: DecorationSet

      constructor(view: EditorView) {
        this.decorations = matcher.createDeco(view)
      }

      update(update: Parameters<MatchDecorator["updateDeco"]>[0]) {
        this.decorations = matcher.updateDeco(update, this.decorations)
      }
    },
    { decorations: (instance) => instance.decorations }
  )
}

const traceVariableHighlight = createMatchHighlight(
  new MatchDecorator({
    regexp: /\{\{[^{}\n]+\}\}/g,
    decoration: Decoration.mark({ class: "cm-trace-variable" }),
  })
)

export function CodeMirrorEditor({
  value,
  onChange,
  language,
  readOnly,
  height = "300px",
  className,
  label,
  allowFullscreen = false,
  title,
  onBrowseTraceVariables,
  browseTraceVariablesDisabled = false,
  fillAvailableHeight = false,
  onRestoreSize,
}: CodeMirrorEditorProps) {
  const { t } = useTranslation()
  const containerRef = useRef<HTMLDivElement>(null)
  const viewRef = useRef<EditorView | null>(null)
  const onChangeRef = useRef(onChange)
  const [fullscreenOpen, setFullscreenOpen] = useState(false)
  const labelText =
    label ??
    (language === "python"
      ? "Python"
      : language === "json"
        ? "JSON"
        : "Markdown")

  useEffect(() => {
    onChangeRef.current = onChange
  }, [onChange])

  useEffect(() => {
    if (!containerRef.current) return

    const lang =
      language === "python"
        ? python()
        : language === "json"
          ? jsonLang()
          : markdown()
    const extensions = [
      basicSetup,
      EditorView.theme({
        "&": {
          width: "100%",
          height: "100%",
          maxWidth: "100%",
          minWidth: 0,
          fontSize: "13px",
          fontFamily: "var(--font-family-code)",
        },
        "&.cm-focused": { outline: "none" },
        ".cm-scroller": { minWidth: 0, overflow: "auto" },
        ".cm-trace-variable": {
          color: "var(--color-auxiliary)",
          fontWeight: "600",
        },
      }),
    ]
    if (lang) extensions.push(lang)
    if (language === "markdown") {
      extensions.push(traceVariableHighlight, EditorView.lineWrapping)
    }

    if (readOnly) {
      extensions.push(EditorState.readOnly.of(true))
    } else {
      extensions.push(
        EditorView.updateListener.of((update) => {
          if (update.docChanged) {
            onChangeRef.current?.(update.state.doc.toString())
          }
        })
      )
    }

    const state = EditorState.create({ doc: value, extensions })
    const view = new EditorView({ state, parent: containerRef.current })
    viewRef.current = view

    return () => {
      view.destroy()
      viewRef.current = null
    }
  }, [language, readOnly]) // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    const view = viewRef.current
    if (!view) return
    const current = view.state.doc.toString()
    if (current !== value) {
      view.dispatch({
        changes: { from: 0, to: current.length, insert: value },
      })
    }
  }, [value])

  return (
    <>
      <div
        className={`flex min-h-0 w-full max-w-full min-w-0 flex-col ${fillAvailableHeight ? "h-full flex-1" : ""}`}
        style={fillAvailableHeight ? { height: "100%" } : undefined}
      >
        <div
          className={`flex min-h-0 w-full max-w-full min-w-0 flex-col overflow-hidden rounded-lg border bg-background ${fillAvailableHeight ? "flex-1" : "shrink-0"} ${className ?? ""}`}
          style={
            fillAvailableHeight
              ? undefined
              : { height, minHeight: height, maxHeight: height }
          }
        >
          <div className="flex h-8 shrink-0 items-center justify-between border-b bg-muted/20 px-3 font-mono text-[11px] text-muted-foreground">
            <span>{labelText}</span>
            {onRestoreSize ? (
              <Button
                type="button"
                variant="ghost"
                size="icon-xs"
                aria-label={t("diagnose.diagnoser.form.restoreSize")}
                onClick={onRestoreSize}
              >
                <Minimize2 className="size-3.5" />
              </Button>
            ) : allowFullscreen ? (
              <Tooltip>
                <TooltipTrigger asChild>
                  <Button
                    type="button"
                    variant="ghost"
                    size="icon-xs"
                    aria-label={t("diagnose.diagnoser.form.fullscreen")}
                    onClick={() => setFullscreenOpen(true)}
                  >
                    <Maximize2 className="size-3.5" />
                  </Button>
                </TooltipTrigger>
                <TooltipContent side="top">
                  {t("diagnose.diagnoser.form.fullscreen")}
                </TooltipContent>
              </Tooltip>
            ) : null}
          </div>
          <div className="relative min-h-0 flex-1">
            <div ref={containerRef} className="h-full min-h-0" />
            {onBrowseTraceVariables ? (
              <Button
                type="button"
                variant="outline"
                size="xs"
                className="absolute top-2 right-5 z-10 h-7 bg-background px-2 font-sans font-medium shadow-sm"
                disabled={browseTraceVariablesDisabled}
                onClick={onBrowseTraceVariables}
              >
                <Search data-icon="inline-start" className="size-3" />
                {t("diagnose.diagnoser.form.browseTrace")}
              </Button>
            ) : null}
          </div>
        </div>
      </div>
      {allowFullscreen ? (
        <Dialog open={fullscreenOpen} onOpenChange={setFullscreenOpen}>
          <DialogContent
            size="fullscreen"
            showCloseButton={false}
            className="flex flex-col gap-0 overflow-hidden p-0"
          >
            <DialogTitle className="sr-only">{title ?? labelText}</DialogTitle>
            <DialogDescription className="sr-only">
              {language === "python"
                ? t("diagnose.diagnoser.form.editRule")
                : t("diagnose.diagnoser.form.editLogic")}
            </DialogDescription>
            <div className="min-h-0 flex-1">
              <CodeMirrorEditor
                value={value}
                onChange={onChange}
                language={language}
                readOnly={readOnly}
                height="100%"
                fillAvailableHeight
                label={labelText}
                title={title}
                onBrowseTraceVariables={onBrowseTraceVariables}
                browseTraceVariablesDisabled={browseTraceVariablesDisabled}
                onRestoreSize={() => setFullscreenOpen(false)}
                className="rounded-none border-0"
              />
            </div>
          </DialogContent>
        </Dialog>
      ) : null}
    </>
  )
}
