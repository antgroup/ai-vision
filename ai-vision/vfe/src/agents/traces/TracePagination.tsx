import { useMemo } from "react"
import { useTranslation } from "react-i18next"
import { cn } from "@/lib/utils"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import {
  Pagination,
  PaginationContent,
  PaginationItem,
  PaginationLink,
  PaginationNext,
  PaginationPrevious,
  PaginationEllipsis,
} from "@/components/ui/pagination"

export function TracePagination({
  maxLimit = 100,
  page,
  totalPages,
  totalItems,
  limit,
  setPage,
  setLimit,
}: {
  maxLimit?: number
  page: number
  totalPages: number
  totalItems: number
  limit: number
  setPage: (page: number) => void
  setLimit: (limit: number) => void
}) {
  const { t } = useTranslation()
  const pageNumbers = useMemo(() => {
    const pages: (number | "ellipsis-start" | "ellipsis-end")[] = []
    if (totalPages <= 7) {
      for (let i = 1; i <= totalPages; i++) pages.push(i)
    } else {
      pages.push(1)
      if (page > 3) pages.push("ellipsis-start")
      for (
        let i = Math.max(2, page - 1);
        i <= Math.min(totalPages - 1, page + 1);
        i++
      )
        pages.push(i)
      if (page < totalPages - 2) pages.push("ellipsis-end")
      pages.push(totalPages)
    }
    return pages
  }, [page, totalPages])

  return (
    <div className="flex flex-wrap items-center justify-end gap-2 pt-4">
      <span className="mr-2 hidden text-sm whitespace-nowrap text-muted-foreground sm:inline">
        {t("common.total", { count: totalItems })}
      </span>
      <Pagination
        className="mx-0 w-auto max-w-full shrink-0"
        aria-label={t("dashboard.selectPage")}
      >
        <PaginationContent className="flex-wrap justify-end gap-0.5 sm:gap-1">
          <PaginationItem>
            <PaginationPrevious
              href="#"
              aria-label={t("common.prev")}
              aria-disabled={page <= 1}
              tabIndex={page <= 1 ? -1 : undefined}
              text=""
              size="icon"
              onClick={(e) => {
                e.preventDefault()
                if (page > 1) setPage(page - 1)
              }}
              className={cn(
                "size-8 p-0! sm:size-9",
                page <= 1 && "pointer-events-none opacity-50"
              )}
            />
          </PaginationItem>
          {pageNumbers.map((item) =>
            typeof item === "string" ? (
              <PaginationItem key={item}>
                <PaginationEllipsis className="size-8 sm:size-9" />
              </PaginationItem>
            ) : (
              <PaginationItem key={item}>
                <PaginationLink
                  href="#"
                  isActive={item === page}
                  className="h-8 w-auto min-w-8 px-1.5 tabular-nums sm:h-9 sm:min-w-9"
                  aria-label={t("common.page", { page: item })}
                  onClick={(e) => {
                    e.preventDefault()
                    setPage(item)
                  }}
                >
                  {item}
                </PaginationLink>
              </PaginationItem>
            )
          )}
          <PaginationItem>
            <PaginationNext
              href="#"
              aria-label={t("common.next")}
              aria-disabled={page >= totalPages}
              tabIndex={page >= totalPages ? -1 : undefined}
              text=""
              size="icon"
              onClick={(e) => {
                e.preventDefault()
                if (page < totalPages) setPage(page + 1)
              }}
              className={cn(
                "size-8 p-0! sm:size-9",
                page >= totalPages && "pointer-events-none opacity-50"
              )}
            />
          </PaginationItem>
        </PaginationContent>
      </Pagination>
      <Select
        value={String(limit)}
        onValueChange={(v) => {
          setLimit(Number(v))
        }}
      >
        <SelectTrigger size="sm" aria-label={t("dashboard.selectPageSize")}>
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          {[10, 20, 50, 100]
            .filter((n) => n <= maxLimit)
            .map((n) => (
              <SelectItem key={n} value={String(n)}>
                {t("common.perPage", { count: n })}
              </SelectItem>
            ))}
        </SelectContent>
      </Select>
    </div>
  )
}
