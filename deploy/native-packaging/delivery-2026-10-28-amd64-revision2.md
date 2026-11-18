---
title: Corrected private amd64 Daily delivery, revision 2
type: explanation
audience: [A4]
runs: no
verified_on: 2026-10-29
sources:
  - aivision-cli/README.md
  - deploy/bootstrap/oceanbase.sh
  - deploy/tests/test_oceanbase_lifecycle.py
  - deploy/native-packaging/images.py
---

# Corrected private amd64 Daily delivery, revision 2

**Published and verified:** private Daily revision 2, tagged
`delivery-v0.0.1-daily.20261028.2` in `ob-labs/ai-vision-skills`.
The original six authenticated assets matched the accepted bytes; native x86 installation and all
seven published-image pulls passed. Temporary credentials and test resources were
removed, and the original environment was preserved. The earlier Release was later
withdrawn during the explicitly approved consolidation below; its
[delivery record](delivery-2026-10-28-amd64.md) and original local evidence retain the
earlier results and limitations.

## Original accepted artifact identity

| Field | Value |
| --- | --- |
| Product version | `0.0.1-daily.20261028.2` |
| Product source | `historical-source-revision-redacted` |
| Actual source workflow | `40767459852.1`, run number `105` |
| Skills catalog | `9ac54119a9197b94d9efb7b1b0be94ed835672a8` |
| Original manifest SHA-256 | `e218353a1f709a4ad55c4e1e4e2561415e364b2c8a4098174ed9eda8870f7959` |
| CLI executable SHA-256 | `231e2db637b4114069977f7795c618b4c5c1404e913fc53f78747dfe6588a7aa` |
| CLI archive SHA-256 | `043bae87f1180952a03ad84b8623e59d5df116de564fda9254d36d59fd62f76c` |
| Two-case acceptance SHA-256 | `17d33204ec054ca12b6c0ca1ebdd8890479c6abbe2085a67012255dbb13a3140` |

Only Linux/amd64 images and the Linux x64 CLI are selected. The six assets are
`install-aiv.sh`, `release-manifest.json`, `release-images.vars.yaml`,
`release-seekdb.vars.yaml`, `aiv-linux-x64.tar.gz` and `SHA256SUMS`. At original
qualification, all seven packages were private and associated with
`ob-labs/ai-vision-skills`. SigNoz and database mirrors preserved upstream native
child manifests, configs, layers and source identities. The current metadata uses
five private service packages and upstream database pins as recorded below.
ARM/macOS remain excluded.

## 2026-10-29 distribution correction

The maintainer explicitly authorized correcting the existing latest Release,
withdrawing the earlier Release and removing both database Packages. The `.2`
Release now retains the same CLI archive, installer asset IDs, five service image
digests, product source/build and catalog commit. Four metadata/checksum assets
and bilingual notes were replaced together and independently downloaded and
validated. The current manifest SHA-256 is
`357cca6c77ee7898d6e7461096286430e1e70ddbe7541ca36e60d9a3baabe4c5`.
The original manifest and acceptance records above remain historical local
evidence; they are not the current downloadable manifest.

The database refs are the fixed upstream pins from
[`release_assets.py`](../../scripts/ci/release_assets.py), matching the ordinary
product release. The registry now contains only the five service Packages, and
only the latest `.2` GitHub Release remains. The old Git tag was retained.
Databases are dependencies declared in the manifest and vars, not recurring
product Daily packages. Replace saved vars referring to the retired private
database mirrors with the current Release's vars before deployment.

The official AMD64 child manifests/config bytes, ordered layer descriptors and
rootfs identities match the original qualification. Fresh reads of retained
Docker-save data verified all 21 CE and six seekdb layer bodies against the fresh
upstream configs. The unchanged released Linux x64 CLI consumed both corrected
vars and rendered the expected source refs under AMD64 emulation. Two native-host
SSH attempts timed out. A complete new upstream layer-body pull was not completed;
registry availability was checked separately. This is source-reference acceptance
by content equivalence, not a new native SQL, lifecycle or model acceptance.

