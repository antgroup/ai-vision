/** Documentation languages differ from the application's regional locale IDs. */
export function documentationUrl(page: string, language?: string): string {
  const locale = language?.trim().toLowerCase().split(/[-_]/)[0]
  const docsLanguage = locale === "zh" ? "zh-CN" : locale === "ja" ? "ja" : "en"
  return `https://aivision.mintlify.site/${docsLanguage}/${page}`
}
