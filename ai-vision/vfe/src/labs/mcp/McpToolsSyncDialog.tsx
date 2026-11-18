import { useState } from "react"
import { Loader2, Lock, RefreshCw } from "lucide-react"
import { useTranslation } from "react-i18next"

import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { DialogContent } from "@/components/dialogs/DialogContent"
import { Input } from "@/components/ui/input"

import type { McpServerConfig } from "./types"

interface McpToolsSyncDialogProps {
  feature?: "mcp" | "memory"
  open: boolean
  onOpenChange: (open: boolean) => void
  config: McpServerConfig
  /** Submits the entered values; empty entries are skipped. */
  onSubmit: (headerValues: Record<string, string>) => void
  syncing: boolean
  /** Backend message from the last failed sync attempt. */
  error: string | null
}

export function McpToolsSyncDialog({
  feature = "mcp",
  open,
  onOpenChange,
  config,
  onSubmit,
  syncing,
  error,
}: McpToolsSyncDialogProps) {
  const { t } = useTranslation()
  const [values, setValues] = useState<Record<string, string>>({})

  const close = (next: boolean) => {
    if (!next) {
      setValues({})
    }
    onOpenChange(next)
  }

  const submit = () => {
    const headerValues = Object.fromEntries(
      Object.entries(values).filter(([, value]) => value.trim() !== "")
    )
    onSubmit(headerValues)
  }

  return (
    <Dialog open={open} onOpenChange={close}>
      <DialogContent
        size="form"
        className="flex flex-col gap-4 rounded-[12px] shadow-none"
      >
        <DialogHeader className="-mx-6 -mt-6 gap-2 rounded-t-[11px] border-b border-[var(--color-border-labs)] bg-[var(--color-bg-labs)] px-6 py-4 pr-12">
          <DialogTitle className="flex items-center gap-2 text-base leading-6 font-medium [overflow-wrap:anywhere] text-[var(--color-text-primary)]">
            <span className="flex size-8 shrink-0 items-center justify-center rounded-lg bg-[var(--color-bg-labs-tag)] text-[var(--color-labs)]">
              <RefreshCw className="size-4" aria-hidden="true" />
            </span>
            {t("labs.mcp.config.syncDialogTitle", { name: config.serverId })}
          </DialogTitle>
          <DialogDescription className="text-sm leading-[22px] text-[var(--color-text-description)]">
            {t(`labs.${feature}.config.syncDialogDesc`)}
          </DialogDescription>
        </DialogHeader>

        <div className="flex min-h-0 flex-col gap-3 overflow-y-auto">
          {config.headerKeys.map((key) => (
            <div key={key} className="flex flex-col gap-1.5">
              <label
                htmlFor={`mcp-sync-header-${key}`}
                className="font-mono text-xs leading-5 text-[var(--color-text-secondary)]"
              >
                {key}
              </label>
              <Input
                id={`mcp-sync-header-${key}`}
                type="password"
                autoComplete="off"
                value={values[key] ?? ""}
                onChange={(event) =>
                  setValues((current) => ({
                    ...current,
                    [key]: event.target.value,
                  }))
                }
                placeholder={t("labs.mcp.config.headerValuePlaceholder", {
                  key,
                })}
                className="rounded-[10px] font-mono text-xs"
              />
            </div>
          ))}
          <p className="inline-flex items-center gap-1 text-xs leading-5 text-[var(--color-text-description)]">
            <Lock className="size-3 shrink-0" />
            {t("labs.mcp.config.syncDialogPrivacy")}
          </p>

          {error ? (
            <p className="text-xs leading-5 break-all text-[var(--color-error)]">
              {t("labs.mcp.config.syncFailed")}: {error}
            </p>
          ) : null}
        </div>

        <DialogFooter>
          <Button
            variant="outline"
            className="rounded-[10px]"
            onClick={() => close(false)}
          >
            {t("labs.common.cancel")}
          </Button>
          <Button
            className="rounded-[10px] bg-[var(--color-labs)] text-white hover:bg-[var(--color-labs)] hover:brightness-95 dark:text-[var(--color-bg-page)]"
            disabled={syncing}
            onClick={submit}
          >
            {syncing ? <Loader2 className="size-4 animate-spin" /> : null}
            {t("labs.mcp.config.syncAction")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
