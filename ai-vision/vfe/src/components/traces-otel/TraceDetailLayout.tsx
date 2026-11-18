import {
  useId,
  useLayoutEffect,
  useRef,
  useState,
  useSyncExternalStore,
  type ReactNode,
} from "react"
import { useTranslation } from "react-i18next"
import { type GroupImperativeHandle, type Layout } from "react-resizable-panels"
import {
  ResizableHandle,
  ResizablePanel,
  ResizablePanelGroup,
} from "@/components/ui/resizable"
import { cn } from "@/lib/utils"

type ViewMode = "tree" | "graph" | "timeline"
const DEFAULT_LEFT: Record<ViewMode, number> = {
  tree: 35,
  graph: 60,
  timeline: 55,
}
const storageKey = (view: ViewMode) => `aivision.trace.split.v1.${view}`
const narrowQuery = "(max-width: 1023px)"

function subscribe(onChange: () => void) {
  const media = window.matchMedia(narrowQuery)
  media.addEventListener("change", onChange)
  return () => media.removeEventListener("change", onChange)
}

function readLayout(
  view: ViewMode,
  navigationId: string,
  detailId: string
): Layout {
  let left = DEFAULT_LEFT[view]
  try {
    const saved = localStorage.getItem(storageKey(view))
    const value = saved?.trim() ? Number(saved) : NaN
    if (Number.isFinite(value) && value > 0 && value < 100) left = value
  } catch {
    // Layout remains usable when browser storage is unavailable.
  }
  return { [navigationId]: left, [detailId]: 100 - left }
}

/** Keep both panes mounted during resizing so selection, scroll and graph zoom survive. */
export function TraceDetailLayout({
  viewMode,
  callListOpen,
  filtersExpanded,
  navigation,
  children,
}: {
  viewMode: ViewMode
  callListOpen: boolean
  filtersExpanded: boolean
  navigation: ReactNode
  children: ReactNode
}) {
  const { t } = useTranslation()
  const narrow = useSyncExternalStore(
    subscribe,
    () => window.matchMedia(narrowQuery).matches,
    () => false
  )
  const id = useId()
  const navigationId = `${id}-navigation`
  const detailId = `${id}-detail`
  const group = useRef<GroupImperativeHandle>(null)
  const element = useRef<HTMLDivElement>(null)
  const interaction = useRef<ViewMode | null>(null)
  const [initialLayout] = useState(() =>
    readLayout(viewMode, navigationId, detailId)
  )

  useLayoutEffect(() => {
    const restore = () => {
      interaction.current = null
      const left = callListOpen ? (filtersExpanded ? 45 : 35) : 0
      group.current?.setLayout(
        narrow
          ? { [navigationId]: left, [detailId]: 100 - left }
          : readLayout(viewMode, navigationId, detailId)
      )
    }
    restore()
    // The panel group recalculates pixel constraints after its container resizes.
    // Restore again with those constraints, rather than keeping a ratio clamped
    // against the previous narrow orientation or an intermediate drawer width.
    const observer = new ResizeObserver(restore)
    if (element.current) observer.observe(element.current)
    return () => observer.disconnect()
  }, [viewMode, narrow, callListOpen, filtersExpanded, navigationId, detailId])

  function remember(layout: Layout) {
    // Ignore automatic constraint adjustments (fullscreen, viewport or orientation).
    // Only deliberate pointer/keyboard resizing should replace the desktop preference.
    if (narrow || interaction.current !== viewMode) return
    interaction.current = null
    try {
      localStorage.setItem(storageKey(viewMode), String(layout[navigationId]))
    } catch {
      // Resizing still works without persistence.
    }
  }

  return (
    <ResizablePanelGroup
      groupRef={group}
      elementRef={element}
      defaultLayout={initialLayout}
      orientation={narrow ? "vertical" : "horizontal"}
      disabled={narrow}
      onLayoutChanged={remember}
      resizeTargetMinimumSize={{ fine: 8, coarse: 24 }}
      className={cn(
        "min-h-0 min-w-0 flex-1 max-lg:touch-auto!",
        // Keep space for span rows after the toolbar. Cap this minimum so
        // crowded headers cannot squeeze the detail out of a short window.
        callListOpen &&
          (filtersExpanded
            ? "max-lg:[&>[data-trace-detail-pane=navigation]]:min-h-[min(22rem,45dvh,50%)]!"
            : "max-lg:[&>[data-trace-detail-pane=navigation]]:min-h-[min(16rem,35dvh,50%)]!")
      )}
      data-trace-detail-split="true"
    >
      <ResizablePanel
        id={navigationId}
        data-trace-detail-pane="navigation"
        minSize={narrow ? "0%" : "320px"}
        className="h-full min-h-0 min-w-0 overflow-hidden"
      >
        {navigation}
      </ResizablePanel>
      <ResizableHandle
        aria-label={t("traceDetail.resizePanes")}
        title={t("traceDetail.resizePanesHint")}
        data-trace-detail-resize="true"
        className={
          narrow
            ? "hidden"
            : "w-2 bg-muted/40 transition-colors hover:bg-primary/20 focus-visible:bg-primary/20"
        }
        withHandle
        disableDoubleClick
        onPointerDownCapture={() => {
          interaction.current = viewMode
        }}
        onKeyDownCapture={(event) => {
          if (
            ["ArrowLeft", "ArrowRight", "Home", "End", "Enter"].includes(
              event.key
            )
          ) {
            interaction.current = viewMode
          }
        }}
        onDoubleClick={() => {
          // The default can be clamped to the current width. Reset the
          // preference even if setLayout does not emit a change.
          interaction.current = null
          try {
            localStorage.removeItem(storageKey(viewMode))
          } catch {
            // Apply the default even when browser storage is unavailable.
          }
          const left = DEFAULT_LEFT[viewMode]
          group.current?.setLayout({
            [navigationId]: left,
            [detailId]: 100 - left,
          })
        }}
      />
      <ResizablePanel
        id={detailId}
        data-trace-detail-pane="detail"
        minSize={narrow ? "0%" : "400px"}
        className="h-full min-h-0 min-w-0 overflow-hidden border-l"
      >
        {children}
      </ResizablePanel>
    </ResizablePanelGroup>
  )
}
