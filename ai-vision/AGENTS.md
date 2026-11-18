# AI Vision Project Guide

**Last updated:** 2026-08-19
**Stack:** Django (Python) + React + Vite + TypeScript

> Division of responsibility with `CLAUDE.md`: this document is the authoritative source for project facts and lasting engineering safeguards. `CLAUDE.md` contains only Claude Code workflow prompts and verification suggestions; it must not duplicate project facts. If the rules conflict, this document takes precedence.

## Project Overview

An AI Agent observability platform for monitoring usage, tokens, and sessions across Agent products.

## Repository Structure

```
ai-vision/
├── aivision-py-be/     # Django REST API backend
├── vfe/                # Active React + Vite + TypeScript frontend
├── agent-plugins/      # Third-party Agent telemetry plugins, native installers, and semantic tools
├── docs/               # Documentation
```

`aivision-fe/` is the retired legacy frontend. Do not add new changes there.

## Key Paths

| Task | Location |
|---|---|
| User / Space / Agent models | `aivision-py-be/apps/core/models/` |
| Agent-to-space routing | `aivision-py-be/apps/core/services/agent_routing.py` |
| V1 / shared endpoints | `aivision-py-be/apps/core/views/` |
| V2 dashboard/query endpoints (`SpaceAgentQueryConfigView`, `DatasourceConfigView`, etc.) | `aivision-py-be/apps/core/views/v2_query.py` |
| Serializers | `aivision-py-be/apps/core/serializers.py` |
| Query validation and errors | `aivision-py-be/apps/core/query/` |
| Trace query providers | `aivision-py-be/apps/core/trace_query/` |
| Django core tests | `aivision-py-be/apps/core/tests/` |
| V2 frontend entry | `vfe/src/pages/DashboardPage.tsx` |
| V2 dashboard entry | `vfe/src/agents/dashboards/DashboardEntry.tsx` |
| V2 Agent registry | `vfe/src/agents/registry/` |
| V2 dashboard implementations | `vfe/src/agents/dashboards/` |
| V2 frontend API client (`getDatasourceConfig` / `getQueryConfig` / `postAgentDashboardQuery`) | `vfe/src/agents/lib/observabilityV2Api.ts` |
| V2 query adapter / hooks | `vfe/src/agents/lib/dashboardQueryAdapter.ts`, `vfe/src/agents/shared/hooks/useSectionQuery.ts` |
| Frontend contracts and types | `vfe/src/lib/schemas/`, `vfe/src/agents/types/` |
| Third-party Agent catalog and installation builds | `agent-plugins/agents.json`, `agent-plugins/scripts/` |
| Plugin downloads and installation scripts | `aivision-py-be/apps/core/views/{download,onboarding}.py` |

## Core Engineering Rules

- **Table prefix:** all tables use `aivision_`.
- **Status fields:** commonly use `SmallIntegerField` with `0/1` values.
- **Timestamp fields:** use `gmt_create`, `gmt_modified`, and `gmt_deleted`.
- **API response shape:** `{code: 0, message: "success", data: {...}}`.
- **Authentication:** email accounts, server-side sessions, and HttpOnly cookies; mutating requests require CSRF validation.
- **Foreign keys:** all ForeignKey / ManyToMany fields use `db_constraint=False`.
- **Public Agent field:** `agent_product`.
- **`agent_product` identifies an Agent type; it is not a query filter:** `AgentTypeConfig.agent_product` locates the Agent type, and its name is retained for historical compatibility. Trace and event filters (for example, `resource.attributes.ant.agent.product = 'X'`) must read `DataSource.config["agent_product_filter"]`. Never use `AgentTypeConfig.agent_product` for SQL filtering or comparisons.
- **Legacy references:** internal and legacy uses of `agent_type` remain in the code. Do not rename them indiscriminately.
- **Active frontend:** `vfe/` only.
- **Preferred V2 endpoints:**
  - `GET /api/v2/spaces/{spaceId}/agent-products/{agentProduct}/query-config`
  - `GET /api/v2/spaces/{spaceId}/agent-products/{agentProduct}/datasource-config`
  - `POST /api/v2/spaces/{spaceId}/agent-products/{agentProduct}/query`
  - `POST /api/v2/spaces/{spaceId}/agent-products/{agentProduct}/query_batch`
