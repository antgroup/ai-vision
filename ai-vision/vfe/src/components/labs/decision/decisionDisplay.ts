import { TONE_TAG_CLASS } from "@/components/labs/shared/labsFormat"
import { parseTimestampMs } from "@/components/traces-otel/format"
import type { OTelSpan } from "@/components/traces-otel/types"

export type DecisionBand = "high" | "medium" | "low"

/** Shared across providers; the derived forms use the same cut-offs for acting on an answer. */
export const DECISION_THRESHOLDS = { high: 0.8, medium: 0.5 }

export const BAND_TAG_CLASS: Record<DecisionBand, string> = {
  high: TONE_TAG_CLASS.success,
  medium: TONE_TAG_CLASS.warning,
  low: TONE_TAG_CLASS.error,
}

interface DecisionOption {
  key: string
  probability: number
}

export interface DecisionSpanInfo {
  type: string
  provider: string | null
  model: string | null
  choice: string | null
  /** What the agent actually used; differs from `choice` after a fallback. */
  applied: string | null
  action: string | null
  confidence: number | null
  band: DecisionBand | null
  options: DecisionOption[]
  latencyMs: number | null
  raw: string | null
  toolCallId: string | null
  responseIndex: string | null
  /**
   * Token naming the governed call (e.g. "notes/b.md"), extracted from the
   * 待执行动作 clause; adjacency only trusts later calls referencing it.
   */
  targetHint: string | null
  /** True when reconstructed from SystemOne traffic, not standard attributes. */
  derived?: boolean
  /**
   * Question output kind: "noul" | "choice" | "score". Null for native
   * decisions and curl payloads whose request side was lost.
   */
  kind?: string | null
  /** Human-readable question text (instructions), falling back to the key. */
  question?: string | null
}

/** Types with a dedicated label; open Jev judgments and unknown native values share "other". */
const KNOWN_TYPES = new Set(["model_routing", "tool_approval"])

function text(attrs: Record<string, unknown>, key: string): string | null {
  const value = attrs[key]
  if (typeof value === "string" && value.trim()) return value.trim()
  if (typeof value === "number" && Number.isFinite(value)) return String(value)
  return null
}

function num(attrs: Record<string, unknown>, key: string): number | null {
  const value = attrs[key]
  const parsed = typeof value === "string" ? Number(value) : value
  return typeof parsed === "number" && Number.isFinite(parsed) ? parsed : null
}

function parseOptions(raw: string | null): DecisionOption[] {
  if (!raw) return []
  try {
    const parsed: unknown = JSON.parse(raw)
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed))
      return []
    return Object.entries(parsed as Record<string, unknown>)
      .filter(
        (entry): entry is [string, number] => typeof entry[1] === "number"
      )
      .map(([key, probability]) => ({ key, probability }))
      .sort((a, b) => b.probability - a.probability)
  } catch {
    return []
  }
}

function decisionBand(confidence: number): DecisionBand {
  if (confidence >= DECISION_THRESHOLDS.high) return "high"
  if (confidence >= DECISION_THRESHOLDS.medium) return "medium"
  return "low"
}

