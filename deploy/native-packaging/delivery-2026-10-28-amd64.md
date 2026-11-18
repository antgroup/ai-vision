---
title: Private amd64 Daily delivery 2026-10-28
type: reference
audience: [A4]
runs: no
verified_on: 2026-10-29
sources:
  - deploy/native-packaging/images.py
  - scripts/ci/release_plan.py
  - scripts/build/build_info.py
  - deploy/native-packaging/Dockerfile.native-backend
  - deploy/native-packaging/Dockerfile.native-agentlens
---

# Private amd64 Daily delivery 2026-10-28

**Historical record:** the original GitHub Release was withdrawn on 2026-10-29
with maintainer authorization. Use the
[corrected revision 2](delivery-2026-10-28-amd64-revision2.md), whose current vars
reference official upstream databases. Original local artifacts and evidence
remain available for audit; this record is not a current download instruction.

This reference records the first completed private native-image delivery. The
release contains Linux/amd64 images and the Linux x64 CLI. The earlier
[artifact qualification](qualification-2026-10-28.md) describes different
candidates; its results do not substitute for this release's image acceptance.

**Later CLI finding:** byte inspection found three source-mode Python deployment
helpers embedded in the published Go executable. The image-layer audits remain
valid, but the first CLI does not satisfy the complete delivery source-exclusion
boundary. Its original bytes are preserved locally as historical evidence. The
corrected revision 2 separately qualified native executable content and replaced
this CLI; the old Release and private database Packages were later retired.

## Published identities

| Field | Accepted value |
| --- | --- |
| Product version | `0.0.1-daily.20261028` |
| Withdrawn private prerelease | `delivery-v0.0.1-daily.20261028` |
| Runtime source revision | `historical-source-revision-redacted` |
| Source workflow identity | `40726481038.1`, run number `100` |
| Catalog tag target | `e6d14d5fe3fa643a148b318a297fd6f3b71fffcc` in `ai-vision-skills` |
| Publisher revision | `historical-source-revision-redacted` in the product repository |
| Manifest SHA-256 | `ce0f23a9937046e525a374013b14e4473615a5f63f97e7a301e6a106cfee8fa2` |
| CLI archive SHA-256 | `bd91c1feb3b332286ee1bfa053716fa2c63abaafcce59fc3ff94175bf05d29f8` |
| CLI executable SHA-256 | `de3a313c2c602fd90c91f00edc3b5904b13fb5d80b6da11961de254c20b4d5a6` |

The publisher revision fixes draft discovery and resumption. It does not change
or relabel the accepted runtime artifacts. The Skills tag identifies catalog
source; the manifest identifies product source. The original snapshot date stays
fixed across retries and midnight. A later ARM delivery requires a new Daily
revision and must preserve this published release.

All image names below have the prefix
`ghcr.io/ob-labs/ai-vision-skills/`. Each accepted index has exactly one
Linux/amd64 child. The packages are private and associated with the Skills
repository; no additional users or teams were granted access.

| Image | Accepted index digest |
| --- | --- |
| `aivision-be` | `sha256:09c8d360c4e1c2742c39b65e60e70c9f2f1f2a84d2bef4d55c86a4ccc47d4393` |
| `aivision-fe` | `sha256:77cd8012d9cc99ef2eb4b6612e4968f418bfae7aa88996c8aabcc91d3319a978` |
| `antcollector-gw` | `sha256:2b2cf553040705df7db7dbcadb17b4f770aa342bf569b9b5edfa40191ee882ef` |
| `agentlens` | `sha256:df82dd1769ce37061d4920f60cdd2c0e35e6b3d06b2e5d09c0c88af0222cbdf8` |
| `signoz-ob` | `sha256:63c3e5d1926ca00c5068a9c74e978f8e4344cb39f33db80c8dcf4e3813479d7a` |
| `oceanbase-ce` | `sha256:1be0a46f823dc7167996c75fefec120c536cdc7955f8d85dbab520fe260758cf` |
| `seekdb` | `sha256:34640dcaf33e6208af5feade545f3d05661fff989ca4c203b603fee57d50ccad` |

## Acceptance scope

