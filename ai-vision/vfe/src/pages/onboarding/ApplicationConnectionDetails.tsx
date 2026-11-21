import { useQuery } from "@tanstack/react-query"
import { Copy } from "lucide-react"
import { useTranslation } from "react-i18next"
import { toast } from "sonner"

import { Button } from "@/components/ui/button"
import { writeClipboardText } from "@/lib/clipboard"

export function ApplicationConnectionDetails({
  spaceId,
  agentUuid,
}: {
  spaceId: string
  agentUuid?: string
}) {
  const { t } = useTranslation()
  const deploymentUrl = useQuery({
    queryKey: ["onboarding-public-url"],
    queryFn: async ({ signal }) => {
      const response = await fetch("/api/public/onboarding/catalog.json", {
        credentials: "omit",
        signal,
      })
      if (!response.ok) throw new Error("Onboarding catalog unavailable")
      const catalog: { code: number; data?: { cdn_base?: string } } =
        await response.json()
      if (catalog.code !== 0 || !catalog.data?.cdn_base)
        throw new Error("Onboarding catalog has no deployment URL")
      return catalog.data.cdn_base
    },
    staleTime: 5 * 60_000,
    retry: false,
  })
  const fields = [
    { label: t("agentAccess.connection.agentUuid"), value: agentUuid },
    { label: t("agentAccess.connection.spaceUuid"), value: spaceId },
    {
      label: t("agentAccess.connection.baseUrl"),
      value: deploymentUrl.data,
      empty: deploymentUrl.isPending ? t("common.loading") : undefined,
    },
  ]

  async function copy(value: string) {
    try {
      await writeClipboardText(value)
      toast.success(t("common.copied"))
    } catch {
      toast.error(t("common.copyFailed"))
    }
  }

  return (
    <div className="rounded-xl border border-border bg-muted/25 p-4">
      <dl className="grid gap-4 sm:grid-cols-2">
        {fields.map(({ label, value, empty }) => {
          const copyLabel = t("agentAccess.connection.copyField", {
            field: label,
          })
          return (
            <div key={label} className="min-w-0 sm:last:col-span-2">
              <dt className="text-xs text-muted-foreground">{label}</dt>
              <dd className="flex min-h-7 items-start gap-1">
                {value ? (
                  <>
                    <code className="min-w-0 pt-1 text-xs leading-5 wrap-anywhere">
                      {value}
                    </code>
                    <Button
                      type="button"
                      variant="ghost"
                      size="icon"
                      className="size-7 shrink-0 text-muted-foreground"
                      aria-label={copyLabel}
                      title={copyLabel}
                      onClick={() => void copy(value)}
                    >
                      <Copy className="size-3.5" aria-hidden="true" />
                    </Button>
                  </>
                ) : (
                  <span className="pt-1 text-xs text-muted-foreground">
                    {empty ?? t("agentAccess.connection.notAvailable")}
                  </span>
                )}
              </dd>
            </div>
          )
        })}
      </dl>
    </div>
  )
}
