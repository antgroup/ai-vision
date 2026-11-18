---
object_key: diagnoser
topic: specialist-material-policy
title: 专项材料与政策
---

# 专项材料与政策

## diagnoser-specialist-material-policy@20261010-0350

**五项可选专项的有限政策、真实材料与裁决范围**

```yaml
record_id: diagnoser/specialist-material-policy@20261010-0350
decision_status: superseded
implementation_status: not_started
proposed_at: 2026-10-10T03:50+08:00
decided_at: 2026-10-10T09:06+08:00
implemented_at: null
supersedes: []
related_records:
  - diagnoser/builtin-definition-source@20261010-0350
  - projection/runtime-facts@20261010-0350
  - executor/llm-history-window@20261010-0350
  - security/private-check-policy@20261010-0350
  - dataset/diagnosis-input-purpose@20261010-0350
  - diagnoser/core-material-policy@20261009-1945
  - task/judge-request-budget@20261009-1945
affected_objects:
  - projection
  - executor
  - llm-judge
  - metric-result
  - task
  - http-api
canonical_doc: docs/diagnoser/README.md
```

### 背景

材料缺失不授权Judge猜语言、单位、保护范围或工具政策；专项必须有有限typed配置、actual source门禁和准确unavailable。

### 决策

全部version1.0.0/default_enabled=false/单metric name=key：

| key / canonical variants | mode / executor | category / score_role |
|---|---|---|
| answer_relevance / explicit_current_text、recorded_conversation | llm / specialist_llm | outcome / quality |
| followup_reasonableness / recorded_conversation | llm / specialist_llm | process / quality |
| numeric_consistency / decimal_binary、decimal_binary_same_unit | rule / specialist_numeric | outcome / quality |
| response_language / configured_final_text | llm / specialist_llm | outcome / quality |
| protected_content_exposure / configured_exact_markers | rule / specialist_protected | safety / signal |

确切字段（canonical Prompt/metrics/role/version/required_evidence不可由browser编辑）：

```ts
type SpecialistPolicy =
 | {kind:'answer_relevance_v1';context_mode:'explicit_current_text'|'recorded_conversation'}
 | {kind:'followup_reasonableness_v1';context_mode:'recorded_conversation'}
 | {kind:'numeric_consistency_v1';checks:Array<{
     id:string;expression:{field:'current_request';prefix:string;suffix:string};
     result:{field:'final_output';prefix:string;suffix:string};
     precision:{mode:'exact'}|{mode:'quantized';scale:number;
       rounding:'ROUND_HALF_EVEN'|'ROUND_HALF_UP'|'ROUND_DOWN'};unit:null|string;
   }>}
 | {kind:'response_language_v1';required_language:'zh-CN'|'zh-TW'|'en'|'ja'|'ko'|'fr'|'de'|'es';
     scope:'final_natural_language';allowed_terms:string[]}
 | {kind:'protected_content_exposure_v1';scope:'final_visible_text';match:'literal_exact';
     markers:Array<{id:string;value:string;visibility:'protected'|'public'}>};
type SpecialistConfiguration = {
 policy:SpecialistPolicy;model_connection_uuid?:string;
 llm_options?:{enable_thinking?:boolean|null;temperature?:number;
   reasoning_effort?:'none'|'minimal'|'low'|'medium'|'high'|'xhigh'|'max'|number;
   max_tokens?:number;request_timeout_sec?:number};
};
```

