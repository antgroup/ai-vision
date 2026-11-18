---
title: Native Python packaging design and operating lessons
type: explanation
audience: [A4]
runs: no
verified_on: 2026-10-29
sources:
  - deploy/native-packaging/build.py
  - deploy/native-packaging/requirements-build.txt
  - deploy/native-packaging/images.py
  - ai-vision/aivision-py-be/scripts/native_build.py
  - ai-vision/aivision-py-be/docker/manage.c
  - ai-vision/aivision-py-be/apps/core/models/agent.py
  - ai-vision/aivision-py-be/apps/core/management/commands/serve_with_projector.py
  - ai-vision/aivision-py-be/apps/core/views/onboarding.py
  - agentlens/native/build.py
  - agentlens/native/migrations.py
  - agentlens/native/custom_rule_worker.c
  - agentlens/src/agentlens/config.py
  - agentlens/src/agentlens/diagnoser/executors/rule/custom/sandbox.py
  - agentlens/docker/selfhost-entrypoint.sh
  - scripts/build/build_native_onboarding.py
  - scripts/build/native_artifacts.py
  - scripts/ci/native_cli_archive.py
  - aivision-cli/internal/cli/connect_native.go
  - deploy/docker/compose.yaml
---

# Native Python packaging design and operating lessons

Native packaging compiles the project's Python implementation while retaining its
framework behavior and required data. Compilation, source-content auditing, and
runtime qualification answer different questions. All three are needed before an
artifact can become an input to a product image.

