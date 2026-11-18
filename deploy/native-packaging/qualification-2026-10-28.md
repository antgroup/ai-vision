---
title: Native packaging qualification reference — 2026-10-28
type: reference
audience: [A4]
runs: no
verified_on: 2026-10-28
sources:
  - deploy/native-packaging/build.py
  - deploy/native-packaging/requirements-build.txt
  - deploy/native-packaging/test_build.py
  - ai-vision/aivision-py-be/scripts/native_build.py
  - ai-vision/aivision-py-be/apps/core/tests/test_agent_type_queryset.py
  - ai-vision/aivision-py-be/apps/core/tests/test_native_onboarding_provider.py
  - agentlens/native/build.py
  - agentlens/native/requirements-build.txt
  - agentlens/tests/unit/infra/ob/test_native_builder.py
  - agentlens/tests/unit/infra/ob/test_native_migrations.py
  - agentlens/tests/unit/test_selfhost_entrypoint.py
  - scripts/build/build_native_onboarding.py
  - scripts/build/test_build_native_onboarding.py
  - scripts/build/test_native_artifacts.py
  - ai-vision/agent-plugins/scripts/tests/test_onboarding_native.py
  - aivision-cli/internal/cli/connect_native_test.go
---

# Native packaging qualification reference — 2026-10-28

## Historical scope and provenance

