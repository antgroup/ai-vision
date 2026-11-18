import type { GuideSection } from "./types"
import { PrerequisitesSection } from "./PrerequisitesSection"
import { SetupStepsSection } from "./SetupStepsSection"
import { VerificationSection } from "./VerificationSection"

interface Props {
  section: GuideSection
}

export function SectionRenderer({ section }: Props) {
  switch (section.type) {
    case "prerequisites":
      return <PrerequisitesSection section={section} />
    case "setup_steps":
      return <SetupStepsSection section={section} />
    case "verification":
      return <VerificationSection section={section} />
    default:
      return null
  }
}
