import { useTranslation } from "react-i18next"
import { useLocation, useSearchParams } from "react-router-dom"
import { SpaceGuard } from "@/components/error/SpaceAgentGuard"
import { useSpace } from "@/contexts/SpaceContext"
import ModelConnectionsSection from "@/features/model-connections/ModelConnectionsSection"
import { useModelConnectionPermissions } from "@/features/model-connections/useModelConnectionPermissions"
import { getModelConnectionListPosition } from "@/features/model-connections/connectionPresentation"

function Inventory({ spaceId }: { spaceId: string }) {
  const { t } = useTranslation()
  const [params, setParams] = useSearchParams()
  const location = useLocation()
  const { canManage } = useModelConnectionPermissions(spaceId)
  return (
    <div className="mx-auto w-full max-w-[100rem] min-w-0 px-4 py-7 sm:px-8 sm:py-8">
      <header className="mb-7">
        <h1 className="text-2xl font-semibold tracking-tight">
          {t("modelConnections.title")}
        </h1>
      </header>
      <ModelConnectionsSection
        standalone
        spaceId={spaceId}
        canManage={canManage}
        initialSearch={params.get("q") ?? ""}
        initialStatus={params.get("status") ?? "all"}
        detailSearch={params.toString()}
        restoreScrollTop={getModelConnectionListPosition(
          location.state,
          spaceId
        )}
        onFilterChange={(search, status) =>
          setParams(
            (current) => {
              const next = new URLSearchParams(current)
              if (search) next.set("q", search)
              else next.delete("q")
              if (status !== "all") next.set("status", status)
              else next.delete("status")
              return next
            },
            { replace: true }
          )
        }
      />
    </div>
  )
}

export default function ModelConnectionsPage() {
  const { currentSpace } = useSpace()
  return (
    <SpaceGuard>
      {currentSpace ? (
        <Inventory key={currentSpace.id} spaceId={currentSpace.id} />
      ) : null}
    </SpaceGuard>
  )
}
