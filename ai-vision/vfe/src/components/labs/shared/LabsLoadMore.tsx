import { useState, type ReactNode } from "react"
import { ChevronDown } from "lucide-react"
import { useTranslation } from "react-i18next"

import { Button } from "@/components/ui/button"

const PAGE_SIZE = 20

/** Reveal bounded observation details without repeating the aggregate scan. */
export function LabsLoadMore<T>({
  items,
  children,
}: {
  items: T[]
  children: (visible: T[]) => ReactNode
}) {
  const { t } = useTranslation()
  const [page, setPage] = useState({ items, count: PAGE_SIZE })
  // A new result (scope, entity or refresh) starts with the first batch.
  const count = page.items === items ? page.count : PAGE_SIZE
  return (
    <div className="flex min-w-0 flex-col gap-3">
      {children(items.slice(0, count))}
      {count < items.length ? (
        <div className="flex justify-center border-t border-[var(--color-border)] pt-3">
          <Button
            type="button"
            variant="outline"
            size="sm"
            className="border-[var(--color-border-labs)] text-[var(--color-labs)] hover:bg-[var(--color-bg-labs-tag)] hover:text-[var(--color-labs)] dark:hover:bg-[var(--color-bg-labs-tag)]"
            onClick={() => setPage({ items, count: count + PAGE_SIZE })}
          >
            {t("common.loadMore")}
            <ChevronDown className="size-3.5" aria-hidden="true" />
          </Button>
        </div>
      ) : null}
    </div>
  )
}
