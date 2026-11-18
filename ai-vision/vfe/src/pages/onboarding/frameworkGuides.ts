export type FrameworkLanguage = "python" | "typescript" | "go"

export type FrameworkGuide = {
  id: string
  label: string
  frameworkId?: string
  language?: FrameworkLanguage
  runtime?: string
  exampleFilename?: string
  runCommand?: string
  requiresModel?: boolean
  logo?: string
  logoLayout?: "wide"
  logoInvertInDark?: boolean
  frontendSort?: number
  install?: string
  code?: string
  pipInstall?: string | null
  npmInstall?: string | null
  pipExample?: string | null
  npmExample?: string | null
  existingExample?: string
  bootstrapExample?: string
  integrationNotes?: string | string[]
  officialDocs?: string
  envShell?: string[] | null
  envDotenv?: string[] | null
}

export function guideLanguage(guide: FrameworkGuide): FrameworkLanguage {
  return guide.language ?? (guide.pipInstall ? "python" : "typescript")
}

export function availableLanguages(
  guides: FrameworkGuide[]
): FrameworkLanguage[] {
  return (["python", "typescript", "go"] as const).filter((language) =>
    guides.some((guide) => guideLanguage(guide) === language)
  )
}

export function guideForLanguage(
  guides: FrameworkGuide[],
  active: FrameworkGuide | null | undefined,
  language: FrameworkLanguage
): FrameworkGuide | undefined {
  const available = guides.filter((guide) => guideLanguage(guide) === language)
  const family = active?.frameworkId ?? active?.id
  return (
    available.find((guide) => (guide.frameworkId ?? guide.id) === family) ??
    available[0]
  )
}

export function resolveProjectFramework(
  guides: FrameworkGuide[],
  saved: unknown,
  selectDefault = false
): FrameworkGuide | undefined {
  return (
    guides.find((guide) => guide.id === saved) ??
    guides.find((guide) => guide.label === saved) ??
    (selectDefault
      ? (guideForLanguage(guides, undefined, "python") ?? guides[0])
      : undefined)
  )
}
