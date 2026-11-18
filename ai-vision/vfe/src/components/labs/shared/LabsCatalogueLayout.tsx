import type { ReactNode } from "react"
import { Loader2 } from "lucide-react"
import { useTranslation } from "react-i18next"

import { useAgent } from "@/contexts/AgentContext"
import { Button } from "@/components/ui/button"
import { LabsStatStrip, type LabsStatColumns } from "./LabsStatStrip"

export function LabsCatalogueHeading({
  title,
  counts,
  children,
}: {
  title: string
  counts: ReactNode
  children?: ReactNode
}) {
  return (
    <div className="flex flex-wrap items-center justify-between gap-2">
      <div className="flex min-w-0 flex-wrap items-baseline gap-x-3 gap-y-1">
        <h2 className="text-base font-medium">{title}</h2>
        {counts}
      </div>
      {children}
    </div>
  )
}

interface CatalogueQuery {
  data: unknown
  isError: boolean
  isFetching: boolean
  refetch: () => unknown
}

export type CatalogueRequest = {
  query: CatalogueQuery
  failure: ReactNode
}

export function LabsCatalogueResults({
  observations,
  configs,
  empty,
  children,
}: {
  observations?: CatalogueRequest
  configs: CatalogueRequest
  empty: ReactNode
  children: ReactNode
}) {
  const { t } = useTranslation()
  const {
    loading: agentsLoading,
    error: agentsError,
    refreshAgents,
  } = useAgent()
  const requests = observations ? [observations, configs] : [configs]
  const failed = requests.filter(
    ({ query }) => query.isError && !query.isFetching
  )
  const loading =
    agentsLoading ||
    requests.some(
      ({ query }) =>
        query.isFetching || (!query.isError && query.data === undefined)
    )
  const agentFailure = !observations && agentsError
  const hasError = Boolean(agentFailure || failed.length)

  return (
    <div className="flex min-h-24 flex-col gap-3" data-labs-catalogue-results>
      {children}
      {hasError || loading || !children ? (
        <div className="flex min-h-24 flex-col items-center justify-center gap-1 rounded-lg border border-dashed border-border px-4 py-4 text-center text-sm text-muted-foreground">
          {agentFailure ? (
            <div
              role="alert"
              className="flex flex-wrap items-center justify-center gap-2"
            >
              <span>{agentFailure}</span>
              <Button
                size="sm"
                variant="outline"
                onClick={() => void refreshAgents()}
              >
                {t("common.retry")}
              </Button>
            </div>
          ) : null}
          {failed.map(({ query, failure }, index) => (
            <div
              key={index}
              role="alert"
              className="flex flex-wrap items-center justify-center gap-2"
            >
              <span>{failure}</span>
              <Button
                size="sm"
                variant="outline"
                onClick={() => void query.refetch()}
              >
                {t("common.retry")}
              </Button>
            </div>
          ))}
          {loading ? (
            <p role="status" className="flex items-center gap-2">
              <Loader2
                className="size-4 motion-safe:animate-spin"
                aria-hidden="true"
              />
              {t("labs.common.loading")}
            </p>
          ) : !hasError && !children ? (
            empty
          ) : null}
        </div>
      ) : null}
    </div>
  )
}

export function LabsCatalogueOverview({
  columns = 5,
  loading,
  children,
}: {
  columns?: LabsStatColumns
  loading: boolean
  children: ReactNode
}) {
  return (
    <div
      className={
        columns === 4
          ? "min-h-[195px] lg:min-h-[98px]"
          : "min-h-73 sm:min-h-[195px] lg:min-h-[98px]"
      }
    >
      {children ?? (loading ? <LabsStatStrip columns={columns} /> : null)}
    </div>
  )
}
