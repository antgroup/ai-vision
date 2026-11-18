import { toast } from "sonner"
import { writeClipboardText } from "@/lib/clipboard"
import { useQuery } from "@tanstack/react-query"
import {
  Download,
  Copy,
  Check,
  Loader2,
  Info,
  Tag,
  Terminal,
} from "lucide-react"
import { useMemo, useState } from "react"
import { useTranslation } from "react-i18next"
import { useSearchParams } from "react-router-dom"

import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { api } from "@/lib/api"
import {
  buildAutoUpdateScript,
  canBuildAutoUpdateScript,
  type AutoUpdateDownload,
  type AutoUpdateFormat,
} from "@/pages/downloadUpdateScript"

interface AgentDownload extends AutoUpdateDownload {
  label: string
  platform: string
  arch: string
  url: string
}

interface AgentInfo {
  key: string
  display_name: string
  version: string | null
  latest_url?: string
  downloads: AgentDownload[]
}

const PLATFORM_TAGS: Record<string, string[]> = {
  "linux-musl-arm64": ["musl"],
  "linux-musl-x64": ["musl"],
  "linux-gnu-arm64": ["glibc"],
  "linux-gnu-x64": ["glibc"],
}

const LINUX_VARIANT_TAGS: Record<string, string[]> = {
  "linux-gnu": ["glibc"],
  "linux-musl": ["musl"],
}

const PLATFORM_ORDER = ["linux-gnu", "linux-musl", "darwin", "windows"]
const OS_ORDER = ["linux", "darwin", "windows"]
const ARCH_ORDER = ["x64", "arm64"]
const SCRIPT_TOKEN_PATTERN =
  /("(?:\\.|[^"\\])*"|'[^']*'|\$\{?[A-Za-z_][A-Za-z0-9_]*\}?|--?[A-Za-z0-9][A-Za-z0-9-]*|\+[A-Za-z0-9]+|\b(?:RUN|set|curl|chmod)\b|\b[A-Z_][A-Z0-9_]*(?==))/g

function scriptTokenClass(token: string) {
  if (token === "RUN") {
    return "font-semibold text-[var(--color-auxiliary)]"
  }
  if (["set", "curl", "chmod"].includes(token)) {
    return "font-medium text-primary"
  }
  if (token.startsWith('"') || token.startsWith("'")) {
    return "text-[var(--color-success)]"
  }
  if (token.startsWith("$") || /^[A-Z_][A-Z0-9_]*$/.test(token)) {
    return "text-[var(--color-warning)]"
  }
  return "text-[var(--color-auxiliary)]"
}

function highlightScriptLine(line: string, lineIndex: number) {
  if (line.startsWith("#!") || line.trimStart().startsWith("#")) {
    return (
      <span key={lineIndex} className="block text-muted-foreground">
        {line || " "}
      </span>
    )
  }

  const parts = []
  let cursor = 0
  let tokenIndex = 0
  for (const match of line.matchAll(SCRIPT_TOKEN_PATTERN)) {
    const index = match.index
    if (index > cursor) parts.push(line.slice(cursor, index))
    parts.push(
      <span
        key={`${lineIndex}-${tokenIndex}`}
        className={scriptTokenClass(match[0])}
      >
        {match[0]}
      </span>
    )
    cursor = index + match[0].length
    tokenIndex += 1
  }
  if (cursor < line.length) parts.push(line.slice(cursor))

  return (
    <span key={lineIndex} className="block min-h-[1lh]">
      {parts.length > 0 ? parts : " "}
    </span>
  )
}

function ScriptPreview({
  value,
  format,
  label,
}: {
  value: string
  format: AutoUpdateFormat
  label: string
}) {
  return (
    <pre
      className="min-h-32 resize-y overflow-auto rounded-md border bg-muted/30 p-3 font-mono text-xs leading-relaxed text-foreground"
      aria-label={label}
      data-language={format}
      tabIndex={0}
    >
      <code>{value.split("\n").map(highlightScriptLine)}</code>
    </pre>
  )
}

function osFamily(platform: string) {
  return platform.startsWith("linux-") ? "linux" : platform
}

function resolveAgentKey(
  param: string | null,
  agents: AgentInfo[]
): string | undefined {
  if (agents.length === 0) return undefined
  if (param) {
    const normalized = param.toLowerCase().replace(/-/g, "")
    const found = agents.find(
      (a) =>
        a.key === param || a.key.replace(/-/g, "").toLowerCase() === normalized
    )
    if (found) return found.key
  }
  return agents[0].key
}

