import { useMemo } from "react"

import {
  Breadcrumb,
  BreadcrumbItem,
  BreadcrumbList,
  BreadcrumbPage,
  BreadcrumbSeparator,
} from "@/components/ui/breadcrumb"
import { useAgent } from "@/contexts/AgentContext"
import { resolveAgentDisplayName } from "@/agents/lib/agent-display"
import { isImageIcon } from "@/agents/lib/agentProducts"
import { cn } from "@/lib/utils"

interface PageBreadcrumbProps {
  pageTitle: string
  agentProduct: string
  compact?: boolean
}

export function PageBreadcrumb({
  pageTitle,
  agentProduct,
  compact = false,
}: PageBreadcrumbProps) {
  const { agentTypes } = useAgent()

  const agentIcon = useMemo(() => {
    const matched = agentTypes.find(
      (agentType) => agentType.agent_product === agentProduct
    )
    return matched?.icon ?? null
  }, [agentTypes, agentProduct])
  const agentLabel = useMemo(() => {
    return resolveAgentDisplayName(agentProduct, agentTypes)
  }, [agentTypes, agentProduct])

  return (
    <Breadcrumb>
      <BreadcrumbList
        className={cn("min-h-8 text-base", compact && "flex-nowrap")}
      >
        <BreadcrumbItem>
          <BreadcrumbPage className="font-semibold">{pageTitle}</BreadcrumbPage>
        </BreadcrumbItem>
        <BreadcrumbSeparator
          className={compact ? "max-sm:hidden" : undefined}
        />
        <BreadcrumbItem
          className={compact ? "min-w-0 max-sm:hidden" : undefined}
        >
          <BreadcrumbPage
            className={cn(
              "flex items-center gap-1.5 font-normal text-muted-foreground",
              compact && "min-w-0"
            )}
          >
            {isImageIcon(agentIcon) ? (
              <img
                src={agentIcon!}
                alt={agentLabel}
                className="size-4 shrink-0 rounded object-contain"
              />
            ) : agentIcon ? (
              // Catalog icons may be emoji glyphs (seeded catalogs), which an
              // <img> would render as a broken-image placeholder.
              <span className="text-sm leading-none">{agentIcon}</span>
            ) : null}
            {compact ? (
              <span className="truncate" title={agentLabel}>
                {agentLabel}
              </span>
            ) : (
              agentLabel
            )}
          </BreadcrumbPage>
        </BreadcrumbItem>
      </BreadcrumbList>
    </Breadcrumb>
  )
}
