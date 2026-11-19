# Linux All-in-One

The All-in-One image runs the frontend, backend, ingest gateway, diagnosis engine, and data backend alongside an embedded seekdb instance. One process owns `pylibseekdb`. The backend, diagnosis engine, and data backend share its local MySQL Unix socket. The image and CLI are specific to a Linux architecture.

## Prepare an offline candidate from a qualified Daily

Use the exact source checkout, `release-assets-<attempt>` directory, and `candidate-acceptance.json` from a successful public Daily. Keep the assets as downloaded. The preparation command checks the frozen acceptance, resolves each of the five image indexes to its target Linux manifest and config digest, and stages files from the exact locally loaded images. The backend and diagnosis images must share the pinned Python 3.11 Bookworm base. Preload that base and the accepted images for the selected architecture.

Build the supervisor and seekdb owner as shown below. Pass the two pinned PyPI wheels for the selected architecture. This example targets Linux ARM64 and stages a gzip bundle for the deployment Skill's offline installer.

```sh
python3 deploy/all-in-one/build.py prepare-daily \
  --architecture arm64 --daily-assets "$release_assets" --acceptance "$acceptance" \
  --base "$python_base" --wheel "$seekdb_wheel" --pymysql-wheel "$pymysql_wheel" \
  --supervisor "$platform_supervisor" --db-owner "$platform_db_owner" --context "$context"

python3 deploy/all-in-one/build.py build \
  --context "$context" --tag "aiv-all-in-one:${daily_version}-arm64" --archive "$image_archive"

python3 deploy/all-in-one/build.py package-daily \
  --daily-assets "$release_assets" --acceptance "$acceptance" --archive "$image_archive" \
  --output "$candidate_dir"

python3 /path/to/install-all-in-one.py --from-dir "$candidate_dir"
```

Use a fresh candidate directory and local image tag. Retain the context and image build receipts alongside the architecture-specific archive, manifest, CLI, and checksums. The candidate retains the Daily identity and uses the same CLI bytes. Qualify the embedded database separately from the Daily's multi-container smoke. Check startup, Agent onboarding, trace and diagnosis readback, shutdown, and same-volume recreation on each intended Linux architecture.

## Reproduce the audited 1108 native prototype

The historical prototype uses the audited 1108 native application payload with the lib64 deduplication fix and its pinned Python base. The commands below describe that frozen input and how to build the two local launchers.

Build the supervisor with Go 1.25 on any supported build host. Build the database owner on the target Linux architecture with the Python 3.11 headers and shared library from the pinned Bookworm runtime. For ARM64, set `GOARCH=arm64` and use the ARM64 runtime for the owner build.

```sh
mkdir -p /tmp/aiv-all-in-one-build
CGO_ENABLED=0 GOOS=linux GOARCH=amd64 GO111MODULE=off go build \
  -trimpath -ldflags='-s -w' -o /tmp/aiv-all-in-one-build/aivision-all-in-one ./deploy/all-in-one
cc -std=gnu11 -O2 -Wall -Wextra -Werror $(python3-config --embed --cflags) \
  deploy/all-in-one/seekdb-owner/main.c $(python3-config --embed --ldflags) \
  -o /tmp/aiv-all-in-one-build/aivision-seekdb
```

Use `build.py prepare --help` for the input paths. It verifies the deduplicated 1108 Docker archive and both pinned wheels, then stages the selected files and compiled launchers. Use `build.py build --help` to assemble a local image and save a compressed Docker archive with its SHA-256 and byte count. The image build installs Debian nginx and tini. An already loaded exact Python base can be passed with `--base`. For ARM64, pass `--architecture arm64` to `prepare`, use the original audited 1108 ARM64 bundle and the matching seekdb wheel, and let `build` select the pinned ARM64 Python base or pass a verified local copy with `--base`. Preparation removes the duplicate `lib64` files only after confirming their contents and modes match the canonical `lib` files.

