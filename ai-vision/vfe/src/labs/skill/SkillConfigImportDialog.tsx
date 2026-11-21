import { useEffect, useMemo, useRef, useState, type ChangeEvent } from "react"
import { useQueryClient } from "@tanstack/react-query"
import {
  AlertTriangle,
  Blocks,
  FileUp,
  FolderOpen,
  Loader2,
} from "lucide-react"
import { useTranslation } from "react-i18next"
import { toast } from "sonner"

import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { DialogContent } from "@/components/dialogs/DialogContent"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { Textarea } from "@/components/ui/textarea"
import { LocalImportConflicts } from "@/components/labs/shared/LocalImportConflicts"
import { useLocalImportReview } from "@/components/labs/shared/localImportReview"
import { useSpace } from "@/contexts/SpaceContext"
import { cn } from "@/lib/utils"

import { importDialogStyles } from "../../components/labs/shared/importDialogStyles"
import { checkSkillImportConflicts, saveSkillConfigs } from "./skillApi"
import { parseSkillConfigText } from "./skillConfigImport"
import {
  MAX_LOCAL_SKILL_BATCH_BYTES,
  readLocalSkillFiles,
  type SkillLocalImportResult,
} from "./skillLocalImport"
import { saveSkillConfigBatches } from "./skillImportRemote"
import { SkillImportLinkPanel } from "./SkillImportLinkPanel"
import { SkillImportRegistryPanel } from "./SkillImportRegistryPanel"

type ImportMode = "file" | "link" | "registry"

interface SkillConfigImportDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  spaceId: string
  observedSkillNames: string[]
}

export function SkillConfigImportDialog(props: SkillConfigImportDialogProps) {
  const { currentSpace } = useSpace()
  const canManage =
    currentSpace?.role === 1 && currentSpace.id === props.spaceId
  return props.open && canManage ? (
    <SkillConfigImportForm key={props.spaceId} {...props} />
  ) : null
}

