import type { TFunction } from "i18next"
import type { DiagnoserDTO, DiagnosisRunOption } from "./types"

const coreVariants = new Set([
  "task_completion.text_response",
  "task_completion.executed_artifact",
  "report_fidelity.observed_fact_comparison",
  "answer_grounding.provided_context",
])

function coreVariant(identity: {
  type: string
  mode: string
  version: string
  builtin_key: unknown
  canonical_variant: unknown
}): string | null {
  const { type, mode, version, builtin_key, canonical_variant } = identity
  if (
    type !== "builtin" ||
    mode !== "llm" ||
    version !== "1.0.0" ||
    typeof builtin_key !== "string" ||
    typeof canonical_variant !== "string"
  ) {
    return null
  }
  const key = `${builtin_key}.${canonical_variant}`
  return coreVariants.has(key) ? key : null
}

export function savedCoreVariant(dto: DiagnoserDTO): string | null {
  return coreVariant({
    type: dto.type,
    mode: dto.mode,
    version: dto.version,
    builtin_key: dto.spec.builtin_key,
    canonical_variant: dto.spec.canonical_variant,
  })
}

export function coreDiagnoserLabels(
  variant: string,
  original: { name: string; description: string },
  t: TFunction
) {
  const key = `diagnose.diagnoser.core.variants.${variant}`
  return {
    name: t(`${key}.name`, { defaultValue: original.name }),
    description: t(`${key}.description`, {
      defaultValue: original.description,
    }),
  }
}

// Exact display defaults from Specialist catalog 1.0.0. Renamed values stay user-owned.
const specialistDefaults: Record<
  string,
  { name: string; description: string }
> = {
  "answer_relevance.explicit_current_text": {
    name: "回答相关性",
    description:
      "仅核对显式当前文本的相关性；历史、图片、完成与外部执行未在此项验证。",
  },
  "answer_relevance.recorded_conversation": {
    name: "对话回答相关性",
    description: "仅核对当前请求与实际提供的有来源历史中的相关性。",
  },
  "followup_reasonableness.recorded_conversation": {
    name: "追问合理性",
    description: "只检查基于实际提供的多轮历史，追问是否必要、重复或无关。",
  },
  "numeric_consistency.decimal_binary": {
    name: "数值计算一致性",
    description:
      "只核对明确的有限Decimal二元运算，不验证数值来源或日期、单位换算。",
  },
  "numeric_consistency.decimal_binary_same_unit": {
    name: "同单位数值一致性",
    description: "只核对配置的同一单位加减，不推断或换算单位。",
  },
  "response_language.configured_final_text": {
    name: "回答语言",
    description:
      "只检查配置语言下的最终自然语言正文；允许代码、API、术语、专名及合法引用。",
  },
  "protected_content_exposure.configured_exact_markers": {
    name: "受保护内容透出",
    description:
      "仅检查配置的exact markers，不保证所有秘密或语义改写都已覆盖。",
  },
}

/** Localize known catalog labels only; custom and frozen result names stay intact. */
export function diagnosisRunLabels(item: DiagnosisRunOption, t: TFunction) {
  const core = item.is_specialist ? null : coreVariant(item)
  if (core) return coreDiagnoserLabels(core, item, t)

  const suffix =
    item.version === "3.0.0" ? "V3" : item.version === "v2" ? "" : null
  if (
    item.type === "builtin" &&
    item.mode === "rule" &&
    item.executor &&
    suffix !== null &&
    !item.is_specialist
  ) {
    return {
      name: t(`diagnose.diagnoser.builtinNames${suffix}.${item.executor}`, {
        defaultValue: item.name,
      }),
      description: t(
        `diagnose.diagnoser.builtinDescriptions${suffix}.${item.executor}`,
        { defaultValue: item.description }
      ),
    }
  }
  const specialistKey = `${item.builtin_key}.${item.canonical_variant}`
  const original = specialistDefaults[specialistKey]
  if (
    item.type === "builtin" &&
    item.is_specialist &&
    item.version === "1.0.0" &&
    original
  ) {
    return {
      name:
        item.name === original.name
          ? t(`diagnose.run.specialists.${specialistKey}.name`, {
              defaultValue: item.name,
            })
          : item.name,
      description:
        item.description === original.description
          ? t(`diagnose.run.specialists.${specialistKey}.description`, {
              defaultValue: item.description,
            })
          : item.description,
    }
  }
  return { name: item.name, description: item.description }
}
