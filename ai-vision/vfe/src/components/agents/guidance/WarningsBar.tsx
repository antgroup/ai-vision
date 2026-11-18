import { TriangleAlert } from "lucide-react"

interface Props {
  warnings: string[]
}

export function WarningsBar({ warnings }: Props) {
  if (warnings.length === 0) return null

  return (
    <div className="rounded-lg border border-amber-500/30 bg-amber-500/5 p-3 dark:border-amber-400/30 dark:bg-amber-400/5">
      {warnings.map((warning, index) => (
        <p
          key={index}
          className="flex items-start gap-2 text-xs text-amber-600 dark:text-amber-400"
        >
          <TriangleAlert className="mt-0.5 size-3.5 shrink-0" />
          <span>{warning}</span>
        </p>
      ))}
    </div>
  )
}