const SYSTEM_ONE_ENDPOINT = /\/v1\/systemone(?:\b|["'\s])/
/** Keypair form only, used to extract the model value ("model":"gpt-6-sol"). */
const MODEL_KEYPAIR =
  /(?:"model"\s*:\s*"|(?:['"\\]|\b)model(?:['"\\]|\b)\s*:\s*['"\\])([\w.-]+)/i
const JEV_QUESTION_KEY =
  /["']?questions["']?\s*:\s*\{(?:\s|\\[nrt]|n(?=\s*["'{]))*["']?([A-Za-z_][\w-]*)["']?\s*:\s*\{/
const JEV_QUESTION_TYPE = /["']type["']\s*:\s*["'](choice|noul|score)["']/
/** Curl payload: the question's human-readable instructions text. */
const QUESTION_INSTRUCTIONS = /"instructions"\s*:\s*"((?:[^"\\]|\\.)*)"/
const HTTP_FAILURE = /\bHTTP(?:_ERROR)?\s+(\d{3})\b\s*(\{[\s\S]*\})?/
/** Bounded slice for noul questions that define no criteria block. */
const QUESTION_SLICE_MAX = 600
/** A command touching the decision endpoint records the decision itself, not the governed call. */
const SYSTEM_ONE_RECORDING = /\/v1\/systemone/

/**
 * Model-name shapes without a hardcoded list: a lowercase stem followed by
 * one or more hyphen/dot-separated segments carrying a digit ("gpt-6-sol",
 * "deepseek-v3.1", "qwen2.5-72b"). Brand-agnostic — new models match with
 * no code changes; plain words (kevin, semifinal) and bare tokens (v2, or
 * o3 without a numbered segment) never do. Consumed only by the
 * option-based routing inference: a stray tech token (utf-8, sha-256) can
 * match, but a misfire needs two differently-shaped ones among one
 * choice's options — an acceptable trade for list-free recognition.
 */
const MODEL_NAME_SHAPE =
  /\b[a-z][a-z0-9]{1,}(?:[-.](?:\d[\w.-]*|[a-z]*\d[\w-]*))+/g

/** Distinct model-shaped tokens across the given option texts. */
function countModelShapes(values: Array<string | null>): number {
  const names = new Set<string>()
  for (const value of values) {
    if (!value) continue
    for (const match of value.matchAll(MODEL_NAME_SHAPE))
      names.add(match[0].toLowerCase())
  }
  return names.size
}

/**
 * Scenario wording families shared by the curl and SDK forms: approval is
 * the more specific signal and is always tested before routing, since
 * routing questions often mention the model to route to.
 */
const APPROVAL_WORDING =
  /approval|approve|allow|permission|safe to run|放行|允许|不经确认/i
// `rout` is word-bounded: route/routes/routed/routing/reroute(s/ing) read as
// model routing; "router" (network gear) must not, so a bare trailing \b
// would fail it — hence the explicit e[ds]?/ing endings.
const ROUTING_WORDING = /\b(?:re)?rout(?:e[ds]?|ing)\b|model|哪个模型|交给哪/i
/** Approval vocabulary among a choice's own options. */
const APPROVAL_OPTIONS = /allow|deny|ask_user|approve|放行|确认/i
/**
 * SDK calls: a system_one (Python snake_case) or systemOne (JS/TS
 * camelCase) invocation asking several questions; results print as plain
 * "key: value" lines with no probabilities.
 */
const SDK_SYSTEM_ONE = /\bsystemOne\s*\(|\bsystem_one\s*\(/
/**
 * Typed question declaration: `"key": Noul(...)`. Used to confirm a
 * systemOne/system_one call — the typed questions sit inline in every
 * supported form (Python positional or keyword arguments, JS object
 * literals). Case-insensitive: Python uses Noul/Choice/Score, JS uses
 * lowercase calls. Non-global on purpose: `.test()` on a /g regex leaks
 * lastIndex state.
 */
const SDK_TYPED_QUESTION =
  /["']?([A-Za-z_][\w-]*)["']?\s*:\s*(?:Noul|Choice|Score)\s*\(/i
/** Case-insensitive: Python uses Noul/Choice/Score, JS uses lowercase calls;
 * the key may be quoted (Python) or a bare identifier (JS object literal). */
const SDK_QUESTION =
  /["']?([A-Za-z_][\w-]*)["']?\s*:\s*(Noul|Choice|Score)\s*\(/gi
const SDK_RESULT_LINE = /([A-Za-z_][\w-]*)\s*:\s*([^\n"']+)/g
const SDK_MODEL = /model\s*=\s*['"]([\w.-]+)['"]/

function rawText(value: unknown): string | null {
  if (typeof value === "string" && value.trim()) return value.trim()
  if (value === null || value === undefined) return null
  try {
    return JSON.stringify(value)
  } catch {
    return null
  }
}

/** Total scanned-suffix budget so pathological non-JSON blobs stay O(n). */
const JSON_SCAN_BUDGET = 1 << 20

function parseJsonBlock(raw: string): Record<string, unknown> | null {
  let budget = JSON_SCAN_BUDGET
  let start = raw.indexOf("{")
  while (start >= 0 && budget > 0) {
    budget -= raw.length - start
    try {
      const parsed: unknown = JSON.parse(raw.slice(start))
      if (parsed && typeof parsed === "object" && !Array.isArray(parsed))
        return parsed as Record<string, unknown>
    } catch {
      // Text such as "HTTP_ERROR 451 {...}" contains a JSON suffix.
    }
    start = raw.indexOf("{", start + 1)
  }
  return null
}

function asNumber(value: unknown): number | null {
  if (typeof value === "string" && !value.trim()) return null
  const parsed = typeof value === "string" ? Number(value) : value
  return typeof parsed === "number" && Number.isFinite(parsed) ? parsed : null
}

function matched(value: string, pattern: RegExp): string | null {
  return value.match(pattern)?.[1] ?? null
}

function timestampMs(value: string | undefined): number | null {
  return value ? parseTimestampMs(value) : null
}

/**
 * Bounded window after a systemOne/system_one call where its typed
 * question declarations sit in every supported form (Python positional
 * or keyword arguments, JS object literals). Wide because a long `state`
 * value can push the first question far from the call site.
 */
const SDK_ARGUMENT_WINDOW = 2000

/**
 * The vendor-neutral SDK form: a systemOne/system_one call whose typed
 * questions (`"key": Noul/Choice/Score(...)`) sit inline in the window
 * after it. Python positional (`system_one("state text", {...})`),
 * Python keyword arguments (`state=..., questions={...}`), and JS object
 * literals (`systemOne({ state, questions })`) all declare questions the
 * same way, so one shape check covers every form. Package names and
 * imports are deliberately not matched — recognition rides on the call's
 * own shape, so it survives SDK rebranding. Doc code that merely shows a
 * call never reaches a card: the parsers below only fire on executed
 * outputs (result lines or printed bare values).
 */
function hasSystemOneCall(source: string): boolean {
  const call = SDK_SYSTEM_ONE.exec(source)
  if (!call) return false
  const window = source.slice(call.index, call.index + SDK_ARGUMENT_WINDOW)
  return SDK_TYPED_QUESTION.test(window)
}

/**
 * Unified traffic gate shared by every derived-form parser: the span counts
 * as decision traffic when its request text hits the SystemOne endpoint
 * (the curl form) or a systemOne/system_one call with typed questions appears
 * in its own request (the SDK form). Conversation code cannot establish what
 * an arbitrary script executed: file-only runs need source provenance before
 * they can be reconstructed. Native decisions and observation payloads do not
 * rely on this gate.
 */
function isDecisionTraffic(argumentText: string): boolean {
  return (
    SYSTEM_ONE_ENDPOINT.test(argumentText) || hasSystemOneCall(argumentText)
  )
}

/**
 * Bash-style arguments embed the HTTP payload as a JSON-encoded string, so
 * quotes arrive as \" and newlines as \n. Strip those escape backslashes so
 * the question patterns below match the actual payload text.
 */
export function decodeEmbeddedPayload(raw: string): string {
  return raw.replace(/\\(["'\\nrt])/g, "$1")
}

/** Index of the brace closing the object opened at `openIndex`, or -1. */
function findMatchingBrace(text: string, openIndex: number): number {
  let depth = 0
  let inString = false
  let escaped = false
  for (let i = openIndex; i < text.length; i++) {
    const ch = text[i]
    if (inString) {
      if (escaped) escaped = false
      else if (ch === "\\") escaped = true
      else if (ch === '"' || ch === "'") inString = false
      continue
    }
    if (ch === '"' || ch === "'") inString = true
    else if (ch === "{") depth++
    else if (ch === "}") {
      depth--
      if (depth === 0) return i
    }
  }
  return -1
}

/** One question of a request's `questions` block: key plus body offsets. */
interface JevQuestionRef {
  key: string
  start: number
  end: number
}

/**
 * Enumerates every question of a request's `questions` block, in payload
 * order. One curl call can ask several questions; brace matching is
 * string-aware so criteria text cannot swallow the sibling questions.
 */
function extractJevQuestions(argumentText: string): JevQuestionRef[] {
  const header = /["']?questions["']?\s*:\s*\{/.exec(argumentText)
  if (!header) return []
  const blockStart = header.index + header[0].length - 1
  const blockEnd = findMatchingBrace(argumentText, blockStart)
  if (blockEnd < 0) return []
  const refs: JevQuestionRef[] = []
  const keyPattern = /["']?([A-Za-z_][\w-]*)["']?\s*:\s*\{/g
  keyPattern.lastIndex = blockStart + 1
  while (keyPattern.lastIndex < blockEnd) {
    const match = keyPattern.exec(argumentText)
    if (!match || match.index >= blockEnd) break
    const open = match.index + match[0].length - 1
    const close = findMatchingBrace(argumentText, open)
    refs.push({
      key: match[1],
      start: match.index,
      end:
        close < 0 ? Math.min(open + QUESTION_SLICE_MAX, blockEnd) : close + 1,
    })
    // Resume past the question body: its own keys ("type", "criteria")
    // must not read as further questions.
    keyPattern.lastIndex = close < 0 ? open + 1 : close + 1
  }
  return refs
}

/**
 * Derives decisions from SystemOne HTTP calls — the curl form where the
 * tool span carries the request payload and the JSON answer directly.
 * One call may ask several questions; like the SDK parser below, every
 * answer renders as its own entry.
 */
function resolveDerivedHttpSpanInfos(
  attrs: Record<string, unknown>,
  argumentText: string,
  resultRaw: string
): DecisionSpanInfo[] {
  const response = parseJsonBlock(resultRaw)
  const answers =
    response?.answers && typeof response.answers === "object"
      ? (response.answers as Record<string, unknown>)
      : null
  const failure = response ? HTTP_FAILURE.exec(resultRaw) : null
  if (!(answers || failure)) return []
  // An answers structure alone can come from doc samples: corroborate with
  // the request side — a real curl call carries the endpoint in its command.
  if (answers && !failure && !SYSTEM_ONE_ENDPOINT.test(argumentText)) return []

  const model =
    (response && typeof response.model === "string" ? response.model : null) ??
    matched(argumentText, MODEL_KEYPAIR)
  const request = {
    model,
    endpoint: argumentText.match(SYSTEM_ONE_ENDPOINT)?.[0] ?? null,
  }
  const responseSummary = failure
    ? { status: failure[1] ? Number(failure[1]) : null, error: response ?? {} }
    : response

  /** One entry per question; questions without an answer stay silent. */
  const buildInfo = (
    questionKey: string,
    questionDefinition: string,
    questionType: string | null
  ): DecisionSpanInfo | null => {
    // Classify before parsing the answer: noul answers synthesize binary
    // options whose keys depend on the decision type. Approval intent is
    // the more specific signal, so test it before routing: request state
    // often carries the routed model ("route":"gpt-6-sol"), which must not
    // reclassify an approval question.
    const looksLikeApproval = APPROVAL_WORDING.test(questionDefinition)
    const looksLikeRouting = ROUTING_WORDING.test(questionDefinition)
    // `let`: the option-based inference below may upgrade an unclassified
    // question once its answer's options are known.
    let type = looksLikeApproval
      ? "tool_approval"
      : looksLikeRouting
        ? "model_routing"
        : "jev_decision"

    const answer = answers?.[questionKey]
    const answerRecord =
      answer && typeof answer === "object"
        ? (answer as Record<string, unknown>)
        : null
    const probabilitySource =
      answerRecord?.probabilities &&
      typeof answerRecord.probabilities === "object"
        ? (answerRecord.probabilities as Record<string, unknown>)
        : null
    let options: DecisionOption[] = probabilitySource
      ? Object.entries(probabilitySource)
          .filter(
            (entry): entry is [string, number] =>
              typeof asNumber(entry[1]) === "number"
          )
          .map(([key, probability]) => ({
            key,
            probability: asNumber(probability) as number,
          }))
          .sort((a, b) =>
            questionType?.toLowerCase() === "score"
              ? Number(a.key) - Number(b.key)
              : b.probability - a.probability
          )
      : []
    // Score answers key their probabilities by criteria index ("0".."3");
    // legend maps those indexes back to option text, so show the label.
    const legend =
      answerRecord?.legend && typeof answerRecord.legend === "object"
        ? (answerRecord.legend as Record<string, unknown>)
        : null
    if (legend && options.length) {
      options = options.map((option) => {
        const label = legend[option.key]
        return typeof label === "string" ? { ...option, key: label } : option
      })
    }
    let choice: string | null =
      typeof answerRecord?.choice === "string"
        ? answerRecord.choice
        : typeof answerRecord?.answer === "string"
          ? answerRecord.answer
          : (options[0]?.key ?? null)
    let confidence: number | null =
      asNumber(answerRecord?.confidence) ?? options[0]?.probability ?? null

    // Noul is the probability that this question is true, not an approval
    // result or a confidence score. The application's threshold is unknown.
    const noul = asNumber(answerRecord?.noul)
    if (
      questionType?.toLowerCase() === "noul" ||
      (!options.length && noul !== null)
    ) {
      options = []
      choice = null
      confidence = null
      if (noul !== null && noul >= 0 && noul <= 1) {
        options = [
          { key: "yes", probability: noul },
          { key: "no", probability: 1 - noul },
        ]
        choice = String(noul)
      }
    }

    // A Score is a position on an ordered scale, not its most probable
    // level. Keep the reported value and confidence independently.
    if (questionType?.toLowerCase() === "score") {
      const scoreValue = asNumber(answerRecord?.score)
      choice = scoreValue === null ? null : String(scoreValue)
      confidence = asNumber(answerRecord?.confidence)
    }

    // Wording matched neither family: infer from the answer's own options.
    // Two or more distinct model-shaped options read as model routing
    // (shape-matched, no hardcoded list); approval vocabulary among the
    // option keys reads as tool approval. Noul-derived yes/no options match
    // neither and stay generic.
    if (type === "jev_decision" && options.length >= 2) {
      if (
        countModelShapes([choice, ...options.map((option) => option.key)]) >= 2
      )
        type = "model_routing"
      else if (options.some((option) => APPROVAL_OPTIONS.test(option.key)))
        type = "tool_approval"
    }

    // No answer, no synthesized options and no failure = nothing to show. This
    // also blocks doc dumps whose JSON sample merely mentioned "answers".
    if (!choice && !options.length && !failure) return null

    return {
      type,
      provider: "jev",
      model,
      choice,
      applied: choice,
      action: failure ? "error" : null,
      confidence,
      band: confidence === null ? null : decisionBand(confidence),
      options,
      latencyMs:
        asNumber(attrs["agentic.tool.duration_ms"]) ??
        asNumber(attrs["agentic.llm.duration_ms"]) ??
        null,
      raw: JSON.stringify(
        {
          summary: { ...request, questionKey, questionType },
          response: responseSummary,
        },
        null,
        2
      ),
      toolCallId: text(attrs, "gen_ai.tool.call.id"),
      responseIndex: null,
      targetHint: extractTargetHint(questionDefinition),
      derived: true,
      kind: questionType ? questionType.toLowerCase() : null,
      question:
        matched(questionDefinition, QUESTION_INSTRUCTIONS) ?? questionKey,
    }
  }

  // A curl call can ask several questions; enumerate the request-side
  // `questions` block so every answer renders, not just the first one.
  if (answers && !failure) {
    const infos = extractJevQuestions(argumentText)
      .map((ref) => {
        const body = argumentText.slice(ref.start, ref.end)
        // Classify on the question's own wording, cut at its criteria so
        // criteria text (or the next question) cannot reclassify it.
        const criteriaIndex = argumentText.indexOf("criteria", ref.start)
        const definitionEnd =
          criteriaIndex > ref.start && criteriaIndex < ref.end
            ? criteriaIndex
            : Math.min(ref.end, ref.start + QUESTION_SLICE_MAX)
        return buildInfo(
          ref.key,
          argumentText.slice(ref.start, definitionEnd),
          matched(body, JEV_QUESTION_TYPE)
        )
      })
      .filter((info): info is DecisionSpanInfo => info !== null)
    if (infos.length) return infos
  }

  // Fallback key: only adopt the response's single answer key when the
  // request-side extraction failed, so doc dumps with multi-key samples
  // stay blocked by the empty-result guard below.
  const questionKey =
    matched(argumentText, JEV_QUESTION_KEY) ??
    (answers && Object.keys(answers).length === 1
      ? Object.keys(answers)[0]
      : "decision")
  const questionType = matched(argumentText, JEV_QUESTION_TYPE)
  const questionStart = argumentText.indexOf(questionKey)
  const criteriaIndex =
    questionStart < 0 ? -1 : argumentText.indexOf("criteria", questionStart)
  const questionEnd =
    criteriaIndex > questionStart
      ? criteriaIndex
      : questionStart < 0
        ? -1
        : Math.min(questionStart + QUESTION_SLICE_MAX, argumentText.length)
  const questionDefinition =
    questionStart >= 0 && questionEnd > questionStart
      ? argumentText.slice(questionStart, questionEnd)
      : questionKey
  const info = buildInfo(questionKey, questionDefinition, questionType)
  return info ? [info] : []
}

/**
 * Derives judgments from Python or JavaScript SDK calls. One systemOne/
 * system_one invocation carries several questions (Noul/Choice/Score), and
 * the script usually prints only the final values, so there are no
 * probabilities: Choice/Score show their result value, Noul still
 * synthesizes a binary split from its single score.
 * Question definitions must be present in this span's own request; matching
 * keys in a script's output alone do not prove that it called the SDK.
 */
function resolveDerivedSdkSpanInfos(
  attrs: Record<string, unknown>,
  argumentText: string,
  resultRaw: string
): DecisionSpanInfo[] {
  // The unified gate already matched decision traffic; these checks confirm
  // the SDK form (JS camelCase or Python snake_case): a systemOne/
  // system_one call declaring `state`/`questions` keywords or inline typed
  // questions (positional form), on the span's own payload.
  if (!hasSystemOneCall(argumentText)) return []

  const questionSource = argumentText
  const questionMatches = [...questionSource.matchAll(SDK_QUESTION)]
  if (!questionMatches.length) return []
  const seenKeys = new Set<string>()
  const questions = questionMatches
    .map((match, index) => {
      const start = match.index ?? 0
      const nextStart =
        questionMatches[index + 1]?.index ?? questionSource.length
      // The question body runs until the next one; bounded so a malformed
      // payload cannot make this quadratic.
      const body = questionSource.slice(
        start,
        Math.min(nextStart, start + QUESTION_SLICE_MAX * 2)
      )
      const instructions =
        matched(body, /instructions\s*=\s*'([^']*)'/) ??
        matched(body, /instructions\s*=\s*"([^"]*)"/) ??
        // JS positional form: choice("What is this ticket about?", { ... })
        body.match(/\(\s*(?:["'])([^"']{0,300})(?:["'])\s*[,)]/)?.[1] ??
        null
      let criteria: string[] = []
      const dict = body.match(/criteria\s*=\s*\{([^}]*)\}/)
      const list = body.match(/criteria\s*=\s*\[([^\]]*)\]/)
      if (dict) {
        criteria = [...dict[1].matchAll(/["']([\w-]+)["']\s*:/g)].map(
          (c) => c[1]
        )
      } else if (list) {
        criteria = [...list[1].matchAll(/["']([^"']+)["']/g)].map((c) => c[1])
      } else {
        // JS positional form: the prompt string is followed by the choices
        // object ({ billing: null, ... }) or array (["a", "b"]).
        const jsForm = body.match(
          /\(\s*["'][^"']*["']\s*,\s*(?:\{([^}]*)\}|\[([^\]]*)\])/
        )
        if (jsForm) {
          // Quoted keys (`{ "gpt-6-sol": 0.7 }`) are legal object syntax and
          // typical for model names: a bare `[\w-]+` key would choke on the
          // quotes and on dots inside the name ("deepseek-v3.1" splits and
          // its tail reads as a key before the colon).
          criteria = jsForm[1]
            ? [
                ...jsForm[1].matchAll(/["']([\w.-]+)["']\s*:|([\w-]+)\s*:/g),
              ].map((c) => c[1] ?? c[2])
            : [...(jsForm[2] ?? "").matchAll(/["']([^"']+)["']/g)].map(
                (c) => c[1]
              )
        }
      }
      return {
        key: match[1],
        kind: match[2].toLowerCase(),
        instructions,
        criteria,
      }
    })
    // The same script can appear both in the command and in the conversation
    // (paste + heredoc, or echoed across turns) — keep the first definition
    // of each key so one question renders exactly one card.
    .filter((q) => {
      if (seenKeys.has(q.key)) return false
      seenKeys.add(q.key)
      return true
    })

  // Plain "key: value" result lines; require at least one key to overlap with
  // the questions, so doc dumps (import + sample code) never read as a call.
  const results = new Map<string, string>()
  for (const match of resultRaw.matchAll(SDK_RESULT_LINE)) {
    // Inside a dumped script the question-definition line itself reads as a
    // key:value line (`department: choice("...")`, or the single-line
    // Python form whose value reads `Noul(instructions=`; tracebacks echo
    // the same source lines) — its "value" starts with the constructor
    // opening, never a printed answer; treating it as one lets a script
    // prove its own results.
    if (/^(?:noul|choice|score)\s*\(/i.test(match[2])) continue
    if (!results.has(match[1])) results.set(match[1], match[2].trim())
  }
  const overlaps = () =>
    [...results.keys()].some((key) => questions.some((q) => q.key === key))
  if (!overlaps()) {
    // Scripts often print one bare value per question instead
    // (print(result.nouls["k"].noul) ...) — no "key:" prefix at all. When the
    // bare-line count matches the question count and every noul/score line
    // type-checks as a number, align them in definition order; otherwise
    // stay silent rather than guess.
    const bareLines = resultRaw
      .split("\n")
      .map((line) => line.trim())
      .filter(Boolean)
    if (results.size === 0 && bareLines.length === questions.length) {
      for (let i = 0; i < questions.length; i++) {
        // Bare values must be plain tokens — a line carrying JSON structure
        // characters is an unparsed payload, not a printed value. A choice
        // value must further be one of its declared options: anything looser
        // would read single-line help text or doc dumps as a decision.
        if (
          /[{}"'\\]/.test(bareLines[i]) ||
          (questions[i].kind !== "choice" && asNumber(bareLines[i]) === null) ||
          (questions[i].kind === "choice" &&
            (questions[i].criteria.length === 0 ||
              !questions[i].criteria.includes(bareLines[i])))
        ) {
          results.clear()
          break
        }
        results.set(questions[i].key, bareLines[i])
      }
    }
    if (!overlaps()) return []
  }

  const model = matched(argumentText, SDK_MODEL)
  return questions.map((question) => {
    const rawValue = results.get(question.key) ?? null
    let choice: string | null = rawValue
    const confidence: number | null = null
    let options: DecisionOption[] = []
    // Same scenario families as the curl form, applied to the declared
    // choice criteria: wording first, then the options themselves — two or
    // more distinct model-shaped criteria read as routing (shape-matched,
    // no hardcoded list), approval vocabulary reads as approval. noul and
    // score questions keep the generic judgment label.
    let type: string = "judgment"
    if (question.kind === "choice") {
      if (APPROVAL_WORDING.test(question.instructions ?? ""))
        type = "tool_approval"
      else if (ROUTING_WORDING.test(question.instructions ?? ""))
        type = "model_routing"
      else if (countModelShapes(question.criteria) >= 2) type = "model_routing"
      else if (
        question.criteria.some((criterion) => APPROVAL_OPTIONS.test(criterion))
      )
        type = "tool_approval"
    }
    if (question.kind === "noul") {
      const noul = asNumber(rawValue)
      if (noul !== null && noul >= 0 && noul <= 1) {
        options = [
          { key: "yes", probability: noul },
          { key: "no", probability: 1 - noul },
        ]
        choice = String(noul)
      }
    } else if (question.kind === "score") {
      const score = asNumber(rawValue)
      choice = score === null ? null : String(score)
    }
    return {
      type,
      provider: "jev",
      model,
      choice,
      applied: choice,
      action: null,
      confidence,
      band: confidence === null ? null : decisionBand(confidence),
      options,
      latencyMs:
        asNumber(attrs["agentic.tool.duration_ms"]) ??
        asNumber(attrs["agentic.llm.duration_ms"]) ??
        null,
      raw: JSON.stringify(
        {
          summary: {
            mode: "sdk",
            model,
            questionKey: question.key,
            questionType: question.kind.toLowerCase(),
            instructions: question.instructions,
            criteria: question.criteria,
          },
          response: rawValue === null ? {} : { [question.key]: rawValue },
        },
        null,
        2
      ),
      toolCallId: text(attrs, "gen_ai.tool.call.id"),
      responseIndex: null,
      targetHint: null,
      derived: true,
      kind: question.kind,
      question: question.instructions ?? question.key,
    }
  })
}

/** One parsed attribute payload with its question/answer blocks resolved. */
interface QuestionAnswerPayload {
  payload: Record<string, unknown>
  questions: Record<string, unknown> | null
  answers: Record<string, unknown> | null
}

/**
 * The question-answer observation form: exporters that record the decision
 * call at the Runnable/observation layer (LangChain callback exporters and
 * their kin) leave the typed `questions` declarations and the graded
 * `answers` in span attributes — as one combined payload or as a request/
 * response pair — because the call itself is an in-process HTTP request
 * that never becomes a tool span. Recognition rides on the payload shape:
 * every answered question renders, and question keys must overlap answer
 * keys, which keeps declaration-only observations and doc-shaped payloads
 * silent. Attribute names are deliberately not matched, so a new exporter
 * needs no code change.
 */
function resolveQuestionAnswerSpanInfos(
  attrs: Record<string, unknown>
): DecisionSpanInfo[] {
  const payloads = collectQuestionAnswerPayloads(attrs)
  if (!payloads.length) return []

  const infos: DecisionSpanInfo[] = []
  const seen = new Set<string>()
  const push = (info: DecisionSpanInfo) => {
    // Nested chain layers and overlapping payload pairs record the same
    // call on one span too; identical answers collapse so one question
    // renders exactly one card.
    const key = [
      info.type,
      info.kind ?? null,
      info.question ?? null,
      info.choice ?? null,
      info.confidence,
    ].join("\u0000")
    if (seen.has(key)) return
    seen.add(key)
    infos.push(info)
  }
  for (const source of payloads) {
    // Combined form: one payload carries questions and answers together.
    if (source.questions && source.answers) {
      collectQuestionAnswerInfos(
        source.payload,
        typeof source.payload.model === "string" ? source.payload.model : null,
        source.questions,
        source.answers,
        push
      )
      continue
    }
    // Paired form: a request payload and a response payload. Every answer
    // payload overlapping the question keys joins; the model rides on the
    // response side when the exporter records it there.
    if (!source.questions) continue
    for (const response of payloads) {
      const answers = response.answers
      if (!answers) continue
      const overlaps = Object.keys(source.questions).some(
        (key) => key in answers
      )
      if (overlaps)
        collectQuestionAnswerInfos(
          source.payload,
          typeof response.payload.model === "string"
            ? response.payload.model
            : typeof source.payload.model === "string"
              ? source.payload.model
              : null,
          source.questions,
          answers,
          push
        )
    }
  }
  return infos
}

/** Parses every attribute that could carry the question/answer shape. */
function collectQuestionAnswerPayloads(
  attrs: Record<string, unknown>
): QuestionAnswerPayload[] {
  const payloads: QuestionAnswerPayload[] = []
  for (const [key, value] of Object.entries(attrs)) {
    // Tool output may be a file or documentation containing a complete
    // example. Its request/response evidence belongs to the tool parsers.
    if (/^(?:gen_ai|agentic)\.tool\./.test(key)) continue
    if (typeof value !== "string" || !value.includes("{")) continue
    // Cheap prefilter before JSON.parse: the shape always names its blocks.
    // Plain substring, not a key-colon pattern — double-encoded payloads
    // escape the quotes, so "questions": would never match there.
    if (!value.includes("questions") && !value.includes("answers")) continue
    const payload = parseObservationPayload(value)
    if (!payload) continue
    payloads.push({
      payload,
      questions: asRecord(payload.questions),
      answers: asRecord(payload.answers),
    })
  }
  return payloads
}

function collectQuestionAnswerInfos(
  requestPayload: Record<string, unknown>,
  model: string | null,
  questions: Record<string, unknown>,
  answers: Record<string, unknown>,
  push: (info: DecisionSpanInfo) => void
): void {
  // An approval observation carries the governed call in state (LangChain
  // tool-call message); its name mirrors the CLI form's 待执行动作 clause
  // that extractTargetHint reads for the adjacency match.
  const toolCall = asRecord(asRecord(requestPayload.state)?.tool_call)
  const toolCallName = typeof toolCall?.name === "string" ? toolCall.name : null
  for (const [questionKey, questionValue] of Object.entries(questions)) {
    const question = asRecord(questionValue)
    const answer = asRecord(answers[questionKey])
    // One entry per answered question; unanswered declarations stay silent.
    if (!question || !answer) continue
    const info = buildQuestionAnswerInfo(
      questionKey,
      question,
      answer,
      model,
      toolCallName
    )
    if (info) push(info)
  }
}

function buildQuestionAnswerInfo(
  questionKey: string,
  question: Record<string, unknown>,
  answer: Record<string, unknown>,
  model: string | null,
  toolCallName: string | null
): DecisionSpanInfo | null {
  const questionType =
    typeof answer.type === "string"
      ? answer.type
      : typeof question.type === "string"
        ? question.type
        : null
  if (!questionType) return null
  const kind = questionType.toLowerCase()
  const instructions =
    typeof question.instructions === "string" ? question.instructions : null
  // Classify before reading the answer, same precedence as the curl form:
  // approval intent beats routing (routing questions often name models),
  // and a question guarding a state.tool_call is approval by construction
  // — it asks whether that call may run.
  const wording = instructions ?? questionKey
  let type = toolCallName
    ? "tool_approval"
    : APPROVAL_WORDING.test(wording)
      ? "tool_approval"
      : ROUTING_WORDING.test(wording)
        ? "model_routing"
        : "jev_decision"

  const probabilitySource = asRecord(answer.probabilities)
  let options: DecisionOption[] = probabilitySource
    ? Object.entries(probabilitySource)
        .filter(
          (entry): entry is [string, number] =>
            typeof asNumber(entry[1]) === "number"
        )
        .map(([key, probability]) => ({
          key,
          probability: asNumber(probability) as number,
        }))
        .sort((a, b) =>
          kind === "score"
            ? Number(a.key) - Number(b.key)
            : b.probability - a.probability
        )
    : []
  // Score answers key their probabilities by criteria index; the legend
  // maps those indexes back to option text, so show the label.
  const legend = asRecord(answer.legend)
  if (legend && options.length)
    options = options.map((option) => {
      const label = legend[option.key]
      return typeof label === "string" ? { ...option, key: label } : option
    })

  let choice: string | null = null
  let confidence: number | null = null
  if (kind === "noul") {
    // Preserve the question's original yes-probability. Wording alone
    // cannot establish an application's allow/deny threshold or action.
    options = []
    const noul = asNumber(answer.noul)
    if (noul !== null && noul >= 0 && noul <= 1) {
      options = [
        { key: "yes", probability: noul },
        { key: "no", probability: 1 - noul },
      ]
      choice = String(noul)
    }
  } else if (kind === "score") {
    const scoreValue = asNumber(answer.score)
    choice = scoreValue === null ? null : String(scoreValue)
    confidence = asNumber(answer.confidence)
  } else {
    choice =
      typeof answer.choice === "string"
        ? answer.choice
        : typeof answer.answer === "string"
          ? answer.answer
          : (options[0]?.key ?? null)
    confidence = asNumber(answer.confidence) ?? options[0]?.probability ?? null
  }

  // Wording matched neither family: infer from the answer's own options,
  // exactly like the curl form (shape-matched, no hardcoded list).
  if (type === "jev_decision" && options.length >= 2) {
    if (countModelShapes([choice, ...options.map((option) => option.key)]) >= 2)
      type = "model_routing"
    else if (options.some((option) => APPROVAL_OPTIONS.test(option.key)))
      type = "tool_approval"
  }

  // No choice and no synthesized options = nothing to show.
  if (!choice && !options.length) return null

  const criteriaValue = question.criteria
  const criteriaRecord = asRecord(criteriaValue)
  const criteria = criteriaRecord
    ? Object.keys(criteriaRecord)
    : Array.isArray(criteriaValue)
      ? criteriaValue.filter((c): c is string => typeof c === "string")
      : []

  return {
    type,
    provider: "jev",
    model,
    choice,
    applied: choice,
    action: null,
    confidence,
    band: confidence === null ? null : decisionBand(confidence),
    options,
    // The observation attributes carry no duration; the card omits it.
    latencyMs: null,
    raw: JSON.stringify(
      {
        summary: {
          mode: "observation",
          questionKey,
          questionType: kind,
          instructions,
          criteria,
        },
        response: answer,
      },
      null,
      2
    ),
    toolCallId: null,
    responseIndex: null,
    // Only approval hints drive adjacency: a routed answer may be a tier
    // name ("fast") rather than the executed model, and the fallback
    // inference would misread that tier→model mapping as a downgrade.
    targetHint: type === "tool_approval" ? toolCallName : null,
    derived: true,
    kind,
    question: instructions ?? questionKey,
  }
}

/**
 * Parses an observation payload attribute: JSON text (some exporters
 * double-encode it, so one more string layer is accepted).
 */
function parseObservationPayload(
  value: string
): Record<string, unknown> | null {
  if (!value.trim()) return null
  try {
    const parsed: unknown = JSON.parse(value)
    if (typeof parsed === "string") return asRecord(JSON.parse(parsed))
    return asRecord(parsed)
  } catch {
    return null
  }
}

function asRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null
}

/**
 * Extracts a token naming the governed call from the 待执行动作 clause of
 * the question. Tool-call actions ("调用 github MCP 的 create_issue 工具")
 * yield the tool name — it matches gen_ai.tool.name on the executing span
 * and reads as the decision's target even when the call was held and never
 * ran. Otherwise only path-like tokens ("notes/b.md") are specific enough
 * to trust; without one adjacency keeps its unfiltered behavior.
 */
function extractTargetHint(argumentText: string): string | null {
  const idx = argumentText.indexOf("待执行动作")
  if (idx < 0) return null
  const window = argumentText.slice(
    idx,
    Math.min(idx + 160, argumentText.length)
  )
  const tool = window.match(/([\w][\w.-]*)\s*工具/)
  if (tool) return tool[1]
  const path = window.match(/[\w.-]+(?:\/[\w.-]+)+/)
  return path ? path[0] : null
}

/** Tool name plus arguments and result of a tool span, for hint matching. */
function spanCommandText(attrs: Record<string, unknown>): string {
  const name = attrs["gen_ai.tool.name"]
  const args = attrs["gen_ai.tool.call.arguments"]
  const result = attrs["gen_ai.tool.call.result"]
  return (
    (typeof name === "string" ? name : "") +
    (typeof args === "string" ? args : "") +
    (typeof result === "string" ? result : "")
  )
}

/**
 * A hint matches when the span text contains it whole, or every significant
 * slash-separated segment of it — MCP arguments carry owner and repo in
 * separate JSON fields, so "owner/repo" never appears as one substring there.
 */
function matchesTargetHint(commandText: string, hint: string): boolean {
  if (commandText.includes(hint)) return true
  const segments = hint.split("/").filter((s) => s.length >= 4)
  return segments.length > 0 && segments.every((s) => commandText.includes(s))
}

const decisionInfoCache = new WeakMap<
  Record<string, unknown>,
  DecisionSpanInfo[]
>()

/** All decisions derivable from one span; SDK calls carry one entry per question. */
export function resolveDecisionSpanInfos(
  attrs: Record<string, unknown>
): DecisionSpanInfo[] {
  const cached = decisionInfoCache.get(attrs)
  if (cached) return cached
  const infos = computeDecisionSpanInfos(attrs)
  decisionInfoCache.set(attrs, infos)
  return infos
}

export function resolveDecisionSpanInfo(
  attrs: Record<string, unknown>
): DecisionSpanInfo | null {
  return resolveDecisionSpanInfos(attrs)[0] ?? null
}

function computeDecisionSpanInfos(
  attrs: Record<string, unknown>
): DecisionSpanInfo[] {
  // Explicit producer decisions are authoritative over reconstructed payloads.
  const type = text(attrs, "agentic.decision.type")
  if (type) {
    const confidence = num(attrs, "agentic.decision.confidence")
    const choice = text(attrs, "agentic.decision.choice")
    return [
      {
        type,
        provider: text(attrs, "agentic.decision.provider"),
        model: text(attrs, "agentic.decision.model"),
        choice,
        applied: text(attrs, "agentic.decision.applied") ?? choice,
        action: text(attrs, "agentic.decision.action"),
        confidence,
        band: confidence === null ? null : decisionBand(confidence),
        options: parseOptions(text(attrs, "agentic.decision.options")),
        latencyMs: num(attrs, "agentic.decision.latency_ms"),
        raw: text(attrs, "agentic.decision.raw"),
        toolCallId: text(attrs, "gen_ai.tool.call.id"),
        responseIndex: text(attrs, "agentic.response.index"),
        targetHint: null,
      },
    ]
  }

  // Unified gate first, then per-form parsers. SDK calls take precedence:
  // a JSON.stringify dump of an SDK response may even carry an answers
  // object, but the SDK parser owns that shape first and leaves the curl
  // form to the HTTP parser when no question/result overlap.
  const argumentRaw =
    rawText(attrs["agentic.tool.call.arguments"]) ??
    rawText(attrs["gen_ai.tool.call.arguments"]) ??
    rawText(attrs["gen_ai.tool.call.input"])
  const resultRaw =
    rawText(attrs["agentic.tool.call.result"]) ??
    rawText(attrs["gen_ai.tool.call.result"]) ??
    rawText(attrs["agentic.tool.call.output"])
  if (argumentRaw && resultRaw) {
    const argumentText = decodeEmbeddedPayload(argumentRaw)
    if (isDecisionTraffic(argumentText)) {
      const sdk = resolveDerivedSdkSpanInfos(attrs, argumentText, resultRaw)
      if (sdk.length) return sdk
      const derivedHttp = resolveDerivedHttpSpanInfos(
        attrs,
        argumentText,
        resultRaw
      )
      if (derivedHttp.length) return derivedHttp
    }
  }
  // Observation payloads are checked after the tool forms; their parser
  // excludes tool-call attributes that may merely contain a document.
  const questionAnswers = resolveQuestionAnswerSpanInfos(attrs)
  if (questionAnswers.length) return questionAnswers
  return []
}

/** i18n key segment for a decision type; unknown types share a generic label. */
export function decisionTypeKey(type: string): string {
  return KNOWN_TYPES.has(type) ? type : "other"
}

interface DecisionContext {
  info: DecisionSpanInfo
  /** The call that ran on this decision: hard link first, else the adjacent call after it. */
  target: OTelSpan | null
}

/**
 * The LLM call or tool call that ran on this decision's result. Hard links
 * (`gen_ai.tool.call.id`, `agentic.response.index`) win; without one, derived
 * decisions fall back to the adjacent call that follows the decision itself.
 */
function findDecisionTarget(
  info: DecisionSpanInfo,
  spans: OTelSpan[],
  self?: OTelSpan
): OTelSpan | null {
  // SDK judgments govern no call at all — no execution ever follows them.
  if (info.type !== "tool_approval" && info.type !== "model_routing")
    return null
  const isDecision = (span: OTelSpan) =>
    !!(span.attributes && resolveDecisionSpanInfo(span.attributes))
  if (info.type === "tool_approval" && info.toolCallId) {
    const linked = spans.find(
      (span) =>
        !isDecision(span) &&
        String(span.attributes?.["gen_ai.tool.call.id"] ?? "") ===
          info.toolCallId
    )
    if (linked) return linked
  }
  if (info.type === "model_routing" && info.responseIndex !== null) {
    const linked = spans.filter(
      (span) =>
        !isDecision(span) &&
        String(span.attributes?.["gen_ai.span.kind"] ?? "").toUpperCase() ===
          "LLM" &&
        String(span.attributes?.["agentic.response.index"] ?? "") ===
          info.responseIndex
    )
    // Response indexes are local to a conversation/turn. A unique match in
    // another branch still provides no evidence that it used this decision.
    const siblings = self
      ? linked.filter((span) => span.parent_span_id === self.parent_span_id)
      : []
    return siblings.length === 1 ? siblings[0] : null
  }
  return self ? findAdjacentTarget(info, self, spans, isDecision) : null
}

/**
 * Time-adjacent fallback: the first eligible call that starts after the
 * decision itself finished, preferring siblings under the same parent.
 */
function findAdjacentTarget(
  info: DecisionSpanInfo,
  self: OTelSpan,
  spans: OTelSpan[],
  isDecision: (span: OTelSpan) => boolean
): OTelSpan | null {
  const startMs = timestampMs(self.start_time_unix_nano)
  if (startMs === null) return null
  const endMs = timestampMs(self.end_time_unix_nano)
  const gateMs = endMs !== null && endMs >= startMs ? endMs : startMs
  const wantModel = info.type === "model_routing"
  const hint = info.type === "tool_approval" ? info.targetHint : null
  // A yes-probability can describe a hypothetical tool or risk. Without an
  // explicit target, the next tool call does not establish what it governed.
  if (info.kind === "noul" && !hint) return null
  const eligible = (span: OTelSpan): boolean => {
    if (span.span_id === self.span_id || isDecision(span)) return false
    const attrs = span.attributes ?? {}
    if (wantModel) {
      return String(attrs["gen_ai.span.kind"] ?? "").toUpperCase() === "LLM"
    }
    // A tool call by id — or, for Langfuse-exported traces, by the
    // normalized call name (those spans carry gen_ai.tool.call.name
    // without an id).
    if (!attrs["gen_ai.tool.call.id"] && !attrs["gen_ai.tool.call.name"])
      return false
    // With a hint, only calls actually referencing the governed target count;
    // later bookkeeping commands (saving logs) must not read as the governed
    // call executing, or ask_user decisions would be misread as user-approved.
    if (hint) {
      const commandText = spanCommandText(attrs)
      // Saving the decision's own curl mentions the hint too, so exclude any
      // command that talks to (or logs) the decision endpoint itself.
      if (SYSTEM_ONE_RECORDING.test(commandText)) return false
      return matchesTargetHint(commandText, hint)
    }
    return true
  }
  const startOf = (span: OTelSpan): number =>
    timestampMs(span.start_time_unix_nano) ?? Number.POSITIVE_INFINITY
  const startsAfter = (span: OTelSpan): boolean => startOf(span) >= gateMs
  const pick = (pool: OTelSpan[]): OTelSpan | null =>
    pool
      .filter((span) => startsAfter(span) && eligible(span))
      .sort((a, b) => startOf(a) - startOf(b))[0] ?? null
  const sameParent = (span: OTelSpan) =>
    (span.parent_span_id ?? "") === (self.parent_span_id ?? "")
  return pick(spans.filter(sameParent)) ?? pick(spans)
}

/**
 * Fills in what the agent actually did from trace facts: whether the governed
 * call ran after the decision. Native `agentic.decision.action` always wins.
 */
function withInferredAction(
  info: DecisionSpanInfo,
  target: OTelSpan | null
): DecisionSpanInfo {
  if (info.action) return info
  if (info.kind === "noul") return info
  if (info.type === "tool_approval") {
    if (info.choice === "deny") return { ...info, action: "denied" }
    if (info.choice === "allow")
      return target ? { ...info, action: "auto_approved" } : info
    // A call executing does not prove that a user was asked or approved it.
    // Only an explicitly reported action can make that claim.
    return info
  }
  if (info.type === "model_routing" && target) {
    const attrs = target.attributes ?? {}
    const executed =
      text(attrs, "gen_ai.response.model") ??
      text(attrs, "gen_ai.request.model")
    if (!executed || executed === info.choice) return info
    // Choices may be application route keys (fast/powerful), not
    // model IDs. Linking the executed model is useful; declaring a fallback
    // requires evidence that the suggested choice named a different model.
    if (countModelShapes([info.choice]) === 0)
      return { ...info, applied: executed }
    return { ...info, applied: executed, action: "fallback" }
  }
  return info
}

/** One pass over the trace: resolve every decision's governed call and action.
 * SDK spans carry several questions, so one span id maps to a list. */
export function enrichDecisionSpans(
  spans: OTelSpan[]
): Map<string, DecisionContext[]> {
  const contexts = new Map<string, DecisionContext[]>()
  const resolved = new Map<string, DecisionSpanInfo[]>()
  for (const span of spans) {
    if (!span.attributes) continue
    const infos = resolveDecisionSpanInfos(span.attributes)
    if (infos.length) resolved.set(span.span_id, infos)
  }
  // Runnable-layer exporters record one decision call on every span of its
  // enclosing chain; keep only the innermost copy before linking targets.
  dropNestedDuplicateSpans(resolved, spans)
  const spanById = new Map(spans.map((span) => [span.span_id, span]))
  for (const [spanId, infos] of resolved) {
    const self = spanById.get(spanId)
    if (!self) continue
    contexts.set(
      spanId,
      infos.map((info) => {
        const target = findDecisionTarget(info, spans, self)
        return { info: withInferredAction(info, target), target }
      })
    )
  }
  return contexts
}

/**
 * Runnable-layer exporters (LangChain callbacks and their kin) record one
 * decision call on every span of its enclosing chain — sequence, wrapper,
 * classifier — each carrying the same questions and answers. Keep only the
 * innermost span of each fingerprint group: identical decisions whose time
 * range nests inside another's. Sibling runs of the same question (a later
 * turn re-deciding the same route) do not nest, so they all stay.
 */
function dropNestedDuplicateSpans(
  resolved: Map<string, DecisionSpanInfo[]>,
  spans: OTelSpan[]
): void {
  if (resolved.size < 2) return
  const spanById = new Map(spans.map((span) => [span.span_id, span]))
  const isAncestor = (ancestor: string, child: string): boolean => {
    const visited = new Set<string>([child])
    let parent = spanById.get(child)?.parent_span_id
    while (parent && !visited.has(parent)) {
      if (parent === ancestor) return true
      visited.add(parent)
      parent = spanById.get(parent)?.parent_span_id
    }
    return false
  }
  const fingerprint = (infos: DecisionSpanInfo[]) =>
    JSON.stringify(
      infos.map((info) => [
        info.type,
        info.kind ?? null,
        info.question ?? null,
        info.choice ?? null,
        info.confidence,
      ])
    )
  const groups = new Map<string, string[]>()
  for (const [spanId, infos] of resolved) {
    if (infos.some((info) => !info.derived)) continue
    const key = fingerprint(infos)
    const group = groups.get(key)
    if (group) group.push(spanId)
    else groups.set(key, [spanId])
  }
  const rangeOf = (spanId: string) => {
    const span = spanById.get(spanId)
    return {
      start: timestampMs(span?.start_time_unix_nano),
      end: timestampMs(span?.end_time_unix_nano),
    }
  }
  for (const ids of groups.values()) {
    if (ids.length < 2) continue
    for (const outer of ids) {
      if (!resolved.has(outer)) continue
      const outerRange = rangeOf(outer)
      if (outerRange.start === null || outerRange.end === null) continue
      for (const inner of ids) {
        if (inner === outer) continue
        const innerRange = rangeOf(inner)
        if (innerRange.start === null || innerRange.end === null) continue
        if (
          isAncestor(outer, inner) &&
          outerRange.start <= innerRange.start &&
          outerRange.end >= innerRange.end
        ) {
          resolved.delete(outer)
          break
        }
      }
    }
  }
}
