import { useQuery } from "@tanstack/react-query"
import { ChevronDown, Loader2 } from "lucide-react"
import { Popover as PopoverPrimitive } from "radix-ui"
import { useCallback, useRef } from "react"
import { useTranslation } from "react-i18next"
import { Link } from "react-router-dom"

import { Button } from "@/components/ui/button"
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover"
import { CostSummary } from "@/features/model-pricing/CostSummary"
import { getPricingInventory } from "@/features/model-pricing/api"
import { PRICING_INVENTORY_QUERY } from "@/features/model-pricing/pricingQueryInvalidation"
import { isValidPricingWindow } from "@/features/model-pricing/timeRange"
import { useDashboardTraceSourceSystem } from "./DashboardTraceSourceContext"

export function DashboardCost({
  spaceId,
  agentUuid,
  from,
  to,
  refreshTick,
  simulated,
}: {
  spaceId: string
  agentUuid: string
  from: string
  to: string
  refreshTick: number
  simulated: boolean
}) {
  const { t } = useTranslation()
  const traceSource = useDashboardTraceSourceSystem()
  const sectionRef = useRef<HTMLElement>(null)
  const statusTriggerRef = useRef<HTMLButtonElement>(null)
  const keepFocusOnReplacement = useCallback((node: HTMLElement | null) => {
    if (!node) return
    return () => {
      const document = node.ownerDocument
      if (!node.contains(document.activeElement)) return
      // The status trigger and its portaled popup can both own focus. Wait
      // for the new amount button to mount before restoring it.
      queueMicrotask(() => {
        if (document.activeElement && document.activeElement !== document.body)
          return
        sectionRef.current
          ?.querySelector<HTMLButtonElement>("[data-cost-details-trigger]")
          ?.focus({ preventScroll: true })
      })
    }
  }, [])
  const rememberStatusTrigger = useCallback(
    (node: HTMLButtonElement | null) => {
      statusTriggerRef.current = node
      return keepFocusOnReplacement(node)
    },
    [keepFocusOnReplacement]
  )
  const window = { agent_uuid: agentUuid, from, to }
  const validWindow = isValidPricingWindow(window)
  const inventory = useQuery({
    queryKey: [PRICING_INVENTORY_QUERY, spaceId, window, refreshTick],
    queryFn: async ({ signal }) => {
      const data = await getPricingInventory(spaceId, window, signal)
      if (
        data.scope?.agentUuid !== agentUuid ||
        data.from !== Date.parse(from) ||
        data.to !== Date.parse(to)
      ) {
        throw new Error(
          "Pricing inventory scope or time window differs from the request"
        )
      }
      if (!data.costCoverage) {
        throw new Error("Pricing inventory is missing cost coverage")
      }
      return data
    },
    enabled:
      traceSource === "signoz" && !!spaceId && !!agentUuid && validWindow,
    retry: false,
    refetchOnWindowFocus: false,
  })

  if (traceSource !== "signoz" || !spaceId || !agentUuid) return null

  const pricingHref = `/model-pricing?${new URLSearchParams({ spaceId, from, to })}`
  const coverage = inventory.data?.costCoverage
  const label = t("metrics.dashboardCost.label")
  const loading = validWindow && !coverage && !inventory.isError
  const status = !validWindow
    ? "metrics.dashboardCost.rangeLimit"
    : inventory.isError
      ? "metrics.dashboardCost.loadFailed"
      : loading
        ? "common.loading"
        : "metrics.dashboardCost.empty"

  return (
    <section
      ref={sectionRef}
      data-dashboard-cost
      aria-label={label}
      className="max-w-[68%] min-w-0 shrink-0 sm:max-w-[55%]"
    >
      {validWindow && !inventory.isError && coverage?.billableCalls ? (
        <CostSummary
          amountTrigger
          coverage={coverage}
          pricingHref={pricingHref}
          simulated={simulated}
        />
      ) : (
        <Popover>
          <PopoverTrigger asChild>
            <Button
              ref={rememberStatusTrigger}
              variant="ghost"
              size="sm"
              className="h-8 max-w-full gap-1 px-1.5 text-xs font-normal text-muted-foreground"
              aria-label={`${label} · ${t(status)}`}
              data-dashboard-cost-status-trigger
            >
              {label}
              {loading ? (
                <Loader2
                  className="size-3 shrink-0 motion-safe:animate-spin"
                  aria-hidden="true"
                />
              ) : (
                <span aria-hidden="true">—</span>
              )}
              <ChevronDown className="size-3 shrink-0" aria-hidden="true" />
            </Button>
          </PopoverTrigger>
          <PopoverContent
            ref={keepFocusOnReplacement}
            align="end"
            aria-label={t("modelPricing.cost.details")}
            className="w-64 max-w-[calc(100vw-2rem)] gap-2 rounded-xl p-3 text-xs"
            data-cost-details-body
          >
            <p role={inventory.isError ? "alert" : "status"}>{t(status)}</p>
            <div className="flex flex-wrap items-center justify-between gap-2">
              <Link
                to={pricingHref}
                className="rounded-sm font-medium underline underline-offset-4 outline-none focus-visible:ring-2 focus-visible:ring-ring"
              >
                {t("nav.modelPricing")}
              </Link>
              {inventory.isError && (
                <PopoverPrimitive.Close asChild>
                  <Button
                    variant="outline"
                    size="xs"
                    disabled={inventory.isFetching}
                    onClick={() => {
                      statusTriggerRef.current?.focus({ preventScroll: true })
                      void inventory.refetch()
                    }}
                  >
                    {t("common.retry")}
                  </Button>
                </PopoverPrimitive.Close>
              )}
            </div>
          </PopoverContent>
        </Popover>
      )}
    </section>
  )
}
