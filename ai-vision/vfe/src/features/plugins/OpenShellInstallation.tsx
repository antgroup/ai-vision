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
import { Input } from "@/components/ui/input"
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetTitle,
} from "@/components/ui/sheet"
import { useOptionalUser } from "@/contexts/UserContext"
import { formatDuration } from "@/lib/formatters"
import {
  getInstallation,
  listInstallations,
  prepareInstallation,
  revokeInstallation,
} from "./installationApi"
import {
  buildInstallationCommand,
  DEFAULT_INSTALLATION_CONFIGURATION,
  installationIsTerminal,
  installationStateDirectory,
  mayPrepareInstallationAgain,
  shellQuote,
  validInstallationConfiguration,
} from "./installation"
import {
  INSTALLATION_STAGES,
  type InstallationConfiguration,
  type ManualInstallationOperation,
  type InstallationSession,
  type ManualInstallationSession,
  type PreparedInstallation,
} from "./installationTypes"

interface Props {
  spaceId: string
  canManage: boolean
  onAddConnection: (prefill: { name: string; image: string }) => void
}
const keyPrefix = "openshell-installations"
const prefix = "plugins.installation."

export function OpenShellInstallation(props: Props) {
  const actor = useOptionalUser()?.user?.user_uuid
  return (
    <InstallationContent
      key={`${props.spaceId}:${actor}:${props.canManage}`}
      {...props}
      actor={actor}
    />
  )
}

