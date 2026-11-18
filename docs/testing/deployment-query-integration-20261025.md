# Query controls and canonical deployment integration — 2026-10-25

PR #114 integrates live main `historical-source-revision-redacted`, which contains the issue #88 deployment consolidation delivered by PR #94. The pre-integration product head was `2bdb1fbb4b5af0082211174334d32231bf901437`. Checks below cover the merged source and generated configuration; they do not establish a deployed release.

The subsequent [functional deployment regression](deployment-functional-regression-20261025.md) adds real canonical Compose backend execution, frozen-store queries, automatic refresh, active-request shutdown and a paired runtime timing check. Its scope and artifact boundaries are recorded separately.

## Result

The canonical inputs under `deploy/` now carry query acceleration defaults through the image blueprint, source blueprint, four product Compose variants and fixed Kubernetes renderer. `make assets` regenerates CLI compatibility copies and Compose output. The preferred deployment keys are `BACKEND_QUERY_MODE=auto` and `DATA_BACKEND_PROJECTION_MAINTENANCE_ENABLED=true`; they map to the existing application environment names. Role keys take precedence over legacy keys within the same configuration layer. These aliases still represent only two emergency controls.

The backend entrypoint retains migrations and production Gunicorn serving while supervising the discovery/projector worker. Explicit bootstrap commands bypass the supervisor. TERM/INT reach both children; shutdown uses one 50-second budget, preserving Gunicorn's 45-second graceful timeout within the existing 60-second container grace. A child failure stops its peer and returns a failure for the deployment supervisor to restart.

Normal backend configuration does not receive the internal recovery key. Operator references use the actual Docker backend container and a Kubernetes stdin-only recovery process, retaining the original deployment namespace, configuration and data identities.

## Staged validation

| Check | Result | Boundary |
| --- | --- | --- |
| Canonical assets freshness | 36 compatibility files and four product Compose templates current | Generation and content checks; no deployment |
| Native Docker Compose 5.1.2 configuration | 16/16 combinations passed | Four templates × defaults, role emergency values, legacy emergency values and same-layer role precedence; checks bootstrap isolation and absence of recovery key in normal backend |
| Full CLI Go suite | Passed with Go 1.25.14, linux/arm64, vendored dependencies, network disabled | Cached toolchain container; includes alias, renderer, Kubernetes and lifecycle tests |
| Deployment Python suite | 68 tests, OK, including 13 optional skips | Fixture/static coverage; skipped cases are not runtime acceptance |
| Installer Python suite | 2 tests, OK | Local fixture coverage |
| Backend regression | 195 tests, OK | SQLite tests, including query/session semantics, health, integration and authentication boundaries |
| Supervisor lifecycle | 8 tests, OK, included above | Isolated real child processes exercise argv, TERM/INT, peer failure, spawn failure and forced cleanup; the default 50-second budget is checked without a full-length drain |
| Gunicorn 21.2.0 invocation | Module/configuration check passed | Fixture WSGI application and production arguments; not a serving container test |
| Documentation | Three-language operator references, deployment guides and Skill instructions reviewed | Command paths, role-key restoration, recovery credential scope and generated copies |

The sanitized [Compose configuration receipt](evidence/deployment-query-integration-20261025.json) records the matrix and hashes of the tested deployment/runtime sources. No credentials or resolved private environment files are included.

An exploratory full CLI run with Go 1.27.3 on macOS failed the existing account-cookie test `TestSecureAndExpiredCookies`. A rerun on the supported Go 1.25 line passed the entire suite, including that test. No unrelated account implementation or dependencies were changed. An initial backend invocation used an incorrect test-module name and lacked permission for the local handshake fixture; the corrected, permitted 195-test run above passed.

## Performance and unverified scope

The query implementation and SigNoz head `22e936bf647ba2042810d71fa200e6cbbb468aab` are unchanged by this deployment integration. The earlier [same-data performance and recovery report](standard-query-defaults-20261025.md), including four frameworks and 10,000 Sessions, remains bound to its recorded source/runtime. Those distributions were not remeasured through the newly integrated Gunicorn startup path. The previously measured long CE recovery cost remains relevant and is not removed by these deployment changes.

No running database, retained trace set, existing deployment or published image was modified for this integration. Full image startup, live Docker lifecycle, Kubernetes rollout, cold installation, production load and a real 45-second in-flight Gunicorn drain remain unverified by these checks. The generated defaults require matching product and SigNoz builds containing the query implementation.
