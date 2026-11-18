import { useTranslation } from "react-i18next"
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs"
import type { FrameworkLanguage } from "./frameworkGuides"

export function FrameworkLanguageSelector({
  value,
  onChange,
  languages,
}: {
  value: FrameworkLanguage
  onChange: (language: FrameworkLanguage) => void
  languages: FrameworkLanguage[]
}) {
  const { t } = useTranslation()
  if (languages.length < 2) return null
  return (
    <div className="mt-4 space-y-2">
      <Tabs
        value={value}
        onValueChange={(next) => {
          const language = languages.find((item) => item === next)
          if (language) onChange(language)
        }}
      >
        <TabsList
          className="h-auto flex-wrap"
          aria-label={t("frameworkOnboarding.language")}
        >
          {languages.map((language) => (
            <TabsTrigger key={language} value={language}>
              {
                {
                  python: "Python",
                  typescript: "JavaScript / TypeScript",
                  go: "Go",
                }[language]
              }
            </TabsTrigger>
          ))}
        </TabsList>
      </Tabs>
      {value === "typescript" && (
        <p className="text-sm leading-6 text-muted-foreground">
          {t("frameworkOnboarding.nodeOnly")}
        </p>
      )}
    </div>
  )
}
