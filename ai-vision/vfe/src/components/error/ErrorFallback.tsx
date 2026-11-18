import { useTranslation } from "react-i18next"
import { isRouteErrorResponse, useRouteError } from "react-router-dom"

import { Button } from "@/components/ui/button"

/**
 * Chrome / Edge 的页面翻译会包裹、替换 React 管理的文本节点，使后续
 * removeChild / insertBefore 抛 NotFoundError。入口 HTML 已通过
 * translate="no" / google notranslate opt out；这里兜住手动翻译、
 * "始终翻译此站点"和翻译扩展等绕过 opt-out 的场景。
 */
function isBrowserTranslationError(error: unknown): boolean {
  if (!(error instanceof Error)) return false
  if (error.name !== "NotFoundError" && error.name !== "InvalidStateError") {
    return false
  }
  return /removeChild|insertBefore|not a child of this node/i.test(
    error.message
  )
}

export function ErrorFallback() {
  const { t } = useTranslation()
  const error = useRouteError()
  const translationConflict = isBrowserTranslationError(error)
  const message = isRouteErrorResponse(error)
    ? error.statusText
    : error instanceof Error
      ? error.message
      : t("error.pageLoadFailed")

  console.error("Route error:", error)

  return (
    <div className="flex min-h-screen items-center justify-center bg-background p-6">
      <div className="w-full max-w-md rounded-xl border bg-card p-6 text-center text-card-foreground">
        <h1 className="text-lg font-semibold">
          {translationConflict
            ? t("error.browserTranslationTitle")
            : t("error.pageLoadFailed")}
        </h1>
        <p className="mt-2 text-sm text-muted-foreground">
          {translationConflict ? t("error.browserTranslationHint") : message}
        </p>
        {translationConflict && (
          <Button
            type="button"
            variant="outline"
            className="mt-4"
            onClick={() => window.location.reload()}
          >
            {t("error.reload")}
          </Button>
        )}
      </div>
    </div>
  )
}
