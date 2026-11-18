# Model connection presets and discovery

The first-level **Integrations → Model connections** page and the diagnoser's inline model manager share the same connection form. Connections belong to the current Space; each feature selects its own connection and keeps its own prompts and runtime parameters. A provider preset supplies its Base URL, API key management link, and
optional static model suggestions. The stored connection remains
`name / endpoint / model_name / api_key`; selecting a preset does not add a
provider-specific persistence format or require a migration.

## Catalog ownership

`vfe/src/features/model-connections/providers.ts` owns the frontend catalog.
Provider names, region labels, instructions, and errors are translated in
`zh-CN`, `en-US`, and `ja-JP`. The locale changes the provider list order:

| Locale | First choices |
| --- | --- |
| Simplified Chinese | DashScope, DeepSeek, GLM, SiliconFlow |
| English | OpenAI, Anthropic, Gemini, OpenRouter, NVIDIA |
| Japanese | OpenAI, Anthropic, Gemini, OpenRouter, NVIDIA |

These are editorial defaults, not market-share rankings or geolocation.
All locales expose the same providers and keep custom OpenAI-compatible
services last. When choosing a provider for a new connection, Chinese defaults
to its China region; English and Japanese default to its international region
where available. Changing the language must not change an already selected
region, endpoint, model, or credential.

DashScope (Alibaba Cloud Model Studio) has explicit China and Singapore presets. GLM has China and international
presets. API keys may be region-specific; GLM presets target the standard API,
not Coding Plan endpoints. A custom endpoint is editable and existing custom
connections remain editable. Provider inference uses exact normalized Base URL
matches; it never guesses from hostname substrings. DashScope is the provider
service; Qwen is one of its model families. The preset's static suggestions are
examples, not a restriction to Qwen models. Discovered or manually entered model
IDs remain available across the provider's supported catalog.

## Model discovery

The browser calls
`POST /api/v1/spaces/{spaceId}/diagnose/model-connections/discover`
with `endpoint` and either a newly entered `api_key` or a
`connection_uuid` for the existing connection. Django checks space access and
model-management permission, injects the authorized space, then forwards to
AgentLens. AgentLens owns the stored credential and provider HTTP requests.

Credentials are not URL parameters, browser storage, cache keys, or read
responses. Discovery does not save the connection or change its test status.
The response is the platform envelope containing
`{items: [{id: "..."}], total: number}`. Stable failures are exposed as
`data.discovery_error` and translated by the client; upstream error bodies
must not be displayed or logged.

Only the exact supported preset URLs can use this new discovery endpoint.
Custom URLs keep manual model entry. The server enforces the target list,
disables redirects, and limits time, response size, and result count. DashScope
uses its native paginated catalog with text-generation and inference filters.
Anthropic discovery uses its native `/v1/models` catalog with version and API
key headers and cursor pagination. Other supported providers use their
OpenAI-compatible model catalogs.
Where a catalog declares model capabilities, discovery excludes explicit
non-chat entries. A catalog that omits capabilities cannot prove that every
returned model supports the diagnostic request.

The form allows both searchable selection and manual model IDs. Static
suggestions are visibly distinct from fetched results and are not evidence of
account access or a successful diagnosis. A successful list request also does
not validate inference permissions, model parameters, or available balance.
Run model validation, then trial the intended diagnoser with representative
inputs. A successful lightweight connection probe does not establish that a
model can return the required diagnostic JSON and evidence citations. Reasoning
models may also need a suitable output-token budget before they produce a final
answer. Invalid or incomplete diagnostic output must remain a failed result;
do not treat it as a passing diagnosis or weaken evidence validation.

Connection tests return an optional `test_error` code for authentication,
permissions, missing models, rate limits, exhausted quota, timeout, connection,
request compatibility, or service availability failures. The shared management
panel in Integrations and the diagnoser flow translates these codes into actionable
Chinese, English, and Japanese messages. It shows the most recent failure beside
the connection until a successful test or saved edit clears it. This detail is
temporary UI state, not persisted connection history. Unknown codes and older
backends fall back to a generic localized message; raw provider responses are
never displayed as test errors.

While a connection test is pending, that row's test, edit, and delete actions
are disabled. Different connections can still be tested concurrently. This
prevents a pending response from describing a configuration edited in the same
view while the test was in progress.

The Django proxy also returns safe `test_error` codes for upstream timeouts
(`timeout`, HTTP 504), connection failures (`connection_failed`, HTTP 502), and
invalid or rejected AgentLens responses (`unavailable`, HTTP 502). An upstream
authentication failure must not become a platform login failure. Space access
and management permissions are still checked before the upstream request.

