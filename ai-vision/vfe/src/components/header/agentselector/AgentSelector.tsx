import { useEffect, useMemo, useState } from "react"
import { useTranslation } from "react-i18next"
import { Check, ChevronDown, Loader2 } from "lucide-react"
import { useLocation } from "react-router-dom"

import { Button } from "@/components/ui/button"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { useAgent, type Agent } from "@/contexts/AgentContext"
import {
  getAgentProductFromDashboardPath,
  isAgentDashboardPath,
} from "@/agents/lib/agentDashboardRoute"
import { resolveAgentDisplayName } from "@/agents/lib/agent-display"
import { isImageIcon } from "@/agents/lib/agentProducts"
import { cn } from "@/lib/utils"

interface AgentOption {
  value: string
  label: string
  category: "ecosystem" | "thirdParty"
  icon: string | null
}

const ANTCC_PRIORITY = new Set(["AntCC", "ANT_CLAUDE_CODE", "Ant Claude Code"])

function getAgentValue(agent: Agent) {
  return String(agent.agent_product || agent.agent_name || "")
}

function getAgentOptionValue(agent: Agent) {
  return agent.agent_product.toLowerCase() === "langfuse"
    ? String(agent.uuid || "")
    : getAgentValue(agent)
}

/**
 * Classify agent into ecosystem or thirdParty.
 * Uses the same logic as AgentsPage: BUILT_IN/BUILTIN → ecosystem, CUSTOM → thirdParty.
 * If no category is provided, defaults to ecosystem (matches AgentsPage behavior).
 */
function getCategory(agent: Agent): AgentOption["category"] {
  const backendCategory = String(agent.category || "").toUpperCase()
  if (backendCategory === "CUSTOM") return "thirdParty"
  // BUILT_IN, BUILTIN, or empty/missing → ecosystem (same as AgentsPage)
  return "ecosystem"
}

function sortOptions(options: AgentOption[]) {
  return [...options].sort((a, b) => {
    const aPriority =
      ANTCC_PRIORITY.has(a.value) || ANTCC_PRIORITY.has(a.label) ? 0 : 1
    const bPriority =
      ANTCC_PRIORITY.has(b.value) || ANTCC_PRIORITY.has(b.label) ? 0 : 1

    return aPriority - bPriority || a.label.localeCompare(b.label)
  })
}

