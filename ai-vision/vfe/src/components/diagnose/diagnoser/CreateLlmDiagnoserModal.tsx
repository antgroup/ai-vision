/**
 * 新建 / 编辑 自定义 LLM 诊断器表单。
 *
 * 通用：用户写诊断 prompt,选输出类型(布尔/数值)、
 * 绑定 dataset、采样。prompt 中用 {{var}} 引用 trace 字段,自动按需取值。
 * - 创建:createDiagnoser(type=custom, mode=llm, spec)
 * - 编辑(传 editing):updateDiagnoser,回传**完整 spec**(避免后端整体替换丢字段)
 * 后端契约见 agentlens docs/llm;必填 prompt/metrics[]。
 */

import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react"
import { useTranslation } from "react-i18next"
import { useMutation, useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"
import { CircleHelp, Loader2, Play, Settings2, Sparkles } from "lucide-react"
import { useAgent } from "@/contexts/AgentContext"
import { useSpace } from "@/contexts/SpaceContext"
import { useUser } from "@/contexts/UserContext"
import { ModelConnectionSelect } from "@/features/model-connections/ModelConnectionSelect"
import { ModelConnectionManagerDialog } from "@/features/model-connections/ModelConnectionsSection"

import {
  Dialog,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { DialogContent } from "@/components/dialogs/DialogContent"
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

import { ModelValidationSummary } from "../ModelValidationSummary"
import { createDiagnoser, updateDiagnoser, runLlmTrial } from "../diagnoseApi"
import {
  SCENARIO_LABEL_KEYS,
  type LlmTrialResponse,
  type ModelConnection,
} from "../types"
import { LazyCodeMirrorEditor } from "./LazyCodeMirrorEditor"
import { DiagnoserFormSection } from "./DiagnoserFormSection"
import { TrialResultView as LlmTrialResultView } from "./TrialResultView"
import { isValidatedTrial } from "./trialResult"
import { MetricDefinitionEditor } from "./MetricDefinitionEditor"
import {
  buildLlmDiagnoserSpec,
  metricDefinitionsFromSpec,
} from "./metricDefinitionModel"
import { RawTraceVariablePicker, TraceIdInput } from "./RawTraceVariablePicker"
import { useDiagnoserReferenceTraceSample } from "./useDiagnoserReferenceTraces"

/** 编辑模式入参:诊断器 uuid + 现有 spec/name/description(预填用)。 */
export interface LlmDiagnoserEditTarget {
  uuid: string
  name: string
  description: string
  spec: Record<string, unknown>
}

interface CreateLlmDiagnoserModalProps {
  open: boolean
  spaceId: string
  agentUuid: string
  onClose: () => void
  /** 传入则进入编辑模式;不传为创建模式。 */
  editing?: LlmDiagnoserEditTarget | null
  /** 内联模式:不套 Dialog,只渲染表单内容(供统一新建弹窗嵌入)。 */
  inline?: boolean
  /** 空间已存在的业务场景（新建诊断器选项来源，去重 trim）。 */
  existingScenarios?: string[]
  /** 当前空间可复用的模型连接。连接为空时允许保存，但不能试跑。 */
  modelConnections?: ModelConnection[]
}

type Category = "outcome" | "process" | "safety" | "system"

/** 自定义场景哨兵值：选中它表示用户要新建一个场景名（走 customScenario 输入框）。 */
const NEW_SCENARIO = "__new__"

const DEFAULT_LLM_PROMPT = `## 诊断任务
<!--【编写完成后必须删除本注释】
编写说明：用一句话描述一个明确、可由判定材料验证的问题。
编写示例：
判断 Agent 最终回答是否完整覆盖用户明确提出的全部问题。
-->
【请填写明确、单一、可验证的诊断问题】。

## 判定材料
<!--【编写完成后必须删除本注释】
编写说明：点击「查看 Trace 结构」定位并复制所需字段，只保留完成判断必需的材料。
编写示例：
- 用户问题：{{spans[name:用户问题所在的 Span 的名称].代表用户问题的字段名称}}
- Agent 回答：{{spans[name:Agent 回答所在的 Span 的名称].代表 Agent 回答的字段名称}}
-->
- 【材料名称】：{{字段表达式}}

## 判定方法
<!--【编写完成后必须删除本注释】
编写说明：分别填写 bad 和 good 的明确条件，确保二者不重叠。
编写示例：
- bad：至少一个明确诉求未被回应、答非所问或存在明显错误。
- good：所有明确诉求均得到正确且有效的回应。
-->
1. 只依据判定材料，不猜测不可见信息。
2. 判定为 bad：【填写问题条件及其必要证据】。
3. 判定为 good：【填写通过条件】。
4. 材料不足时判定为 good，并说明缺少的关键材料。
5. 说明判定理由并引用关键材料；判定为 bad 时指出具体问题。`

/** 新建默认选中：空间有"通用"则选通用，否则第一个已有场景，都没有则进入自定义。 */
function defaultScenarioSel(existing: string[]): string {
  if (existing.includes("common")) return "common"
  return existing[0] ?? NEW_SCENARIO
}

const DEFAULTS = {
  name: "",
  description: "",
  prompt: DEFAULT_LLM_PROMPT,
  category: "outcome" as Category,
  scenarioSel: NEW_SCENARIO,
  customScenario: "",
}

export function CreateLlmDiagnoserModal({
  open,
  spaceId,
  agentUuid,
  onClose,
  editing,
  inline = false,
  existingScenarios = [],
  modelConnections = [],
}: CreateLlmDiagnoserModalProps) {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const isEdit = !!editing
  const existingMetrics = useMemo(
    () =>
      editing ? metricDefinitionsFromSpec(editing.spec.metrics) : undefined,
    [editing]
  )
  const scenarioOptionsRef = useRef(existingScenarios)
  useEffect(() => {
    scenarioOptionsRef.current = existingScenarios
  }, [existingScenarios])

  const [name, setName] = useState(DEFAULTS.name)
  const [description, setDescription] = useState(DEFAULTS.description)
  const [prompt, setPrompt] = useState(DEFAULTS.prompt)
  const [category, setCategory] = useState<Category>(DEFAULTS.category)
  const [scenarioSel, setScenarioSel] = useState<string>(DEFAULTS.scenarioSel)
  const [customScenario, setCustomScenario] = useState<string>(
    DEFAULTS.customScenario
  )
  const [modelConnectionUuid, setModelConnectionUuid] = useState("")
  const [modelManagerOpen, setModelManagerOpen] = useState(false)
  const [tracePickerOpen, setTracePickerOpen] = useState(false)
  const [activeReferenceTraceId, setActiveReferenceTraceId] = useState("")
  const [requestedTraceId, setRequestedTraceId] = useState("")
  const [pendingTrial, setPendingTrial] = useState<string | null>(null)
  const [trialOutcome, setTrialOutcome] = useState<{
    fingerprint: string
    result: LlmTrialResponse
  } | null>(null)
  const activeTrial = useRef<{ fingerprint: string } | null>(null)

  const { currentAgent } = useAgent()
  const { currentSpace } = useSpace()
  const { user, loading: userLoading } = useUser()
  const savedRuntimeType = editing?.spec.runtime_type
  const runtimeType =
    typeof savedRuntimeType === "string"
      ? savedRuntimeType
      : currentAgent?.runtime_type || "other"

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
    const samplesByTraceId = new Map<
      string,
      { trace_id: string; test_input: Record<string, unknown> }
    >()
    for (const sample of requestedTraceQuery.data?.samples ?? []) {
      samplesByTraceId.set(sample.trace_id, sample)
    }
    for (const sample of firstReferenceTraceQuery.data?.samples ?? []) {
      if (!samplesByTraceId.has(sample.trace_id)) {
        samplesByTraceId.set(sample.trace_id, sample)
      }
    }
    return [...samplesByTraceId.values()]
  }, [
    firstReferenceTraceQuery.data?.samples,
    requestedTraceQuery.data?.samples,
  ])
  const activeReferenceSample =
    referenceSamples.find(
      (sample) => sample.trace_id === activeReferenceTraceId
    ) ?? referenceSamples[0]
  const referenceTestInput = activeReferenceSample?.test_input

  const selectedModelConnection = useMemo(
    () =>
      modelConnections.find(
        (connection) => connection.uuid === modelConnectionUuid
      ) ?? null,
    [modelConnectionUuid, modelConnections]
  )
  const modelAvailable = selectedModelConnection?.status === "available"
  const canRunTrial =
    open &&
    !!spaceId &&
    !!agentUuid &&
    !userLoading &&
    !!user?.user_uuid &&
    currentSpace?.id === spaceId &&
    currentSpace.permissions.includes("diagnosis.execute") &&
    modelAvailable &&
    (!activeReferenceTraceId ||
      activeReferenceSample?.trace_id === activeReferenceTraceId)

  const selectReferenceTrace = (traceId: string) => {
    setActiveReferenceTraceId(traceId)
    setTrialOutcome(null)
  }

  const requestReferenceTrace = (traceId: string) => {
    setActiveReferenceTraceId(traceId)
    setRequestedTraceId(traceId)
    setTrialOutcome(null)
  }

  /* eslint-disable react-hooks/set-state-in-effect -- Dialog 打开时按模式预填表单 */
  useEffect(() => {
    if (!open) return
    const scenarios = scenarioOptionsRef.current
    if (editing) {
      const s = editing.spec ?? {}
      setName(editing.name ?? "")
      setDescription(editing.description ?? "")
      setPrompt(typeof s.prompt === "string" ? s.prompt : "")
      const dim = s.category
      setCategory(
        dim === "process" || dim === "safety" || dim === "system"
          ? dim
          : "outcome"
      )
      // scenario：已有场景直选；旧自定义场景名进输入框；空则默认
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
      setModelConnectionUuid(
        typeof s.model_connection_uuid === "string"
          ? s.model_connection_uuid
          : ""
      )
      setTracePickerOpen(false)
      setActiveReferenceTraceId("")
      setRequestedTraceId("")
      setTrialOutcome(null)
    } else {
      setName(DEFAULTS.name)
      setDescription(DEFAULTS.description)
      setPrompt(DEFAULTS.prompt)
      setCategory(DEFAULTS.category)
      setScenarioSel(defaultScenarioSel(scenarios))
      setCustomScenario(DEFAULTS.customScenario)
      setModelConnectionUuid("")
      setTracePickerOpen(false)
      setActiveReferenceTraceId("")
      setRequestedTraceId("")
      setTrialOutcome(null)
    }
  }, [editing, open])
  /* eslint-enable react-hooks/set-state-in-effect */

  const buildSpec = () =>
    buildLlmDiagnoserSpec({
      existingSpec: editing?.spec,
      prompt,
      runtimeType,
      category,
      scenario:
        scenarioSel === NEW_SCENARIO ? customScenario.trim() : scenarioSel,
      modelConnectionUuid,
    })

  const trialFingerprint = JSON.stringify([
    open,
    spaceId,
    agentUuid,
    editing?.uuid,
    user?.user_uuid,
    canRunTrial,
    name.trim(),
    buildSpec(),
    activeReferenceTraceId,
    requestedTraceId,
    activeReferenceSample?.trace_id,
    referenceTestInput,
  ])
  const trialResult =
    trialOutcome?.fingerprint === trialFingerprint ? trialOutcome.result : null
  const trialLoading = pendingTrial === trialFingerprint
  useLayoutEffect(() => {
    // Returning to an old draft must not revive its result or pending callbacks.
    // eslint-disable-next-line react-hooks/set-state-in-effect -- Trial ownership ends with its input/context lifecycle.
    setTrialOutcome(null)
    setPendingTrial(null)
    return () => {
      activeTrial.current = null
    }
  }, [trialFingerprint])

  const mutation = useMutation({
    mutationFn: () => {
      if (editing) {
        // 编辑:回传完整 spec(后端 update 整体替换 spec,不能只传增量)
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
        mode: "llm",
        spec: buildSpec(),
      })
    },
    onSuccess: () => {
      toast.success(
        isEdit
          ? t("diagnose.diagnoser.llm.updateSuccess")
          : t("diagnose.diagnoser.llm.success")
      )
      queryClient.invalidateQueries({ queryKey: ["diagnosers"] })
      onClose()
    },
    onError: (e: Error) => toast.error(e.message),
  })

  // 自定义场景模式且未填名 → 阻断提交
  const canSubmit =
    !!name.trim() &&
    !!prompt.trim() &&
    !mutation.isPending &&
    (scenarioSel !== NEW_SCENARIO || !!customScenario.trim())

  const runTrial = async () => {
    const testInput = referenceTestInput
    if (
      !testInput ||
      typeof testInput !== "object" ||
      Array.isArray(testInput) ||
      !canRunTrial ||
      activeTrial.current?.fingerprint === trialFingerprint
    ) {
      return
    }
    const request = { fingerprint: trialFingerprint }
    activeTrial.current = request
    setPendingTrial(trialFingerprint)
    setTrialOutcome(null)
    try {
      const result = await runLlmTrial(spaceId, {
        name: name.trim(),
        spec: buildSpec(),
        test_input: testInput,
      })
      if (activeTrial.current !== request) return
      setTrialOutcome({ fingerprint: request.fingerprint, result })
      if (result.execution.status === "failed") {
        toast.error(
          result.execution.msg || t("diagnose.diagnoser.form.trialFailed")
        )
      }
    } catch (error) {
      if (activeTrial.current !== request) return
      const message =
        error instanceof Error
          ? error.message
          : t("diagnose.diagnoser.form.trialFailed")
      setTrialOutcome({
        fingerprint: request.fingerprint,
        result: {
          verdict: "failed",
          summary: "",
          metrics: [],
          execution: {
            status: "failed",
            code: "request_failed",
            msg: message,
            evidence: {},
          },
        },
      })
      toast.error(message)
    } finally {
      if (activeTrial.current === request) {
        activeTrial.current = null
        setPendingTrial(null)
      }
    }
  }

  const formContent = (
    <>
      {!inline && (
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2 text-xl font-medium">
            <Sparkles className="size-4" />
            {isEdit
              ? t("diagnose.diagnoser.llm.editTitle")
              : t("diagnose.diagnoser.llm.title")}
          </DialogTitle>
        </DialogHeader>
      )}

      <div className="flex min-h-0 min-w-0 flex-1 flex-col gap-8 overflow-y-auto px-6 py-5">
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
          title={t("diagnose.diagnoser.form.logic")}
          help={t("diagnose.diagnoser.form.logicHelp")}
        >
          <LazyCodeMirrorEditor
            value={prompt}
            onChange={setPrompt}
            label="Markdown"
            language="markdown"
            height="420px"
            allowFullscreen
            title={t("diagnose.diagnoser.form.fullscreenLogic")}
            onBrowseTraceVariables={() => setTracePickerOpen(true)}
            browseTraceVariablesDisabled={false}
          />
        </DiagnoserFormSection>

        <DiagnoserFormSection
          title={t("diagnose.diagnoser.metrics.title", {
            defaultValue: "诊断指标",
          })}
          help={
            isEdit
              ? t("diagnose.diagnoser.metrics.llmEditHelp", {
                  defaultValue:
                    "现有指标仅供查看。编辑其他配置时会保留全部指标定义。",
                })
              : t("diagnose.diagnoser.metrics.llmHelp", {
                  defaultValue:
                    "新建 LLM 诊断器默认使用指标 quality（质量），本表单不支持新增、删除或修改指标。",
                })
          }
        >
          <MetricDefinitionEditor mode="llm" metrics={existingMetrics} />
        </DiagnoserFormSection>

        <DiagnoserFormSection
          title={t("diagnose.diagnoser.form.model")}
          help={t("diagnose.diagnoser.form.modelHelp")}
        >
          <div className="flex flex-col gap-3">
            <div className="flex items-center gap-2">
              <ModelConnectionSelect
                connections={modelConnections}
                value={modelConnectionUuid}
                onValueChange={(value) => {
                  setModelConnectionUuid(value)
                  setTrialOutcome(null)
                }}
                placeholder={t("diagnose.diagnoser.form.selectModel")}
                noneLabel={t("diagnose.diagnoser.form.noModel")}
                diagnosticValidation
              />
              <Button
                type="button"
                variant="outline"
                size="sm"
                className="h-8 min-w-24 shrink-0 gap-1.5 px-2 text-xs"
                onClick={() => setModelManagerOpen(true)}
                aria-label={t("diagnose.diagnoser.form.manageModels")}
              >
                <Settings2 className="size-3.5" />
                {t("diagnose.diagnoser.form.manageConnections")}
              </Button>
            </div>
          </div>
        </DiagnoserFormSection>

        {selectedModelConnection ? (
          <ModelValidationSummary
            report={selectedModelConnection.diagnosticValidation}
            currentConfiguration
          />
        ) : null}

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
                key={activeReferenceSample?.trace_id ?? ""}
                value={activeReferenceSample?.trace_id ?? ""}
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
            <div className="flex shrink-0 items-center">
              {modelAvailable ? (
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  className="h-8 min-w-24 shrink-0 gap-1.5 px-2 text-xs"
                  disabled={trialLoading || !referenceTestInput || !canRunTrial}
                  onClick={() => void runTrial()}
                >
                  {trialLoading ? (
                    <Loader2 className="size-3.5 animate-spin" />
                  ) : (
                    <Play className="size-3.5" />
                  )}
                  {t("diagnose.diagnoser.form.run")}
                </Button>
              ) : (
                <Tooltip>
                  <TooltipTrigger asChild>
                    <span
                      className="inline-flex cursor-not-allowed"
                      tabIndex={0}
                    >
                      <Button
                        type="button"
                        variant="outline"
                        size="sm"
                        disabled
                        className="pointer-events-none h-8 min-w-24 gap-1.5 px-2 text-xs"
                      >
                        <Play className="size-3.5" />
                        {t("diagnose.diagnoser.form.run")}
                      </Button>
                    </span>
                  </TooltipTrigger>
                  <TooltipContent side="top">
                    {t("diagnose.diagnoser.form.healthyModelRequired")}
                  </TooltipContent>
                </Tooltip>
              )}
            </div>
          </div>
          <LlmTrialResultView result={trialResult} />
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
            ? t("diagnose.diagnoser.llm.submitting")
            : isEdit
              ? t("common.save")
              : t("diagnose.diagnoser.form.create")}
        </Button>
      </DialogFooter>
      <RawTraceVariablePicker
        open={tracePickerOpen}
        onOpenChange={setTracePickerOpen}
        sample={referenceTestInput}
        samples={referenceSamples}
        onActiveTraceChange={setActiveReferenceTraceId}
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
      <ModelConnectionManagerDialog
        open={modelManagerOpen}
        spaceId={spaceId}
        onOpenChange={setModelManagerOpen}
        onConnectionCreated={(connection) => {
          setModelConnectionUuid(connection.uuid)
          setTrialOutcome(null)
        }}
      />
    </>
  )

  if (inline) {
    return <div className="flex min-h-0 flex-1 flex-col">{formContent}</div>
  }

  return (
    <Dialog open={open} onOpenChange={(next) => !next && onClose()}>
      <DialogContent
        size="workspace"
        fillHeight
        className="flex flex-col overflow-hidden"
      >
        {formContent}
      </DialogContent>
    </Dialog>
  )
}

function Field({
  label,
  required,
  hint,
  help,
  children,
  className,
}: {
  label: string
  required?: boolean
  hint?: string
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
      {hint && <p className="text-xs text-muted-foreground">{hint}</p>}
      {children}
    </div>
  )
}