When editing, an empty key retains the saved key only if the endpoint is
unchanged (ignoring surrounding whitespace and trailing slashes). Moving to a
different endpoint requires a new key, both for discovery and update. The
form clears typed keys when switching providers or regions and ignores stale
discovery responses after the endpoint, key, provider, region, or mounted
form changes.

See [AgentLens API](../../../agentlens/docs/api.md) for the backend discovery
contract and transport bounds.

## Basic diagnostic validation

The model manager's **Validate model** action runs two server-owned synthetic
text samples: one that follows a short instruction and one that contradicts it.
It uses the canonical `task_completion/text_response` prompt, projection,
executor, JSON parser and evidence-citation validation used by Core diagnostics.
Each sample must produce the expected verdict and valid request/final-output
citations. A model that always returns GOOD cannot pass both cases.

The browser calls
`POST /api/v1/spaces/{spaceId}/diagnose/model-connection/{uuid}/validate`.
Django enforces model-management permission and space membership, then injects
the authorized space. The backend uses the saved connection key. Client-supplied
prompts, traces, credentials and arbitrary executor policies are not accepted.
The legacy `/test` endpoint remains a lightweight connection-only probe.

Validation makes at most two real model requests, one per sample, without
automatic retries or an additional ping. The default test profile uses
4096 output tokens, provider-default thinking, temperature 0, no explicit
reasoning effort and a 45-second request timeout. Users may adjust the supported
generation options within bounded ranges. The exact official Anthropic transport
exception is reflected in the effective profile rather than claiming ignored
controls were applied. Timeouts limit network phases; they are not a guaranteed
end-to-end wall-clock deadline. A deadline check also prevents starting the
second sample after the validation budget is exhausted.

The result keeps connection acceptance separate from basic diagnostic output:

| Basic validation state | Meaning |
| --- | --- |
| Passed | Both samples produced the expected verdict and valid diagnostic output |
| Failed | A completed response had an incorrect verdict or invalid diagnostic output/citations |
| Incomplete | Authentication, permissions, network, quota, timeout, output budget or another execution limit prevented a complete assessment |

HTTP acceptance can establish a working connection even when its diagnostic
output fails validation. Safe reason codes drive actionable Chinese, English
and Japanese messages; raw provider error text is not displayed. Reported input,
output and total tokens remain nullable when any request omits the relevant
measurement. A missing measurement is never displayed as zero.

The sanitized result is persisted with its validation time, canonical-definition
digest and effective generation options. Settings and model selectors show that
this is a basic text check under the recorded test configuration. It does not
mark a different diagnoser prompt or configuration as validated. In particular,
the existing **Run diagnosis** action still tests the selected real trace against
the current diagnoser and owns that form's validated state. Neither action
creates a diagnostic Task or Record as a side effect.

Changing the connection configuration invalidates the saved report. A revision
check rejects results from a validation that started before a concurrent edit;
refreshing or merely reading a report never issues another provider request.
Outdated canonical-definition reports cannot establish current validation.

This revision adds a nullable validation report and an internal configuration
revision to the AgentLens connection table. Apply migration `011` before running
the new backend, and upgrade all AgentLens writers before enabling validation.
Older binaries do not invalidate the new report or advance its configuration
revision, so mixed-version writers do not provide the same edit protection.
Existing connections start without a validation
report; their credentials and chosen models remain unchanged. A passing report
is bounded evidence for its samples and configuration, not a model-quality
benchmark or a guarantee for arbitrary prompts, tools, contexts or future router
backends.

## Source references and maintenance

Provider documentation was checked on 2026-10-20 and 2026-10-21. Static
suggestions are snapshots, so live discovery and manual entry remain available
as providers revise their catalogs.

