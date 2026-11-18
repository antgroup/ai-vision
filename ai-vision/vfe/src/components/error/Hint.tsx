import type { ReactNode } from "react"

export interface HintProps {
  icon: ReactNode
  title: string
  description?: string
  action?: ReactNode
}

export function Hint({ icon, title, description, action }: HintProps) {
  return (
    <div className="flex min-h-full flex-1 flex-col items-center justify-center py-16">
      <div className="flex size-12 items-center justify-center rounded-xl bg-muted text-muted-foreground [&_svg]:size-6">
        {icon}
      </div>
      <h2 className="mt-5 text-xl font-semibold text-foreground">{title}</h2>
      {description && (
        <p className="mt-2 text-sm text-muted-foreground">{description}</p>
      )}
      {action && <div className="mt-6">{action}</div>}
    </div>
  )
}
