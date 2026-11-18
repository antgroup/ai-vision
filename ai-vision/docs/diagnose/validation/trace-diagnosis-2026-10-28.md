# Selected-trace diagnosis: local validation

Date: 2026-10-28. Baseline: `historical-source-revision-redacted`. This report covers the working-tree implementation before commit. The [sanitized receipt](trace-diagnosis-2026-10-28.json) records the tested backend source hashes, HTTP observations, task identifiers, and separate historical regression receipts. It excludes accounts, credentials, private specialist policy, application prompts, and raw private responses.

The final backend, frontend, and manual browser checks are complete with the limits below. Earlier Quick Check product screenshots are excluded from this report.

The subsequent [selection action-bar validation](trace-selection-actions-2026-10-28.md) records the revised table and bottom actions. Screenshots in this original report describe the earlier validated layout at commit `historical-source-revision-redacted`.

## Scope and environment

The [approved design](../../../../docs/superpowers/plans/2026-10-28-trace-diagnosis.md) has one action: **Run diagnosis**. The trace list supplies 1–10 exact checked IDs; trace detail supplies its single current ID. The dialog selects one saved diagnoser, including an existing configured specialist. The saved definition owns its rules, model connection, and specialist configuration. The dialog has no second trace picker, scenario preset, independent rule pack, or model override.

The checks used prepared macOS services running local Vite, Django, and AgentLens source. Django used an isolated SQLite database. AgentLens used a dedicated OceanBase schema with normal lifespan, scheduler, and workers. Source telemetry reads used read-only SQL sessions. Diagnostic outputs and safe synthetic inputs passed through the existing real OTLP collector and SigNoz OceanBase storage. The real HTTP execution used no collector, database, scheduler, or rule-execution mock.

The application input was a normalized native-source replay of an authorized export, rather than an original OTLP wire replay. Its 50 traces contain 1,062 spans. The selected large inputs contain 85 and 66 spans. Separate synthetic traces provide deterministic safe success/error and historical cases. External model requests were **zero**.

## Saved-definition HTTP execution

The final saved-definition receipt contains seven completed HTTP-driven tasks. These are separate from the manual browser task and earlier multi-diagnoser regression runs.

| Run | Traces / saved diagnosers | Persisted verdicts | Source complete | Detail read-back |
| --- | --- | --- | --- | --- |
| Saved built-in rule | 2 / 1 | 1 good, 1 bad | Yes | Both HTTP 200 |
| Saved numeric check | 2 / 1 | 1 good, 1 bad | Yes | Both HTTP 200 |
| Configured protected-content specialist | 1 / 1 | 1 good | Yes | HTTP 200 |
| Historical saved rule | 1 / 1 | 1 bad | Yes | HTTP 200 |
| Saved large-input latency rule | 2 / 1 | 1 failed, 1 good | **No** | Both HTTP 200 |
| Fresh detail after timestamp fix | 2 / 1 | 1 good, 1 bad | Yes | Both HTTP 200 |
| First detail in the record-write second | 1 / 1 | 1 good | Yes | First request HTTP 200 |

All seven tasks reached `succeeded`. That state means the task finished; it does not turn a `bad` or `failed` record into a pass. The large-input task retains `source.projection_failed=1` and `source_complete=false`.

The options response listed 11 saved definitions. The numeric check and configured protected-content specialist were selectable. A definition without its required model was unavailable with `QUICK_MODEL_UNAVAILABLE`; submitting it returned HTTP 422. The environment had no configured external model, so real LLM execution and provider availability were not tested. LLM snapshot, prompt, budget, and ledger-hook coverage uses offline/mock tests only.

The receipt currently records **61 HTTP case classes**. A class can contain repeated observations, so this is not the number of requests or tasks.