1. **配置**：所有层strict/extra-forbid，拒bool充数、NaN/Infinity、重复/隐式字符串转数值；kind/context_mode/unit精确key/variant。canonical UTF-8配置JSON<=32KiB/depth<=6。LLM必需同Space AVAILABLE-only UUID [A-Za-z0-9_-]{1,64}；thinking bool|null、temperature finite0..2、reasoning上述enum或非bool整数1..100、max_tokens整数1..8192/缺省4096、timeout finite0<x<=300；Rule禁model/options。preserve_existing_policy为PATCH控制标记，不入持久policy。
2. **来源与共同门禁**：server从Django授权完整Trace读/project后gate，SourceReference={trace_id,span_id|null,field_path,call_id?}指实际唯一row/leaf/alias原字段；path<=512/depth<=12/index<=10000，无wildcard/filter/eval。PolicyReference={kind:'configured_check_policy',definition_digest,policy_digest,policy_field}独立，不能伪span。应用政策当前无accepted mapping，application_policy_mapping_unavailable；开发者配置仅检查要求，不声明历史/工具complete/authoritative。LLM request/final各<=32KiB、history<=40/各<=8KiB、bundle<=96KiB；protected final<=1MiB。完整读后缺/遮蔽/超支持范围zeroJudge SKIP且不截断；分页/查询/解码/投影/内部计算/Judge/协议fault FAILED。未知runtime mapping unavailable。必要image/nontext无mapping→unsupported_required_evidence SKIP，不text-only降级；最新reasoning/tool-only assistant不提升旧assistant为final。
3. **relevance**：explicit_current_text只核sourced current/final显式文本相关性，不评history/image/completion/外部执行；正常拒答可相关，相关但未完成不算跑题。实际明示依赖缺上下文/图片则SKIP。recorded_conversation另需current前同一实际SDK容器至少一非空user/assistant、原index/order/source；history只消解当前明确引用，不替current。缺final不GOOD。引用必含request/final，conversation另含必要history。
4. **followup**：current/final与current前同一真实容器至少一非空user/assistant，不跨span/相似文本拼会话、不以system/tool代替；None/空prefix不证明首轮，missing_required_history SKIP。保留原index/order，不按文本去重/取最后10或40；无额外LLM预classifier。材料充分一轮语义Judge判断必要/重复/无关或GOOD无不合理追问，引request/final/支撑history。只证明该份生成前可见history，非全会话采集完整。
5. **numeric**：checks1..8、唯一id [a-z][a-z0-9_]{0,31}；literal prefix/suffix各0..128，每选项一个实际leaf唯一有效切片<=128codepoints，空边界字段首尾/双空整字段；歧义/缺片/跨source/超界整Diagnoser SKIP。number可选sign、0或非零开头整数<=18digits、可选1..12digits小数，无指数/变量/括号/链/NaN。二元number WS operator WS number，仅+ - * × / ÷，Decimal localcontext precision128有限分派，最多8次，无eval/AST运行。decimal_binary禁unit；same_unit只加减，unit1..16非空白literal、两input逐字同unit，result解析实际unit后比较，明确不同BAD、缺unit SKIP，无“米=m”转换猜测。exact division用有界整数约分，分母仅2/5素因子且expected<=12小数位；非terminating需precision policy，超界SKIP。quantized scale整数0..12且明确HALF_EVEN/HALF_UP/DOWN。div0/不支持子集SKIP，非法policy/internalDecimalfault FAILED。任一check缺材料整项SKIP无Metric，全部完整时任一BAD整体BAD、全一致GOOD；evidence check_id/真实原式actual来源/precision unit PolicyRef/expected actual位置。日期/相对时间/币种与单位换算unsupported；零Judge。
6. **language**：required_language/scope明确配置，allowed_terms0..32唯一/各1..80/总<=2048。仅final自然语言，允许代码/API/identifier/专名/合法引用/terms；不猜用户语言/runtime/多数字符、不用char-ratioRule。sourcedcurrent/final+languagePolicyRef，无policy/final/必要遮蔽/unsupported材料zeroJudgeSKIP，非法locale/spec/source/Judgefault FAILED；一轮语义Judge引request/final/policy，只判断语言范围非技术正确性。
7. **protected**：markers1..64、唯一id同上、唯一value1..256非空白/总<=8192、至少一protected；literal_exact case-sensitive normalize=none，跨visibility双向substring包含拒绝。public/普通工具名不BAD，不猜system/paraphrase。仅sourcedfinal不要求request/“已提供模型”事实，protected命中BAD、完整支持无命中GOOD；无policy/final/必要遮蔽/超界/nontext SKIP，非法policy/source/internalfault FAILED，零Judge，私密传播按security/private-check-policy。
8. **输出与预算**：LLM恰好一canonical metric、good/bad、reason非空<=2048，citations1..32/每quote非空<=512、bundleknownID/实际leaf逐字可定位且满足variant必需覆盖；modelskip/failed、缺覆盖/未知ID/不可定位/超界为invalid_result FAILED，不自动纠正。Rule确定性evidence。Trial一次一专项一Trace最多3 request attempts含retry，无probe/preclassifier；Rule0。advanced正式Task execution_policy=None、judge_request_summary_state=not_enabled，无durable30/3；Core政策不扩。
9. **目录/API**：后四required_tool_execution/subject_consistency/provided_context_use/data_request_fulfilment fixed selectable=false/required_fact_mapping_unavailable，无executor/sync/Trial。五项按真实runtime/material/configuration列selectable/requires_configuration/variants/required_materials/unavailable_reason/result_boundary/digest。Django前缀/api/v1/spaces/{space_id}/diagnose、agent_uuid query，仅specialists/catalog GET、specialists/sync POST、specialists/{uuid}/configuration PATCH、specialists/trial POST；AL对应/api/v1/diagnosers/specialist-catalog、/sync-specialist-builtins、/{uuid}/specialist-configuration、/api/v1/diagnoser/specialist-trial。sync={key,variant,configuration}；PATCH expected_configuration_digest(64lowerhex)+configuration(policy显式replace或preserve_existing_policy)；Trial strict trace_id+[saved diagnoser_uuid XOR draft syncInput]，ID[A-Za-z0-9._:-]{1,128}。unknown422，禁browserPrompt/spans/test_input/SQL/DSN/scope/sources/complete/authoritative。saved锁读资源、draftserver装canonical Spec，完整server读取后gate。Trial无Task/Record/receipt/丢响应自动重发；SPECIALIST_UNAVAILABLE与execution.failed分开，AUTH按security主题。