| Provider | Official documentation |
| --- | --- |
| OpenAI | [List models](https://developers.openai.com/api/reference/resources/models/methods/list) |
| Anthropic | [OpenAI compatibility](https://platform.claude.com/docs/en/cli-sdks-libraries/libraries/openai-sdk), [API keys](https://platform.claude.com/docs/en/api/overview) |
| NVIDIA | [Hosted API catalog](https://docs.nvidia.com/nim-operator/latest/guardrail.html), [Build API keys](https://docs.nvidia.com/nemo/microservices/26.3.1/troubleshooting/guardrails.html) |
| Gemini | [OpenAI compatibility, including model discovery](https://ai.google.dev/gemini-api/docs/openai) |
| DashScope | [Base URLs](https://www.alibabacloud.com/help/en/model-studio/base-url), [list models](https://www.alibabacloud.com/help/en/model-studio/list-models), [model catalog](https://www.alibabacloud.com/help/en/model-studio/models), [regions and credentials](https://www.alibabacloud.com/help/en/model-studio/regions) |
| DeepSeek | [API entry point](https://api-docs.deepseek.com/), [list models](https://api-docs.deepseek.com/api/list-models/) |
| GLM | [China quick start](https://docs.bigmodel.cn/cn/guide/start/quick-start), [international quick start](https://docs.z.ai/guides/overview/quick-start), [API index](https://docs.z.ai/llms.txt) |
| SiliconFlow | [Model discovery and chat filter](https://api-docs.siliconflow.cn/docs/api/models-get), [API key and Base URL](https://docs.siliconflow.cn/docs/usercases/use-siliconcloud-in-dify) |
| OpenRouter | [Model catalog and modality filter](https://openrouter.ai/docs/api/api-reference/models/get-models), [quick start](https://openrouter.ai/docs/quickstart), [free route](https://openrouter.ai/docs/guides/routing/routers/free-router) |
| Groq | [Model catalog](https://console.groq.com/docs/models), [quick start](https://console.groq.com/docs/quickstart) |
| Mistral | [Model catalog and capabilities](https://docs.mistral.ai/api/endpoint/models), [API keys and first request](https://docs.mistral.ai/getting-started/quickstarts/developer/first-api-request) |

The DashScope China shared-domain native catalog is also documented by the
[official SDK endpoint defaults](https://github.com/dashscope/dashscope-sdk-python/blob/main/_autodocs/endpoints.md)
and [Models API](https://github.com/dashscope/dashscope-sdk-python/blob/main/_autodocs/api-reference/models.md).
GLM uses static suggestions in this release because the reviewed official API
index did not establish a model-list contract; this is not a claim that no such
interface exists.

Anthropic uses its official OpenAI-compatible endpoint at
`https://api.anthropic.com/v1`. The form explains that it uses Claude's default
sampling and reasoning settings. Keys that access multiple workspaces are not
yet supported because the connection form does not accept a workspace header.
This behavior applies only to the exact official endpoint.

NVIDIA uses the hosted `https://integrate.api.nvidia.com/v1` endpoint and NVIDIA
Build API keys, rather than NGC image-download credentials. Both Anthropic and
NVIDIA rely on discovery or manual entry instead of static model snapshots.
OpenRouter includes `openrouter/free` as a static route suggestion; account
access and successful inference still require a connection test.

Kimi presets remain outside this revision pending diagnostic runtime
verification. Existing custom OpenAI-compatible connections remain available.

When adding a provider, update the frontend preset, localized display names,
backend discovery targets/parser (if supported), and relevant contract and
interaction tests together. Do not add a Base URL, API key link, or static
model ID based only on remembered examples.

## Validation

From `ai-vision/vfe`, run `npm run test:model-connections`,
`npm run test:i18n`, and `npm run typecheck`. The model-connection tests cover
locale selection, endpoint matching, credential transport, saved-key behavior,
form interactions, stale responses, and management permissions.

Backend validation includes the Django proxy permission/space tests and
AgentLens discovery transport/credential tests. HTTP fixtures prove the
parsing and error contracts; they do not establish live provider availability.
Report live provider checks separately with the tested service chain, provider,
scenario, observed result, and unverified scope. Distinguish model discovery
from inference, and backend checks from browser-to-provider checks. Only claim
an end-to-end scenario passed when the complete service chain was exercised.
Changes to the user tutorial belong in the separate `ai-vision-docs` repository;
this document records the implementation contract and its evidence limits.


## Shared integration entry and compatibility

Use `/model-connections?spaceId=<space-id>` for the Space inventory and
`/model-connections/<connection-uuid>?spaceId=<space-id>` for overview, current diagnostic
validation and references. Model pages show Space scope without Agent or time-range filters.
The legacy `/settings?tab=models&spaceId=<space-id>` uses replace navigation to the same inventory;
other settings tabs keep their meaning. Existing connection UUIDs, stored keys and diagnoser
references are reused without resource migration.

Inline management remains available while configuring a diagnoser. Creating a connection returns
and selects it without discarding unsaved prompts, parameters or Trace material. Saving a connection
does not save the diagnoser or establish that its custom requirements pass. A connection can be
connected while basic diagnostic validation fails; other features must validate their own use.

All members with model-read permission may view connections; model-management permission controls
creation, edits and model calls. Referenced deletion remains server protected. A failed reference
request does not mean the connection has zero users. Endpoint/model/key changes clear the current
validation; affected configuration references are shown before saving a shared change. No universal
default model, observation inference feature, price guarantee or validation-history ledger is added.
