export interface AutoUpdateDownload {
  label: string
  platform: string
  arch: string
  url_template?: string
  target_filename?: string
  executable?: boolean
}

export interface AutoUpdateAgent {
  latest_url?: string
}

export type AutoUpdateFormat = "shell" | "dockerfile"

export function shellQuote(value: string): string {
  return "'" + value.replace(/'/g, "'\"'\"'") + "'"
}

function shellVersionedUrl(urlTemplate: string): string {
  return urlTemplate.split("{version}").map(shellQuote).join('"$VERSION"')
}

function targetPath(installDir: string, targetFilename: string): string {
  const normalizedDir = installDir.trim().replace(/\/+$/, "")
  return normalizedDir
    ? `${normalizedDir}/${targetFilename}`
    : `/${targetFilename}`
}

export function canBuildAutoUpdateScript(
  agent: AutoUpdateAgent,
  download: AutoUpdateDownload | undefined,
  installDir: string
): download is AutoUpdateDownload & {
  url_template: string
  target_filename: string
} {
  return Boolean(
    agent.latest_url &&
    download?.url_template &&
    download.target_filename &&
    installDir.trim()
  )
}

export function buildAutoUpdateScript(
  agent: AutoUpdateAgent,
  download: AutoUpdateDownload,
  installDir: string,
  format: AutoUpdateFormat = "shell"
): string {
  const latestUrl = agent.latest_url ?? ""
  const urlTemplate = download.url_template ?? ""
  const targetFilename = download.target_filename ?? ""
  const outputPath = targetPath(installDir, targetFilename)
  const downloadCommand = `curl -fsSL --retry 3 --create-dirs ${shellVersionedUrl(urlTemplate)} -o "$TARGET_PATH"`
  const installCommand = download.executable
    ? `${downloadCommand}\nchmod +x "$TARGET_PATH"`
    : downloadCommand
  const versionCommand = `VERSION="$(curl -fsSL --retry 3 ${shellQuote(latestUrl)})"`

  if (format === "dockerfile") {
    const commands = [
      "set -eu",
      `TARGET_PATH=${shellQuote(outputPath)}`,
      versionCommand,
      downloadCommand,
      ...(download.executable ? ['chmod +x "$TARGET_PATH"'] : []),
    ]
    return `RUN ${commands.join("; \\\n    ")}\n`
  }

  return `#!/bin/sh
set -eu

TARGET_PATH=${shellQuote(outputPath)}
${versionCommand}
${installCommand}
`
}