function SkillConfigImportForm({
  open,
  onOpenChange,
  spaceId,
  observedSkillNames,
}: SkillConfigImportDialogProps) {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const [text, setText] = useState("")
  const [mode, setMode] = useState<ImportMode>("file")
  const [saving, setSaving] = useState(false)
  const [saveError, setSaveError] = useState(false)
  const [selection, setSelection] = useState<SkillLocalImportResult | null>(
    null
  )
  const [reading, setReading] = useState(false)
  const [savedCount, setSavedCount] = useState(0)
  const filesInput = useRef<HTMLInputElement>(null)
  const directoryInput = useRef<HTMLInputElement>(null)
  const canPickDirectory =
    typeof HTMLInputElement !== "undefined" &&
    "webkitdirectory" in HTMLInputElement.prototype
  const active = useRef(true)
  const dismissedWhileSaving = useRef(false)
  useEffect(() => {
    active.current = true
    return () => {
      active.current = false
    }
  }, [])

  const result = useMemo(
    () => (text.trim() ? parseSkillConfigText(text) : null),
    [text]
  )
  const parsed = result && typeof result !== "string" ? result : null
  const local = selection && "candidates" in selection ? selection : null
  const review = useLocalImportReview(
    "skill",
    spaceId,
    local?.candidates ?? null,
    checkSkillImportConflicts
  )
  const configs = selection
    ? review.entries.map(({ name, value }) => ({ ...value, name }))
    : (parsed?.configs ?? [])
  const previewConfigs = local
    ? configs.filter((_, index) => !review.entries[index].conflict)
    : configs
  const finishing = savedCount > 0 && review.canFinishWithoutSaving
  const canSave =
    (!!configs.length || finishing) &&
    !reading &&
    !saving &&
    (!local || (review.status === "ready" && !review.needsDecision))
  const rejectedCount = selection
    ? (local?.rejected.length ?? 0)
    : (parsed?.rejected.length ?? 0)
  const observed = useMemo(
    () => new Set(observedSkillNames),
    [observedSkillNames]
  )

  const close = (next: boolean) => {
    if (!next) {
      active.current = false
      dismissedWhileSaving.current = saving
      setText("")
      setSelection(null)
      setSavedCount(0)
      setSaveError(false)
      setMode("file")
    }
    onOpenChange(next)
  }

  const choose = async (
    event: ChangeEvent<HTMLInputElement>,
    fromDirectory: boolean
  ) => {
    // Keep the assigned FileList before clearing the input for a same-file retry.
    const files = Array.from(event.currentTarget.files ?? [])
    event.currentTarget.value = ""
    if (!files.length) return
    setReading(true)
    setSaveError(false)
    setSavedCount(0)
    try {
      const selection = await readLocalSkillFiles(files, fromDirectory)
      if (active.current) setSelection(selection)
    } finally {
      if (active.current) setReading(false)
    }
  }

  const save = async () => {
    if (!canSave) return
    setSaving(true)
    setSaveError(false)
    try {
      let count = configs.length
      if (local) {
        const batch = await saveSkillConfigBatches(
          spaceId,
          configs,
          MAX_LOCAL_SKILL_BATCH_BYTES,
          () => active.current,
          review.entries.flatMap(({ overwrite }) =>
            overwrite ? [overwrite] : []
          )
        )
        count = savedCount + batch.savedNames.length
        if (batch.savedNames.length || !active.current)
          await queryClient.invalidateQueries({
            queryKey: ["labs", "skill", "configs", spaceId],
          })
        if (!active.current) {
          if (dismissedWhileSaving.current && count > 0)
            toast.info(t("labs.common.importCancelledSaved", { count }))
          return
        }
        if (batch.pendingConfigs.length) {
          review.markSaved(new Set(batch.savedNames))
          setSavedCount(count)
          setSaveError(!review.reportBatchCollision(batch.batchCollision))
          await review.refresh()
          return
        }
      } else {
        const saved = await saveSkillConfigs(spaceId, configs)
        const savedNames = new Set(saved.map((config) => config.name))
        if (configs.some((config) => !savedNames.has(config.name)))
          throw new Error("Incomplete skill import response")
        await queryClient.invalidateQueries({
          queryKey: ["labs", "skill", "configs", spaceId],
        })
      }
      if (active.current) {
        toast.success(
          t("labs.skill.import.saved", {
            saved: count,
            rejected: rejectedCount,
          })
        )
        close(false)
      }
    } catch {
      if (active.current) setSaveError(true)
    } finally {
      if (active.current) setSaving(false)
    }
  }

  const localFileView = mode === "file" && (reading || selection !== null)
  const fileButtonClass = cn(
    importDialogStyles.button,
    localFileView && importDialogStyles.localToolbarButton
  )
  const showPreview = local
    ? previewConfigs.length > 0 || local.rejected.length > 0
    : !selection && !!parsed

  return (
    <Dialog open={open} onOpenChange={close}>
      <DialogContent
        size={localFileView ? "detail" : "workspace"}
        fillHeight={!localFileView}
        className={importDialogStyles.dialog}
      >
        <DialogHeader className={importDialogStyles.header}>
          <DialogTitle className={importDialogStyles.title}>
            <span className={importDialogStyles.titleIcon}>
              <Blocks className="size-4" aria-hidden="true" />
            </span>
            {t("labs.skill.import.title")}
          </DialogTitle>
          <DialogDescription
            className={
              localFileView ? "sr-only" : importDialogStyles.description
            }
          >
            {t(
              mode === "file" && selection
                ? "labs.common.importConflicts.localDescription"
                : "labs.skill.import.desc"
            )}
          </DialogDescription>
        </DialogHeader>

        <Tabs
          value={mode}
          onValueChange={(value) => {
            if (!saving) setMode(value as ImportMode)
          }}
          className={importDialogStyles.tabs}
        >
          <TabsList variant="line" className={importDialogStyles.tabList}>
            <TabsTrigger
              value="file"
              disabled={saving}
              className={importDialogStyles.tab}
            >
              {t("labs.skill.import.tab.file")}
            </TabsTrigger>
            <TabsTrigger
              value="link"
              disabled={saving}
              className={importDialogStyles.tab}
            >
              {t("labs.skill.import.tab.link")}
            </TabsTrigger>
            <TabsTrigger
              value="registry"
              disabled={saving}
              className={importDialogStyles.tab}
            >
              {t("labs.skill.import.tab.registry")}
            </TabsTrigger>
          </TabsList>

          <TabsContent value="file" className={importDialogStyles.content}>
            <div className="flex flex-wrap items-center gap-2">
              <input
                ref={filesInput}
                type="file"
                accept=".md,.markdown"
                multiple
                hidden
                onChange={(event) => void choose(event, false)}
              />
              <Button
                type="button"
                variant="outline"
                className={fileButtonClass}
                disabled={reading || saving}
                onClick={() => filesInput.current?.click()}
              >
                <FileUp className="size-4" aria-hidden="true" />
                {t("labs.skill.import.local.files")}
              </Button>
              {canPickDirectory ? (
                <>
                  <input
                    ref={directoryInput}
                    type="file"
                    {...{ webkitdirectory: "" }}
                    multiple
                    hidden
                    onChange={(event) => void choose(event, true)}
                  />
                  <Button
                    type="button"
                    variant="outline"
                    className={fileButtonClass}
                    disabled={reading || saving}
                    onClick={() => directoryInput.current?.click()}
                  >
                    <FolderOpen className="size-4" aria-hidden="true" />
                    {t("labs.skill.import.local.folder")}
                  </Button>
                </>
              ) : null}
              {selection ? (
                <Button
                  type="button"
                  variant="ghost"
                  className={cn(
                    importDialogStyles.ghost,
                    importDialogStyles.localToolbarButton
                  )}
                  disabled={reading || saving}
                  onClick={() => {
                    setSelection(null)
                    setSaveError(false)
                    setSavedCount(0)
                  }}
                >
                  {t("labs.skill.import.local.backToPaste")}
                </Button>
              ) : null}
            </div>

            {reading ? (
              <p
                role="status"
                className="text-sm text-[var(--color-text-description)]"
              >
                {t("labs.skill.import.local.reading")}
              </p>
            ) : null}

            {selection && "error" in selection && !reading ? (
              <p role="alert" className="text-sm text-[var(--color-error)]">
                {t(`labs.skill.import.local.${selection.error}`)}
              </p>
            ) : null}

            {!selection && !reading ? (
              <div className="flex flex-col gap-2">
                <label htmlFor="skill-config-text" className="sr-only">
                  {t("labs.skill.import.label")}
                </label>
                <Textarea
                  id="skill-config-text"
                  value={text}
                  onChange={(event) => setText(event.target.value)}
                  placeholder={
                    "---\nname: blog-post\ndescription: write posts\n---\n\n# Blog post writing skill"
                  }
                  spellCheck={false}
                  disabled={saving}
                  aria-invalid={typeof result === "string"}
                  className={importDialogStyles.editor}
                />
              </div>
            ) : null}

            {!selection && !reading && typeof result === "string" ? (
              <p
                role="alert"
                className="text-xs leading-5 text-[var(--color-error)]"
              >
                {t(`labs.skill.import.error.${result}`)}
              </p>
            ) : null}

            {saveError && !finishing ? (
              <p
                role="alert"
                className="text-xs leading-5 text-[var(--color-error)]"
              >
                {local && configs.length
                  ? t("labs.skill.import.local.saveFailed", {
                      saved: savedCount,
                      pending: configs.length,
                    })
                  : t("labs.skill.import.error.saveFailed")}
              </p>
            ) : savedCount > 0 ? (
              <p role="status" className="text-xs text-muted-foreground">
                {t("labs.common.importConflicts.savedSoFar", {
                  count: savedCount,
                })}
              </p>
            ) : null}

            {local && !reading ? (
              <LocalImportConflicts
                review={review}
                disabled={saving || reading}
              />
            ) : null}

            {!reading && showPreview ? (
              <section className="flex flex-col gap-2">
                {!local || previewConfigs.length ? (
                  <h3 className="text-sm leading-[22px] font-medium text-[var(--color-text-primary)]">
                    {t("labs.skill.import.preview", {
                      count: previewConfigs.length,
                    })}
                  </h3>
                ) : null}
                {!selection && parsed?.rejected.length ? (
                  <p className="text-xs leading-5 text-[var(--color-text-description)]">
                    {t("labs.skill.import.parseCounts", {
                      total: parsed.inputCount,
                      valid: parsed.configs.length,
                      rejected: parsed.rejected.length,
                    })}
                  </p>
                ) : null}
                {previewConfigs.length ? (
                  <ul className={importDialogStyles.previewList}>
                    {previewConfigs.map((config) => {
                      const matched = observed.has(config.name)
                      return (
                        <li
                          key={config.name}
                          className="flex flex-col gap-1.5 px-3 py-3"
                        >
                          <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
                            <span className="max-w-full truncate text-sm leading-[22px] font-medium text-[var(--color-text-primary)] sm:w-28 sm:shrink-0">
                              {config.name}
                            </span>
                            {!local ? (
                              <span className="inline-flex h-6 shrink-0 items-center rounded-[6px] border border-[var(--color-border)] px-2 text-xs leading-5 text-[var(--color-text-secondary)]">
                                {config.source}
                              </span>
                            ) : null}
                            <span className="min-w-20 flex-1 truncate text-xs leading-5 text-[var(--color-text-description)]">
                              {config.description}
                            </span>
                            <span
                              className={cn(
                                "inline-flex h-6 shrink-0 items-center rounded-[6px] px-2 text-xs leading-5",
                                matched
                                  ? "bg-[var(--color-bg-success)] text-[var(--color-success)]"
                                  : "bg-[var(--color-bg-hover)] text-[var(--color-text-description)]"
                              )}
                            >
                              {matched
                                ? t("labs.skill.import.matched")
                                : t("labs.skill.import.unmatched")}
                            </span>
                          </div>
                          {local ? (
                            <span className="font-mono text-xs [overflow-wrap:anywhere] text-[var(--color-text-description)]">
                              {config.path}
                            </span>
                          ) : null}
                        </li>
                      )
                    })}
                  </ul>
                ) : null}
                {local?.rejected.length ? (
                  <ul className="space-y-1 text-xs text-[var(--color-text-description)]">
                    {local.rejected.map((entry, index) => (
                      <li key={index} className="flex items-start gap-2">
                        <AlertTriangle
                          className="size-4 shrink-0 text-[var(--color-warning)]"
                          aria-hidden="true"
                        />
                        <span className="min-w-0 [overflow-wrap:anywhere]">
                          {t("labs.skill.import.local.rejected", {
                            path: entry.path,
                            reason: t(
                              `labs.skill.import.local.${entry.reason}`
                            ),
                          })}
                        </span>
                      </li>
                    ))}
                  </ul>
                ) : null}
                {!selection && parsed?.rejected.length ? (
                  <div className="flex items-start gap-2 text-xs leading-5 text-[var(--color-text-description)]">
                    <AlertTriangle className="mt-0.5 size-3.5 shrink-0 text-[var(--color-warning)]" />
                    <span>
                      {parsed.rejected.map((entry) => (
                        <span key={entry.index} className="block">
                          {t("labs.skill.import.rejectedEntry", {
                            index: entry.index,
                            name: entry.name || "—",
                            reason: t(
                              `labs.skill.import.reason.${entry.reason}`
                            ),
                          })}
                        </span>
                      ))}
                    </span>
                  </div>
                ) : null}
              </section>
            ) : null}
          </TabsContent>

          <TabsContent value="link" className={importDialogStyles.panel}>
            <SkillImportLinkPanel
              spaceId={spaceId}
              onClose={() => close(false)}
            />
          </TabsContent>

          <TabsContent value="registry" className={importDialogStyles.panel}>
            <SkillImportRegistryPanel spaceId={spaceId} />
          </TabsContent>
        </Tabs>

        <DialogFooter
          className={cn(
            importDialogStyles.footer,
            localFileView && importDialogStyles.localFooter
          )}
        >
          <Button
            variant="outline"
            className={importDialogStyles.button}
            onClick={() => close(false)}
          >
            {t("labs.common.cancel")}
          </Button>
          {mode === "file" ? (
            <Button
              className={importDialogStyles.primary}
              disabled={!canSave}
              onClick={save}
            >
              {saving ? <Loader2 className="size-4 animate-spin" /> : null}
              {finishing
                ? t("labs.common.importConflicts.finish")
                : local && saveError && configs.length
                  ? t("labs.skill.import.local.retry", {
                      count: configs.length,
                    })
                  : configs.length
                    ? t("labs.skill.import.save", { count: configs.length })
                    : t("labs.skill.import.saveDisabled")}
            </Button>
          ) : null}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
