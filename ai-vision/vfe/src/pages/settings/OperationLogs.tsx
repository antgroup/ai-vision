import { useEffect, useMemo, useState } from "react"
import { useQuery, keepPreviousData } from "@tanstack/react-query"
import { useTranslation } from "react-i18next"
import {
  Bot,
  ClipboardList,
  Eye,
  KeyRound,
  Search,
  Settings2,
  Users,
  type LucideIcon,
} from "lucide-react"

import { api } from "@/lib/api"
import { useSpace } from "@/contexts/SpaceContext"
import { getPresetTimeWindow } from "@/lib/timeFilter"
import {
  OperationLogsTimeFilter,
  type OperationLogsTimeValue,
} from "./OperationLogsTimeFilter"
import { Avatar, AvatarFallback, AvatarImage } from "@/components/ui/avatar"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Skeleton } from "@/components/ui/skeleton"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"

// ── Types ──

interface OperationLogItem {
  id: number
  time: string
  operator_name: string
  operator_uuid: string
  operator_avatar: string | null
  category: string
  category_display: string
  action: string
  target_display: string
  detail: string
}

interface OperationLogCategory {
  value: string
  label: string
}

interface OperationLogPagination {
  page: number
  pageSize: number
  total: number
  totalPages: number
}

interface OperationLogListResponse {
  code: number
  message?: string
  data?: {
    items: OperationLogItem[]
    categories: OperationLogCategory[]
    pagination: OperationLogPagination
  }
}

// ── Constants ──

const PAGE_SIZE = 20
const ALL_VALUE = "all"
const SEARCH_DEBOUNCE_MS = 350
const SEARCH_MAX_LEN = 100
const DEFAULT_TIME_RANGE = "3h"

function buildInitialTimeValue(): OperationLogsTimeValue {
  const window = getPresetTimeWindow(DEFAULT_TIME_RANGE)
  return {
    timeRange: DEFAULT_TIME_RANGE,
    isCustom: false,
    fromTimestamp: window.from,
    toTimestamp: window.to,
  }
}

interface CategoryStyle {
  icon: LucideIcon
  className: string
}

const CATEGORY_STYLES: Record<string, CategoryStyle> = {
  member_change: {
    icon: Users,
    className:
      "border-sky-200 bg-sky-50 text-sky-700 dark:border-sky-900 dark:bg-sky-950/40 dark:text-sky-300",
  },
  space_info_change: {
    icon: Settings2,
    className:
      "border-violet-200 bg-violet-50 text-violet-700 dark:border-violet-900 dark:bg-violet-950/40 dark:text-violet-300",
  },
  key_operation: {
    icon: KeyRound,
    className:
      "border-amber-200 bg-amber-50 text-amber-700 dark:border-amber-900 dark:bg-amber-950/40 dark:text-amber-300",
  },
  trace_desensitize_view: {
    icon: Eye,
    className:
      "border-cyan-200 bg-cyan-50 text-cyan-700 dark:border-cyan-900 dark:bg-cyan-950/40 dark:text-cyan-300",
  },
  agent_access: {
    icon: Bot,
    className:
      "border-emerald-200 bg-emerald-50 text-emerald-700 dark:border-emerald-900 dark:bg-emerald-950/40 dark:text-emerald-300",
  },
}

const FALLBACK_CATEGORY_STYLE: CategoryStyle = {
  icon: ClipboardList,
  className: "border-border bg-muted/40 text-muted-foreground",
}

function getCategoryStyle(category: string): CategoryStyle {
  return CATEGORY_STYLES[category] ?? FALLBACK_CATEGORY_STYLE
}

// ── Component ──