function InstallationContent({
  spaceId,
  canManage,
  actor,
  onAddConnection,
}: Props & { actor?: string }) {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const allowed = canManage && !!actor
  const alive = useRef(true)
  const launcher = useRef<HTMLButtonElement>(null)
  const handingOffConnection = useRef(false)
  useEffect(() => {
    alive.current = true
    return () => {
      alive.current = false
    }
  }, [])
  const [open, setOpen] = useState(false)
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [prepared, setPrepared] = useState<PreparedInstallation | null>(null)
  const [configuration, setConfiguration] = useState({
    ...DEFAULT_INSTALLATION_CONFIGURATION,
  })
  const [working, setWorking] = useState(false)
  const [actionError, setActionError] = useState(false)
  const [copied, setCopied] = useState(false)
  const [copyError, setCopyError] = useState(false)
  const [now, setNow] = useState(Date.now)
  useEffect(() => {
    if (!open) return
    const timer = window.setInterval(() => setNow(Date.now()), 1000)
    return () => window.clearInterval(timer)
  }, [open])
  const listKey = [keyPrefix, spaceId, actor]
  const inventory = useQuery({
    queryKey: listKey,
    queryFn: () => listInstallations(spaceId),
    enabled: allowed,
    retry: false,
  })
  const manualItems = inventory.data?.items.filter(
    (item): item is ManualInstallationSession => item.operation !== "bootstrap"
  )
  const listed =
    manualItems?.find((item) => item.uuid === selectedId) ??
    (!selectedId ? manualItems?.[0] : undefined)
  const uuid = selectedId ?? listed?.uuid
  const detailKey = [keyPrefix, spaceId, actor, uuid]
  const detail = useQuery({
    queryKey: detailKey,
    queryFn: async () => {
      const result = await getInstallation(spaceId, uuid!)
      if (result.operation === "bootstrap")
        throw Error("Unexpected installation mode")
      return result
    },
    enabled: allowed && open && !!uuid,
    retry: false,
    refetchInterval: (query) => {
      const snapshot = query.state.data ?? listed
      return open && snapshot && !installationIsTerminal(snapshot)
        ? 2000
        : false
    },
  })
  const session = detail.data ?? listed
  const terminal = session ? installationIsTerminal(session) : false
  const statusLabel =
    session?.operation === "status" && session.status === "running"
      ? "checking"
      : session?.status
  const commandAvailable =
    !!prepared && prepared.session.uuid === session?.uuid && !terminal
  const showForm = !session && !inventory.isLoading && !inventory.isError
  const lastReceived = session?.last_received_at
    ? Date.parse(session.last_received_at)
    : null
  const stale =
    session?.status === "running" &&
    lastReceived !== null &&
    now - lastReceived >= 20_000
  const stages =
    session?.operation === "status"
      ? ["readiness" as const]
      : INSTALLATION_STAGES
  const active = session?.stages.find(
    (stage) => stage.state === "start" || stage.state === "wait"
  )

  async function generate(
    operation: ManualInstallationOperation,
    input: InstallationConfiguration
  ) {
    if (!allowed || working) return
    setWorking(true)
    setActionError(false)
    try {
      const result = await prepareInstallation(spaceId, operation, input)
      if (!alive.current) return
      // Cache only the public snapshot. The one-time reporting credential stays in component memory.
      queryClient.setQueryData(
        [keyPrefix, spaceId, actor, result.session.uuid],
        result.session
      )
      queryClient.setQueryData(
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
      setSelectedId(result.session.uuid)
      setPrepared(result)
      setCopied(false)
      setCopyError(false)
    } catch {
      if (alive.current) setActionError(true)
    } finally {
      if (alive.current) setWorking(false)
    }
  }

  async function endReporting() {
    if (!session || !allowed || working) return
    setWorking(true)
    setActionError(false)
    try {
      const updated = await revokeInstallation(spaceId, session.uuid)
      if (!alive.current) return
      queryClient.setQueryData(detailKey, updated)
      setPrepared(null)
      void queryClient.invalidateQueries({ queryKey: listKey, exact: true })
    } catch {
      if (alive.current) setActionError(true)
    } finally {
      if (alive.current) setWorking(false)
    }
  }

  async function copyCommand() {
    if (!commandAvailable || !prepared || !session) return
    try {
      await writeClipboardText(
        buildInstallationCommand(
          session.operation,
          session.configuration,
          prepared.report_path,
          prepared.report_token,
          window.location.origin
        )
      )
      if (alive.current) {
        setCopied(true)
        setCopyError(false)
      }
    } catch {
      if (alive.current) {
        setCopied(false)
        setCopyError(true)
      }
    }
  }

  if (!allowed)
    return (
      <p className="mb-6 text-xs leading-relaxed text-muted-foreground">
        {t(prefix + "readOnly")}
      </p>
    )
  return (
    <section
      className="mb-8 border-b pb-6"
      data-testid="openshell-installation"
    >
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="min-w-0 space-y-1.5">
          <h2 className="text-base font-semibold">{t(prefix + "title")}</h2>
          <p className="max-w-2xl text-sm leading-relaxed text-muted-foreground">
            {t(prefix + "description")}
          </p>
          {session && (
            <p className="text-xs text-muted-foreground">
              {session.configuration.label} ·{" "}
              {t(prefix + `status.${statusLabel}`)}
            </p>
          )}
        </div>
        <Button
          ref={launcher}
          type="button"
          size="sm"
          onClick={() => {
            setOpen(true)
            void inventory.refetch()
          }}
        >
          <TerminalSquare className="size-4" aria-hidden="true" />
          {t(prefix + (session ? "viewProgress" : "install"))}
        </Button>
      </div>
      <Sheet open={open} onOpenChange={setOpen}>
        <SheetContent
          showCloseButton={false}
          onCloseAutoFocus={(event) => {
            event.preventDefault()
            if (!handingOffConnection.current) launcher.current?.focus()
            handingOffConnection.current = false
          }}
          className="flex !w-full !max-w-full flex-col gap-0 overflow-hidden p-0 sm:!w-[560px] sm:!max-w-[92vw]"
        >
          <header className="flex shrink-0 items-start justify-between gap-4 border-b px-5 py-4 sm:px-6">
            <div className="min-w-0 space-y-1.5">
              <SheetTitle className="text-base font-semibold">
                {t(
                  prefix +
                    (session?.operation === "status" && !showForm
                      ? "checkTitle"
                      : "install")
                )}
              </SheetTitle>
              <SheetDescription className="text-xs leading-relaxed">
                {t(prefix + "hostBoundary")}
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
            {(inventory.isLoading ||
              (!!uuid && detail.isLoading && !session)) && (
              <p role="status" className="text-sm text-muted-foreground">
                {t("common.loading")}
              </p>
            )}
            {(inventory.isError || detail.isError) && (
              <div
                role="alert"
                className="mb-5 space-y-2 text-sm text-muted-foreground"
              >
                <p>{t(prefix + "loadError")}</p>
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  onClick={() => {
                    void inventory.refetch()
                    if (uuid) void detail.refetch()
                  }}
                >
                  {t("common.retry")}
                </Button>
              </div>
            )}
            {showForm ? (
              <InstallationForm
                configuration={configuration}
                onChange={setConfiguration}
                disabled={working}
                onGenerate={() => void generate("install", configuration)}
              />
            ) : (
              session && (
                <div className="space-y-6">
                  <div className="space-y-2">
                    <p
                      className="text-sm font-medium break-words"
                      role="status"
                      aria-live="polite"
                    >
                      {t(prefix + `status.${statusLabel}`)}
                    </p>
                    <p className="text-xs break-words text-muted-foreground">
                      {session.configuration.label}
                    </p>
                    {session.status === "waiting" && (
                      <p className="text-sm leading-relaxed text-muted-foreground">
                        {t(prefix + "waitingHelp")}
                      </p>
                    )}
                    {session.status === "not_ready" && (
                      <p className="text-sm leading-relaxed text-muted-foreground">
                        {t(prefix + "notReadyHelp")}
                      </p>
                    )}
                    {session.status === "ready" && (
                      <p className="text-sm leading-relaxed text-muted-foreground">
                        {t(prefix + "bindingHelp")}
                      </p>
                    )}
                    {active && (
                      <p className="text-sm">
                        {t(prefix + `stages.${active.stage}`)}{" "}
                        <span className="text-xs text-muted-foreground">
                          {t(prefix + "elapsed", {
                            duration: formatDuration(active.elapsed_ms),
                          })}
                        </span>
                      </p>
                    )}
                    {lastReceived !== null && (
                      <p className="text-xs text-muted-foreground">
                        {t(prefix + "lastUpdate", {
                          duration: formatDuration(
                            Math.max(0, now - lastReceived)
                          ),
                        })}
                      </p>
                    )}
                    {stale && (
                      <p
                        role="status"
                        className="text-sm text-[var(--color-warning)]"
                      >
                        {t(prefix + "delayed")}
                      </p>
                    )}
                  </div>
                  {commandAvailable && prepared && (
                    <div className="space-y-3 rounded-lg border bg-muted/20 p-3.5">
                      <p className="text-xs leading-relaxed text-muted-foreground">
                        {t(prefix + "commandHelp")}
                      </p>
                      <pre
                        className="max-h-40 overflow-auto rounded-md bg-background p-3 text-xs break-all whitespace-pre-wrap"
                        data-testid="installation-command-preview"
                      >
                        {buildInstallationCommand(
                          session.operation,
                          session.configuration,
                          prepared.report_path,
                          "••••••••",
                          window.location.origin
                        )}
                      </pre>
                      <p className="text-xs text-muted-foreground">
                        {t(prefix + "tokenHelp")}
                      </p>
                      <Button
                        type="button"
                        size="sm"
                        variant="outline"
                        onClick={() => void copyCommand()}
                      >
                        <Copy aria-hidden="true" className="size-3.5" />
                        {t(prefix + "copyCommand")}
                      </Button>
                      {copied && (
                        <p
                          role="status"
                          className="text-xs text-muted-foreground"
                        >
                          {t(prefix + "copied")}
                        </p>
                      )}
                      {copyError && (
                        <p role="alert" className="text-xs text-destructive">
                          {t(prefix + "copyError")}
                        </p>
                      )}
                    </div>
                  )}
                  {!terminal && !commandAvailable && (
                    <p className="rounded-md border p-3 text-xs leading-relaxed text-muted-foreground">
                      {t(prefix + "commandUnavailable")}
                    </p>
                  )}
                  <ol
                    className="space-y-0"
                    aria-label={t(prefix + "stagesLabel")}
                  >
                    {stages.map((stage) => {
                      const snapshot = session.stages.find(
                        (item) => item.stage === stage
                      )
                      const running =
                        snapshot?.state === "start" ||
                        snapshot?.state === "wait"
                      const failed = snapshot?.state === "failed"
                      const done = snapshot?.state === "done"
                      const notReady =
                        stage === "readiness" &&
                        snapshot?.detail === "not_ready"
                      const Icon =
                        failed || notReady
                          ? CircleAlert
                          : done
                            ? Check
                            : running && !terminal
                              ? Loader2
                              : Circle
                      return (
                        <li
                          key={stage}
                          aria-current={running ? "step" : undefined}
                          className="flex min-w-0 items-start gap-3 border-b py-3 last:border-0"
                        >
                          <Icon
                            aria-hidden="true"
                            className={`mt-0.5 size-4 shrink-0 ${failed ? "text-destructive" : "text-muted-foreground"} ${running && !terminal ? "animate-spin motion-reduce:animate-none" : ""}`}
                          />
                          <div className="min-w-0 flex-1">
                            <p
                              className={`text-sm ${!snapshot || snapshot.state === "pending" ? "text-muted-foreground" : ""}`}
                            >
                              {t(prefix + `stages.${stage}`)}
                            </p>
                            <p className="mt-0.5 text-xs text-muted-foreground">
                              {t(
                                prefix +
                                  (notReady
                                    ? "status.not_ready"
                                    : `stageState.${snapshot?.state ?? "pending"}`)
                              )}
                              {snapshot && snapshot.state !== "pending"
                                ? ` · ${formatDuration(snapshot.elapsed_ms)}`
                                : ""}
                              {snapshot?.detail && !notReady
                                ? ` · ${t(prefix + `details.${snapshot.detail}`)}`
                                : ""}
                            </p>
                          </div>
                        </li>
                      )
                    })}
                  </ol>
                  <details
                    className="group border-t pt-4"
                    data-testid="installation-events"
                  >
                    <summary className="flex cursor-pointer list-none items-center justify-between gap-3 rounded-sm text-sm focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none [&::-webkit-details-marker]:hidden">
                      <span>{t(prefix + "eventLog")}</span>
                      <ChevronDown
                        className="size-4 transition-transform group-open:rotate-180 motion-reduce:transition-none"
                        aria-hidden="true"
                      />
                    </summary>
                    <p className="mt-3 text-xs text-muted-foreground">
                      {t(prefix + "eventsCount", {
                        visible: session.events.length,
                        total: session.event_count,
                      })}
                    </p>
                    <div
                      className="mt-2 max-h-64 overflow-y-auto rounded-md border p-3"
                      tabIndex={0}
                      aria-label={t(prefix + "eventLog")}
                    >
                      {session.events.length === 0 && (
                        <p className="text-xs text-muted-foreground">
                          {t(prefix + "noEvents")}
                        </p>
                      )}
                      {session.events.map((event) => (
                        <p
                          key={event.seq}
                          className="mb-2 text-xs leading-relaxed break-words last:mb-0"
                        >
                          <span className="text-muted-foreground">
                            {event.seq}.{" "}
                          </span>
                          {t(prefix + `stages.${event.stage}`)} ·{" "}
                          {t(prefix + `stageState.${event.state}`)} ·{" "}
                          {formatDuration(event.elapsed_ms)}
                          {event.detail
                            ? ` · ${t(prefix + `details.${event.detail}`)}`
                            : ""}
                          {event.image
                            ? ` · ${t(prefix + "layer", { layer: event.image.layer })}: ${t(prefix + `imageStatus.${event.image.status}`)}${event.image.current && event.image.total ? ` ${event.image.current} / ${event.image.total}` : ""}`
                            : ""}
                        </p>
                      ))}
                    </div>
                  </details>
                  {terminal && (
                    <div className="space-y-3 border-t pt-4">
                      <p className="text-xs leading-relaxed text-muted-foreground">
                        {t(prefix + "recoveryHelp")}
                      </p>
                      {mayPrepareInstallationAgain(session) && (
                        <p className="text-xs leading-relaxed text-muted-foreground">
                          {t(prefix + "prepareTrackingHelp")}
                        </p>
                      )}
                      <pre className="overflow-auto rounded-md bg-muted/30 p-3 text-xs break-all whitespace-pre-wrap">{`aiv plugin status openshell --state-dir ${shellQuote(installationStateDirectory(session.configuration))}\naiv plugin logs openshell --state-dir ${shellQuote(installationStateDirectory(session.configuration))} -n 100`}</pre>
                      <div className="flex flex-wrap gap-2">
                        <Button
                          type="button"
                          size="sm"
                          variant="outline"
                          disabled={working}
                          onClick={() =>
                            void generate("status", session.configuration)
                          }
                        >
                          {t(prefix + "checkAgain")}
                        </Button>
                        {mayPrepareInstallationAgain(session) && (
                          <Button
                            type="button"
                            size="sm"
                            variant="outline"
                            disabled={working}
                            onClick={() =>
                              void generate("install", session.configuration)
                            }
                          >
                            {t(
                              prefix +
                                (session.status === "failed"
                                  ? "prepareAgain"
                                  : "prepareTracking")
                            )}
                          </Button>
                        )}
                        {session.status === "ready" && (
                          <Button
                            type="button"
                            size="sm"
                            onClick={() => {
                              handingOffConnection.current = true
                              setOpen(false)
                              onAddConnection({
                                name: session.configuration.label,
                                image: session.configuration.workload_image,
                              })
                            }}
                          >
                            {t(prefix + "addConnection")}
                          </Button>
                        )}
                      </div>
                    </div>
                  )}
                </div>
              )
            )}
            {actionError && (
              <p role="alert" className="mt-4 text-sm text-destructive">
                {t(prefix + "actionError")}
              </p>
            )}
          </div>
          <footer className="shrink-0 space-y-3 border-t px-5 py-4 sm:px-6">
            <p className="text-xs leading-relaxed text-muted-foreground">
              {t(prefix + "closeHelp")}
            </p>
            <div className="flex flex-wrap justify-between gap-2">
              <Button
                type="button"
                variant="outline"
                size="sm"
                onClick={() => setOpen(false)}
              >
                {t("common.close")}
              </Button>
              {session && !terminal && !showForm && (
                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                  disabled={working}
                  onClick={() => void endReporting()}
                >
                  {t(prefix + "endReporting")}
                </Button>
              )}
            </div>
            {session && !terminal && !showForm && (
              <p className="text-xs leading-relaxed text-muted-foreground">
                {t(prefix + "endReportingHelp")}
              </p>
            )}
          </footer>
        </SheetContent>
      </Sheet>
    </section>
  )
}

function InstallationForm({
  configuration,
  onChange,
  disabled,
  onGenerate,
}: {
  configuration: InstallationConfiguration
  onChange: (value: InstallationConfiguration) => void
  disabled: boolean
  onGenerate: () => void
}) {
  const { t } = useTranslation()
  const fields = (keys: (keyof InstallationConfiguration)[]) =>
    keys.map((key) => (
      <div key={key} className="space-y-1.5">
        <label className="text-xs font-medium" htmlFor={`installation-${key}`}>
          {t(prefix + `fields.${key}`)}
        </label>
        <Input
          id={`installation-${key}`}
          disabled={disabled}
          value={configuration[key] ?? ""}
          maxLength={key === "label" ? 128 : 1024}
          autoComplete="off"
          spellCheck={false}
          onChange={(event) =>
            onChange({
              ...configuration,
              [key]: ["port", "timeout_seconds", "docker_socket_gid"].includes(
                key
              )
                ? event.target.value === "" && key === "docker_socket_gid"
                  ? null
                  : Number(event.target.value)
                : event.target.value,
            })
          }
          {...(["port", "timeout_seconds", "docker_socket_gid"].includes(key)
            ? {
                type: "number",
                step: 1,
                min:
                  key === "docker_socket_gid"
                    ? 0
                    : key === "timeout_seconds"
                      ? 60
                      : 1,
                max:
                  key === "docker_socket_gid"
                    ? 2147483647
                    : key === "timeout_seconds"
                      ? 1800
                      : 65535,
              }
            : { type: "text" })}
        />
      </div>
    ))
  return (
    <form
      className="space-y-4"
      onSubmit={(event) => {
        event.preventDefault()
        if (!disabled && validInstallationConfiguration(configuration))
          onGenerate()
      }}
    >
      <p className="text-sm leading-relaxed text-muted-foreground">
        {t(prefix + "prerequisites")}
      </p>
      {fields([
        "label",
        "image",
        "gateway_endpoint",
        "workload_image",
        "config_dir",
      ])}
      <p className="text-xs leading-relaxed text-muted-foreground">
        {t(prefix + "configDirectoryHelp")}
      </p>
      <details className="group border-y py-3">
        <summary className="flex cursor-pointer list-none items-center justify-between gap-3 rounded-sm text-sm focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none [&::-webkit-details-marker]:hidden">
          {t(prefix + "advanced")}
          <ChevronDown
            aria-hidden="true"
            className="size-4 group-open:rotate-180"
          />
        </summary>
        <div className="mt-4 space-y-4">
          {fields([
            "docker_socket",
            "docker_socket_gid",
            "workspace",
            "port",
            "timeout_seconds",
            "gateway_token_file",
            "gateway_ca_file",
            "gateway_cert_file",
            "gateway_key_file",
          ])}
        </div>
      </details>
      <Button
        type="submit"
        size="sm"
        disabled={disabled || !validInstallationConfiguration(configuration)}
      >
        {disabled ? (
          <Loader2
            aria-hidden="true"
            className="size-4 animate-spin motion-reduce:animate-none"
          />
        ) : null}
        {t(prefix + (disabled ? "generating" : "generate"))}
      </Button>
    </form>
  )
}
