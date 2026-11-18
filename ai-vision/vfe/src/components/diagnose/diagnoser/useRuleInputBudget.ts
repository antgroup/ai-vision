import { useEffect, useState } from "react"
import { useQuery } from "@tanstack/react-query"
import { previewRuleInput } from "./ruleApi"

export function useRuleInputBudget(
  spaceId: string,
  identity: string,
  payload: string | null,
  enabled: boolean
) {
  const [settled, setSettled] = useState<{
    identity: string
    payload: string | null
  } | null>(null)
  useEffect(() => {
    const timer = setTimeout(() => setSettled({ identity, payload }), 500)
    return () => clearTimeout(timer)
  }, [identity, payload])
  const current =
    enabled &&
    !!payload &&
    settled?.identity === identity &&
    settled.payload === payload
  const query = useQuery({
    queryKey: ["rule-input-preview", spaceId, settled?.identity],
    queryFn: ({ signal }) =>
      previewRuleInput(spaceId, JSON.parse(settled!.payload!), signal),
    enabled: current,
    gcTime: 0,
    retry: false,
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
  })
  return {
    data: current && !query.isFetching ? query.data : undefined,
    loading: enabled && (!current || query.isFetching || query.isPending),
    error: current && query.isError,
    retry: () => {
      if (current) void query.refetch()
    },
  }
}
