import { useCallback, useEffect, useRef, useState } from "react"

const MAX_ZOOM_RATIO = 500
const ZOOM_SCALE_FACTOR = 1.15
const DRAG_THRESHOLD_PX = 3
const MIN_TICK_SPACING_PX = 80

const NICE_INTERVALS_MS = [
  1, 2, 5, 10, 20, 50,
  100, 200, 500,
  1_000, 2_000, 5_000, 10_000, 15_000, 30_000,
  60_000, 120_000, 300_000, 600_000, 1_800_000, 3_600_000,
]

function clamp(value: number, min: number, max: number): number {
  return Math.max(min, Math.min(max, value))
}

export interface TickMark {
  timeMs: number
  label: string
  frac: number
}

function formatTickLabel(ms: number): string {
  if (ms <= 0) return "0ms"
  if (ms < 1000) return `${Math.round(ms)}ms`
  if (ms < 60_000) {
    const s = ms / 1000
    return s % 1 === 0 ? `${s}s` : `${s.toFixed(1)}s`
  }
  const totalSec = Math.round(ms / 1000)
  const min = Math.floor(totalSec / 60)
  const sec = totalSec % 60
  if (sec === 0) return `${min}m`
  return `${min}m ${sec}s`
}

export function computeTicks(
  viewStart: number,
  viewEnd: number,
  containerWidthPx: number
): TickMark[] {
  const viewDuration = viewEnd - viewStart
  if (viewDuration <= 0 || containerWidthPx <= 0) return []

  const maxTicks = Math.max(2, Math.floor(containerWidthPx / MIN_TICK_SPACING_PX))
  const idealInterval = viewDuration / maxTicks

  let interval = NICE_INTERVALS_MS[NICE_INTERVALS_MS.length - 1]
  for (const nice of NICE_INTERVALS_MS) {
    if (nice >= idealInterval) {
      interval = nice
      break
    }
  }

  const firstTick = Math.ceil(viewStart / interval) * interval
  const ticks: TickMark[] = []
  for (let t = firstTick; t <= viewEnd + 0.001; t += interval) {
    ticks.push({
      timeMs: t,
      label: formatTickLabel(t),
      frac: (t - viewStart) / viewDuration,
    })
  }

  if (viewStart <= 0.001 && !ticks.some((t) => Math.abs(t.timeMs) < 0.001)) {
    ticks.unshift({ timeMs: 0, label: "0ms", frac: -viewStart / viewDuration })
  }

  return ticks
}

type DragMode =
  | "none"
  | "pan"
  | "select"
  | "minimap-pan"
  | "minimap-resize-left"
  | "minimap-resize-right"

interface DragState {
  mode: DragMode
  startX: number
  startViewStart: number
  startViewEnd: number
  currentX: number
  thresholdMet: boolean
}

const DRAG_INIT: DragState = {
  mode: "none",
  startX: 0,
  startViewStart: 0,
  startViewEnd: 0,
  currentX: 0,
  thresholdMet: false,
}

export interface UseTimelineZoomReturn {
  viewStart: number
  viewEnd: number
  viewDuration: number
  isZoomed: boolean
  selectionPx: { startX: number; endX: number } | null
  dragMode: DragMode
  resetZoom: () => void
  zoomToRange: (start: number, end: number) => void
  attachWheelListener: (
    containerEl: HTMLElement | null,
    ganttEl: HTMLElement | null
  ) => void
  handlePointerDown: (e: React.PointerEvent, ganttRect: DOMRect) => void
  handlePointerMove: (e: React.PointerEvent, ganttRect: DOMRect) => void
  handlePointerUp: (e: React.PointerEvent, ganttRect: DOMRect) => void
  handleDoubleClick: (e: React.MouseEvent, ganttRect: DOMRect) => void
  handleMinimapPointerDown: (
    e: React.PointerEvent,
    minimapRect: DOMRect
  ) => void
  handleMinimapPointerMove: (
    e: React.PointerEvent,
    minimapRect: DOMRect
  ) => void
  handleMinimapPointerUp: () => void
}