The optional [native candidate workflow](../../.github/workflows/native-candidate.yml)
reuses these producers and local OCI audits without private repositories,
registry credentials or a publication step. It prepares this repository for
independent open-source CI and retains both source-capable and restricted native
CLI variants. Its initial scope is Linux amd64 build/content/import/OCI evidence,
not deployment or a complete product delivery; see the
[CI boundary](../../scripts/ci/README.md#daily-and-release-handoff).
User-facing Skills stay in their separate catalog. The standard product release
publishes through `antgroup/ai-vision`.

Coding agents start with the [deployment rules](../AGENTS.md) and the
[native packaging and qualification contract](release-contract.md). The contract
connects native builds to image/CLI audits, platform qualification, upstream
database pins, and offline scan evidence.

Use [Build native artifact stages](build-artifacts.md) to invoke the central
producer, then [assemble and inspect native images](build-images.md) from its
accepted output. Component producers remain implementation
details behind that entry point. This page explains the design and the failures
that shaped it; historical service qualification and refreshed-source build
results are recorded separately in the
[2026-10-28 qualification reference](qualification-2026-10-28.md).
The later [amd64 Daily delivery reference](delivery-2026-10-28-amd64.md)
records the actual image, packaged CLI, Agent, and diagnosis acceptance for the
first private release. The [corrected revision 2 record](delivery-2026-10-28-amd64-revision2.md)
adds compile-time CLI source exclusion and the completed replacement x86 delivery.

## Different runtimes need different compilation units

| Runtime | Selected form | Owning build documentation |
| --- | --- | --- |
| Django backend | Cython 3.2.4 extension per module, with native management/bootstrap entry points | [Backend native build](../../ai-vision/aivision-py-be/README.md#原生分发构建验证) |
| AgentLens | Cython 3.2.4 extension per module, with native migration/database/worker entry points | [AgentLens native producer](../../agentlens/native/README.md) |
| Agent installer and persisted hook helper | Nuitka 4.3.1 standalone bundle | [Onboarding producer and its input contract](../../scripts/build/build_native_onboarding.py#L1) |

The service builds target CPython 3.11.14 on Debian Bookworm. They retain a
matching interpreter, standard library, and separately resolved upstream
dependencies. The installer bundles its runtime because an Agent host must not
need a client Python installation.

Per-module extensions preserve package paths, Django command discovery, executor
registration, and migration discovery. Both service producers use
`language_level=3`, `binding=True`, and `annotation_typing=False`; extension flags
are `-O0 -g0`. These settings prioritize Python compatibility in the qualified
build. Performance remains unqualified. The
[backend producer](../../ai-vision/aivision-py-be/scripts/native_build.py#L110) and
[AgentLens producer](../../agentlens/native/build.py#L174) own those settings.

The backend's alternative Nuitka fused-package candidates exposed a useful
qualification trap. Versions 2.8.10 and 4.3.1 imported successfully, but four
warmed Django command regressions failed. An attribute lookup returned
`BaseCommand` while the module dictionary contained the expected `Command`
subclass. Tracing changed the outcome, so an import-only smoke check would have
missed the failure. Per-module Cython passed those regressions without an
interpreter or Django patch. This observation explains the service compiler
choice; it does not generalize to every Nuitka application. The smaller
standalone installer passed its own behavioral checks with Nuitka 4.3.1.

## Framework discovery is part of the runtime contract

Compiling a callable can change its reflected type even when calling it behaves
the same. Django's automatic QuerySet-to-manager copier uses
`inspect.isfunction`, which does not recognize Cython functions. The explicit
[AgentTypeConfigManager](../../ai-vision/aivision-py-be/apps/core/models/agent.py#L18)
therefore forwards the existing runtime filters to the QuerySet. Verifying row
materialization and chained filters catches failures that a successful model
import cannot.

Pydantic similarly needs to recognize compiled Settings methods as methods.
[Settings.model_config](../../agentlens/src/agentlens/config.py#L48) adds the type
of an existing helper to `ignored_types`. In source mode that type remains the
ordinary Python function type. The adjustment preserves the existing model
fields and configuration semantics without patching Pydantic.

Migration loaders need explicit attention as well:

- Django migration filenames begin with digits. Cython requires an identifier
  for the compiled module, so the [backend builder](../../ai-vision/aivision-py-be/scripts/native_build.py#L79)
  compiles an internal alias and exports the canonical `PyInit_` symbol. The
  delivered filename and database migration name stay canonical. Internal
  aliases may still appear in Python metadata; callback pickling is outside the
  qualification.
- Alembic's normal loader expects source revisions. The
  [native migration adapter](../../agentlens/native/migrations.py#L20) discovers
  extension suffixes and loads the original compiled environment and revisions.
  Its `from_config` factory explicitly returns the subclass: the inherited
  factory constructs `ScriptDirectory` directly and would discard the native
  loader. This adapter depends on the Alembic version in `agentlens/uv.lock` and
  needs requalification when that dependency changes.

Real fresh-schema and retained-data upgrades complement loader tests. Native
`makemigrations --check --dry-run` finding no changes does not demonstrate that a
native distribution can create new migration source.

## Entry points must preserve isolation, relocation, and shutdown

The custom-rule worker previously relied on the isolation properties of
`python -I -S`. Its [native C launcher](../../agentlens/native/custom_rule_worker.c#L1)
uses isolated CPython configuration, disables `site`, verifies all four isolation
flags, and adds only its sibling protocol module directory. The
[source/native command selector](../../agentlens/src/agentlens/diagnoser/executors/rule/custom/sandbox.py#L14)
is shared by pooled and one-shot execution. JSON transport, date serialization,
timeouts, replacement after a crash, and stopped-pool fallback remain acceptance
requirements; compiling the protocol alone would not establish them.

Copying a virtual environment does not make its console-script shebangs
relocatable. The [AgentLens entry point](../../agentlens/docker/selfhost-entrypoint.sh#L45)
uses `python -m uvicorn` so the selected runtime interpreter loads Uvicorn after
the dependency environment moves. Its native migration/bootstrap path still
honors explicit configuration and the canonical database initializer's sanitized
errors. The backend likewise [selects its native management launcher](../../ai-vision/aivision-py-be/apps/core/management/commands/serve_with_projector.py#L17)
for projector children.

Lifecycle evidence must come from the real entry point as container PID 1.
Starting a server with a separate exec operation under a `sleep` test-runner
parent measures the runner's cleanup behavior. The qualification therefore
started the actual entry point on each target, checked readiness and PID 1, then
verified normal SIGTERM completion and the absence of OOM termination.

## Ownership and input identity control what can be distributed

The content boundary follows file ownership.
Project business code, migrations, executable installers, and hook helpers are
native artifacts. CPython and reviewed open-source dependencies may retain
source. Public Python SDK integration examples are a separately approved
exception: each exact file needs an approval reason in the inventory. An example
directory or an upstream dependency label cannot exempt a project runtime.

The [artifact inventory and audit](../../scripts/build/native_artifacts.py#L1)
bind every staged file to a hash and explicit ownership. It rejects changed,
added, missing, or unmapped files, owned Python/Cython source and bytecode,
generated Cython/Nuitka C, common embedded programs, and source in inspected
archives. Renaming source or retaining `.pyx` inputs does not meet the boundary.
Archive inspection rejects unsafe entries and fails when inspection limits are
exceeded. Ownership declarations still require review; this audit cannot prove
upstream provenance or detect arbitrary obfuscated source inside native binaries.

Dependency copies require the same ownership check. On CPython configured to use
`lib`, native staging skips the virtual environment's top-level `lib64` symlink
when it resolves to the same environment's real `lib` directory. Separate
`lib64` trees are copied. [AgentLens dependency staging](../../agentlens/native/build.py#L46)
removes its package, editable finders, metadata, and caches from every copied
`site-packages` tree.

All producers separate source, private compiler output, and runtime staging,
reject overlapping paths, and compare input fingerprints after compilation.
The [onboarding producer](../../scripts/build/build_native_onboarding.py#L138)
also checks its builder and auditor before atomic archive publication, and derives
dependency ownership from the compiler report. A passing build with mutable
inputs is not a receipt for the final source. Generated C and compiler caches stay
outside the runtime; input hashes and audit receipts identify the accepted bytes.

## Artifact qualification and image acceptance have separate scopes

The initial qualification reference separates historical native Linux/arm64 and emulated
Linux/amd64 service acceptance from later central-workflow builds on refreshed
main. Real SQL, telemetry readback, live-model diagnosis, and PID 1 lifecycle
results apply to the historical candidate. New build/content/import checks do
not extend those service claims. The later packaged native x86 stack has its own
[revision 2 acceptance record](delivery-2026-10-28-amd64-revision2.md); its results
do not qualify a different image, ARM stack, or performance under emulation.

Source-mode component Dockerfiles still copy project source. The dedicated
[native image builder](images.py) instead assembles artifact-only contexts and
audits their added layers. Generated Compose, Kubernetes, and CLI callers also
need their own content review: source-mode bootstrap bind mounts can carry
fallback helpers even when an executable selects a native helper. Selecting
native execution does not remove distributed source bytes.

The image gate requires an allowlisted artifact-only runtime context, inspection
of every distributed image layer and archive, native-only helper mounts or
ConfigMaps, and acceptance of the actual image/caller combination. Source-mode
development remains a separate supported path. The present packaging audit is
separate from the team's offline vulnerability scanning; it adds no scanner CI or
mandatory SBOM process.

The native CLI archive has its own content checks. The
[archive validator](../../scripts/ci/native_cli_archive.py) binds attribution
members to trusted bytes and checks archive structure as well as the linked
executable. The [product release workflow](../../.github/workflows/release-images.yml)
publishes accepted service image versions and promotes rolling aliases.
Database dependencies are pinned independently of the product version.

Build output, execution logs, credentials, private configuration, and private
traces remain ignored local evidence. Public records retain only redacted results
and artifact identities. They must not publish private addresses, worker/client
identities, company paths, or model keys.
