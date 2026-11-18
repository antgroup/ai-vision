import { useTranslation } from "react-i18next"
import { cn } from "@/lib/utils"

export type TraceContentMode = "enhanced" | "raw"

/** One content mode, with the original-data format subordinate to raw mode. */
export function TraceContentModeToggle({
  mode,
  onModeChange,
  formatMode,
  onFormatChange,
}: {
  mode: TraceContentMode
  onModeChange: (mode: TraceContentMode) => void
  formatMode: "formatted" | "json"
  onFormatChange: (mode: "formatted" | "json") => void
}) {
  const { t } = useTranslation()
  const buttonClass = (active: boolean) =>
    cn(
      "min-h-7 rounded-sm px-2 py-1 text-xs focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring",
      active
        ? "bg-foreground text-background"
        : "text-muted-foreground hover:bg-muted hover:text-foreground"
    )
  return (
    <div
      className="flex min-w-0 flex-wrap items-center justify-end gap-2"
      data-testid="trace-content-modes"
    >
      <div
        className="flex rounded-md border p-0.5"
        role="group"
        aria-label={t("traceView.contentMode")}
      >
        {(["enhanced", "raw"] as const).map((value) => (
          <button
            key={value}
            type="button"
            data-product-guide={
              value === "enhanced" ? "trace-mode-enhanced" : undefined
            }
            aria-pressed={mode === value}
            className={buttonClass(mode === value)}
            onClick={() => onModeChange(value)}
          >
            {t(`traceView.${value}`)}
          </button>
        ))}
      </div>
      {mode === "raw" && (
        <div
          className="flex rounded-md border p-0.5"
          role="group"
          aria-label={t("traceView.rawFormat")}
        >
          {(["formatted", "json"] as const).map((value) => (
            <button
              key={value}
              type="button"
              aria-pressed={formatMode === value}
              className={buttonClass(formatMode === value)}
              onClick={() => onFormatChange(value)}
            >
              {value === "json" ? "JSON" : t("traceView.tree")}
            </button>
          ))}
        </div>
      )}
    </div>
  )
}