### 保持不变

专项独立，不扩八Rule/四Core/Quick/Metric枚举；原CUSTOM Trial test_input合同不成为专项可信来源；原公共模型参数合同不收窄。

### 影响与风险

有限SKIP/unavailable要准确显示，source_complete不证明采集完整。LLM准确性需独立人工标签/模型校准，fake绿色不证明；退役定义的历史曲线不能拼入新身份。

### 验证

后续strict/bounds/sourceleaf/alias/citation/unknownhistory/image、逐项独立oracle/precision/unit/partialchecks/privateDTO/language配置、Trial<=3/Rule0/后四无能力与CoreQuick不变。

### 实施说明

未实施。A独立definitions/policy/material/executors/service/routes/schema；C消费安全DTO/四route；不是新增durablepolicy授权。

## diagnoser-specialist-material-policy@20261117-1152

**Specialist configuration, evidence, and seven executable checks**

```yaml
record_id: diagnoser/specialist-material-policy@20261117-1152
decision_status: accepted
implementation_status: implemented
proposed_at: 2026-11-17T11:52+08:00
decided_at: 2026-11-17T11:52+08:00
implemented_at: 2026-11-17T12:14+08:00
supersedes:
  - diagnoser/specialist-material-policy@20261010-0350
related_records:
  - diagnoser/builtin-definition-source@20261010-0350
  - projection/runtime-facts@20261010-0350
  - executor/llm-history-window@20261010-0350
  - security/private-check-policy@20261010-0350
  - dataset/diagnosis-input-purpose@20261010-0350
  - diagnoser/core-material-policy@20261009-1945
  - task/judge-request-budget@20261028-1611
affected_objects:
  - projection
  - executor
  - llm-judge
  - metric-result
  - task
  - http-api
canonical_doc: docs/diagnoser/specialists.md
```

### Background

Each Specialist checks one requirement on an ingested Trace. Structured output and required tool
execution can use deterministic checks for complete final output and observed tool completion.
The Trace dialog carries the selected IDs into the existing Specialist workflow.

### Decision

Each `key + variant` has an independent canonical identity, version, and digest. Specialists start
disabled and emit at most one declared metric. Relevance, follow-up, and language use an LLM. Numeric,
protected content, structured output, and tool checks use Rules. Protected content and tool results
are signals. Other metrics represent quality.

| Key and variant | Policy, evidence, and verdict |
|---|---|
| `answer_relevance / explicit_current_text` or `recorded_conversation` | Requires the sourced current request and final visible text. The conversation variant also requires prior user and assistant messages from the same recorded container, in original order. Use history to resolve explicit references. A reasonable refusal can be relevant. Missing required history or unsupported evidence causes a skip. |
| `followup_reasonableness / recorded_conversation` | Uses the same history requirements. Checks whether follow-up questions in the final answer are necessary, repeated, or unrelated. An answer without unreasonable questions can pass. |
| `numeric_consistency / decimal_binary` or `decimal_binary_same_unit` | Supports 1–8 unique check IDs. Literal prefix and suffix anchors locate a unique fragment within one source leaf, with 128-character limits for anchors and fragments. Supports finite Decimal binary arithmetic. The same-unit variant limits the operations to addition and subtraction with literal units. An explicit unit mismatch is BAD and a missing unit is SKIP. Exact results allow up to 12 decimal places. Quantized results use 0–12 places with HALF_EVEN, HALF_UP, or DOWN. Missing, ambiguous, or unsupported expressions and division by zero skip the check. When all inputs are supported, any mismatch is BAD. |
| `response_language / configured_final_text` | Requires the sourced current request, final text, and a configured locale from zh-CN, zh-TW, en, ja, ko, fr, de, and es. Allows up to 32 terms of 1–80 characters, with a 2,048-character total limit. Checks natural language in the final answer and allows code, identifiers, proper names, and legitimate quotations. |
| `protected_content_exposure / configured_exact_markers` | Matches 1–64 case-sensitive literal markers against sourced final visible text. IDs and values must be unique. Values have a 1–256-character limit and an 8,192-character total limit. At least one marker must be protected. Values may not contain one another across visibility types. A protected match in complete material is BAD and absence of a match is GOOD. |
| `structured_output_contract / json_object_fields` | Checks 1–16 unique required top-level fields and their declared types. Takes a complete sourced final text body or Framework application output explicitly encoded as JSON. The top level must be an object and may have extra fields. Invalid JSON, duplicate keys, missing fields, and type mismatches are BAD. Missing, ambiguous, redacted, or oversized material is SKIP. Evidence reports source, field names, and issue types. |
| `required_tool_execution / observed_tool_completion` | Requires one exact Framework tool identity of up to 128 characters. A native SDK terminal result or an execution span with an actual result, a call ID, and explicit success can prove a completed call. At least one trustworthy matching success is GOOD. A proposal, failures alone, or insufficient evidence is SKIP. Evidence reports source, configuration reference, and outcome without arguments or result contents. Capture completeness and business effects are outside this check. |

