import { writeClipboardText } from "@/lib/clipboard"
import { Check, Copy } from "lucide-react"
import { useState } from "react"
import { useTranslation } from "react-i18next"

import { cn } from "@/lib/utils"

interface CopyableCodeProps {
  value: string
  className?: string
  multiline?: boolean
}

export function CopyableCode({
  value,
  className,
  multiline,
}: CopyableCodeProps) {
  const { t } = useTranslation()
  const [copied, setCopied] = useState(false)
  const [copyFailed, setCopyFailed] = useState(false)

  const copy = async (event: React.MouseEvent) => {
    event.stopPropagation()
    try {
      await writeClipboardText(value)
      setCopyFailed(false)
      setCopied(true)
      window.setTimeout(() => setCopied(false), 1500)
    } catch {
      setCopied(false)
      setCopyFailed(true)
    }
  }

  return (
    <div
      className={cn(
        "group/code flex min-w-0 flex-wrap items-start gap-2 rounded-[8px] bg-[var(--color-bg-hover)] px-3 py-1",
        className
      )}
    >
      <code
        className={cn(
          "min-w-0 flex-1 font-mono text-xs leading-5 text-[var(--color-text-primary)]",
          multiline ? "break-all whitespace-pre-wrap" : "truncate"
        )}
        title={multiline ? undefined : value}
      >
        {value}
      </code>
      <button
        type="button"
        onClick={copy}
        aria-label={t("labs.common.copy")}
        className="mt-0.5 shrink-0 rounded-[6px] text-[var(--color-icon-normal)] hover:text-[var(--color-icon-primary)] focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--color-text-primary)]"
      >
        {copied ? (
          <Check className="size-4 text-[var(--color-success)]" />
        ) : (
          <Copy className="size-4" />
        )}
      </button>
      {copyFailed ? (
        <span
          role="alert"
          className="w-full text-xs leading-5 text-[var(--color-error)]"
        >
          {t("labs.common.copyFailed")}
        </span>
      ) : null}
    </div>
  )
}
