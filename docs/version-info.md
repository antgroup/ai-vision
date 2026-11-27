# Versions and Updates

Open `/about` from the global help menu to see **the current version, available updates, and release notes**. The root [VERSION](../VERSION) is the product version source, currently set to the release target `0.6.1`. Publication happens separately.

## Page Behavior and Update Checks

- The current product version comes from the installed release manifest supplied by the matching deployment variables. `GET /api/v1/version` returns this release record separately from the backend's actual embedded build identity. The frontend checks its own bundle and the responding backend against their respective expected component builds. Legitimately reused components can have different source SHAs and original build versions. Missing release metadata is shown as unknown; a mismatch is reported without inventing a version. Build details remain collapsed under **Diagnostics** and can be copied.
- Opening the page automatically calls `GET /api/v1/releases` to retrieve the latest stable release and its notes from `antgroup/ai-vision` on GitHub. Users can retry manually. The backend sends an unauthenticated request without deployment identity. If the backend returns update metadata from a retired release source, the page reports that updates could not be checked and links to the public release history.
- Stable versions are compared using the three numeric Pride Versioning components. Development, daily, and RC builds retain their identity and are never called the latest stable release merely because their base version matches.
- When offline, rate-limited, using a private repository, or awaiting the first stable release, the page keeps the current version visible and explains the check status. A failed check never means "up to date." The page does not perform upgrades.

GitHub's [latest release API](https://docs.github.com/en/rest/releases/releases#get-the-latest-release) excludes drafts and prereleases. The page uses the project's designated latest stable release as its reference. The backend caches results for five minutes and failures for one minute. Set `AIVISION_UPDATE_CHECK_ENABLED=false` to disable checks in isolated environments; local version and diagnostic information remain available.

The switch must be set in the **backend process environment**. For Docker deployments, export the current built-in configuration with `aiv gen -o blueprint.yaml`, add `AIVISION_UPDATE_CHECK_ENABLED: "false"` under `env` for the component named `be`, and recreate the backend container using that file with the original namespace and deployment arguments. If already using a custom blueprint, edit that file. For Kubernetes, set the variable on the `be` container in the `aivision-be` Deployment, complete the rollout, and retain it in subsequent deployment configuration. Exporting it only on the host or passing a same-named `--var` to the current built-in blueprint does not automatically forward it to the container. Verify that `/api/v1/releases` returns `data.status: disabled`.

**Check for updates** reads the backend result again and remains subject to these cache durations. A quick retry may return the previous result.

The page displays the GitHub Release body, including both language sections and contributor profile links, and links to the original. It renders Markdown without HTML or images; the contributor avatar wall lives in the READMEs. Component changes belong in the notes; the page does not maintain a separate component inventory.

## Verify a Release or Daily Deployment

For a stable public Release or qualified Daily preview, select a published
immutable product tag from `antgroup/ai-vision`. Get the matching CLI, manifest,
checksums and pinned image variables from that same Release. Pass the verified
`release-images.vars.yaml` (OceanBase CE) or `release-seekdb.vars.yaml` (seekdb)
to `aiv cast --vars-file` to select the public digests. For an existing
deployment, update the image overrides and release metadata in the original
private vars file. The manifest binds
`source_repository`, `release_tag` and `product_version` to the selected product tag.
`daily` is a moving pointer to those versioned assets, not a product version or a
CLI asset download tag.

After deploying and logging in, open **Help → About** or `/about` on the actual
Web endpoint. Compare the displayed version with `product_version` in the
verified `release-manifest.json`; a Daily shows its full
`X.Y.Z-daily.YYYYMMDD[.REVISION]` identity. The first snapshot for a date has no
revision suffix; the next accepted snapshot uses `.2`. Expand Diagnostics to compare frontend
and backend build identities against their own entries in that release record,
and copy these details for a mismatch report. Older component SHAs can be valid
when the manifest explicitly reuses those builds. A requested image tag alone
does not establish what is actually running. The page observes only frontend and backend identity; the
release manifest and runtime image inspection cover the other components.

Daily installations still check for the latest **stable** Release. The page
does not discover newer Daily builds; use the Daily Release's versioned-asset
link to select an upgrade, preserving the deployment's credentials, history
start and target configuration. Until a stable Release exists, the current
Daily remains visible with an explicit no-release or unavailable check status.

## One Set of Product Release Notes

The public product manifest binds the five service images and the companion CLI as one tested combination. The `frontend`, `backend`, `ingest-gateway`, `diagnosis` and `data-backend` packages under `ghcr.io/antgroup/ai-vision/` receive immutable `:<product_version>` tags and rolling aliases on publication. The `data-backend` reference pins the mirrored SigNoz index digest, and `data_backend_mirror` records its upstream and mirror coordinates. The version API validates historical standard and private Skills manifests alongside the public format. Reused images retain their original contents and build identities. Mention dependency changes in product notes when they affect users.

