import { ChevronDown, RefreshCw } from "lucide-react"
import { useRef } from "react"
import { useTranslation } from "react-i18next"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Dialog, DialogTitle } from "@/components/ui/dialog"
import { DialogContent } from "@/components/dialogs/DialogContent"
import { CostCurrencyControls } from "./PricingCurrency"
import { PricingLabel } from "./PricingLabel"
import { catalogProvenance } from "./presentation"
import type { CatalogStatus, PricingSyncSource } from "./types"

interface PricingSettingsDialogProps {
  spaceId: string
  catalog?: CatalogStatus
  canManage: boolean
  syncSource: PricingSyncSource
  onSourceChange: (source: PricingSyncSource) => void
  syncPending: boolean
  syncError: boolean
  onSync: () => void
  onClose: () => void
}

/** Keep occasional price maintenance out of the daily cost review. */
export function PricingSettingsDialog({
  spaceId,
  catalog,
  canManage,
  syncSource,
  onSourceChange,
  syncPending,
  syncError,
  onSync,
  onClose,
}: PricingSettingsDialogProps) {
  const { t, i18n } = useTranslation()
  const returnFocus = useRef(
    typeof document !== "undefined" &&
      document.activeElement instanceof HTMLElement
      ? document.activeElement
      : null
  )
  const provenance = catalogProvenance(catalog)
  const retrieved = catalog?.fetchedAt ? new Date(catalog.fetchedAt) : null
  const retrievedLabel =
    retrieved && Number.isFinite(retrieved.getTime())
      ? retrieved.toLocaleString(i18n.resolvedLanguage || i18n.language)
      : t("modelPricing.catalog.never")

  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent
        size="detail"
        className="gap-0 overflow-y-auto p-0"
        aria-describedby={undefined}
        onCloseAutoFocus={(event) => {
          event.preventDefault()
          returnFocus.current?.focus()
        }}
      >
        <header className="px-6 pt-6 pr-12 pb-5">
          <DialogTitle>{t("modelPricing.workspace.settingsTitle")}</DialogTitle>
        </header>

        <div className="divide-y border-y">
          <section
            aria-labelledby="pricing-settings-catalog-title"
            className="space-y-4 px-6 py-5"
          >
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div className="min-w-0 space-y-1.5">
                <h3 id="pricing-settings-catalog-title" className="font-medium">
                  {t("modelPricing.catalog.title")}
                </h3>
                <p className="max-w-prose text-xs leading-relaxed text-muted-foreground">
                  {t("modelPricing.catalog.description")}
                </p>
              </div>
              <Button
                size="sm"
                variant="outline"
                disabled={!canManage || syncPending || !catalog}
                onClick={onSync}
              >
                <RefreshCw
                  className={syncPending ? "size-3.5 animate-spin" : "size-3.5"}
                />
                {t(
                  syncPending
                    ? "modelPricing.catalog.syncing"
                    : "modelPricing.catalog.sync"
                )}
              </Button>
            </div>

            <div className="flex flex-wrap items-center gap-x-4 gap-y-2 text-xs">
              {catalog ? (
                <>
                  {catalog.status !== "ready" && (
                    <Badge variant="secondary" className="font-normal">
                      {t(`modelPricing.catalog.${catalog.status}`)}
                    </Badge>
                  )}
                  <span>
                    {t("modelPricing.catalog.models", {
                      count: catalog.modelCount,
                    })}
                  </span>
                  <span className="text-muted-foreground">
                    {t("modelPricing.catalog.fetched", {
                      date: retrievedLabel,
                    })}
                  </span>
                </>
              ) : (
                <span className="text-muted-foreground">
                  {t("common.loading")}
                </span>
              )}
            </div>

            {(syncError || catalog?.lastError) && (
              <p
                role="alert"
                className="rounded-lg bg-destructive/5 px-3 py-2 text-xs leading-relaxed text-destructive"
              >
                {t(
                  catalog?.snapshotId
                    ? "modelPricing.catalog.syncError"
                    : "modelPricing.catalog.firstSyncError"
                )}
              </p>
            )}

            <details className="group text-xs">
              <summary className="flex w-fit cursor-pointer list-none items-center gap-1.5 rounded-sm py-1 text-muted-foreground outline-none focus-visible:ring-2 focus-visible:ring-ring [&::-webkit-details-marker]:hidden">
                {t("modelPricing.workspace.advanced")}
                <ChevronDown className="size-3.5 transition-transform group-open:rotate-180 motion-reduce:transition-none" />
              </summary>
              <div className="space-y-4 pt-3">
                <div className="max-w-sm space-y-2">
                  <PricingLabel htmlFor="pricing-sync-source">
                    {t("modelPricing.catalog.syncSource")}
                  </PricingLabel>
                  <select
                    id="pricing-sync-source"
                    value={syncSource}
                    disabled={!canManage || syncPending}
                    onChange={(event) =>
                      onSourceChange(event.target.value as PricingSyncSource)
                    }
                    className="h-9 w-full rounded-md border border-input bg-background px-3 text-sm shadow-xs outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-50"
                  >
                    {(["auto", "api", "repository"] as const).map((source) => (
                      <option key={source} value={source}>
                        {t(`modelPricing.catalog.sources.${source}`)}
                      </option>
                    ))}
                  </select>
                </div>
                {catalog?.snapshotId && (
                  <div
                    data-catalog-provenance
                    className="space-y-2 break-words text-muted-foreground"
                  >
                    <p>
                      {t("modelPricing.catalog.source")}{" "}
                      {provenance.href ? (
                        <a
                          href={provenance.href}
                          target="_blank"
                          rel="noreferrer"
                          className="rounded-sm underline underline-offset-2 outline-none focus-visible:ring-2 focus-visible:ring-ring"
                        >
                          {t(
                            `modelPricing.catalog.sources.${provenance.source}`
                          )}
                        </a>
                      ) : (
                        t(`modelPricing.catalog.sources.${provenance.source}`)
                      )}
                    </p>
                    {provenance.revision && (
                      <p>
                        {t(
                          `modelPricing.catalog.revisions.${provenance.revision.kind}`
                        )}{" "}
                        <code
                          title={provenance.revision.full}
                          className="font-mono"
                        >
                          {provenance.revision.short}
                        </code>
                      </p>
                    )}
                  </div>
                )}
                <p className="max-w-prose leading-relaxed text-muted-foreground">
                  {t("modelPricing.catalog.valuation")}
                </p>
              </div>
            </details>
          </section>

          <section
            aria-labelledby="pricing-settings-currency-title"
            className="space-y-4 px-6 py-5"
          >
            <h3 id="pricing-settings-currency-title" className="font-medium">
              {t("modelPricing.workspace.currencyTitle")}
            </h3>
            <CostCurrencyControls spaceId={spaceId} />
          </section>
        </div>

        <footer className="flex flex-wrap items-center justify-between gap-3 px-6 py-4">
          {!canManage ? (
            <p className="max-w-prose text-xs leading-relaxed text-muted-foreground">
              {t("modelPricing.readOnly")}
            </p>
          ) : (
            <span />
          )}
          <Button variant="outline" size="sm" onClick={onClose}>
            {t("common.close")}
          </Button>
        </footer>
      </DialogContent>
    </Dialog>
  )
}
