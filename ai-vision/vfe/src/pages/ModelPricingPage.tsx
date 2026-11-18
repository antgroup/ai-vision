import { SpaceGuard } from "@/components/error/SpaceAgentGuard"
import { useSpace } from "@/contexts/SpaceContext"
import ModelPricingContent from "@/features/model-pricing/ModelPricingContent"

export default function ModelPricingPage() {
  const { currentSpace } = useSpace()
  return (
    <SpaceGuard>
      {currentSpace ? (
        <ModelPricingContent key={currentSpace.id} spaceId={currentSpace.id} />
      ) : null}
    </SpaceGuard>
  )
}