| Boundary | Observed result |
| --- | --- |
| Unknown, same-Space cross-Agent, or cross-Space saved diagnoser | HTTP 422 |
| Missing diagnoser, extra inline configuration, or a diagnoser array | HTTP 422 |
| Eleven IDs, duplicate IDs, missing trace, or foreign-scope trace | HTTP 422 |
| Agent under a different Space URL | HTTP 404 |
| Developer/member accesses another owner's options, run submission, task, or record | Eight HTTP 403 responses, business code 4030 |
| Same request ID, same input | Original task returned, HTTP 200 |
| Same request ID with changed trace selection or diagnoser | HTTP 409 |
| Accepted retry after the saved numeric configuration changed | Original accepted task returned, HTTP 200 |
| Delete a referenced saved definition | Existing deletion protection returned HTTP 409 |
| Retry after that rejected deletion | Original task returned, HTTP 200 |

The deletion check did not bypass the database protection and therefore does not establish a real deletion-after-acceptance case. Offline regressions cover accepted receipt replay without rereading a deleted definition. New invocations snapshot the current saved definition; accepted retries retain their original task. Current authorization is still required. The eight fresh ownership denials exercise the new saved-run endpoints and results. They do not establish a blanket denial for those roles on their own Agents or a permission-revocation scenario.

Private protected-content markers were absent from the options, public run/task/record responses, and the five inspected Django receipt rows. Complete specialist policy remains in the authorized AgentLens task snapshot. The public receipt contains neither that marker nor the policy.

## Large-input persistence regression

A supported projector could fail and then copy the original raw spans into its failure snapshot. The 85-span input has ambiguous framework roots. Its failed records exceeded the downstream OTLP body limit. The fix keeps a compact failed snapshot with the source span count and projection error.

The rows below describe **three distinct executions**. The earlier before/after runs used two diagnosers; the final saved-definition run used one. Their byte and record counts must not be treated as identical workloads.

| Execution | Downstream body | Downstream response | Persisted records |
| --- | ---: | --- | --- |
| Earlier two-diagnoser run before compaction | 35,054,814 bytes | HTTP 400, `read otlp body: http: request body too large` | None read back |
| Earlier two-diagnoser run after compaction | 22,162 bytes | HTTP 200 | 3: failed, failed, good |
| Final single saved-diagnoser run | 19,572 bytes | HTTP 200 | 2: failed, good |

The final run had no gateway export error in its measured window. Its two record details returned HTTP 200 with 2,142- and 15,662-byte responses. The earlier three-record task also remained readable. Failed snapshots retain `source_span_count=85` and `spans_omitted=true`, with no raw spans. Successful semantic snapshots retain their existing contract.

The source remains semantically ambiguous. Compaction makes the failed result persistently visible; it does not repair source topology. The guarantee is limited to supported-projector failures. It does not cover arbitrary large successful outputs, successful CUSTOM raw snapshots, or unknown runtimes.

All 1,062 original source rows were hashed in stable trace/span order before and after execution. Their SHA-256 remained `2df002062c1d933c655d7244680b1ddc2b8ad0823f4d6be309490c9f220936e3`. Task-owned output IDs are separate from original source IDs.

## Historical and immediate record reads

Fixed-task result pages must read their retained batch even when its source timestamp lies outside the global dashboard window. A nonempty `task_uuid` with neither time endpoint no longer injects the ordinary seven-day default. Explicit endpoints remain filters, including one-sided bounds. Ordinary queries keep the seven-day default. Retention and the existing 50,000-physical-candidate cap still apply.

A safe trace dated 190 hours before its ingestion passed through the real collector. The final saved rule produced one bad record:

| Query | Records |
| --- | ---: |
| Fixed task, no time endpoints | 1 |
| Fixed task, explicit recent window | 0 |
| Fixed task, explicit source-time window | 1 |
| Fixed task, start-only after source / end-only before source | 0 / 0 |
| Ordinary query, default seven days / explicit source-time window | 0 / 1 |

The receipt also preserves an earlier ten-case historical regression at its own source hashes. That separate two-rule task returned two records, and direct AgentLens fixed-task/default-list checks returned 2/0. Those historical counts are not final saved-rule counts.

