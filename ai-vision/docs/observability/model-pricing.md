# Model pricing and cost coverage

AI Vision estimates native text-token costs using reported monetary values,
explicit deployment prices, Space pricing rules, and a models.dev catalog.
These estimates are observability data, not provider invoices.
Model pricing, coverage and converted display amounts are **Beta**.

## Calculation and precedence

For ordinary pay-as-you-go observations, reported costs win, including a reported zero. For an observation without
a reported amount, pricing requires an exact observed provider/model identity.
Estimated-price precedence is:

1. Space manual prices.
2. Explicit `AIVISION_MODEL_PRICES` deployment entries.
3. User-confirmed model aliases in the same billing provider.
4. Exact models.dev provider/model prices.
5. User-selected reference prices.

Rules with the same precedence use the newest applicable explicit effective
timestamp. Ambiguous identical timestamps remain unknown. Future rules do not
replace earlier applicable prices. Fuzzy candidates are suggestions only; they
are never selected automatically.

Rates use decimal USD per million tokens. Canonical input includes cache reads
and writes, and output includes reasoning tokens. Cache reads, cache writes,
and reasoning use a separate rate (including an explicit zero) only when both
the rate and the token count are available. Measured optional tokens are
removed from the base-rate calculation. If a measured bucket has no rate,
the full call cost remains unknown and the priced usage, when present,
contributes to the known subtotal. When a breakdown is not reported, there is no
separate charge or deduction for it; the remaining observed input/output
is still priced. Unreported breakdowns remain unknown in usage, not zero.
Missing required base rates or inclusive counts, overlapping buckets, and
unsupported tariff conditions preserve a null cost with a reason.

The complete total remains unknown if any included billable call has unknown
cost. A separate known subtotal can be displayed for non-overlapping calls.
Reference costs are part of that subtotal and are also identified separately;
do not add them a second time. Nested generation calls with ambiguous ownership
disable additive totals and subtotals, even if every call reports a measurement.

## Catalog and history

The public models.dev catalog is shared across Spaces. Space rules are private
to their Space and do not contain model connection credentials. Members may
view pricing; Space administrators may create/delete rules and synchronize
the shared catalog. A catalog synchronization can affect default estimates in
all Spaces and is labeled accordingly in the UI.
Successful catalog synchronization invalidates derived pricing caches across
Spaces in the same browser. Space rule changes invalidate only their own Space.

Catalog imports validate all entries before publishing a new durable snapshot.
The backend ships a [bundled models.dev snapshot](../../aivision-py-be/apps/core/data/models-dev-NOTICE.txt).
An empty deployment can use it offline. For online updates, Space administrators
can select **Model Pricing → Pricing and currency settings → Public price
catalog → Sync public catalog**. The default tries the public JSON API first,
then the fixed `anomalyco/models.dev` GitHub source archive. Use **Advanced
settings → Sync source → models.dev API** to update from the API alone. If a
sync fails, the previous successful snapshot stays active. Fetches use bounded body
sizes/timeouts and reject redirects. No telemetry, prompts, API keys, or Space
rules are sent to either source.

Repository synchronization parses static `models/**/*.toml` and
`providers/*/models/**/*.toml`, resolving the upstream base-model inheritance
and provider overrides. It never installs dependencies or executes upstream
code. Archive member counts, expanded size and per-file sizes are bounded;
unsafe paths/links and malformed references fail the import atomically.
Upstream-authored model symlinks resolve only to verified model files inside
the repository data tree, with bounded depth and cycle/escape checks; tar
members are never extracted onto the filesystem.
The root repository `models.json` is a separate OpenRouter-shaped snapshot,
not the generated provider pricing API, and is not used for this import.
The adapter follows the v2 source tree: it includes v2-only model files and
omits legacy `v1_alias` provider definitions that have no model files of their
own. Repository and generated v1 API record counts may therefore differ.

