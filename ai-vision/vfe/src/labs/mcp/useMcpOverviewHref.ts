import { useCallback } from "react"
import { useLocation } from "react-router-dom"

import type { McpDrawerTab } from "./types"

const CARRIED_PARAMS = ["agent", "spaceId", "range", "from", "to"]

/** Links into the MCP overview, keeping the current agent / space / time filters. */
export function useMcpOverviewHref() {
  const { search } = useLocation()
  return useCallback(
    (serverId: string | null, tab?: McpDrawerTab) => {
      const current = new URLSearchParams(search)
      const next = new URLSearchParams()
      for (const key of CARRIED_PARAMS) {
        const value = current.get(key)
        if (value) next.set(key, value)
      }
      if (serverId) next.set("server", serverId)
      if (tab) next.set("tab", tab)
      return `/labs/mcp?${next.toString()}`
    },
    [search]
  )
}
