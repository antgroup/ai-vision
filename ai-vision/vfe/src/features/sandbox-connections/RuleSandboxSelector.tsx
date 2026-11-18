import { useId } from "react"
import { useTranslation } from "react-i18next"
import { useQuery } from "@tanstack/react-query"
import { Link } from "react-router-dom"
import { ArrowUpRight, Check, ChevronDown, Loader2 } from "lucide-react"
import { Button } from "@/components/ui/button"
import { cn } from "@/lib/utils"
import { sandboxPluginPath } from "@/features/plugins/navigation"
import { getSandboxBinding, listSandboxConnections } from "./api"
import type { SandboxSelection } from "./types"

export function RuleSandboxSelector({
  spaceId,
  enabled,
  value,
  onChange,
}: {
  spaceId: string
  enabled: boolean
  value: SandboxSelection
  onChange: (selection: SandboxSelection) => void
}) {
  const { t } = useTranslation()
  const groupId = useId()
  const statusId = `${groupId}-status`
  const connections = useQuery({
    queryKey: ["sandbox-connections", spaceId],
    queryFn: () => listSandboxConnections(spaceId),
    enabled: enabled && !!spaceId,
  })
  const binding = useQuery({
    queryKey: ["sandbox-binding", spaceId],
    queryFn: () => getSandboxBinding(spaceId),
    enabled: enabled && !!spaceId,
  })
  const items = connections.data?.items ?? []
  const requiresSandbox = binding.data?.require_sandbox === true
  const localRejected = value.mode === "local" && requiresSandbox
  const defaultConnection = items.find(
    (item) => item.uuid === binding.data?.connection_uuid
  )
  const connectionId =
    value.mode === "local"
      ? null
      : value.connection_uuid || binding.data?.connection_uuid
  const selected = items.find((item) => item.uuid === connectionId)
  const loading = connections.isLoading || binding.isLoading
  const loadError = connections.isError || binding.isError
  const settingsReady = !!connections.data && !!binding.data
  const runsLocally =
    !requiresSandbox &&
    (value.mode === "local" || (value.mode === "inherit" && !connectionId))
  const targetName = runsLocally
    ? t("sandbox.resolvedLocal")
    : selected?.name ||
      t(connectionId ? "sandbox.missingConnection" : "sandbox.noDefaultTarget")
  const ready = !runsLocally && selected?.status === "ready"
  const changeMode = (mode: SandboxSelection["mode"]) => {
    const rest = { ...value }
    delete rest.connection_uuid
    onChange({ ...rest, mode })
  }

  return (
    <div className="space-y-3">
      <fieldset id="rule-sandbox-mode" className="min-w-0">
        <legend className="sr-only">{t("sandbox.ruleMode")}</legend>
        <div className="grid grid-cols-1 gap-1 rounded-xl bg-muted/60 p-1 sm:grid-cols-3">
          {(["inherit", "required", "local"] as const).map((mode) => (
            <label
              key={mode}
              className="min-w-0 cursor-pointer"
              title={t(`sandbox.modeDescription.${mode}`)}
            >
              <input
                type="radio"
                name={groupId}
                value={mode}
                checked={value.mode === mode}
                disabled={mode === "local" && requiresSandbox}
                onChange={() => changeMode(mode)}
                className="peer sr-only"
              />
              <span className="flex min-h-9 items-center justify-center gap-1.5 rounded-lg px-3 py-1.5 text-center text-sm text-muted-foreground transition-colors peer-checked:bg-background peer-checked:font-medium peer-checked:text-foreground peer-checked:shadow-sm peer-focus-visible:ring-2 peer-focus-visible:ring-ring peer-focus-visible:ring-offset-2 peer-disabled:cursor-not-allowed peer-disabled:opacity-45">
                {value.mode === mode && (
                  <Check className="size-3.5 shrink-0" aria-hidden="true" />
                )}
                {t(`sandbox.mode.${mode}`)}
              </span>
            </label>
          ))}
        </div>
      </fieldset>

      <div
        className="space-y-2"
        data-testid="sandbox-execution-target"
        aria-live="polite"
      >
        <div className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1">
          <label
            htmlFor={
              value.mode === "required" ? "rule-sandbox-connection" : undefined
            }
            className="text-xs text-muted-foreground"
          >
            {t(
              value.mode === "required"
                ? "sandbox.ruleConnection"
                : "sandbox.effectiveTarget"
            )}
          </label>
          <Link
            to={sandboxPluginPath(spaceId)}
            className="inline-flex items-center gap-0.5 text-xs text-muted-foreground underline-offset-4 hover:text-foreground hover:underline focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
          >
            {t("sandbox.manageConnections")}
            <ArrowUpRight className="size-3" aria-hidden="true" />
          </Link>
        </div>

        {loading || (!loadError && !settingsReady) ? (
          <p
            role="status"
            className="flex min-h-9 items-center gap-2 text-xs text-muted-foreground"
          >
            <Loader2 className="size-3.5 animate-spin" aria-hidden="true" />
            {t("common.loading")}
          </p>
        ) : loadError ? (
          <div role="alert" className="space-y-2 text-xs text-muted-foreground">
            <p>{t("sandbox.ruleLoadError")}</p>
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={() => {
                void connections.refetch()
                void binding.refetch()
              }}
            >
              {t("common.retry")}
            </Button>
          </div>
        ) : (
          <>
            {!localRejected && (
              <div
                className="relative"
                title={
                  selected && !runsLocally
                    ? t(`sandbox.status.${selected.status}`)
                    : undefined
                }
              >
                {ready && (
                  <Check
                    className="pointer-events-none absolute top-1/2 left-3 z-10 size-4 -translate-y-1/2 text-[var(--color-success)]"
                    aria-hidden="true"
                  />
                )}
                {selected && !runsLocally && (
                  <span id={statusId} className="sr-only">
                    {t(`sandbox.status.${selected.status}`)}
                  </span>
                )}
                {value.mode === "required" ? (
                  <>
                    <select
                      id="rule-sandbox-connection"
                      value={value.connection_uuid ?? ""}
                      aria-describedby={selected ? statusId : undefined}
                      className={cn(
                        "h-9 w-full min-w-0 appearance-none truncate rounded-3xl border border-transparent bg-input/50 pr-9 text-sm transition-[color,box-shadow,background-color] outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/30",
                        ready ? "pl-9" : "pl-3"
                      )}
                      onChange={(event) => {
                        const rest = { ...value }
                        delete rest.connection_uuid
                        onChange(
                          event.target.value
                            ? { ...rest, connection_uuid: event.target.value }
                            : rest
                        )
                      }}
                    >
                      <option value="">
                        {defaultConnection
                          ? `${defaultConnection.name} (${t("sandbox.bound")})`
                          : t("sandbox.useDefault")}
                      </option>
                      {value.connection_uuid &&
                        !items.some(
                          (item) => item.uuid === value.connection_uuid
                        ) && (
                          <option value={value.connection_uuid}>
                            {t("sandbox.missingConnection")}
                          </option>
                        )}
                      {items.map((connection) => (
                        <option key={connection.uuid} value={connection.uuid}>
                          {connection.name}
                          {connection.uuid !== value.connection_uuid
                            ? ` · ${t(`sandbox.status.${connection.status}`)}`
                            : ""}
                        </option>
                      ))}
                    </select>
                    <ChevronDown
                      className="pointer-events-none absolute top-1/2 right-3 size-4 -translate-y-1/2 text-muted-foreground"
                      aria-hidden="true"
                    />
                  </>
                ) : (
                  <div
                    className={cn(
                      "flex min-h-9 min-w-0 items-center justify-between gap-2 rounded-3xl bg-input/50 pr-3 text-sm",
                      ready ? "pl-9" : "pl-3"
                    )}
                    aria-describedby={
                      selected && !runsLocally ? statusId : undefined
                    }
                  >
                    <span className="truncate" title={targetName}>
                      {targetName}
                    </span>
                    {selected && !value.connection_uuid && (
                      <span className="shrink-0 text-xs text-muted-foreground">
                        {t("sandbox.bound")}
                      </span>
                    )}
                  </div>
                )}
              </div>
            )}
            {!localRejected && !runsLocally && !selected && (
              <div
                role="status"
                className="space-y-1 text-xs text-muted-foreground"
              >
                {value.mode === "required" && <p>{targetName}</p>}
                <p>{t("sandbox.noDefaultHelp")}</p>
              </div>
            )}
            {!localRejected &&
              !runsLocally &&
              selected &&
              selected.status !== "ready" && (
                <p
                  role="alert"
                  className="text-xs leading-relaxed text-destructive"
                >
                  {t("sandbox.selectedUnavailable")}
                </p>
              )}
            {requiresSandbox && (
              <p
                role={localRejected ? "alert" : "status"}
                className={cn(
                  "text-xs leading-relaxed",
                  localRejected ? "text-destructive" : "text-muted-foreground"
                )}
              >
                {t("sandbox.rulePolicy")}
              </p>
            )}
          </>
        )}
      </div>
    </div>
  )
}
