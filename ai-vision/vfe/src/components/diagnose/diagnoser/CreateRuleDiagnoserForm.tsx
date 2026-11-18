/**
 * 新建 / 编辑自定义 Rule 诊断器表单。
 *
 * 用户编写 evaluate(trace) 函数并配置单指标，
 * 可试运行查看结果后再提交。
 * - 创建: createDiagnoser(type=custom, mode=rule, spec)
 * - 编辑(传 editing): updateDiagnoser,回传完整 spec
 */

import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react"
import { useTranslation } from "react-i18next"
import { useMutation, useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"
import { CircleHelp, Play, Loader2 } from "lucide-react"
import { useAgent } from "@/contexts/AgentContext"
import { useSpace } from "@/contexts/SpaceContext"
import { useUser } from "@/contexts/UserContext"

import { DialogFooter } from "@/components/ui/dialog"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Textarea } from "@/components/ui/textarea"
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import {
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"

import { createDiagnoser, updateDiagnoser } from "../diagnoseApi"
import { SCENARIO_LABEL_KEYS } from "../types"
import { LazyCodeMirrorEditor } from "./LazyCodeMirrorEditor"
import { DiagnoserFormSection } from "./DiagnoserFormSection"
import { TrialResultView } from "./TrialResultView"
import { isValidatedTrial } from "./trialResult"
import { MetricDefinitionEditor } from "./MetricDefinitionEditor"
import {
  areMetricDefinitionsValid,
  createDefaultMetricDefinitionDraft,
  metricDefinitionsFromSpec,
  type MetricDefinitionDraft,
} from "./metricDefinitionModel"
import { runRuleTrial, type RuleTrialResponse } from "./ruleApi"
import { useDiagnoserReferenceTraceSample } from "./useDiagnoserReferenceTraces"
import { RawTraceVariablePicker, TraceIdInput } from "./RawTraceVariablePicker"
import { RuleSandboxSelector } from "@/features/sandbox-connections/RuleSandboxSelector"
import {
  buildSandboxRuleSpec,
  sandboxSelectionFromSpec,
  type SandboxSelection,
} from "@/features/sandbox-connections/types"

import { EnhancedRuleBudgetAction } from "./EnhancedRuleBudgetAction"
import { EnhancedRuleEnvironment } from "./EnhancedRuleEnvironment"
import { EnhancedRuleMaterials } from "./EnhancedRuleMaterials"
import { useRuleRuntime } from "./useRuleRuntime"
import { useRuleInputBudget } from "./useRuleInputBudget"
import { RuleInputBudget } from "./RuleInputBudget"
import {
  buildEnhancedRuleSpec,
  buildRuleMaterials,
  expectedTextFromMaterials,
  validateMaterials,
  type RuleMaterials,
  type RuleRuntimeManifest,
} from "./enhancedRuleModel"

/* ── Constants ── */

const NEW_SCENARIO = "__new__"
const DEFAULT_CODE = `def evaluate(trace):
    # 诊断示例：诊断 Agent 是否生成了输出。
    # 未生成输出时返回 bad，已生成输出时返回 good。
    #
    # 实现方式：
    # 1. 点击右上角“查看 Trace 结构”，选择一条真实 Trace。
    # 2. 在 spans 中找到 Agent 输出所在的 Span，并选中对应输出字段。
    # 3. 复制右侧生成的 Python 表达式，粘贴到下方 output = 的右侧。
    # 4. 表达式会从原始 trace.spans 取值，再根据内容是否为空进行判定。
    # 请将空字符串替换为从“查看 Trace 结构”复制的 Python 表达式。
    output = ""
    output = str(output or "").strip()

    # 返回结构约定：
    # 1. 必须返回 {"metrics": [...]}，每个已声明指标都要且只能返回一次。
    # 2. name 必须与指标声明完全一致，本示例的指标名是 quality。
    # 3. verdict 只能是 good 或 bad；failed 由平台在执行失败时生成。
    # 4. bad 必须填写非空 reason；evidence 可省略，填写时必须是 JSON 对象。
    #
    # 整体诊断结果由平台根据各指标结果自动计算，不需要在代码中返回：
    # - 单指标：整体结果与该指标结果一致。
    # - 多指标：任一指标为 bad，则整体为 bad；没有 bad 但存在 failed，
    #   则整体为 failed；所有指标均为 good，整体才为 good。
    if not output:
        return {
            "metrics": [{
                "name": "quality",
                "verdict": "bad",
                "reason": "Agent 未生成输出",
                "evidence": {},
            }]
        }

    return {
        "metrics": [{
            "name": "quality",
            "verdict": "good",
            "reason": "Agent 已生成输出",
            "evidence": {"output_length": len(output)},
        }]
    }
`

function defaultTestInput(runtimeType: string) {
  return JSON.stringify({ runtime_type: runtimeType, spans: [] }, null, 2)
}

type Category = "outcome" | "process" | "safety" | "system"

function defaultScenarioSel(existing: string[]): string {
  if (existing.includes("common")) return "common"
  return existing[0] ?? NEW_SCENARIO
}

/* ── Props ── */

interface CreateRuleDiagnoserFormProps {
  open: boolean
  spaceId: string
  agentUuid: string
  editing?: {
    uuid: string
    name: string
    description?: string
    spec: Record<string, unknown>
    writePython?: boolean
  } | null
  existingScenarios: string[]
  onClose: () => void
}

/* ── Component ── */

export function CreateRuleDiagnoserForm({
  open,
  spaceId,
  agentUuid,
  editing,
  existingScenarios,
  onClose,
}: CreateRuleDiagnoserFormProps) {
  const { t } = useTranslation()
  const { currentAgent } = useAgent()
  const { currentSpace } = useSpace()
  const { user, loading: userLoading } = useUser()
  const queryClient = useQueryClient()
  const isEdit = !!editing
  const runtimeType = currentAgent?.runtime_type || "other"
  const scenarioOptionsRef = useRef(existingScenarios)
  useEffect(() => {
    scenarioOptionsRef.current = existingScenarios
  }, [existingScenarios])

  /* ── Form state ── */
  const [name, setName] = useState("")
  const [description, setDescription] = useState("")
  const [category, setCategory] = useState<Category>("outcome")
  const [scenarioSel, setScenarioSel] = useState<string>(NEW_SCENARIO)
  const [customScenario, setCustomScenario] = useState("")
  const [code, setCode] = useState(DEFAULT_CODE)
  const [metrics, setMetrics] = useState<MetricDefinitionDraft[]>(() => [
    createDefaultMetricDefinitionDraft(),
  ])
  const [testInput, setTestInput] = useState(() =>
    defaultTestInput(runtimeType)
  )
  const [activeReferenceTraceId, setActiveReferenceTraceId] = useState("")
  const [requestedTraceId, setRequestedTraceId] = useState("")
  const [tracePickerOpen, setTracePickerOpen] = useState(false)
  const [trialOutcome, setTrialOutcome] = useState<{
    fingerprint: string
    result: RuleTrialResponse
  } | null>(null)
  const [pendingTrial, setPendingTrial] = useState<string | null>(null)
  const activeTrial = useRef<{ fingerprint: string } | null>(null)
  const [sandbox, setSandbox] = useState<SandboxSelection>({ mode: "inherit" })
  const [sandboxChanged, setSandboxChanged] = useState(false)
  const [ruleRuntime, setRuleRuntime] = useState<RuleRuntimeManifest>()
  const [materials, setMaterials] = useState<RuleMaterials>({ files: [] })
  const [expectedText, setExpectedText] = useState("")
  const [syntheticInput, setSyntheticInput] = useState(false)
  const [budgetUpdating, setBudgetUpdating] = useState(false)
  const runtime = useRuleRuntime(
    spaceId,
    user?.user_uuid,
    open && !userLoading && currentSpace?.id === spaceId,
    sandbox
  )
  let expectedError = false
  let effectiveMaterials: RuleMaterials = { files: materials.files }
  try {
    effectiveMaterials = buildRuleMaterials(materials.files, expectedText)
  } catch {
    expectedError = true
  }
  const materialError = validateMaterials(effectiveMaterials)
  const runtimeBlocked =
    (!isEdit && runtime.loading) ||
    (!!ruleRuntime &&
      (runtime.loading ||
        !runtime.manifest ||
        runtime.manifest.manifest_sha256 !== ruleRuntime.manifest_sha256 ||
        runtime.manifest.id !== ruleRuntime.id))

  const firstReferenceTraceQuery = useDiagnoserReferenceTraceSample(
    spaceId,
    agentUuid,
    open && !!spaceId && !!agentUuid,
    undefined,
    3
  )
  const requestedTraceQuery = useDiagnoserReferenceTraceSample(
    spaceId,
    agentUuid,
    open && !!requestedTraceId,
    requestedTraceId
  )
  const referenceSamples = useMemo(() => {
    const byTraceId = new Map<
      string,
      { trace_id: string; test_input: Record<string, unknown> }
    >()
    for (const sample of requestedTraceQuery.data?.samples ?? []) {
      byTraceId.set(sample.trace_id, sample)
    }
    for (const sample of firstReferenceTraceQuery.data?.samples ?? []) {
      if (!byTraceId.has(sample.trace_id)) {
        byTraceId.set(sample.trace_id, sample)
      }
    }
    return [...byTraceId.values()]
  }, [
    firstReferenceTraceQuery.data?.samples,
    requestedTraceQuery.data?.samples,
  ])
  const activeReferenceSample =
    referenceSamples.find(
      (sample) => sample.trace_id === activeReferenceTraceId
    ) ?? referenceSamples[0]

  /* ── Pre-fill on open / edit ── */
  useEffect(() => {
    if (!open) return
    const scenarios = scenarioOptionsRef.current
    let active = true

    queueMicrotask(() => {
      if (!active) return
      setActiveReferenceTraceId("")
      setRequestedTraceId("")
      setTracePickerOpen(false)
      setSandbox(sandboxSelectionFromSpec(editing?.spec ?? {}))
      setSandboxChanged(false)
      setSyntheticInput(false)
      setRuleRuntime(
        typeof editing?.spec.rule_runtime === "string"
          ? {
              id: editing.spec.rule_runtime,
              manifest_sha256: String(editing.spec.rule_runtime_manifest ?? ""),
            }
          : undefined
      )
      const savedMaterials = editing?.spec.materials as
        | RuleMaterials
        | undefined
      setMaterials(
        savedMaterials && Array.isArray(savedMaterials.files)
          ? savedMaterials
          : { files: [] }
      )
      setExpectedText(expectedTextFromMaterials(savedMaterials))
      if (editing) {
        const s = editing.spec ?? {}
        setName(editing.name ?? "")
        setDescription(editing.description ?? "")
        setCode(
          editing.writePython
            ? ""
            : typeof s.code === "string"
              ? s.code
              : DEFAULT_CODE
        )
        setMetrics(metricDefinitionsFromSpec(s.metrics))
        setCategory(
          (["outcome", "process", "safety", "system"] as const).includes(
            s.category as Category
          )
            ? (s.category as Category)
            : "outcome"
        )
        const sc = typeof s.scenario === "string" ? s.scenario.trim() : ""
        if (sc && scenarios.includes(sc)) {
          setScenarioSel(sc)
          setCustomScenario("")
        } else if (sc) {
          setScenarioSel(NEW_SCENARIO)
          setCustomScenario(sc)
        } else {
          setScenarioSel(defaultScenarioSel(scenarios))
          setCustomScenario("")
        }

        return
      }

      setName("")
      setDescription("")
      setCategory("outcome")
      setScenarioSel(defaultScenarioSel(scenarios))
      setCustomScenario("")
      setCode(DEFAULT_CODE)
      setMetrics([createDefaultMetricDefinitionDraft()])
      setTestInput(defaultTestInput(runtimeType))
      setTrialOutcome(null)
    })

    return () => {
      active = false
    }
  }, [editing, open, runtimeType])

  useEffect(() => {
    if (!open || isEdit || ruleRuntime || !runtime.manifest) return
    const manifest = runtime.manifest
    let active = true
    queueMicrotask(() => {
      if (active) setRuleRuntime(manifest)
    })
    return () => {
      active = false
    }
  }, [open, isEdit, ruleRuntime, runtime.manifest])

  /* eslint-disable react-hooks/set-state-in-effect -- load state mirrors the asynchronous reference sample query. */
  useEffect(() => {
    if (!open || syntheticInput) return
    const hasRequestedTrace = Boolean(requestedTraceId)
    if (firstReferenceTraceQuery.isLoading && !hasRequestedTrace) {
      setActiveReferenceTraceId("")
      return
    }
    if (firstReferenceTraceQuery.isError && !hasRequestedTrace) {
      setActiveReferenceTraceId("")
      setTestInput(defaultTestInput(runtimeType))
      return
    }
    const response = requestedTraceQuery.data ?? firstReferenceTraceQuery.data
    if (!response) return
    const activeSample =
      referenceSamples.find(
        (sample) => sample.trace_id === activeReferenceTraceId
      ) ?? referenceSamples[0]
    if (activeSample) {
      setActiveReferenceTraceId(activeSample.trace_id)
      setTestInput(JSON.stringify(activeSample.test_input, null, 2))
      return
    }
    setTestInput(defaultTestInput(runtimeType))
  }, [
    activeReferenceTraceId,
    syntheticInput,
    referenceSamples,
    firstReferenceTraceQuery.data,
    firstReferenceTraceQuery.isError,
    firstReferenceTraceQuery.isLoading,
    requestedTraceId,
    requestedTraceQuery.data,
    open,
    runtimeType,
  ])
  /* eslint-enable react-hooks/set-state-in-effect */

  /* ── Spec builder ── */
  const buildSpec = (): Record<string, unknown> => {
    return buildEnhancedRuleSpec(
      buildSandboxRuleSpec(
        editing?.spec,
        {
          category,
          scenario:
            scenarioSel === NEW_SCENARIO ? customScenario.trim() : scenarioSel,
          weight: editing?.spec.weight ?? 1,
          code,
          metrics: metrics.map((metric) => ({
            name: metric.name.trim(),
            desc: metric.desc.trim(),
          })),
        },
        sandbox,
        sandboxChanged
      ),
      ruleRuntime,
      effectiveMaterials
    )
  }
  const canRunTrial =
    open &&
    !budgetUpdating &&
    !userLoading &&
    !!user?.user_uuid &&
    currentSpace?.id === spaceId &&
    currentSpace.permissions.includes("diagnosis.execute")
  const trialFingerprint = JSON.stringify([
    open,
    spaceId,
    agentUuid,
    runtimeType,
    editing?.uuid,
    user?.user_uuid,
    canRunTrial,
    buildSpec(),
    runtime.identity,
    expectedText,
    testInput,
  ])
  const parsedTrialInput = useMemo(() => {
    try {
      const parsed: unknown = JSON.parse(testInput)
      if (!parsed || typeof parsed !== "object") return null
      return Array.isArray(parsed)
        ? { runtime_type: runtimeType, trace_id: "test-trace-1", spans: parsed }
        : { ...(parsed as Record<string, unknown>), runtime_type: runtimeType }
    } catch {
      return null
    }
  }, [testInput, runtimeType])
  const inputPreviewRequired = sandbox.mode !== "local" && !runtime.local
  const inputPreview = useRuleInputBudget(
    spaceId,
    trialFingerprint,
    parsedTrialInput
      ? JSON.stringify({ spec: buildSpec(), test_input: parsedTrialInput })
      : null,
    canRunTrial &&
      inputPreviewRequired &&
      !!runtime.connection &&
      !runtime.loading &&
      !runtimeBlocked &&
      !materialError &&
      !expectedError &&
      !!code.trim() &&
      areMetricDefinitionsValid(metrics) &&
      !!parsedTrialInput
  )
  const inputPreviewBlocked = inputPreviewRequired && !inputPreview.data?.valid
  // Draft/context changes hide old results in the same render. Invalidate the
  // request on commit too, so its catch/finally cannot affect the current form.
  const trialResult =
    trialOutcome?.fingerprint === trialFingerprint ? trialOutcome.result : null
  const trialLoading = pendingTrial === trialFingerprint
  useLayoutEffect(() => {
    // Persist invalidation even if the user later restores the same draft or
    // principal. Synchronize only when this external request context changes.
    // eslint-disable-next-line react-hooks/set-state-in-effect -- Trial ownership ends with the draft/context lifecycle.
    setTrialOutcome(null)
    setPendingTrial(null)
    return () => {
      activeTrial.current = null
    }
  }, [trialFingerprint])

  /* ── Trial run ── */
  const handleTrial = async () => {
    if (
      !canRunTrial ||
      inputPreviewBlocked ||
      runtimeBlocked ||
      materialError ||
      expectedError ||
      activeTrial.current?.fingerprint === trialFingerprint
    )
      return
    if (!parsedTrialInput) {
      toast.error(t("diagnose.diagnoser.rule.invalidJson"))
      return
    }
    const trialInput = parsedTrialInput
    const request = { fingerprint: trialFingerprint }
    activeTrial.current = request
    setPendingTrial(trialFingerprint)
    setTrialOutcome(null)
    try {
      const result = await runRuleTrial(
        spaceId,
        buildSpec(),
        trialInput as Record<string, unknown>
      )
      if (activeTrial.current === request)
        setTrialOutcome({ fingerprint: request.fingerprint, result })
    } catch (e) {
      if (activeTrial.current === request)
        toast.error(e instanceof Error ? e.message : String(e))
    } finally {
      if (activeTrial.current === request) {
        activeTrial.current = null
        setPendingTrial(null)
      }
    }
  }

  /* ── Create / Update ── */
  const mutation = useMutation({
    mutationFn: () => {
      if (editing) {
        return updateDiagnoser(
          spaceId,
          editing.uuid,
          {
            description: description.trim(),
            spec: buildSpec(),
          },
          agentUuid
        )
      }
      return createDiagnoser(spaceId, agentUuid, {
        name: name.trim(),
        description: description.trim(),
        type: "custom",
        mode: "rule",
        spec: buildSpec(),
      })
    },
    onSuccess: () => {
      toast.success(
        isEdit
          ? t("diagnose.diagnoser.rule.updateSuccess", {
              defaultValue: "规则诊断器已更新",
            })
          : t("diagnose.diagnoser.rule.createSuccess", {
              defaultValue: "规则诊断器已创建",
            })
      )
      queryClient.invalidateQueries({ queryKey: ["diagnosers"] })
      onClose()
    },
    onError: (e: Error) => toast.error(e.message),
  })

  const canSubmit =
    !!name.trim() &&
    !!code.trim() &&
    areMetricDefinitionsValid(metrics) &&
    !mutation.isPending &&
    !budgetUpdating &&
    !runtimeBlocked &&
    !materialError &&
    !expectedError &&
    (scenarioSel !== NEW_SCENARIO || !!customScenario.trim())

  const selectReferenceTrace = (traceId: string) => {
    const sample = referenceSamples.find(
      (candidate) => candidate.trace_id === traceId
    )
    if (!sample) return
    setSyntheticInput(false)
    setActiveReferenceTraceId(traceId)
    setTestInput(JSON.stringify(sample.test_input, null, 2))
    setTrialOutcome(null)
  }

  const requestReferenceTrace = (traceId: string) => {
    setSyntheticInput(false)
    setActiveReferenceTraceId(traceId)
    setRequestedTraceId(traceId)
    setTrialOutcome(null)
  }

  /* ── Render ── */
  return (
    <div className="flex h-full min-h-0 flex-1 flex-col">
      <div className="flex min-h-0 flex-1 flex-col gap-8 overflow-y-auto px-6 py-5">
        {editing?.writePython && (
          <p className="text-sm text-muted-foreground">
            {t("diagnose.diagnoser.ruleManagement.writePythonHelp")}
          </p>
        )}
        <DiagnoserFormSection title={t("diagnose.diagnoser.form.basic")}>
          <div className="flex min-w-0 flex-col gap-4">
            <Field label={t("diagnose.diagnoser.llm.name")} required>
              <Input
                value={name}
                disabled={isEdit}
                onChange={(e) => setName(e.target.value)}
                placeholder={t("diagnose.diagnoser.llm.namePlaceholder")}
                className="text-xs"
              />
            </Field>
            <Field
              label={t("diagnose.common.description", {
                defaultValue: "描述",
              })}
            >
              <div className="relative">
                <Textarea
                  value={description}
                  onChange={(e) => setDescription(e.target.value)}
                  placeholder={t(
                    "diagnose.diagnoser.llm.descriptionPlaceholder"
                  )}
                  maxLength={200}
                  rows={1}
                  className="min-h-0 resize-y text-xs"
                />
                <span className="pointer-events-none absolute right-3 bottom-2 text-xs text-muted-foreground/60">
                  {description.length}/200
                </span>
              </div>
            </Field>
            <div className="@container">
              <div className="grid grid-cols-1 gap-x-6 gap-y-5 @min-[640px]:grid-cols-2">
                <Field label={t("diagnose.diagnoser.llm.dimension")} required>
                  <Select
                    value={category}
                    onValueChange={(v) => setCategory(v as Category)}
                  >
                    <SelectTrigger className="w-full">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent position="popper">
                      <SelectGroup>
                        <SelectItem value="outcome">
                          {t("diagnose.diagnoser.llm.dimensionOutcome")}
                        </SelectItem>
                        <SelectItem value="process">
                          {t("diagnose.diagnoser.llm.dimensionProcess")}
                        </SelectItem>
                        <SelectItem value="safety">
                          {t("diagnose.diagnoser.llm.dimensionSafety")}
                        </SelectItem>
                        <SelectItem value="system">
                          {t("diagnose.diagnoser.llm.dimensionSystem")}
                        </SelectItem>
                      </SelectGroup>
                    </SelectContent>
                  </Select>
                </Field>
                <Field
                  label={t("diagnose.diagnoser.llm.scenario", {
                    defaultValue: "业务场景",
                  })}
                  required
                >
                  <div className="flex min-w-0 items-center gap-2">
                    <Select
                      value={scenarioSel}
                      onValueChange={(v) => {
                        setScenarioSel(v)
                        if (v !== NEW_SCENARIO) setCustomScenario("")
                      }}
                    >
                      <SelectTrigger className="min-w-0 flex-1">
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent position="popper">
                        <SelectGroup>
                          {existingScenarios.map((sc) => (
                            <SelectItem key={sc} value={sc}>
                              {Object.hasOwn(SCENARIO_LABEL_KEYS, sc)
                                ? t(SCENARIO_LABEL_KEYS[sc])
                                : sc}
                            </SelectItem>
                          ))}
                          <SelectItem value={NEW_SCENARIO}>
                            {t("diagnose.diagnoser.llm.scenarioCustom", {
                              defaultValue: "自定义场景",
                            })}
                          </SelectItem>
                        </SelectGroup>
                      </SelectContent>
                    </Select>
                    {scenarioSel === NEW_SCENARIO ? (
                      <Input
                        value={customScenario}
                        onChange={(event) =>
                          setCustomScenario(event.target.value)
                        }
                        placeholder={t(
                          "diagnose.diagnoser.llm.scenarioCustomPlaceholder",
                          { defaultValue: "输入自定义场景名" }
                        )}
                        className="min-w-0 flex-1 text-xs"
                      />
                    ) : null}
                  </div>
                </Field>
              </div>
            </div>
          </div>
        </DiagnoserFormSection>

        <DiagnoserFormSection
          title={t("sandbox.ruleTitle")}
          help={
            <>
              {t("sandbox.ruleHelp")}
              <br />
              {t("sandbox.customRuleOnly")}
            </>
          }
        >
          <RuleSandboxSelector
            spaceId={spaceId}
            enabled={open}
            value={sandbox}
            onChange={(selection) => {
              setSandbox(selection)
              setSandboxChanged(true)
              setTrialOutcome(null)
            }}
          />
          <EnhancedRuleEnvironment
            runtime={ruleRuntime}
            available={runtime.manifest}
            loading={runtime.loading}
            local={runtime.local}
            error={runtime.error}
            timeout={runtime.effectiveTimeout}
            connectionTimeout={runtime.connection?.timeout_seconds}
            hasConnection={!!runtime.connection}
            onUpgrade={() => setRuleRuntime(runtime.manifest)}
            onExample={(example) => {
              setCode(example.code)
              setMetrics(
                metricDefinitionsFromSpec(
                  example.metrics.map((name) => ({
                    name,
                    desc: t(`enhancedRule.metric.${name}`),
                  }))
                )
              )
              setMaterials(example.materials)
              setExpectedText(expectedTextFromMaterials(example.materials))
              setSyntheticInput(true)
              setTestInput(
                JSON.stringify(
                  { ...example.input, runtime_type: runtimeType },
                  null,
                  2
                )
              )
              setActiveReferenceTraceId("")
              setTrialOutcome(null)
            }}
          />
          {open && runtime.connection && user?.user_uuid && (
            <EnhancedRuleBudgetAction
              key={`${spaceId}:${user.user_uuid}:${runtime.connection.uuid}`}
              spaceId={spaceId}
              actor={user.user_uuid}
              connection={runtime.connection}
              qualified={!!runtime.manifest}
              onBusyChange={setBudgetUpdating}
            />
          )}
        </DiagnoserFormSection>

        <DiagnoserFormSection
          title={t("diagnose.diagnoser.form.logic")}
          help={
            ruleRuntime
              ? t("enhancedRule.codeHelp")
              : t("diagnose.diagnoser.rule.codeHelp", {
                  defaultValue:
                    "规则通过 trace.spans 读取原始 Span。请先点击“查看 Trace 结构”确认实际字段。代码只能定义一个 evaluate(trace) 函数且不能使用 import；指标 name 必须与声明一致，verdict 只能是 good 或 bad。",
                })
          }
        >
          <LazyCodeMirrorEditor
            value={code}
            onChange={setCode}
            language="python"
            height="420px"
            label="Python"
            allowFullscreen
            title={t("diagnose.diagnoser.form.fullscreenPython")}
            onBrowseTraceVariables={() => setTracePickerOpen(true)}
          />
          {ruleRuntime && (
            <EnhancedRuleMaterials
              key={`${spaceId}:${user?.user_uuid}:${editing?.uuid ?? "new"}:${open}`}
              value={materials}
              expectedText={expectedText}
              onExpectedChange={setExpectedText}
              onChange={setMaterials}
            />
          )}
          {(materialError || expectedError) && (
            <p role="alert" className="text-xs text-destructive">
              {t(
                `enhancedRule.errors.${expectedError ? "expected" : materialError}`
              )}
            </p>
          )}
        </DiagnoserFormSection>

        <DiagnoserFormSection
          title={t("diagnose.diagnoser.metrics.title", {
            defaultValue: "诊断指标",
          })}
          help={t("diagnose.diagnoser.metrics.ruleHelp", {
            defaultValue:
              "规则诊断器支持定义多个指标，默认指标为 quality。诊断逻辑返回的 metrics[].name 必须与这里的指标名称完全一致。",
          })}
        >
          <MetricDefinitionEditor
            mode="rule"
            metrics={metrics}
            onChange={(nextMetrics) => {
              setMetrics(nextMetrics)
              setTrialOutcome(null)
            }}
          />
        </DiagnoserFormSection>

        <DiagnoserFormSection
          title={t("diagnose.diagnoser.form.debug")}
          help={t("diagnose.diagnoser.form.debugHelp")}
          status={
            isValidatedTrial(trialResult)
              ? t("diagnose.diagnoser.form.validated")
              : undefined
          }
        >
          <div className="flex items-center gap-2">
            <div className="min-w-0 flex-1">
              <TraceIdInput
                key={activeReferenceTraceId}
                value={activeReferenceTraceId}
                samples={referenceSamples}
                loading={
                  firstReferenceTraceQuery.isLoading ||
                  requestedTraceQuery.isFetching
                }
                onSelect={selectReferenceTrace}
                onRequest={requestReferenceTrace}
                popoverClassName="w-[var(--radix-popover-trigger-width)]"
              />
            </div>
            <Button
              type="button"
              variant="outline"
              size="sm"
              className="h-8 min-w-24 shrink-0 gap-1.5 px-2 text-xs"
              onClick={handleTrial}
              disabled={
                trialLoading ||
                inputPreviewBlocked ||
                !canRunTrial ||
                runtimeBlocked ||
                !!materialError ||
                expectedError ||
                !code.trim() ||
                !areMetricDefinitionsValid(metrics)
              }
            >
              {trialLoading ? (
                <Loader2 className="size-3.5 animate-spin" />
              ) : (
                <Play className="size-3.5" />
              )}
              {t("diagnose.diagnoser.form.run")}
            </Button>
          </div>
          {inputPreviewRequired && (
            <RuleInputBudget
              data={inputPreview.data}
              loading={inputPreview.loading}
              error={inputPreview.error}
              onRetry={inputPreview.retry}
            />
          )}
          {syntheticInput && (
            <div className="space-y-2">
              <p className="text-xs text-muted-foreground">
                {t("enhancedRule.syntheticActive")}
              </p>
              <Textarea
                aria-label={t("enhancedRule.syntheticInput")}
                value={testInput}
                onChange={(event) => setTestInput(event.target.value)}
                rows={6}
                wrap="off"
                className="field-sizing-fixed max-h-48 overflow-auto font-mono text-xs"
                spellCheck={false}
              />
            </div>
          )}
          {trialResult && <TrialResultView result={trialResult} ruleEvidence />}
        </DiagnoserFormSection>
      </div>

      <DialogFooter className="shrink-0 justify-end border-t bg-background px-6 py-4">
        <Button variant="outline" size="sm" onClick={onClose}>
          {t("common.cancel")}
        </Button>
        <Button
          size="sm"
          disabled={!canSubmit}
          onClick={() => mutation.mutate()}
        >
          {mutation.isPending
            ? t("diagnose.diagnoser.rule.submitting", {
                defaultValue: "提交中...",
              })
            : isEdit
              ? t("common.save")
              : t("diagnose.diagnoser.rule.create", { defaultValue: "创建" })}
        </Button>
      </DialogFooter>
      <RawTraceVariablePicker
        open={tracePickerOpen}
        onOpenChange={setTracePickerOpen}
        expressionMode="python"
        sample={activeReferenceSample?.test_input}
        samples={referenceSamples}
        onActiveTraceChange={selectReferenceTrace}
        onRequestTrace={requestReferenceTrace}
        traceLoading={
          firstReferenceTraceQuery.isFetching || requestedTraceQuery.isFetching
        }
        traceLoadError={
          requestedTraceQuery.isError || firstReferenceTraceQuery.isError
            ? (() => {
                const error =
                  requestedTraceQuery.error ?? firstReferenceTraceQuery.error
                return error instanceof Error
                  ? error.message
                  : t("diagnose.diagnoser.form.traceLoadFailed")
              })()
            : null
        }
      />
    </div>
  )
}

/* ── Field helper (same pattern as CreateLlmDiagnoserModal) ── */

function Field({
  label,
  required,
  help,
  children,
  className,
}: {
  label: string
  required?: boolean
  help?: React.ReactNode
  children: React.ReactNode
  className?: string
}) {
  const { t } = useTranslation()
  return (
    <div className={`min-w-0 space-y-1.5 ${className ?? ""}`}>
      <div className="flex items-center gap-0.5">
        <label className="flex items-center gap-1 text-xs font-medium text-muted-foreground">
          {label}
          {required && <span className="text-[var(--color-error)]">*</span>}
        </label>
        {help ? (
          <Tooltip>
            <TooltipTrigger asChild>
              <Button
                type="button"
                variant="ghost"
                size="icon-xs"
                className="size-4 p-0 text-muted-foreground hover:bg-transparent hover:text-foreground"
                aria-label={t("diagnose.diagnoser.form.help")}
              >
                <CircleHelp className="size-3.5" />
              </Button>
            </TooltipTrigger>
            <TooltipContent side="top" className="max-w-xs">
              {help}
            </TooltipContent>
          </Tooltip>
        ) : null}
      </div>
      {children}
    </div>
  )
}
