import { Plug } from "lucide-react"
import { useTranslation } from "react-i18next"

import { LabsIntroBanner } from "@/components/labs/shared/LabsIntroBanner"

const GLOSSARY_KEYS = ["server", "tool", "transport", "detection"] as const

export function McpIntroBanner() {
  const { t } = useTranslation()

  return (
    <LabsIntroBanner
      icon={Plug}
      title={t("labs.mcp.intro.title")}
      summary={t("labs.mcp.intro.summary")}
      links={[
        {
          label: t("labs.mcp.intro.linkOfficial"),
          href: "https://modelcontextprotocol.io/introduction",
        },
        {
          label: t("labs.mcp.intro.linkCursor"),
          href: "https://cursor.com/docs/mcp",
        },
        {
          label: t("labs.mcp.intro.linkClaudeCode"),
          href: "https://docs.claude.com/en/docs/claude-code/mcp",
        },
        {
          label: t("labs.mcp.intro.linkServers"),
          href: "https://github.com/modelcontextprotocol/servers",
        },
      ]}
    >
      <dl className="grid gap-x-8 gap-y-2 md:grid-cols-2">
        {GLOSSARY_KEYS.map((key) => (
          <div
            key={key}
            className="grid min-w-0 grid-cols-[max-content_minmax(0,1fr)] gap-x-2 text-xs leading-5"
          >
            <dt className="font-medium text-[var(--color-text-primary)]">
              {t(`labs.mcp.glossary.${key}.term`)}
            </dt>
            <dd className="min-w-0 [overflow-wrap:anywhere] text-[var(--color-text-secondary)]">
              {t(`labs.mcp.glossary.${key}.desc`)}
            </dd>
          </div>
        ))}
      </dl>
    </LabsIntroBanner>
  )
}
