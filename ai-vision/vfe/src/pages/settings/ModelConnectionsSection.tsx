import type { ComponentProps } from "react"
import SharedSection, {
  ModelConnectionManagerPanel as SharedPanel,
  ModelConnectionManagerDialog as SharedDialog,
} from "@/features/model-connections/ModelConnectionsSection"

export default function ModelConnectionsSection(
  props: ComponentProps<typeof SharedSection>
) {
  return <SharedSection {...props} />
}

export function ModelConnectionManagerPanel(
  props: ComponentProps<typeof SharedPanel>
) {
  return <SharedPanel {...props} />
}

export function ModelConnectionManagerDialog(
  props: ComponentProps<typeof SharedDialog>
) {
  return <SharedDialog {...props} />
}
