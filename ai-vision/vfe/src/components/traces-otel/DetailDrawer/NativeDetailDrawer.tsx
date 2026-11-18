import { useTranslation } from "react-i18next"
import { useState } from "react"

import { Sheet, SheetContent, SheetTitle } from "@/components/ui/sheet"
import type { TraceQueryTimeRange } from "@/agents/types/langfuse"

import { NativeTraceDetail } from "../NativeTraceDetail"
import { NativeSessionDetailPanel } from "../NativeSessionDetailPanel"
import type { NativeTraceSourceSystem } from "../api"

/**
 * Native (signoz) detail drawer.
 *
 * Mirrors the prop shape of the Langfuse `DetailDrawer` so callers can swap
 * implementations without changing wiring, but renders the OTel-native
 * components that hit the selected native-shaped `/signoz/` or `/signoz/`
 * endpoints instead of `/lf/`.
 *
 * `NativeTraceDetail` already wraps itself in a Sheet (open driven by
 * `traceId`); the session panel is wrapped here to match NativeTracePage.
 */
export type NativeDetailDrawerProps = {
  spaceId: string
  userUuid?: string
  agentProduct?: string
  agentUuid?: string | null
  demoCost?: boolean
  sourceSystem?: NativeTraceSourceSystem
  focusSpanId?: string | null
  traceId: string | null
  traceTimeRange?: TraceQueryTimeRange | null
  sessionId: string | null
  sessionSemantics?: "canonical-trace-v1"
  sessionTimeRange?: TraceQueryTimeRange | null
  focusTraceId?: string | null
  onClose: () => void
  onOpenTrace: (traceId: string) => void
  onOpenSession: (sessionId: string, focusTraceId?: string) => void
}

export function NativeDetailDrawer({
  spaceId,
  userUuid,
  agentProduct,
  agentUuid,
  demoCost,
  sourceSystem = "signoz",
  traceId,
  focusSpanId,
  traceTimeRange,
  sessionId,
  sessionSemantics,
  sessionTimeRange,
  focusTraceId,
  onClose,
  onOpenTrace,
  onOpenSession,
}: NativeDetailDrawerProps) {
  const { t } = useTranslation()
  const [isSessionFullscreen, setIsSessionFullscreen] = useState(false)

  return (
    <>
      {/* Trace detail — self-managed Sheet driven by traceId */}
      <NativeTraceDetail
        userUuid={userUuid}
        spaceId={spaceId}
        traceId={traceId}
        focusSpanId={focusSpanId}
        agentProduct={agentProduct}
        agentUuid={agentUuid}
        demoCost={demoCost}
        sourceSystem={sourceSystem}
        fromTimestamp={traceTimeRange?.from ?? undefined}
        toTimestamp={traceTimeRange?.to ?? undefined}
        onClose={onClose}
        onNavigateToSession={(sid, focusTid) => onOpenSession(sid, focusTid)}
      />

      {/* Session detail — wrapped in a Sheet, mirroring NativeTracePage */}
      <Sheet
        open={!traceId && !!sessionId}
        onOpenChange={(open) => {
          if (!open) {
            setIsSessionFullscreen(false)
            onClose()
          }
        }}
      >
        <SheetContent
          id="native-dashboard-session-detail-sheet"
          tabIndex={-1}
          side="right"
          showCloseButton={false}
          onOpenAutoFocus={(event) => {
            event.preventDefault()
            requestAnimationFrame(() => {
              document
                .getElementById("native-dashboard-session-detail-sheet")
                ?.focus()
            })
          }}
          className={
            isSessionFullscreen
              ? "!w-screen !max-w-none p-0 focus:outline-none"
              : "!w-[70vw] !max-w-[calc(100vw-48px)] !min-w-[min(900px,calc(100vw-48px))] p-0 transition-[width] duration-200 focus:outline-none"
          }
        >
          <SheetTitle className="sr-only">
            {t("traceLabels.sessionDetail")}
          </SheetTitle>
          {!traceId && sessionId && (
            <NativeSessionDetailPanel
              sessionId={sessionId}
              sessionSemantics={sessionSemantics}
              spaceId={spaceId}
              agentProduct={agentProduct}
              agentUuid={agentUuid}
              demoCost={demoCost}
              sourceSystem={sourceSystem}
              fromTimestamp={sessionTimeRange?.from ?? undefined}
              toTimestamp={sessionTimeRange?.to ?? undefined}
              isFullscreen={isSessionFullscreen}
              focusTraceId={focusTraceId}
              onToggleFullscreen={() => setIsSessionFullscreen((prev) => !prev)}
              onClose={() => {
                setIsSessionFullscreen(false)
                onClose()
              }}
              onNavigateToTrace={(tid) => {
                setIsSessionFullscreen(false)
                onOpenTrace(tid)
              }}
            />
          )}
        </SheetContent>
      </Sheet>
    </>
  )
}
