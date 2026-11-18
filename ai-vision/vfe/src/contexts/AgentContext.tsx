import {
  createContext,
  useContext,
  useState,
  useEffect,
  useLayoutEffect,
  useCallback,
  useRef,
  type ReactNode,
} from "react"
import { useLocation, useSearchParams } from "react-router-dom"
import { useTranslation } from "react-i18next"

import { useSpace } from "@/contexts/SpaceContext"
import { AuthStatusScreen } from "@/components/layout/AuthStatusScreen"
import { getSpaceAgents } from "@/agents/lib/observabilityV2Api"
import {
  getAgentProductFromDashboardPath,
  getAgentProductFromSearch,
  getAgentUuidFromDashboardPath,
  getAgentUuidFromSearch,
  setAgentProductInSearch,
} from "@/agents/lib/agentDashboardRoute"

export interface Agent {
  id?: number
  uuid?: string
  agent_name: string
  agent_product: string
  agent_type_id?: number | null
  runtime_type?: string
  display_name?: string | null
  display_scope?: string | null
  category?: string
  icon?: string | null
  description?: string | null
  metadata?: Record<string, unknown> | null
  capabilities?: Record<string, unknown> | null
  config_guide?: Record<string, unknown> | null
  [key: string]: unknown
}

export interface AgentType {
  agent_name: string
  agent_product: string
  runtime_type?: string
  display_name?: string | null
  display_scope?: string | null
  category: string
  description: string
  icon: string | null
  capabilities: Record<string, unknown> | null
  setup_mode?: string
  cli_command?: string | null
  config_guide?: Record<string, unknown> | null
  sort_order?: number
}

type AgentContextState = {
  agents: Agent[]
  agentTypes: AgentType[]
  currentAgent: Agent | null
  loading: boolean
  error: string | null
  disabledAgents: { uuid: string; agent_product: string }[]
  switchAgent: (agent: Agent | null) => void
  refreshAgents: (options?: AgentRefreshOptions) => Promise<Agent[] | undefined>
}

type AgentRefreshOptions = {
  silent?: boolean
  signal?: AbortSignal
}

const AgentContext = createContext<AgentContextState | undefined>(undefined)

const AGENT_STORAGE_KEY = "selectedAgentProduct"
const WORKSPACE_AGENT_KEY = "ai-vision:last-agent-by-workspace"

function readWorkspaceAgent(workspaceId: string): string | null {
  try {
    const raw = localStorage.getItem(WORKSPACE_AGENT_KEY)
    if (!raw) return null
    const map = JSON.parse(raw) as Record<string, string>
    return map[workspaceId] ?? null
  } catch {
    return null
  }
}

function writeWorkspaceAgent(workspaceId: string, agentProduct: string): void {
  try {
    const raw = localStorage.getItem(WORKSPACE_AGENT_KEY)
    const map: Record<string, string> = raw ? JSON.parse(raw) : {}
    map[workspaceId] = agentProduct
    localStorage.setItem(WORKSPACE_AGENT_KEY, JSON.stringify(map))
  } catch {
    // localStorage unavailable — silent degradation
  }
}

function clearWorkspaceAgent(workspaceId: string): void {
  try {
    const raw = localStorage.getItem(WORKSPACE_AGENT_KEY)
    if (!raw) return
    const map = JSON.parse(raw) as Record<string, string>
    delete map[workspaceId]
    localStorage.setItem(WORKSPACE_AGENT_KEY, JSON.stringify(map))
  } catch {
    // silent
  }
}

function shouldSyncAgentUrl(pathname: string): boolean {
  return (
    pathname === "/" ||
    pathname === "/dashboard" ||
    pathname === "/trace" ||
    pathname === "/trace/compare" ||
    pathname === "/labs/mcp" ||
    pathname === "/labs/skill" ||
    pathname === "/labs/memory" ||
    pathname === "/badcase" ||
    pathname === "/diagnosers" ||
    pathname === "/diagnose-jobs"
  )
}

