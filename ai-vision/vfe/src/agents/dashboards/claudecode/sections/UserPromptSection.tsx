import { useDashboardSectionPresentation } from "@/agents/shared/dashboardSectionPresentation"
import { useTranslation } from "react-i18next"
// UserPrompt section — matches V1 user_prompt fetch group.

import { useState } from "react"
import { ChevronDown, ChevronRight } from "lucide-react"

import type { DashboardScope, QueryTimeRange } from "@/agents/types"
import { useSectionQuery } from "@/agents/shared/hooks/useSectionQuery"
import { DataTable } from "@/components/bizcharts/datatable/DataTable"
import type { TableColumn } from "@/lib/schemas/observability"
import { useSourceSystem } from "@/contexts/SourceSystemContext"
import { cn } from "@/lib/utils"
import { selectQuery } from "../queries-select"
import { useTableControls } from "./tableControls"

export interface UserPromptSectionProps {
  scope: DashboardScope
  spaceId: string
  agentProduct: string
  timeRange: QueryTimeRange
  refreshTick?: number
}

export function UserPromptSection({
  scope,
  spaceId,
  agentProduct,
  timeRange,
  refreshTick,
}: UserPromptSectionProps) {
  const { t } = useTranslation()
  const promptColumns: TableColumn[] = [
    { key: "time", label: t("dashboard.columns.time"), data_type: "datetime" },
    {
      key: "session_id",
      label: t("dashboard.columns.session_id"),
      data_type: "string",
    },
    {
      key: "prompt",
      label: t("dashboard.columns.prompt"),
      data_type: "string",
    },
    {
      key: "prompt_length",
      label: t("dashboard.columns.prompt_length"),
      data_type: "number",
    },
    {
      key: "host_arch",
      label: t("dashboard.columns.host_arch"),
      data_type: "string",
    },
    {
      key: "os_type",
      label: t("dashboard.columns.os_type"),
      data_type: "string",
    },
    {
      key: "os_version",
      label: t("dashboard.columns.os_version"),
      data_type: "string",
    },
  ]
  const [collapsed, setCollapsed] = useState(false)

  const sourceSystem = useSourceSystem()
  const userPromptDetailQuery = selectQuery(
    "userPromptDetailQuery",
    sourceSystem
  )

  const detail = useSectionQuery({
    queryKey: userPromptDetailQuery.queryKey,
    scope,
    spaceId,
    agentProduct,
    timeRange,
    refreshTick,
  })
  const table = useTableControls(
    (detail.data?.rows ?? []) as Record<string, unknown>[],
    promptColumns,
    8
  )

  const embedded = useDashboardSectionPresentation([detail.error])

  return (
    <section className={embedded ? "min-w-0" : "px-4 pb-3"}>
      {!embedded ? (
        <button
          type="button"
          className="flex items-center gap-2 hover:opacity-80"
          onClick={() => setCollapsed((prev) => !prev)}
        >
          {collapsed ? (
            <ChevronRight className="size-4 text-muted-foreground" />
          ) : (
            <ChevronDown className="size-4 text-muted-foreground" />
          )}
          <h2 className="text-base font-medium">
            {t("dashboard.userPrompts")}
          </h2>
        </button>
      ) : null}

      <div
        className={cn(
          "mt-4 grid grid-cols-[repeat(24,minmax(0,1fr))] gap-4 transition-all",
          collapsed && "hidden",
          embedded && "mt-0 gap-3"
        )}
      >
        <DataTable
          className="col-span-24"
          title={embedded ? undefined : t("metrics.userPromptDetail")}
          columns={promptColumns}
          rows={table.rows}
          total={table.total}
          loading={detail.loading}
          error={detail.error ? String(detail.error) : undefined}
          emptyText={t("dashboard.emptyPrompts")}
          page={table.page}
          pageOptions={table.pageOptions}
          pageSize={table.pageSize}
          pageSizeOptions={table.pageSizeOptions}
          showAll={table.showAll}
          sortKey={table.sortKey}
          sortDirection={table.sortDirection}
          sortableKeys={table.sortableKeys}
          onSort={table.onSort}
          onPageChange={table.onPageChange}
          onPageSizeChange={table.onPageSizeChange}
          onShowAllToggle={table.onShowAllToggle}
        />
      </div>
    </section>
  )
}
