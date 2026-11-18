# Product walkthrough UI evidence

## Design review follow-up

[Registry search, localized example names, rounded highlights, and Trace comparison](design-review/README.md) records an earlier design review and actual-platform acceptance, including light/dark screenshots and independent seekdb readback. It predates the demo Token metrics, the default MCP comparison view, and account-based welcome and skip behavior.

## Real-platform demo

The demo acceptance started with a new Space containing zero Agents and zero model connections. Chromium drove the source frontend against Django, the OTLP gateway, SigNoz, OceanBase seekdb 1.4.0.0, and the AgentLens scheduler and worker. The browser used the actual application APIs throughout.

| Capture | Verified behavior |
| --- | --- |
| [Consent](live-demo/01-consent.png) | Cancelling left the Space empty. Confirming created one independent example Agent. |
| [Dashboard](live-demo/02-dashboard.png) | The dashboard read back 2 Traces and 36 spans after ingestion. |
| [MCP summary](live-demo/03-mcp-summary.png) | The selected Trace showed 2 calls to `storage_policy.retrieve_policy`, with 1 failure. |
| [MCP call](live-demo/04-mcp-call.png) | The enhanced call detail showed the parameter, duration, and failure from the stored span. |
| [Rule result](live-demo/05-rule-result.png) | The user ran the built-in tool execution rule and the scheduler persisted its result. |
| [Failed span](live-demo/06-failed-span.png) | The diagnosis source link selected the same failed MCP span and enabled guide completion. |
| [Replay](live-demo/07-mcp-replay.png) | Help reopened the demo and its MCP guide with the same Agent, Trace, and rule. |

[Evidence manifest](live-demo/evidence.json) binds the screenshots to the tested source, sample hash, and SQL readback. The rule counted 2 successful tools, 1 failure, and 0 unknown outcomes in the retry Trace. Its source span ID matched the browser selection, and the Space still had zero model connections. The sample used local LangChain fake chat and embeddings with an actual MCP tool call, then replayed the captured OTLP into the existing platform storage.

To repeat against an isolated deployment, supply the existing Playwright platform configuration with a private authenticated storage state and set `AIVISION_DEMO_SPACE_ID` to an empty Space. From `ai-vision/vfe`, run `npm run test:e2e:platform -- onboardingDemo.spec.ts`. To regenerate the bundled sample from the repository root, use the framework test Python environment with `mcp==1.28.1` installed.

```sh
python ai-vision/agent-plugins/tests/framework_e2e/generate_onboarding_demo.py \
  --out ai-vision/aivision-py-be/apps/core/fixtures/onboarding_demo.otlp
```

## Original walkthrough evidence

The original captures below used synthetic local API responses to exercise the connection and diagnosis navigation in Chrome.

The clean baseline capture runs `historical-source-revision-redacted`. The initial six after/connection captures run source commit `historical-source-revision-redacted`. The four populated-dashboard captures and the historical fixture checks below run `historical-source-revision-redacted`. See [evidence.json](evidence.json) for hashes and the environment.

| Image | State |
| --- | --- |
| [Before](before-dashboard.png) | Clean baseline, selected application with zero traces, no product walkthrough |
| [Agent access](after-connect.png) | Same zero-trace dashboard, access entry highlighted after the user starts |
| [Verification](after-verification.png) | Selected framework/application; the actual Trace ID input and verification control are highlighted |
| [Trace detail](after-open-diagnosis.png) | Existing detail drawer, Run diagnosis highlighted |
| [Run](after-run-diagnosis.png) | Existing diagnosis dialog, rule selected, user must explicitly click Run |
| [Result](after-result.png) | Complete simulated record readback, result highlighted; the fixture disclaimer is visible |

## Populated-dashboard supplement

A positive Trace count from the actual selected application/time-window metrics is required before dashboard instructions appear. The sequence is scope → metrics → usage → non-empty trend → non-empty recent operations → trace list → diagnosis. Empty or failed optional sections are skipped; a failed metrics request does not imply an empty dataset. The Help menu offers Dashboard walkthrough only when readable data exists. After first-Trace verification, both the existing metrics query and the separate section-adapter response cache are refreshed before returning to the dashboard.

| Image | State |
| --- | --- |
| [Metrics](dashboard-01-metrics.png) | Actual rendered positive-count fixture summary highlighted |
| [Usage](dashboard-02-usage.png) | Reported input/output and model/tool counts; unknown values are preserved |
| [Trend](dashboard-03-trend.png) | Populated token chart highlighted; guide does not synthesize chart points |
| [Recent operations](dashboard-04-operations.png) | Two fixture calls, with status/duration and source-Trace links |

These images use one synthetic Trace (the same model call and tool call as its detail). The two time buckets sum to that fixture's usage. They do not establish real ingestion or model execution.

## Executed checks

On macOS, Node 24.18.0 and Chrome 149.0.7827.156, from `ai-vision/vfe`:

```sh
npm run build
npm run test:onboarding
node --experimental-strip-types --test tests/quickDiagnosis.test.ts tests/quickDiagnosisDom.test.ts tests/i18n.test.ts tests/frameworkDashboard.test.ts tests/dashboardQueryStateDom.test.ts tests/frameworkModuleLoading.test.ts
```

Build (including TypeScript compilation) passed with the existing bundle-size advisory. The two test commands passed 79 and 146 tests, respectively. ESLint with zero warnings and Prettier passed for the changed TypeScript modules. `git diff --check` passed. These are local runs, not hosted CI.

`tests/e2e/productGuide.spec.ts` passed all 22 Playwright cases against the real Vite frontend on localhost, intercepting all `/api/**` calls with `productGuide.fixtures.ts`. Unhandled API endpoints fail closed. A local Playwright configuration selected installed Chrome (`channel: "chrome"`), `baseURL: "http://127.0.0.1:5184"`, viewport 1440x1000, zero retries. For the repository's normal installed-browser environment, the equivalent entry point is:

```sh
npm run test:e2e -- tests/e2e/productGuide.spec.ts
```

Assertions cover actual clicks, empty/error separation, no automatic application/key/diagnosis creation, permission denial, first-Agent form preservation, Chinese/English/Japanese connection flows, language switching without resetting the step, 320/390-pixel layouts, dark mode, Escape/pause/reload/resume, complete verification, and delayed/empty diagnosis records. The connection tests prime zero-count metrics and section caches, verify a newly arriving fixture Trace, then assert that refreshed metrics, populated dashboard panels and the trace list are readable. Added checks replay the dashboard through Help, block the guide after a time-range change returns zero/error, and skip empty/failed optional panels without mislabeling the entire dataset. The screenshot walkthrough performed one explicit simulated diagnosis run, with zero automatic runs, page errors or unexpected API requests.

## Limits and documentation

This is frontend interaction evidence for the SDK/framework-native-Trace path. The fixture market contains one SDK type only; the production market/catalog is unchanged. Real SDK/plugin installation and ingestion, a live rule/model/sandbox execution, the complete Agent catalog, other dashboard profiles, Windows, Safari and Firefox remain unverified. No deployment was changed or load-tested. Existing authentication, authorization and diagnostic execution APIs remain authoritative.

The new in-product prompts are localized in Simplified Chinese, English and Japanese. If accepted, the separate `ob-labs/ai-vision-docs` quickstart should describe Help > Walkthrough and the connection-to-diagnosis flow; that separate documentation update is not part of this PR.
