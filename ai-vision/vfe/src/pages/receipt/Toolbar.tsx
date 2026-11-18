/**
 * Receipt page sidebar.
 *
 * Vertical agent multi-selector + quick actions + save-image trigger.
 * Selection state, save handler, and loading flags are owned by the page
 * so URL sync and html-to-image capture remain a single source of truth.
 *
 * Time range, refresh, and theme controls live in the AppShell header
 * and are intentionally not duplicated here.
 */

import { useTranslation } from "react-i18next"
import { Download, Loader2 } from "lucide-react"

import { Button } from "@/components/ui/button"
import { Checkbox } from "@/components/ui/checkbox"

import type { ReceiptAgentDescriptor } from "./hooks/useReceiptData"

interface ReceiptToolbarProps {
  availableAgents: ReceiptAgentDescriptor[]
  selectedProducts: string[]
  onChange: (next: string[]) => void
  loading?: boolean
  onSave?: () => void
  saving?: boolean
  canSave?: boolean
}

export function ReceiptToolbar({
  availableAgents,
  selectedProducts,
  onChange,
  loading = false,
  onSave,
  saving = false,
  canSave = false,
}: ReceiptToolbarProps) {
  const { t } = useTranslation()
  if (availableAgents.length === 0) {
    return (
      <div className="w-full max-w-[420px] rounded-xl border border-dashed border-zinc-300 bg-white px-4 py-3 text-sm text-zinc-500 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-400">
        {t("receipt.noAgents")}
      </div>
    )
  }

  const selectedSet = new Set(selectedProducts)

  const toggle = (product: string) => {
    if (selectedSet.has(product)) {
      onChange(selectedProducts.filter((p) => p !== product))
    } else {
      onChange([...selectedProducts, product])
    }
  }

  return (
    <aside className="flex w-full max-w-[420px] flex-col gap-4 rounded-xl border border-border bg-background p-4 lg:w-[420px] lg:flex-none">
      {/* Header */}
      <div className="flex items-center justify-between">
        <span className="text-sm font-medium text-foreground">
          {t("receipt.agents")}
        </span>
        {loading ? (
          <Loader2 className="size-3.5 animate-spin text-muted-foreground" />
        ) : (
          <span className="text-xs text-muted-foreground">
            {selectedProducts.length} / {availableAgents.length}
          </span>
        )}
      </div>

      {/* Agent list */}
      <ul className="flex flex-col gap-2">
        {availableAgents.map((agent) => {
          const checked = selectedSet.has(agent.agentProduct)
          return (
            <li key={agent.agentProduct}>
              <label className="flex cursor-pointer items-center gap-2.5 rounded-md px-1 py-1 text-sm text-foreground transition-colors hover:bg-muted/50">
                <Checkbox
                  checked={checked}
                  onCheckedChange={() => toggle(agent.agentProduct)}
                />
                <span className="truncate">{agent.agentLabel}</span>
              </label>
            </li>
          )
        })}
      </ul>

      {/* Quick actions */}
      <div className="flex items-center gap-2 text-xs text-muted-foreground">
        <button
          type="button"
          className="rounded-md px-2 py-1 transition-colors hover:bg-muted hover:text-foreground"
          onClick={() => onChange(availableAgents.map((a) => a.agentProduct))}
        >
          {t("receipt.selectAll")}
        </button>
        <button
          type="button"
          className="rounded-md px-2 py-1 transition-colors hover:bg-muted hover:text-foreground"
          onClick={() => onChange([])}
        >
          {t("receipt.clear")}
        </button>
      </div>

      {/* Save image */}
      {onSave ? (
        <>
          <div className="border-t border-border" />
          <Button
            variant="outline"
            size="sm"
            disabled={!canSave || saving}
            onClick={onSave}
            className="w-full"
          >
            {saving ? (
              <Loader2 className="size-4 animate-spin" />
            ) : (
              <Download className="size-4" />
            )}
            {t("receipt.save")}
          </Button>
        </>
      ) : null}
    </aside>
  )
}
