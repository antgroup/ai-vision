import { useCallback, useMemo, useState } from "react"
import { useTranslation } from "react-i18next"

import type { TableColumn } from "@/lib/schemas/observability"

export function useTableControls(
  rows: Record<string, unknown>[],
  columns: TableColumn[],
  initialPageSize = 10,
  initialShowAll = false,
  defaultSortKey?: string,
) {
  const { t } = useTranslation()
  const standardPageSizes = [5, 10, 20, 50]
  const safeInitialPageSize = standardPageSizes.includes(initialPageSize)
    ? initialPageSize
    : 10
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(safeInitialPageSize)
  const [showAll, setShowAll] = useState(initialShowAll)
  const [sortKey, setSortKey] = useState<string | null>(defaultSortKey ?? null)
  const [sortDirection, setSortDirection] = useState<"asc" | "desc">("desc")
  const [searchText, setSearchText] = useState("")

  const sortableKeys = useMemo(
    () =>
      new Set(
        columns
          .filter(
            (c) =>
              c.data_type === "datetime" ||
              c.data_type === "number" ||
              c.data_type === "duration" ||
              c.key === "time" ||
              c.key === "timestamp" ||
              c.key === "cost_ms" ||
              c.key.endsWith("_tokens"),
          )
          .map((c) => c.key),
      ),
    [columns],
  )

  const handleSort = useCallback(
    (key: string) => {
      if (sortKey === key) {
        setSortDirection((prev) => (prev === "asc" ? "desc" : "asc"))
      } else {
        setSortKey(key)
        setSortDirection("desc")
      }
      setPage(1)
    },
    [sortKey, setPage],
  )

  const sortedRows = useMemo(() => {
    if (!sortKey) return rows
    return [...rows].sort((a, b) => {
      const comparison = String(a[sortKey] ?? "").localeCompare(
        String(b[sortKey] ?? ""),
        undefined,
        { numeric: true },
      )
      return sortDirection === "asc" ? comparison : -comparison
    })
  }, [rows, sortDirection, sortKey])

  const filteredRows = useMemo(() => {
    if (!searchText) return sortedRows
    const lower = searchText.toLowerCase()
    return sortedRows.filter((row) =>
      columns.some((col) => {
        const val = row[col.key]
        return (
          val !== null &&
          val !== undefined &&
          String(val).toLowerCase().includes(lower)
        )
      }),
    )
  }, [sortedRows, searchText, columns])

  const totalPages = Math.max(1, Math.ceil(filteredRows.length / pageSize))
  const boundedPage = Math.min(page, totalPages)
  const visibleRows = showAll
    ? filteredRows
    : filteredRows.slice((boundedPage - 1) * pageSize, boundedPage * pageSize)

  const onSearchChange = useCallback(
    (text: string) => {
      setSearchText(text)
      setPage(1)
    },
    [setPage],
  )

  return {
    rows: visibleRows,
    total: filteredRows.length,
    searchText,
    onSearchChange,
    page: String(boundedPage),
    pageSize: String(pageSize),
    pageOptions: Array.from({ length: totalPages }, (_, i) => ({
      label: t("common.page", { page: i + 1 }),
      value: String(i + 1),
    })),
    pageSizeOptions: standardPageSizes.map((n) => ({
      label: t("common.perPage", { count: n }),
      value: String(n),
    })),
    showAll,
    sortKey,
    sortDirection,
    sortableKeys,
    onSort: handleSort,
    onPageChange: (value: string) => setPage(Number(value) || 1),
    onPageSizeChange: (value: string) => {
      setPage(1)
      setPageSize(Number(value) || safeInitialPageSize)
      setShowAll(false)
    },
    onShowAllToggle: () => {
      setShowAll((prev) => !prev)
      setPage(1)
    },
  }
}
