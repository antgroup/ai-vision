import { Component, type ReactNode } from "react"
import { useTranslation } from "react-i18next"
import { AlertTriangle } from "lucide-react"
import { Button } from "@/components/ui/button"

function DetailErrorFallback({ onRetry }: { onRetry: () => void }) {
  const { t } = useTranslation()
  return (
    <div
      role="alert"
      className="m-4 rounded-lg border border-border bg-muted/30 p-4"
    >
      <div className="flex items-start gap-3">
        <AlertTriangle
          className="mt-0.5 size-4 shrink-0 text-muted-foreground"
          aria-hidden="true"
        />
        <div className="min-w-0 space-y-2">
          <h3 className="text-sm font-medium">
            {t("traceLabels.detailRenderError")}
          </h3>
          <p className="text-sm leading-6 text-muted-foreground">
            {t("traceLabels.detailRenderErrorHint")}
          </p>
          <Button variant="outline" size="sm" onClick={onRetry}>
            {t("traceLabels.retryDetail")}
          </Button>
        </div>
      </div>
    </div>
  )
}

/** Isolate render failures without hiding trace navigation or exposing payloads. */
export class SpanDetailErrorBoundary extends Component<
  { children: ReactNode; resetKey?: string },
  { hasError: boolean; resetKey?: string }
> {
  state = { hasError: false, resetKey: undefined as string | undefined }

  static getDerivedStateFromProps(
    props: { resetKey?: string },
    state: { resetKey?: string }
  ) {
    return props.resetKey !== state.resetKey
      ? { hasError: false, resetKey: props.resetKey }
      : null
  }

  static getDerivedStateFromError() {
    return { hasError: true }
  }

  render() {
    if (this.state.hasError) {
      return (
        <DetailErrorFallback
          onRetry={() => this.setState({ hasError: false })}
        />
      )
    }
    return this.props.children
  }
}