The CLI archive validator now checks every attribution member against trusted
bytes and validates raw tar/gzip structure. Regression cases carrying full owned
Python in a license or PAX metadata are rejected while the actual `.2` CLI remains
accepted. The publisher compares stable asset identity/content fields separately
from mutable download counters and asset order: its own authenticated downloads
must not be mistaken for a changed artifact. The complete local CI-helper suite
passed 148 tests, including real Go-linked CLI regressions and Daily partial-retry
checks. Hosted CI remains subject to the separately recorded billing restriction.

Keep every acceptance receipt input at an immutable path. An older envelope
referencing updated working-tree scripts correctly fails; retain it, verify any
identical historical files against the clean source snapshot, and create a new
explicit correction/promotion envelope binding the current tools and evidence.
Do not rewrite the historical acceptance to fit current code.

The final authenticated `daily` promotion passed for all five services, matching
the corrected manifest's exact single-AMD64 index digests and retaining the
full-version tags. It resumed the first two accepted aliases and updated the
remaining three after fixing the download-counter comparison. Only Skills
service aliases changed; the product repository's aliases and database upstream
refs were not republished.

The corrected offline scan archive is
`aivision-security-scan-0.0.1-daily.20261028.2-upstream-linux-amd64.tar.gz`
(1,004,385,505 bytes), SHA-256
`d39697cb0f0a8fe92960f5c94ff0dfac5b2f1cf481fe57529d41d2f0e9ef6102`.
Its embedded manifest matches the corrected Release. The unmodified Docker-save
`images.tar` retains SHA-256
`e1f1bd68c5d153268bad7088faf5c29a2a372757dec464da57f4aa91683fe5a4`;
seven configurations and 60 unique layers were checked during copy and readback.
Neither a Docker load nor vulnerability scanning was performed for this export.

## Source exclusion includes executable resources

The original CLI embedded complete Python bootstrap bodies despite selecting native
runtime. Unused Go resources still enter the executable. The required
`native_distribution` build tag now excludes the inventoried first-party Python program
payloads (seven byte patterns) before linking, including inline template/probe bodies.
Independent actual-ELF checks complement strict added-layer, nested-archive and metadata
audits. Exact approved public examples and upstream dependencies remain allowed;
filenames alone are not implementation-source findings.

The restricted CLI defaults to native runtime, rejecting source mode, source
blueprints/installers and standalone Compose files before mutations. Ordinary builds
retain source compatibility; the private artifact gate rejects them. This delivery
used the versioned native build command with `AIV_PRODUCT_VERSION=0.0.1-daily.20261028.2`
and the Linux/amd64 target. `make native-build` reads source `VERSION` (`0.0.1`); it does not select this Daily
version.
The qualified explicit Go command omits `-trimpath`.

## Runtime evidence and limits

Fresh deployment and retained-data recast passed on both OceanBase CE and seekdb,
including SQL authentication, OTLP/readback, native custom-port health checks and client
hook integration. Both recast and final destroy recorded database plus five application
exits of zero, expected stop signals and no OOM events. Each backend persisted one
successful rule diagnosis with zero new model calls. Cleanup preserved the original 41
containers, 12 running states and 180 image records.

Real process fixtures exposed a CE guard gap: immediate `pgrep` rejects brief draining
and already-dead zombies. Bounded verification corrects those cases while preserving OBD
errors/timeouts and failing closed for surviving or unknown processes. The earlier
exit-1 episode's cause remains unproven.

The native client ran on Bookworm/glibc 2.36. Its installer requires glibc 2.35 symbols;
older clients are unsupported, and 2.35 itself was not exercised. Controlled Codex
probes/transcripts exercised hooks without a model session. Kubernetes, ARM,
load/performance and vulnerability-scan results are outside this acceptance. Source
workflow `40767459852` created nine jobs but executed zero steps because of billing
limits; hosted CI did not pass.

This delivery used the native artifact producer, image auditor, and the retired
Skills publisher. Current native artifact guidance is in the
[qualification contract](release-contract.md). Offline vulnerability scanning
is a separate process and was outside this delivery's scope.