export default function OperationLogs() {
  const { t } = useTranslation()
  const { currentSpace } = useSpace()
  const spaceId = currentSpace?.id ?? ""

  const [category, setCategory] = useState<string>(ALL_VALUE)
  const [page, setPage] = useState(1)
  const [searchInput, setSearchInput] = useState("")
  const [searchTerm, setSearchTerm] = useState("")
  const [isComposing, setIsComposing] = useState(false)
  const [timeValue, setTimeValue] = useState<OperationLogsTimeValue>(
    buildInitialTimeValue
  )
  const { fromTimestamp, toTimestamp } = timeValue

  useEffect(() => {
    if (isComposing) return
    const next = searchInput.trim()
    if (next === searchTerm) return
    const timer = window.setTimeout(() => {
      setSearchTerm(next)
      setPage(1)
    }, SEARCH_DEBOUNCE_MS)
    return () => window.clearTimeout(timer)
  }, [searchInput, isComposing, searchTerm])

  const handleTimeChange = (value: OperationLogsTimeValue) => {
    setTimeValue(value)
    setPage(1)
  }

  const queryKey = useMemo(
    () =>
      [
        "space-operation-logs",
        spaceId,
        category,
        searchTerm,
        fromTimestamp,
        toTimestamp,
        page,
      ] as const,
    [spaceId, category, searchTerm, fromTimestamp, toTimestamp, page]
  )

  const { data, isLoading, isError, isFetching, refetch } = useQuery({
    queryKey,
    queryFn: () => {
      const params = new URLSearchParams({
        page: String(page),
        pageSize: String(PAGE_SIZE),
      })
      if (category && category !== ALL_VALUE) {
        params.set("category", category)
      }
      if (searchTerm) {
        params.set("keyword", searchTerm)
      }
      if (fromTimestamp) {
        params.set("from", fromTimestamp)
      }
      if (toTimestamp) {
        params.set("to", toTimestamp)
      }
      return api.get<OperationLogListResponse>(
        `/spaces/${spaceId}/operation-logs?${params.toString()}`
      )
    },
    enabled: !!spaceId,
    placeholderData: keepPreviousData,
    refetchOnMount: "always",
    refetchOnWindowFocus: true,
    staleTime: 0,
  })

  const payload = data?.code === 0 ? data.data : undefined
  const items = payload?.items ?? []
  const categories = payload?.categories ?? []
  const pagination = payload?.pagination
  const totalPages = Math.max(1, pagination?.totalPages ?? 1)
  const total = pagination?.total ?? 0

  const handleCategoryChange = (value: string) => {
    setCategory(value)
    setPage(1)
  }

  const getCategoryLabel = (value: string, fallback?: string) =>
    t(`spaceSettings.operationLogs.categories.${value}`, {
      defaultValue: fallback || value,
    })

  const getRoleLabel = (value: string) => {
    const roleKeys: Record<string, string> = {
      管理员: "admin",
      开发者: "developer",
      成员: "member",
    }
    const roleKey = roleKeys[value.trim()]
    return roleKey ? t(`spaceSettings.roles.${roleKey}`) : value.trim()
  }

  const getDetailLabel = (item: OperationLogItem) => {
    if (item.action === "change_role") {
      const [fromRole, toRole] = item.detail.split("->")
      if (fromRole && toRole) {
        return t("spaceSettings.operationLogs.details.changeRole", {
          from: getRoleLabel(fromRole),
          to: getRoleLabel(toRole),
        })
      }
    }

    if (item.action === "reveal_key") {
      return item.detail === "复制 Key"
        ? t("spaceSettings.operationLogs.details.copyKey")
        : t("spaceSettings.operationLogs.details.revealKey")
    }

    const detailKeys: Record<string, string> = {
      remove_member: "removeMember",
      member_leave: "memberLeave",
      update_space: "updateSpace",
      regenerate_key: "regenerateKey",
      create_agent: "createAgent",
      disable_agent: "disableAgent",
      enable_agent: "enableAgent",
    }
    const detailKey = detailKeys[item.action]
    return detailKey
      ? t(`spaceSettings.operationLogs.details.${detailKey}`)
      : item.detail || "-"
  }

  const getTargetLabel = (target: string) => {
    const targetKeys: Record<string, string> = {
      "上报 Key": "writeKey",
      "读取 Key": "readKey",
    }
    const targetKey = targetKeys[target]
    return targetKey
      ? t(`spaceSettings.operationLogs.targets.${targetKey}`)
      : target || "-"
  }

  // ── Loading ──
  if (isLoading) {
    return (
      <div className="space-y-4">
        <div className="flex items-center gap-3">
          <Skeleton className="h-9 w-44" />
        </div>
        <div className="overflow-hidden rounded-xl border bg-card">
          <div className="divide-y">
            {Array.from({ length: 5 }).map((_, i) => (
              <Skeleton key={i} className="h-14 w-full rounded-none" />
            ))}
          </div>
        </div>
      </div>
    )
  }

  // ── Error ──
  if (isError || data?.code !== 0) {
    return (
      <div className="flex flex-col items-center justify-center py-16 text-center">
        <p className="text-sm text-destructive">
          {data?.message || t("spaceSettings.operationLogs.loadFailed")}
        </p>
        <Button
          variant="outline"
          size="sm"
          className="mt-3"
          onClick={() => refetch()}
        >
          {t("common.retry")}
        </Button>
      </div>
    )
  }

  // ── Main ──
  return (
    <div className="space-y-4">
      {/* Toolbar */}
      <div className="flex flex-wrap items-center gap-3">
        <div className="relative w-64">
          <Search className="absolute top-1/2 left-2.5 size-4 -translate-y-1/2 text-muted-foreground" />
          <Input
            placeholder={t("spaceSettings.operationLogs.searchPlaceholder")}
            value={searchInput}
            onChange={(e) => setSearchInput(e.target.value)}
            onCompositionStart={() => setIsComposing(true)}
            onCompositionEnd={(e) => {
              setIsComposing(false)
              setSearchInput((e.target as HTMLInputElement).value)
            }}
            maxLength={SEARCH_MAX_LEN}
            className="h-9 pl-9 text-sm"
          />
        </div>
        <Select value={category} onValueChange={handleCategoryChange}>
          <SelectTrigger className="h-9 w-44 text-sm">
            <SelectValue
              placeholder={t("spaceSettings.operationLogs.categoryPlaceholder")}
            />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value={ALL_VALUE}>{t("common.all")}</SelectItem>
            {categories.map((opt) => (
              <SelectItem key={opt.value} value={opt.value}>
                {getCategoryLabel(opt.value, opt.label)}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <OperationLogsTimeFilter
          value={timeValue}
          onChange={handleTimeChange}
        />
        {isFetching && (
          <span className="text-xs text-muted-foreground">
            {t("common.loading")}
          </span>
        )}
      </div>

      {/* Table / Empty */}
      {items.length === 0 ? (
        <div className="flex flex-col items-center justify-center rounded-xl border bg-card py-16 text-center">
          <ClipboardList className="mb-3 size-10 text-muted-foreground/50" />
          <p className="text-sm text-muted-foreground">
            {searchTerm
              ? t("spaceSettings.operationLogs.noMatch")
              : category === ALL_VALUE
                ? t("spaceSettings.operationLogs.empty")
                : t("spaceSettings.operationLogs.emptyCategory")}
          </p>
        </div>
      ) : (
        <>
          <div className="overflow-hidden rounded-xl border bg-card">
            <Table className="w-full table-fixed">
              <TableHeader>
                <TableRow className="bg-muted/40 hover:bg-muted/40">
                  <TableHead
                    className="h-11 px-4 text-xs font-medium whitespace-nowrap text-muted-foreground"
                    style={{ width: 260 }}
                  >
                    {t("spaceSettings.operationLogs.time")}
                  </TableHead>
                  <TableHead
                    className="h-11 px-4 text-xs font-medium whitespace-nowrap text-muted-foreground"
                    style={{ width: 180 }}
                  >
                    {t("spaceSettings.operationLogs.operator")}
                  </TableHead>
                  <TableHead
                    className="h-11 px-4 text-xs font-medium whitespace-nowrap text-muted-foreground"
                    style={{ width: 200 }}
                  >
                    {t("spaceSettings.operationLogs.category")}
                  </TableHead>
                  <TableHead className="h-11 px-4 text-xs font-medium whitespace-nowrap text-muted-foreground">
                    {t("spaceSettings.operationLogs.target")}
                  </TableHead>
                  <TableHead className="h-11 px-4 text-xs font-medium text-muted-foreground">
                    {t("spaceSettings.operationLogs.detail")}
                  </TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {items.map((item) => {
                  const style = getCategoryStyle(item.category)
                  const Icon = style.icon
                  const avatarSrc = item.operator_avatar || ""
                  const initial = (item.operator_name || "U")
                    .slice(0, 1)
                    .toUpperCase()
                  return (
                    <TableRow key={item.id} className="h-14">
                      <TableCell className="px-4 font-mono text-xs whitespace-nowrap text-muted-foreground">
                        {item.time}
                      </TableCell>
                      <TableCell className="px-4 whitespace-nowrap">
                        <div className="flex items-center gap-2">
                          <Avatar className="size-7 shrink-0">
                            {avatarSrc && (
                              <AvatarImage
                                src={avatarSrc}
                                alt={item.operator_name}
                              />
                            )}
                            <AvatarFallback className="text-xs">
                              {initial}
                            </AvatarFallback>
                          </Avatar>
                          <span className="text-sm">
                            {item.operator_name || item.operator_uuid || "-"}
                          </span>
                        </div>
                      </TableCell>
                      <TableCell className="px-4 whitespace-nowrap">
                        <Badge
                          variant="outline"
                          className={`gap-1 font-normal ${style.className}`}
                        >
                          <Icon className="size-3" />
                          {getCategoryLabel(
                            item.category,
                            item.category_display
                          )}
                        </Badge>
                      </TableCell>
                      <TableCell className="px-4 text-sm break-words">
                        {getTargetLabel(item.target_display)}
                      </TableCell>
                      <TableCell className="px-4 text-sm break-words text-muted-foreground">
                        {getDetailLabel(item)}
                      </TableCell>
                    </TableRow>
                  )
                })}
              </TableBody>
            </Table>
          </div>

          {/* Pagination */}
          {totalPages > 1 && (
            <div className="flex items-center justify-between pt-2">
              <span className="text-xs text-muted-foreground">
                {t("spaceSettings.operationLogs.pagination", {
                  total,
                  page,
                  totalPages,
                })}
              </span>
              <div className="flex items-center gap-1">
                <Button
                  variant="outline"
                  size="sm"
                  disabled={page <= 1 || isFetching}
                  onClick={() => setPage((p) => Math.max(1, p - 1))}
                >
                  {t("common.prev")}
                </Button>
                <Button
                  variant="outline"
                  size="sm"
                  disabled={page >= totalPages || isFetching}
                  onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
                >
                  {t("common.next")}
                </Button>
              </div>
            </div>
          )}
        </>
      )}
    </div>
  )
}
