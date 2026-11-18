import { useTranslation } from "react-i18next"
import { useState } from "react"
import { Check, Copy, Eye } from "lucide-react"
import { toast } from "sonner"

import { api } from "@/lib/api"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent } from "@/components/ui/card"
import {
  extractServerMessage,
  type CredentialInfo,
  type RevealedCredential,
} from "./credentialUtils"

interface KeyCardProps {
  label: string
  credential: CredentialInfo
  canReveal?: boolean
  spaceId: string
  onReveal: (credentialId: number, title: string) => void
  onCopy: (text: string, key: string) => void
  copyState: string | null
}

export default function KeyCard({
  label,
  credential,
  canReveal,
  spaceId,
  onReveal,
  onCopy,
  copyState,
}: KeyCardProps) {
  const { t } = useTranslation()
  const [copying, setCopying] = useState<string | null>(null)
  const pkCopyKey = `pk-${credential.scope}`
  const skCopyKey = `sk-${credential.scope}`

  const handleCopyWithReveal = async (field: "public_key" | "secret_key") => {
    const copyKey = field === "public_key" ? pkCopyKey : skCopyKey
    setCopying(copyKey)
    try {
      const response = await api.post<{
        code: number
        data?: RevealedCredential
        message?: string
      }>(`/spaces/${spaceId}/credentials/${credential.id}/reveal?intent=copy`)
      if (response.code === 0 && response.data) {
        onCopy(response.data[field], copyKey)
      } else {
        toast.error(response.message || t("spaceSettings.page.keyLoadFailed"))
      }
    } catch (err) {
      toast.error(
        extractServerMessage(err) || t("spaceSettings.page.networkError")
      )
    } finally {
      setCopying(null)
    }
  }

  return (
    <>
      <Card>
        <CardContent className="px-5 py-4">
          <div className="flex items-center justify-between">
            <h3 className="text-sm font-medium text-foreground">{label}</h3>
            <Badge variant="outline" className="text-xs font-normal">
              {credential.scope === "WRITE"
                ? t("spaceSettings.keys.write")
                : t("spaceSettings.keys.read")}
            </Badge>
          </div>

          <div className="mt-3">
            <div className="flex items-center justify-between">
              <span className="text-xs text-muted-foreground">
                {t("spaceSettings.credentials.publicKey")}
              </span>
              {canReveal && (
                <Button
                  variant="ghost"
                  size="sm"
                  className="h-6 px-2 text-xs font-normal"
                  onClick={() => handleCopyWithReveal("public_key")}
                  disabled={copying === pkCopyKey}
                >
                  {copyState === pkCopyKey ? (
                    <Check className="mr-1 size-3 text-emerald-500" />
                  ) : (
                    <Copy className="mr-1 size-3" />
                  )}
                  {copyState === pkCopyKey
                    ? t("common.copied")
                    : t("common.copy")}
                </Button>
              )}
            </div>
            <div className="mt-1 rounded-lg bg-muted/50 px-3 py-2">
              <code className="text-xs break-all text-foreground select-none">
                {credential.public_key.slice(0, 8)}
                {"********"}
              </code>
            </div>
          </div>

          <div className="mt-3">
            <div className="flex items-center justify-between">
              <span className="text-xs text-muted-foreground">
                {t("spaceSettings.credentials.secretKey")}
              </span>
              {canReveal && (
                <Button
                  variant="ghost"
                  size="sm"
                  className="h-6 px-2 text-xs font-normal"
                  onClick={() => handleCopyWithReveal("secret_key")}
                  disabled={copying === skCopyKey}
                >
                  {copyState === skCopyKey ? (
                    <Check className="mr-1 size-3 text-emerald-500" />
                  ) : (
                    <Copy className="mr-1 size-3" />
                  )}
                  {copyState === skCopyKey
                    ? t("common.copied")
                    : t("common.copy")}
                </Button>
              )}
            </div>
            <div className="mt-1 rounded-lg bg-muted/50 px-3 py-2">
              <code className="text-xs break-all text-foreground select-none">
                {credential.public_key.replace(/^pk-/, "sk-").slice(0, 8)}
                {"********"}
              </code>
            </div>
          </div>

          {canReveal && (
            <div className="mt-3 flex gap-2">
              <Button
                variant="outline"
                size="sm"
                className="h-7 rounded-full px-3 text-xs font-normal"
                onClick={() => onReveal(credential.id, label)}
              >
                <Eye className="mr-1 size-3" />
                {t("spaceSettings.keys.reveal")}
              </Button>
              {/* TODO: 重新生成功能待产品方案确认后开放 */}
              {/* <Button
                variant="outline"
                size="sm"
                className="h-7 rounded-full px-3 text-xs font-normal"
                onClick={() => setRegenOpen(true)}
              >
                <RefreshCw className="mr-1 size-3" />
                重新生成
              </Button> */}
            </div>
          )}
        </CardContent>
      </Card>
    </>
  )
}
