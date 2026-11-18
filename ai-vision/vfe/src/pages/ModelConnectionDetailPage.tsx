import { useParams } from "react-router-dom"
import { SpaceGuard } from "@/components/error/SpaceAgentGuard"
import { useSpace } from "@/contexts/SpaceContext"
import { ModelConnectionDetail } from "@/features/model-connections/ModelConnectionDetail"

export default function ModelConnectionDetailPage() {
  const { currentSpace } = useSpace()
  const { connectionUuid } = useParams<{ connectionUuid: string }>()
  return (
    <SpaceGuard>
      {currentSpace && connectionUuid ? (
        <ModelConnectionDetail
          key={`${currentSpace.id}:${connectionUuid}`}
          spaceId={currentSpace.id}
          connectionUuid={connectionUuid}
        />
      ) : null}
    </SpaceGuard>
  )
}
