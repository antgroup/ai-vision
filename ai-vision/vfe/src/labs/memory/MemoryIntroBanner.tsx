import { useTranslation } from "react-i18next"
import { Database } from "lucide-react"

import { LabsIntroBanner } from "@/components/labs/shared/LabsIntroBanner"
import { observabilityViewDocsUrl } from "@/components/labs/shared/observabilityViewDocs"

const GLOSSARY_KEYS = ["server", "entry", "hit"] as const

export function MemoryIntroBanner() {
  const { t, i18n } = useTranslation()

  return (
    <LabsIntroBanner
      icon={Database}
      title={t("labs.memory.intro.title")}
      summary={t("labs.memory.intro.summary")}
      links={[
        {
          label: t("labs.memory.intro.linkGuide"),
          href: observabilityViewDocsUrl("memory", i18n.language),
        },
      ]}
    >
      <dl className="grid gap-x-6 gap-y-2 sm:grid-cols-2">
        {GLOSSARY_KEYS.map((key) => (
          <div key={key} className="flex min-w-0 gap-2 text-xs leading-5">
            <dt className="shrink-0 font-medium text-[var(--color-text-primary)]">
              {t(`labs.memory.glossary.${key}.term`)}
            </dt>
            <dd className="min-w-0 [overflow-wrap:anywhere] text-[var(--color-text-secondary)]">
              {t(`labs.memory.glossary.${key}.desc`)}
            </dd>
          </div>
        ))}
      </dl>
    </LabsIntroBanner>
  )
}
