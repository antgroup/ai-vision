import { useRef } from "react"

import type { LangfuseObservation } from "@/agents/types/langfuse"
import { getObsTypeColor } from "../traces-lf/shared/utils"

const MINIMAP_HEIGHT = 24
const BAR_THICKNESS = 3

export function TimelineMinimap({
  observations,
  traceStartTime,
  totalDuration,
  viewStart,
  viewEnd,
  labelWidth,
  durationLabelWidth,
  onPointerDown,
  onPointerMove,
  onPointerUp,
}: {
  observations: LangfuseObservation[]
  traceStartTime: number
  totalDuration: number
  viewStart: number
  viewEnd: number
  labelWidth: number
  durationLabelWidth: number
  onPointerDown: (e: React.PointerEvent, rect: DOMRect) => void
  onPointerMove: (e: React.PointerEvent, rect: DOMRect) => void
  onPointerUp: () => void
}) {
  const stripRef = useRef<HTMLDivElement>(null)

  const overlayLeftPct =
    totalDuration > 0 ? (viewStart / totalDuration) * 100 : 0
  const overlayWidthPct =
    totalDuration > 0 ? ((viewEnd - viewStart) / totalDuration) * 100 : 100

  const getRect = () => stripRef.current?.getBoundingClientRect()

  return (
    <div
      className="sticky top-0 z-[15] flex shrink-0 border-b bg-muted/30"
      style={{
        height: MINIMAP_HEIGHT,
        paddingLeft: labelWidth,
        paddingRight: durationLabelWidth,
      }}
    >
      <div
        ref={stripRef}
        className="relative h-full flex-1"
        onPointerDown={(e) => {
          const r = getRect()
          if (r) onPointerDown(e, r)
        }}
        onPointerMove={(e) => {
          const r = getRect()
          if (r) onPointerMove(e, r)
        }}
        onPointerUp={onPointerUp}
      >
        {overlayLeftPct > 0.1 && (
          <div
            className="absolute inset-y-0 left-0 bg-foreground/5"
            style={{ width: `${overlayLeftPct}%` }}
          />
        )}
        {overlayLeftPct + overlayWidthPct < 99.9 && (
          <div
            className="absolute inset-y-0 right-0 bg-foreground/5"
            style={{ width: `${100 - overlayLeftPct - overlayWidthPct}%` }}
          />
        )}

        {observations.map((obs) => {
          const start = new Date(obs.startTime).getTime() - traceStartTime
          const end = obs.endTime
            ? new Date(obs.endTime).getTime() - traceStartTime
            : start
          const leftPct = totalDuration > 0 ? (start / totalDuration) * 100 : 0
          const widthPct =
            totalDuration > 0 ? ((end - start) / totalDuration) * 100 : 0
          return (
            <div
              key={obs.id}
              className="absolute rounded-[1px]"
              style={{
                left: `${leftPct}%`,
                width: `max(1px, ${widthPct}%)`,
                height: BAR_THICKNESS,
                top: "50%",
                transform: "translateY(-50%)",
                backgroundColor: getObsTypeColor(obs.type),
                opacity: 0.6,
              }}
            />
          )
        })}

        <div
          className="absolute inset-y-0 rounded-sm border border-blue-400/50 bg-blue-500/10"
          style={{
            left: `${overlayLeftPct}%`,
            width: `${Math.max(0.5, overlayWidthPct)}%`,
            cursor: "grab",
          }}
        >
          <div className="absolute inset-y-0 -left-0.5 w-1.5 cursor-col-resize" />
          <div className="absolute inset-y-0 -right-0.5 w-1.5 cursor-col-resize" />
        </div>
      </div>
    </div>
  )
}
