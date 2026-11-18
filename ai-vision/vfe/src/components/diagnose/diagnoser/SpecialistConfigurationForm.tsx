import { useEffect, useEffectEvent, useRef, useState } from "react"
import { useTranslation } from "react-i18next"
import { useUser } from "@/contexts/UserContext"
import { ApiBusinessError } from "@/lib/api"
import { useQueryClient } from "@tanstack/react-query"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { listDiagnosers } from "../diagnoserApi"
import {
  getSpecialistCatalog,
  syncSpecialist,
  configureSpecialist,
  trialSpecialist,
} from "../specialistApi"
import {
  isSpecialistDiagnoser,
  newNumericCheck,
  nextSpecialistPolicyId,
  specialistTrialInput,
  specialistConfigurationError,
  structuredOutputFieldTypes,
} from "../specialistPolicy"
import type {
  NumericCheck,
  ProtectedMarker,
  SpecialistCatalog,
  SpecialistConfiguration,
  SpecialistKey,
  SpecialistPolicy,
  SpecialistSaved,
  SpecialistSyncInput,
  SpecialistTrialResult,
  StructuredOutputField,
} from "../specialistPolicy"
import type { DiagnoserDTO, ModelConnection } from "../types"
import { TrialResultView } from "./TrialResultView"

const selectClass =
  "h-9 w-full rounded-md border border-input bg-background px-3 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
const labelClass = "grid gap-1.5 text-xs text-muted-foreground"
interface Props {
  open: boolean
  spaceId: string
  agentUuid: string
  modelConnections: ModelConnection[]
  traceIds?: readonly string[]
  onOpenSource?: (traceId: string, spanId?: string) => void
}

export function SpecialistConfigurationForm(props: Props) {
  const { user } = useUser()
  if (!props.open || !user?.user_uuid) return null
  return (
    <ScopedSpecialistConfigurationForm
      key={JSON.stringify([
        props.spaceId,
        props.agentUuid,
        user.user_uuid,
        props.traceIds,
      ])}
      {...props}
    />
  )
}