function getAgentValue(agent: Agent): string {
  return String(agent.agent_product || agent.agent_name || "")
}

function findAgentByProduct(
  agents: Agent[],
  product: string,
  agentTypes: AgentType[] = []
): Agent | null {
  const exactMatch = agents.find(
    (agent) =>
      getAgentValue(agent) === product ||
      agent.agent_product === product ||
      agent.agent_name === product
  )

  if (exactMatch) {
    return exactMatch
  }

  const exactAgentType = agentTypes.find(
    (agentType) =>
      agentType.agent_product === product || agentType.agent_name === product
  )

  if (exactAgentType) {
    return (
      agents.find(
        (agent) =>
          getAgentValue(agent) === exactAgentType.agent_product ||
          agent.agent_product === exactAgentType.agent_product ||
          agent.agent_name === exactAgentType.agent_name
      ) ?? null
    )
  }

  return null
}

function findAgentByUuid(agents: Agent[], uuid: string): Agent | null {
  return agents.find((agent) => agent.uuid === uuid) ?? null
}

export function AgentProvider({ children }: { children: ReactNode }) {
  const { t } = useTranslation()
  const { currentSpace } = useSpace()
  const location = useLocation()
  const [searchParams, setSearchParams] = useSearchParams()
  const [agents, setAgents] = useState<Agent[]>([])
  const [agentTypes, setAgentTypes] = useState<AgentType[]>([])
  const [currentAgent, setCurrentAgent] = useState<Agent | null>(null)
  const [loadedSpaceId, setLoadedSpaceId] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [initialized, setInitialized] = useState(false)
  const [errorKey, setError] = useState<string | null>(null)
  const error = errorKey ? t(errorKey) : null
  const [disabledAgents, setDisabledAgents] = useState<
    { uuid: string; agent_product: string }[]
  >([])
  const lastSyncedAgent = useRef<string | null>(null)
  const pendingAgentSelection = useRef<string | null | undefined>(undefined)
  const spaceSwitchRef = useRef(false)
  const pendingUrlAgentSync = useRef(false)
  const loadedSpaceIdRef = useRef(loadedSpaceId)
  const spaceId = currentSpace?.id ?? null
  const shouldSyncUrl = shouldSyncAgentUrl(location.pathname)
  const agentReadScope = useRef({ spaceId, active: true })

  useLayoutEffect(() => {
    const scope = { spaceId, active: true }
    agentReadScope.current = scope
    return () => {
      scope.active = false
    }
  }, [spaceId])

  useEffect(() => {
    loadedSpaceIdRef.current = loadedSpaceId
  }, [loadedSpaceId])

  const fetchAgents = useCallback(
    async (options?: AgentRefreshOptions) => {
      const silent = options?.silent === true
      const scope = agentReadScope.current
      const canPublish = () => scope.active && !options?.signal?.aborted
      if (scope.spaceId !== spaceId || !canPublish()) return

      if (!spaceId) {
        setAgents([])
        setAgentTypes([])
        setCurrentAgent(null)
        setLoadedSpaceId(null)
        setLoading(false)
        setInitialized(true)
        return []
      }

      if (!silent) {
        setLoading(true)
      }
      setError(null)

      const loadedSpace = loadedSpaceIdRef.current
      const isSpaceSwitch = loadedSpace !== null && loadedSpace !== spaceId

      try {
        const v2Data = await getSpaceAgents(spaceId, options?.signal)
        if (!canPublish()) return
        const items: Agent[] = v2Data.agents.map((a) => ({
          id: a.id,
          uuid: a.uuid,
          agent_name: a.agent_name,
          agent_product: a.agent_product,
          agent_type_id: a.agent_type_id,
          runtime_type: a.runtime_type,
          display_name: a.display_name,
          display_scope: a.display_scope,
          category: a.category ?? undefined,
          icon: a.icon ?? null,
          description: a.description,
          metadata: a.metadata,
          name: a.name,
          custom_name: a.custom_name,
          status: a.status,
          last_active_at: a.last_active_at,
        }))
        const typesData: AgentType[] = v2Data.agent_types.map((t) => ({
          agent_name: t.agent_name,
          agent_product: t.agent_product,
          runtime_type: t.runtime_type,
          display_name: t.display_name,
          display_scope: t.display_scope,
          category: t.category,
          description: t.description ?? "",
          icon: t.icon ?? null,
          setup_mode: t.setup_mode,
          cli_command: t.cli_command,
          config_guide: t.config_guide ?? null,
          capabilities: t.capabilities,
          sort_order: t.sort_order,
        }))

        setAgentTypes(typesData)
        setAgents(items)
        setDisabledAgents(v2Data.disabled_agents || [])

        // On space switch: restore per-workspace memory or pick first agent.
        if (isSpaceSwitch) {
          const rememberedProduct = readWorkspaceAgent(spaceId)
          const remembered = rememberedProduct
            ? findAgentByProduct(items, rememberedProduct, typesData)
            : null

          if (remembered) {
            setCurrentAgent(remembered)
            localStorage.setItem(AGENT_STORAGE_KEY, getAgentValue(remembered))
          } else {
            if (rememberedProduct) {
              clearWorkspaceAgent(spaceId)
            }
            const first = items[0] ?? null
            if (first) {
              setCurrentAgent(first)
              localStorage.setItem(AGENT_STORAGE_KEY, getAgentValue(first))
              writeWorkspaceAgent(spaceId, getAgentValue(first))
            } else {
              setCurrentAgent(null)
              localStorage.removeItem(AGENT_STORAGE_KEY)
            }
          }
          spaceSwitchRef.current = true
          setLoadedSpaceId(spaceId)
          return items
        }

        // Check URL for agent selection
        const currentRouteAgentProduct =
          getAgentProductFromDashboardPath(
            window.location.pathname,
            window.location.search
          ) ||
          (shouldSyncAgentUrl(window.location.pathname)
            ? getAgentProductFromSearch(window.location.search)
            : null)
        // Prefer exact agent_uuid match (multi-instance products like langfuse
        // share one agent_product, so product-only routing picks the wrong one).
        const routeAgentUuid =
          getAgentUuidFromDashboardPath(
            window.location.pathname,
            window.location.search
          ) ||
          (shouldSyncAgentUrl(window.location.pathname)
            ? getAgentUuidFromSearch(window.location.search)
            : null)
        const routeMatch = routeAgentUuid
          ? findAgentByUuid(items, routeAgentUuid)
          : currentRouteAgentProduct
            ? findAgentByProduct(items, currentRouteAgentProduct, typesData)
            : null

        if (routeMatch) {
          const value = getAgentValue(routeMatch)
          setCurrentAgent(routeMatch)
          localStorage.setItem(AGENT_STORAGE_KEY, value)
          writeWorkspaceAgent(spaceId, value)
          setLoadedSpaceId(spaceId)
          return items
        }

        if (currentRouteAgentProduct) {
          setCurrentAgent(null)
          localStorage.setItem(AGENT_STORAGE_KEY, currentRouteAgentProduct)
          setLoadedSpaceId(spaceId)
          return items
        }

        // No route agent — restore from per-workspace memory, then global localStorage, then first.
        const workspaceProduct = readWorkspaceAgent(spaceId)
        const workspaceMatch = workspaceProduct
          ? findAgentByProduct(items, workspaceProduct, typesData)
          : null

        if (workspaceMatch) {
          setCurrentAgent(workspaceMatch)
          localStorage.setItem(AGENT_STORAGE_KEY, getAgentValue(workspaceMatch))
          setLoadedSpaceId(spaceId)
          return items
        }

        if (workspaceProduct) {
          clearWorkspaceAgent(spaceId)
        }

        const savedProduct = localStorage.getItem(AGENT_STORAGE_KEY)
        const match = savedProduct
          ? items.find((agent) => getAgentValue(agent) === savedProduct)
          : null
        const chosen = match ?? items[0] ?? null

        if (chosen) {
          setCurrentAgent(chosen)
          localStorage.setItem(AGENT_STORAGE_KEY, getAgentValue(chosen))
          writeWorkspaceAgent(spaceId, getAgentValue(chosen))
        } else {
          setCurrentAgent(null)
          localStorage.removeItem(AGENT_STORAGE_KEY)
        }
        setLoadedSpaceId(spaceId)
        return items
      } catch (err) {
        if (!canPublish()) return
        if (!silent) {
          setAgents([])
          setAgentTypes([])
          setCurrentAgent(null)
          setLoadedSpaceId(spaceId)
          setError("appState.agentsFailed")
        }
        console.error("Failed to fetch agents:", err)
      } finally {
        if (canPublish()) {
          if (!silent) setLoading(false)
          setInitialized(true)
        }
      }
    },
    [spaceId]
  )

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void fetchAgents()
  }, [fetchAgents])

  const hasLoadedCurrentSpace = loadedSpaceId === spaceId

  const switchAgent = useCallback(
    (agent: Agent | null) => {
      const value = agent ? getAgentValue(agent) : null
      const agentUuid = agent?.uuid ?? null
      if (value !== null) {
        localStorage.setItem(AGENT_STORAGE_KEY, value)
        if (spaceId) {
          writeWorkspaceAgent(spaceId, value)
        }
      } else {
        localStorage.removeItem(AGENT_STORAGE_KEY)
      }

      if (shouldSyncUrl) {
        pendingAgentSelection.current = value
        setSearchParams(
          (prev) => {
            return new URLSearchParams(
              setAgentProductInSearch(prev.toString(), value, {
                agentUuid,
              })
            )
          },
          { replace: true }
        )
        lastSyncedAgent.current = value
      }

      setCurrentAgent(agent)
      setLoadedSpaceId(spaceId)
    },
    [setSearchParams, shouldSyncUrl, spaceId]
  )

  // Non-sync pages: strip stale ?agent= from URL (keep other params intact).
  // /downloads owns its own ?agent= tab param, so it is exempt.
  useEffect(() => {
    if (shouldSyncUrl) return
    if (location.pathname === "/downloads") return
    if (!searchParams.has("agent")) return

    setSearchParams(
      (prev) => {
        const next = new URLSearchParams(prev)
        next.delete("agent")
        return next
      },
      { replace: true }
    )
  }, [searchParams, setSearchParams, shouldSyncUrl, location.pathname])

  // Effect: URL → context
  // Only acts when URL explicitly specifies an agent. When URL has no ?agent=
  // (e.g. navigating from a non-sync page), we do NOT clear context — instead
  // let Context→URL restore the agent to the URL.
  useEffect(() => {
    if (!shouldSyncUrl || !initialized) return

    const urlAgent = getAgentProductFromSearch(location.search)
    const urlAgentUuid = getAgentUuidFromSearch(location.search)
    // Prefer exact agent_uuid match: multi-instance products (langfuse) share
    // one agent_product, so product-only sync picks the wrong project.
    const urlIdentity = urlAgentUuid || urlAgent

    if (pendingAgentSelection.current !== undefined) {
      if (urlAgent === pendingAgentSelection.current) {
        pendingAgentSelection.current = undefined
      } else {
        return
      }
    }

    if (urlIdentity === lastSyncedAgent.current) return

    if (!urlAgent) {
      // URL has no agent — don't override context, let Context→URL restore it.
      lastSyncedAgent.current = null
      return
    }

    if (agents.length > 0) {
      const match = urlAgentUuid
        ? findAgentByUuid(agents, urlAgentUuid)
        : findAgentByProduct(agents, urlAgent, agentTypes)
      // Compare identity by agent_uuid when present (multi-instance products
      // share one agent_product, so product-only comparison misses the switch).
      const currentMatches =
        urlAgentUuid && currentAgent
          ? currentAgent.uuid === urlAgentUuid
          : currentAgent
            ? getAgentValue(currentAgent) === urlAgent
            : false
      if (match && !currentMatches) {
        pendingUrlAgentSync.current = true
        // eslint-disable-next-line react-hooks/set-state-in-effect
        setCurrentAgent(match)
        const value = getAgentValue(match)
        localStorage.setItem(AGENT_STORAGE_KEY, value)
        if (spaceId) {
          writeWorkspaceAgent(spaceId, value)
        }
      }
    }
    lastSyncedAgent.current = urlIdentity
  }, [
    initialized,
    agents,
    agentTypes,
    location.search,
    currentAgent,
    shouldSyncUrl,
    spaceId,
  ])

  // Effect: context → URL (keep ?agent= in sync with currentAgent)
  // Guard: skip while agents are loading for the current space — the internal
  // currentAgent is stale (belongs to the previous space) and must not be
  // pushed to the URL.
  useEffect(() => {
    if (!shouldSyncUrl || !initialized) return
    if (loadedSpaceId !== spaceId) return
    // Agent refresh can commit an older render after a navigation. Let the
    // next router snapshot synchronize the Agent without restoring stale filters.
    if (
      window.location.pathname !== location.pathname ||
      window.location.search !== location.search
    )
      return

    if (pendingUrlAgentSync.current) {
      pendingUrlAgentSync.current = false
      return
    }

    const agentValue = currentAgent ? getAgentValue(currentAgent) : null
    const agentUuid = currentAgent?.uuid ?? null
    // Use agent_uuid as the URL identity so multi-instance products (langfuse)
    // stay pinned to the exact project instead of the shared agent_product.
    const urlIdentity = agentUuid || agentValue
    const currentUrlAgent = getAgentProductFromSearch(location.search)
    const currentUrlUuid = getAgentUuidFromSearch(location.search)
    const currentUrlIdentity = currentUrlUuid || currentUrlAgent

    if (urlIdentity === currentUrlIdentity) {
      lastSyncedAgent.current = urlIdentity
      return
    }
    if (!agentValue && !currentUrlAgent) {
      lastSyncedAgent.current = null
      return
    }

    if (agentValue) {
      setSearchParams(
        (prev) =>
          setAgentProductInSearch(prev.toString(), agentValue, { agentUuid }),
        { replace: true }
      )
    } else {
      setSearchParams(
        (prev) => {
          const next = new URLSearchParams(prev)
          next.delete("agent")
          next.delete("agent_uuid")
          return next
        },
        { replace: true }
      )
    }
    lastSyncedAgent.current = urlIdentity
  }, [
    currentAgent,
    initialized,
    loadedSpaceId,
    spaceId,
    location.pathname,
    location.search,
    setSearchParams,
    shouldSyncUrl,
  ])

  const exposedLoading = loading || !hasLoadedCurrentSpace
  const exposedAgents = hasLoadedCurrentSpace ? agents : []
  const exposedAgentTypes = hasLoadedCurrentSpace ? agentTypes : []
  const exposedCurrentAgent = hasLoadedCurrentSpace ? currentAgent : null

  if (!initialized) {
    return (
      <AuthStatusScreen
        status="loading"
        message={t("appState.loadingAgents")}
      />
    )
  }

  if (error && agents.length === 0) {
    return (
      <AuthStatusScreen
        status="error"
        message={error}
        onRetry={() => void fetchAgents()}
        retryLabel={t("common.retry")}
      />
    )
  }

  return (
    <AgentContext.Provider
      value={{
        agents: exposedAgents,
        agentTypes: exposedAgentTypes,
        currentAgent: exposedCurrentAgent,
        loading: exposedLoading,
        error,
        disabledAgents,
        switchAgent,
        refreshAgents: fetchAgents,
      }}
    >
      {children}
    </AgentContext.Provider>
  )
}

export function useAgent() {
  const ctx = useContext(AgentContext)

  if (ctx === undefined) {
    throw new Error("useAgent must be used within an AgentProvider")
  }

  return ctx
}
