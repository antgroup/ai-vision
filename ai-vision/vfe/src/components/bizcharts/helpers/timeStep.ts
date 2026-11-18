import type { QueryTimeRange } from "@/agents/types"
import { resolveQueryTimeWindow } from "./chartHelpers"

const MAX_POINTS = 500
const MIN_POINTS = 2
const TARGET_MIN = 10
const TARGET_MAX = 100

export interface TimeStepOption {
  step: string
  label: string
  bucketMs: number
  disabled: boolean
}

const ALL_STEPS = [
  { step: "PT5M", label: "5 分钟", bucketMs: 300_000 },
  { step: "PT10M", label: "10 分钟", bucketMs: 600_000 },
  { step: "PT1H", label: "1 小时", bucketMs: 3_600_000 },
  { step: "P1D", label: "1 天", bucketMs: 86_400_000 },
] as const

const DEFAULT_STEP = "P1D"

export function getAvailableSteps(timeRange: QueryTimeRange): TimeStepOption[] {
  const window = resolveQueryTimeWindow(timeRange)
  if (!window) {
    return ALL_STEPS.map((s) => ({ ...s, disabled: s.step !== DEFAULT_STEP }))
  }

  const span = window.end - window.start
  return ALL_STEPS.map((s) => {
    const points = span / s.bucketMs
    return { ...s, disabled: points > MAX_POINTS || points < MIN_POINTS }
  })
}

/**
 * Pick the coarsest step that yields 10–100 data points.
 * Fallback: coarsest step with ≥ MIN_POINTS.
 */
export function resolveDefaultStep(timeRange: QueryTimeRange): string {
  const window = resolveQueryTimeWindow(timeRange)
  if (!window) return DEFAULT_STEP

  const span = window.end - window.start

  for (let i = ALL_STEPS.length - 1; i >= 0; i--) {
    const points = span / ALL_STEPS[i].bucketMs
    if (points >= TARGET_MIN && points <= TARGET_MAX) return ALL_STEPS[i].step
  }

  for (let i = ALL_STEPS.length - 1; i >= 0; i--) {
    const points = span / ALL_STEPS[i].bucketMs
    if (points >= MIN_POINTS) return ALL_STEPS[i].step
  }

  return DEFAULT_STEP
}

export function stepBucketMs(step: string): number {
  return ALL_STEPS.find((s) => s.step === step)?.bucketMs ?? 86_400_000
}

export function stepLabel(step: string): string {
  return ALL_STEPS.find((s) => s.step === step)?.label ?? step
}

export function resolveDefaultStepLabel(timeRange: QueryTimeRange): string {
  return stepLabel(resolveDefaultStep(timeRange))
}
