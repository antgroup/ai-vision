import { useId, useState } from "react"
import { useTranslation } from "react-i18next"
import { Loader2 } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import {
  Dialog,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { DialogContent } from "@/components/dialogs/DialogContent"
import { normalizeModelEndpoint } from "./providers"
import { DEFAULT_MODEL_VALIDATION_OPTIONS } from "@/components/diagnose/modelValidation"
import { ModelValidationSummary } from "@/components/diagnose/ModelValidationSummary"
import type { ModelConnection, ModelValidationOptions } from "./types"

export function ModelValidationDialog({
  connection,
  pending,
  error,
  onRun,
  onClose,
}: {
  connection: ModelConnection
  pending: boolean
  error?: string
  onRun: (options: ModelValidationOptions) => void
  onClose: () => void
}) {
  const { t } = useTranslation()
  const id = useId()
  const isOfficialAnthropic =
    normalizeModelEndpoint(connection.endpoint) ===
    "https://api.anthropic.com/v1"
  const [options, setOptions] = useState<ModelValidationOptions>(() => ({
    ...DEFAULT_MODEL_VALIDATION_OPTIONS,
  }))
  const valid =
    Number.isInteger(options.max_tokens) &&
    options.max_tokens >= 256 &&
    options.max_tokens <= 8192 &&
    Number.isFinite(options.temperature) &&
    options.temperature >= 0 &&
    options.temperature <= 2 &&
    Number.isFinite(options.request_timeout_sec) &&
    options.request_timeout_sec >= 10 &&
    options.request_timeout_sec <= 60
  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent
        size="workspace"
        className="flex flex-col gap-0 overflow-hidden p-0"
      >
        <DialogHeader className="shrink-0 border-b px-6 pt-6 pr-14 pb-4">
          <DialogTitle
            className="min-w-0 truncate leading-normal"
            title={connection.name}
          >
            {t("modelConnections.test")} · {connection.name}
          </DialogTitle>
        </DialogHeader>
        <div className="min-h-0 space-y-5 overflow-y-auto overscroll-contain px-6 py-5">
          <DialogDescription>
            {t("modelConnections.validation.description")}
          </DialogDescription>
          <div className="grid items-start gap-6 md:grid-cols-[minmax(0,0.9fr)_minmax(0,1.1fr)]">
            <div className="min-w-0 space-y-4">
              <dl className="space-y-3 text-sm">
                <div>
                  <dt className="text-xs text-muted-foreground">
                    {t("modelConnections.name")}
                  </dt>
                  <dd className="mt-1 break-all">{connection.name}</dd>
                </div>
                <div>
                  <dt className="text-xs text-muted-foreground">
                    {t("modelConnections.model")}
                  </dt>
                  <dd className="mt-1 break-all">{connection.modelName}</dd>
                </div>
              </dl>
              <div className="rounded-lg border bg-muted/30 p-3 text-sm">
                <p className="font-medium">
                  {t("modelConnections.validation.scope")}
                </p>
                <p className="mt-1 text-xs leading-relaxed text-muted-foreground">
                  {t("modelConnections.validation.costHint")}
                </p>
                <ul className="mt-2 space-y-1 text-xs">
                  <li>{t("modelConnections.validation.positiveSample")}</li>
                  <li>{t("modelConnections.validation.negativeSample")}</li>
                </ul>
              </div>
              <p className="text-xs text-muted-foreground">
                {t("modelConnections.validation.configurationSummary", {
                  tokens: options.max_tokens,
                  timeout: options.request_timeout_sec,
                })}
              </p>
              <details className="rounded-lg border p-3">
                <summary className="cursor-pointer text-sm font-medium focus-visible:outline-2 focus-visible:outline-ring">
                  {t("modelConnections.validation.advanced")}
                </summary>
                {isOfficialAnthropic ? (
                  <p className="mt-3 text-xs text-muted-foreground">
                    {t("modelConnections.anthropicHint")}
                  </p>
                ) : null}
                <fieldset
                  disabled={pending}
                  className="mt-3 grid gap-3 sm:grid-cols-2"
                >
                  <legend className="mb-2 text-sm font-medium">
                    {t("modelConnections.validation.parameters")}
                  </legend>
                  {(
                    [
                      ["max_tokens", "maxTokens", 256, 8192, 1],
                      ["temperature", "temperature", 0, 2, 0.1],
                      ["request_timeout_sec", "timeout", 10, 60, 1],
                    ] as const
                  ).map(([key, label, min, max, step]) => (
                    <div key={key} className="space-y-1">
                      <label htmlFor={`${id}-${key}`} className="text-xs">
                        {t(`modelConnections.validation.${label}`)}
                      </label>
                      <Input
                        id={`${id}-${key}`}
                        type="number"
                        disabled={isOfficialAnthropic && key === "temperature"}
                        min={min}
                        max={max}
                        step={step}
                        value={Number.isNaN(options[key]) ? "" : options[key]}
                        onChange={(event) =>
                          setOptions((current) => ({
                            ...current,
                            [key]:
                              event.target.value === ""
                                ? NaN
                                : Number(event.target.value),
                          }))
                        }
                      />
                    </div>
                  ))}
                  <div className="space-y-1">
                    <label htmlFor={`${id}-thinking`} className="text-xs">
                      {t("modelConnections.validation.thinking")}
                    </label>
                    <Select
                      value={
                        options.enable_thinking === null
                          ? "default"
                          : String(options.enable_thinking)
                      }
                      onValueChange={(value) =>
                        setOptions((current) => ({
                          ...current,
                          enable_thinking:
                            value === "default" ? null : value === "true",
                        }))
                      }
                      disabled={pending || isOfficialAnthropic}
                    >
                      <SelectTrigger id={`${id}-thinking`}>
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        {[
                          ["default", "default"],
                          ["true", "enabled"],
                          ["false", "disabled"],
                        ].map(([value, key]) => (
                          <SelectItem key={value} value={value}>
                            {t(`modelConnections.validation.${key}`)}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  </div>
                  <div className="space-y-1">
                    <label htmlFor={`${id}-reasoning`} className="text-xs">
                      {t("modelConnections.validation.reasoning")}
                    </label>
                    <Select
                      value={String(options.reasoning_effort ?? "default")}
                      onValueChange={(value) =>
                        setOptions((current) => ({
                          ...current,
                          reasoning_effort: value === "default" ? null : value,
                        }))
                      }
                      disabled={pending || isOfficialAnthropic}
                    >
                      <SelectTrigger id={`${id}-reasoning`}>
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        {["default", "low", "medium", "high"].map((value) => (
                          <SelectItem key={value} value={value}>
                            {t(`modelConnections.validation.${value}`)}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  </div>
                </fieldset>
                <p className="text-xs text-muted-foreground">
                  {t("modelConnections.validation.parametersHint")}
                </p>
              </details>
              {!valid ? (
                <p role="alert" className="text-xs text-destructive">
                  {t("modelConnections.validation.invalidOptions")}
                </p>
              ) : null}
            </div>
            <div className="min-w-0 space-y-4">
              {pending ? (
                <p role="status" className="rounded-lg border p-3 text-sm">
                  {t("modelConnections.validation.running")}
                </p>
              ) : null}
              {error ? (
                <p
                  role="alert"
                  className="rounded-lg border border-destructive/30 p-3 text-sm text-destructive"
                >
                  {t(`modelConnections.validation.reasons.${error}`)}
                </p>
              ) : null}
              <div
                className="space-y-4 rounded-xl border p-4"
                aria-live="polite"
              >
                <p className="text-sm font-medium">
                  {t("modelConnections.validation.savedResult")}
                </p>
                <p className="text-sm">
                  {t("modelConnections.validation.connection")} ·{" "}
                  {t(`modelConnections.${connection.status}`)}
                </p>
                <ModelValidationSummary
                  report={connection.diagnosticValidation}
                  details
                />
              </div>
            </div>
          </div>
        </div>
        <DialogFooter className="shrink-0 border-t px-6 py-4">
          <Button variant="outline" onClick={onClose}>
            {t("common.close")}
          </Button>
          <Button
            disabled={pending || !valid}
            onClick={() => onRun({ ...options })}
          >
            {pending ? (
              <Loader2 className="mr-1.5 size-4 animate-spin" />
            ) : null}
            {t("modelConnections.validation.run")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
