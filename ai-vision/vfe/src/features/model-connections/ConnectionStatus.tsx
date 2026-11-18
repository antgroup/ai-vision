import { useTranslation } from "react-i18next"
import { cn } from "@/lib/utils"
import type { ModelConnection } from "./types"
import { connectionStatus } from "./connectionPresentation"

export function ConnectionStatus({
  connection,
}: {
  connection: ModelConnection
}) {
  const { t } = useTranslation()
  const status = connectionStatus(connection)
  return (
    <span
      className={cn(
        "inline-flex items-center gap-2 text-xs",
        status === "available"
          ? "text-[var(--color-success)]"
          : status === "unavailable"
            ? "text-destructive"
            : "text-muted-foreground"
      )}
    >
      <span className="size-1.5 shrink-0 rounded-full bg-current" />
      {t(`modelConnections.${status}`)}
    </span>
  )
}