function ScopedSpecialistConfigurationForm({
  open,
  spaceId,
  agentUuid,
  modelConnections,
  traceIds,
  onOpenSource,
}: Props) {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const { user } = useUser()
  const ownerScope = `${spaceId}:${agentUuid}:${user?.user_uuid ?? ""}`
  const [loadedScope, setLoadedScope] = useState("")
  const [dirty, setDirty] = useState(false)
  const [retainedOptions, setRetainedOptions] = useState<
    SpecialistConfiguration["llm_options"]
  >({})
  const [catalog, setCatalog] = useState<SpecialistCatalog | null>(null)
  const [savedItems, setSavedItems] = useState<DiagnoserDTO[]>([])
  const [saved, setSaved] = useState<DiagnoserDTO | null>(null)
  const [key, setKey] = useState<SpecialistKey>("answer_relevance")
  const [variant, setVariant] = useState("explicit_current_text")
  const [model, setModel] = useState("")
  const [maxTokens, setMaxTokens] = useState(4096)
  const [timeout, setTimeoutValue] = useState(120)
  const [language, setLanguage] = useState("zh-CN")
  const [terms, setTerms] = useState("")
  const [checks, setChecks] = useState<NumericCheck[]>([newNumericCheck(1)])
  const [markers, setMarkers] = useState<ProtectedMarker[]>([
    { id: "marker_1", value: "", visibility: "protected" },
  ])
  const [fields, setFields] = useState<StructuredOutputField[]>([
    { name: "", type: "string" },
  ])
  const [toolName, setToolName] = useState("")
  const [preserve, setPreserve] = useState(false)
  const [traceId, setTraceId] = useState("")
  const selectedTraceId = traceIds
    ? traceIds.length === 1
      ? traceIds[0]
      : traceIds.includes(traceId)
        ? traceId
        : ""
    : traceId.trim()
  const [result, setResult] = useState<SpecialistTrialResult | null>(null)
  const [error, setError] = useState("")
  const [notice, setNotice] = useState("")
  const [busy, setBusy] = useState(false)
  const [loading, setLoading] = useState(true)
  const [reload, setReload] = useState(0)
  const pending = useRef<number | null>(null)
  const generation = useRef(0)
  const defaultSelected = useRef(false)
  const availableModels = modelConnections.filter(
    (value) => value.status === "available" && value.apiKeyConfigured
  )
  const receiveCatalog = useEffectEvent(
    (data: SpecialistCatalog, diagnosers: DiagnoserDTO[]) => {
      const items = diagnosers.filter(isSpecialistDiagnoser)
      if (!defaultSelected.current && data.can_trial_draft) {
        const selectable = data.items
          .filter((item) => item.selectable)
          .flatMap(({ key, supported_variants }) =>
            supported_variants.map(({ variant, mode }) => ({
              key,
              variant,
              mode,
            }))
          )
        const initial =
          (availableModels.length
            ? undefined
            : selectable.find(({ mode }) => mode === "rule")) ?? selectable[0]
        if (initial) {
          defaultSelected.current = true
          setKey(initial.key as SpecialistKey)
          setVariant(initial.variant)
        }
      }
      if (saved)
        chooseSaved(items.find((item) => item.uuid === saved.uuid) ?? null)
      setLoadedScope(ownerScope)
      setCatalog(data)
      setSavedItems(items)
    }
  )
  const failCatalog = useEffectEvent(() => {
    clearCatalog()
    setError(t("diagnose.specialist.loadFailed"))
  })
  useEffect(() => {
    const revision = ++generation.current
    Promise.all([
      getSpecialistCatalog(spaceId, agentUuid),
      listDiagnosers(spaceId, agentUuid),
    ])
      .then(([data, list]) => {
        if (generation.current === revision) receiveCatalog(data, list.items)
      })
      .catch(() => {
        if (generation.current === revision) failCatalog()
      })
      .finally(() => {
        if (generation.current === revision) setLoading(false)
      })
    return () => {
      generation.current = revision + 1
    }
  }, [open, spaceId, agentUuid, user?.user_uuid, reload])
  const item = catalog?.items.find((value) => value.key === key)
  const definition = item?.supported_variants.find(
    (value) => value.variant === variant
  )
  const isLlm = definition?.mode === "llm"
  const canConfigure = catalog?.can_configure === true
  const canDraft = catalog?.can_trial_draft === true
  const canEditConfiguration = canConfigure || (!saved && canDraft)
  const supportedItems =
    catalog?.items.filter((entry) => entry.selectable) ?? []
  const unavailableItems =
    catalog?.items.filter(
      (entry) =>
        !entry.selectable &&
        entry.unavailable_reason === "runtime_mapping_unavailable" &&
        entry.supported_variants.length
    ) ?? []
  const openSource =
    onOpenSource &&
    ((sourceTraceId: string, spanId?: string) => {
      if (sourceTraceId === selectedTraceId) onOpenSource(sourceTraceId, spanId)
    })
  function reloadCatalog() {
    setLoading(true)
    setResult(null)
    setError("")
    setNotice("")
    setReload((value) => value + 1)
  }
  function clearResult() {
    setDirty(true)
    setResult(null)
    setNotice("")
    setError("")
  }
  function chooseKey(next: SpecialistKey) {
    setRetainedOptions({})
    setMaxTokens(4096)
    setTimeoutValue(120)
    setModel("")
    setChecks([newNumericCheck(1)])
    setFields([{ name: "", type: "string" }])
    setToolName("")
    setKey(next)
    setVariant(
      catalog?.items.find((value) => value.key === next)?.supported_variants[0]
        ?.variant ?? ""
    )
    setSaved(null)
    setPreserve(false)
    clearResult()
    setDirty(false)
  }
  function chooseSaved(value: DiagnoserDTO | null) {
    if (!value) {
      setSaved(null)
      setPreserve(false)
      clearResult()
      setDirty(false)
      return
    }
    const spec = value.spec
    setSaved(value)
    setKey(spec.builtin_key as SpecialistKey)
    setVariant(String(spec.canonical_variant))
    setModel(String(spec.model_connection_uuid ?? ""))
    setPreserve(spec.builtin_key === "protected_content_exposure")
    clearResult()
    const policy = spec.specialist_policy as Partial<SpecialistPolicy>
    if (
      policy.kind === "numeric_consistency_v1" &&
      Array.isArray(policy.checks)
    )
      setChecks(policy.checks)
    if (policy.kind === "response_language_v1") {
      setLanguage(policy.required_language ?? "zh-CN")
      setTerms(policy.allowed_terms?.join("\n") ?? "")
    }
    if (
      policy.kind === "structured_output_contract_v1" &&
      Array.isArray(policy.fields)
    )
      setFields(policy.fields)
    if (policy.kind === "required_tool_execution_v1")
      setToolName(policy.tool_name ?? "")
    const options = Object.fromEntries(
      [
        "max_tokens",
        "request_timeout_sec",
        "enable_thinking",
        "temperature",
        "reasoning_effort",
      ]
        .filter((field) => field in spec)
        .map((field) => [field, spec[field]])
    ) as NonNullable<SpecialistConfiguration["llm_options"]>
    setRetainedOptions(options)
    setMaxTokens(options?.max_tokens ?? 4096)
    setTimeoutValue(options?.request_timeout_sec ?? 120)
    setMarkers([{ id: "marker_1", value: "", visibility: "protected" }])
    setDirty(false)
  }
  function configuration(): SpecialistConfiguration {
    let policy: SpecialistConfiguration["policy"]
    if (preserve && saved) policy = { kind: "preserve_existing_policy" }
    else if (key === "answer_relevance")
      policy = {
        kind: "answer_relevance_v1",
        context_mode: variant as
          | "explicit_current_text"
          | "recorded_conversation",
      }
    else if (key === "followup_reasonableness")
      policy = {
        kind: "followup_reasonableness_v1",
        context_mode: "recorded_conversation",
      }
    else if (key === "numeric_consistency")
      policy = {
        kind: "numeric_consistency_v1",
        checks: checks.map((check) => ({
          ...check,
          unit: variant === "decimal_binary" ? null : (check.unit ?? ""),
        })),
      }
    else if (key === "response_language")
      policy = {
        kind: "response_language_v1",
        required_language: language,
        scope: "final_natural_language",
        allowed_terms: terms
          .split("\n")
          .map((term) => term.trim())
          .filter(Boolean),
      }
    else if (key === "structured_output_contract")
      policy = { kind: "structured_output_contract_v1", fields }
    else if (key === "required_tool_execution")
      policy = { kind: "required_tool_execution_v1", tool_name: toolName }
    else
      policy = {
        kind: "protected_content_exposure_v1",
        scope: "final_visible_text",
        match: "literal_exact",
        markers,
      }
    return {
      policy,
      ...(isLlm
        ? {
            model_connection_uuid: model,
            llm_options: {
              ...retainedOptions,
              max_tokens: maxTokens,
              request_timeout_sec: timeout,
            },
          }
        : {}),
    }
  }
  const formError = specialistConfigurationError(configuration())
  const draft = (): SpecialistSyncInput => ({
    key,
    variant,
    configuration: configuration(),
  })
  function updateCheck(index: number, patch: Partial<NumericCheck>) {
    setChecks(
      checks.map((check, i) => (i === index ? { ...check, ...patch } : check))
    )
    clearResult()
  }
  function updateMarker(index: number, patch: Partial<ProtectedMarker>) {
    setMarkers(
      markers.map((marker, i) =>
        i === index ? { ...marker, ...patch } : marker
      )
    )
    clearResult()
  }
  function updateField(index: number, patch: Partial<StructuredOutputField>) {
    setFields(
      fields.map((field, i) => (i === index ? { ...field, ...patch } : field))
    )
    clearResult()
  }
  function changeTrace(nextTraceId: string) {
    setTraceId(nextTraceId)
    setResult(null)
    setError("")
  }
  function clearCatalog() {
    setLoadedScope("")
    setCatalog(null)
    setSavedItems([])
    setSaved(null)
    setMarkers([{ id: "marker_1", value: "", visibility: "protected" }])
    setTraceId("")
  }
  function fail(caught?: unknown) {
    setError(t("diagnose.specialist.requestFailed"))
    setResult(null)
    if (
      caught instanceof ApiBusinessError &&
      [401, 403, 404].includes(caught.statusCode ?? 0)
    ) {
      clearCatalog()
    }
  }
  async function act(operation: "save" | "trial") {
    if (
      !open ||
      !user?.user_uuid ||
      loadedScope !== ownerScope ||
      formError ||
      pending.current === generation.current ||
      !item?.selectable ||
      !definition ||
      ((operation === "save" || !saved) &&
        isLlm &&
        !availableModels.some((value) => value.uuid === model))
    )
      return
    if (operation === "save" && !canConfigure) return
    if (
      operation === "trial" &&
      (!selectedTraceId || (!saved && !canDraft) || (saved && dirty))
    )
      return
    const revision = generation.current
    pending.current = revision
    setBusy(true)
    setResult(null)
    setError("")
    setNotice("")
    try {
      if (operation === "trial") {
        const value = await trialSpecialist(
          spaceId,
          agentUuid,
          specialistTrialInput(selectedTraceId, draft(), saved?.uuid)
        )
        if (generation.current === revision) setResult(value)
      } else {
        let value: SpecialistSaved
        if (saved)
          value = await configureSpecialist(
            spaceId,
            agentUuid,
            saved.uuid,
            String(saved.spec.configuration_digest ?? ""),
            configuration()
          )
        else value = await syncSpecialist(spaceId, agentUuid, draft())
        if (generation.current === revision) {
          setDirty(false)
          setSaved(value.diagnoser)
          setSavedItems((values) => [
            ...values.filter((entry) => entry.uuid !== value.diagnoser.uuid),
            value.diagnoser,
          ])
          setPreserve(key === "protected_content_exposure")
          setMarkers([{ id: "marker_1", value: "", visibility: "protected" }])
          setNotice(t("diagnose.specialist.saved"))
          await Promise.all([
            queryClient.invalidateQueries({ queryKey: ["diagnosers"] }),
            queryClient.invalidateQueries({
              queryKey: [
                "quick-options",
                `${user.user_uuid}:${spaceId}:${agentUuid}`,
              ],
            }),
          ])
        }
      }
    } catch (caught) {
      if (generation.current === revision) fail(caught)
    } finally {
      if (pending.current === revision) pending.current = null
      if (generation.current === revision) setBusy(false)
    }
  }
  if (loading || (catalog && loadedScope !== ownerScope))
    return (
      <p className="p-6 text-sm text-muted-foreground" role="status">
        {t("diagnose.specialist.loading")}
      </p>
    )
  return (
    <div
      className={traceIds ? "py-2" : "min-h-0 flex-1 overflow-auto px-6 py-5"}
    >
      <div className="mx-auto grid max-w-3xl gap-5">
        <div className="flex flex-wrap items-start gap-2">
          <p className="min-w-0 flex-1 basis-60 text-sm leading-6 text-muted-foreground">
            {t(
              traceIds
                ? "diagnose.specialist.traceIntro"
                : "diagnose.specialist.intro"
            )}
          </p>
          {catalog && (
            <Button
              variant="ghost"
              size="sm"
              disabled={busy}
              onClick={reloadCatalog}
            >
              {t("diagnose.specialist.reload")}
            </Button>
          )}
        </div>
        {error && (
          <p role="alert" className="text-sm text-destructive">
            {error}
          </p>
        )}
        {notice && (
          <p role="status" className="text-sm text-primary">
            {notice}
          </p>
        )}
        {!catalog ? (
          <Button onClick={reloadCatalog}>
            {t("diagnose.specialist.reload")}
          </Button>
        ) : supportedItems.length === 0 ? (
          <p role="status" className="text-sm text-muted-foreground">
            {t("diagnose.specialist.noSupportedChecks")}
          </p>
        ) : (
          <fieldset disabled={busy} className="contents">
            {traceIds && (
              <div className="grid gap-1.5">
                <label className={labelClass}>
                  {t("diagnose.specialist.traceId")}
                  {traceIds.length === 1 ? (
                    <Input
                      value={selectedTraceId}
                      readOnly
                      className="font-mono"
                    />
                  ) : (
                    <select
                      className={`${selectClass} font-mono`}
                      value={selectedTraceId}
                      onChange={(event) => changeTrace(event.target.value)}
                    >
                      <option value="">
                        {t("diagnose.specialist.chooseSelectedTrace")}
                      </option>
                      {traceIds.map((id) => (
                        <option key={id} value={id}>
                          {id}
                        </option>
                      ))}
                    </select>
                  )}
                </label>
                {traceIds.length > 1 && (
                  <p className="text-xs text-muted-foreground">
                    {t("diagnose.specialist.oneTraceAtATime")}
                  </p>
                )}
              </div>
            )}
            <label className={labelClass}>
              {t("diagnose.specialist.savedSelection")}
              <select
                className={selectClass}
                value={saved?.uuid ?? ""}
                onChange={(event) =>
                  chooseSaved(
                    savedItems.find(
                      (item) => item.uuid === event.target.value
                    ) ?? null
                  )
                }
                disabled={busy}
              >
                <option value="">
                  {t("diagnose.specialist.newConfiguration")}
                </option>
                {savedItems.map((value) => (
                  <option key={value.uuid} value={value.uuid}>
                    {value.name}
                  </option>
                ))}
              </select>
            </label>
            <div className="grid gap-4 sm:grid-cols-2">
              <label className={labelClass}>
                {t("diagnose.specialist.check")}
                <select
                  className={selectClass}
                  value={key}
                  disabled={busy || !!saved || !canDraft}
                  onChange={(event) =>
                    chooseKey(event.target.value as SpecialistKey)
                  }
                >
                  {supportedItems.map((value) => (
                    <option key={value.key} value={value.key}>
                      {t(`diagnose.specialist.names.${value.key}`, {
                        defaultValue: value.name,
                      })}
                    </option>
                  ))}
                </select>
              </label>
              <label className={labelClass}>
                {t("diagnose.specialist.scope")}
                <select
                  className={selectClass}
                  value={variant}
                  disabled={busy || !!saved || !canDraft}
                  onChange={(event) => {
                    setVariant(event.target.value)
                    clearResult()
                  }}
                >
                  {item?.supported_variants.map((value) => (
                    <option key={value.variant} value={value.variant}>
                      {t(`diagnose.specialist.variants.${value.variant}`, {
                        defaultValue: value.variant,
                      })}
                    </option>
                  ))}
                </select>
              </label>
            </div>
            {definition && (
              <section className="grid gap-2 border-l-2 border-primary/50 pl-4">
                <h3 className="text-sm font-medium">
                  {t("diagnose.specialist.materials")}
                </h3>
                <p className="text-sm leading-6 text-muted-foreground">
                  {definition.required_materials
                    .map((value) =>
                      t(`diagnose.specialist.materialLabels.${value}`, {
                        nsSeparator: false,
                        defaultValue: value,
                      })
                    )
                    .join(t("diagnose.specialist.materialSeparator"))}
                </p>
                <p className="text-sm leading-6">
                  {t(`diagnose.specialist.guidance.${key}.${variant}`, {
                    defaultValue: definition.result_boundary,
                  })}
                </p>
                {!isLlm && (
                  <p className="text-xs text-muted-foreground">
                    {t("diagnose.specialist.ruleNoModel")}
                  </p>
                )}
              </section>
            )}
            {key === "numeric_consistency" && (
              <section className="grid gap-4">
                <p className="text-xs leading-5 text-muted-foreground">
                  {t("diagnose.specialist.numericHelp")}
                </p>
                {checks.map((check, index) => (
                  <fieldset
                    key={index}
                    className="grid gap-3 rounded-md border p-4"
                  >
                    <legend className="px-1 text-sm">
                      {t("diagnose.specialist.numericCheck", {
                        count: index + 1,
                      })}
                    </legend>
                    <label className={labelClass}>
                      {t("diagnose.specialist.identifier")}
                      <Input
                        disabled={!canEditConfiguration}
                        value={check.id}
                        maxLength={32}
                        onChange={(event) =>
                          updateCheck(index, { id: event.target.value })
                        }
                      />
                    </label>
                    <div className="grid gap-3 sm:grid-cols-2">
                      {(["expression", "result"] as const).map((field) => (
                        <div key={field} className="grid gap-2">
                          <p className="text-xs font-medium">
                            {t(`diagnose.specialist.${field}`)}
                          </p>
                          {(["prefix", "suffix"] as const).map((anchor) => (
                            <label key={anchor} className={labelClass}>
                              {t(`diagnose.specialist.${anchor}`)}
                              <Input
                                disabled={!canEditConfiguration}
                                value={check[field][anchor]}
                                maxLength={128}
                                onChange={(event) =>
                                  updateCheck(index, {
                                    [field]: {
                                      ...check[field],
                                      [anchor]: event.target.value,
                                    },
                                  })
                                }
                              />
                            </label>
                          ))}
                        </div>
                      ))}
                    </div>
                    <div className="grid gap-3 sm:grid-cols-3">
                      <label className={labelClass}>
                        {t("diagnose.specialist.precision")}
                        <select
                          disabled={!canEditConfiguration}
                          className={selectClass}
                          value={check.precision.mode}
                          onChange={(event) =>
                            updateCheck(index, {
                              precision:
                                event.target.value === "exact"
                                  ? { mode: "exact" }
                                  : {
                                      mode: "quantized",
                                      scale: 2,
                                      rounding: "ROUND_HALF_EVEN",
                                    },
                            })
                          }
                        >
                          <option value="exact">
                            {t("diagnose.specialist.exact")}
                          </option>
                          <option value="quantized">
                            {t("diagnose.specialist.quantized")}
                          </option>
                        </select>
                      </label>
                      {check.precision.mode === "quantized" && (
                        <>
                          <label className={labelClass}>
                            {t("diagnose.specialist.scale")}
                            <Input
                              disabled={!canEditConfiguration}
                              type="number"
                              min={0}
                              max={12}
                              value={check.precision.scale}
                              onChange={(event) =>
                                updateCheck(index, {
                                  precision: {
                                    ...(check.precision as Extract<
                                      NumericCheck["precision"],
                                      { mode: "quantized" }
                                    >),
                                    scale: Number(event.target.value),
                                  },
                                })
                              }
                            />
                          </label>
                          <label className={labelClass}>
                            {t("diagnose.specialist.rounding")}
                            <select
                              disabled={!canEditConfiguration}
                              className={selectClass}
                              value={check.precision.rounding}
                              onChange={(event) =>
                                updateCheck(index, {
                                  precision: {
                                    ...(check.precision as Extract<
                                      NumericCheck["precision"],
                                      { mode: "quantized" }
                                    >),
                                    rounding: event.target.value as
                                      | "ROUND_HALF_EVEN"
                                      | "ROUND_HALF_UP"
                                      | "ROUND_DOWN",
                                  },
                                })
                              }
                            >
                              {[
                                "ROUND_HALF_EVEN",
                                "ROUND_HALF_UP",
                                "ROUND_DOWN",
                              ].map((value) => (
                                <option key={value} value={value}>
                                  {t(`diagnose.specialist.${value}`)}
                                </option>
                              ))}
                            </select>
                          </label>
                        </>
                      )}
                    </div>
                    {variant === "decimal_binary_same_unit" && (
                      <label className={labelClass}>
                        {t("diagnose.specialist.unit")}
                        <Input
                          disabled={!canEditConfiguration}
                          value={check.unit ?? ""}
                          maxLength={16}
                          onChange={(event) =>
                            updateCheck(index, { unit: event.target.value })
                          }
                        />
                      </label>
                    )}
                    {checks.length > 1 && (
                      <Button
                        variant="ghost"
                        disabled={!canEditConfiguration}
                        onClick={() => {
                          setChecks(checks.filter((_, i) => i !== index))
                          clearResult()
                        }}
                      >
                        {t("diagnose.specialist.remove")}
                      </Button>
                    )}
                  </fieldset>
                ))}
                <Button
                  variant="outline"
                  disabled={checks.length >= 8 || busy || !canEditConfiguration}
                  onClick={() => {
                    setChecks([
                      ...checks,
                      {
                        ...newNumericCheck(1),
                        id: nextSpecialistPolicyId("check", checks),
                      },
                    ])
                    clearResult()
                  }}
                >
                  {t("diagnose.specialist.addCheck")}
                </Button>
              </section>
            )}
            {key === "response_language" && (
              <div className="grid gap-3">
                <label className={labelClass}>
                  {t("diagnose.specialist.language")}
                  <select
                    disabled={!canEditConfiguration}
                    className={selectClass}
                    value={language}
                    onChange={(event) => {
                      setLanguage(event.target.value)
                      clearResult()
                    }}
                  >
                    {["zh-CN", "zh-TW", "en", "ja", "ko", "fr", "de", "es"].map(
                      (value) => (
                        <option key={value} value={value}>
                          {t(`diagnose.specialist.languages.${value}`)}
                        </option>
                      )
                    )}
                  </select>
                </label>
                <label className={labelClass}>
                  {t("diagnose.specialist.allowedTerms")}
                  <textarea
                    disabled={!canEditConfiguration}
                    className={`${selectClass} h-24 py-2`}
                    value={terms}
                    onChange={(event) => {
                      setTerms(event.target.value)
                      clearResult()
                    }}
                  />
                </label>
              </div>
            )}
            {key === "protected_content_exposure" && (
              <section className="grid gap-3">
                <p className="text-xs leading-5 text-muted-foreground">
                  {t("diagnose.specialist.protectedHelp")}
                </p>
                {saved && (
                  <label className="flex items-center gap-2 text-sm">
                    <input
                      type="checkbox"
                      disabled={!canEditConfiguration}
                      checked={preserve}
                      onChange={(event) => {
                        setPreserve(event.target.checked)
                        clearResult()
                      }}
                    />
                    {t("diagnose.specialist.preserve")}
                  </label>
                )}
                {preserve ? (
                  <p className="text-sm text-muted-foreground">
                    {t("diagnose.specialist.configuredMarkers", {
                      count: Array.isArray(
                        (
                          saved?.spec.specialist_policy as {
                            markers?: unknown[]
                          }
                        )?.markers
                      )
                        ? (
                            saved?.spec.specialist_policy as {
                              markers: unknown[]
                            }
                          ).markers.length
                        : 0,
                    })}
                  </p>
                ) : (
                  <>
                    {markers.map((marker, index) => (
                      <fieldset
                        key={index}
                        className="grid gap-3 rounded-md border p-4 sm:grid-cols-3"
                      >
                        <label className={labelClass}>
                          {t("diagnose.specialist.identifier")}
                          <Input
                            disabled={!canEditConfiguration}
                            value={marker.id}
                            maxLength={32}
                            onChange={(event) =>
                              updateMarker(index, { id: event.target.value })
                            }
                          />
                        </label>
                        <label className={labelClass}>
                          {t("diagnose.specialist.markerValue")}
                          <Input
                            disabled={!canEditConfiguration}
                            type="password"
                            autoComplete="new-password"
                            value={marker.value}
                            maxLength={256}
                            onChange={(event) =>
                              updateMarker(index, { value: event.target.value })
                            }
                          />
                        </label>
                        <label className={labelClass}>
                          {t("diagnose.specialist.visibility")}
                          <select
                            disabled={!canEditConfiguration}
                            className={selectClass}
                            value={marker.visibility}
                            onChange={(event) =>
                              updateMarker(index, {
                                visibility: event.target.value as
                                  | "protected"
                                  | "public",
                              })
                            }
                          >
                            <option value="protected">
                              {t("diagnose.specialist.protected")}
                            </option>
                            <option value="public">
                              {t("diagnose.specialist.public")}
                            </option>
                          </select>
                        </label>
                        {markers.length > 1 && (
                          <Button
                            variant="ghost"
                            disabled={!canEditConfiguration}
                            onClick={() => {
                              setMarkers(markers.filter((_, i) => i !== index))
                              clearResult()
                            }}
                          >
                            {t("diagnose.specialist.remove")}
                          </Button>
                        )}
                      </fieldset>
                    ))}
                    <Button
                      variant="outline"
                      disabled={
                        markers.length >= 64 || busy || !canEditConfiguration
                      }
                      onClick={() => {
                        setMarkers([
                          ...markers,
                          {
                            id: nextSpecialistPolicyId("marker", markers),
                            value: "",
                            visibility: "protected",
                          },
                        ])
                        clearResult()
                      }}
                    >
                      {t("diagnose.specialist.addMarker")}
                    </Button>
                  </>
                )}
              </section>
            )}
            {key === "structured_output_contract" && (
              <fieldset className="grid gap-3 rounded-md border p-4">
                <legend className="px-1 text-sm font-medium">
                  {t("diagnose.specialist.requiredFields")}
                </legend>
                {fields.map((field, index) => (
                  <div
                    key={index}
                    className="grid items-end gap-2 sm:grid-cols-[minmax(0,2fr)_minmax(0,1fr)_auto]"
                  >
                    <label className={labelClass}>
                      {t("diagnose.specialist.fieldName")}
                      <Input
                        disabled={!canEditConfiguration}
                        value={field.name}
                        maxLength={64}
                        placeholder={index === 0 ? "approved" : undefined}
                        onChange={(event) =>
                          updateField(index, { name: event.target.value })
                        }
                      />
                    </label>
                    <label className={labelClass}>
                      {t("diagnose.specialist.fieldType")}
                      <select
                        className={selectClass}
                        disabled={!canEditConfiguration}
                        value={field.type}
                        onChange={(event) =>
                          updateField(index, {
                            type: event.target
                              .value as StructuredOutputField["type"],
                          })
                        }
                      >
                        {structuredOutputFieldTypes.map((type) => (
                          <option key={type} value={type}>
                            {t(`diagnose.specialist.fieldTypes.${type}`)}
                          </option>
                        ))}
                      </select>
                    </label>
                    {fields.length > 1 && (
                      <Button
                        variant="ghost"
                        disabled={!canEditConfiguration}
                        onClick={() => {
                          setFields(fields.filter((_, i) => i !== index))
                          clearResult()
                        }}
                      >
                        {t("diagnose.specialist.remove")}
                      </Button>
                    )}
                  </div>
                ))}
                <Button
                  variant="outline"
                  className="justify-self-start"
                  disabled={!canEditConfiguration || fields.length >= 16}
                  onClick={() => {
                    setFields([...fields, { name: "", type: "string" }])
                    clearResult()
                  }}
                >
                  {t("diagnose.specialist.addField")}
                </Button>
              </fieldset>
            )}
            {key === "required_tool_execution" && (
              <label className={labelClass}>
                {t("diagnose.specialist.toolName")}
                <Input
                  disabled={!canEditConfiguration}
                  value={toolName}
                  maxLength={128}
                  placeholder="lookup_order"
                  onChange={(event) => {
                    setToolName(event.target.value)
                    clearResult()
                  }}
                />
              </label>
            )}
            {isLlm && (
              <section className="grid gap-3 border-t pt-4">
                <label className={labelClass}>
                  {t("diagnose.specialist.model")}
                  <select
                    disabled={!canEditConfiguration}
                    className={selectClass}
                    value={model}
                    onChange={(event) => {
                      setModel(event.target.value)
                      clearResult()
                    }}
                  >
                    <option value="">
                      {t("diagnose.specialist.selectModel")}
                    </option>
                    {saved &&
                      !canEditConfiguration &&
                      model &&
                      !availableModels.some(
                        (value) => value.uuid === model
                      ) && (
                        <option value={model}>
                          {t("diagnose.specialist.savedModel")}
                        </option>
                      )}
                    {availableModels.map((value) => (
                      <option key={value.uuid} value={value.uuid}>
                        {value.name} ({value.modelName})
                      </option>
                    ))}
                  </select>
                </label>
                {!availableModels.length &&
                  (!saved || (canConfigure && dirty)) && (
                    <p className="text-xs text-muted-foreground">
                      {t("diagnose.specialist.modelHelp")}
                    </p>
                  )}
                <details className="text-xs text-muted-foreground">
                  <summary className="w-fit cursor-pointer rounded-sm py-1 focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none">
                    {t("diagnose.specialist.generationOptions")}
                  </summary>
                  <div className="mt-2 grid gap-3 sm:grid-cols-2">
                    <label className={labelClass}>
                      {t("diagnose.specialist.maxTokens")}
                      <Input
                        disabled={!canEditConfiguration}
                        type="number"
                        min={1}
                        max={8192}
                        value={maxTokens}
                        onChange={(event) => {
                          setMaxTokens(Number(event.target.value))
                          clearResult()
                        }}
                      />
                    </label>
                    <label className={labelClass}>
                      {t("diagnose.specialist.timeout")}
                      <Input
                        disabled={!canEditConfiguration}
                        type="number"
                        min={1}
                        max={300}
                        value={timeout}
                        onChange={(event) => {
                          setTimeoutValue(Number(event.target.value))
                          clearResult()
                        }}
                      />
                    </label>
                  </div>
                </details>
                <p className="text-xs text-muted-foreground">
                  {t("diagnose.specialist.budgetBoundary")}
                </p>
              </section>
            )}
            {formError && dirty && (
              <p role="alert" className="text-xs text-destructive">
                {t(`diagnose.specialist.formErrors.${formError}`)}
              </p>
            )}
            <section className="grid gap-3 border-t pt-4">
              {!traceIds && (
                <label className={labelClass}>
                  {t("diagnose.specialist.traceId")}
                  <Input
                    value={traceId}
                    maxLength={128}
                    onChange={(event) => changeTrace(event.target.value)}
                    placeholder={t("diagnose.specialist.tracePlaceholder")}
                  />
                </label>
              )}
              <p className="text-xs leading-5 text-muted-foreground">
                {t("diagnose.specialist.trialHelp")}
              </p>
              <div className="flex flex-wrap gap-3">
                <Button
                  onClick={() => void act("trial")}
                  disabled={
                    !!formError ||
                    busy ||
                    (!!saved && dirty) ||
                    !selectedTraceId ||
                    (!saved && !canDraft) ||
                    !item?.selectable ||
                    (!saved &&
                      isLlm &&
                      !availableModels.some((value) => value.uuid === model))
                  }
                >
                  {busy
                    ? t("diagnose.specialist.running")
                    : t("diagnose.specialist.trial")}
                </Button>
                <Button
                  variant="outline"
                  disabled={
                    !!formError ||
                    busy ||
                    !canConfigure ||
                    !item?.selectable ||
                    (isLlm &&
                      !availableModels.some((value) => value.uuid === model))
                  }
                  onClick={() => void act("save")}
                >
                  {saved
                    ? t("diagnose.specialist.saveChanges")
                    : t("diagnose.specialist.save")}
                </Button>
              </div>
              {saved && dirty && (
                <p className="text-xs text-muted-foreground">
                  {t("diagnose.specialist.saveFirst")}
                </p>
              )}
              {!canConfigure && (
                <p className="text-xs text-muted-foreground">
                  {t("diagnose.specialist.teamRequired")}
                </p>
              )}
              {result && (
                <div aria-live="polite">
                  <TrialResultView
                    result={result}
                    showEvidence
                    onOpenSource={openSource}
                  />
                  {result.request_attempts.max > 0 && (
                    <p className="mt-2 text-xs text-muted-foreground">
                      {t("diagnose.specialist.attempts", {
                        count: result.request_attempts.reserved,
                        max: result.request_attempts.max,
                      })}
                    </p>
                  )}
                </div>
              )}
            </section>
            {unavailableItems.length > 0 && (
              <details className="border-t pt-4 text-xs text-muted-foreground">
                <summary className="cursor-pointer py-1">
                  {t("diagnose.specialist.notAvailable")}
                </summary>
                <ul className="mt-2 grid gap-2">
                  {unavailableItems.map((value) => (
                    <li key={value.key}>
                      {t(`diagnose.specialist.names.${value.key}`, {
                        defaultValue: value.name,
                      })}
                      {" · "}
                      {t(
                        `diagnose.specialist.unavailableReasons.${value.key === "required_tool_execution" ? "unsupported_tool_runtime" : value.unavailable_reason}`,
                        { defaultValue: t("diagnose.specialist.unavailable") }
                      )}
                    </li>
                  ))}
                </ul>
              </details>
            )}
          </fieldset>
        )}
      </div>
    </div>
  )
}
