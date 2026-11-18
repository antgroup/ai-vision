import { CircleAlert } from "lucide-react"
import { useTranslation } from "react-i18next"
import type { PrerequisitesSection as PrerequisitesSectionType } from "./types"

interface Props {
  section: PrerequisitesSectionType
}

export function PrerequisitesSection({ section }: Props) {
  const { t } = useTranslation()
  return (
    <div className="space-y-2">
      <h4 className="flex items-center gap-1.5 text-sm font-medium text-foreground">
        <CircleAlert className="size-4 text-amber-500 dark:text-amber-400" />
        {section.title || t("agentAccess.guide.prerequisitesTitle")}
      </h4>
      <ul className="space-y-1.5 pl-6">
        {section.items.map((item) => (
          <li key={item.label} className="flex items-baseline gap-2 text-sm">
            <span className="shrink-0 font-normal text-foreground">
              {item.label}
            </span>
            <span className="text-muted-foreground">{item.value}</span>
          </li>
        ))}
      </ul>
    </div>
  )
}
