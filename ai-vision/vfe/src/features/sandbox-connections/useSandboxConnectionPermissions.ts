import { useQuery } from "@tanstack/react-query"
import { api, throwOnApiError } from "@/lib/api"
import { useOptionalUser } from "@/contexts/UserContext"

export function useSandboxConnectionPermissions(spaceId: string) {
  const actor = useOptionalUser()?.user?.user_uuid
  const query = useQuery({
    queryKey: ["settings", spaceId, actor],
    queryFn: async () =>
      throwOnApiError(
        await api.get<{ code: number; data: { permissions?: string[] } }>(
          `/spaces/${spaceId}/settings`
        )
      ),
    enabled: !!spaceId && !!actor,
    staleTime: 30_000,
  })
  const permissions =
    actor && query.data?.code === 0 ? (query.data.data.permissions ?? []) : []
  return {
    canRead: permissions.includes("sandbox_connection.read"),
    canManage: permissions.includes("sandbox_connection.manage"),
    query,
  }
}
