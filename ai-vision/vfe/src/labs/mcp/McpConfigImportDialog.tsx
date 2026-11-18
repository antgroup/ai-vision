import { useEffect, useMemo, useRef, useState } from "react"
import { useQueryClient } from "@tanstack/react-query"
import { AlertTriangle, Loader2, Lock, FileJson } from "lucide-react"
import { useTranslation } from "react-i18next"

import { Button } from "@/components/ui/button"
import { importDialogStyles } from "../../components/labs/shared/importDialogStyles"
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

import { saveMcpConfigs, syncMcpConfigTools } from "./mcpApi"
import { parseMcpConfigJson } from "./mcpConfigImport"
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
  const [registryPreview, setRegistryPreview] =
    useState<RegistryImportPreview | null>(null)
  const [saving, setSaving] = useState(false)
  const [saveError, setSaveError] = useState(false)
  const [syncing, setSyncing] = useState(false)
  const [syncFailures, setSyncFailures] = useState<string[]>([])
  const active = useRef(true)
  useEffect(() => {
    active.current = true
    return () => {
      active.current = false
    }
  }, [])

  const result = useMemo(() => {
    if (!text.trim()) return null
    const parsed = parseMcpConfigJson(text)
    if (typeof parsed === "string") return parsed
    return {
      ...parsed,
      headerValuesByServerId:
        registryPreview?.headerValuesByServerId ??
        parsed.headerValuesByServerId,
    }
  }, [text, registryPreview])
  const errorFeature = result === "noServers" ? feature : "mcp"
  const parsed = result && typeof result !== "string" ? result : null
  const observed = useMemo(
    () => new Set(observedServerIds),
    [observedServerIds]
  )

  const close = (next: boolean) => {
    if (!next) {
      setText("")
      setRegistryPreview(null)
      setSaveError(false)
    }
    onOpenChange(next)
  }

  const save = async () => {
    if (!parsed) return
    setSaving(true)
    setSaveError(false)
    try {
      const savedConfigs = await saveMcpConfigs(spaceId, parsed.configs)
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
      // Bring the tool list along on import. Pasted header values are used
      // for this one sync and never stored; stdio requires deployment opt-in
      // and remains a manual action. Use the returned version, not local time.
      const { configs, headerValuesByServerId } = parsed
      const importedIds = new Set(configs.map((config) => config.serverId))
      if (active.current)
        onImported?.(
          savedConfigs.filter((config) => importedIds.has(config.serverId))
        )
      const autoTargets = savedConfigs.filter(
        (config) =>
          importedIds.has(config.serverId) && config.transport !== "stdio"
      )
      if (autoTargets.length > 0) {
        if (active.current) {
          setText("")
          setRegistryPreview(null)
          setSyncing(true)
        }
        const outcomes = await Promise.allSettled(
          autoTargets.map((config) =>
            syncMcpConfigTools(
              spaceId,
              config.serverId,
              headerValuesByServerId[config.serverId],
              config.importedAt
            )
          )
        )
        await queryClient.invalidateQueries({
          queryKey: ["labs", "mcp", "config-tools", spaceId],
        })
        const failed = autoTargets
          .filter((_, index) => outcomes[index].status === "rejected")
          .map((config) => config.serverId)
        if (failed.length > 0) {
          if (active.current) setSyncFailures(failed)
          return
        }
      }
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
        size="workspace"
        fillHeight
        className={importDialogStyles.dialog}
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
          <DialogDescription className={importDialogStyles.description}>
            {t(`labs.${feature}.import.desc`)}
          </DialogDescription>
        </DialogHeader>

        <Tabs
          value={tab}
          onValueChange={(next) => {
            if (next === tab) return
            // Discard registry text with its metadata; retaining the text alone
            // would allow a later save without explicit secret redaction.
            if (registryPreview) setText("")
            setRegistryPreview(null)
            setTab(next)
          }}
          className={importDialogStyles.tabs}
        >
          {discovery && !saving && !syncing && syncFailures.length === 0 ? (
            <TabsList variant="line" className={importDialogStyles.tabList}>
              <TabsTrigger value="search" className={importDialogStyles.tab}>
                {t("labs.mcp.import.tabs.search")}
              </TabsTrigger>
              <TabsTrigger value="paste" className={importDialogStyles.tab}>
                {t("labs.mcp.import.tabs.paste")}
              </TabsTrigger>
            </TabsList>
          ) : null}
          {discovery && !saving && !syncing && syncFailures.length === 0 ? (
            <TabsContent value="search" className={importDialogStyles.panel}>
              <McpImportRegistryPanel
                spaceId={spaceId}
                onPreview={(preview) => {
                  setText(preview.json)
                  setRegistryPreview(preview)
                  setSaveError(false)
                  setTab("paste")
                  requestAnimationFrame(() =>
                    document.getElementById("mcp-config-json")?.focus()
                  )
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
          ) : syncFailures.length > 0 ? (
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
                <div className="flex items-center justify-between gap-3">
                  <label
                    htmlFor="mcp-config-json"
                    className="text-sm leading-[22px] font-medium text-[var(--color-text-primary)]"
                  >
                    {t("labs.mcp.import.label")}
                  </label>
                </div>
                <Textarea
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
                  disabled={saving}
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

              {saveError ? (
                <p
                  role="alert"
                  className="text-xs leading-5 text-[var(--color-error)]"
                >
                  {t("labs.mcp.import.error.saveFailed")}
                </p>
              ) : null}

              {parsed ? (
                <section className="flex flex-col gap-2">
                  <h3 className="text-sm leading-[22px] font-medium text-[var(--color-text-primary)]">
                    {t(`labs.${feature}.import.preview`, {
                      count: parsed.configs.length,
                    })}
                  </h3>
                  <ul className={importDialogStyles.previewList}>
                    {parsed.configs.map((config) => {
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
                  {parsed.skipped.length ? (
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

        <DialogFooter className={importDialogStyles.footer}>
          {syncing || syncFailures.length > 0 ? (
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
                  disabled={tab !== "paste" || !parsed || saving}
                  onClick={save}
                >
                  {saving ? <Loader2 className="size-4 animate-spin" /> : null}
                  {parsed
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