Each snapshot records its content version, source and retrieval time. Repository
archives retain the exact Git commit from the archive metadata; an optional
requested commit must match it. Local directories and offline JSON files retain
a content digest and do not expose local filesystem paths in the API. Source
provenance participates in snapshot identity, including when two commits happen
to contain the same model prices. Retrieval time
does not prove when a provider price became effective. Automatic catalog costs
therefore carry `valuationBasis=current_catalog`: they value usage with the
selected catalog snapshot, including usage older than the snapshot. They must
not be described as historical provider charges.

Manual rules require an explicit timezone-aware effective timestamp. Confirmed
alias/reference rules freeze the selected catalog rates and snapshot; later
catalog synchronization does not silently replace those rates. Enter a new rule
to introduce a new effective price version. Deleting a rule removes that rule
from derived calculations and may expose another applicable rule or catalog
price. Original reported telemetry is never rewritten.

The models.dev v1 `api.json` projection contains token prices but can omit
conditions present in upstream provider definitions, such as multiple cache
write TTLs or service-tier/region multipliers. Its prices are standard token
estimates. Explicit unsupported conditions in imported data remain unsupported;
they must not be flattened into a supposedly complete price. Audio/video/image
units and provider bill reconciliation remain outside this phase. Subscription
handling and display currency conversion follow the explicit contracts below.

Repository data retains the richer v2 prices. Unconditional text-token prices,
scalar cache read/write rates and separate reasoning prices are supported.
Non-token units, multiple cache-write TTL rates, context overrides, multipliers
and mode-specific prices remain explicitly unsupported. Normalized eligibility
participates in snapshot identity, so parser improvements cannot accidentally
reuse an older interpretation of the same upstream commit. Importing
a larger directory therefore does not imply that every model is automatically
calculable; manual prices and confirmed references can fill supported gaps.

## Management and API

Open **Integrations → Model pricing**. The default **Cost overview** shows known
costs, separate subscription equivalents and a model worklist for the selected
period. Models needing attention appear first; missing prices, missing evidence
and subscriptions remain distinct. Narrow screens use a vertical worklist so
model identity and its action stay visible together.

Choose a model's action to search and explicitly confirm a price, or configure
manual rates or a subscription. **Price rules** holds existing overrides and
their effective dates. **Prices and exchange rates** holds occasional catalog
and FX maintenance; source/version details are collapsed by default.

The observed inventory uses a bounded complete native telemetry window and
reports why calls cannot be priced. Missing required usage cannot be repaired by
selecting a price. Refreshing a relative period advances its end to the current
time; a custom period keeps both explicit boundaries.

Inventory reuses the native metrics snapshot: canonical per-observation model,
usage, pricing and ancestry facts are read without fetching each Trace's full
messages. Dirty or boundary-crossing Traces retain the snapshot's verified raw
recovery; older data planes can use the existing bounded legacy fallback.
The reader validates compact cost evidence and checks its per-Trace coverage
against the canonical summary. Incomplete, malformed, or inconsistent snapshots
return a structured error.
Inventory returns structured errors for capacity (`3036`) and concurrency
(`3037`) limits, upstream HTTP/connection failures (HTTP 502, `3035`), and
transport timeouts including response-body reads (HTTP 504, `3035`). Invalid
or excessively nested successful upstream JSON returns
`3035`; upstream URLs, credentials and database error bodies are not exposed.

Saving or deleting a price rule also invalidates this Space's cached Trace,
Session and framework cost queries. An already viewed historical Trace can be
re-estimated from its original usage when the rule's effective date covers that
call; no model rerun or telemetry rewrite is needed. Missing required usage
remains unknown for pricing, and reported costs keep their normal precedence.

Trace and Session lists, their details, and framework summaries share a compact
cost row showing the amount and calculation coverage. **Details** opens pricing
sources, exchange-rate date and source, and unresolved reasons without expanding
the surrounding layout. Its default view explains the amount in plain language;
**More details** retains the source counters and complete coverage evidence.
Missing usage or unsupported pricing remains visible in the default explanation.
Partial subtotals and currency warnings remain visible. For a pure subscription
scope with a calculable equivalent, the reference amount is the primary result,
with the actual subscription charge explicitly unknown. Mixed billing keeps
the monetary subtotal and subscription equivalent separate. Token shares use
two decimal places; cached-input help explains its selected rate and inclusion
in input usage without double charging.