Run `prepare` on a host with GNU binutils for the target ELF formats. Pass
`--strip-amd64` with a GNU strip executable that supports Linux x86-64. For
ARM64 preparation, also pass `--strip-arm64` with a GNU strip executable
that supports Linux ARM64. For example, on Debian ARM64 use the native
`strip` for ARM64 and `x86_64-linux-gnu-strip` for x86-64.

Preparation removes debug data from the reviewed third-party Python libraries
in the backend and AgentLens. It verifies the original wheel `RECORD` entries,
checks the ELF runtime content and refreshes the entries for the changed
files. Both server architectures still carry both Linux Agent installer
providers. Preparation also removes debug data from the CPython libraries in
their shared x86-64 installer. The context receipt records the tool versions
and before/after file hashes and sizes. Prepare a new context when moving from
a build made before this step.

## Prepare dual-format preview assets

`repack.py` takes a trusted dual-architecture preview asset directory, including
its two manifests, gzip image archives, gzip Agent CLI archives, and
`SHA256SUMS`. When the input comes from a private GitHub Release, authenticate
the downloaded asset identities against GitHub before repacking. Use the
installer and sample Trace helper from the same reviewed Skills checkout.
The packaging host needs Python and the `zstd` CLI.

```sh
python3 deploy/all-in-one/repack.py \
  --source "$verified_assets" --output "$candidate_dir" \
  --release-tag "$preview_tag" \
  --installer "$skills_checkout/skills/aivision-deploy/scripts/install-all-in-one.py" \
  --sample-trace "$skills_checkout/skills/aivision-deploy/scripts/sample-trace.py"
```

Assign a new preview tag and use a fresh output directory whose parent exists.
Both gzip image archives and both CLI archives retain their original bytes.
The command verifies that each `.tar.zst` decodes to the same Docker tar, adds
its name, size, and digest to the corresponding manifest, and writes a new
`SHA256SUMS`. Retain the JSON printed to stdout with the qualification record
to identify the source preview and both Docker tar hashes.

Qualify the exact staged directory with `install-all-in-one.py --from-dir` on
native Linux x86-64 and ARM64 Docker hosts. Confirm the selected platform and
format, a healthy container, and a sample Trace visible in the UI. Exercise
the explicit gzip path or an older gzip-only preview with the same installer.
The resulting archive size measures download bytes. The image ID and base
release in the manifest identify the original runtime payload.

## Run locally

The commands below show the x86-64 artifact. For ARM64, use the ARM64 archive and the tag from its build receipt.

```sh
docker load -i /path/to/aiv-all-in-one-prototype-linux-amd64-health.tar.gz
docker network create aiv-aio-local
docker volume create aiv-aio-data
docker run -d --name aiv-aio-local --network aiv-aio-local \
  --cpus 2 --memory 6g --ulimit nofile=65536:65536 --ulimit core=0 \
  --stop-timeout 120 -v aiv-aio-data:/data \
  -p 127.0.0.1:5174:5174 -p 127.0.0.1:4318:4318 \
  aiv-all-in-one:prototype-20261113-amd64
```

Open `http://127.0.0.1:5174` after Docker reports the container healthy. The database uses a Unix socket. The example publishes the web and OTLP endpoints to host loopback. Keep the prototype on a dedicated bridge network because seekdb needs a network interface other than loopback to initialize. For custom host ports, set `AIVISION_PUBLIC_BASE_URL` and `AIVISION_OTLP_INGEST_ENDPOINT` to the actual published origins. Add a loopback mapping for container port 4317 if the local client sends OTLP/gRPC.

The `/data` volume stores seekdb, the SigNoz metastore, application logs, and generated settings. Stop the container before replacing it and attach the same volume to retain local state. Prototype validation uses a fresh volume, then recreates the container and reads back the retained Trace and diagnosis. Version upgrades and backup recovery need their own verification.