export function useTimelineZoom(totalDuration: number): UseTimelineZoomReturn {
  const [viewStart, setViewStart] = useState(0)
  const [viewEnd, setViewEnd] = useState(totalDuration || 1)
  const [drag, setDrag] = useState<DragState>(DRAG_INIT)

  const totalRef = useRef(totalDuration)
  const viewRef = useRef({ viewStart, viewEnd })

  useEffect(() => {
    viewRef.current = { viewStart, viewEnd }
  }, [viewStart, viewEnd])

  useEffect(() => {
    if (totalDuration !== totalRef.current) {
      totalRef.current = totalDuration
      setViewStart(0)
      setViewEnd(totalDuration || 1)
      setDrag(DRAG_INIT)
    }
  }, [totalDuration])

  const viewDuration = viewEnd - viewStart
  const isZoomed =
    viewStart > 0.01 || viewEnd < totalDuration - 0.01

  const setView = useCallback((start: number, end: number) => {
    const td = totalRef.current
    const minVisible = Math.max(1, td / MAX_ZOOM_RATIO)
    let dur = end - start
    if (dur < minVisible) dur = minVisible
    if (dur > td) dur = td
    let s = start
    let e = s + dur
    if (s < 0) {
      e -= s
      s = 0
    }
    if (e > td) {
      s -= e - td
      e = td
    }
    s = Math.max(0, s)
    e = Math.min(td, e)
    setViewStart(s)
    setViewEnd(e)
  }, [])

  const resetZoom = useCallback(() => {
    setViewStart(0)
    setViewEnd(totalRef.current || 1)
  }, [])

  const zoomToRange = useCallback(
    (start: number, end: number) => {
      if (end <= start) return
      setView(start, end)
    },
    [setView]
  )

  const pxToTime = useCallback((clientX: number, rect: DOMRect): number => {
    const frac = clamp((clientX - rect.left) / rect.width, 0, 1)
    return (
      viewRef.current.viewStart +
      frac * (viewRef.current.viewEnd - viewRef.current.viewStart)
    )
  }, [])

  const wheelCleanupRef = useRef<(() => void) | null>(null)

  const attachWheelListener = useCallback(
    (containerEl: HTMLElement | null, ganttEl: HTMLElement | null) => {
      wheelCleanupRef.current?.()
      wheelCleanupRef.current = null
      if (!containerEl || !ganttEl) return

      const handler = (e: WheelEvent) => {
        if (totalRef.current <= 0) return
        const ganttRect = ganttEl.getBoundingClientRect()
        if (e.clientX < ganttRect.left || e.clientX > ganttRect.right) return

        e.preventDefault()
        const { viewStart: vs, viewEnd: ve } = viewRef.current
        const vd = ve - vs
        const frac = clamp(
          (e.clientX - ganttRect.left) / ganttRect.width,
          0,
          1
        )
        const cursorTime = vs + frac * vd

        const scale = e.deltaY > 0 ? ZOOM_SCALE_FACTOR : 1 / ZOOM_SCALE_FACTOR
        const newDur = clamp(
          vd * scale,
          Math.max(1, totalRef.current / MAX_ZOOM_RATIO),
          totalRef.current
        )

        const newStart = cursorTime - frac * newDur
        setView(newStart, newStart + newDur)
      }

      containerEl.addEventListener("wheel", handler, { passive: false })
      wheelCleanupRef.current = () =>
        containerEl.removeEventListener("wheel", handler)
    },
    [setView]
  )

  useEffect(() => {
    return () => {
      wheelCleanupRef.current?.()
    }
  }, [])

  const handlePointerDown = useCallback(
    (e: React.PointerEvent, ganttRect: DOMRect) => {
      if (e.button !== 0) return
      const inGantt =
        e.clientX >= ganttRect.left && e.clientX <= ganttRect.right
      if (!inGantt) return

      const { viewStart: vs, viewEnd: ve } = viewRef.current
      const mode: DragMode = e.shiftKey
        ? "select"
        : vs > 0.01 || ve < totalRef.current - 0.01
          ? "pan"
          : "select"

      setDrag({
        mode,
        startX: e.clientX,
        startViewStart: vs,
        startViewEnd: ve,
        currentX: e.clientX,
        thresholdMet: false,
      })

      ;(e.target as HTMLElement).setPointerCapture?.(e.pointerId)
    },
    []
  )

  const handlePointerMove = useCallback(
    (e: React.PointerEvent, ganttRect: DOMRect) => {
      setDrag((prev) => {
        if (prev.mode === "none") return prev
        const dx = e.clientX - prev.startX
        const thresholdMet =
          prev.thresholdMet || Math.abs(dx) >= DRAG_THRESHOLD_PX
        if (!thresholdMet) return { ...prev, currentX: e.clientX }

        if (prev.mode === "pan") {
          const pxPerMs =
            ganttRect.width / (prev.startViewEnd - prev.startViewStart)
          const timeDelta = dx / pxPerMs
          setView(
            prev.startViewStart - timeDelta,
            prev.startViewEnd - timeDelta
          )
        }

        return { ...prev, currentX: e.clientX, thresholdMet: true }
      })
    },
    [setView]
  )

  const handlePointerUp = useCallback(
    (e: React.PointerEvent, ganttRect: DOMRect) => {
      setDrag((prev) => {
        if (prev.mode === "none") return DRAG_INIT
        if (!prev.thresholdMet) return DRAG_INIT

        if (prev.mode === "select") {
          const t1 = pxToTime(prev.startX, ganttRect)
          const t2 = pxToTime(e.clientX, ganttRect)
          const lo = Math.min(t1, t2)
          const hi = Math.max(t1, t2)
          if (hi - lo > 0.5) {
            setView(lo, hi)
          }
        }
        return DRAG_INIT
      })
    },
    [pxToTime, setView]
  )

  const handleDoubleClick = useCallback(() => {
    resetZoom()
  }, [resetZoom])

  const handleMinimapPointerDown = useCallback(
    (e: React.PointerEvent, minimapRect: DOMRect) => {
      if (e.button !== 0) return
      const td = totalRef.current
      const { viewStart: vs, viewEnd: ve } = viewRef.current
      const frac = clamp(
        (e.clientX - minimapRect.left) / minimapRect.width,
        0,
        1
      )
      const clickTime = frac * td

      const edgeThreshold = td * 0.015
      let mode: DragMode

      if (Math.abs(clickTime - vs) < edgeThreshold) {
        mode = "minimap-resize-left"
      } else if (Math.abs(clickTime - ve) < edgeThreshold) {
        mode = "minimap-resize-right"
      } else if (clickTime >= vs && clickTime <= ve) {
        mode = "minimap-pan"
      } else {
        const halfDur = (ve - vs) / 2
        setView(clickTime - halfDur, clickTime + halfDur)
        mode = "minimap-pan"
      }

      setDrag({
        mode,
        startX: e.clientX,
        startViewStart: viewRef.current.viewStart,
        startViewEnd: viewRef.current.viewEnd,
        currentX: e.clientX,
        thresholdMet: true,
      })

      ;(e.target as HTMLElement).setPointerCapture?.(e.pointerId)
    },
    [setView]
  )

  const handleMinimapPointerMove = useCallback(
    (e: React.PointerEvent, minimapRect: DOMRect) => {
      setDrag((prev) => {
        if (prev.mode === "none") return prev
        const td = totalRef.current
        const dx = e.clientX - prev.startX
        const timeDelta = (dx / minimapRect.width) * td

        if (prev.mode === "minimap-pan") {
          setView(
            prev.startViewStart + timeDelta,
            prev.startViewEnd + timeDelta
          )
        } else if (prev.mode === "minimap-resize-left") {
          const newStart = clamp(
            prev.startViewStart + timeDelta,
            0,
            prev.startViewEnd - 1
          )
          setView(newStart, prev.startViewEnd)
        } else if (prev.mode === "minimap-resize-right") {
          const newEnd = clamp(
            prev.startViewEnd + timeDelta,
            prev.startViewStart + 1,
            td
          )
          setView(prev.startViewStart, newEnd)
        }

        return { ...prev, currentX: e.clientX }
      })
    },
    [setView]
  )

  const handleMinimapPointerUp = useCallback(() => {
    setDrag(DRAG_INIT)
  }, [])

  const selectionPx =
    drag.mode === "select" && drag.thresholdMet
      ? {
          startX: Math.min(drag.startX, drag.currentX),
          endX: Math.max(drag.startX, drag.currentX),
        }
      : null

  return {
    viewStart,
    viewEnd,
    viewDuration,
    isZoomed,
    selectionPx,
    dragMode: drag.mode,
    resetZoom,
    zoomToRange,
    attachWheelListener,
    handlePointerDown,
    handlePointerMove,
    handlePointerUp,
    handleDoubleClick,
    handleMinimapPointerDown,
    handleMinimapPointerMove,
    handleMinimapPointerUp,
  }
}
