/**
 * /receipt-spa — Standalone receipt page (mobile-first, no shell).
 *
 * Renders the receipt directly with the first 4 available agents pre-selected.
 * No AppShell, no sidebar, no header — just the receipt, optimized for
 * mobile sharing.
 */

import { useEffect, useMemo } from "react"

import { useAgent } from "@/contexts/AgentContext"
import { useUser } from "@/contexts/UserContext"
import { useTimeFilter } from "@/contexts/TimeFilterContext"

import { Receipt } from "./Receipt"
import {
  buildReceiptAgents,
  useReceiptData,
  type ReceiptAgentDescriptor,
} from "./hooks/useReceiptData"

const DEFAULT_AGENT_COUNT = 4

export default function ReceiptSpaPage() {
  const { agents, agentTypes } = useAgent()
  const { user } = useUser()
  const { setCustomTime } = useTimeFilter()

  useEffect(() => {
    const now = new Date()
    const startOfMonth = new Date(now.getFullYear(), now.getMonth(), 1)
    startOfMonth.setHours(0, 0, 0, 0)
    const endOfMonth = new Date(now.getFullYear(), now.getMonth() + 1, 0)
    endOfMonth.setHours(23, 59, 59, 0)
    setCustomTime(startOfMonth.toISOString(), endOfMonth.toISOString())
  }, [setCustomTime])

  const receiptUser = useMemo(
    () => (user ? { name: user.name, userUuid: user.user_uuid } : null),
    [user]
  )

  const availableAgents: ReceiptAgentDescriptor[] = useMemo(
    () => buildReceiptAgents(agents, agentTypes),
    [agents, agentTypes]
  )

  const selectedAgents = useMemo(
    () => availableAgents.slice(0, DEFAULT_AGENT_COUNT),
    [availableAgents]
  )

  const { shares, totals, loading, errors, timeRange } = useReceiptData({
    selectedAgents,
  })

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

  return (
    <div className="min-h-dvh bg-zinc-50 px-4 py-3 sm:px-6 sm:py-4 dark:bg-zinc-950">
      <div className="mx-auto flex max-w-[420px] flex-col items-center">
        <div className="w-full">
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
      </div>
    </div>
  )
}
