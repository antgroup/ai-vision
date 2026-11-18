import { writeClipboardText } from "@/lib/clipboard"
import { useEffect, useState } from "react"
import { useTranslation } from "react-i18next"
import { Link } from "react-router-dom"
import { ArrowLeft, ArrowUpRight, Copy, RefreshCw } from "lucide-react"
import ReactMarkdown from "react-markdown"
import remarkGfm from "remark-gfm"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent } from "@/components/ui/card"
import {
  compareBuilds,
  frontendBuildInfo,
  installedRelease,
  parseVersionObservation,
  versionReport,
  type BackendState,
  type BuildInfo,
} from "./versionInfo"
import {
  parseReleaseResponse,
  RELEASES_URL,
  updateStatus,
  type ReleaseState,
} from "./releaseInfo"

function BuildDetails({ info }: { info: BuildInfo }) {
  const { t } = useTranslation()
  const values = [
    ["productVersion", info.product_version],
    ["componentVersion", info.component_version],
    ["channel", info.target_channel === "unknown" ? null : info.target_channel],
    ["source", info.source_sha],
    ["buildId", info.build_id],
    ["builtAt", info.built_at],
    [
      "worktree",
      info.dirty === null
        ? null
        : t(info.dirty ? "about.dirty" : "about.clean"),
    ],
  ]
  return (
    <dl className="space-y-3">
      {values.map(([field, value]) => (
        <div
          key={field}
          className="grid gap-1 sm:grid-cols-[8rem_1fr] sm:gap-4"
        >
          <dt className="text-muted-foreground">
            {t(`about.fields.${field}`)}
          </dt>
          <dd className="min-w-0 font-mono text-xs leading-5 break-all">
            {value ?? t("about.unknown")}
          </dd>
        </div>
      ))}
    </dl>
  )
}

