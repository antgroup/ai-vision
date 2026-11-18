/**
 * /receipt — Token usage receipt page.
 *
 * Renders one combined "small-paper" receipt that aggregates token usage
 * across the user's Claude-family and Codex-family agents in the current
 * space, for the global time range. Page-local state lives in the URL so
 * a link reproduces the exact view.
 *
 * Page is mounted as a child of AppShell (so it inherits AuthGuard +
 * SpaceProvider + AgentProvider + TimeFilterProvider), but no sidebar
 * entry — it's reached only by direct URL.
 */

import {
  startTransition,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react"
import { useTranslation } from "react-i18next"
import { useSearchParams } from "react-router-dom"
import { toast } from "sonner"

import { useAgent } from "@/contexts/AgentContext"
import { useUser } from "@/contexts/UserContext"
import { useTimeFilter } from "@/contexts/TimeFilterContext"

import { ReceiptToolbar } from "./Toolbar"
import { Receipt } from "./Receipt"
import {
  buildReceiptAgents,
  useReceiptData,
  type ReceiptAgentDescriptor,
} from "./hooks/useReceiptData"
import { formatDateOnly } from "./utils/format"

const URL_PARAM_AGENTS = "agents"

function parseUrlAgents(value: string | null): string[] {
  if (!value) return []
  return value
    .split(",")
    .map((p) => p.trim())
    .filter(Boolean)
}

function selectionsEqual(a: string[], b: string[]): boolean {
  if (a.length !== b.length) return false
  const setB = new Set(b)
  return a.every((item) => setB.has(item))
}

export default function ReceiptPage() {
  const { t } = useTranslation()
  const { agents, agentTypes } = useAgent()
  const { user } = useUser()
  const { setCustomTime } = useTimeFilter()
  const [searchParams, setSearchParams] = useSearchParams()

  useEffect(() => {
    const now = new Date()
    const startOfMonth = new Date(now.getFullYear(), now.getMonth(), 1)
    startOfMonth.setHours(0, 0, 0, 0)
    const endOfMonth = new Date(now.getFullYear(), now.getMonth() + 1, 0)
    endOfMonth.setHours(23, 59, 59, 0)
    setCustomTime(startOfMonth.toISOString(), endOfMonth.toISOString())
  }, [setCustomTime])

  const receiptUser = useMemo(
    () =>
      user
        ? {
            name: user.name,
            userUuid: user.user_uuid,
          }
        : null,
    [user]
  )

  const availableAgents: ReceiptAgentDescriptor[] = useMemo(
    () => buildReceiptAgents(agents, agentTypes),
    [agents, agentTypes]
  )

  // null = not yet initialized from URL/availableAgents
  const [selectedProducts, setSelectedProducts] = useState<string[] | null>(
    null
  )

  // Initialize selection once agents load. Prefer URL filter; otherwise select first 4.
  useEffect(() => {
    if (selectedProducts !== null) return
    if (availableAgents.length === 0) return

    const urlAgents = parseUrlAgents(searchParams.get(URL_PARAM_AGENTS))
    const validUrlAgents = urlAgents.filter((p) =>
      availableAgents.some((a) => a.agentProduct === p)
    )
    const initial =
      validUrlAgents.length > 0
        ? validUrlAgents
        : availableAgents.slice(0, 4).map((a) => a.agentProduct)
    startTransition(() => setSelectedProducts(initial))
  }, [availableAgents, searchParams, selectedProducts])

  // Mirror selection back into the URL — replace, not push, to keep history clean.
  useEffect(() => {
    if (selectedProducts === null) return
    const currentParam = searchParams.get(URL_PARAM_AGENTS) ?? ""
    const nextValue = selectedProducts.join(",")
    if (currentParam === nextValue) return

    const next = new URLSearchParams(searchParams)
    if (nextValue) {
      next.set(URL_PARAM_AGENTS, nextValue)
    } else {
      next.delete(URL_PARAM_AGENTS)
    }
    setSearchParams(next, { replace: true })
  }, [selectedProducts, searchParams, setSearchParams])

  const selectedDescriptors = useMemo(() => {
    if (selectedProducts === null) return []
    const lookup = new Map(availableAgents.map((a) => [a.agentProduct, a]))
    return selectedProducts
      .map((p) => lookup.get(p))
      .filter((a): a is ReceiptAgentDescriptor => Boolean(a))
  }, [selectedProducts, availableAgents])

  const { shares, totals, loading, errors, timeRange } = useReceiptData({
    selectedAgents: selectedDescriptors,
  })

  const handleSelectionChange = (next: string[]) => {
    setSelectedProducts((prev) => {
      if (prev !== null && selectionsEqual(prev, next)) return prev
      return next
    })
  }

  const rangeStart = useMemo(
    () =>
      timeRange.type === "absolute" ? new Date(timeRange.from) : new Date(),
    [timeRange]
  )
  const rangeEnd = useMemo(
    () => (timeRange.type === "absolute" ? new Date(timeRange.to) : new Date()),
    [timeRange]
  )

  const errorCount = Object.keys(errors).length
  const effectiveSelected = selectedProducts ?? []
  const hasData = effectiveSelected.length > 0 && shares.length > 0

  // Image export — wraps Receipt in a padded snapshot target so the layered
  // box-shadow has room to render in the PNG instead of being clipped.
  const captureRef = useRef<HTMLDivElement>(null)
  const [saving, setSaving] = useState(false)

  const handleSave = useCallback(async () => {
    const node = captureRef.current
    if (!node || saving) return
    setSaving(true)
    try {
      const { toPng } = await import("html-to-image")
      const dataUrl = await toPng(node, {
        pixelRatio: 2,
        cacheBust: true,
        backgroundColor: "transparent",
      })
      const stamp = formatDateOnly(rangeEnd).replace(/-/g, "")
      const link = document.createElement("a")
      link.download = `ai-vision-receipt-${stamp}.png`
      link.href = dataUrl
      link.click()
      toast.success(t("receipt.saved"))
    } catch (err) {
      const message =
        err instanceof Error ? err.message : t("receipt.saveFailed")
      toast.error(t("receipt.saveFailedWithReason", { message }))
    } finally {
      setSaving(false)
    }
  }, [rangeEnd, saving, t])

  return (
    <div className="min-h-full bg-zinc-50 px-4 py-6 sm:px-6 sm:py-8 dark:bg-zinc-900/40">
      <div className="mx-auto flex max-w-5xl flex-col items-center gap-6 lg:flex-row-reverse lg:items-start lg:justify-center lg:gap-6">
        <ReceiptToolbar
          availableAgents={availableAgents}
          selectedProducts={effectiveSelected}
          onChange={handleSelectionChange}
          loading={loading}
          onSave={handleSave}
          saving={saving}
          canSave={hasData && !loading}
        />

        <div className="min-w-0 flex-1 lg:flex lg:justify-center">
          {availableAgents.length > 0 && effectiveSelected.length === 0 ? (
            <div className="flex w-full max-w-[420px] items-center justify-center rounded-2xl border border-dashed border-zinc-300 bg-white px-6 py-20 text-sm text-zinc-500 dark:border-zinc-700 dark:bg-zinc-950 dark:text-zinc-400">
              {t("receipt.selectOne")}
            </div>
          ) : (
            <div ref={captureRef} className="px-4 pb-6">
              <Receipt
                totals={totals}
                shares={shares}
                rangeStart={rangeStart}
                rangeEnd={rangeEnd}
                user={receiptUser}
                loading={loading}
                hasError={errorCount > 0}
              />
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
