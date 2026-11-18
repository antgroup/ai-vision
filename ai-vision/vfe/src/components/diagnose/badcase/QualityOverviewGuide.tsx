import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { createPortal } from "react-dom"
import { X } from "lucide-react"
import { useTranslation } from "react-i18next"

import { Button } from "@/components/ui/button"
import {
  Popover,
  PopoverAnchor,
  PopoverContent,
  PopoverDescription,
  PopoverHeader,
  PopoverTitle,
} from "@/components/ui/popover"
import {
  hasSeenQualityOverviewGuide,
  markQualityOverviewGuideSeen,
} from "@/components/diagnose/qualityOverviewGuideState"

const VIEWPORT_GUTTER = 16
const TARGET_GUTTER = 4
const POPOVER_GAP = 10

export type QualityOverviewGuideStepKey =
  | "filters"
  | "scores"
  | "trend"
  | "detail"
  | "badcaseList"

interface GuideStep {
  key: QualityOverviewGuideStepKey
  title: string
  description: string
  selectors: string[]
}

interface TargetRect {
  top: number
  right: number
  bottom: number
  left: number
  width: number
  height: number
}

interface GuideSession {
  stepKeys: QualityOverviewGuideStepKey[]
  index: number
}

function resolveTargets(step: GuideStep): HTMLElement[] {
  if (typeof document === "undefined") return []
  for (const selector of step.selectors) {
    const elements = Array.from(
      document.querySelectorAll<HTMLElement>(selector)
    ).filter((element) => {
      const rect = element.getBoundingClientRect()
      return rect.width > 4 && rect.height > 4
    })
    if (elements.length > 0) return elements
  }
  return []
}

function toTargetRect(elements: HTMLElement[]): TargetRect {
  const rects = elements.map((element) => element.getBoundingClientRect())
  const top = Math.min(...rects.map((rect) => rect.top))
  const right = Math.max(...rects.map((rect) => rect.right))
  const bottom = Math.max(...rects.map((rect) => rect.bottom))
  const left = Math.min(...rects.map((rect) => rect.left))
  return {
    top,
    right,
    bottom,
    left,
    width: right - left,
    height: bottom - top,
  }
}

function rectChanged(previous: TargetRect | null, next: TargetRect): boolean {
  if (!previous) return true
  return (
    Math.abs(previous.top - next.top) > 0.5 ||
    Math.abs(previous.right - next.right) > 0.5 ||
    Math.abs(previous.bottom - next.bottom) > 0.5 ||
    Math.abs(previous.left - next.left) > 0.5 ||
    Math.abs(previous.width - next.width) > 0.5 ||
    Math.abs(previous.height - next.height) > 0.5
  )
}