export function AgentSelector() {
  const { t } = useTranslation()
  const location = useLocation()
  const [optimisticValue, setOptimisticValue] = useState<string | null>(null)
  const { agents, agentTypes, currentAgent, loading, switchAgent } = useAgent()
  const routeAgentProduct = getAgentProductFromDashboardPath(
    location.pathname,
    location.search
  )

  const iconMap = useMemo(() => {
    const map = new Map<string, string | null>()
    for (const agentType of agentTypes) {
      map.set(agentType.agent_product, agentType.icon)
    }
    return map
  }, [agentTypes])

  const options = useMemo(() => {
    const dedupe = new Map<string, AgentOption>()

    for (const agent of agents) {
      const value = getAgentOptionValue(agent)
      if (!value) {
        continue
      }

      dedupe.set(value, {
        value,
        label:
          agent.agent_product.toLowerCase() === "langfuse"
            ? String(agent.custom_name || agent.name || value)
            : resolveAgentDisplayName(value, agentTypes),
        category: getCategory(agent),
        icon:
          agent.agent_product.toLowerCase() === "langfuse"
            ? (iconMap.get(agent.agent_product) ?? null)
            : (iconMap.get(value) ?? null),
      })
    }

    return sortOptions(Array.from(dedupe.values()))
  }, [agents, agentTypes, iconMap])

  const routeAgentType = useMemo(() => {
    if (
      !routeAgentProduct ||
      !isAgentDashboardPath(location.pathname, location.search)
    ) {
      return null
    }
    return (
      agentTypes.find(
        (agentType) =>
          agentType.agent_product === routeAgentProduct ||
          agentType.agent_name === routeAgentProduct
      ) ?? null
    )
  }, [agentTypes, location.pathname, location.search, routeAgentProduct])

  const resolvedSelectedValue = currentAgent
    ? getAgentOptionValue(currentAgent)
    : routeAgentProduct && routeAgentType
      ? routeAgentType.agent_product
      : routeAgentProduct &&
          isAgentDashboardPath(location.pathname, location.search)
        ? routeAgentProduct
        : (options[0]?.value ?? "")
  const selectedValue = optimisticValue ?? resolvedSelectedValue
  const routeAgentTypeLabel = routeAgentType
    ? resolveAgentDisplayName(
        routeAgentType.agent_product || routeAgentType.agent_name,
        [routeAgentType]
      )
    : undefined
  const selectedLabel =
    (options.find((option) => option.value === selectedValue)?.label ??
      routeAgentTypeLabel ??
      resolveAgentDisplayName(selectedValue, agentTypes)) ||
    t("agent.noAgent")
  const groups = [
    {
      key: "ecosystem",
      label: t("agent.ecosystem"),
      items: options.filter((option) => option.category === "ecosystem"),
    },
    {
      key: "thirdParty",
      label: t("agent.thirdParty"),
      items: options.filter((option) => option.category === "thirdParty"),
    },
  ].filter((group) => group.items.length > 0)

  useEffect(() => {
    if (!optimisticValue) return
    if (resolvedSelectedValue === optimisticValue) {
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setOptimisticValue(null)
    }
  }, [optimisticValue, resolvedSelectedValue])

  const handleSelect = (value: string) => {
    setOptimisticValue(value)
    const agent = agents.find((a) => getAgentOptionValue(a) === value)
    if (agent) {
      switchAgent(agent)
    }
  }

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button
          variant="outline"
          size="sm"
          className="h-9 w-64 min-w-0 shrink justify-between gap-2 rounded-full px-3"
          aria-label={`${t("agent.selectAgent")}: ${selectedLabel}`}
          title={selectedLabel}
          disabled={loading || options.length === 0}
        >
          {loading ? (
            <>
              <Loader2 className="size-4 shrink-0 animate-spin text-muted-foreground" />
              <span className="min-w-0 flex-1 truncate text-left text-sm font-normal text-muted-foreground">
                {t("common.loading")}
              </span>
            </>
          ) : (
            <>
              <span className="min-w-0 truncate text-sm font-normal text-foreground">
                {selectedLabel}
              </span>
              <ChevronDown className="size-4 shrink-0 text-muted-foreground" />
            </>
          )}
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent
        align="start"
        className="w-[min(20rem,var(--radix-dropdown-menu-content-available-width))] min-w-0 rounded-xl p-0"
      >
        {groups.length > 0 ? (
          <>
            {groups.map((group, groupIndex) => (
              <DropdownMenuGroup key={group.key}>
                {groupIndex > 0 ? <DropdownMenuSeparator /> : null}
                <DropdownMenuLabel className="px-4 pt-3 text-sm font-normal text-muted-foreground">
                  {group.label}
                </DropdownMenuLabel>
                {group.items.map((option) => (
                  <DropdownMenuItem
                    key={option.value}
                    className="mx-1 flex min-h-10 items-center justify-between gap-3 rounded-lg px-3 text-base font-normal"
                    onSelect={() => handleSelect(option.value)}
                  >
                    <div className="flex items-center gap-2 overflow-hidden">
                      {isImageIcon(option.icon) ? (
                        <img
                          src={option.icon!}
                          alt={option.label}
                          className="size-5 shrink-0 rounded object-contain"
                        />
                      ) : option.icon ? (
                        // Emoji catalog icons (seeded catalogs) are glyphs, not
                        // URLs; rendering them through <img> shows a broken
                        // image placeholder in the dropdown.
                        <span className="flex size-5 shrink-0 items-center justify-center text-sm leading-none">
                          {option.icon}
                        </span>
                      ) : (
                        <span className="flex size-5 shrink-0 items-center justify-center rounded bg-muted text-xs font-medium text-muted-foreground">
                          {option.label.slice(0, 1)}
                        </span>
                      )}
                      <span className="truncate">{option.label}</span>
                    </div>
                    <Check
                      className={cn(
                        "size-4 shrink-0",
                        selectedValue === option.value
                          ? "opacity-100"
                          : "opacity-0"
                      )}
                    />
                  </DropdownMenuItem>
                ))}
              </DropdownMenuGroup>
            ))}
          </>
        ) : (
          <div className="px-4 py-8 text-center text-sm text-muted-foreground">
            {t("agent.noAgent")}
          </div>
        )}
      </DropdownMenuContent>
    </DropdownMenu>
  )
}
