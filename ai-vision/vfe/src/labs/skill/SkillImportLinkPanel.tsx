import { useEffect, useRef, useState } from "react"
import { useQuery, useQueryClient } from "@tanstack/react-query"
import { Loader2, Link2 } from "lucide-react"
import { useTranslation } from "react-i18next"

import { Button } from "@/components/ui/button"
import { Checkbox } from "@/components/ui/checkbox"
import { Input } from "@/components/ui/input"

import { importDialogStyles } from "../../components/labs/shared/importDialogStyles"
import { ImportLookupStatus } from "../../components/labs/shared/ImportLookupStatus"
import { resolveSkillRepo } from "./skillApi"
import type { RepoSkillEntry } from "./skillApi"
import { installRepoSkills, skillResolveErrorKey } from "./skillImportRemote"
import type { RemoteInstallResult } from "./skillImportRemote"

interface SkillImportLinkPanelProps {
  spaceId: string
  onClose: () => void
}

/** 链接导入：解析 GitHub 仓库里的全部 SKILL.md，勾选后取回全文并走共享
 * 解析与保存链路。解析由 seed 驱动的 useQuery 承担，默认全选用「排除集」
 * 表达，避免数据到达后再初始化选中状态。 */
export function SkillImportLinkPanel({
  spaceId,
  onClose,
}: SkillImportLinkPanelProps) {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const active = useRef(true)
  useEffect(() => {
    active.current = true
    return () => {
      active.current = false
    }
  }, [])
  const [url, setUrl] = useState("")
  // The seed is the requested URL; empty means "not resolved yet".
  const [seed, setSeed] = useState("")
  // Deselected paths — an empty set is the default "all selected".
  const [deselected, setDeselected] = useState<Set<string>>(new Set())
  const [installing, setInstalling] = useState(false)
  const [installError, setInstallError] = useState("")
  const [installResult, setInstallResult] =
    useState<RemoteInstallResult | null>(null)

  const resolveQuery = useQuery({
    queryKey: ["labs", "skill", "registry", "resolve", spaceId, seed],
    queryFn: () => resolveSkillRepo(spaceId, { url: seed }),
    enabled: !!seed,
    retry: false,
  })
  const resolving = resolveQuery.isPending && !!seed
  const resolveErrorKey = skillResolveErrorKey(resolveQuery.error)

  const resolve = () => {
    const target = url.trim()
    if (!target || resolving) return
    setDeselected(new Set())
    setInstallResult(null)
    setInstallError("")
    if (target === seed) void resolveQuery.refetch()
    else setSeed(target)
  }

  const entries = resolveQuery.data?.skills ?? []
  const selectedEntries = entries.filter((entry) => !deselected.has(entry.path))
  const allSelected =
    entries.length > 0 && selectedEntries.length === entries.length

  const toggle = (entry: RepoSkillEntry, checked: boolean) => {
    if (installResult) return
    setDeselected((current) => {
      const next = new Set(current)
      if (checked) next.delete(entry.path)
      else next.add(entry.path)
      return next
    })
  }

  const install = async () => {
    if (!resolveQuery.data || !selectedEntries.length || installing) return
    setInstalling(true)
    setInstallError("")
    try {
      const result = await installRepoSkills(
        spaceId,
        resolveQuery.data.source,
        selectedEntries,
        resolveQuery.data.branch,
        installResult ?? undefined
      )
      await queryClient.invalidateQueries({
        queryKey: ["labs", "skill", "configs", spaceId],
      })
      if (!active.current) return
      setInstallResult(result)
      if (
        result.skipped.length ||
        result.pendingConfigs.length ||
        result.failedEntries.length
      ) {
        setInstallError(
          t("labs.skill.topic.installResult", {
            saved: result.saved,
            failed: result.pendingConfigs.length + result.failedEntries.length,
            skipped: result.skipped.length,
          })
        )
        return
      }
      onClose()
    } catch (error) {
      if (!active.current) return
      setInstallError(
        error instanceof Error && error.message
          ? error.message
          : t("labs.skill.import.link.installFailed")
      )
    } finally {
      if (active.current) setInstalling(false)
    }
  }

  return (
    <div className={importDialogStyles.panel}>
      <div className={importDialogStyles.toolbar}>
        <div className="flex items-center gap-2">
          <div className="relative min-w-0 flex-1">
            <Link2 className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-[var(--color-text-description)]" />
            <Input
              value={url}
              onChange={(event) => setUrl(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter") resolve()
              }}
              placeholder={t("labs.skill.import.link.placeholder")}
              aria-label={t("labs.skill.import.tab.link")}
              spellCheck={false}
              disabled={resolving || installing || !!installResult}
              className={`${importDialogStyles.field} pl-9`}
            />
          </div>
          <Button
            variant="outline"
            className={importDialogStyles.action}
            disabled={!url.trim() || resolving || installing}
            onClick={resolve}
          >
            {resolving ? <Loader2 className="size-4 animate-spin" /> : null}
            {resolving
              ? t("labs.skill.import.link.resolving")
              : t("labs.skill.import.link.resolve")}
          </Button>
        </div>

        {entries.length ? (
          <div className="flex flex-wrap items-center justify-between gap-3 text-sm leading-[22px]">
            <label className="flex min-w-0 items-center gap-2 text-[var(--color-text-primary)]">
              <Checkbox
                checked={allSelected}
                disabled={installing || !!installResult}
                onCheckedChange={(checked) =>
                  setDeselected(
                    checked
                      ? new Set()
                      : new Set(entries.map((entry) => entry.path))
                  )
                }
                aria-label={t("labs.skill.import.link.selectAll")}
              />
              {t("labs.skill.import.link.selectAll")}
            </label>
            <span className="min-w-0 text-xs leading-5 break-words text-[var(--color-text-description)]">
              {t("labs.skill.import.link.skillsFound", {
                count: entries.length,
                branch: resolveQuery.data?.branch ?? "",
              })}
            </span>
          </div>
        ) : null}
      </div>

      <div className={importDialogStyles.results}>
        {resolveQuery.isError ? (
          <p
            role="alert"
            className="text-xs leading-5 text-[var(--color-error)]"
          >
            {t(resolveErrorKey ?? "labs.skill.import.link.failed")}
          </p>
        ) : null}

        <ImportLookupStatus
          icon={Link2}
          message={
            !seed
              ? t("labs.skill.import.link.hint")
              : !resolveQuery.isFetching &&
                  !resolveQuery.isError &&
                  resolveQuery.data &&
                  !entries.length
                ? t("labs.skill.import.link.empty")
                : null
          }
        />

        {entries.length ? (
          <div className="flex flex-col gap-3">
            <ul className="flex flex-col divide-y divide-[var(--color-border)] rounded-[12px] border border-[var(--color-border)]">
              {entries.map((entry) => (
                <li key={entry.path}>
                  <label className="flex cursor-pointer items-start gap-3 px-3.5 py-3">
                    <Checkbox
                      disabled={installing || !!installResult}
                      className="mt-1 shrink-0"
                      checked={!deselected.has(entry.path)}
                      onCheckedChange={(checked) =>
                        toggle(entry, checked === true)
                      }
                    />
                    <span className="flex min-w-0 flex-col gap-0.5">
                      <span className="truncate text-sm leading-[22px] font-medium text-[var(--color-text-primary)]">
                        {entry.name || entry.dirname || entry.path}
                      </span>
                      {entry.description ? (
                        <span className="line-clamp-2 text-xs leading-5 text-[var(--color-text-description)]">
                          {entry.description}
                        </span>
                      ) : null}
                      <span className="truncate font-mono text-xs leading-5 text-[var(--color-text-secondary)]">
                        {entry.path}
                      </span>
                    </span>
                  </label>
                </li>
              ))}
            </ul>

            {installResult?.skipped.length ? (
              <p className="text-xs leading-5 [overflow-wrap:anywhere] text-[var(--color-text-description)]">
                {t("labs.skill.import.link.skipped", {
                  names: installResult.skipped.join(", "),
                })}
              </p>
            ) : null}
            {installResult?.errors.length ? (
              <details className="text-xs leading-5 text-[var(--color-text-description)]">
                <summary>{t("metrics.errorDetails")}</summary>
                <pre className="break-all whitespace-pre-wrap">
                  {installResult.errors.join("\n")}
                </pre>
              </details>
            ) : null}
            {installError ? (
              <p
                role="alert"
                className="text-xs leading-5 text-[var(--color-error)]"
              >
                {installError || t("labs.skill.import.link.installFailed")}
              </p>
            ) : null}
          </div>
        ) : null}
      </div>

      {entries.length ? (
        <div className="flex shrink-0 justify-end">
          <Button
            className={importDialogStyles.primary}
            disabled={
              !selectedEntries.length ||
              installing ||
              (!!installResult &&
                !installResult.pendingConfigs.length &&
                !installResult.failedEntries.length)
            }
            onClick={() => void install()}
          >
            {installing ? <Loader2 className="size-4 animate-spin" /> : null}
            {installing
              ? t("labs.skill.import.link.installing")
              : installResult
                ? t("labs.skill.topic.retryFailed")
                : t("labs.skill.import.link.install", {
                    count: selectedEntries.length,
                  })}
          </Button>
        </div>
      ) : null}
    </div>
  )
}