Native model-call details receive the server's canonical `usage` and
`costCoverage` alongside the original span. Per-call coverage retains ownership
checks from the complete trace; the browser never calculates prices from raw
attributes. External Langfuse views retain source-provided USD amounts, display
them in the selected currency with exchange-rate evidence, and preserve explicit
zero. Catalog price supplementation applies to native records only.

Native Span filters support an independent monetary lower bound. The condition
stores decimal `amount`, explicit `currency` (USD/CNY/JPY), and `metric` (`cost`
or `listPriceEquivalent`) under optional `minCost`; old saved combinations remain
valid. Only additive, server-projected single-call evidence can match. Unknown
amounts, ambiguous ownership and raw SDK attributes cannot substitute for that
evidence; an explicit zero matches a zero threshold. Ordinary monetary amounts
and subscription references are never selected or added together implicitly.

Comparison converts canonical USD evidence with decimal arithmetic and compares
unrounded values. The saved condition retains its currency when display language
changes; a missing CNY/JPY rate cannot reinterpret it as USD. The filter keeps its
FX warning visible while collapsed. Chain, Graph and Timeline share the matched
IDs and preserve ancestors as context; Graph semantics are projected from the
complete trace before hiding branches. This is a filter within the current native
Trace, not an account-wide budget, alert or an external Langfuse filter.

## Subscriptions and Coding Plans

A monthly Coding Plan fee is not a per-token price. Providers may meter requests,
tokens, rolling windows or shared account quotas. A generic SDK `cost` field may
represent the list-price value of usage rather than an actual payment. AI Vision
does not infer the billing mode from a model name or protocol-compatible provider.

Administrators can create an effective `kind=subscription` rule for an exact
observed provider/model in a Space. This is a broad default for **all matching
calls**, not a connection-specific account setting. It takes precedence over
token-price rules until deactivated. When paid API accounts and subscriptions
share that identity, collectors should supply the explicit per-call attribute
`aivision.billing.mode=subscription|pay_as_you_go`; that evidence overrides the
Space default. Missing or invalid effective-time evidence stays unknown.