- **V2 data source routing:** queries resolve through `AgentTypeConfig` and enabled SigNoz `DataSource` rows. Do not add data source/configuration tables for this path.
- **V2 query encryption:** the backend reads `AGENT_QUERY_ENCRYPTION_ENABLED` / `AGENT_QUERY_ALLOW_PLAIN` / `AGENT_QUERY_ENCRYPTION_KEYS`; the frontend reads `VITE_AGENT_QUERY_ENCRYPTION_ENABLED` / `VITE_AGENT_QUERY_ENCRYPTION_KID` / `VITE_AGENT_QUERY_ENCRYPTION_KEY_B64`.

## Lasting Engineering Safeguards

- Preserve the existing architecture and coding style.
- Do not hardcode business data, IDs, tokens, API responses, or UI state for a single request.
- Do not introduce one-off, ticket-specific hacks or branches unless they represent an actual product rule.
- Prefer extending existing models, serializers, views, API helpers, shared types, and page patterns over introducing parallel implementations.
- Keep corresponding backend, frontend, API, and type definitions consistent.
- If a requirement conflicts with the architecture, explain the tradeoff instead of silently forcing it into the implementation.
- **Never edit `vfe/src/components/ui/` directly; it is the shadcn/ui component library.** Update it only through `pnpm dlx shadcn@latest add/update <component>`. Put custom UI in `vfe/src/components/` outside `ui/`, or in `vfe/src/agents/`.

## V2 Agent Dashboard Rules

Follow the layers defined in `docs/v2/frontend-component-layers.md`:

```
registry -> DashboardEntry -> product dashboard -> scopes -> sections -> queries -> adapter/api
```

- Put new V2 dashboard business logic in `vfe/src/agents/`.
- Put dashboard implementations in `vfe/src/agents/dashboards/` and use the runtime registry to locate the owning implementation.
- Adding a product family requires a registry entry and alias/family metadata.
- Registry aliases select only the frontend implementation. Backend requests must preserve the original `agentProduct` value; Products in the same runtime family can share a UI and retain separate data sources.
- Let `useSectionQuery` / `useSectionQueries` determine each section's queries. Do not hardcode product-specific batching rules in the API or adapter.
- Use the `agentDashboardRoute` helper for all CodeFuse CLI dashboard, trace, and redirect links.

## Data Source Boundaries

- The active sources are `signoz` and `langfuse`. DataSource / SpaceDatasourceBinding configuration determines the default.
- SigNoz V2 dashboards accept only `queryKey/scope/timeRange/params`; do not add SQL templates.
- Retain Langfuse query adapters, shared templates, project provisioning, credential and identity bindings, sharing, and export.
- Diagnosis uses SigNoz only. When only Langfuse is available, clearly explain this limitation.
- Shared OTel components live in `vfe/src/components/traces-otel/`. Keep raw telemetry attribute names stable.

## Validation and Delivery Evidence

See the root [contribution guide](../CONTRIBUTING.md) for shared validation, screenshot, and evidence requirements. Select checks based on the platform change's actual impact on frontend/backend APIs, identity and space isolation, data source routing, and relevant callers. Local changes do not automatically require the full stack. Label fixtures and mocked services clearly; they do not establish acceptance of real external services or the entire call chain.

## Known Risks and Anti-Patterns

- `settings.py` still uses `ALLOWED_HOSTS = ["*"]`; production deployments must restrict it.
- Frontend test coverage is limited.
- Shared frontend/backend types and serializer field names are not fully aligned.
- The `gmt_deleted` soft-delete field affects counts and lists.
- Do not claim browser verification unless you actually opened and checked the application locally.

## Common Commands

```bash
# Start the backend development server
cd aivision-py-be && uv run python manage.py runserver

# Start the frontend development server
cd vfe && npm run dev

# Check frontend types
cd vfe && npm run typecheck

# Run backend V2/security regression tests
cd aivision-py-be && uv run python manage.py test \
  apps.core.tests.test_v2_query \
  apps.core.tests.test_v2_encryption_integration \
  apps.core.tests.test_v2_security_audit \
  apps.core.tests.test_v1_security
```

## Notes

- The frontend development server proxies `/api` to Django on port `8000` by default. Set `VITE_DEV_API_TARGET` when using a different backend address.
- When project summaries disagree, the current code is authoritative.
- [Claude Code workflow notes](CLAUDE.md) are in this directory.
