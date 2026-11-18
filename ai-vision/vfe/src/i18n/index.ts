import i18n from "i18next"
import { initReactI18next } from "react-i18next"

import zhCN from "./locales/zh-CN.json"
import enUS from "./locales/en-US.json"
import jaJP from "./locales/ja-JP.json"
import {
  normalizeLocale,
  preferredLocale,
  type SupportedLocale,
} from "./locales"

export { languageOptions } from "./locales"
export type { SupportedLocale } from "./locales"

const resources = {
  "zh-CN": { translation: zhCN },
  "en-US": { translation: enUS },
  "ja-JP": { translation: jaJP },
}

function readSavedLanguage(): SupportedLocale {
  const languages = typeof navigator === "undefined" ? [] : navigator.languages
  try {
    return preferredLocale(localStorage.getItem("language"), languages)
  } catch {
    return preferredLocale(null, languages)
  }
}

i18n.on("languageChanged", (language) => {
  document.documentElement.lang = normalizeLocale(language)
})

i18n.use(initReactI18next).init({
  resources,
  lng: readSavedLanguage(),
  supportedLngs: ["zh-CN", "en-US", "ja-JP"],
  fallbackLng: {
    "ja-JP": ["en-US", "zh-CN"],
    default: ["zh-CN"],
  },
  interpolation: {
    escapeValue: false,
  },
})

export default i18n

export function getCurrentLocale(): SupportedLocale {
  return normalizeLocale(i18n.language)
}

export async function changeLanguage(language: SupportedLocale): Promise<void> {
  await i18n.changeLanguage(language)
  try {
    localStorage.setItem("language", language)
  } catch {
    // The current session can still switch language when storage is unavailable.
  }
}

export function isChineseLocale(): boolean {
  return getCurrentLocale().startsWith("zh")
}