export default function AboutPage() {
  const { t } = useTranslation()
  const [frontend] = useState(frontendBuildInfo)
  const [backend, setBackend] = useState<BackendState>({
    status: "loading",
    observation: null,
  })
  const [release, setRelease] = useState<ReleaseState>({
    status: "loading",
    latest: null,
    checked_at: null,
  })
  const [backendRefresh, setBackendRefresh] = useState(0)
  const [releaseRefresh, setReleaseRefresh] = useState(0)
  const [copyState, setCopyState] = useState<
    "idle" | "copying" | "copied" | "failed"
  >("idle")

  useEffect(() => {
    let active = true
    const controller = new AbortController()
    const timeout = setTimeout(() => controller.abort(), 10_000)
    async function readBackend() {
      try {
        const response = await fetch("/api/v1/version", {
          credentials: "same-origin",
          cache: "no-store",
          headers: { Accept: "application/json" },
          signal: controller.signal,
        })
        if (!response.ok) throw new Error("Version request failed")
        const observation = parseVersionObservation(await response.json())
        if (active) setBackend({ status: "ready", observation })
      } catch {
        if (active) setBackend({ status: "unavailable", observation: null })
      } finally {
        clearTimeout(timeout)
      }
    }
    void readBackend()
    return () => {
      active = false
      clearTimeout(timeout)
      controller.abort()
    }
  }, [backendRefresh])

  useEffect(() => {
    let active = true
    const controller = new AbortController()
    const timeout = setTimeout(() => controller.abort(), 10_000)
    async function readReleases() {
      try {
        const response = await fetch("/api/v1/releases", {
          credentials: "same-origin",
          cache: "no-store",
          headers: { Accept: "application/json" },
          signal: controller.signal,
        })
        if (!response.ok) throw new Error("Release request failed")
        const next = parseReleaseResponse(await response.json())
        if (active) setRelease(next)
      } catch {
        if (active)
          setRelease({ status: "unavailable", latest: null, checked_at: null })
      } finally {
        clearTimeout(timeout)
      }
    }
    void readReleases()
    return () => {
      active = false
      clearTimeout(timeout)
      controller.abort()
    }
  }, [releaseRefresh])

  async function copyReport() {
    setCopyState("copying")
    try {
      await writeClipboardText(versionReport(frontend, backend))
      setCopyState("copied")
    } catch {
      setCopyState("failed")
    }
  }

  const comparison = compareBuilds(frontend, backend)
  const installed = installedRelease(backend)
  const availableStatus = updateStatus(installed, release)
  const status =
    availableStatus === "current" && comparison !== "matched"
      ? "unknown"
      : availableStatus
  const latest = release.latest

  return (
    <main className="min-h-svh bg-background px-4 py-6 text-foreground sm:px-8 sm:py-10">
      <div className="mx-auto max-w-3xl space-y-7">
        <Button asChild variant="ghost" size="sm" className="-ml-2">
          <Link to="/">
            <ArrowLeft aria-hidden="true" />
            {t("about.back")}
          </Link>
        </Button>
        <header>
          <h1 className="text-2xl font-semibold tracking-tight">
            {t("about.title")}
          </h1>
        </header>

        <Card>
          <CardContent className="space-y-6 p-6 sm:p-8">
            <section
              aria-label={t("about.currentVersion")}
              className="space-y-2"
            >
              <p className="text-sm text-muted-foreground">
                {t("about.currentVersion")}
              </p>
              <p className="min-w-0 font-mono text-xl font-semibold tracking-tight break-words sm:text-2xl">
                {installed?.product_version ?? t("about.unknown")}
              </p>
              {installed &&
                (installed.channel !== "stable" ||
                  installed.product_version.includes("-")) && (
                  <Badge variant="secondary">{t("about.previewBuild")}</Badge>
                )}
              {backend.status !== "loading" &&
                (!installed ||
                  comparison === "unknown" ||
                  comparison === "dirty") && (
                  <p className="text-sm text-muted-foreground">
                    {t("about.installedUnknown")}
                  </p>
                )}
            </section>
            <div className="flex flex-wrap items-center justify-between gap-4 border-t pt-5">
              <section
                aria-label={t("about.updateLabel")}
                aria-live="polite"
                className="space-y-1"
              >
                <p className="text-sm font-medium">
                  {t(`about.updates.${status}`)}
                </p>
                {latest && (
                  <p className="text-sm text-muted-foreground">
                    {t("about.latestVersion", { version: latest.version })}
                  </p>
                )}
              </section>
              <Button
                type="button"
                variant="outline"
                disabled={release.status === "loading"}
                onClick={() => {
                  setRelease({
                    status: "loading",
                    latest: null,
                    checked_at: null,
                  })
                  setReleaseRefresh((value) => value + 1)
                }}
              >
                <RefreshCw
                  aria-hidden="true"
                  className={release.status === "loading" ? "animate-spin" : ""}
                />
                {t("about.checkUpdates")}
              </Button>
            </div>
            {comparison === "mixed" && (
              <p
                role="note"
                className="text-sm text-amber-700 dark:text-amber-400"
              >
                {t("about.mixedHelp")}
              </p>
            )}
          </CardContent>
        </Card>

        {latest ? (
          <section aria-label={t("about.releaseNotes")} className="space-y-5">
            <div className="flex flex-wrap items-baseline justify-between gap-2">
              <h2 className="text-lg font-semibold">
                {t("about.releaseNotes")} · {latest.version}
              </h2>
              <a
                href={latest.url}
                target="_blank"
                rel="noreferrer"
                className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground"
              >
                {t("about.fullNotes")}
                <ArrowUpRight aria-hidden="true" className="size-3.5" />
              </a>
            </div>
            <div className="prose prose-sm dark:prose-invert prose-headings:font-medium prose-a:text-primary max-w-none break-words">
              {latest.body.trim() ? (
                <ReactMarkdown
                  skipHtml
                  disallowedElements={["img"]}
                  remarkPlugins={[remarkGfm]}
                  components={{
                    a: ({ href, children }) => (
                      <a href={href} target="_blank" rel="noreferrer">
                        {children}
                      </a>
                    ),
                  }}
                >
                  {latest.body}
                </ReactMarkdown>
              ) : (
                <p className="text-sm text-muted-foreground">
                  {t("about.noNotes")}
                </p>
              )}
            </div>
          </section>
        ) : (
          <a
            href={RELEASES_URL}
            target="_blank"
            rel="noreferrer"
            className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground"
          >
            {t("about.releaseHistory")}
            <ArrowUpRight aria-hidden="true" className="size-3.5" />
          </a>
        )}

        <details className="group border-t pt-5 text-sm">
          <summary className="cursor-pointer text-muted-foreground hover:text-foreground">
            {t("about.diagnostics")}
          </summary>
          <div className="space-y-6 pt-5">
            <section
              aria-label={t("about.comparisonLabel")}
              className="text-muted-foreground"
            >
              {t(`about.comparison.${comparison}`)}
            </section>
            <div className="space-y-6">
              <section
                aria-label={t("about.frontendTitle")}
                className="space-y-4"
              >
                <h3 className="font-medium">{t("about.frontendTitle")}</h3>
                <BuildDetails info={frontend} />
              </section>
              <section
                aria-label={t("about.backendTitle")}
                className="space-y-4 border-t pt-5"
                aria-busy={backend.status === "loading"}
              >
                <h3 className="font-medium">{t("about.backendTitle")}</h3>
                {backend.status === "ready" ? (
                  <>
                    <BuildDetails info={backend.observation.backend} />
                    <p className="text-xs text-muted-foreground">
                      {t("about.observedAt")}:{" "}
                      <time dateTime={backend.observation.observed_at}>
                        {backend.observation.observed_at}
                      </time>
                    </p>
                  </>
                ) : (
                  <p className="text-muted-foreground">
                    {t(
                      backend.status === "loading"
                        ? "about.loading"
                        : "about.unavailable"
                    )}
                  </p>
                )}
              </section>
            </div>
            <div className="flex flex-wrap gap-2">
              <Button
                type="button"
                variant="outline"
                size="sm"
                disabled={backend.status === "loading"}
                onClick={() => {
                  setBackend({ status: "loading", observation: null })
                  setCopyState("idle")
                  setBackendRefresh((value) => value + 1)
                }}
              >
                <RefreshCw aria-hidden="true" />
                {t("about.refresh")}
              </Button>
              <Button
                type="button"
                variant="secondary"
                size="sm"
                disabled={copyState === "copying"}
                onClick={() => void copyReport()}
              >
                <Copy aria-hidden="true" />
                {t("about.copy")}
              </Button>
            </div>
            <p
              role="status"
              aria-live="polite"
              className="text-xs text-muted-foreground"
            >
              {copyState === "copied"
                ? t("about.copied")
                : copyState === "failed"
                  ? t("about.copyFailed")
                  : ""}
            </p>
          </div>
        </details>
      </div>
    </main>
  )
}