A separate fresh-detail regression exposed an upper bound rounded to whole seconds. A newly written record could fall after that bound. The initial platform detail returned HTTP 503; an initial direct AgentLens status was not captured. After preserving microseconds in the detail-query upper bound, a new task's first detail request returned HTTP 200 in the same second as the stored record. The safe receipt records the request timestamp and stored nanosecond period. This evidence does not establish the cause of the separate browser authentication issue below.

## Browser evidence

The Codex built-in browser used the real local source services at `http://localhost:5175`. Separate Chinese, English, and Japanese saved numeric-check runs each selected two traces and persisted one good and one bad record, with `source_complete=true` and zero model requests. Their task/job identifiers are recorded in the receipt. They are additional runs, rather than part of the seven HTTP-driven tasks above.

Confirmed interactions:

- Opening the dialog requested saved-run options. Submission used `/diagnose/runs` with only `request_id`, `trace_ids`, and `diagnoser_uuid`. It performed no duplicate candidate query.
- The list supplied its two checked IDs. The current detail supplied exactly its one current ID, independently of the list selection. That final detail-selection check was not submitted again.
- A numeric record without a span ID offered **View trace**, opening the exact source trace with `trace_from` and `trace_to` timestamp bounds.
- Official saved-definition names and descriptions rendered in English and Japanese. Frozen record names and reasons retain their stored Chinese text. The Japanese quality page read back the real 50.0 score and had no duplicate picker.
- Desktop checks used 1440 × 1000. At a settled 390 × 844 viewport, the Japanese dialog measured 358 pixels wide, positioned 16 pixels from the left. Its client and scroll widths were both 343 pixels, with no horizontal overflow. All Japanese controls remained visible, and vertical scrolling reached both results. This is a built-in browser responsive check, not a separate mobile-device acceptance test.

At `127.0.0.1`, `auth/me` and refresh intermittently returned HTTP 401 while server sessions remained active and unrevoked. The cause is unknown. The final browser evidence uses `localhost` against the same Vite/backend services to isolate cookie-host state. No further 401 was observed in that final pass. No authentication root-cause fix is claimed.

The historical saved-rule task was opened in Chinese, English, and Japanese while the global window remained **Last 3 hours**. All three pages displayed its one bad record from October 20 and a localized notice that the fixed batch ignores the page window. Chinese and English history screenshots use dark theme; Japanese uses light theme.

Each locale was inspected in both light and dark themes across the surfaces listed below. The three numeric executions were separate locale runs; this is not a claim that all six locale/theme combinations independently executed a task. Unavailable-model API rejection and offline UI regressions are covered separately; an unavailable-definition browser state was not separately exercised end to end.

Fifteen final screenshots contain only safe synthetic content. The Space/Agent fixture names retain “Quick check regression”; they are synthetic resource names, not another product action. Thirteen measure 1440 × 1000; the two narrow images measure 390 × 844. Their SHA-256 hashes and exact dimensions are in the sanitized receipt.

| Surface | Locale / theme | Screenshot |
| --- | --- | --- |
| Trace list with two checked IDs | Chinese / light | [Trace list](assets/trace-diagnosis/list-zh-light.jpg) |
| Saved-diagnoser dialog | Chinese / light | [Dialog](assets/trace-diagnosis/dialog-zh-light.jpg) |
| Persisted good/bad results | Chinese / dark | [Results](assets/trace-diagnosis/result-zh-dark.jpg) |
| Source trace detail and single-ID action | Chinese / dark | [Detail](assets/trace-diagnosis/detail-zh-dark.jpg) |
| Saved-diagnoser dialog | English / light | [Dialog](assets/trace-diagnosis/dialog-en-light.jpg) |
| Saved-diagnoser dialog | English / dark | [Dialog](assets/trace-diagnosis/dialog-en-dark.jpg) |
| Persisted good/bad results | English / light | [Results](assets/trace-diagnosis/result-en-light.jpg) |
| Saved-diagnoser dialog | Japanese / light | [Dialog](assets/trace-diagnosis/dialog-ja-light.jpg) |
| Saved-diagnoser dialog | Japanese / dark | [Dialog](assets/trace-diagnosis/dialog-ja-dark.jpg) |
| Narrow saved-diagnoser controls | Japanese / light | [Narrow dialog](assets/trace-diagnosis/dialog-ja-light-narrow.jpg) |
| Narrow persisted results | Japanese / light | [Narrow results](assets/trace-diagnosis/result-ja-light-narrow.jpg) |
| Quality overview with a 50.0 score | Japanese / light | [Quality overview](assets/trace-diagnosis/quality-ja-light.jpg) |
| October 20 source under a three-hour page window | Chinese / dark | [Historical batch](assets/trace-diagnosis/history-zh-dark.jpg) |
| October 20 source under a three-hour page window | English / dark | [Historical batch](assets/trace-diagnosis/history-en-dark.jpg) |
| October 20 source under a three-hour page window | Japanese / light | [Historical batch](assets/trace-diagnosis/history-ja-light.jpg) |

