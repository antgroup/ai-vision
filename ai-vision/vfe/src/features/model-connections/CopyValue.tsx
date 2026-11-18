import { writeClipboardText } from "@/lib/clipboard"
import { Copy } from "lucide-react"
import { useTranslation } from "react-i18next"
import { toast } from "sonner"

export function CopyValue({
  value,
  label,
  compact = false,
}: {
  value: string
  label: string
  compact?: boolean
}) {
  const { t } = useTranslation()
  return (
    <div className="flex min-w-0 items-start gap-2">
      <span
        className={
          compact
            ? "min-w-0 font-mono text-xs break-all text-muted-foreground"
            : "min-w-0 font-mono text-sm break-all"
        }
      >
        {value}
      </span>
      <button
        type="button"
        className="shrink-0 rounded-sm p-0.5 text-muted-foreground hover:text-foreground focus-visible:outline-2 focus-visible:outline-ring"
        aria-label={t("modelConnections.copy", { field: label })}
        onClick={async () => {
          try {
            await writeClipboardText(value)
            toast.success(t("modelConnections.copied"))
          } catch {
            toast.error(t("modelConnections.copyFailed"))
          }
        }}
      >
        <Copy className="size-3.5" />
      </button>
    </div>
  )
}
