# React + TypeScript + Vite + shadcn/ui

This is a template for a new Vite project with React, TypeScript, and shadcn/ui.

## Adding components

To add components to your app, run the following command:

```bash
npx shadcn@latest add button
```

This will place the ui components in the `src/components` directory.

## Using components

To use the components in your app, import them as follows:

```tsx
import { Button } from "@/components/ui/button"
```

## Trace Labs regression tests

Run from `ai-vision/vfe` with the Node version in `../.nvmrc` and the locked
dependencies (`pnpm install --frozen-lockfile`):

```bash
pnpm test:decision
pnpm test:mcp
pnpm exec playwright install chromium
pnpm test:e2e
```

The functional suites cover Choice / Score / Noul interpretation, native and
inferred decision evidence, reported token totals, Gantt timestamps, MCP secret
masking, space isolation, administrator permissions and asynchronous sync state.
They are also included in `pnpm test`.

`tests/fixtures/kev-4b-live-2026-10-16.json` preserves sanitized responses from
four actual SiliconFlow `kev-4b` requests: Choice / Score / Noul, routing, and
two authorization judgments. Regression tests retain all six answers and the
reported 490 input / 274 output tokens. These tests replay captured evidence;
they do not call the provider. Raw Noul probabilities are shown without claiming
that a tool was authorized or executed.

Decision reconstruction preserves producer evidence: Noul is a raw probability,
not confidence or approval; a later tool call does not establish human approval.
Inline SDK / HTTP requests and explicit native / observation payloads are
supported. A file-only command without linked source evidence is left as a
regular tool span rather than inferred from unrelated conversation code.

The Chromium suite starts a fresh Vite server on `127.0.0.1:41965`. It exercises
the real router, providers and components with deterministic API fixtures:
signed traces, public trace/session shares, summary-to-span navigation, filters,
MCP import, failed-sync recovery and the default trace time window. Unexpected API requests fail the tests;
public routes must not request private configuration. No account, model key or
running backend is needed. If a local HTTP proxy is configured, include
`127.0.0.1,localhost` in both `NO_PROXY` and `no_proxy`.

Failures retain screenshots and Playwright traces in ignored `test-results/`.
The Linux frontend workflow runs this suite and uploads failure evidence.
Browser API fixtures do not prove live ingestion or external model behavior.
Backend `apps.core.tests.test_labs_mcp` separately exercises real local stdio,
HTTP and SSE transports; live third-party MCP and production SigNoz acceptance
remain separate checks.
