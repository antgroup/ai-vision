import type { ReactNode } from "react"
import { Search } from "lucide-react"
import { useTranslation } from "react-i18next"

import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { cn } from "@/lib/utils"
import type { McpHealth } from "@/labs/mcp/types"
import { LabsCatalogueHeading } from "./LabsCatalogueLayout"

const HEALTH_FILTERS: Array<McpHealth | "all"> = [
  "all",
  "healthy",
  "degraded",
  "failing",
  "unknown",
]
const SELECT_CLASS =
  "h-8 min-w-0 rounded-md border border-input bg-background px-2 text-xs text-foreground outline-none transition-[border-color,box-shadow] focus-visible:border-[var(--color-labs)] focus-visible:ring-2 focus-visible:ring-[var(--color-border-labs)]"

export type ServerInventoryFilter = "all" | "observed" | "imported"
export type ServerCatalogueSort = "name" | "calls" | "errors" | "p95"

export function LabsServerCatalogueToolbar({
  title,
  counts,
  shown,
  total,
  keyword,
  health,
  inventory,
  inventoryDisabled = false,
  sort,
  onKeywordChange,
  onHealthChange,
  onInventoryChange,
  onSortChange,
  onReset,
}: {
  title: string
  counts: ReactNode
  shown: number
  total: number
  keyword: string
  health: McpHealth | "all"
  inventory: ServerInventoryFilter
  inventoryDisabled?: boolean
  sort: ServerCatalogueSort
  onKeywordChange: (value: string) => void
  onHealthChange: (value: McpHealth | "all") => void
  onInventoryChange: (value: ServerInventoryFilter) => void
  onSortChange: (value: ServerCatalogueSort) => void
  onReset: () => void
}) {
  const { t } = useTranslation()
  const filtered =
    Boolean(keyword.trim()) || health !== "all" || inventory !== "all"
  return (
    <>
      <LabsCatalogueHeading title={title} counts={counts}>
        {total > 0 && (shown < total || filtered) ? (
          <span className="ml-auto text-xs text-muted-foreground">
            {t("labs.mcp.topic.shown", { shown, total })}
          </span>
        ) : null}
      </LabsCatalogueHeading>
      <div className="flex flex-wrap items-center justify-end gap-2">
        <div className="relative min-w-0 basis-full md:min-w-40 md:flex-1 md:basis-auto">
          <Search className="pointer-events-none absolute top-2 left-2 size-4 text-muted-foreground" />
          <Input
            aria-label={t("labs.mcp.topic.search")}
            value={keyword}
            onChange={(event) => onKeywordChange(event.target.value)}
            placeholder={t("labs.mcp.topic.search")}
            className="h-8 pl-8 focus-visible:border-[var(--color-labs)] focus-visible:ring-[var(--color-border-labs)]"
          />
        </div>
        <div className="grid w-full grid-cols-3 gap-2 sm:w-auto">
          <select
            aria-label={t("labs.mcp.healthFilter")}
            value={health}
            onChange={(event) =>
              onHealthChange(event.target.value as McpHealth | "all")
            }
            className={cn(
              SELECT_CLASS,
              health !== "all" &&
                "border-[var(--color-border-labs)] bg-[var(--color-bg-labs)] text-[var(--color-labs)]"
            )}
          >
            {HEALTH_FILTERS.map((value) => (
              <option key={value} value={value}>
                {t(
                  value === "all"
                    ? "labs.mcp.topic.allStatuses"
                    : `labs.mcp.health.${value}`
                )}
              </option>
            ))}
          </select>
          <select
            aria-label={t("labs.mcp.topic.inventoryLabel")}
            value={inventory}
            disabled={inventoryDisabled}
            onChange={(event) =>
              onInventoryChange(event.target.value as ServerInventoryFilter)
            }
            className={cn(
              SELECT_CLASS,
              "disabled:opacity-50",
              inventory !== "all" &&
                "border-[var(--color-border-labs)] bg-[var(--color-bg-labs)] text-[var(--color-labs)]"
            )}
          >
            {["all", "observed", "imported"].map((value) => (
              <option key={value} value={value}>
                {t(`labs.mcp.topic.inventory.${value}`)}
              </option>
            ))}
          </select>
          <select
            aria-label={t("labs.mcp.topic.sortLabel")}
            value={sort}
            onChange={(event) =>
              onSortChange(event.target.value as ServerCatalogueSort)
            }
            className={cn(
              SELECT_CLASS,
              sort !== "name" &&
                "border-[var(--color-border-labs)] bg-[var(--color-bg-labs)] text-[var(--color-labs)]"
            )}
          >
            {["name", "calls", "errors", "p95"].map((value) => (
              <option key={value} value={value}>
                {t(`labs.mcp.topic.sort.${value}`)}
              </option>
            ))}
          </select>
        </div>
        {filtered || sort !== "name" ? (
          <Button
            variant="ghost"
            size="sm"
            className="shrink-0 text-[var(--color-labs)] hover:bg-[var(--color-bg-labs-tag)] hover:text-[var(--color-labs)] dark:hover:bg-[var(--color-bg-labs-tag)]"
            onClick={onReset}
          >
            {t("labs.mcp.topic.reset")}
          </Button>
        ) : null}
      </div>
    </>
  )
}