Stable release notes use English and Simplified Chinese in one GitHub Release body, with one shared contributor section. The Release Manager copies the [release-note template](../.github/RELEASE_TEMPLATE.md) into a draft for the intended tag and uses **Generate release notes** for the actual release range. Summarize user-facing changes, affected components, compatibility, upgrade guidance, and known issues consistently in both languages. Follow the [credit guidelines](../CONTRIBUTING.md#recognition-and-release-credits) to recognize verified code and non-code contributions and preserve the generated PR author and full changelog links.

GitHub supplies the release title, so do not repeat it as a heading in the body. Start with user-facing changes; keep source/build IDs and checksum information in an optional collapsed **Build details** section at the end. Generated development/Daily notes follow the same layout. Automation preserves GitHub-generated changes, PR authors and changelog links for the tested source and the previous versioned release, then appends deployment guidance and collapsed build details. It generates this content before publication writes; an existing rolling entry without one unambiguous versioned-asset link must be reconciled before automation can choose its range. Stable and RC drafts retain their reviewed bilingual notes.

The template and GitHub generator do not translate or publish notes automatically. The [release workflow](../.github/workflows/release-images.yml) defaults to candidate-only manual runs. An explicit publication passes source and artifact gates; stable/RC additionally requires the protected release environment and reviewed draft. A separately [promoted Daily/Dev candidate](../.github/workflows/promote-release.yml) reuses its frozen assets after verifying the successful source run/attempt and current default-branch head. Required remote setup and hosted acceptance are tracked in the [release guide](deployment/en/dev-image-release.md). The version page reuses that body, so it needs no separately maintained copy. PR descriptions supply the source material; extra product changelog fragments are not required.

## Build Identity and Validation

The [build identity generator](../scripts/build/build_info.py) reads the root VERSION and the actual Git checkout. It records the component, product and component versions, source SHA, build ID, time, target channel, and dirty state. New stable, RC, and daily builds require a clean, identifiable checkout. Reused images retain their actual original source, build ID and build-time version; these fields are not rewritten to impersonate the new release. Unknown values are not filled with historical internal package versions.

| Artifact | Version source |
|---|---|
| `aivision-fe` | `__AIVISION_BUILD_INFO__`, embedded in the compiled bundle |
| `aivision-be` | `/app/aivision_backend/build-info.json` in the image |
| `antcollector-gw`, `agentlens` | `/etc/aivision/build-info.json` in the image |
| `aiv` | Makefile reads root VERSION; inspect with `aiv --version` |

The manifest records `component_builds` for the four owned images and their build-context fingerprints for optional reuse. The generated vars assets carry `AIVISION_RELEASE_MANIFEST_BASE64`; the blueprint forwards it to the backend. This public release record is validated and allowlisted by the version API, not merged into the image's original build information. The page observes frontend/backend membership only; runtime image inspection covers the remaining services. Update comparison uses the installed product version, not an older reused frontend's build-time version.

The image workflow supplies `AIVISION_BUILD_INFO_BASE64` automatically for new builds. A Daily product version combines root VERSION with a fixed snapshot date, for example `0.6.0-daily.20261118`. The original workflow creation date in Asia/Shanghai supplies an omitted snapshot date; a manual candidate can explicitly retain its selected snapshot date across midnight. The date labels the selected candidate, not the last commit timestamp of every component. Actual source and build timestamps remain in the manifest and Diagnostics.

The first snapshot uses revision `1` without a suffix. If another snapshot for that date must be published after the first was accepted, select revision `2` to produce `0.6.0-daily.20261118.2`, then `3` as needed. Retrying a candidate keeps its date and revision; a workflow rerun does not increment the user-facing version. Published versions remain immutable. The full workflow run ID and attempt remain in the diagnostic build ID, and the cumulative workflow run number is only operational metadata. Manual `dev` builds retain their separate development identity. Builds do not edit VERSION.

For a formal release candidate, set VERSION to `0.6.0-rc.1` on the release commit and use the matching Git tag `v0.6.0-rc.1`. RC builds use the release test tier. Branch trials use the Daily or Dev version and stage images under `candidate-<run_id>-<attempt>`.

The release workflow schedules Daily at 02:23 Asia/Shanghai on the default `develop` branch. Successful publication creates a versioned prerelease and advances the rolling `daily` entry after asset readback. Manual candidates default to `publish=false` and retain their frozen assets for review. Manual Daily/Dev promotion requires the exact successful default-branch candidate, frozen asset hashes and both database smoke receipts. See the [release guide](deployment/en/dev-image-release.md) for download and validation details.

Direct Docker builds without this argument keep the identity unknown. Local Vite builds in a complete checkout read root VERSION and Git state. The current source-deployment blueprint mounts only the frontend subdirectory, without root VERSION or Git metadata, and does not generate the backend build file; both build identities may therefore be unknown. Without a valid installed release record, the product release is also unknown. Historical internal image-build entry points also lack this injection. The page never guesses a version from image tags or deployment parameters.

```bash
# Repository root: generate and validate build identity
python3 scripts/build/build_info.py --component aivision-be
python3 -m unittest discover -s scripts/build -p 'test_*.py'

# ai-vision/vfe/
pnpm test:build-info
pnpm test:version-info
pnpm typecheck
pnpm build

# ai-vision/aivision-py-be/
AIVISION_ENV=version-test uv run python manage.py test \
  apps.core.tests.test_build_info apps.core.tests.test_releases apps.core.tests.test_url_routing
```

Automated checks cover version comparison, release-note rendering, offline and failure states, retries, diagnostic details, and copying. API tests use controlled network fixtures. Browser screenshots must identify synthetic data and environment limits; they do not prove a complete image deployment.
