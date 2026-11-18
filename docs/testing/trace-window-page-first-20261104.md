# Page-first Trace recovery validation (2026-11-04)

## Problem and change

Refs #151. Baseline: `historical-source-revision-redacted`
(merged main, including the product-side #129 changes).

When canonical summary coverage is stale, ordinary Trace lists previously tried
the complete-window snapshot before the existing storage-page adapter. The
snapshot could reject a >512 MiB source window before a one-row page was tried.

The fallback now tries the existing validated `native-trace-page-v1` contract
first for ordinary auto-mode lists. That adapter decides whether filters,
ordering and requested fields are supported, and verifies global totals/facets,
complete page spans and authorized scope. Unsupported requests retain the
complete-window path; page transport/auth/invalid-contract failures are not
silently converted into recovery. Explicit raw mode keeps snapshot-first
behavior, and existing page-based full-IO evidence requests remain unchanged.

Legacy SigNoz `400 / invalid_input` errors specifically identifying an oversized
complete recovery window now map to the existing capacity code 3036. Error-body
inspection is limited to 8 KiB; unrelated errors retain their failure semantics.
The message recommends narrowing time, rather than suggesting that page size
necessarily bounds a whole-window recovery.

## Executed checks

Host: macOS, CPython 3.12, frozen backend `uv.lock` environment, isolated Django
SQLite test database. No production service, data or configuration was changed.

Working directory: `ai-vision/aivision-py-be`.

```sh
uv sync --frozen --python 3.12
.venv/bin/python manage.py test \
  apps.core.tests.test_trace_query_memory \
  apps.core.tests.test_native_trace_pagination \
  apps.core.tests.test_native_read_model \
  apps.core.tests.test_native_pricing_snapshot \
  apps.core.tests.test_framework_snapshot --noinput
```

**102 tests passed.** Regression checks cover a stale summary followed by a
valid 1-row page while the snapshot would reject the window; snapshot must not
be called. They also cover explicit raw control, unsupported page recovery,
page transport errors, bounded legacy capacity-error parsing and preservation
of unrelated malformed/oversized HTTP 400 failures. Existing page tests verify
scope, ordering, facets, canonical output and unsupported-query behavior.

Before implementation, the stale-coverage regression raised the injected
whole-window capacity failure instead of returning the valid storage page.
These dependency-level tests use storage fixtures; they do not allocate or
transfer a physical 512 MiB dataset.

A broader `manage.py test apps.core.tests --noinput` run (before adding the last
two targeted route assertions) ran **1,666 tests**, with **50 skipped and two
failures**. Both failures were independently reproduced on the unmodified
baseline in a separate detached checkout using the same locked environment:

- `test_integration_lifecycle.IntegrationLifecycleTests.test_team_source_plugins_can_restore_but_cannot_use_trace_confirmation`
- `test_download_latest.AgentDownloadLatestTests.test_hermes_source_guide_does_not_offer_an_unbuilt_cli_installer`

The broader suite is therefore **not reported as green**. An earlier exploratory
run reused an older environment without PyYAML; it is superseded by the locked
environment results, not counted as validation.

## Root-status compatibility correction (2026-11-06)

Review of PR head `historical-source-revision-redacted` found a
cross-provider status mismatch: the existing storage-page SQL derives Trace
status using the maximum status across all spans, while the public native
contract follows the canonical root observation. For example, a successful root
with a failed, retried tool child must remain a successful Trace.

Page-first routing exposed this mismatch when summary coverage was stale:
filtering for OK silently omitted the Trace, filtering for ERROR failed local
validation, and sorting by status could select the wrong global page. Validating
only the returned page cannot detect an omitted match or prove global ordering.

The adapter now declines status predicates and status sorting before making a
storage-page request, after the ordinary public parameter validation. Those
requests retain the verified complete-window path. Status projection from
complete page spans remains eligible, so ordinary lightweight lists continue to
avoid whole-window recovery. No new provider capability or larger budget is
assumed; a status-dependent query may still hit the complete-window guard.

Three regressions cover OK/ERROR predicates and filter aliases, all four status
sort spellings across the first two one-row pages, and status projection while
window recovery would exceed its budget. Before the adapter correction, the
first two tests produced ten failed subtests and one invalid-page error; the
projection control already passed. After the correction, the pagination and
native-query modules passed **48 tests**. The original five validation modules
plus managed-query, full-input evidence, preview, diagnosis-purpose, dashboard
pricing and query-threshold modules passed **149 tests** in one combined run.
These remain fixture-level checks on macOS / CPython 3.12.14 with frozen
`uv.lock`; they do not establish real storage or browser acceptance.

The broader backend rerun executed **1,671 tests**, with **50 skipped and the
same two failures listed above**, both also reproduced on the unchanged
comparison base in the same dependency environment. This run allowed the local
HTTP fixture servers and excluded localhost from the host proxy. Earlier
sandbox runs could not bind those servers and produced additional setup errors;
they are superseded by this complete run. The broader suite remains **not
green**; no hosted CI or production validation is inferred from these checks.

## Capacity recovery compatibility correction (2026-11-06)

A separate real local combination exposed a second routing regression under
the ordinary budgets: one large synthetic Trace had a clean published summary,
while six other Traces still needed raw recovery. The complete snapshot returned
seven correct rows using one clean summary and six small raw groups, but loading
the large Trace's original spans for the selected storage page returned 413.
Page-first routing must not make that previously valid snapshot inaccessible.

Only an explicit capacity error (3036) from the preferred page now allows an
attempt through the existing verified snapshot adapter. Snapshot authorization,
completeness and resource guards still apply. If that capability declines, the
original page capacity error is retained; the implementation does not retry the
failed page or bypass the guard through a raw compatibility scan. Other page
errors, including authentication, invalid contracts and busy capacity (3037),
retain their existing propagation.

The success regression failed before this correction with the injected page
capacity error. It and the unavailable-snapshot control now pass; the combined
eleven validation modules pass **151 tests**, including the status and long-window
page-first controls. This corrects representation selection without increasing
budgets or claiming every oversized Trace can be served.

## Final minimal-fix integration check (2026-11-07)

The final PR candidate includes both compatibility corrections above and is
integrated with main `historical-source-revision-redacted`, after merging
PR #155. This check excludes the separate layered-capacity proposal, page-v2,
per-Trace partial-error DTOs, frontend changes and data-plane extensions.

A fresh macOS / CPython 3.12.14 / Django 6.0.3 run reused the existing locked
backend dependency environment with an external test-settings module overriding
the database to SQLite `:memory:` and the cache to LocMemCache. From the backend
directory, the following ten modules passed **171 tests**:

```sh
python manage.py test \
  apps.core.tests.test_native_trace_pagination \
  apps.core.tests.test_trace_query_memory \
  apps.core.tests.test_native_query \
  apps.core.tests.test_signoz_trace_provider \
  apps.core.tests.test_native_read_model \
  apps.core.tests.test_native_pricing_snapshot \
  apps.core.tests.test_framework_snapshot \
  apps.core.tests.test_projection_memory_lifetime \
  apps.core.tests.test_source_pages \
  apps.core.tests.test_trace_projection --verbosity=1
```

A separate temporary probe using the actual Django provider and mocked
data-plane readers passed **14 cases**: page-first priority, explicit raw mode,
3036 snapshot recovery, unavailable-snapshot propagation without a raw scan,
authorization/3035/3037 propagation from each applicable reader, actual malformed
page-contract rejection, and retention of the same request budget and admission
slot during recovery. These are fixture-level assertions, not a new Linux or
browser acceptance run. The historical broader suite failures above remain
separate and are not relabeled as passing. Hosted CI is not claimed green;
billing-blocked Actions are excluded from the merge gate at the user's request.

## Limits and next acceptance

This is a backend routing/error fix, not a data-plane budget increase, schema
migration, frontend layout change or production deployment. No browser screenshots
are applicable to this backend-only change. Existing response fields, capability
checks and authorization validators remain in use.

The page endpoint itself still has bounded response/work limits. Unsupported
content-wide filters/sorts, canonical SDK-user queries or unavailable legacy
capabilities can still require complete recovery and return an actionable limit
error. Do not silently truncate, infer global facets from one page, or accept a
partial trace to avoid this restriction. A separate facet request is not claimed
to become page-bounded by this change.

Actual current-product/current-SigNoz isolation acceptance with a >512 MiB window,
production authentication, browser navigation and packaged release images has
not been run for this patch. Before rollout, compare IDs/order/counts/previews
with a complete authorized reference, exercise both matching current data plane
and older-provider decline paths, and verify serving build identities. Source
checkout updates alone do not replace deployed images. This PR does not claim
to close every scenario in #151, #124, or the separate diagnosis path in #127.
