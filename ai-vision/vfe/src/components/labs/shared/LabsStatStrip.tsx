import { cn } from "@/lib/utils"

import { TONE_TEXT_CLASS, type StatTone } from "./labsFormat"
import { LabsMetricLabel } from "./LabsMetricLabel"

export interface StatItem {
  label: string
  value: string
  hint?: string
  help?: string
  tone?: StatTone
}

export type LabsStatColumns = 4 | 5

export function LabsStatStrip({
  items,
  columns = 5,
}: {
  items?: StatItem[]
  columns?: LabsStatColumns
}) {
  const grid = cn(
    "grid grid-cols-2 gap-px overflow-hidden rounded-xl border border-border bg-border",
    columns === 4 ? "lg:grid-cols-4" : "sm:grid-cols-3 lg:grid-cols-5"
  )
  const cell = cn(
    "flex min-h-24 min-w-0 flex-col gap-1 bg-[var(--color-bg-page)] px-4 py-3",
    columns === 5 && "last:max-sm:col-span-2 last:sm:max-lg:col-span-2"
  )

  if (!items) {
    return (
      <div aria-hidden="true" className={grid}>
        {Array.from({ length: columns }, (_, index) => (
          <div key={index} className={cell}>
            <span className="mt-1 h-3 w-14 rounded bg-muted" />
            <span className="mt-1 h-7 w-10 rounded bg-muted" />
          </div>
        ))}
      </div>
    )
  }

  return (
    <dl className={grid}>
      {items.map((item) => (
        <div key={item.label} className={cell}>
          <dt className="text-xs leading-5 text-[var(--color-text-description)]">
            <LabsMetricLabel label={item.label} help={item.help} />
          </dt>
          <dd
            className={cn(
              "font-stats text-2xl leading-8 font-semibold",
              TONE_TEXT_CLASS[item.tone ?? "default"]
            )}
          >
            {item.value}
          </dd>
          {item.hint ? (
            <dd className="text-xs leading-5 [overflow-wrap:anywhere] text-[var(--color-text-description)]">
              {item.hint}
            </dd>
          ) : null}
        </div>
      ))}
    </dl>
  )
}