The server validates the key, variant, and typed policy. Canonical UTF-8 JSON permits at most 32 KiB
and depth six. Unknown fields, coercions, and nonfinite numbers are rejected. Rule policies forbid
models. LLM policies require a same-Space AVAILABLE model UUID. Thinking and reasoning use the
existing bounded controls. Temperature is 0–2, the token limit is 1–8192 with a default of 4096, and
the timeout is at most 300 seconds. PATCH requires the expected configuration digest and can
replace or explicitly preserve the policy. Protected values are used for authorized writes,
internal freezing, and execution. Readbacks and results follow the private policy, and a new
protected-content record masks only its own Trace copy.

The server reads and projects the full authorized Trace by exact ID. A `SourceReference` resolves
to one observed span and original field. Paths allow up to 512 characters, 12 levels, and indexes
up to 10,000, without wildcards or evaluation. `PolicyReference` identifies configuration digest
and definition. A complete query does not prove complete history or tool capture. Request and final
text are capped at 32 KiB each, with a 1 MiB final-text limit for protected markers. An LLM can consume
up to 40 history messages of 8 KiB each and a 96 KiB evidence bundle. The server skips unsupported,
missing, redacted, or oversized material. Query, decoding, projection, executor, Judge, and protocol
failures surface as FAILED. Each Judge result requires one GOOD or BAD verdict with a nonempty
reason of up to 2,048 characters and 1–32 locatable verbatim citations of up to 512 characters each.
The required citation coverage depends on the policy.

The four existing Specialist platform endpoints serve catalog, sync, configuration, and Trial.
`subject_consistency`, `provided_context_use`, and `data_request_fulfilment` retain unavailable
catalog entries until their material mappings exist. Trial accepts one exact authorized `trace_id`
and either a saved diagnoser or a typed draft. The UI uses the only selected Trace or asks the user
to choose one from the existing selection. Clients cannot supply raw spans, Prompt, SQL, Scope, or
capture-completeness assertions. The server checks draft Trial and save permissions independently.
Trial creates no Job, Task, Record, or receipt. Up to three requests including retries are allowed
for an LLM Trial, and Rules make zero. Trial, save, and persisted manual run are separate actions.

### Unchanged

Advanced Jobs retain `execution_policy=None` and `judge_request_summary_state=not_enabled` for
Specialists. A saved Specialist run from selected Traces retains `bounded_diagnosis_v1` and the 30/3
request budget for applicable LLM calls. Specialists stay outside default Jobs, Common and Coding
defaults, Core, and Quick scene templates. CUSTOM Trial and shared model parameters retain their
existing contracts.

### Impact and risks

Existing canonical identities and configuration summaries remain readable. The new checks require
AgentLens instances with their executors. Remove their Job bindings before rolling back to an older
worker. The UI exposes missing tool evidence as a skip.

### Validation

Cover strict policy inputs, complete and invalid JSON, actual tool completion and counterexamples,
permission and selection boundaries, zero Judge requests for Rules, and both draft and saved paths.
Judge quality still requires validation with a live provider and human-labeled examples.

### Implementation notes

The Trace dialog and Specialist management share the form. The runtime uses the existing material
projection, executor registration, Scope authorization, and persisted saved-diagnoser path.
