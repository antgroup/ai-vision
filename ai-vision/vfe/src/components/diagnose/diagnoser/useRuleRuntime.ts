import { useQuery } from "@tanstack/react-query"
import {
  getSandboxBinding,
  getSandboxConnectionRuntime,
  listSandboxConnections,
} from "@/features/sandbox-connections/api"
import type { SandboxSelection } from "@/features/sandbox-connections/types"
import { RULE_RUNTIME } from "./enhancedRuleModel"

export function useRuleRuntime(
  spaceId: string,
  principal: string | undefined,
  enabled: boolean,
  selection: SandboxSelection
) {
  const active = enabled && !!spaceId && !!principal
  const connections = useQuery({
    queryKey: ["rule-runtime-connections", spaceId, principal],
    queryFn: () => listSandboxConnections(spaceId),
    enabled: active,
  })
  const binding = useQuery({
    queryKey: ["rule-runtime-binding", spaceId, principal],
    queryFn: () => getSandboxBinding(spaceId),
    enabled: active,
  })
  const connectionId =
    selection.mode === "local"
      ? undefined
      : selection.connection_uuid || binding.data?.connection_uuid || undefined
  const connection = connections.data?.items?.find(
    (item) => item.uuid === connectionId
  )
  const runtime = useQuery({
    queryKey: [
      "rule-runtime",
      spaceId,
      principal,
      connectionId,
      connection?.config_revision,
      connection?.image,
    ],
    queryFn: () => getSandboxConnectionRuntime(spaceId, connectionId!),
    enabled: active && !!connectionId && !!connection,
    staleTime: 0,
  })
  const data =
    active &&
    !runtime.isFetching &&
    !connections.isFetching &&
    !binding.isFetching &&
    !runtime.isError &&
    !connections.isError &&
    !binding.isError &&
    connectionId &&
    connection &&
    runtime.data?.connection_uuid === connectionId &&
    runtime.data?.config_revision === connection.config_revision &&
    runtime.data?.image === connection.image
      ? runtime.data
      : undefined
  const manifest =
    data?.ready && data.capabilities.includes("python_eval_v1")
      ? data.runtime_manifests.find(
          (item) =>
            item.id === RULE_RUNTIME &&
            /^[a-f0-9]{64}$/.test(item.manifest_sha256) &&
            item.image === data.image
        )
      : undefined
  return {
    manifest,
    effectiveTimeout: data?.effective_timeout_seconds,
    connection,
    loading:
      active &&
      (connections.isLoading ||
        connections.isFetching ||
        binding.isLoading ||
        binding.isFetching ||
        (!!connectionId && (runtime.isLoading || runtime.isFetching))),
    local:
      selection.mode === "local" ||
      (!!binding.data && selection.mode === "inherit" && !connectionId),
    error: connections.isError || binding.isError || runtime.isError,
    identity: JSON.stringify([
      spaceId,
      principal,
      connectionId,
      connection?.config_revision,
      connection?.image,
      data?.ready,
      data?.capabilities,
      data?.runtime_manifests,
      data?.effective_timeout_seconds,
      data?.effective_input_bytes,
      active,
      runtime.isFetching,
      connections.isFetching,
      binding.isFetching,
      runtime.isError,
      connections.isError,
      binding.isError,
    ]),
  }
}
