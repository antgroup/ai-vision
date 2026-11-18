import { useEffect, useId, useState, type ReactNode } from "react"
import { useTranslation } from "react-i18next"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { cn } from "@/lib/utils"
import { SpanFilterHelp } from "./SpanFilterHelp"
import {
  formatSpanThreshold,
  parseSpanThreshold,
  SPAN_THRESHOLD_PRESETS,
  type SpanThresholdKind,
} from "./spanThresholds"

export interface SpanThresholdInputProps {
  value: number | null
  defaultValue: number
  onChange: (value: number | null) => void
  label: string
  kind: SpanThresholdKind
  headerAccessory?: ReactNode
  resetKey?: string | number
  helpText?: string
}

export function SpanThresholdInput({
  value,
  defaultValue,
  onChange,
  label,
  kind,
  headerAccessory,
  resetKey,
  helpText,
}: SpanThresholdInputProps) {
  const { t, i18n } = useTranslation()
  const inputId = useId()
  const hintId = `${inputId}-hint`
  const labelId = `${inputId}-label`
  const errorId = `${inputId}-error`
  const [draft, setDraft] = useState<string | null>(null)
  const [invalid, setInvalid] = useState(false)
  const fallback = kind === "tokens" ? 10000 : 1000
  const enabledValue =
    value === null ? null : parseSpanThreshold(String(value), kind)
  const validDefault =
    parseSpanThreshold(String(defaultValue), kind) ?? fallback
  const displayed = enabledValue ?? validDefault
  const enabled = enabledValue !== null
  const unit = kind === "tokens" ? "Token" : "ms / s / min"
  const inputHint = t(
    `nativeSpanFilters.${kind === "tokens" ? "thresholdTokensHint" : "thresholdDurationHint"}`
  )

  /* eslint-disable react-hooks/set-state-in-effect -- a newly applied combination replaces any uncommitted local input draft */
  useEffect(() => {
    setDraft(null)
    setInvalid(false)
  }, [value, defaultValue, kind, resetKey])
  /* eslint-enable react-hooks/set-state-in-effect */

  const apply = (next: number | null) => {
    setDraft(null)
    setInvalid(false)
    if (next !== value) onChange(next)
  }
  const commitDraft = () => {
    if (!enabled || draft === null) return
    const parsed = parseSpanThreshold(draft, kind)
    if (parsed === null) {
      setInvalid(true)
      return
    }
    apply(parsed)
  }

  return (
    <fieldset
      className={cn(
        "min-w-0 space-y-2 rounded-md border p-2.5",
        enabled
          ? "border-border bg-background/60"
          : "border-border/60 bg-muted/20"
      )}
      data-threshold-kind={kind}
      aria-labelledby={labelId}
    >
      <div className="flex min-w-0 flex-wrap items-center gap-2">
        <div className="flex min-w-0 flex-1 items-center gap-0.5">
          <label
            id={labelId}
            className="flex min-w-0 items-center gap-1.5 text-xs font-medium"
          >
            <input
              type="checkbox"
              className="size-3.5 shrink-0 accent-primary"
              checked={enabled}
              aria-controls={inputId}
              onChange={(event) =>
                apply(event.target.checked ? validDefault : null)
              }
            />
            <span>{label}</span>
          </label>
          <SpanFilterHelp
            name={kind}
            label={t("nativeSpanFilters.thresholdHelp", { label })}
          >
            {helpText && <p>{helpText}</p>}
            <p>{inputHint}</p>
          </SpanFilterHelp>
        </div>
        <div className="flex shrink-0 items-center gap-1 text-xs text-muted-foreground">
          <span aria-hidden="true">≥</span>
          <Input
            data-trace-filter-input
            id={inputId}
            type="text"
            inputMode="text"
            autoComplete="off"
            spellCheck={false}
            disabled={!enabled}
            aria-label={t("nativeSpanFilters.thresholdLabel", { label, unit })}
            aria-describedby={`${hintId}${invalid ? ` ${errorId}` : ""}`}
            aria-invalid={invalid}
            title={t("nativeSpanFilters.thresholdExactValue", {
              value: displayed.toLocaleString(i18n.language, {
                maximumSignificantDigits: 21,
              }),
              unit: kind === "tokens" ? "Token" : "ms",
            })}
            className="h-7 w-20 px-2 text-xs tabular-nums disabled:bg-muted/60"
            value={draft ?? formatSpanThreshold(displayed, kind)}
            onChange={(event) => {
              setDraft(event.target.value)
              setInvalid(false)
            }}
            onBlur={commitDraft}
            onKeyDown={(event) => {
              if (event.nativeEvent.isComposing) return
              if (event.key === "Enter") {
                event.preventDefault()
                event.stopPropagation()
                commitDraft()
              } else if (event.key === "Escape") {
                event.preventDefault()
                event.stopPropagation()
                setDraft(null)
                setInvalid(false)
              }
            }}
          />
        </div>
      </div>
      {headerAccessory && <div className="min-w-0">{headerAccessory}</div>}
      <div
        className="flex min-w-0 flex-wrap gap-1"
        role="group"
        aria-label={t("nativeSpanFilters.thresholdQuickValues", { label })}
      >
        {SPAN_THRESHOLD_PRESETS[kind].map((preset) => {
          const formatted = formatSpanThreshold(preset, kind)
          const selected = enabled && enabledValue === preset
          return (
            <Button
              key={preset}
              type="button"
              variant="outline"
              size="sm"
              className={cn(
                "h-7 min-w-9 px-2 text-[11px] tabular-nums",
                selected && "border-primary/40 bg-primary/10 text-primary"
              )}
              aria-pressed={selected}
              aria-label={t("nativeSpanFilters.thresholdQuickValue", {
                label,
                value: kind === "tokens" ? `${formatted} Token` : formatted,
              })}
              onClick={() => apply(preset)}
            >
              {formatted}
            </Button>
          )
        })}
      </div>
      <p id={hintId} className="sr-only">
        {inputHint}
      </p>
      {invalid && (
        <p
          id={errorId}
          role="alert"
          className="text-[11px] leading-relaxed text-destructive"
        >
          {t(
            `nativeSpanFilters.${kind === "tokens" ? "thresholdTokensInvalid" : "thresholdDurationInvalid"}`
          )}
        </p>
      )}
    </fieldset>
  )
}
