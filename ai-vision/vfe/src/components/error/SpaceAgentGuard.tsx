import { Trans, useTranslation } from "react-i18next"
import { useState, useEffect, type ReactNode } from "react"
import { AlertTriangle, Info, SearchX } from "lucide-react"
import { useLocation, useNavigate } from "react-router-dom"

import { Button } from "@/components/ui/button"
import { ErrorPage } from "@/components/error/ErrorPage"
import { useAgent } from "@/contexts/AgentContext"
import { useSpace } from "@/contexts/SpaceContext"
import { spaceApi } from "@/lib/api"
import { SpaceType } from "@/lib/schemas/space"
import { getAgentProductFromSearch } from "@/agents/lib/agentDashboardRoute"

type ProbeResult =
  | { status: "loading" }
  | { status: "not_found" }
  | { status: "error" }

function SpaceNotFoundGuard({
  spaceId,
  spaces,
  switchSpace,
}: {
  spaceId: string
  spaces: ReturnType<typeof useSpace>["spaces"]
  switchSpace: ReturnType<typeof useSpace>["switchSpace"]
}) {
  const { t } = useTranslation()
  const location = useLocation()
  const navigate = useNavigate()
  const [probe, setProbe] = useState<ProbeResult>({ status: "loading" })

  useEffect(() => {
    let cancelled = false
    spaceApi.probe(spaceId).then((result) => {
      if (cancelled) return
      if (result.status === "ok") {
        setProbe({ status: "error" })
      } else {
        setProbe(result)
      }
    })
    return () => {
      cancelled = true
    }
  }, [spaceId])

  if (probe.status === "loading") {
    return (
      <div className="flex min-h-full items-center justify-center">
        <div className="size-8 animate-spin rounded-full border-4 border-muted border-t-foreground" />
      </div>
    )
  }

  if (probe.status === "not_found") {
    return (
      <ErrorPage
        icon={<SearchX className="text-muted-foreground" />}
        title={t("error.spaceNotFoundTitle")}
        description={t("error.spaceNotFoundDescription")}
        actions={
          <SpaceSwitcher
            spaces={spaces}
            switchSpace={switchSpace}
            location={location}
            navigate={navigate}
          />
        }
      />
    )
  }

  return (
    <ErrorPage
      icon={<AlertTriangle className="text-destructive" />}
      title={t("error.spaceUnavailableTitle")}
      description={t("error.spaceUnavailableDescription")}
      actions={
        <SpaceSwitcher
          spaces={spaces}
          switchSpace={switchSpace}
          location={location}
          navigate={navigate}
        />
      }
    />
  )
}

function SpaceSwitcher({
  spaces,
  switchSpace,
  location,
  navigate,
  label,
}: {
  spaces: ReturnType<typeof useSpace>["spaces"]
  switchSpace: ReturnType<typeof useSpace>["switchSpace"]
  location: ReturnType<typeof useLocation>
  navigate: ReturnType<typeof useNavigate>
  label?: string
}) {
  const { t } = useTranslation()
  return (
    <div className="flex flex-col items-center gap-3">
      <span className="text-xs text-muted-foreground">
        {label ?? t("error.selectAvailableSpace")}
      </span>
      <div className="flex flex-wrap justify-center gap-2">
        {spaces.map((space) => (
          <Button
            key={space.id}
            variant="outline"
            size="sm"
            onClick={() => {
              switchSpace(space)
              const params = new URLSearchParams(location.search)
              params.delete("agent")
              if (space.type === SpaceType.Business) {
                params.set("spaceId", space.id)
              } else {
                params.delete("spaceId")
              }
              const search = params.toString()
              navigate(
                search ? `${location.pathname}?${search}` : location.pathname
              )
            }}
          >
            {space.name}
          </Button>
        ))}
      </div>
    </div>
  )
}

export function SpaceGuard({ children }: { children: ReactNode }) {
  const { spaces, loading: spaceLoading, switchSpace } = useSpace()
  const location = useLocation()
  const searchParams = new URLSearchParams(location.search)
  const spaceIdFromUrl = searchParams.get("spaceId")

  const spaceNotFound =
    spaceIdFromUrl !== null &&
    spaceIdFromUrl !== undefined &&
    !spaceLoading &&
    spaces.length > 0 &&
    !spaces.find((s) => s.id === spaceIdFromUrl)

  if (spaceNotFound) {
    return (
      <SpaceNotFoundGuard
        spaceId={spaceIdFromUrl}
        spaces={spaces}
        switchSpace={switchSpace}
      />
    )
  }

  return <>{children}</>
}

export function SpaceAgentGuard({ children }: { children: ReactNode }) {
  const { t } = useTranslation()
  const location = useLocation()
  const navigate = useNavigate()
  const { spaces, loading: spaceLoading, switchSpace } = useSpace()
  const { agents, loading: agentLoading } = useAgent()

  const searchParams = new URLSearchParams(location.search)
  const spaceIdFromUrl = searchParams.get("spaceId")
  const agentFromUrl = getAgentProductFromSearch(location.search)

  const spaceNotFound =
    spaceIdFromUrl !== null &&
    spaceIdFromUrl !== undefined &&
    !spaceLoading &&
    spaces.length > 0 &&
    !spaces.find((s) => s.id === spaceIdFromUrl)

  if (spaceNotFound) {
    return (
      <SpaceNotFoundGuard
        spaceId={spaceIdFromUrl}
        spaces={spaces}
        switchSpace={switchSpace}
      />
    )
  }

  const agentNotInSpace =
    agentFromUrl !== null &&
    agentFromUrl !== undefined &&
    !agentLoading &&
    !agents.find(
      (a) => a.agent_product === agentFromUrl || a.agent_name === agentFromUrl
    )

  if (agentNotInSpace) {
    return (
      <ErrorPage
        icon={<Info />}
        title={t("error.agentNotInSpaceTitle")}
        description={
          <Trans
            i18nKey="error.agentNotInSpaceDescription"
            values={{ agent: agentFromUrl }}
            components={{
              code: (
                <code className="rounded bg-muted px-1.5 py-0.5 font-mono text-xs" />
              ),
            }}
          />
        }
        actions={
          <Button
            variant="outline"
            size="sm"
            onClick={() => navigate("/access")}
          >
            {t("error.openAgentAccess")}
          </Button>
        }
      />
    )
  }

  return <>{children}</>
}
