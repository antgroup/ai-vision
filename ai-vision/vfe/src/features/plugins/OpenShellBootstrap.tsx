import { writeClipboardText } from "@/lib/clipboard"
import { useEffect, useRef, useState } from "react"
import { useQuery, useQueryClient } from "@tanstack/react-query"
import { useTranslation } from "react-i18next"
import {
  Check,
  ChevronDown,
  Circle,
  CircleAlert,
  Copy,
  Loader2,
  TerminalSquare,
  X,
} from "lucide-react"
import { Button } from "@/components/ui/button"
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetTitle,
} from "@/components/ui/sheet"
import { useOptionalUser } from "@/contexts/UserContext"
import { formatDuration } from "@/lib/formatters"
import { getSandboxBinding } from "@/features/sandbox-connections/api"
import { bootstrapIsSettled, buildBootstrapCommand } from "./installation"
import {
  enableBootstrap,
  getBootstrapCapabilities,
  getInstallation,
  listInstallations,
  prepareBootstrap,
  revokeInstallation,
} from "./installationApi"
import {
  BOOTSTRAP_STAGES,
  INSTALLATION_DIAGNOSTIC_CODES,
  type BootstrapInstallationSession,
  type InstallationDiagnostic,
  type InstallationSession,
  type PreparedBootstrap,
} from "./installationTypes"

const prefix = "plugins.bootstrap."
const shared = "plugins.installation."
const diagnosticCodes = new Set<string>(INSTALLATION_DIAGNOSTIC_CODES)
const environmentFailures = new Set<string>([
  "docker_version_unsupported",
  "landlock_unavailable",
  "landlock_abi_unsupported",
  "environment_probe_failed",
  "docker_unavailable",
  "docker_permission_denied",
  "docker_context_unsupported",
  "deployment_not_found",
  "deployment_ambiguous",
])
function safeDiagnostic(
  value?: InstallationDiagnostic
): InstallationDiagnostic | undefined {
  if (!value || typeof value !== "object" || !diagnosticCodes.has(value.code))
    return
  const version = (raw?: string) =>
    typeof raw === "string" &&
    raw.length <= 64 &&
    /^[0-9]+(?:\.[0-9]+)*$/.test(raw)
      ? raw
      : undefined
  return {
    code: value.code,
    actual: version(value.actual),
    required: version(value.required),
  }
}
interface Props {
  spaceId: string
  canManage: boolean
}

export function OpenShellBootstrap(props: Props) {
  const actor = useOptionalUser()?.user?.user_uuid
  return (
    <BootstrapContent
      key={`${props.spaceId}:${actor}:${props.canManage}`}
      {...props}
      actor={actor}
    />
  )
}