## Automated verification and independent review

| Check | Final result |
| --- | --- |
| AgentLens broad affected-path suite | **685 passed, 32 skipped**, zero failures; 36.52 seconds |
| Django selected-trace input, receipt, and proxy/scope suite | **39 passed**, zero skipped |
| AgentLens Ruff, format check, compileall, ADR check | Passed |
| Changed Django Python Ruff and compileall | Passed |
| Frontend full `npm test` | **1,257 passed**, zero failed, zero skipped |
| Frontend changed TypeScript/tests ESLint, typecheck, build | Passed; existing approximately 4.08 MB chunk warning |
| Report JSON, relative links, screenshot manifest, and scoped whitespace check | Passed |

The 32 AgentLens skips require an optional isolated OceanBase/seekdb test database fixture that was not configured for that command. The real HTTP database/collector runs provide additional evidence for their exercised paths; they do not replace every skipped database case. The broad suite retained an existing Starlette deprecation warning. No historical test totals are added to these results.

A fresh-context integrated source review found no remaining P1/P2 issue at its recorded manifest. Its independent focused runs passed 129 frontend, 125 AgentLens (six skipped), and 38 Django checks. These overlap the author suites. Review was read-only and used local offline/mock tests, without live external-model or deployment validation. The sanitized receipt records the manifest hashes so later source changes can be distinguished.

Run the final broad AgentLens command from `agentlens/`:

```sh
.venv/bin/pytest tests/unit/kernel/worker tests/unit/service/test_diagnosis_record_store.py tests/unit/service/test_quick_runs.py tests/unit/service/test_saved_diagnosis_runs.py tests/unit/service/test_authorized_diagnosis_input.py tests/unit/service/test_ingest_exporter.py tests/unit/infra/antcollector tests/unit/infra/signoz/test_input_marker_types.py tests/unit/infra/ob/test_judge_request_ledger.py tests/unit/diagnoser/test_core_execution_policy.py tests/unit/diagnoser/test_diagnoser_lifecycle.py tests/unit/diagnoser/test_specialist_executors.py tests/unit/diagnoser/test_specialist_policies.py tests/unit/diagnoser/test_retired_runtime_projection.py tests/unit/diagnoser/test_shared_evidence_projection.py tests/unit/web/routes/test_diagnosis_record_routes.py tests/unit/web/routes/test_specialist_routes.py -o addopts='' -q --tb=short
```

Run the final Django command from `ai-vision/aivision-py-be/`:

```sh
.venv/bin/python -m unittest apps.core.tests.test_diagnosis_quick_offline apps.core.tests.test_diagnosis_quick_input apps.core.tests.test_diagnosis_run_offline apps.core.tests.test_agentlens_proxy_scope.AgentLensProxyParameterTests apps.core.tests.test_agentlens_proxy_scope.TraceProjectionScopeTests apps.core.tests.test_agentlens_proxy_scope.DiagnosisResultScopeTests
```