The OTLP gateway can recognize the dedicated Baidu Qianfan Token Plan and GLM
Coding Plan endpoints from request attributes on a model span. For shared
endpoints, proxies, and other providers, set the billing mode on the connection
and report it on each model call. The gateway preserves an explicit mode.
Langfuse clients can supply the mode and provider as [model connection
metadata](../../agent-plugins/langfuse/README.md#订阅与按量账户).
Codex clients with a fixed billing connection can declare the mode in their
[launch environment](../../agent-plugins/codex/cdxhooks/config.md#turn-导出与补偿).

Subscription calls preserve token usage but leave canonical `cost.total`,
`input` and `output` null with `reason=subscription_billing`. Existing reported
monetary telemetry is retained under `cost.reportedCost`; even a reported zero
is not silently interpreted as an attributable subscription charge.

AI Vision automatically estimates subscription usage with the model publisher's
base text-token price from the current models.dev snapshot. It uses the resolved
response model when available and matches exact model identities and catalog
aliases. The estimate carries the publisher, model and snapshot in
`cost.listPriceEquivalent` with `pricingSource=official_catalog`.

Publisher rate cards fill specific catalog gaps for subscription comparisons.
DeepSeek confirmed that V4.1 Flash and its 1017 upgrade have the same prices,
so an exact V4.1 Flash identity can use the 1017 publisher tariff. The retired
V4 Flash 0731 uses the last published DeepSeek base rate as a historical
comparison, with the source and original tariff period in `publisherTariff`.
It does not use the current price for the recycled `deepseek-v4-flash` API name.
See the [DeepSeek changelog](https://api-docs.deepseek.com/updates/) and
[archived rate card](https://api-docs.deepseek.com/img/v4_260813_price_en.png).

For the two ERNIE models, the [Baidu China online inference tariff](https://cloud.baidu.com/doc/qianfan/s/wmh4sv6ya)
supplies CNY prices. The Light model uses its flat rate and the Flash Preview
uses its published undiscounted off-peak rate. The saved ECB snapshot converts
these rates to the USD comparison base so they can be added to other estimates.
The amount's evidence retains the original currency, rates, source, and FX
snapshot. Without that FX snapshot the CNY-based estimate stays unknown.
These supplements apply only to subscription estimates, with provenance
`official_publisher` for current cards and `archived_publisher` for retired ones.

The estimate uses published base rates, excluding time, context, service tier,
and tool surcharges. The same optional rate rules apply to cache and reasoning
breakdowns. Missing measurements stay unknown in usage. An
unresolvable model, unsupported base tariff, or missing required usage leaves
the estimate unknown. Legacy
`estimateListPrice` rule inputs and `aivision.billing.estimate_list_price` span
attributes are accepted for compatibility. The estimate is automatic.

`costCoverage.subscriptionCalls` is a subset of unknown monetary calls.
`listPriceEquivalentCalls` counts only fully priced subscription estimates.
Priced usage from an incomplete estimate can still contribute to the
subscription subtotal, and the call stays in the estimate's unknown reasons.
`listPriceEquivalentSubtotal` is separate from both `knownSubtotal` and
`referenceSubtotal`, and must never be added to them. This lets a user assess
the approximate pay-as-you-go scale of a subscription month without claiming
that amount was charged. Ambiguous nested generations still prohibit additive
subtotals. No subscription invoice, monthly fee allocation, savings figure or
remaining provider quota is inferred. Those require an account/period ledger
and billing evidence beyond model and token usage.

Successful estimates retain their source in
`costCoverage.listPriceEquivalentSources`. When required evidence is missing,
`costCoverage.listPriceEquivalentUnknownReasons` counts those failures separately
from `unknownReasons.subscription_billing`. Cost **Details** shows the causes.

### Codex and mixed billing

Codex calls covered by a ChatGPT subscription use the base price of the model
from the official API catalog. The
[official Codex pricing guide](https://learn.chatgpt.com/docs/pricing) distinguishes
included usage, additional credits, and API-key billing. Token usage alone does
not establish which balance paid for a call or turn credits into a USD payment.

An explicit per-call `aivision.billing.mode=pay_as_you_go` overrides a matching
Space subscription rule. That call follows ordinary reported/estimated cost
precedence; subscription calls retain their separate equivalents. Without
per-call billing evidence, AI Vision does not infer that a subscription ran out
or automatically switched to paid API usage.

For pay-as-you-go calls with unsupported catalog tariffs, administrators can
enter supported scalar rates from the [official API price list](https://developers.openai.com/api/docs/pricing)
for a chosen scenario. Cached input is included in total input, and reasoning
is included in output. Each Token contributes to the estimate once.

## Display currencies

Canonical rates and costs remain decimal USD. Chinese, English and Japanese
interfaces default to CNY, USD and JPY respectively, with a user-selectable
display currency. Converted estimates are reference amounts in that currency,
not the provider's settlement currency or an exchange transaction.

Administrators explicitly synchronize the fixed official
[ECB daily reference rates](https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml).
USD cross rates derive from the published EUR quotes using Decimal. Immutable
snapshots retain their rate date, source quotes, content identity and fetch time.
Conversion metadata remains separate from canonical pricing evidence. The UI
shows the rate date and source; missing rates visibly fall back to USD, stale
rates are marked, and a failed refresh retains the last successful snapshot.
Ordinary telemetry reads never fetch external rates. Sub-cent positive amounts
must remain distinguishable from explicit zero.
See [display currency operations](model-pricing-currency.md) for source validation,
refresh behavior and the API contract.

The authenticated API base is
`/api/v1/spaces/{space_id}/model-pricing`. Responses use the standard
`{code, message, data}` envelope; mutating browser requests require CSRF.

| Method and suffix | Behavior |
| --- | --- |
| `GET` base | Catalog status, active Space rules, management capability |
| `GET /catalog` | Search provider/model candidates; reference search must be explicit |
| `POST /sync` | Refresh shared catalog; optional `source=auto\|api\|repository` and repository `revision`; administrator only |
| `POST /rules` | Create an immutable effective rule version; administrator only |
| `DELETE /rules/{uuid}` | Deactivate a rule within this Space; administrator only |
| `GET /inventory` | Model-level native cost evidence for explicit `from`/`to` bounds |
| `GET /exchange-rates` | Current dated public FX snapshot and refresh capability; Space members |
| `POST /exchange-rates/sync` | Explicit bounded ECB refresh, no body options; administrator only |

Native usage responses add `costCoverage` with call counts by pricing source,
unknown reasons, additive known/reference subtotals, and a currency. Existing
`cost.source=reported|estimated` and nullable `cost.total` remain compatible.
When an incomplete call contributes to the known subtotal, `partialCostSources`
records the source of that priced portion. The call remains outside the counts
of fully priced calls.
Consumers must not infer monetary completeness from model-name matches or
reconstruct totals from the spans loaded in a browser. Catalog/rule versions
participate in derived projection fingerprints to prevent reuse of stale prices.

## Upgrade and verification boundaries

Apply backend migrations `0030_model_pricing`, `0031_model_pricing_source`,
`0032_model_pricing_subscription` and `0033_model_exchange_rates`
before serving the pricing endpoints.
Packaged backend startup imports the bundled catalog in a background process
when the migrated database has no active snapshot. The API starts independently.
Existing snapshots, including offline imports, take precedence on subsequent
starts and upgrades. The catalog settings show the source and the content
version. Telemetry queries use the saved snapshot. Retain
`AIVISION_MODEL_PRICES` for existing explicitly configured prices.

From `ai-vision/aivision-py-be/`, synchronize on demand with
`uv run python manage.py sync_model_pricing`. For custom or development server
launchers, run `uv run python manage.py sync_model_pricing --bundled --if-missing` to
initialize the catalog once. To select the repository directly,
use `uv run python manage.py sync_model_pricing --source repository`; add
`--revision <40-character-commit-sha>` for a pinned archive. API-only mode is
`--source api`.

For offline updates, download the public catalog on a connected machine and
transfer `api.json` to the backend host. If the backend runs in a container,
copy or mount the file into the backend container before importing it.

```sh
curl --fail --location https://models.dev/api.json --output api.json
# On the backend host, from ai-vision/aivision-py-be/
uv run python manage.py sync_model_pricing --file /path/to/api.json
```

In the source backend image, run `python manage.py sync_model_pricing --file
/path/to/api.json` from `/app`. In the native image, run the same command using
`./manage` instead of `python manage.py`. The importer validates the file before
activating it as the shared snapshot. Refresh the pricing page to see the new
catalog. A local models.dev checkout can also be imported with
`uv run python manage.py sync_model_pricing --repository /path/to/models.dev`.
These offline inputs are mutually exclusive and cannot be combined with network
source/revision options. Every path validates the complete catalog before
replacing the active snapshot. Offline data has the same historical-valuation
limits; local content hashes do not establish an official upstream revision.

Run `uv run python manage.py sync_model_exchange_rates` to refresh the public FX
snapshot explicitly. It does not refresh model prices or change canonical USD
costs.

This pricing path covers canonical native Trace, Session, and framework metrics.
External Langfuse-backed costs continue to come from that source; the catalog
does not replace its provider-side cost configuration. Inventory failures or
incomplete scans must be visible instead of returning an empty success result.

For source verification, use the focused model-pricing API/semantics tests,
existing native trace/session projection regressions, frontend pricing tests,
and the pricing Playwright flow. Catalog/telemetry fixtures verify application
behavior; they do not establish live models.dev availability or vendor billing
accuracy.