function BootstrapContent({
  spaceId,
  canManage,
  actor,
}: Props & { actor?: string }) {
  const { t } = useTranslation()
  const client = useQueryClient()
  const allowed = canManage && !!actor
  const alive = useRef(true)
  const launcher = useRef<HTMLButtonElement>(null)
  useEffect(() => {
    alive.current = true
    return () => {
      alive.current = false
    }
  }, [])
  const [open, setOpen] = useState(false)
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [prepared, setPrepared] = useState<PreparedBootstrap | null>(null)
  const [working, setWorking] = useState(false)
  const [error, setError] = useState<"prepare" | "enable" | "revoke" | null>(
    null
  )
  const [copyStatus, setCopyStatus] = useState<"copied" | "error" | null>(null)
  const [now, setNow] = useState(Date.now)
  useEffect(() => {
    if (!open) return
    const timer = window.setInterval(() => setNow(Date.now()), 1000)
    return () => window.clearInterval(timer)
  }, [open])
  const listKey = ["openshell-installations", spaceId, actor]
  const capability = useQuery({
    queryKey: ["sandbox-bootstrap", spaceId, actor],
    queryFn: () => getBootstrapCapabilities(spaceId),
    enabled: allowed,
    retry: false,
  })
  const inventory = useQuery({
    queryKey: listKey,
    queryFn: () => listInstallations(spaceId),
    enabled: allowed,
    retry: false,
  })
  const items = inventory.data?.items.filter(
    (item): item is BootstrapInstallationSession =>
      item.operation === "bootstrap"
  )
  const listed =
    items?.find((item) => item.uuid === selectedId) ??
    (!selectedId ? items?.[0] : undefined)
  const uuid = selectedId ?? listed?.uuid
  const detailKey = ["openshell-installations", spaceId, actor, uuid]
  const detail = useQuery({
    queryKey: detailKey,
    queryFn: async () => {
      const result = await getInstallation(spaceId, uuid!)
      if (result.operation !== "bootstrap")
        throw Error("Unexpected installation mode")
      return result
    },
    enabled: allowed && open && !!uuid,
    retry: false,
    refetchInterval: (query) => {
      const snapshot = query.state.data ?? listed
      return open && snapshot && !bootstrapIsSettled(snapshot) ? 2000 : false
    },
  })
  const session = detail.data ?? listed
  const settled = session ? bootstrapIsSettled(session) : false
  const detailText = (value: string) =>
    t(
      prefix +
        "details." +
        ([
          "cached",
          "pulling",
          "pulled",
          "ready",
          "not_ready",
          "timeout",
          "canceled",
          "not_installed",
        ].includes(value)
          ? value
          : "unknown")
    )
  const registered =
    session?.registration_state === "saved" && !!session.connection_uuid
  const canEnable = registered
  const bindingKey = ["sandbox-binding", spaceId, actor]
  const binding = useQuery({
    queryKey: bindingKey,
    queryFn: () => getSandboxBinding(spaceId),
    enabled: allowed && open && !!canEnable,
    retry: false,
  })
  const bound =
    !!canEnable &&
    !binding.isFetching &&
    !binding.isError &&
    binding.data?.connection_uuid === session?.connection_uuid
  const commandAvailable =
    prepared?.session.uuid === session?.uuid && session?.status === "waiting"
  const lastReceived = session?.last_received_at
    ? Date.parse(session.last_received_at)
    : null
  const delayed =
    !!session &&
    !settled &&
    lastReceived !== null &&
    now - lastReceived >= 20_000
  const waitingForHost =
    session?.status === "waiting" &&
    lastReceived === null &&
    now - Date.parse(session.created_at) >= 20_000
  const active = session?.stages.find(
    (stage) => stage.state === "start" || stage.state === "wait"
  )
  const failed = session?.stages.find((stage) => stage.state === "failed")
  const unready = session?.stages.find(
    (stage) =>
      stage.stage === "readiness" &&
      stage.state === "done" &&
      stage.detail === "not_ready"
  )
  const failureEvent = [...(session?.events ?? [])]
    .reverse()
    .find(
      (event) =>
        event.state === "failed" && (!failed || event.stage === failed.stage)
    )
  const failureDiagnostic =
    safeDiagnostic(failed?.diagnostic) ??
    (unready?.diagnostic?.code === "provider_not_ready"
      ? safeDiagnostic(unready.diagnostic)
      : undefined) ??
    safeDiagnostic(failureEvent?.diagnostic) ??
    (session?.status === "not_ready"
      ? { code: "provider_not_ready" as const }
      : undefined)
  const environmentFailure =
    !!failureDiagnostic && environmentFailures.has(failureDiagnostic.code)
  const failureStage =
    failed?.stage ??
    unready?.stage ??
    failureEvent?.stage ??
    (session?.status === "not_ready" ? "readiness" : undefined)
  const completedStages =
    session?.stages.filter(
      (stage) =>
        stage.state === "done" &&
        stage.detail !== "not_ready" &&
        (stage.stage !== "registration" || registered)
    ).length ?? 0
  const elapsed = Math.max(
    0,
    ...(session?.stages.map((stage) => stage.elapsed_ms) ?? [])
  )
  const imageProgress =
    active?.stage === "images" && !settled
      ? [...(session?.events ?? [])]
          .reverse()
          .find((event) => event.stage === "images" && event.image)?.image
      : undefined
  const providerReady =
    session?.status === "ready" ||
    session?.stages.some(
      (stage) =>
        stage.stage === "readiness" &&
        stage.state === "done" &&
        stage.detail === "ready"
    )
  const statusKey = bound
    ? "bound"
    : registered
      ? session?.status === "running"
        ? "savedFinishing"
        : "saved"
      : providerReady &&
          (session?.status === "ready" || session?.status === "running")
        ? "unregistered"
        : (session?.status ?? "intro")
  const registeredUuid = registered ? session?.connection_uuid : null
  useEffect(() => {
    if (!registeredUuid || !allowed) return
    void client.invalidateQueries({
      queryKey: ["sandbox-connections", spaceId],
    })
  }, [registeredUuid, allowed, spaceId, client])

  async function prepare() {
    if (!allowed || working || !capability.data?.available) return
    setWorking(true)
    setError(null)
    try {
      const result = await prepareBootstrap(spaceId)
      if (!alive.current) return
      client.setQueryData(
        ["openshell-installations", spaceId, actor, result.session.uuid],
        result.session
      )
      client.setQueryData(
        listKey,
        (old: { items: InstallationSession[]; total: number } | undefined) => ({
          items: [
            result.session,
            ...(old?.items.filter(
              (item) => item.uuid !== result.session.uuid
            ) ?? []),
          ].slice(0, 20),
          total: (old?.total ?? 0) + 1,
        })
      )
      // The three capabilities are never placed in a query cache or browser storage.
      setPrepared(result)
      setSelectedId(result.session.uuid)
      await copyPrepared(result)
    } catch {
      if (alive.current) setError("prepare")
    } finally {
      if (alive.current) setWorking(false)
    }
  }
  async function copyPrepared(value: PreparedBootstrap) {
    try {
      await writeClipboardText(
        buildBootstrapCommand(value, window.location.origin)
      )
      if (alive.current) setCopyStatus("copied")
    } catch {
      if (alive.current) setCopyStatus("error")
    }
  }
  async function copy() {
    if (!prepared || !commandAvailable) return
    await copyPrepared(prepared)
  }
  async function enable() {
    if (!allowed || !session?.connection_uuid || !canEnable || working) return
    const connectionUuid = session.connection_uuid
    setWorking(true)
    setError(null)
    try {
      const result = await enableBootstrap(spaceId, session.uuid)
      if (!alive.current) return
      if (
        result.connection_uuid !== connectionUuid ||
        result.binding.connection_uuid !== connectionUuid
      )
        throw Error("Unconfirmed connection")
      const readback = await getSandboxBinding(spaceId)
      if (!alive.current) return
      if (readback.connection_uuid !== connectionUuid)
        throw Error("Unconfirmed binding")
      client.setQueryData(bindingKey, readback)
      void client.invalidateQueries({
        queryKey: ["sandbox-binding", spaceId],
        exact: true,
      })
      void client.invalidateQueries({
        queryKey: ["sandbox-connections", spaceId],
      })
    } catch {
      if (alive.current) setError("enable")
    } finally {
      if (alive.current) setWorking(false)
    }
  }
  async function revoke() {
    if (!session || !allowed || working) return
    setWorking(true)
    setError(null)
    try {
      const result = await revokeInstallation(spaceId, session.uuid)
      if (!alive.current) return
      client.setQueryData(detailKey, result)
      setPrepared(null)
      void client.invalidateQueries({ queryKey: listKey, exact: true })
    } catch {
      if (alive.current) setError("revoke")
    } finally {
      if (alive.current) setWorking(false)
    }
  }
  function refresh() {
    void inventory.refetch()
    void capability.refetch()
    if (uuid) void detail.refetch()
    if (canEnable) void binding.refetch()
  }
  if (!allowed)
    return (
      <p className="mb-6 text-xs leading-relaxed text-muted-foreground">
        {t(shared + "readOnly")}
      </p>
    )
  return (
    <section className="mb-8 border-b pb-6" data-testid="openshell-bootstrap">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="min-w-0 space-y-1.5">
          <h2 className="text-base font-semibold">{t(prefix + "title")}</h2>
          <p className="max-w-2xl text-sm leading-relaxed text-muted-foreground">
            {t(prefix + "description")}
          </p>
          {session && (
            <p className="text-xs text-muted-foreground">
              {t(prefix + `status.${statusKey}`)}
            </p>
          )}
        </div>
        <Button
          ref={launcher}
          type="button"
          size="sm"
          onClick={() => {
            setOpen(true)
            refresh()
          }}
        >
          <TerminalSquare aria-hidden="true" className="size-4" />
          {t(shared + (session ? "viewProgress" : "install"))}
        </Button>
      </div>
      <Sheet open={open} onOpenChange={setOpen}>
        <SheetContent
          showCloseButton={false}
          onCloseAutoFocus={(event) => {
            event.preventDefault()
            launcher.current?.focus()
          }}
          className="flex !w-full !max-w-full flex-col gap-0 overflow-hidden p-0 sm:!w-[560px] sm:!max-w-[92vw]"
        >
          <header className="flex shrink-0 items-start justify-between gap-4 border-b px-5 py-4 sm:px-6">
            <div className="min-w-0 space-y-1.5">
              <SheetTitle className="text-base font-semibold">
                {t(shared + "install")}
              </SheetTitle>
              <SheetDescription className="text-xs leading-relaxed">
                {t(prefix + "host")}
              </SheetDescription>
            </div>
            <Button
              type="button"
              variant="ghost"
              size="icon-sm"
              aria-label={t("common.close")}
              onClick={() => setOpen(false)}
            >
              <X aria-hidden="true" className="size-4" />
              <span className="sr-only">{t("common.close")}</span>
            </Button>
          </header>
          <div className="min-h-0 flex-1 overflow-y-auto px-5 py-5 sm:px-6">
            {(inventory.isLoading || capability.isLoading) && !session && (
              <p role="status" className="text-sm text-muted-foreground">
                {t("common.loading")}
              </p>
            )}
            {(inventory.isError ||
              detail.isError ||
              (!session && capability.isError)) && (
              <div
                role="alert"
                className="mb-5 space-y-2 text-sm text-muted-foreground"
              >
                <p>{t(shared + "loadError")}</p>
                <Button
                  type="button"
                  size="sm"
                  variant="outline"
                  onClick={refresh}
                >
                  {t("common.retry")}
                </Button>
              </div>
            )}
            {!session && !inventory.isLoading && !inventory.isError && (
              <div className="space-y-5">
                <p className="text-sm leading-relaxed">
                  {t(prefix + "purpose")}
                </p>
                <ol
                  aria-label={t(prefix + "stepsLabel")}
                  className="space-y-3 border-y py-4"
                >
                  {(["copy", "run", "enable"] as const).map((step, index) => (
                    <li key={step} className="flex items-start gap-3 text-sm">
                      <span
                        aria-hidden="true"
                        className="flex size-5 shrink-0 items-center justify-center rounded-full bg-muted text-xs tabular-nums"
                      >
                        {index + 1}
                      </span>
                      <span>{t(prefix + `steps.${step}`)}</span>
                    </li>
                  ))}
                </ol>
                <div className="space-y-2">
                  <p className="text-sm leading-relaxed text-muted-foreground">
                    {t(prefix + "prerequisites")}
                  </p>
                </div>
                <p className="text-sm leading-relaxed text-muted-foreground">
                  {t(prefix + "hostRequirements")}
                </p>
                <p className="text-sm leading-relaxed text-muted-foreground">
                  {t(prefix + "automatic")}
                </p>
                <p className="text-xs leading-relaxed text-muted-foreground">
                  {t(prefix + "cliHelp")}
                </p>
                {capability.data && !capability.data.available && (
                  <p
                    role="alert"
                    className="rounded-md border p-3 text-sm leading-relaxed text-muted-foreground"
                  >
                    {t(
                      prefix +
                        `unavailable.${capability.data.reason === "deployment_unavailable" ? "deployment_unavailable" : "catalog_unavailable"}`
                    )}
                  </p>
                )}
                {capability.data?.product_version && (
                  <p className="text-xs text-muted-foreground">
                    {t(prefix + "version", {
                      version: capability.data.product_version,
                      provider: capability.data.provider_version ?? "—",
                    })}
                  </p>
                )}
              </div>
            )}
            {session && (
              <div className="space-y-5">
                <div className="space-y-2">
                  <p
                    role="status"
                    aria-live="polite"
                    className="text-sm font-medium"
                  >
                    {t(prefix + `status.${statusKey}`)}
                  </p>
                  {session.status === "waiting" && (
                    <p className="text-sm leading-relaxed text-muted-foreground">
                      {t(prefix + "waitingHelp")}
                    </p>
                  )}
                  {waitingForHost && (
                    <p
                      role="status"
                      className="text-sm leading-relaxed text-muted-foreground"
                    >
                      {t(prefix + "waitingConnectionHelp")}
                    </p>
                  )}
                  {session.status === "not_ready" && !failureDiagnostic && (
                    <p className="text-sm leading-relaxed text-muted-foreground">
                      {t(prefix + "notReadyHelp")}
                    </p>
                  )}
                  {providerReady && !registered && (
                    <p className="text-sm leading-relaxed text-muted-foreground">
                      {t(prefix + "registrationHelp")}
                    </p>
                  )}
                  {registered && (
                    <p className="text-sm leading-relaxed text-muted-foreground">
                      {t(prefix + "savedHelp")}
                    </p>
                  )}
                  {active && (
                    <p className="text-sm">
                      {t(prefix + `stages.${active.stage}`)}{" "}
                    </p>
                  )}
                  {failureDiagnostic ? (
                    <div
                      role="alert"
                      data-testid="bootstrap-failure-diagnostic"
                      className="space-y-3 rounded-md border border-destructive/30 bg-destructive/5 p-3.5 text-sm"
                    >
                      <div className="flex items-start gap-2">
                        <CircleAlert
                          aria-hidden="true"
                          className="mt-0.5 size-4 shrink-0 text-destructive"
                        />
                        <div className="space-y-1">
                          <p className="font-medium">
                            {t(
                              prefix +
                                `diagnostics.${failureDiagnostic.code}.title`
                            )}
                          </p>
                          {failureStage && (
                            <p className="text-xs text-muted-foreground">
                              {t(prefix + "failedAtStage", {
                                stage: t(prefix + `stages.${failureStage}`),
                              })}
                            </p>
                          )}
                        </div>
                      </div>
                      {(failureDiagnostic.actual ||
                        failureDiagnostic.required) && (
                        <div className="space-y-1 text-xs break-words text-muted-foreground">
                          {failureDiagnostic.actual && (
                            <p>
                              {t(
                                prefix +
                                  (failureDiagnostic.code.startsWith("docker_")
                                    ? "detectedDockerVersion"
                                    : failureDiagnostic.code.startsWith(
                                          "landlock_"
                                        )
                                      ? "detectedLandlockAbi"
                                      : "detectedValue"),
                                { value: failureDiagnostic.actual }
                              )}
                            </p>
                          )}
                          {failureDiagnostic.required && (
                            <p>
                              {t(prefix + "minimumRequired", {
                                value: failureDiagnostic.required,
                              })}
                            </p>
                          )}
                        </div>
                      )}
                      <p className="leading-relaxed">
                        {t(
                          prefix + `diagnostics.${failureDiagnostic.code}.help`
                        )}
                      </p>
                    </div>
                  ) : (
                    failed && (
                      <p role="alert" className="text-sm text-destructive">
                        {t(prefix + "failedStage", {
                          stage: t(prefix + `stages.${failed.stage}`),
                        })}
                      </p>
                    )
                  )}
                  {session.status !== "waiting" && (
                    <p className="text-xs text-muted-foreground">
                      {t(prefix + "completedStages", {
                        count: completedStages,
                        total: BOOTSTRAP_STAGES.length,
                      })}
                      {" · "}
                      {t(prefix + "totalElapsed", {
                        duration: formatDuration(elapsed),
                      })}
                    </p>
                  )}
                  {lastReceived !== null && (
                    <p className="text-xs text-muted-foreground">
                      {t(shared + "lastUpdate", {
                        duration: formatDuration(
                          Math.max(0, now - lastReceived)
                        ),
                      })}
                    </p>
                  )}
                  {delayed && (
                    <p
                      role="status"
                      className="text-sm text-[var(--color-warning)]"
                    >
                      {t(shared + "delayed")}
                    </p>
                  )}
                </div>
                {commandAvailable && prepared && (
                  <div className="space-y-3 rounded-lg border bg-muted/20 p-3.5">
                    <p className="text-xs leading-relaxed text-muted-foreground">
                      {t(prefix + "commandHelp")}
                    </p>
                    <p className="text-xs leading-relaxed text-muted-foreground">
                      {t(prefix + "cliHelp")}
                    </p>
                    <pre
                      data-testid="bootstrap-command-preview"
                      className="max-h-36 overflow-auto rounded-md bg-background p-3 text-xs break-all whitespace-pre-wrap"
                    >
                      {buildBootstrapCommand(
                        {
                          ...prepared,
                          bootstrap_token: "BOOTSTRAP_TOKEN",
                          report_token: "PROGRESS_TOKEN",
                          enrollment_token: "ENROLLMENT_TOKEN",
                        },
                        window.location.origin
                      )}
                    </pre>
                    <p className="text-xs leading-relaxed text-muted-foreground">
                      {t(prefix + "tokenHelp")}
                    </p>
                  </div>
                )}
                {session.status === "waiting" && !commandAvailable && (
                  <p className="rounded-md border p-3 text-xs leading-relaxed text-muted-foreground">
                    {t(prefix + "commandUnavailable")}
                  </p>
                )}
                {copyStatus && commandAvailable && (
                  <p
                    role={copyStatus === "error" ? "alert" : "status"}
                    className={`text-xs ${copyStatus === "error" ? "text-destructive" : "text-muted-foreground"}`}
                  >
                    {t(
                      prefix + (copyStatus === "error" ? "copyError" : "copied")
                    )}
                  </p>
                )}
                <ol aria-label={t(shared + "stagesLabel")}>
                  {BOOTSTRAP_STAGES.map((stage) => {
                    const snapshot = session.stages.find(
                      (item) => item.stage === stage
                    )
                    const running =
                      snapshot?.state === "start" || snapshot?.state === "wait"
                    const stageFailed = snapshot?.state === "failed"
                    const unready = snapshot?.detail === "not_ready"
                    const done =
                      snapshot?.state === "done" &&
                      (stage !== "registration" || registered)
                    const Icon =
                      stageFailed || unready
                        ? CircleAlert
                        : done
                          ? Check
                          : running && !settled
                            ? Loader2
                            : Circle
                    return (
                      <li
                        key={stage}
                        aria-current={running ? "step" : undefined}
                        className="flex min-w-0 items-start gap-3 border-b py-2.5 last:border-0"
                      >
                        <Icon
                          aria-hidden="true"
                          className={`mt-0.5 size-4 shrink-0 ${stageFailed ? "text-destructive" : "text-muted-foreground"} ${running && !settled ? "animate-spin motion-reduce:animate-none" : ""}`}
                        />
                        <div className="min-w-0 flex-1">
                          <p
                            className={`text-sm ${!snapshot || snapshot.state === "pending" ? "text-muted-foreground" : ""}`}
                          >
                            {t(prefix + `stages.${stage}`)}
                          </p>
                          <p className="text-xs text-muted-foreground">
                            {unready
                              ? detailText("not_ready")
                              : t(
                                  shared +
                                    `stageState.${stage === "registration" && snapshot?.state === "done" && !registered ? "wait" : (snapshot?.state ?? "pending")}`
                                )}
                            {snapshot?.detail && !unready
                              ? ` · ${detailText(snapshot.detail)}`
                              : ""}
                          </p>
                          {stage === "images" && imageProgress && (
                            <p
                              data-testid="bootstrap-image-progress"
                              role="status"
                              className="mt-1 text-xs leading-relaxed text-foreground"
                            >
                              {t(
                                shared + `imageStatus.${imageProgress.status}`
                              )}
                              {imageProgress.current && imageProgress.total
                                ? ` · ${imageProgress.current} / ${imageProgress.total}`
                                : ""}
                              <span className="block text-muted-foreground">
                                {t(shared + "layer", {
                                  layer: imageProgress.layer,
                                })}
                              </span>
                            </p>
                          )}
                        </div>
                      </li>
                    )
                  })}
                </ol>
                <details
                  className="group border-t pt-3"
                  data-testid="bootstrap-events"
                >
                  <summary className="flex cursor-pointer list-none items-center justify-between gap-3 rounded-sm text-sm focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none [&::-webkit-details-marker]:hidden">
                    {t(shared + "eventLog")}
                    <ChevronDown
                      aria-hidden="true"
                      className="size-4 group-open:rotate-180"
                    />
                  </summary>
                  <p className="mt-3 text-xs text-muted-foreground">
                    {t(shared + "eventsCount", {
                      visible: session.events.length,
                      total: session.event_count,
                    })}
                  </p>
                  <div
                    className="mt-2 max-h-56 overflow-y-auto rounded-md border p-3"
                    tabIndex={0}
                    aria-label={t(shared + "eventLog")}
                  >
                    {session.events.length === 0 && (
                      <p className="text-xs text-muted-foreground">
                        {t(shared + "noEvents")}
                      </p>
                    )}
                    {session.events.map((event) => (
                      <p
                        key={event.seq}
                        className="mb-2 text-xs leading-relaxed break-words last:mb-0"
                      >
                        {event.seq}. {t(prefix + `stages.${event.stage}`)} ·{" "}
                        {t(shared + `stageState.${event.state}`)} ·{" "}
                        {formatDuration(event.elapsed_ms)}
                        {event.detail ? ` · ${detailText(event.detail)}` : ""}
                        {event.image
                          ? ` · ${t(shared + "layer", { layer: event.image.layer })}: ${t(shared + `imageStatus.${event.image.status}`)}${event.image.current && event.image.total ? ` ${event.image.current} / ${event.image.total}` : ""}`
                          : ""}
                      </p>
                    ))}
                  </div>
                </details>
                {registered && (
                  <div className="space-y-1 rounded-md border p-3 text-xs">
                    <p className="font-medium">{t(prefix + "connection")}</p>
                    <p className="break-all text-muted-foreground">
                      {session.connection_uuid}
                    </p>
                  </div>
                )}
                {settled && !canEnable && !environmentFailure && (
                  <p className="text-xs leading-relaxed text-muted-foreground">
                    {t(prefix + "resumeHelp")}
                  </p>
                )}
                {binding.isError && (
                  <div
                    role="alert"
                    className="space-y-2 text-xs text-muted-foreground"
                  >
                    <p>{t(prefix + "bindingLoadError")}</p>
                    <Button
                      type="button"
                      size="sm"
                      variant="outline"
                      onClick={() => void binding.refetch()}
                    >
                      {t("common.retry")}
                    </Button>
                  </div>
                )}
              </div>
            )}
            {error && (
              <p role="alert" className="mt-4 text-sm text-destructive">
                {t(prefix + `errors.${error}`)}
              </p>
            )}
            {session &&
              settled &&
              !canEnable &&
              environmentFailure &&
              capability.data?.available && (
                <details
                  data-testid="bootstrap-repaired-host"
                  className="mt-5 space-y-2 border-t pt-3 text-xs text-muted-foreground"
                >
                  <summary className="cursor-pointer rounded-sm focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none">
                    {t(prefix + "repairSummary")}
                  </summary>
                  <p className="mt-2 leading-relaxed">
                    {t(prefix + "repairHelp")}
                  </p>
                  <Button
                    type="button"
                    size="sm"
                    variant="outline"
                    disabled={working}
                    onClick={() => void prepare()}
                  >
                    {t(prefix + "repairAction")}
                  </Button>
                </details>
              )}
            {session && !settled && (
              <details className="mt-5 border-t pt-3 text-xs text-muted-foreground">
                <summary className="cursor-pointer rounded-sm focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none">
                  {t(shared + "endReporting")}
                </summary>
                <p className="mt-2 leading-relaxed">
                  {t(shared + "endReportingHelp")}
                </p>
                <Button
                  type="button"
                  className="mt-2"
                  variant="ghost"
                  size="sm"
                  disabled={working}
                  onClick={() => void revoke()}
                >
                  {t(shared + "endReporting")}
                </Button>
              </details>
            )}
          </div>
          <footer
            data-testid="bootstrap-action-bar"
            className="flex min-h-14 shrink-0 items-center gap-3 border-t px-5 py-3 sm:px-6"
          >
            {session?.status === "running" && !bound && (
              <p className="min-w-0 flex-1 text-xs leading-4 text-muted-foreground">
                {t(prefix + "runningCloseHint")}
              </p>
            )}
            <div className="ml-auto flex shrink-0 items-center gap-2">
              {bound ? (
                <Button type="button" size="sm" onClick={() => setOpen(false)}>
                  {t(prefix + "done")}
                </Button>
              ) : (
                <>
                  <Button
                    type="button"
                    size="sm"
                    variant="ghost"
                    onClick={() => setOpen(false)}
                  >
                    {t("common.close")}
                  </Button>
                  {!session &&
                    capability.data?.available &&
                    !inventory.isLoading &&
                    !inventory.isError && (
                      <Button
                        type="button"
                        size="sm"
                        disabled={working}
                        onClick={() => void prepare()}
                      >
                        {t(prefix + (working ? "preparing" : "prepare"))}
                      </Button>
                    )}
                  {commandAvailable && (
                    <Button type="button" size="sm" onClick={() => void copy()}>
                      <Copy aria-hidden="true" className="size-3.5" />
                      {t(shared + "copyCommand")}
                    </Button>
                  )}
                  {canEnable && (
                    <Button
                      type="button"
                      size="sm"
                      disabled={working}
                      onClick={() => void enable()}
                    >
                      {t(prefix + (working ? "enabling" : "enable"))}
                    </Button>
                  )}
                  {session &&
                    settled &&
                    !canEnable &&
                    !environmentFailure &&
                    capability.data?.available && (
                      <Button
                        type="button"
                        size="sm"
                        disabled={working}
                        onClick={() => void prepare()}
                      >
                        {t(prefix + "prepareAgain")}
                      </Button>
                    )}
                  {session?.status === "waiting" &&
                    !commandAvailable &&
                    capability.data?.available && (
                      <Button
                        type="button"
                        size="sm"
                        disabled={working}
                        onClick={() => void prepare()}
                      >
                        {t(prefix + "regenerate")}
                      </Button>
                    )}
                </>
              )}
            </div>
          </footer>
        </SheetContent>
      </Sheet>
    </section>
  )
}
