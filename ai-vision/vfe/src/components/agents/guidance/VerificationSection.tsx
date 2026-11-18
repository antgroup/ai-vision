import { CheckCircle2 } from "lucide-react"
import { useTranslation } from "react-i18next"
import type { VerificationSection as VerificationSectionType } from "./types"

interface Props {
  section: VerificationSectionType
}

export function VerificationSection({ section }: Props) {
  const { t } = useTranslation()
  return (
    <div className="space-y-2">
      <h4 className="flex items-center gap-1.5 text-sm font-medium text-foreground">
        <CheckCircle2 className="size-4 text-emerald-500" />
        {section.title || t("agentAccess.guide.verificationFallbackTitle")}
      </h4>
      <ol className="list-decimal space-y-1 pl-5 text-sm text-muted-foreground">
        {section.steps.map((step, index) => (
          <li key={index}>{step}</li>
        ))}
      </ol>
    </div>
  )
}
