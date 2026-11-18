import { useLayoutEffect, useRef, useState } from "react"
import { useQueryClient } from "@tanstack/react-query"
import { useTranslation } from "react-i18next"
import { Button } from "@/components/ui/button"
import { useSandboxConnectionPermissions } from "@/features/sandbox-connections/useSandboxConnectionPermissions"
import {
  testSandboxConnection,
  updateSandboxConnection,
} from "@/features/sandbox-connections/api"
import type {
  SandboxConnection,
  SandboxConnectionInput,
} from "@/features/sandbox-connections/types"

function recommendedRuleBudget(
  connection: SandboxConnection
): Partial<SandboxConnectionInput> | null {
  const memory = /^([1-9][0-9]*)(Mi|Gi)$/.exec(connection.memory)
  if (!memory) return null
  const memoryMi = Number(memory[1]) * (memory[2] === "Gi" ? 1024 : 1)
  const patch = {
    ...(connection.cpu < 1 ? { cpu: 1 } : {}),
    ...(memoryMi < 1024 ? { memory: "1Gi" } : {}),
    ...(connection.timeout_seconds < 30 ? { timeout_seconds: 30 } : {}),
  }
  return Object.keys(patch).length ? patch : null
}

export function EnhancedRuleBudgetAction({
  spaceId,
  actor,
  connection,
  qualified,
  onBusyChange,
}: {
  spaceId: string
  actor: string
  connection: SandboxConnection
  qualified: boolean
  onBusyChange: (busy: boolean) => void
}) {
  const { t } = useTranslation()
  const permissions = useSandboxConnectionPermissions(spaceId)
  const client = useQueryClient()
  const [pending, setPending] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const active = useRef<object | null>(null)
  const patch = recommendedRuleBudget(connection)
  const identity = JSON.stringify([
    spaceId,
    actor,
    connection.uuid,
    connection.config_revision,
    qualified,
    permissions.canManage,
  ])
  useLayoutEffect(
    () => () => {
      active.current = null
      onBusyChange(false)
    },
    [identity, onBusyChange]
  )
  const invalidate = async () => {
    await Promise.all(
      ["sandbox-connections", "rule-runtime-connections", "rule-runtime"].map(
        (prefix) => client.invalidateQueries({ queryKey: [prefix, spaceId] })
      )
    )
  }
  const apply = async () => {
    if (!qualified || !permissions.canManage || !patch || active.current) return
    const request = {}
    active.current = request
    setPending(identity)
    setError(null)
    onBusyChange(true)
    let applied = false
    try {
      await updateSandboxConnection(spaceId, connection.uuid, {
        ...patch,
        expected_config_revision: connection.config_revision,
      })
      applied = true
      if (active.current !== request) return
      const checked = await testSandboxConnection(spaceId, connection.uuid)
      if (active.current !== request) return
      if (checked.status !== "ready") setError("verifyFailed")
      await invalidate()
    } catch (failure) {
      if (active.current !== request) return
      const status =
        (failure as { statusCode?: number; code?: number })?.statusCode ??
        (failure as { code?: number })?.code
      setError(
        status === 409
          ? "conflict"
          : status === 401 || status === 403
            ? "permission"
            : applied
              ? "verifyFailed"
              : "failed"
      )
      if (applied || status === 409 || status === 401 || status === 403) {
        await invalidate()
        if (active.current !== request) return
        if (status === 401 || status === 403)
          await client.invalidateQueries({
            queryKey: ["settings", spaceId, actor],
          })
      }
    } finally {
      if (active.current === request) {
        active.current = null
        setPending(null)
        onBusyChange(false)
      }
    }
  }
  if (!qualified || !permissions.canManage || !patch) return null
  return (
    <div className="space-y-2 text-xs">
      <div className="flex flex-wrap items-center gap-2">
        <Button
          type="button"
          size="sm"
          variant="outline"
          disabled={pending === identity}
          onClick={apply}
        >
          {t(
            pending === identity
              ? "enhancedRule.budgetAction.applying"
              : "enhancedRule.budgetAction.apply"
          )}
        </Button>
        <span className="text-muted-foreground">
          {t("enhancedRule.budgetAction.help")}
        </span>
      </div>
      {error && (
        <p role="alert" className="text-destructive">
          {t(`enhancedRule.budgetAction.${error}`)}
        </p>
      )}
    </div>
  )
}
