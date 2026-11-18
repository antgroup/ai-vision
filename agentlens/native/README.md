# Native AgentLens packaging

This producer compiles the owned Python modules into CPython extension modules and
builds native process entry points. Source development continues to use the existing
editable install, stock Alembic command, and `python -I -S` custom-rule worker.

Run the build in a target Linux container with CPython **3.11.14**, GCC and matching
Python development headers. Install the exact compiler environment from
`requirements-build.txt` in a separate virtual environment. Cython uses
`binding=True` and `annotation_typing=False` to retain Python calling and annotation
behavior. `-O0 -g0` prioritizes compatibility; this is not a performance build.

Supply an upstream dependency virtual environment resolved from `agentlens/uv.lock`,
plus the exact lock file used for that resolution. The producer checks the lock
identity and removes the owned project installation, editable finders, and metadata
from every copied `site-packages` directory. On CPython configured to use `lib`,
the producer skips a top-level `lib64` symlink that points to the virtual
environment's own `lib` directory and keeps separate `lib64` trees.

```sh
/opt/compiler/bin/python /repo/agentlens/native/build.py \
  --source /repo/agentlens \
  --output /artifacts/agentlens \
  --dependency-venv /deps/venv \
  --dependency-lock /deps/uv.lock \
  --database-init-source /repo/deploy/bootstrap/database-init.py \
  --jobs 1
```

Use separate outputs for each target architecture. `--build-directory` optionally
selects a private compiler cache outside source, dependency, and runtime trees.
The producer rejects overlapping paths, an existing runtime without
`--replace-runtime`, and input changes during compilation. A failed build can leave
a partial stage; only a successful build followed by a content audit is eligible
for further qualification.

The output consists of `runtime/`, `input-hashes.json`, and `toolchain.json`.
`runtime/` contains only owned native modules/entry points, required configuration
data, and the upstream `.venv`; generated C, Python inputs, and compiler caches
remain outside that tree. The canonical database bootstrap source is an explicit
input and is compiled as `/app/database-init`, never copied as Python source.

Mount or copy `runtime/` at `/app` in the matching Python runtime, with
`PATH=/app/.venv/bin:$PATH` and `PYTHONPATH=/app/src`. Configuration and credentials
remain external environment or retained deployment files. The existing
`agentlens-entrypoint` selects native migration/bootstrap commands when present and
retains source-mode commands otherwise.

The `agentlens-migrate` entry point supports `heads`, `current`, `upgrade`,
`downgrade`, and the internal `read-org-id-file` bootstrap operation. Its adapter
loads the original compiled Alembic environment and revisions and uses the Alembic
version pinned in `uv.lock`; it does not replace the general development CLI.

The rule-worker launcher initializes CPython with `PyConfig_InitIsolatedConfig`
and `site_import=0`, verifies isolation flags, and imports its fixed sibling native
protocol implementation. It retains JSON-line transport, per-process code caching,
timeout/termination, worker replacement, and the one-shot fallback. The launcher
can report startup flags with `--check-isolation` for artifact verification.

Application imports, migrations, real diagnostic execution and write/readback,
onboarding, and every intended architecture must be qualified against the actual
staged artifacts before product images are assembled. A successful native build
alone does not qualify a distributed image or its layers.
