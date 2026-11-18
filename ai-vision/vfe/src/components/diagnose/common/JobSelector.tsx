import { useEffect } from "react"
import { useTranslation } from "react-i18next"
import { Loader2 } from "lucide-react"
import { useQuery } from "@tanstack/react-query"

import {
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectLabel,
  SelectSeparator,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { useUser } from "@/contexts/UserContext"
import { cn } from "@/lib/utils"

import { listJobs } from "../diagnoseApi"

function storageKey(spaceId: string, agentUuid: string): string {
  return `ai-vision:diagnose:last-job:${spaceId}:${agentUuid}`
}

function readRememberedJob(
  spaceId: string,
  agentUuid: string
): string | undefined {
  if (typeof window === "undefined" || !spaceId || !agentUuid) return undefined
  return (
    window.localStorage.getItem(storageKey(spaceId, agentUuid)) ?? undefined
  )
}

function rememberJob(
  spaceId: string,
  agentUuid: string,
  jobUuid: string
): void {
  if (typeof window === "undefined" || !spaceId || !agentUuid) return
  window.localStorage.setItem(storageKey(spaceId, agentUuid), jobUuid)
}

/** R1:Job 级主入口选择器。列该 agent 的 jobs;
 * 优先恢复当前 space + agent 上次选择的 job；无记录时选择 name="default"。 */
export function JobSelector({
  spaceId,
  agentUuid,
  value,
  onChange,
  className,
  disabled = false,
  refreshTick,
  groupByStatus = false,
  fullWidth = false,
  preserveAnchoredValue = false,
}: {
  spaceId: string
  agentUuid: string
  value: string | undefined
  onChange: (jobUuid: string | undefined) => void
  className?: string
  disabled?: boolean
  refreshTick?: number
  groupByStatus?: boolean
  fullWidth?: boolean
  preserveAnchoredValue?: boolean
}) {
  const { t } = useTranslation()
  const { user } = useUser()
  const { data, isLoading, isError } = useQuery({
    queryKey: [
      "diagnose-jobs",
      spaceId,
      agentUuid,
      refreshTick,
      user?.user_uuid,
    ],
    queryFn: () => listJobs(spaceId, { agent_uuid: agentUuid }),
    enabled: !!spaceId && !!agentUuid && !!user?.user_uuid,
    retry: false,
  })
  const jobs = isError ? [] : (data?.items ?? [])

  // 默认选择 name="default" 的 job
  const defaultJob = jobs.find((j) => j.name === "default")
  const valueExists = !!value && jobs.some((j) => j.uuid === value)
  const rememberedJob = readRememberedJob(spaceId, agentUuid)
  const rememberedValueExists =
    !!rememberedJob && jobs.some((j) => j.uuid === rememberedJob)
  const fallbackValue = rememberedValueExists
    ? rememberedJob
    : (defaultJob?.uuid ?? jobs[0]?.uuid)
  // A selected Task owns its Job; a lagging list cannot replace that anchor.
  const resolvedValue =
    value && (preserveAnchoredValue || valueExists) ? value : fallbackValue

  useEffect(() => {
    if (!isLoading && resolvedValue && value !== resolvedValue) {
      rememberJob(spaceId, agentUuid, resolvedValue)
      onChange(resolvedValue)
    }
  }, [agentUuid, isLoading, onChange, resolvedValue, spaceId, value])

  if (!isLoading && jobs.length === 0) return null

  const selectJob = (jobUuid: string) => {
    rememberJob(spaceId, agentUuid, jobUuid)
    onChange(jobUuid)
  }

  const statusGroups = [
    {
      status: "enabled" as const,
      label: t("common.enabled"),
      items: jobs.filter((job) => job.status === "enabled"),
    },
    {
      status: "disabled" as const,
      label: t("common.disabled"),
      items: jobs.filter((job) => job.status === "disabled"),
    },
  ].filter((group) => group.items.length > 0)

  return (
    <div className={className}>
      {isLoading ? (
        <Loader2 className="size-3 animate-spin text-muted-foreground" />
      ) : (
        <Select
          value={resolvedValue}
          disabled={disabled}
          onValueChange={selectJob}
        >
          <SelectTrigger
            className={cn(
              "h-8 max-w-full text-xs font-normal",
              fullWidth ? "w-full" : "w-[200px]"
            )}
          >
            <span className="truncate">
              {t("diagnose.jobSelector.label")}：<SelectValue />
            </span>
          </SelectTrigger>
          <SelectContent
            position="popper"
            className={
              fullWidth ? "w-(--radix-select-trigger-width)" : "w-[200px]"
            }
          >
            {groupByStatus
              ? statusGroups.map((group, groupIndex) => (
                  <SelectGroup key={group.status}>
                    {groupIndex > 0 ? <SelectSeparator /> : null}
                    <SelectLabel>{group.label}</SelectLabel>
                    {group.items.map((job) => (
                      <SelectItem key={job.uuid} value={job.uuid}>
                        {job.name || job.uuid}
                      </SelectItem>
                    ))}
                  </SelectGroup>
                ))
              : jobs.map((job) => (
                  <SelectItem key={job.uuid} value={job.uuid}>
                    {job.name || job.uuid}
                  </SelectItem>
                ))}
          </SelectContent>
        </Select>
      )}
    </div>
  )
}
