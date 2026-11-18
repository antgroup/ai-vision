import { Info } from "lucide-react"
import { useTranslation } from "react-i18next"
import { Link } from "react-router-dom"

import { Button } from "@/components/ui/button"
import { useAgent } from "@/contexts/AgentContext"
import { useSpace } from "@/contexts/SpaceContext"

export function LabsAgentQueryNotice() {
  const { t } = useTranslation()
  const { currentSpace } = useSpace()
  const { agents, currentAgent, loading, error } = useAgent()

  if (!currentSpace || currentAgent?.agent_product || error || loading)
    return null

  const needsConnection = agents.length === 0
  const message = t(
    needsConnection ? "hint.noAgentDescEmpty" : "hint.noAgentDescSelect"
  )

  return (
    <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2 rounded-lg border border-border bg-muted/30 px-3 py-2 text-sm text-muted-foreground">
      <p role="status" className="flex min-w-0 items-center gap-2">
        <Info className="size-4 shrink-0" aria-hidden="true" />
        {message}
      </p>
      {needsConnection ? (
        <Button asChild variant="outline" size="sm">
          <Link
            to={`/access?${new URLSearchParams({ spaceId: currentSpace.id })}`}
          >
            {t("nav.access")}
          </Link>
        </Button>
      ) : null}
    </div>
  )
}
