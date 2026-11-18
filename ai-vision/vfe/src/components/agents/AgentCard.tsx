import { type CSSProperties } from "react"
import { useTranslation } from "react-i18next"
import { useNavigate } from "react-router-dom"
import { AppWindow, MinusCircle, Pencil } from "lucide-react"

import { Button } from "@/components/ui/button"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import { cn } from "@/lib/utils"
import { resolveAgentDisplayName } from "@/agents/lib/agent-display"
import {
  buildAgentDashboardUrl,
  buildAgentScopedUrl,
} from "@/agents/lib/agentDashboardRoute"
import {
  type AgentCardTheme,
  getCapability,
  getCapabilityTip,
  isImageIcon,
  getIconFallback,
} from "@/agents/lib/agentProducts"

export interface AgentCardProps {
  agentProduct: string
  agentUuid?: string | null
  displayName: string
  description: string | null
  icon: string | null | undefined
  theme: AgentCardTheme
  status?: number
  statusText?: string
  framework?: string
  capabilities?: Record<string, unknown> | null
  integration?: { icon: string | null; onEdit?: () => void }
  onRemove?: () => void
}

export function AgentCard({
  agentProduct,
  agentUuid,
  displayName,
  description,
  icon,
  theme,
  status,
  statusText,
  framework,
  capabilities,
  integration,
  onRemove,
}: AgentCardProps) {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const detail = description || resolveAgentDisplayName(agentProduct, [])

  const traceSupported = getCapability(capabilities, "supports_trace")
  const traceDisabledTip =
    getCapabilityTip(capabilities, "trace_disabled_tip") ||
    t("agentAccess.traceUnsupported")

  /* Light-mode gradient via inline style; dark mode hides it via opacity */
  const gradientStyle: CSSProperties = {
    background: theme.gradientCss,
  }

  return (
    <div className="group relative w-full overflow-hidden rounded-xl border border-border bg-card shadow-sm">
      {/* Gradient overlay — visible in light mode, hidden in dark */}
      <div className="absolute inset-0 dark:opacity-0" style={gradientStyle} />

      {onRemove && (
        <div className="absolute top-3 right-3 z-10">
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <button
                type="button"
                aria-label={t("agentAccess.removeAgent")}
                className="inline-flex size-7 cursor-pointer items-center justify-center rounded-md text-muted-foreground transition-opacity duration-150 group-hover:opacity-100 hover:bg-muted hover:text-foreground focus-visible:opacity-100 data-[state=open]:opacity-100 [@media(hover:hover)]:opacity-0"
              >
                <MinusCircle className="size-4 shrink-0" />
              </button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end" className="w-40">
              <DropdownMenuItem
                className="cursor-pointer text-destructive"
                onClick={onRemove}
              >
                {t("agentAccess.removeFromChannel")}
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        </div>
      )}

      {/* Card body */}
      <div className="relative flex h-full flex-col p-5">
        {/* Icon */}
        <div
          className={cn(
            "mb-4 flex size-11 items-center justify-center rounded-lg text-sm font-medium",
            "bg-background dark:bg-muted"
          )}
        >
          {isImageIcon(icon) ? (
            <img
              src={icon!}
              alt={integration ? "" : displayName}
              className="size-full rounded-lg object-contain"
            />
          ) : integration ? (
            <span className="text-base text-foreground" aria-hidden="true">
              {Array.from(displayName.trim())[0]?.toUpperCase() || (
                <AppWindow className="size-5 text-muted-foreground" />
              )}
            </span>
          ) : (
            <span className="text-foreground">
              {getIconFallback(displayName)}
            </span>
          )}
        </div>

        {/* Name & description */}
        <div className="mb-4 space-y-1.5">
          <div className="flex items-center gap-2">
            <h3 className="truncate text-base font-medium text-foreground">
              {displayName}
            </h3>
            {/* Status chip — driven by connection status for every agent.
                statusText only overrides the label (e.g. langfuse "未接入"). */}
            {(status === 1 || status === 2 || !!statusText) && (
              <span
                className={cn(
                  "inline-flex shrink-0 items-center gap-1.5 text-xs font-normal",
                  status === 1
                    ? "text-emerald-600 dark:text-emerald-400"
                    : "text-amber-600 dark:text-amber-400"
                )}
              >
                <span className="inline-block size-2 shrink-0 rounded-full bg-current" />
                {statusText ??
                  (status === 1
                    ? t("agentAccess.connected")
                    : t("agentAccess.pending"))}
              </span>
            )}
            {framework && (
              <span className="shrink-0 text-xs text-foreground">
                {framework}
              </span>
            )}
          </div>
          {detail &&
            detail !== displayName &&
            (isImageIcon(integration?.icon) ? (
              <div className="inline-flex max-w-full items-center gap-1.5 rounded-md border border-border bg-background/70 px-2 py-1 text-xs text-muted-foreground">
                <img
                  src={integration?.icon ?? undefined}
                  alt=""
                  className="size-4 shrink-0 object-contain"
                />
                <p className="truncate">{detail}</p>
              </div>
            ) : (
              <p className="truncate text-xs text-muted-foreground">{detail}</p>
            ))}
        </div>

        {/* Action buttons */}
        <div
          className={cn(
            "mt-auto flex gap-2",
            integration?.onEdit && "flex-wrap"
          )}
        >
          {integration?.onEdit && (
            <Button
              variant="outline"
              size="sm"
              className="flex-1 bg-background font-normal"
              onClick={integration.onEdit}
            >
              <Pencil className="size-3.5" aria-hidden="true" />
              {t("agentAccess.editSetup")}
            </Button>
          )}
          <Button
            variant="outline"
            size="sm"
            className="flex-1 bg-background font-normal"
            onClick={() =>
              navigate(
                buildAgentDashboardUrl(agentProduct, {
                  agentUuid: agentUuid || null,
                })
              )
            }
          >
            {t("agentAccess.dashboard")}
          </Button>
          {traceSupported ? (
            <Button
              variant="outline"
              size="sm"
              className="flex-1 bg-background font-normal"
              onClick={() =>
                navigate(
                  buildAgentScopedUrl("/trace", agentProduct, "", agentUuid)
                )
              }
            >
              {t("agentAccess.trace")}
            </Button>
          ) : (
            <TooltipProvider>
              <Tooltip>
                <TooltipTrigger asChild>
                  <div className="flex-1">
                    <Button
                      variant="outline"
                      size="sm"
                      className="pointer-events-none w-full bg-background font-normal"
                      disabled
                    >
                      {t("agentAccess.trace")}
                    </Button>
                  </div>
                </TooltipTrigger>
                <TooltipContent side="bottom">
                  {traceDisabledTip}
                </TooltipContent>
              </Tooltip>
            </TooltipProvider>
          )}
        </div>
      </div>
    </div>
  )
}