The final frontend run from `ai-vision/vfe/` executed `npm test`, `npm run typecheck`, and `npm run build`, plus ESLint on all changed TypeScript and test files. Focused selected-trace regressions are available through `npm run test:quick-diagnosis`, `npm run test:diagnosis-time`, and `npm run test:i18n`; they are included in the full test command.

## Manual reproduction

Use prepared platform, frontend, AgentLens, collector, and telemetry services. Use disposable platform/AgentLens state, an Agent you own, and authorized input. Keep original telemetry rows read-only and track newly created source/output IDs for targeted cleanup.

1. Save a rule diagnoser or configured specialist on the existing diagnosers page. Confirm it belongs to the current Space and Agent. A model-based definition requires an available saved model connection; it must not receive a browser-side override.
2. Ingest safe success/error traces through the real OTLP endpoint with scoped credentials. Independently read back their source rows. For historical checks, use a safe source timestamp older than seven days.
3. Sign in as the Agent owner. Check `GET /api/v1/spaces/{space_id}/diagnose/runs/options?agent_uuid={agent_uuid}`. Confirm the saved definition appears with its correct availability and without private policy.
4. Select one or two traces in the existing list and open **Run diagnosis**. Inspect the network log: only saved options load; no candidate list is fetched. The equivalent authenticated, CSRF-protected request uses exactly this body. All angle-bracket values are placeholders.

   ```http
   POST /api/v1/spaces/{space_id}/diagnose/runs?agent_uuid={agent_uuid}
   Content-Type: application/json
   X-CSRFToken: <current-session-csrf-token>
   ```

   ```json
   {
     "request_id": "<new-request-UUID>",
     "trace_ids": ["<success-trace-id>", "<error-trace-id>"],
     "diagnoser_uuid": "<saved-diagnoser-uuid>"
   }
   ```

5. Retry a provisioning response using the unchanged request ID and body. Poll `/diagnose/tasks/{task_uuid}` under the same Space and Agent. Inspect source completeness, skips, failures, and budget statistics as well as terminal status.
6. Read `/diagnose/records` with `agent_uuid`, `task_uuid`, and `job_uuid`, then each `/diagnose/records/{record_uuid}` detail. Confirm persistence after collector processing. Open the source action and verify exact trace identity and source-time bounds. From trace detail, reopen the dialog and confirm it sends only the current trace ID.
7. Repeat the immutable request, change its selection or saved diagnoser, and exercise the rejected-input/configuration cases above. To test frozen definitions, accept a task, change only your owned saved definition, and retry the original request. It must return the original task; use a new request ID to invoke the current definition.
8. Query a historical fixed task without dates, with a recent window, and with its source window. Remove `task_uuid` to verify the ordinary seven-day default. Query each newly created record immediately to exercise the detail upper bound.
9. For a safe large-input regression, use a supported framework trace with multiple roots and a harmless large attribute payload. A projection-dependent check must persist a compact `failed` record with the original span count and `spans_omitted=true`. Verify downstream acceptance and record details. The measured byte counts above are dataset-specific, not general transport limits.
10. Compare stable-order source hashes before and after execution. Retain only review-needed synthetic resources and task receipts; clean up using their tracked IDs without deleting original inputs.

## Compatibility and unverified scope

Legacy `/quick-runs` routes, receipts, and internal metadata remain compatible. New saved-definition tasks expose `can_retest=false`; a new invocation uses the current saved configuration and a new request ID. The action remains restricted to authorized SigNoz trace surfaces; public and Langfuse surfaces do not receive diagnosis controls.

This report establishes only the exercised prepared macOS source paths. Linux, cold-start installation, Kubernetes, released images, upgrades, and hosted CI were not validated. Existing collector/storage images do not identify the locally tested product source. External model execution, provider availability, and model quality were not tested. Synthetic inputs establish behavior for the measured cases, rather than general diagnosis quality. Task-owned resources retained for review total 22 tasks, 85 result spans, 10 synthetic source spans, four saved diagnosers, and one Agent as of the final runtime/browser receipt. Original replay inputs are excluded. Cleanup had not run when the receipt was captured.
