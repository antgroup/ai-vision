import { enUS, zhCN } from "date-fns/locale"
import { ja } from "react-day-picker/locale"
import { normalizeLocale } from "./locales"

const calendarLocales = { "zh-CN": zhCN, "en-US": enUS, "ja-JP": ja }

export function getCalendarLocale(language: string) {
  return calendarLocales[normalizeLocale(language)]
}
