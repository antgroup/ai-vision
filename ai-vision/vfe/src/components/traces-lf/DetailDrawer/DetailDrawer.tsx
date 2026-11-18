import { useTranslation } from "react-i18next"
import { useState } from "react"

import { Sheet, SheetContent, SheetTitle } from "@/components/ui/sheet"
import { TraceDetailPanel } from "../TraceDetailPanel"
import { SessionDetailPanel } from "../SessionDetailPanel"
import type { TraceDetailPanelProps } from "../TraceDetailPanel"
import type { SessionDetailPanelProps } from "../SessionDetailPanel"
import type { TraceQueryTimeRange } from "@/agents/types/langfuse"

type DetailDrawerSharedProps = Pick<
  TraceDetailPanelProps,
  | "spaceId"
  | "agentProduct"
  | "agentUuid"
  | "showLangfuseLink"
  | "langfuseProjectUrl"
> &
  Partial<Pick<TraceDetailPanelProps, "desensitized">>

export type DetailDrawerProps = DetailDrawerSharedProps & {
  traceId: string | null
  traceTimeRange?: TraceQueryTimeRange | null
  sessionId: string | null
  sessionTimeRange?: TraceQueryTimeRange | null
  focusTraceId?: SessionDetailPanelProps["focusTraceId"]
  onClose: () => void
  onOpenTrace: NonNullable<SessionDetailPanelProps["onNavigateToTrace"]>
  onOpenSession: NonNullable<TraceDetailPanelProps["onNavigateToSession"]>
}

export function DetailDrawer({
  spaceId,
  agentProduct,
  agentUuid,
  desensitized = false,
  showLangfuseLink,
  langfuseProjectUrl,
  traceId,
  traceTimeRange,
  sessionId,
  sessionTimeRange,
  focusTraceId,
  onClose,
  onOpenTrace,
  onOpenSession,
}: DetailDrawerProps) {
  const { t } = useTranslation()
  const [isFullscreen, setIsFullscreen] = useState(false)

  const open = !!traceId || !!sessionId

  return (
    <Sheet
      open={open}
      onOpenChange={(next) => {
        if (!next) {
          setIsFullscreen(false)
          onClose()
        }
      }}
    >
      <SheetContent
        side="right"
        showCloseButton={false}
        className={
          isFullscreen
            ? "!w-screen !max-w-none p-0"
            : sessionId
              ? "!w-[70vw] !max-w-[calc(100vw-48px)] !min-w-[900px] p-0 transition-[width] duration-200"
              : "!w-fit !max-w-[calc(100vw-48px)] !min-w-[900px] p-0 transition-[width] duration-200"
        }
      >
        <SheetTitle className="sr-only">
          {traceId
            ? t("traceLabels.traceDetail")
            : t("traceLabels.sessionDetail")}
        </SheetTitle>
        {traceId && (
          <TraceDetailPanel
            traceId={traceId}
            traceTimeRange={traceTimeRange}
            spaceId={spaceId}
            agentProduct={agentProduct}
            agentUuid={agentUuid}
            desensitized={desensitized}
            isFullscreen={isFullscreen}
            showLangfuseLink={showLangfuseLink}
            langfuseProjectUrl={langfuseProjectUrl}
            onToggleFullscreen={() => setIsFullscreen((prev) => !prev)}
            onClose={() => {
              setIsFullscreen(false)
              onClose()
            }}
            onNavigateToSession={(sid, focusTid) => {
              setIsFullscreen(false)
              onOpenSession(sid, focusTid)
            }}
          />
        )}
        {!traceId && sessionId && (
          <SessionDetailPanel
            sessionId={sessionId}
            spaceId={spaceId}
            agentProduct={agentProduct}
            agentUuid={agentUuid}
            sessionTimeRange={sessionTimeRange}
            desensitized={desensitized}
            isFullscreen={isFullscreen}
            focusTraceId={focusTraceId}
            showLangfuseLink={showLangfuseLink}
            langfuseProjectUrl={langfuseProjectUrl}
            onToggleFullscreen={() => setIsFullscreen((prev) => !prev)}
            onClose={() => {
              setIsFullscreen(false)
              onClose()
            }}
            onNavigateToTrace={(tid) => {
              setIsFullscreen(false)
              onOpenTrace(tid)
            }}
          />
        )}
      </SheetContent>
    </Sheet>
  )
}
