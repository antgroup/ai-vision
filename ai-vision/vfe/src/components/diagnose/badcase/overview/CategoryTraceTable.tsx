/**
 * CategoryTraceTable — 分类级 BadCase 表(一条 DiagnosisRecord 一行)。
 *
 * 使用通用记录列表查询，按 task_uuid + 可选 category + verdict=bad 筛选，
 * 不按 trace_id 聚合；详情打开后再读取当前 Record 的完整证据。
 */
import { useState } from "react"
import { useTranslation } from "react-i18next"
import { useQuery } from "@tanstack/react-query"
import { useNavigate } from "react-router-dom"
import { ExternalLink, Loader2 } from "lucide-react"

import { Button } from "@/components/ui/button"
import { useUser } from "@/contexts/UserContext"
import { useSpace } from "@/contexts/SpaceContext"

import { listRecords } from "../../recordApi"
import type {
  DiagnosisStatsDiagnoser,
  DiagnosisRecordListItemDTO,
  OverviewTraceEvidence,
} from "../../types"
import { sanitizeQuestionPreview } from "./overviewFormatters"

const PAGE_SIZE = 5

export function CategoryTraceTable({
  category,
  diagnoserUuid,
  taskUuid,
  jobUuid,
  ownerId,
  timeRange,
  refreshTick,
  onOpenTrace,
}: {
  category?: string
  diagnosers: DiagnosisStatsDiagnoser[]
  diagnoserUuid?: string
  taskUuid?: string
  jobUuid?: string
  ownerId?: string
  timeRange?: { start_time?: string; end_time?: string }
  refreshTick?: number
  onOpenTrace?: (
    traceId: string,
    jobUuid?: string,
    recordUuid?: string,
    evidence?: OverviewTraceEvidence
  ) => void
}) {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const { user } = useUser()
  const { currentSpace } = useSpace()
  const spaceId = currentSpace?.id ?? ""
  const [offset, setOffset] = useState(0)

  const query = useQuery<{
    items: DiagnosisRecordListItemDTO[]
    total: number
  }>({
    queryKey: [
      "diagnosis-records",
      spaceId,
      user?.user_uuid,
      category ?? "all",
      diagnoserUuid ?? "all",
      jobUuid ?? "nojob",
      ownerId,
      taskUuid ?? "latest",
      timeRange?.start_time,
      timeRange?.end_time,
      offset,
      refreshTick,
    ],
    queryFn: () =>
      listRecords(spaceId, {
        job_uuid: jobUuid,
        owner_id: ownerId,
        task_uuid: taskUuid,
        start_time: timeRange?.start_time,
        end_time: timeRange?.end_time,
        category,
        diagnoser_uuid: diagnoserUuid,
        verdict: "bad",
        limit: PAGE_SIZE,
        offset,
        sort_by: "diagnosed_at",
        sort_order: "desc",
      }),
    enabled: !!spaceId && !!user?.user_uuid && !!jobUuid && !!taskUuid,
    retry: false,
  })

  const records = query.isError ? [] : (query.data?.items ?? [])
  const total = query.data?.total ?? 0
  const currentPage = Math.floor(offset / PAGE_SIZE) + 1
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE))

  if (query.isLoading) {
    return (
      <div className="flex items-center justify-center py-12 text-sm text-muted-foreground">
        <Loader2 className="mr-2 size-4 animate-spin" />
        {t("common.loading")}
      </div>
    )
  }

  if (records.length === 0) {
    return null
  }

  return (
    <>
      <div className="overflow-hidden rounded-lg border bg-card">
        <table className="w-full table-fixed border-collapse text-sm">
          <thead className="sticky top-0 z-10">
            <tr>
              <th className="w-[24%] border-b border-border bg-[var(--color-bg-subtle)] px-3 py-2 text-left text-sm font-medium text-[var(--color-text-secondary)]">
                {t("diagnose.overview.traceColTraceId", {
                  defaultValue: "Trace ID",
                })}
              </th>
              <th className="w-[14%] border-b border-border bg-[var(--color-bg-subtle)] px-3 py-2 text-left text-sm font-medium text-[var(--color-text-secondary)]">
                {t("diagnose.overview.traceColDiagnoser", {
                  defaultValue: "诊断器",
                })}
              </th>
              <th className="border-b border-border bg-[var(--color-bg-subtle)] px-3 py-2 text-left text-sm font-medium text-[var(--color-text-secondary)]">
                {t("diagnose.overview.traceColSummary")}
              </th>
              <th className="w-[40px] border-b border-border bg-[var(--color-bg-subtle)] px-3 py-2" />
            </tr>
          </thead>
          <tbody>
            {records.map((record) => (
              <tr
                key={record.uuid}
                className="group h-[40px] cursor-pointer border-b border-[var(--color-border)] transition-colors last:border-b-0 hover:bg-accent/40"
                onClick={() =>
                  onOpenTrace
                    ? onOpenTrace(
                        record.target_id,
                        record.job_uuid,
                        record.uuid,
                        {
                          summary: record.summary,
                        }
                      )
                    : navigate(`/trace?traceId=${record.target_id}`)
                }
              >
                <td
                  className="max-w-0 px-3 py-1 align-middle"
                  title={record.target_id}
                >
                  <span className="block truncate text-sm text-[var(--color-text-primary)]">
                    {sanitizeQuestionPreview(record.target_id)}
                  </span>
                </td>
                <td className="px-3 py-1 align-middle">
                  <span
                    className="block truncate text-xs text-[var(--color-text-secondary)]"
                    title={
                      record.definition_source === "task_snapshot"
                        ? (record.definition?.name ?? record.diagnoser_uuid)
                        : record.diagnoser_uuid
                    }
                  >
                    {record.definition_source === "task_snapshot"
                      ? (record.definition?.name ?? record.diagnoser_uuid)
                      : record.diagnoser_uuid}
                  </span>
                </td>
                <td className="max-w-0 px-3 py-1 align-middle">
                  <span className="block truncate text-xs leading-5 text-[var(--color-text-description)]">
                    {record.summary}
                  </span>
                </td>
                <td className="px-3 py-1 text-right align-middle">
                  <ExternalLink className="ml-auto size-3.5 text-muted-foreground opacity-0 transition-opacity group-hover:opacity-100" />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="flex flex-wrap items-center justify-end gap-2 text-xs text-muted-foreground">
        <span>
          {t("diagnose.overview.tracePagination", {
            page: currentPage,
            pages: totalPages,
            count: total,
          })}
        </span>
        <Button
          variant="outline"
          size="sm"
          disabled={offset === 0}
          onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}
        >
          {t("common.prev", { defaultValue: "上一页" })}
        </Button>
        <Button
          variant="outline"
          size="sm"
          disabled={offset + PAGE_SIZE >= total}
          onClick={() => setOffset(offset + PAGE_SIZE)}
        >
          {t("common.next", { defaultValue: "下一页" })}
        </Button>
      </div>
    </>
  )
}