export function QualityOverviewGuide({
  userKey,
  autoStart,
  onStepChange,
}: {
  userKey?: string
  autoStart: boolean
  onStepChange?: (step: QualityOverviewGuideStepKey | null) => void
}) {
  const { t } = useTranslation()
  const steps = useMemo<GuideStep[]>(
    () => [
      {
        key: "filters",
        title: t("diagnose.overview.guide.filtersTitle"),
        description: t("diagnose.overview.guide.filtersDescription"),
        selectors: [
          '[data-quality-guide="filters"] [role="combobox"]',
          '[data-quality-guide="empty"]',
        ],
      },
      {
        key: "scores",
        title: t("diagnose.overview.guide.scoresTitle"),
        description: t("diagnose.overview.guide.scoresDescription"),
        selectors: ['[data-quality-guide="scores"]'],
      },
      {
        key: "trend",
        title: t("diagnose.overview.guide.trendTitle"),
        description: t("diagnose.overview.guide.trendDescription"),
        selectors: ['[data-quality-guide="trend"]'],
      },
      {
        key: "detail",
        title: t("diagnose.overview.guide.detailTitle"),
        description: t("diagnose.overview.guide.detailDescription"),
        selectors: ['[data-quality-guide="detail"]'],
      },
      {
        key: "badcaseList",
        title: t("diagnose.overview.guide.badcaseListTitle"),
        description: t("diagnose.overview.guide.badcaseListDescription"),
        selectors: ['[data-quality-guide="badcase-list"]'],
      },
    ],
    [t]
  )
  const stepMap = useMemo(
    () => new Map(steps.map((step) => [step.key, step])),
    [steps]
  )
  const [session, setSession] = useState<GuideSession | null>(null)
  const [targetRect, setTargetRect] = useState<TargetRect | null>(null)
  const autoAttemptedRef = useRef(false)
  const previousFocusRef = useRef<HTMLElement | null>(null)

  const currentStep = session
    ? stepMap.get(session.stepKeys[session.index])
    : undefined

  const collectAvailableStepKeys = useCallback(
    () =>
      steps
        .filter((step) => resolveTargets(step).length > 0)
        .map((step) => step.key),
    [steps]
  )

  const startGuide = useCallback(
    (requireOverviewData: boolean) => {
      if (
        requireOverviewData &&
        !document.querySelector('[data-quality-guide="scores"]')
      ) {
        return false
      }
      const stepKeys = collectAvailableStepKeys()
      if (stepKeys.length === 0) return false
      previousFocusRef.current =
        document.activeElement instanceof HTMLElement
          ? document.activeElement
          : null
      setSession({ stepKeys, index: 0 })
      return true
    },
    [collectAvailableStepKeys]
  )

  const endGuide = useCallback(() => {
    if (userKey) markQualityOverviewGuideSeen(userKey)
    setSession(null)
    setTargetRect(null)
    requestAnimationFrame(() => previousFocusRef.current?.focus())
  }, [userKey])

  const goBack = useCallback(() => {
    setSession((current) =>
      current ? { ...current, index: Math.max(0, current.index - 1) } : current
    )
  }, [])

  const goNext = useCallback(() => {
    if (!session) return
    if (session.index >= session.stepKeys.length - 1) {
      endGuide()
      return
    }
    setSession((current) =>
      current ? { ...current, index: current.index + 1 } : current
    )
  }, [endGuide, session])

  const syncTargetRect = useCallback(() => {
    if (!currentStep) return
    const elements = resolveTargets(currentStep)
    if (elements.length === 0) {
      endGuide()
      return
    }
    const next = toTargetRect(elements)
    setTargetRect((previous) => (rectChanged(previous, next) ? next : previous))
  }, [currentStep, endGuide])

  useEffect(() => {
    if (!autoStart || !userKey || session || autoAttemptedRef.current) return
    if (hasSeenQualityOverviewGuide(userKey)) return

    const attemptStart = () => {
      if (!startGuide(true)) return false
      autoAttemptedRef.current = true
      return true
    }

    if (attemptStart()) return
    const observer = new MutationObserver(() => {
      if (attemptStart()) observer.disconnect()
    })
    observer.observe(document.body, { childList: true, subtree: true })
    const timeout = window.setTimeout(() => observer.disconnect(), 15_000)
    return () => {
      window.clearTimeout(timeout)
      observer.disconnect()
    }
  }, [autoStart, session, startGuide, userKey])

  useEffect(() => {
    onStepChange?.(currentStep?.key ?? null)
  }, [currentStep?.key, onStepChange])

  useEffect(
    () => () => {
      onStepChange?.(null)
    },
    [onStepChange]
  )

  useEffect(() => {
    if (!currentStep) return
    const elements = resolveTargets(currentStep)
    if (elements.length === 0) return
    const rect = toTargetRect(elements)
    if (rect.top < 72 || rect.bottom > window.innerHeight - VIEWPORT_GUTTER) {
      elements[0].scrollIntoView({ behavior: "smooth", block: "center" })
    }
    const frame = requestAnimationFrame(syncTargetRect)
    const timer = window.setTimeout(syncTargetRect, 300)
    const resizeObserver = new ResizeObserver(syncTargetRect)
    const mutationObserver = new MutationObserver(syncTargetRect)
    elements.forEach((element) => resizeObserver.observe(element))
    mutationObserver.observe(document.body, { childList: true, subtree: true })
    window.addEventListener("resize", syncTargetRect)
    window.addEventListener("scroll", syncTargetRect, true)
    return () => {
      cancelAnimationFrame(frame)
      window.clearTimeout(timer)
      resizeObserver.disconnect()
      mutationObserver.disconnect()
      window.removeEventListener("resize", syncTargetRect)
      window.removeEventListener("scroll", syncTargetRect, true)
    }
  }, [currentStep, syncTargetRect])

  useEffect(() => {
    if (!session) return
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.defaultPrevented) return
      const target = event.target instanceof Element ? event.target : null
      if (
        target?.closest('[role="combobox"], [role="listbox"], [role="option"]')
      ) {
        return
      }
      if (event.key === "Escape") endGuide()
      if (event.key === "ArrowLeft") goBack()
      if (event.key === "ArrowRight") goNext()
    }
    window.addEventListener("keydown", onKeyDown)
    return () => {
      window.removeEventListener("keydown", onKeyDown)
    }
  }, [endGuide, goBack, goNext, session])

  if (
    !session ||
    !currentStep ||
    !targetRect ||
    typeof document === "undefined"
  ) {
    return null
  }

  const viewportWidth = window.innerWidth
  const viewportHeight = window.innerHeight
  const focusRect = {
    top: Math.max(0, targetRect.top - TARGET_GUTTER),
    right: Math.min(viewportWidth, targetRect.right + TARGET_GUTTER),
    bottom: Math.min(viewportHeight, targetRect.bottom + TARGET_GUTTER),
    left: Math.max(0, targetRect.left - TARGET_GUTTER),
  }
  const focusWidth = Math.max(0, focusRect.right - focusRect.left)
  const focusHeight = Math.max(0, focusRect.bottom - focusRect.top)
  const isLastStep = session.index === session.stepKeys.length - 1
  const navigationButtonClassName =
    "h-8 min-w-[72px] rounded-lg px-3 font-normal"

  const mask = createPortal(
    <div role="presentation" className="pointer-events-none fixed inset-0 z-40">
      <div
        className="pointer-events-auto fixed top-0 left-0 bg-black/[0.28]"
        style={{ width: viewportWidth, height: focusRect.top }}
      />
      <div
        className="pointer-events-auto fixed left-0 bg-black/[0.28]"
        style={{
          top: focusRect.top,
          width: focusRect.left,
          height: focusHeight,
        }}
      />
      <div
        className="pointer-events-auto fixed bg-black/[0.28]"
        style={{
          top: focusRect.top,
          left: focusRect.right,
          width: viewportWidth - focusRect.right,
          height: focusHeight,
        }}
      />
      <div
        className="pointer-events-auto fixed bottom-0 left-0 bg-black/[0.28]"
        style={{
          top: focusRect.bottom,
          width: viewportWidth,
        }}
      />
    </div>,
    document.body
  )

  return (
    <>
      {mask}
      <Popover open modal={false}>
        <PopoverAnchor asChild>
          <span
            className="pointer-events-none fixed z-40"
            style={{
              top: focusRect.top,
              left: focusRect.left,
              width: focusWidth,
              height: focusHeight,
            }}
          />
        </PopoverAnchor>
        <PopoverContent
          role="dialog"
          aria-modal="false"
          aria-labelledby="quality-guide-title"
          aria-describedby="quality-guide-description"
          side="bottom"
          align="start"
          sideOffset={POPOVER_GAP}
          collisionPadding={VIEWPORT_GUTTER}
          onOpenAutoFocus={(event) => event.preventDefault()}
          onInteractOutside={(event) => event.preventDefault()}
          className="z-[60] h-[212px] w-[320px] max-w-[calc(100vw-32px)] gap-0 rounded-lg border border-[var(--color-border)] bg-background p-4 shadow-[0_2px_8px_rgba(0,0,0,0.12)]"
        >
          <div className="flex items-start justify-between gap-3">
            <PopoverHeader className="min-w-0 gap-1">
              <p className="text-xs text-[var(--color-text-description)] tabular-nums">
                {t("diagnose.overview.guide.progress", {
                  current: session.index + 1,
                  total: session.stepKeys.length,
                })}
              </p>
              <PopoverTitle
                id="quality-guide-title"
                className="text-base font-medium text-[var(--color-text-primary)]"
              >
                {currentStep.title}
              </PopoverTitle>
            </PopoverHeader>
            <Button
              type="button"
              variant="ghost"
              size="icon-sm"
              className="shrink-0 rounded-lg"
              onClick={endGuide}
              aria-label={t("diagnose.overview.guide.close")}
            >
              <X className="size-4" />
            </Button>
          </div>
          <PopoverDescription
            id="quality-guide-description"
            className="mt-2 min-h-[66px] text-sm leading-[22px] text-[var(--color-text-secondary)]"
          >
            {currentStep.description}
          </PopoverDescription>
          <div className="mt-auto flex items-center justify-between pt-4">
            {!isLastStep ? (
              <Button
                type="button"
                variant="ghost"
                size="sm"
                className="h-8 rounded-lg px-2 font-normal text-muted-foreground"
                onClick={endGuide}
              >
                {t("diagnose.overview.guide.skip")}
              </Button>
            ) : (
              <span />
            )}
            <div className="grid w-[152px] grid-cols-2 gap-2">
              {session.index > 0 ? (
                <Button
                  type="button"
                  variant="secondary"
                  size="sm"
                  className={navigationButtonClassName}
                  onClick={goBack}
                >
                  {t("diagnose.overview.guide.previous")}
                </Button>
              ) : (
                <span aria-hidden="true" />
              )}
              <Button
                type="button"
                size="sm"
                className={navigationButtonClassName}
                onClick={goNext}
              >
                {isLastStep
                  ? t("diagnose.overview.guide.finish")
                  : t("diagnose.overview.guide.next")}
              </Button>
            </div>
          </div>
        </PopoverContent>
      </Popover>
    </>
  )
}
