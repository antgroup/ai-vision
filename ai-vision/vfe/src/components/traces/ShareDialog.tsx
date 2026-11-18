import { writeClipboardText } from "@/lib/clipboard"
import { useState } from "react"
import { useTranslation } from "react-i18next"
import { useQuery } from "@tanstack/react-query"
import { Check, Copy, Loader2 } from "lucide-react"
import { toast } from "sonner"

import { api } from "@/lib/api"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Dialog, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import { DialogContent } from "@/components/dialogs/DialogContent"

interface ShareDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  spaceId: string
  resourceType: "trace" | "session"
  resourceId: string
  agentProduct?: string
  agentUuid?: string | null
  protocol?: string
  /**
   * The viewer's current query time window. Persisted into the signed share
   * link so the public page reuses it; without it, SigNoz
   * session queries fall back to a 7-day scan and exceed the OSS file limit.
   */
  fromTimestamp?: string
  toTimestamp?: string
}

interface GenerateResponse {
  code: number
  message?: string
  data?: {
    url: string
    expires_at: string
  }
}

export function ShareDialog({
  open,
  onOpenChange,
  spaceId,
  resourceType,
  resourceId,
  agentProduct,
  agentUuid,
  protocol = "lf",
  fromTimestamp,
  toTimestamp,
}: ShareDialogProps) {
  const { t } = useTranslation()
  const [copied, setCopied] = useState(false)

  const { data, isLoading, error } = useQuery({
    queryKey: [
      "shareGenerate",
      spaceId,
      resourceType,
      resourceId,
      agentProduct,
      agentUuid,
      protocol,
      fromTimestamp,
      toTimestamp,
    ],
    queryFn: () =>
      api.post<GenerateResponse>(`/spaces/${spaceId}/share-links/generate`, {
        resource_type: resourceType,
        resource_id: resourceId,
        agent_product: agentProduct || "",
        agent_uuid: agentUuid || undefined,
        protocol,
        from: fromTimestamp || "",
        to: toTimestamp || "",
      }),
    enabled: open,
    retry: false,
  })

  const rawUrl = data && data.code === 0 && data.data ? data.data.url : ""
  const shareUrl =
    rawUrl && rawUrl.startsWith("/")
      ? `${window.location.origin}${rawUrl}`
      : rawUrl
  const errorMessage = error
    ? t("share.generateFailed")
    : data && data.code !== 0
      ? data.message || t("share.generateFailed")
      : ""

  const handleCopy = async () => {
    try {
      await writeClipboardText(shareUrl)
      setCopied(true)
      toast.success(t("share.copied"))
      setTimeout(() => setCopied(false), 2000)
    } catch {
      setCopied(false)
      toast.error(t("share.copyFailed"))
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent size="compact">
        <DialogHeader>
          <DialogTitle>
            {t("share.title")} -{" "}
            {resourceType === "trace"
              ? t("traceLabels.trace")
              : t("traceLabels.session")}
          </DialogTitle>
        </DialogHeader>
        <div className="flex flex-col gap-4">
          {isLoading ? (
            <div className="flex items-center justify-center py-6">
              <Loader2 className="size-5 animate-spin text-muted-foreground" />
            </div>
          ) : errorMessage ? (
            <p className="text-sm text-destructive">{errorMessage}</p>
          ) : (
            <>
              <Input
                value={shareUrl}
                readOnly
                onClick={(e) => (e.target as HTMLInputElement).select()}
                className="font-mono text-xs"
              />
              <Button onClick={handleCopy} disabled={!shareUrl}>
                {copied ? (
                  <>
                    <Check className="mr-2 size-4" />
                    {t("share.copied")}
                  </>
                ) : (
                  <>
                    <Copy className="mr-2 size-4" />
                    {t("share.copyLink")}
                  </>
                )}
              </Button>
              <p className="text-xs text-muted-foreground">
                {t("share.validityNote")}
              </p>
            </>
          )}
        </div>
      </DialogContent>
    </Dialog>
  )
}