| Gate | Observed result |
| --- | --- |
| Native image source boundary | Independent host and build-machine audits agree: no prohibited first-party source or bytecode in added image layers, nested archives, or inspected metadata. Exact approved public examples and upstream dependencies remain allowed. This did not inspect the Go CLI's embedded resource bytes. |
| Artifact reuse | Complete input-name and input-hash sets match the frozen source. Original producer receipts, including null producer revisions, remain unchanged. New image wrappers and CLI carry the actual runtime revision. |
| Datastores | Native x86 fresh deployment and retained-data redeployment pass for OceanBase CE and seekdb, including SQL authentication and invalid-password checks. |
| Telemetry and queries | Real OTLP ingestion and authenticated API readback pass on both datastore paths. |
| Agent installation | Packaged native installer and released Codex hook pass in a native x86 Bookworm client with `--python /no-client-python`. Controlled Codex executable probes and transcripts exercise the hook path. |
| Diagnosis on CE | One saved rule task and one live Judge task produce two persisted `GOOD` records, with list/detail readback and request replay. One connectivity probe and one Judge HTTP attempt; no retries. Judge usage: 7,253 input and 232 output tokens. |
| Diagnosis on seekdb | One saved rule task produces a persisted `GOOD` record with list/detail readback and request replay. No model configuration reads or model/provider calls. |
| Runtime lifecycle | Five application services stop normally with exit code 0 and no OOM termination. Backend and AgentLens Docker health checks pass on configured custom ports. Two scratch-based Go images have no shell, so optional shell-based PID 1 snapshots are unavailable. |
| Published bytes | All six assets reread through authenticated downloads and checked; the installed CLI matches its executable hash. All seven image references pull and select native amd64 configurations. |
| Cleanup | Temporary read credentials and acceptance resources removed; original containers, image records, volumes, and networks preserved. A task-owned read-only base registry remains for the following ARM delivery. |

Manual source checks: native packaging 38 passed; build scripts 46 passed;
deployment checks ran 59 with 13 skips for unavailable local prerequisites;
publisher checks 22 passed after the draft-resume fix. The
[source workflow](https://github.com/ob-labs/ai-vision-ob/actions/runs/40726481038)
executed zero steps because of billing/spending limits. Hosted CI did not pass;
manual checks and artifact acceptance are separate evidence.

## Operating lessons

| Situation | Required handling |
| --- | --- |
| Source changes after compilation | Compare complete input sets and hashes. Recompile any changed producer; never rewrite old provenance to claim the new revision. |
| Go 1.25 CLI identity | Use the canonical build flags. The ad hoc `-trimpath` build omitted linker flags from inspected BuildInfo and failed the strict embedded-version gate. The accepted CLI was rebuilt without that flag. |
| Configurable service ports | Native Docker health probes must use the same `BE_PORT` and `AGENTLENS_PORT` values as the service. Default-port-only acceptance missed a real candidate failure. |
| Existing private draft | By-tag REST lookup does not expose an unpublished draft. The publisher falls back to bounded authenticated release listing, rejects ambiguous matches, and resumes only an exactly matching draft without replacing accepted assets. |
| Isolated installer verification | An isolated HOME may receive GitHub CLI device-state files. Check credentials and owned artifacts explicitly; an empty-HOME assertion is not an installation-success criterion. |
| Failed verification retry | Preserve the failed attempt. The first installation completed successfully; a later optional download retry timed out. The completion receipt uses the inspected first installation and records the unsuccessful retry. |
| Runtime metadata in bulk layers | Changing a small build-info file inside the backend runtime COPY changes the large layer. Separating metadata is a future optimization, not part of this accepted delivery. |
| Go embedded resources | Avoiding export or execution does not remove a resource from an executable. The original CLI includes the complete `backend-bootstrap.py` (2,325 bytes), `database-init.py` (2,354 bytes), and `wait-http.py` (564 bytes). Restricted builds must exclude legacy resources at compile time and check the actual ELF, including first-party inline programs in other templates. |

This delivery used the native artifact producer, image auditor, and the retired
Skills publisher. Current native artifact guidance is in the
[qualification contract](release-contract.md). Execution logs, detailed
acceptance receipts, and private test configuration stay outside Git.
The Release manifest and this reference record the accepted identities.

## Later CE shutdown investigation

A subsequent unpublished candidate used the same CE native child and managed
shutdown script. Its full amd64 case passed the functional and retained-data
checks but failed final database shutdown with exit code 1 and no OOM. All five
application services exited zero. Three bounded database-only stops and one
observed full-case rerun then exited zero; the original failure's cause remains
unconfirmed because its database process and OBD logs were not captured before
emergency cleanup.

Separate real Linux process fixtures demonstrate that the immediate `pgrep`
check rejects both a process still draining briefly and an already-dead zombie.
The bounded verification change addresses those demonstrated cases while
preserving OBD failures, timeouts, and failure for a surviving or unclassifiable
process. Those fixtures do not prove the cause of the earlier episode or replace
qualification of the corrected packaged CLI.

## Unverified scope

This release does not qualify ARM, Kubernetes, load or performance, a customer
account, or a real Codex model session. The Agent installer requires glibc symbols
available from 2.35; the exercised client is Bookworm with glibc 2.36. A direct
client run on glibc 2.32 failed and is unsupported by this artifact. Service
containers were nevertheless accepted on that x86 Docker host.

Content auditing is separate from the team's offline vulnerability scan. No
vulnerability-scan pass, hosted CI pass, or protection against native binary
reverse engineering is claimed.
