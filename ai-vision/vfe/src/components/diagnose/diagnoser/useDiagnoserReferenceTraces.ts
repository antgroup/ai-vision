import { useQuery } from "@tanstack/react-query"

import { getDiagnoserTraceSample } from "./ruleApi"

export function useDiagnoserReferenceTraceSample(
  spaceId: string,
  agentUuid: string,
  enabled: boolean,
  traceId?: string,
  recentLimit = 1,
  excludeTraceIds: string[] = []
) {
  const excludedKey = [...excludeTraceIds].sort().join(",")
  return useQuery({
    queryKey: [
      "diagnose-reference-trace",
      spaceId,
      agentUuid,
      traceId ?? "recent",
      recentLimit,
      excludedKey,
    ],
    queryFn: () =>
      getDiagnoserTraceSample(
        spaceId,
        agentUuid,
        traceId,
        recentLimit,
        excludeTraceIds
      ),
    enabled,
    staleTime: 30_000,
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
    retry: false,
  })
}
