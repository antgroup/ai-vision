# Display currencies and exchange-rate evidence

Model pricing keeps its canonical amounts and rule rates in USD. Display
conversion does not change telemetry, price rules, monetary completeness,
subscription attribution, or the distinction between a complete total and a
known subtotal. It also does not make a token-price equivalent into a provider
invoice or an allocation of a monthly subscription.

## Display behavior

The default follows the interface language: Simplified Chinese uses CNY,
English uses USD, and Japanese uses JPY. A user can choose USD, CNY, or JPY in
**Integrations → Model pricing**; the browser remembers the choice. Selecting
the interface default resumes language-based selection. This is a display
preference, not an inference about a user's residence or account currency.

Internal signed Trace and Observation links use the same browser preference and
saved reference rates. The link can display converted amounts without a Space
session.

Model pricing, native Trace/Session summaries, and framework summaries use the
same rate snapshot. The selected currency, reference date, and ECB source are
shown with the converted evidence. Rates shown for a model are display values;
manual price entry and confirmation continue to use explicitly labeled USD per
million tokens. Existing non-USD monetary evidence is not converted as USD.

If a usable rate is absent, values remain in USD and show a fallback notice.
Missing amounts remain unknown, and explicit zero remains zero. Conversion
uses decimal strings and integer arithmetic rather than binary floating point.
Localized formatting keeps small positive values visible instead of rounding
them to zero. A reference date older than seven calendar days is labeled stale;
a failed refresh retains the previous saved rates and exposes the error state.

After a successful refresh, the browser invalidates cached exchange rates in
other Spaces and cancels their older requests. Active views refetch immediately;
inactive Spaces refetch when next viewed. Each Space reads its own management
capability; the refresh response is not copied across Space permissions.
Failed refreshes update only the initiating Space's cached status.

## Source and refresh

Rates come directly from the European Central Bank's fixed
[daily XML download](https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml).
The [ECB reference-rate page](https://www.ecb.europa.eu/stats/policy_and_exchange_rates/euro_reference_exchange_rates/html/index.en.html)
describes publication timing and reference-rate limitations. These are
informational rates, not bank settlement rates or tax/accounting guidance.

ECB publishes each currency against EUR. The backend derives USD-to-CNY and
USD-to-JPY cross rates with Decimal arithmetic, rounding cross rates to 24
fractional digits. Each immutable snapshot retains the original published EUR
rates, reference date, derived rates, source URL, and content version. Retrieval
time is recorded separately from the reference date. The backend rejects
malformed, incomplete, duplicate, nonpositive, oversized, or future-dated rate
sets, XML DTD/entity declarations, and non-UTF-8 documents. A late response
cannot replace a newer saved reference date.

No refresh runs at startup or during telemetry queries. An administrator may
explicitly refresh the shared public snapshot from Model pricing; the UI notes
that this affects all Spaces. The backend makes one fixed HTTPS request with
bounded body size and timeouts, no redirects, and no telemetry or credentials.
Refresh responses and mutation status stay bound to the initiating Space when
the user navigates elsewhere, preserving each Space's management capability.
Operators can refresh it using:

```bash
cd ai-vision/aivision-py-be
uv run python manage.py sync_model_exchange_rates
```

## API and upgrade

Apply migration `0033_model_exchange_rates` after the model-pricing migrations.
Authenticated routes use the normal response envelope and Space permissions:

| Route under `/api/v1/spaces/{space_id}/model-pricing` | Access and behavior |
| --- | --- |
| `GET /exchange-rates` | Members read the last saved public snapshot; no network fetch |
| `POST /exchange-rates/sync` | Administrators refresh; accepts only an empty object; browser CSRF applies |

Responses include `baseCurrency=USD`, supported currency codes, decimal-text
`rates`, `snapshotId`, `rateDate`, `fetchedAt`, `sourceName`, `sourceUrl`,
`status=empty|ready|stale|error`, `lastError`, `scope=global`, and `canManage`.
When no snapshot exists, only the USD identity rate is available. Unknown or
cross-Space resources remain hidden through the existing authorization layer.

Successful signed `/api/v1/trace-link/detail` and
`/api/v1/observation-link/detail` responses carry an `exchange_rates` object
with `rates`, `rateDate`, and `status`. A snapshot lookup failure returns
`null` for that field while preserving the authorized detail response.

`test_model_exchange_rates` covers parsing, exact cross rates, failed refresh
retention, stale dates, rollback prevention, fixed fetch bounds, and permissions.
`modelPricingCurrency.test.ts` covers decimal conversion, large amounts, tiny
positive values, language defaults, localized formatting, and USD fallback.
These fixtures do not prove current ECB availability; a real fetch must be
recorded separately.
