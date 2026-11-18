import type { ReactNode } from "react"

import { cn } from "@/lib/utils"

export interface ErrorPageProps {
  code?: string | number
  icon?: ReactNode
  title: string
  description?: ReactNode
  actions?: ReactNode
  fullScreen?: boolean
}

export function ErrorPage({
  code,
  icon,
  title,
  description,
  actions,
  fullScreen = false,
}: ErrorPageProps) {
  return (
    <div
      className={cn(
        "flex flex-col items-center justify-center gap-4 p-6 text-center",
        fullScreen ? "min-h-screen bg-background" : "min-h-full flex-1",
      )}
    >
      {icon && (
        <div className="flex size-12 items-center justify-center rounded-xl bg-muted [&_svg]:size-6">
          {icon}
        </div>
      )}
      {code && (
        <h1 className="text-6xl font-bold text-muted-foreground">{code}</h1>
      )}
      <h2 className="text-lg font-medium">{title}</h2>
      {description && (
        <p className="max-w-md text-sm text-muted-foreground">{description}</p>
      )}
      {actions && <div className="mt-2">{actions}</div>}
    </div>
  )
}