| Field | Qualified value |
| --- | --- |
| Status | Historical native-stage qualification before product-image assembly; manual execution, not hosted CI |
| Qualified source baseline | `historical-source-revision-redacted`, plus the native-packaging working-tree changes and new producer files; the baseline alone is not the tested candidate |
| Latest-main boundary | Later source refresh to `historical-source-revision-redacted` has separate central-workflow evidence below. It does not inherit the historical real-SQL, integration, live-model, or lifecycle qualification |
| Runtime identity | Producer input hashes, toolchain receipts, staged-file inventories, and final archive hashes; private execution logs retained separately |
| Platforms | Linux/arm64 native execution; Linux/amd64 application/compiler/seekdb execution under emulation on an ARM64 host |
| Shared services | ARM64 gateway and data backend; no all-stack-amd64 qualification |
| Toolchain | CPython 3.11.14, Debian Bookworm, GCC 12.2.0; Cython 3.2.4 service modules; Nuitka 4.3.1 standalone installer; AgentLens build setuptools 79.0.1 |
| Compiler policy | Services: `language_level=3`, `binding=True`, `annotation_typing=False`, extensions `-O0 -g0`; native C launchers `-O2` |
| Installer libc boundary | Highest required symbol version `GLIBC_2.35`; execution on Bookworm glibc 2.36. A glibc 2.35 host was not separately exercised; older glibc and musl are unqualified |
| Source exception | 24 exact public Langfuse/Relay YAML guides and `nvidia-nemo-relay/relay_tracing.py`, declared as owned public examples with approval reasons; no runtime-helper exemption |
| Design and producers | [Compilation design](README.md), [backend build documentation](../../ai-vision/aivision-py-be/README.md#原生分发构建验证), [AgentLens build documentation](../../agentlens/native/README.md), [standalone installer producer](../../scripts/build/build_native_onboarding.py) |

## Historical executed checks

| Area | Recorded result | Evidence boundary |
| --- | --- | --- |
| Backend source regression | 92 passed | Focused source suite |
| Backend native regression | 294 passed per architecture | SQLite fixtures; separate from real SQL checks |
| Backend real-service behavior | Fresh seekdb migrations, seeds, repeated migration, SigNoz/market bootstrap, liveness/readiness passed on both targets | Real isolated services; not production migration |
| Backend retained data | ARM core0032→0033 sentinel retained; repeated seed/bootstrap stable; final ARM replacement retained account/Space/Agent/session/key primary-key sets | Specific retained-schema/data scenarios |
| Canonical database initializer | Compiled initializer and sanitized failure behavior passed on both targets | Used the original AgentLens PyMySQL dependency environment; not evidence that the backend dependency lock alone supplies the SQL driver |
| AgentLens native regression | 1,466 passed and 6 skipped per architecture | Six cases require a separate disposable unit-test database fixture |
| AgentLens real migrations | Empty schema→011, retained sentinel during 010→011, repeated head upgrade passed per architecture | Eight tables per fresh schema; real seekdb |
| AgentLens discovery and worker | 15 executors, 51 API paths, real SQL readiness, isolated worker flags, pooled/one-shot protocol, date transport, timeout/replacement/fallback passed per architecture | Production modules loaded from native extensions |
| Scheduled diagnosis | One source item; pooled custom-rule and live-model judge each evaluated one good metric; two successful records persisted and authenticated list/detail readback passed per final worker | Actual scheduler, scoped telemetry, SQL, and configured model; no model-quality claim |
| Build and content audit | 42 passed | Guarded producer, ownership, generated C/Cython, archive, source-drift, and path-overlap regressions |
| Installer source regression | 37 passed | Focused onboarding unit suite |
| Actual Go/native installer fixture | 11 checks passed per architecture | HTTP/auth/catalog and host inputs controlled; download/hash, install/reinstall, rollback, source cleanup, persisted hook runtime, status, and no client Python requirement |
| Actual Agent integration | Final archive installation, released Codex hook→gateway→SigNoz, authenticated Trace list/detail and installed/verified/valid/data-found status passed per architecture | Controlled host probe/transcript; real hook, account/Space, auth, network services, and storage; no Codex model session |
| Installed-byte identity | All 47 runtime files matched the final archive per architecture | Successful persisted execution after temporary download cleanup |
| Backend lifecycle | Readiness 200, API/projector children observed, SIGTERM exit 0, no OOM on both targets | Actual supervisor/native helper paths |
| AgentLens lifecycle | Actual entry point as PID 1; readiness 200; SIGTERM exit 0, no OOM on both targets | ARM 0.541 s; emulated AMD 0.611 s, within the existing 300 s budget; no performance claim |
| Full Agent scripts suite | Linux: 110 passed, 3 failed; macOS: 106 passed, 7 failed, out of 113 | Three existing documentation substring failures; four additional macOS Bash 3 worktree-array failures. Full suite not passing |

## Historical accepted artifact identities

| Artifact | Linux/arm64 | Linux/amd64 |
| --- | --- | --- |
| Backend | 264 native modules, 4 native entry points; 276 inventoried files, zero source-audit violations | Same counts and audit result |
| AgentLens | 202 native modules, 3 native entry points; 209 owned files including 205 ELF files; complete inventory 7,258 files, zero violations | Same owned counts; complete inventory 4,547 files, zero violations |
| AgentLens source receipt | 211 inputs; SHA-256 `8273bb17340739e80535a10d3201c219304f1731da178362ece9c62c98999098` | Same receipt |
| AgentLens final entry-point refresh | One shell file changed; all 205 ELF files unchanged; receipts/audits refreshed before final execution | Same refresh boundary |
| Installer archive SHA-256 | `806c6715f01a41b99a478d17b5d66e4db0ef4ebd12b936babc3b2a67affe8208` | `4db5135abaa0dd89fde3f1786810a5c284cdeacc6928136964ceca3dd13fc41b` |
| Installer content | 47 regular files: 1 owned native executable, 46 reviewed upstream libraries | Same ownership/counts |
| Installer size | 8,076,636 compressed / 27,304,062 unpacked bytes | 16,900,357 compressed / 44,196,366 unpacked bytes |

## Central workflow verification on refreshed main

| Field | Recorded value |
| --- | --- |
| Source | Host-recorded baseline `historical-source-revision-redacted` plus native-packaging working-tree changes |
| Receipt source identity | `source_revision` is null in these mounted builders; per-input hashes identify the tested bytes. Independent final audit matched all 496 ARM inputs and AMD backend 282 / AgentLens 214 / onboarding 7 inputs to the checkout |
| Central builder SHA-256 | `f5d314d39646bf855cb9e31e00ecf722b3d19b158acde9093bf6f1282cfd1a69` |
| Prepared-input execution | ARM: one `--component all` invocation with prepared tools and both dependency environments; AMD: separate backend, AgentLens and onboarding invocations with prepared inputs; all six component builds succeeded |
| Build checks | Content audit: zero violations; database-free component smoke checks passed; every ELF file matched its requested target architecture |
| Service inventories including dependencies | Backend: ARM 7,950 / AMD 11,592 files; AgentLens: ARM 7,258 / AMD 4,547 files |
| Installer identity | Rebuilt ARM and AMD archives byte-identical to the historical installer hashes above |
| Central boundary tests | 14 passed |
| Fresh backend regression | Source: 49 passed; compiled: 351 passed per architecture, including refreshed diagnosis behavior |
| Fresh AgentLens regression | Full source unit suite successful with explicit database-fixture skips; compiled: 1,580 passed and 12 explicit database-fixture skips per architecture, with one upstream deprecation warning |
| Native AgentLens test scope | Diagnosis/domain, saved runs, quick runs, diagnosis record store, and projection-failure snapshots; external test scenarios staged separately outside the production artifact; artifact bytes unchanged |
| Fresh Go/deployment validation | Go packages and generated assets passed; all 59 Linux deployment tests passed with zero skips; installer suite 9 passed and 1 skipped |
| Default preparation | Fresh isolated tool environment installed and verified all eight pinned packages; supplementary cold-cache dependency sync stopped before completion due to limited network throughput; automatic frozen dependency preparation remains unqualified; complete automatic-preparation build pipeline not executed as one run |
| Backend initializer boundary | Compiled backend helper present but not exercised by backend smoke; SQL initialization requires the AgentLens dependency environment with PyMySQL |
| New service-acceptance boundary | No real SQL migrations/upgrades, live-model diagnosis, persisted service readback or PID 1 lifecycle rerun on the refreshed source; no product images built |
| Procedure | [Build native artifact stages](build-artifacts.md) |

| Successful receipt | SHA-256 |
| --- | --- |
| ARM, all components | `0b9e9f5509217d8b669e6b0ab7bfc52db1d96da12d8913d12b8ef10205214bb9` |
| AMD, backend | `d45d5a51f8a931364cbe02a5099750d59752bc4b845236b9a5db97ef808ab8ec` |
| AMD, AgentLens | `a80ee972d42d5d4a8ba3f5693ec776658fdbecd2c8cab1e9dceab5cd9840644b` |
| AMD, onboarding | `4a9465595562d2df681a0d43342ca771ee677d13a7ed8b2a5aa9148e7db61a71` |

## Unqualified scope and subsequent gate

| Boundary | Status |
| --- | --- |
| Product images | No product image built or published; existing source-copying Dockerfiles do not satisfy native-only delivery |
| Deployment assembly | Compose/Kubernetes/CLI fallback helper mounts and generated source bytes not qualified as native-only deployments |
| Image acceptance | Pending artifact-only context, all-layer/archive content audit, native-only mounts/ConfigMaps, and actual candidate-image/caller acceptance |
| Other environments | macOS/Windows artifacts, older glibc, musl, OceanBase CE, recipient-only access, and all-stack-amd64 operation unqualified |
| Other Agent distributions | Available released Codex 0.3.18 artifacts exercised; other Agent/runtime packages unqualified |
| Released hook provenance | Catalog artifact 0.3.18 and verified digest; executable banner `dev (unknown)`; installer glibc result does not qualify the hook's separate libc floor |
| Native framework limits | New migration-source generation, callback pickling, sustained load/capacity/SLO and performance unqualified |
| Lifecycle distinction | Earlier cleanup of sleep-parent test runners excluded from accepted PID 1 shutdown evidence |
| Historical cleanup | Original qualification Jobs disabled; active Tasks and enabled scheduled Jobs zero; original task-owned qualification/compiler/test-runner containers stopped |
| Evidence handling | Build output, logs, private configuration and traces remain ignored; no credentials, private addresses, worker/client identities or company paths in public records |
| Vulnerability scanning | Separate offline/manual process owned by the team; no scanner CI or mandatory SBOM added |
