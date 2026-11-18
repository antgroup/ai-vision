import { useTranslation } from "react-i18next"
import { Loader2 } from "lucide-react"

import { AppLogoMark } from "./AppLogo"

type AuthStatusScreenProps =
  | { status: "loading"; message: string }
  | { status: "recovering"; message: string }
  | {
      status: "error"
      message: string
      onRetry: () => void
      retryLabel?: string
    }

export function AuthStatusScreen(props: AuthStatusScreenProps) {
  const { t } = useTranslation()
  if (props.status === "recovering") {
    return (
      <div className="flex min-h-screen items-center justify-center bg-background">
        <div className="flex flex-col items-center gap-6">
          <div className="relative size-44">
            <div
              className="absolute inset-0 animate-[spin_2.4s_linear_infinite] rounded-full"
              style={{
                background:
                  "conic-gradient(from 0deg, transparent 0%, rgba(255,180,200,0.9) 30%, rgba(167,139,250,0.95) 60%, rgba(124,58,237,0.95) 80%, transparent 100%)",
                WebkitMask:
                  "radial-gradient(closest-side, transparent calc(100% - 6px), #000 calc(100% - 5px))",
                mask: "radial-gradient(closest-side, transparent calc(100% - 6px), #000 calc(100% - 5px))",
              }}
            />
            <div className="absolute inset-[6px] flex items-center justify-center rounded-full bg-background">
              <AppLogoMark className="inline-flex items-center gap-1.5" />
            </div>
          </div>
          <p className="text-sm font-medium text-foreground" role="status">
            {props.message}
          </p>
        </div>
      </div>
    )
  }

  if (props.status === "loading") {
    return (
      <div className="flex min-h-screen items-center justify-center bg-background">
        <div className="flex flex-col items-center gap-3">
          <Loader2 className="size-8 animate-spin text-foreground" />
          <p className="text-sm text-muted-foreground">{props.message}</p>
        </div>
      </div>
    )
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-background">
      <div className="flex flex-col items-center gap-3 rounded-xl border border-destructive/30 bg-card px-8 py-6 text-center">
        <p className="text-sm text-destructive">{props.message}</p>
        <button
          type="button"
          className="text-sm text-muted-foreground underline hover:text-foreground"
          onClick={props.onRetry}
        >
          {props.retryLabel ?? t("common.retry")}
        </button>
      </div>
    </div>
  )
}
