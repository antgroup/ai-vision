export const languageOptions = [
  { locale: "zh-CN", labelKey: "language.zh" },
  { locale: "en-US", labelKey: "language.en" },
  { locale: "ja-JP", labelKey: "language.ja" },
] as const

export type SupportedLocale = (typeof languageOptions)[number]["locale"]

export function normalizeLocale(value: unknown): SupportedLocale {
  return (
    languageOptions.find((option) => option.locale === value)?.locale ?? "zh-CN"
  )
}

/** Explicit product preference wins; a first visit follows the browser's OS languages. */
export function preferredLocale(
  saved: unknown,
  languages: readonly string[]
): SupportedLocale {
  if (languageOptions.some((option) => option.locale === saved))
    return saved as SupportedLocale
  for (const language of languages) {
    const base = language.toLowerCase().split(/[-_]/)[0]
    const match = languageOptions.find((option) =>
      option.locale.toLowerCase().startsWith(base + "-")
    )
    if (match) return match.locale
  }
  return "zh-CN"
}
