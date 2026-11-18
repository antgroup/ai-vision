import { useEffect, useMemo, useRef, useState } from "react"
import { useQueryClient } from "@tanstack/react-query"
import { AlertTriangle, Blocks, Loader2 } from "lucide-react"
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
import { useSpace } from "@/contexts/SpaceContext"
import { cn } from "@/lib/utils"

import { importDialogStyles } from "../../components/labs/shared/importDialogStyles"
import { saveSkillConfigs } from "./skillApi"
import { parseSkillConfigText } from "./skillConfigImport"
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
  const active = useRef(true)
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
  const observed = useMemo(
    () => new Set(observedSkillNames),
    [observedSkillNames]
  )

  const close = (next: boolean) => {
    if (!next) {
      setText("")
      setSaveError(false)
      setMode("file")
    }
    onOpenChange(next)
  }

  const save = async () => {
    if (!parsed?.configs.length) return
    setSaving(true)
    setSaveError(false)
    try {
      // Path stays whatever the imported file itself declares; the manual
      // per-entry fill-in was dropped from the preview cards.
      const saved = await saveSkillConfigs(spaceId, parsed.configs)
      const savedNames = new Set(saved.map((config) => config.name))
      if (parsed.configs.some((config) => !savedNames.has(config.name)))
        throw new Error("Incomplete skill import response")
      await queryClient.invalidateQueries({
        queryKey: ["labs", "skill", "configs", spaceId],
      })
      if (active.current) {
        toast.success(
          t("labs.skill.import.saved", {
            saved: parsed.configs.length,
            rejected: parsed.rejected.length,
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

  return (
    <Dialog open={open} onOpenChange={close}>
      <DialogContent
        size="workspace"
        fillHeight
        className={importDialogStyles.dialog}
      >
        <DialogHeader className={importDialogStyles.header}>
          <DialogTitle className={importDialogStyles.title}>
            <span className={importDialogStyles.titleIcon}>
              <Blocks className="size-4" aria-hidden="true" />
            </span>
            {t("labs.skill.import.title")}
          </DialogTitle>
          <DialogDescription className={importDialogStyles.description}>
            {t("labs.skill.import.desc")}
          </DialogDescription>
        </DialogHeader>

        <Tabs
          value={mode}
          onValueChange={(value) => setMode(value as ImportMode)}
          className={importDialogStyles.tabs}
        >
          <TabsList variant="line" className={importDialogStyles.tabList}>
            <TabsTrigger value="file" className={importDialogStyles.tab}>
              {t("labs.skill.import.tab.file")}
            </TabsTrigger>
            <TabsTrigger value="link" className={importDialogStyles.tab}>
              {t("labs.skill.import.tab.link")}
            </TabsTrigger>
            <TabsTrigger value="registry" className={importDialogStyles.tab}>
              {t("labs.skill.import.tab.registry")}
            </TabsTrigger>
          </TabsList>

          <TabsContent value="file" className={importDialogStyles.content}>
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

            {typeof result === "string" ? (
              <p
                role="alert"
                className="text-xs leading-5 text-[var(--color-error)]"
              >
                {t(`labs.skill.import.error.${result}`)}
              </p>
            ) : null}

            {saveError ? (
              <p
                role="alert"
                className="text-xs leading-5 text-[var(--color-error)]"
              >
                {t("labs.skill.import.error.saveFailed")}
              </p>
            ) : null}

            {parsed ? (
              <section className="flex flex-col gap-2">
                <h3 className="text-sm leading-[22px] font-medium text-[var(--color-text-primary)]">
                  {t("labs.skill.import.preview", {
                    count: parsed.configs.length,
                  })}
                </h3>
                {parsed.rejected.length ? (
                  <p className="text-xs leading-5 text-[var(--color-text-description)]">
                    {t("labs.skill.import.parseCounts", {
                      total: parsed.inputCount,
                      valid: parsed.configs.length,
                      rejected: parsed.rejected.length,
                    })}
                  </p>
                ) : null}
                <ul className={importDialogStyles.previewList}>
                  {parsed.configs.map((config) => {
                    const matched = observed.has(config.name)
                    return (
                      <li
                        key={config.name}
                        className="flex flex-col gap-1.5 px-3 py-3"
                      >
                        <div className="flex items-center gap-3">
                          <span className="w-28 shrink-0 truncate text-sm leading-[22px] font-medium text-[var(--color-text-primary)]">
                            {config.name}
                          </span>
                          <span className="inline-flex h-6 shrink-0 items-center rounded-[6px] border border-[var(--color-border)] px-2 text-xs leading-5 text-[var(--color-text-secondary)]">
                            {config.source}
                          </span>
                          <span className="min-w-0 flex-1 truncate text-xs leading-5 text-[var(--color-text-description)]">
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
                      </li>
                    )
                  })}
                </ul>
                {parsed.rejected.length ? (
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

        <DialogFooter className={importDialogStyles.footer}>
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
              disabled={!parsed?.configs.length || saving}
              onClick={save}
            >
              {saving ? <Loader2 className="size-4 animate-spin" /> : null}
              {parsed
                ? t("labs.skill.import.save", { count: parsed.configs.length })
                : t("labs.skill.import.saveDisabled")}
            </Button>
          ) : null}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
