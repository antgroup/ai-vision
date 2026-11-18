import type { ReactNode } from "react"
import { LabsMetricLabel } from "@/components/labs/shared/LabsMetricLabel"

export function ActivityTable({
  headers,
  children,
}: {
  headers: (string | { label: string; help: string })[]
  children: ReactNode
}) {
  return (
    <div className="max-h-[520px] overflow-auto rounded-lg border border-[var(--color-border-labs)] bg-[var(--color-bg-page)]">
      <table className="w-full min-w-[560px] text-xs leading-5 text-[var(--color-text-secondary)]">
        <thead className="sticky top-0 z-10 bg-[var(--color-bg-page)] text-[var(--color-text-secondary)]">
          <tr>
            {headers.map((header) => (
              <th
                key={typeof header === "string" ? header : header.label}
                className="border-b border-[var(--color-border-labs)] bg-[var(--color-bg-labs)] px-3 py-2 text-left align-top font-medium whitespace-nowrap"
              >
                {typeof header === "string" ? (
                  header
                ) : (
                  <LabsMetricLabel label={header.label} help={header.help} />
                )}
              </th>
            ))}
          </tr>
        </thead>
        <tbody className="divide-y divide-[var(--color-border)]">
          {children}
        </tbody>
      </table>
    </div>
  )
}
