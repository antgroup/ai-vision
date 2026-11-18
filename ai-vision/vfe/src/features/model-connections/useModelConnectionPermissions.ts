import { useQuery } from "@tanstack/react-query"
import { api } from "@/lib/api"
import { canManageModelConnections } from "./connectionPresentation"

export function useModelConnectionPermissions(spaceId: string) {
  const query = useQuery({
    queryKey: ["settings", spaceId],
    queryFn: () =>
      api.get<{
        code: number
        data: { permissions?: string[]; role?: number | null }
      }>(`/spaces/${spaceId}/settings`),
    enabled: !!spaceId,
    staleTime: 30_000,
  })
  return {
    canManage:
      query.data?.code === 0 && canManageModelConnections(query.data.data),
    query,
  }
}
