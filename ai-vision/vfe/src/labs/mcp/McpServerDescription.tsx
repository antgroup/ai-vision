import { useTranslation } from "react-i18next"

import { TraceTextContent } from "@/components/traces/shared/TraceTextContent"
import { cn } from "@/lib/utils"

import { resolveMcpDescription } from "./mcpDescription"
import type { McpDescription, McpServerConfig } from "./types"

export function McpServerDescription({
  server,
  config,
  className,
  feature = "mcp",
}: {
  server: McpDescription & { serverId: string }
  config?: McpServerConfig
  className?: string
  feature?: "mcp" | "memory"
}) {
  const { t } = useTranslation()
  const { description, descriptionSource } = resolveMcpDescription(
    server,
    config
  )
  if (!description) return null

  return (
    <div
      className={cn(
        "flex min-w-0 items-start gap-2 font-sans text-xs leading-5 font-normal break-words text-[var(--color-text-secondary)]",
        className
      )}
    >
      <span
        className="shrink-0 text-[var(--color-text-description)]"
        title={
          descriptionSource
            ? t(`labs.mcp.descriptionSource.${descriptionSource}`)
            : undefined
        }
      >
        {t(
          feature === "memory"
            ? "labs.memory.description"
            : "labs.mcp.descriptionLabel"
        )}
      </span>
      <TraceTextContent
        content={description}
        previewLines={2}
        className="min-w-0 flex-1"
        textClassName="font-sans leading-5"
      />
    </div>
  )
}
