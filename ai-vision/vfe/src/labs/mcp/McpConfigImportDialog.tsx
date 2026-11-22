import { useEffect, useMemo, useRef, useState, type ChangeEvent } from "react"
import { useQueryClient } from "@tanstack/react-query"
import { AlertTriangle, Loader2, Lock, FileJson } from "lucide-react"
import { useTranslation } from "react-i18next"
import { toast } from "sonner"

import { Button } from "@/components/ui/button"
import { importDialogStyles } from "../../components/labs/shared/importDialogStyles"
import { LocalImportConflicts } from "@/components/labs/shared/LocalImportConflicts"
import { LocalImportBatchCollision } from "@/components/labs/shared/localImportBatchCollision"
import {
  fingerprintImportValue,
  useLocalImportReview,
} from "@/components/labs/shared/localImportReview"
import {
  Dialog,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { DialogContent } from "@/components/dialogs/DialogContent"
import { Textarea } from "@/components/ui/textarea"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { McpImportRegistryPanel } from "./McpImportRegistryPanel"
import { cn } from "@/lib/utils"
import { useSpace } from "@/contexts/SpaceContext"

import {
  checkMcpImportConflicts,
  saveMcpConfigBatch,
  saveMcpConfigs,
  syncMcpConfigTools,
} from "./mcpApi"
import { parseMcpConfigJson } from "./mcpConfigImport"
import {
  mcpConfigBatches,
  readMcpConfigFiles,
  type McpLocalImportResult,
} from "./mcpLocalImport"
import type { RegistryImportPreview } from "./mcpRegistryImport"
import { launchLine } from "./mcpDisplay"
import type { McpServerConfig } from "./types"

interface McpConfigImportDialogProps {
  feature?: "mcp" | "memory"
  open: boolean
  onOpenChange: (open: boolean) => void
  spaceId: string
  observedServerIds: string[]
  targetServerId?: string
  onImported?: (configs: McpServerConfig[]) => void
}

export function McpConfigImportDialog(props: McpConfigImportDialogProps) {
  const { currentSpace } = useSpace()
  const canManage =
    currentSpace?.role === 1 && currentSpace.id === props.spaceId
  // Never retain a pasted secret after closing or switching to another space.
  return props.open && canManage ? (
    <McpConfigImportForm
      key={JSON.stringify([props.spaceId, props.targetServerId])}
      {...props}
    />
  ) : null
}

function McpConfigImportForm({
  feature = "mcp",
  open,
  onOpenChange,
  spaceId,
  observedServerIds,
  targetServerId,
  onImported,
}: McpConfigImportDialogProps) {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const discovery = feature === "mcp" && !targetServerId
  const [tab, setTab] = useState(discovery ? "search" : "paste")
  const [text, setText] = useState("")
  const [localImport, setLocalImport] = useState<McpLocalImportResult | null>(
    null
  )
  const [readingFiles, setReadingFiles] = useState(false)
  const [savedCount, setSavedCount] = useState(0)
  const filesInput = useRef<HTMLInputElement>(null)
  const pasteInput = useRef<HTMLTextAreaElement>(null)
  const [registryPreview, setRegistryPreview] =
    useState<RegistryImportPreview | null>(null)
  const [saving, setSaving] = useState(false)
  const [saveError, setSaveError] = useState(false)
  const [syncing, setSyncing] = useState(false)
  const [syncFailures, setSyncFailures] = useState<string[]>([])
  const active = useRef(true)
  const dismissedWhileSaving = useRef(false)
  useEffect(() => {
    active.current = true
    return () => {
      active.current = false
    }
  }, [])

  const result = useMemo(() => {
    if (localImport || !text.trim()) return null
    const parsed = parseMcpConfigJson(text)
    if (typeof parsed === "string") return parsed
    return {
      ...parsed,
      headerValuesByServerId:
        registryPreview?.headerValuesByServerId ??
        parsed.headerValuesByServerId,
    }
  }, [text, registryPreview, localImport])
  const errorFeature = result === "noServers" ? feature : "mcp"
  const localResult =
    localImport && "candidates" in localImport ? localImport : null
  const localCandidates = useMemo(
    () =>
      localResult?.candidates.map(({ source, config, headerValues }) => ({
        name: config.serverId,
        source,
        detail: launchLine(config).slice(0, 160),
        value: { config, headerValues },
        fingerprint: fingerprintImportValue({
          config: {
            ...config,
            serverId: undefined,
            importedAt: undefined,
            envKeys: [...config.envKeys].sort(),
            headerKeys: [...config.headerKeys].sort(),
          },
          headerValues,
        }),
      })) ?? null,
    [localResult]
  )
  const review = useLocalImportReview(
    "mcp",
    spaceId,
    localCandidates,
    checkMcpImportConflicts
  )
  const reviewedConfigs = review.entries.map(({ name, value }) => ({
    ...value.config,
    serverId: name,
  }))
  const parsed = localImport
    ? localResult && {
        configs: reviewedConfigs,
        headerValuesByServerId: Object.fromEntries(
          review.entries.flatMap(({ name, value }) =>
            value.headerValues ? [[name, value.headerValues] as const] : []
          )
        ),
        skipped: [],
      }
    : result && typeof result !== "string"
      ? result
      : null
  const previewConfigs = localImport
    ? reviewedConfigs.filter((_, index) => !review.entries[index].conflict)
    : (parsed?.configs ?? [])
  const syncReport = syncFailures.length > 0 && !localImport
  const localFileView = readingFiles || localImport !== null
  const compact = localFileView || syncing || syncReport
  const showTabs =
    discovery && !syncing && !syncReport && (!saving || localFileView)
  const observed = useMemo(
    () => new Set(observedServerIds),
    [observedServerIds]
  )
  const finishing = savedCount > 0 && review.canFinishWithoutSaving
  const canSave =
    (!!parsed?.configs.length || finishing) &&
    !readingFiles &&
    !saving &&
    (!localImport || (review.status === "ready" && !review.needsDecision))

  const close = (next: boolean) => {
    if (!next) {
      active.current = false
      dismissedWhileSaving.current = saving && !syncing
      setText("")
      setRegistryPreview(null)
      setLocalImport(null)
      setSaveError(false)
    }
    onOpenChange(next)
  }

  const readFiles = async (event: ChangeEvent<HTMLInputElement>) => {
    const files = [...(event.currentTarget.files ?? [])]
    event.currentTarget.value = ""
    if (!files.length) return
    setTab("paste")
    setReadingFiles(true)
    setSaveError(false)
    setSavedCount(0)
    setSyncFailures([])
    try {
      const selection = await readMcpConfigFiles(files)
      if (active.current) setLocalImport(selection)
    } finally {
      if (active.current) setReadingFiles(false)
    }
  }

  const focusPaste = () =>
    requestAnimationFrame(() => pasteInput.current?.focus())

  const resetLocalImport = () => {
    setLocalImport(null)
    setSaveError(false)
    setSavedCount(0)
    setSyncFailures([])
  }

  const save = async () => {
    if (!canSave || !parsed) return
    if (finishing) {
      if (syncFailures.length) setLocalImport(null)
      else close(false)
      return
    }
    setSaving(true)
    setSaveError(false)
    try {
      const { configs, headerValuesByServerId } = parsed
      const saved: { config: McpServerConfig; requestedId: string }[] = []
      const confirmedIds = new Set<string>()
      let batchCollision: LocalImportBatchCollision | undefined
      if (!localResult) {
        const importedIds = new Set(configs.map((config) => config.serverId))
        const receipts = await saveMcpConfigs(spaceId, configs)
        for (const config of receipts) {
          if (importedIds.has(config.serverId))
            saved.push({ config, requestedId: config.serverId })
        }
      }
      const overwrites = new Map(
        review.entries.flatMap(({ name, overwrite }) =>
          overwrite ? [[name, { ...overwrite, name }] as const] : []
        )
      )
      for (const batch of localResult
        ? mcpConfigBatches(configs, overwrites)
        : []) {
        if (!active.current) break
        const submitted = new Map(
          batch.map((config) => [config.serverId, config])
        )
        try {
          const approved = batch.flatMap(({ serverId }) => {
            const overwrite = overwrites.get(serverId)
            return overwrite ? [overwrite] : []
          })
          for (const receipt of await saveMcpConfigBatch(
            spaceId,
            batch,
            approved
          )) {
            const config = submitted.get(receipt.requestedServerId)
            if (config && !confirmedIds.has(receipt.requestedServerId)) {
              saved.push({
                config: {
                  ...config,
                  serverId: receipt.serverId,
                  importedAt: receipt.importedAt,
                },
                requestedId: receipt.requestedServerId,
              })
              confirmedIds.add(receipt.requestedServerId)
            }
          }
          if (batch.some((config) => !confirmedIds.has(config.serverId))) break
        } catch (error) {
          if (
            error instanceof LocalImportBatchCollision &&
            error.matchesBatch([...submitted.keys()])
          )
            batchCollision = error
          break
        }
      }
      const savedConfigs = saved.map(({ config }) => config)
      const pending = localResult
        ? configs.filter((config) => !confirmedIds.has(config.serverId))
        : []
      if (active.current && localResult && pending.length) {
        review.markSaved(confirmedIds)
        setSavedCount((count) => count + savedConfigs.length)
        setSaveError(!review.reportBatchCollision(batchCollision))
        void review.refresh()
        if (!savedConfigs.length) return
      }
      await Promise.all([
        queryClient.invalidateQueries({
          queryKey: ["labs", "mcp", "configs", spaceId],
        }),
        queryClient.invalidateQueries({
          queryKey: ["labs", "mcp", "config-tools", spaceId],
        }),
        queryClient.invalidateQueries({
          queryKey: ["labs", "mcp", "servers"],
        }),
        queryClient.invalidateQueries({
          queryKey: ["labs", "memory", "observations"],
        }),
      ])
      if (!active.current) {
        const count = savedCount + savedConfigs.length
        if (localResult && dismissedWhileSaving.current && count > 0)
          toast.info(t("labs.common.importCancelledSaved", { count }))
        return
      }
      // Bring the tool list along on import. Pasted header values are used
      // for this one sync and never stored; stdio requires deployment opt-in
      // and remains a manual action. Use the returned version, not local time.
      onImported?.(savedConfigs)
      const autoTargets = saved.filter(
        ({ config }) => config.transport !== "stdio"
      )
      const failed: string[] = []
      if (autoTargets.length > 0) {
        if (active.current) {
          if (!localResult) setText("")
          setRegistryPreview(null)
          if (!pending.length) setLocalImport(null)
          setSyncing(true)
        }
        // Local imports can cover hundreds of servers. Use a small sync pool.
        let nextTarget = 0
        const sync = async () => {
          while (active.current && nextTarget < autoTargets.length) {
            const { config, requestedId } = autoTargets[nextTarget++]
            try {
              await syncMcpConfigTools(
                spaceId,
                config.serverId,
                Object.hasOwn(headerValuesByServerId, requestedId)
                  ? headerValuesByServerId[requestedId]
                  : undefined,
                config.importedAt
              )
            } catch {
              failed.push(config.serverId)
            }
          }
        }
        await Promise.all(
          Array.from({ length: Math.min(autoTargets.length, 4) }, sync)
        )
        await queryClient.invalidateQueries({
          queryKey: ["labs", "mcp", "config-tools", spaceId],
        })
      }
      if (active.current && (syncFailures.length || failed.length)) {
        if (!pending.length) setLocalImport(null)
        setSyncFailures([...syncFailures, ...failed])
      }
      if (pending.length || syncFailures.length || failed.length) return
      if (active.current) close(false)
    } catch {
      if (active.current) setSaveError(true)
    } finally {
      if (active.current) {
        setSaving(false)
        setSyncing(false)
      }
    }
  }

  return (
    <Dialog open={open} onOpenChange={close}>
      <DialogContent
        size={compact ? "detail" : "workspace"}
        fillHeight={!compact && tab !== "search"}
        className={cn(
          importDialogStyles.dialog,
          tab === "search" && importDialogStyles.fitWorkspace
        )}
        data-product-guide="mcp-import"
        data-guide-state={parsed ? "ready" : "empty"}
        data-guide-mode={tab}
      >
        <DialogHeader className={importDialogStyles.header}>
          <DialogTitle className={importDialogStyles.title}>
            <span className={importDialogStyles.titleIcon}>
              <FileJson className="size-4" aria-hidden="true" />
            </span>
            {t(`labs.${feature}.import.title`)}
          </DialogTitle>
          <DialogDescription
            className={compact ? "sr-only" : importDialogStyles.description}
          >
            {t(
              localImport
                ? "labs.common.importConflicts.localDescription"
                : tab === "search"
                  ? "labs.mcp.import.registry.hint"
                  : feature === "mcp" && !registryPreview
                    ? "labs.mcp.import.pasteDesc"
                    : `labs.${feature}.import.desc`
            )}
          </DialogDescription>
        </DialogHeader>

        <Tabs
          value={tab}
          onValueChange={(next) => {
            if (next === tab || readingFiles) return
            // Discard registry text with its metadata; retaining the text alone
            // would allow a later save without explicit secret redaction.
            if (registryPreview) setText("")
            setRegistryPreview(null)
            resetLocalImport()
            setTab(next)
          }}
          className={importDialogStyles.tabs}
        >
          {showTabs ? (
            <div className="flex shrink-0 flex-col items-start gap-2 sm:flex-row sm:items-center sm:justify-between">
              <TabsList variant="line" className={importDialogStyles.tabList}>
                <TabsTrigger
                  value="search"
                  disabled={readingFiles || saving}
                  className={importDialogStyles.tab}
                >
                  {t("labs.mcp.import.tabs.search")}
                </TabsTrigger>
                <TabsTrigger
                  value="paste"
                  disabled={readingFiles || saving}
                  className={importDialogStyles.tab}
                >
                  {t("labs.mcp.import.tabs.paste")}
                </TabsTrigger>
              </TabsList>
              {!registryPreview ? (
                <div className="flex flex-wrap gap-2 sm:justify-end">
                  <input
                    ref={filesInput}
                    type="file"
                    aria-label={t("labs.mcp.import.local.choose")}
                    accept=".json,application/json"
                    multiple
                    hidden
                    onChange={(event) => void readFiles(event)}
                  />
                  <Button
                    type="button"
                    variant="outline"
                    className={cn(
                      importDialogStyles.button,
                      localFileView && importDialogStyles.localToolbarButton
                    )}
                    disabled={readingFiles || saving}
                    onClick={() => filesInput.current?.click()}
                  >
                    {t("labs.mcp.import.local.choose")}
                  </Button>
                  {localImport ? (
                    <Button
                      type="button"
                      variant="ghost"
                      className={cn(
                        importDialogStyles.ghost,
                        importDialogStyles.localToolbarButton
                      )}
                      disabled={readingFiles || saving}
                      onClick={() => {
                        resetLocalImport()
                        focusPaste()
                      }}
                    >
                      {t("labs.mcp.import.local.backToPaste")}
                    </Button>
                  ) : null}
                </div>
              ) : null}
            </div>
          ) : null}
          {showTabs ? (
            <TabsContent value="search" className={importDialogStyles.panel}>
              <McpImportRegistryPanel
                spaceId={spaceId}
                onPreview={(preview) => {
                  setText(preview.json)
                  setRegistryPreview(preview)
                  setSaveError(false)
                  setTab("paste")
                  focusPaste()
                }}
              />
            </TabsContent>
          ) : null}
          {syncing ? (
            <p
              role="status"
              className="flex items-center gap-2 text-sm text-[var(--color-text-secondary)]"
            >
              <Loader2
                className="size-4 shrink-0 animate-spin"
                aria-hidden="true"
              />
              {t("labs.mcp.import.syncing")}
            </p>
          ) : syncReport ? (
            <div
              role="alert"
              className="flex min-h-0 items-start gap-2 overflow-y-auto rounded-[10px] border border-[var(--color-border)] bg-[var(--color-bg-subtle)] p-3 text-sm leading-[22px]"
            >
              <AlertTriangle
                className="mt-0.5 size-4 shrink-0 text-[var(--color-warning)]"
                aria-hidden="true"
              />
              <div className="min-w-0 space-y-2">
                <p className="font-medium text-[var(--color-text-primary)]">
                  {t(`labs.${feature}.import.syncFailedTitle`)}
                </p>
                <ul className="list-inside list-disc font-mono text-xs break-words text-[var(--color-text-secondary)]">
                  {syncFailures.map((serverId) => (
                    <li key={serverId}>{serverId}</li>
                  ))}
                </ul>
                <p className="text-[var(--color-text-secondary)]">
                  {t(`labs.${feature}.import.syncFailedHint`)}
                </p>
              </div>
            </div>
          ) : (
            <TabsContent value="paste" className={importDialogStyles.content}>
              <div className="flex flex-col gap-2">
                {!localFileView ? (
                  <label
                    htmlFor="mcp-config-json"
                    className={
                      discovery
                        ? "sr-only"
                        : "text-sm leading-[22px] font-medium text-[var(--color-text-primary)]"
                    }
                  >
                    {t("labs.mcp.import.label")}
                  </label>
                ) : null}
                {readingFiles ? (
                  <p
                    role="status"
                    className="text-sm text-[var(--color-text-description)]"
                  >
                    {t("labs.mcp.import.local.reading")}
                  </p>
                ) : null}
                {!localFileView ? (
                  <Textarea
                    ref={pasteInput}
                    id="mcp-config-json"
                    value={text}
                    readOnly={!!registryPreview}
                    onChange={(event) => {
                      if (!registryPreview) setText(event.target.value)
                    }}
                    placeholder={JSON.stringify(
                      {
                        mcpServers: {
                          [targetServerId ?? "github"]: { url: "https://..." },
                        },
                      },
                      null,
                      2
                    )}
                    spellCheck={false}
                    disabled={saving || readingFiles}
                    aria-invalid={typeof result === "string"}
                    aria-describedby={
                      typeof result === "string"
                        ? "mcp-import-privacy mcp-import-error"
                        : registryPreview
                          ? "mcp-import-privacy mcp-registry-preview-hint"
                          : "mcp-import-privacy"
                    }
                    className={importDialogStyles.editor}
                  />
                ) : null}
                {registryPreview ? (
                  <p
                    id="mcp-registry-preview-hint"
                    className="text-xs leading-5 text-[var(--color-text-description)]"
                  >
                    {t("labs.mcp.import.registry.previewReadonly")}
                  </p>
                ) : null}
                <p
                  id="mcp-import-privacy"
                  className="inline-flex items-center gap-1 text-xs leading-5 text-[var(--color-text-description)]"
                >
                  <Lock className="size-3 shrink-0" />
                  {t("labs.mcp.import.privacy")}
                </p>
              </div>

              {typeof result === "string" ? (
                <p
                  id="mcp-import-error"
                  role="alert"
                  className="text-xs leading-5 text-[var(--color-error)]"
                >
                  {t(`labs.${errorFeature}.import.error.${result}`, {
                    serverId: targetServerId,
                  })}
                </p>
              ) : null}

              {localImport && "error" in localImport ? (
                <p role="alert" className="text-xs text-[var(--color-error)]">
                  {t("labs.mcp.import.local.limit")}
                </p>
              ) : null}
              {localResult?.issues.length ? (
                <ul
                  aria-label={t("labs.mcp.import.local.skipped")}
                  className={importDialogStyles.previewList}
                >
                  {localResult.issues.map((issue, index) => (
                    <li
                      key={index}
                      className="px-3 py-2 text-xs [overflow-wrap:anywhere] text-[var(--color-text-secondary)]"
                    >
                      <span className="font-medium">{issue.file}</span>
                      {" · "}
                      {t(`labs.mcp.import.local.issue.${issue.reason}`)}
                      {issue.names?.length
                        ? ` · ${issue.names.join(", ")}`
                        : ""}
                    </li>
                  ))}
                </ul>
              ) : null}
              {localResult && syncFailures.length > 0 ? (
                <p role="alert" className="text-xs text-[var(--color-warning)]">
                  {t("labs.mcp.import.syncFailedTitle")} ·{" "}
                  {syncFailures.join(", ")}
                </p>
              ) : null}
              {saveError && !finishing ? (
                <p
                  role="alert"
                  className="text-xs leading-5 text-[var(--color-error)]"
                >
                  {savedCount && localResult && parsed?.configs.length
                    ? t("labs.mcp.import.local.partial", {
                        saved: savedCount,
                        pending: parsed.configs.length,
                      })
                    : t("labs.mcp.import.error.saveFailed")}
                </p>
              ) : savedCount > 0 ? (
                <p role="status" className="text-xs text-muted-foreground">
                  {t("labs.common.importConflicts.savedSoFar", {
                    count: savedCount,
                  })}
                </p>
              ) : null}

              {localResult ? (
                <LocalImportConflicts
                  review={review}
                  disabled={saving || readingFiles}
                />
              ) : null}
              {previewConfigs.length ? (
                <section className="flex flex-col gap-2">
                  <h3 className="text-sm leading-[22px] font-medium text-[var(--color-text-primary)]">
                    {t(`labs.${feature}.import.preview`, {
                      count: previewConfigs.length,
                    })}
                  </h3>
                  <ul className={importDialogStyles.previewList}>
                    {previewConfigs.map((config) => {
                      const matched = observed.has(config.serverId)
                      return (
                        <li
                          key={config.serverId}
                          className="flex flex-wrap items-center gap-x-3 gap-y-2 px-3 py-3"
                        >
                          <span className="min-w-0 basis-[calc(100%-6rem)] text-sm leading-[22px] font-medium break-all text-[var(--color-text-primary)] sm:w-28 sm:shrink-0 sm:basis-auto">
                            {config.serverId}
                          </span>
                          <span className="inline-flex h-6 shrink-0 items-center rounded-[6px] border border-[var(--color-border)] px-2 font-mono text-xs leading-5 text-[var(--color-text-secondary)]">
                            {config.transport}
                          </span>
                          <code className="min-w-0 basis-full font-mono text-xs leading-5 [overflow-wrap:anywhere] text-[var(--color-text-secondary)] sm:flex-1 sm:basis-auto">
                            {launchLine(config)}
                          </code>
                          <span
                            className={cn(
                              "inline-flex h-6 shrink-0 items-center rounded-[6px] px-2 text-xs leading-5",
                              matched
                                ? "bg-[var(--color-bg-success)] text-[var(--color-success)]"
                                : "bg-[var(--color-bg-hover)] text-[var(--color-text-description)]"
                            )}
                          >
                            {matched
                              ? t("labs.mcp.import.matched")
                              : t("labs.mcp.import.unmatched")}
                          </span>
                        </li>
                      )
                    })}
                  </ul>
                  {parsed?.skipped.length ? (
                    <p className="text-xs leading-5 text-[var(--color-text-description)]">
                      {t("labs.mcp.import.skipped", {
                        names: parsed.skipped.join(", "),
                      })}
                    </p>
                  ) : null}
                </section>
              ) : null}
            </TabsContent>
          )}
        </Tabs>

        <DialogFooter
          className={cn(
            importDialogStyles.footer,
            compact && importDialogStyles.localFooter
          )}
        >
          {syncing || syncReport ? (
            <Button
              variant="outline"
              className={importDialogStyles.button}
              onClick={() => close(false)}
            >
              {t("labs.common.close")}
            </Button>
          ) : (
            <>
              <Button
                variant="outline"
                className={importDialogStyles.button}
                onClick={() => close(false)}
              >
                {t("labs.common.cancel")}
              </Button>
              {tab === "paste" ? (
                <Button
                  className={importDialogStyles.primary}
                  data-product-guide="mcp-import-save"
                  disabled={!canSave}
                  onClick={save}
                >
                  {saving ? <Loader2 className="size-4 animate-spin" /> : null}
                  {finishing
                    ? t("labs.common.importConflicts.finish")
                    : parsed?.configs.length
                      ? t("labs.mcp.import.save", {
                          count: parsed.configs.length,
                        })
                      : t("labs.mcp.import.saveDisabled")}
                </Button>
              ) : null}
            </>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
