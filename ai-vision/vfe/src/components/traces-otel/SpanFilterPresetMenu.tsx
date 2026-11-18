import { useId, useRef, useState } from "react"
import { Bookmark, ChevronDown, Trash2, Undo2 } from "lucide-react"
import { useTranslation } from "react-i18next"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover"
import {
  cloneSpanFilterQuery,
  hasSpanFilterQuery,
  MAX_SPAN_FILTER_PRESET_NAME,
  MAX_SPAN_FILTER_PRESETS,
  readSpanFilterPresets,
  sameSpanFilterQuery,
  validateSpanFilterPresetName,
  writeSpanFilterPresets,
  type SavedSpanFilterPreset,
  type SpanFilterQuery,
} from "./spanFilterPresets"

interface SpanFilterPresetsProps {
  storageKey: string
  query: SpanFilterQuery
  onApply: (query: SpanFilterQuery) => void
}

type UndoAction =
  | { kind: "apply"; query: SpanFilterQuery; activeId: string | null }
  | {
      kind: "delete"
      preset: SavedSpanFilterPreset
      index: number
      wasActive: boolean
    }

function browserStorage(): Storage | null {
  try {
    return window.localStorage
  } catch {
    return null
  }
}

/** Remount with the trace and viewer identity so a new trace starts without an applied preset. */
export function SpanFilterPresets({
  storageKey,
  query,
  onApply,
}: SpanFilterPresetsProps) {
  const { t } = useTranslation()
  const [open, setOpen] = useState(false)
  const [stored, setStored] = useState(() =>
    readSpanFilterPresets(storageKey, browserStorage())
  )
  const [activeId, setActiveId] = useState<string | null>(null)
  const [name, setName] = useState("")
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [undo, setUndo] = useState<UndoAction | null>(null)
  const nameInput = useRef<HTMLInputElement>(null)
  const nameId = useId()
  const presets = stored.ok ? stored.presets : []
  const active = presets.find((preset) => preset.id === activeId)
  const modified = Boolean(active && !sameSpanFilterQuery(active.query, query))
  const meaningful = hasSpanFilterQuery(query)

  function persist(next: SavedSpanFilterPreset[]): boolean {
    if (!stored.ok) return false
    // Do not overwrite a different tab's changes with this menu's stale snapshot.
    const latest = readSpanFilterPresets(storageKey, browserStorage())
    if (
      !latest.ok ||
      JSON.stringify(latest.presets) !== JSON.stringify(presets)
    ) {
      setStored(latest)
      setError(latest.ok ? "storageChanged" : "readFailed")
      setNotice(null)
      return false
    }
    const result = writeSpanFilterPresets(storageKey, next, browserStorage())
    if (!result.ok) {
      setError(result.reason === "invalid" ? "invalidQuery" : "writeFailed")
      setNotice(null)
      return false
    }
    setStored({ ok: true, presets: next })
    setError(null)
    return true
  }

  function save() {
    setNotice(null)
    const nameError = validateSpanFilterPresetName(name, presets)
    if (nameError) {
      setError(nameError)
      return
    }
    if (!meaningful) {
      setError("emptyQuery")
      return
    }
    if (presets.length >= MAX_SPAN_FILTER_PRESETS) {
      setError("limit")
      return
    }
    const preset: SavedSpanFilterPreset = {
      // This is a local record ID, not a credential; HTTP origins may omit randomUUID.
      id:
        globalThis.crypto?.randomUUID?.() ??
        `preset:${Date.now()}:${Math.random().toString(36).slice(2)}`,
      name: name.trim(),
      query: cloneSpanFilterQuery(query),
      updatedAt: Date.now(),
    }
    if (!persist([...presets, preset])) return
    setActiveId(preset.id)
    setName("")
    setNotice("saved")
  }

  function update() {
    if (!active || !meaningful) return
    const next = presets.map((preset) =>
      preset.id === active.id
        ? {
            ...preset,
            query: cloneSpanFilterQuery(query),
            updatedAt: Date.now(),
          }
        : preset
    )
    if (persist(next)) setNotice("updated")
  }

  function apply(preset: SavedSpanFilterPreset) {
    setUndo({ kind: "apply", query: cloneSpanFilterQuery(query), activeId })
    setActiveId(preset.id)
    setError(null)
    setNotice(null)
    onApply(cloneSpanFilterQuery(preset.query))
    setOpen(false)
  }

  function remove(preset: SavedSpanFilterPreset) {
    const index = presets.findIndex((value) => value.id === preset.id)
    if (!persist(presets.filter((value) => value.id !== preset.id))) return
    setUndo({
      kind: "delete",
      preset,
      index,
      wasActive: preset.id === activeId,
    })
    if (preset.id === activeId) setActiveId(null)
    setNotice("deleted")
    nameInput.current?.focus()
  }

  function undoLastAction() {
    if (!undo) return
    if (undo.kind === "apply") {
      onApply(cloneSpanFilterQuery(undo.query))
      setActiveId(undo.activeId)
    } else {
      const nameError = validateSpanFilterPresetName(undo.preset.name, presets)
      if (nameError || presets.length >= MAX_SPAN_FILTER_PRESETS) {
        setError(nameError ?? "limit")
        setOpen(true)
        return
      }
      const next = [...presets]
      next.splice(undo.index, 0, undo.preset)
      if (!persist(next)) {
        setOpen(true)
        return
      }
      if (undo.wasActive) setActiveId(undo.preset.id)
    }
    setUndo(null)
    setNotice(null)
    setError(null)
  }

  return (
    <div className="flex min-w-0 items-center gap-1" data-span-filter-presets>
      <Popover open={open} onOpenChange={setOpen}>
        <PopoverTrigger asChild>
          <Button
            type="button"
            variant="outline"
            size="sm"
            className="h-7 min-w-0 gap-1 px-2 text-xs"
            aria-label={t("nativeSpanPresets.title")}
            title={active?.name}
          >
            <Bookmark className="size-3.5 shrink-0" aria-hidden="true" />
            <span className="max-w-28 truncate">
              {active?.name ?? t("nativeSpanPresets.title")}
            </span>
            {modified && (
              <span className="shrink-0 text-muted-foreground">
                {t("nativeSpanPresets.modified")}
              </span>
            )}
            <ChevronDown className="size-3 shrink-0" aria-hidden="true" />
          </Button>
        </PopoverTrigger>
        <PopoverContent
          align="start"
          className="max-h-[min(34rem,var(--radix-popover-content-available-height))] w-80 max-w-[calc(100vw-2rem)] gap-3 overflow-y-auto rounded-xl p-3"
          aria-label={t("nativeSpanPresets.title")}
        >
          <div>
            <p className="text-sm font-medium">
              {t("nativeSpanPresets.heading")}
            </p>
            <p className="mt-1 text-xs text-muted-foreground">
              {t("nativeSpanPresets.localOnly")}
            </p>
          </div>
          {!stored.ok ? (
            <div className="space-y-2">
              <p role="alert" className="text-xs text-destructive">
                {t(
                  stored.reason === "invalid"
                    ? "nativeSpanPresets.invalidStorage"
                    : "nativeSpanPresets.readFailed"
                )}
              </p>
              <Button
                type="button"
                variant="outline"
                size="sm"
                className="h-7 text-xs"
                onClick={() => {
                  setStored(readSpanFilterPresets(storageKey, browserStorage()))
                  setError(null)
                }}
              >
                {t("nativeSpanPresets.retry")}
              </Button>
            </div>
          ) : (
            <>
              {presets.length ? (
                <div
                  className="max-h-48 space-y-1 overflow-y-auto"
                  aria-label={t("nativeSpanPresets.savedList")}
                >
                  {presets.map((preset) => (
                    <div
                      key={preset.id}
                      className="flex min-w-0 items-center gap-1 rounded-md border border-border/60"
                    >
                      <Button
                        type="button"
                        variant="ghost"
                        size="sm"
                        className="h-8 min-w-0 flex-1 justify-start px-2 text-xs"
                        aria-label={t("nativeSpanPresets.apply", {
                          name: preset.name,
                        })}
                        title={preset.name}
                        onClick={() => apply(preset)}
                      >
                        <span className="truncate">{preset.name}</span>
                        {preset.id === activeId && (
                          <span className="ml-auto shrink-0 text-muted-foreground">
                            {t(
                              modified
                                ? "nativeSpanPresets.modified"
                                : "nativeSpanPresets.applied"
                            )}
                          </span>
                        )}
                      </Button>
                      <Button
                        type="button"
                        variant="ghost"
                        size="icon"
                        className="mr-1 size-7 shrink-0"
                        aria-label={t("nativeSpanPresets.delete", {
                          name: preset.name,
                        })}
                        title={t("nativeSpanPresets.delete", {
                          name: preset.name,
                        })}
                        onClick={() => remove(preset)}
                      >
                        <Trash2 className="size-3.5" aria-hidden="true" />
                      </Button>
                    </div>
                  ))}
                </div>
              ) : (
                <p className="text-xs text-muted-foreground">
                  {t("nativeSpanPresets.none")}
                </p>
              )}
              <p className="text-xs text-muted-foreground">
                {t("nativeSpanPresets.replaceHint")}
              </p>
              {active && modified && (
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  className="h-auto min-h-8 w-full text-xs whitespace-normal"
                  disabled={!meaningful}
                  onClick={update}
                >
                  {t("nativeSpanPresets.update", { name: active.name })}
                </Button>
              )}
              <form
                className="space-y-2 border-t pt-3"
                onSubmit={(event) => {
                  event.preventDefault()
                  save()
                }}
              >
                <label htmlFor={nameId} className="text-xs font-medium">
                  {t("nativeSpanPresets.name")}
                </label>
                <Input
                  ref={nameInput}
                  id={nameId}
                  className="h-8 text-xs"
                  maxLength={MAX_SPAN_FILTER_PRESET_NAME}
                  value={name}
                  placeholder={t("nativeSpanPresets.namePlaceholder")}
                  onChange={(event) => {
                    setName(event.target.value)
                    setError(null)
                    setNotice(null)
                  }}
                  disabled={
                    !meaningful || presets.length >= MAX_SPAN_FILTER_PRESETS
                  }
                />
                <Button
                  type="submit"
                  variant="outline"
                  size="sm"
                  className="h-8 w-full text-xs"
                  disabled={
                    !meaningful || presets.length >= MAX_SPAN_FILTER_PRESETS
                  }
                >
                  {t(
                    active
                      ? "nativeSpanPresets.saveAs"
                      : "nativeSpanPresets.save"
                  )}
                </Button>
                {!meaningful && (
                  <p className="text-xs text-muted-foreground">
                    {t("nativeSpanPresets.emptyQuery")}
                  </p>
                )}
                {presets.length >= MAX_SPAN_FILTER_PRESETS && (
                  <p className="text-xs text-muted-foreground">
                    {t("nativeSpanPresets.limit", {
                      count: MAX_SPAN_FILTER_PRESETS,
                    })}
                  </p>
                )}
              </form>
            </>
          )}
          {error && (
            <p role="alert" className="text-xs text-destructive">
              {t(`nativeSpanPresets.${error}`)}
            </p>
          )}
          {notice && (
            <p role="status" className="text-xs text-muted-foreground">
              {t(`nativeSpanPresets.${notice}`)}
            </p>
          )}
        </PopoverContent>
      </Popover>
      {undo && (
        <Button
          type="button"
          variant="ghost"
          size="sm"
          className="h-7 shrink-0 gap-1 px-1.5 text-xs"
          aria-label={t(
            undo.kind === "apply"
              ? "nativeSpanPresets.undoApply"
              : "nativeSpanPresets.undoDelete"
          )}
          title={t(
            undo.kind === "apply"
              ? "nativeSpanPresets.undoApply"
              : "nativeSpanPresets.undoDelete"
          )}
          onClick={undoLastAction}
        >
          <Undo2 className="size-3" aria-hidden="true" />
          {t("nativeSpanPresets.undo")}
        </Button>
      )}
    </div>
  )
}