interface DownloadLatestResponse {
  code: number
  message: string
  data: {
    cdn_base: string
    agents: AgentInfo[]
  }
}

function CopyButton({ url }: { url: string }) {
  const { t } = useTranslation()
  const [copied, setCopied] = useState(false)

  const handleCopy = async () => {
    try {
      await writeClipboardText(url)
    } catch {
      setCopied(false)
      toast.error(t("common.copyFailed"))
      return
    }
    setCopied(true)
    setTimeout(() => setCopied(false), 2000)
  }

  return (
    <Button
      variant="ghost"
      size="sm"
      className="h-7 gap-1 px-2 text-xs text-muted-foreground hover:text-foreground"
      onClick={handleCopy}
    >
      {copied ? (
        <>
          <Check className="h-3.5 w-3.5 text-green-500" />
          <span className="text-green-500">{t("common.copied")}</span>
        </>
      ) : (
        <>
          <Copy className="h-3.5 w-3.5" />
          <span>{t("downloads.copyLink")}</span>
        </>
      )}
    </Button>
  )
}

function downloadKey(download: AgentDownload) {
  return `${download.platform}-${download.arch}-${download.url_template ?? download.url}`
}

function ImageAutoUpdateSection({
  agent,
  selectedDownloadKey,
  installDir,
  onDownloadChange,
  onInstallDirChange,
}: {
  agent: AgentInfo
  selectedDownloadKey: string
  installDir: string
  onDownloadChange: (value: string) => void
  onInstallDirChange: (value: string) => void
}) {
  const { t } = useTranslation()
  const [copied, setCopied] = useState(false)
  const [outputFormat, setOutputFormat] = useState<AutoUpdateFormat>("shell")
  const automationDownloads = useMemo(
    () =>
      agent.latest_url
        ? agent.downloads.filter(
            (download) => download.url_template && download.target_filename
          )
        : [],
    [agent.downloads, agent.latest_url]
  )
  const platformDownloads = useMemo(() => {
    return [...automationDownloads].sort((left, right) => {
      const platformDiff =
        PLATFORM_ORDER.indexOf(left.platform) -
        PLATFORM_ORDER.indexOf(right.platform)
      if (platformDiff !== 0) return platformDiff
      return ARCH_ORDER.indexOf(left.arch) - ARCH_ORDER.indexOf(right.arch)
    })
  }, [automationDownloads])
  const defaultDownload =
    platformDownloads.find(
      (download) => download.platform === "linux-gnu" && download.arch === "x64"
    ) ?? platformDownloads[0]
  const effectiveDownloadKey = platformDownloads.some(
    (download) => downloadKey(download) === selectedDownloadKey
  )
    ? selectedDownloadKey
    : defaultDownload
      ? downloadKey(defaultDownload)
      : ""
  const selectedDownload = platformDownloads.find(
    (download) => downloadKey(download) === effectiveDownloadKey
  )
  const osOptions = OS_ORDER.filter((os) =>
    platformDownloads.some((download) => osFamily(download.platform) === os)
  )
  const selectedOs = selectedDownload
    ? osFamily(selectedDownload.platform)
    : undefined
  const linuxVariantOptions = PLATFORM_ORDER.filter(
    (platform) =>
      platform.startsWith("linux-") &&
      platformDownloads.some((download) => download.platform === platform)
  )
  const architectureDownloads = selectedDownload
    ? platformDownloads.filter(
        (download) => download.platform === selectedDownload.platform
      )
    : []
  const isUniversalPackage =
    platformDownloads.length === 1 && platformDownloads[0]?.platform === "all"

  const handleOsChange = (os: string) => {
    const candidates = platformDownloads.filter(
      (download) => osFamily(download.platform) === os
    )
    const preferredPlatform = os === "linux" ? "linux-gnu" : os
    const nextDownload =
      candidates.find(
        (download) =>
          download.platform === preferredPlatform &&
          download.arch === selectedDownload?.arch
      ) ??
      candidates.find((download) => download.arch === selectedDownload?.arch) ??
      candidates.find(
        (download) =>
          download.platform === preferredPlatform && download.arch === "x64"
      ) ??
      candidates.find((download) => download.arch === "x64") ??
      candidates[0]
    if (nextDownload) onDownloadChange(downloadKey(nextDownload))
  }

  const handleLinuxVariantChange = (platform: string) => {
    const nextDownload =
      platformDownloads.find(
        (download) =>
          download.platform === platform &&
          download.arch === selectedDownload?.arch
      ) ??
      platformDownloads.find(
        (download) => download.platform === platform && download.arch === "x64"
      ) ??
      platformDownloads.find((download) => download.platform === platform)
    if (nextDownload) onDownloadChange(downloadKey(nextDownload))
  }
  const canBuildScript = canBuildAutoUpdateScript(
    agent,
    selectedDownload,
    installDir
  )
  const script = canBuildScript
    ? buildAutoUpdateScript(agent, selectedDownload, installDir, outputFormat)
    : ""

  if (!agent.latest_url || automationDownloads.length === 0) {
    return null
  }

  const handleCopyScript = async () => {
    if (!canBuildScript || !script) return
    try {
      await writeClipboardText(script)
    } catch {
      setCopied(false)
      toast.error(t("common.copyFailed"))
      return
    }
    setCopied(true)
    setTimeout(() => setCopied(false), 2000)
  }

  return (
    <div className="rounded-md border">
      <div className="flex items-center gap-3 border-b bg-muted/40 px-4 py-3">
        <Terminal className="size-4 shrink-0 text-muted-foreground" />
        <h2 className="text-base font-semibold">
          {t("downloads.autoUpdate.title")}
        </h2>
      </div>
      <div className="space-y-4 px-4 py-4">
        {isUniversalPackage ? (
          <div className="inline-flex w-fit rounded-full bg-muted px-3 py-1.5 text-xs font-medium">
            {platformDownloads[0].label}
          </div>
        ) : (
          <div className="space-y-3">
            <fieldset className="space-y-1.5">
              <legend className="text-xs font-medium text-muted-foreground">
                {t("downloads.autoUpdate.osLabel")}
              </legend>
              <div className="inline-flex max-w-full flex-wrap gap-0.5 rounded-full bg-muted p-0.5">
                {osOptions.map((os) => {
                  const checked = selectedOs === os
                  return (
                    <label
                      key={os}
                      className={`flex cursor-pointer items-center gap-1.5 rounded-full px-3 py-1.5 text-xs font-medium transition-colors ${
                        checked
                          ? "bg-background text-foreground shadow-sm"
                          : "text-muted-foreground hover:text-foreground"
                      }`}
                    >
                      <input
                        type="radio"
                        name={`auto-update-os-${agent.key}`}
                        value={os}
                        checked={checked}
                        onChange={() => handleOsChange(os)}
                        className="sr-only"
                      />
                      {t(`downloads.autoUpdate.platforms.${os}`)}
                    </label>
                  )
                })}
              </div>
            </fieldset>

            <div
              className={`grid gap-3 ${selectedOs === "linux" ? "sm:grid-cols-2" : ""}`}
            >
              <fieldset className="space-y-1.5">
                <legend className="text-xs font-medium text-muted-foreground">
                  {t("downloads.autoUpdate.archLabel")}
                </legend>
                <div className="inline-flex rounded-full bg-muted p-0.5">
                  {architectureDownloads.map((download) => {
                    const key = downloadKey(download)
                    const checked = key === effectiveDownloadKey
                    return (
                      <label
                        key={key}
                        className={`cursor-pointer rounded-full px-3 py-1.5 text-xs font-medium transition-colors ${
                          checked
                            ? "bg-background text-foreground shadow-sm"
                            : "text-muted-foreground hover:text-foreground"
                        }`}
                      >
                        <input
                          type="radio"
                          name={`auto-update-arch-${agent.key}`}
                          value={download.arch}
                          checked={checked}
                          onChange={() => onDownloadChange(key)}
                          className="sr-only"
                        />
                        {t(
                          `downloads.autoUpdate.architectures.${download.arch}`
                        )}
                      </label>
                    )
                  })}
                </div>
              </fieldset>

              {selectedOs === "linux" && (
                <fieldset className="space-y-1.5">
                  <legend className="text-xs font-medium text-muted-foreground">
                    {t("downloads.autoUpdate.linuxVariantLabel")}
                  </legend>
                  <div className="inline-flex max-w-full flex-wrap gap-0.5 rounded-full bg-muted p-0.5">
                    {linuxVariantOptions.map((platform) => {
                      const checked = selectedDownload?.platform === platform
                      const tags = LINUX_VARIANT_TAGS[platform]
                      const variant = platform.replace("linux-", "")
                      return (
                        <label
                          key={platform}
                          className={`flex cursor-pointer items-center gap-1.5 rounded-full px-3 py-1.5 text-xs font-medium transition-colors ${
                            checked
                              ? "bg-background text-foreground shadow-sm"
                              : "text-muted-foreground hover:text-foreground"
                          }`}
                        >
                          <input
                            type="radio"
                            name={`auto-update-linux-variant-${agent.key}`}
                            value={platform}
                            checked={checked}
                            onChange={() => handleLinuxVariantChange(platform)}
                            className="sr-only"
                          />
                          {t(`downloads.autoUpdate.linuxVariants.${variant}`)}
                          {tags?.map((tag) => (
                            <span
                              key={tag}
                              className="rounded-full bg-primary/10 px-1.5 py-0.5 text-[11px] leading-none text-primary"
                            >
                              {tag}
                            </span>
                          ))}
                        </label>
                      )
                    })}
                  </div>
                </fieldset>
              )}
            </div>
          </div>
        )}

        <div className="grid gap-3 sm:grid-cols-2">
          <div className="flex flex-col gap-1.5">
            <span
              id={`auto-update-format-label-${agent.key}`}
              className="h-4 text-xs leading-4 font-medium text-muted-foreground"
            >
              {t("downloads.autoUpdate.formatLabel")}
            </span>
            <div
              role="radiogroup"
              aria-labelledby={`auto-update-format-label-${agent.key}`}
              className="grid h-9 grid-cols-2 gap-2"
            >
              {(["shell", "dockerfile"] as const).map((format) => {
                const checked = outputFormat === format
                return (
                  <label
                    key={format}
                    className={`flex h-9 cursor-pointer items-center gap-2 rounded-md border px-3 text-sm transition-colors ${
                      checked
                        ? "border-primary bg-primary/5"
                        : "hover:bg-muted/40"
                    }`}
                  >
                    <input
                      type="radio"
                      name={`auto-update-format-${agent.key}`}
                      value={format}
                      checked={checked}
                      onChange={() => setOutputFormat(format)}
                      className="h-3.5 w-3.5 accent-primary"
                    />
                    {t(`downloads.autoUpdate.formats.${format}`)}
                  </label>
                )
              })}
            </div>
          </div>
          <label className="flex flex-col gap-1.5">
            <span className="h-4 text-xs leading-4 font-medium text-muted-foreground">
              {t("downloads.autoUpdate.installDirLabel")}
            </span>
            <Input
              value={installDir}
              onChange={(event) => onInstallDirChange(event.target.value)}
              placeholder={t("downloads.autoUpdate.installDirPlaceholder")}
              className="h-9 font-mono"
            />
          </label>
        </div>

        <ScriptPreview
          value={script || t("downloads.autoUpdate.previewPlaceholder")}
          format={outputFormat}
          label={t("downloads.autoUpdate.previewLabel")}
        />

        <div className="flex flex-col items-start gap-3 sm:flex-row sm:items-center sm:justify-between">
          <p className="text-xs text-muted-foreground">
            {t("downloads.autoUpdate.stepsNote")}
          </p>
          <Button
            type="button"
            size="sm"
            className="shrink-0 gap-2 self-end sm:self-auto"
            disabled={!canBuildScript}
            onClick={handleCopyScript}
          >
            {copied ? (
              <>
                <Check className="h-4 w-4" />
                {t("downloads.autoUpdate.copied")}
              </>
            ) : (
              <>
                <Copy className="h-4 w-4" />
                {t("downloads.autoUpdate.copyScript")}
              </>
            )}
          </Button>
        </div>
      </div>
    </div>
  )
}

