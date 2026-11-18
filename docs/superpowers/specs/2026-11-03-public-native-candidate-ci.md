# Portable native candidate CI

## Accepted scope

Prepare this repository's CI for future open-source operation. The current
`ob-labs/ai-vision-skills` closed distribution is a temporary downstream channel;
user-facing Skills remain in their separate repository. Neither is a prerequisite
for source CI or candidate production here. This change does not open-source the
repository, move Skills, change licensing, or publish a delivery.

Keep automatic basic PR/push checks. Remove the legacy `Release images` nightly
schedule while retaining its explicit manual/tag entry points. Add a manual-only,
read-only-token native candidate workflow, initially Linux amd64. It must work
without private Packages, a Skills/catalog checkout, repository-specific secrets,
or publisher credentials.

## Candidate boundary

- Freeze the actual clean checkout, root product version, successful same-source
  `ci.yml` run/attempt, and separate producer workflow run/attempt. Never reuse the
  producer ID as a source-CI ID or claim private publisher acceptance.
- Require a digest-pinned official public CPython 3.11.14 slim Bookworm image.
  Use its pristine bytes as runtime base; install compiler tools only in a separate
  local builder image. Build natively with one compiler job and bounded resources.
- Reuse `build.py` for backend, AgentLens and x64 onboarding. Reuse `images.py`
  for artifact-only contexts and each complete saved image's layer/content audit.
  Local image/config IDs and archives identify these unpushed candidates.
- Build only Linux x64 Rust hooks plus the portable Node plugins, from this exact
  source. Keep the existing all-platform plugin workflow default unchanged.
- Backend provider example approvals use an explicit reviewed path allowlist;
  unexpected new examples fail, rather than gaining an automatic exemption.
- Build both the ordinary source-capable CLI and optional `native_distribution`
  CLI, with explicit version/VCS metadata outside the checkout. Native packaging
  does not become the only open-source CLI profile.
- Upload only candidate OCI archives, CLI archives and typed provenance/audits.
  Compiler work, generated C, raw logs, credentials and runtime configuration stay
  outside artifacts. Build/content/import/OCI checks are not deployment acceptance.

## Deferred work

Frontend/gateway product assembly and external SigNoz integration remain their
own capabilities. This native candidate is not a complete five-service delivery.
Actual `aiv` lifecycle, CE/seekdb, migrations, retained data, diagnosis, hooks,
readiness, shutdown, model/platform qualification, release assets, registry tags
and alias publication require later separate gates. No full candidate build,
deployment, Daily or Release is dispatched in this implementation task.

## Approach

Reuse the existing native producers and layer auditor in a neutral local-output
workflow. Reusing the legacy product publisher would couple candidates to a
different release contract. Making the temporary private Skills distribution the
default would prevent independent public/fork CI. Both alternatives are excluded
by the accepted repository boundary.
