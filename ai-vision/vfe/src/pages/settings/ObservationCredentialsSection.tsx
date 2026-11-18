import { useTranslation } from "react-i18next"
import { Card, CardContent } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import KeyCard from "./KeyCard"
import type { CredentialInfo } from "./credentialUtils"

interface ObservationCredentialsSectionProps {
  writeKey?: CredentialInfo | null
  readKey?: CredentialInfo | null
  canRevealKey: boolean
  spaceId: string
  onReveal: (credentialId: number, title: string) => void
  onCopy: (text: string, key: string) => void
  copyState: string | null
}

/**
 * 业务空间的观测摄入凭证（SpaceObservationCredential）区块。
 * 与 SpaceKey（外部消费）区分：这组 Key 与 Langfuse 兼容，用于接入观测数据。
 */
export default function ObservationCredentialsSection({
  writeKey,
  readKey,
  canRevealKey,
  spaceId,
  onReveal,
  onCopy,
  copyState,
}: ObservationCredentialsSectionProps) {
  const { t } = useTranslation()
  if (!writeKey && !readKey) {
    return null
  }

  return (
    <Card>
      <CardContent className="px-5 py-4">
        <div className="flex items-center gap-2">
          <h3 className="text-base font-medium text-foreground">
            {t("spaceSettings.credentials.compatibilityTitle")}
          </h3>
          <Badge variant="secondary" className="text-xs font-normal">
            {t("spaceSettings.credentials.compatible")}
          </Badge>
        </div>
        <p className="mt-1 text-sm text-muted-foreground">
          {t("spaceSettings.credentials.description")}
        </p>

        <div className="mt-4 grid gap-4 md:grid-cols-2">
          {writeKey && (
            <KeyCard
              label={t("spaceSettings.keys.write")}
              credential={writeKey}
              canReveal={canRevealKey}
              spaceId={spaceId}
              onReveal={onReveal}
              onCopy={onCopy}
              copyState={copyState}
            />
          )}
          {readKey && (
            <KeyCard
              label={t("spaceSettings.keys.read")}
              credential={readKey}
              canReveal={canRevealKey}
              spaceId={spaceId}
              onReveal={onReveal}
              onCopy={onCopy}
              copyState={copyState}
            />
          )}
        </div>
      </CardContent>
    </Card>
  )
}