function AgentReleaseSection({ agent }: { agent: AgentInfo }) {
  const { t } = useTranslation()
  return (
    <div className="rounded-md border">
      <div className="flex items-center gap-3 border-b bg-muted/40 px-4 py-3">
        <h2 className="text-base font-semibold">{agent.display_name}</h2>
        <span className="rounded-full bg-primary/10 px-2.5 py-0.5 text-xs font-medium text-primary">
          v{agent.version}
        </span>
        <span className="text-xs text-muted-foreground">
          {t("downloads.commonEdition")}
        </span>
      </div>
      <div className="divide-y">
        {agent.downloads.map((dl) => {
          const filename = dl.url.split("/").pop() ?? ""
          const tags = PLATFORM_TAGS[`${dl.platform}-${dl.arch}`]
          return (
            <div
              key={`${dl.platform}-${dl.arch}-${dl.url}`}
              className="flex items-center justify-between px-4 py-2.5 hover:bg-muted/30"
            >
              <div className="flex items-center gap-3">
                <a
                  href={dl.url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="font-mono text-sm text-primary hover:underline"
                >
                  {filename}
                </a>
                <span className="text-xs text-muted-foreground">
                  {dl.label}
                </span>
                {tags?.map((tag) => (
                  <span
                    key={tag}
                    className="inline-flex items-center gap-1 rounded-full bg-primary/10 px-2.5 py-0.5 text-xs font-medium text-primary"
                  >
                    <Tag className="h-3 w-3" />
                    {tag}
                  </span>
                ))}
              </div>
              <div className="flex items-center gap-1">
                <CopyButton url={dl.url} />
                <Button variant="ghost" size="sm" className="h-7 px-2" asChild>
                  <a
                    href={dl.url}
                    target="_blank"
                    rel="noopener noreferrer"
                    aria-label={t("downloads.downloadFile", { filename })}
                  >
                    <Download className="h-3.5 w-3.5" />
                  </a>
                </Button>
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}

export default function DownloadPage() {
  const [searchParams, setSearchParams] = useSearchParams()
  const [downloadSelection, setDownloadSelection] = useState({
    agentKey: "",
    downloadKey: "",
  })
  const { t } = useTranslation()
  const [installDir, setInstallDir] = useState("/tool/bin/")
  const { data, isLoading, error } = useQuery({
    queryKey: ["download-latest"],
    queryFn: () => api.get<DownloadLatestResponse>("/download/latest"),
    staleTime: 60_000,
  })

  const agents = (data?.data?.agents ?? []).filter(
    (a) => a.version && a.downloads.length > 0
  )

  const activeKey = resolveAgentKey(searchParams.get("agent"), agents)
  const selectedDownloadKey =
    downloadSelection.agentKey === activeKey
      ? downloadSelection.downloadKey
      : ""

  const handleTabChange = (key: string) => {
    const next = new URLSearchParams(searchParams)
    next.set("agent", key)
    setDownloadSelection({ agentKey: key, downloadKey: "" })
    setSearchParams(next)
  }

  return (
    <div className="mx-auto w-full max-w-5xl space-y-6 px-6 py-8">
      <h1 className="text-2xl font-bold">{t("downloads.title")}</h1>

      <div className="flex items-start gap-2.5 rounded-md border border-blue-200 bg-blue-50 px-4 py-3 dark:border-blue-900 dark:bg-blue-950/30">
        <Info className="mt-0.5 h-4 w-4 shrink-0 text-blue-600 dark:text-blue-400" />
        <div className="text-sm text-blue-800 dark:text-blue-300">
          <p>{t("downloads.editionDescription")}</p>
          <p className="mt-1">{t("downloads.endpointHint")}</p>
        </div>
      </div>

      {isLoading && (
        <div className="flex items-center justify-center py-16">
          <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
        </div>
      )}

      {error && (
        <div className="rounded-md border border-destructive/30 bg-destructive/5 px-4 py-3 text-sm text-destructive">
          {t("downloads.loadFailed")}
        </div>
      )}

      {!isLoading && !error && agents.length === 0 && (
        <div className="rounded-md border px-4 py-8 text-center text-sm text-muted-foreground">
          {t("downloads.empty")}
        </div>
      )}

      {!isLoading && !error && agents.length > 0 && activeKey && (
        <Tabs value={activeKey} onValueChange={handleTabChange}>
          <TabsList>
            {agents.map((agent) => (
              <TabsTrigger key={agent.key} value={agent.key}>
                {agent.display_name}
              </TabsTrigger>
            ))}
          </TabsList>
          {agents.map((agent) => (
            <TabsContent key={agent.key} value={agent.key}>
              <div className="space-y-4">
                <AgentReleaseSection agent={agent} />
                <ImageAutoUpdateSection
                  agent={agent}
                  selectedDownloadKey={selectedDownloadKey}
                  installDir={installDir}
                  onDownloadChange={(downloadKey) =>
                    setDownloadSelection({
                      agentKey: agent.key,
                      downloadKey,
                    })
                  }
                  onInstallDirChange={setInstallDir}
                />
              </div>
            </TabsContent>
          ))}
        </Tabs>
      )}
    </div>
  )
}
