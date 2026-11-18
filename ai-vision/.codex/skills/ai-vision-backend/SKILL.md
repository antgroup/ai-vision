---
name: ai-vision-backend
description: AI Vision Django backend development workflow. Use when Codex needs to inspect, implement, debug, review, or verify API views, serializers, response schemas, models, migrations, services, adapters, datasource routing, authentication, Agent status behavior, observability queries, URL registration, or backend tests under aivision-py-be.
---

# AI Vision Backend

Use the repository code and root `AGENTS.md` as the source of truth. Preserve existing architecture, database conventions, and user changes.

## Authorization Corrections

If the user explicitly says the current edits were not requested or authorized, stop editing immediately and list every change produced in the current turn. Continue implementation only after new explicit authorization. Revert only when the user explicitly asks, and then remove only changes demonstrably produced by the current turn; preserve user changes, other-task changes, and anything of uncertain ownership. Verify and report the resulting diff so it shows only the current-turn changes were removed and no unauthorized edits continue to accumulate.

## Locate Ownership First

Before editing, identify:

1. URL registration in `aivision_backend/urls.py` and the included route modules in `apps/core/urls/`.
2. Owning view in `apps/core/views/`.
3. Serializer or manually assembled response schema.
4. Model and existing migration history.
5. Service, adapter, provider, or datasource routing layer.
6. Existing tests and security expectations.

Prefer these locations:

- Models: `apps/core/models/`
- V1 views: `apps/core/views/`
- V2 Agent query views: `apps/core/views/v2_query.py`
- Serializers: `apps/core/serializers.py`
- Services: `apps/core/services/`
- Query helpers/errors: `apps/core/query/`
- Trace providers: `apps/core/trace_query/`
- Tests: `apps/core/tests/`

Re-export views from `apps/core/views/__init__.py` where their route module imports them from `apps.core.views`.

## Layering

- Keep parameter/auth/response concerns in views.
- Put reusable business logic and external I/O in services.
- Extend existing models, serializers, services, adapters, and providers before adding parallel systems.
- Do not perform optional external health/status requests synchronously inside list endpoints. Return usable database data first; isolate slow refreshes behind bounded services or separate endpoints.
- Use configured `DataSource` and `AgentTypeConfig` rows for routing. Do not add a new datasource/config table for V2 Agent queries.

## API Contracts

- Preserve `{ "code": 0, "message": "success", "data": ... }` through `success_response`.
- Preserve error envelopes through `error_response`; do not return ad hoc shapes.
- Keep `agent_product` as the primary outward Agent field.
- Do not blindly rename internal or legacy `agent_type` references.
- Keep V1/V2 behavior compatible when both endpoints expose the same domain data.
- Apply `IsAuthenticated` and `check_space_access_or_error` or the established equivalent.
- Never expose credentials, upstream tokens, internal endpoints, or raw sensitive exceptions.

## Models And Data

- Tables use the `aivision_` prefix.
- Preserve `db_constraint=False` on ForeignKey and ManyToMany fields.
- Current timestamps are `gmt_create`, `gmt_modified`, and `gmt_deleted`.
- Filter soft-deleted rows with `gmt_deleted__isnull=True` where applicable.
- Use precise `update_fields` for model saves.
- Treat status values as domain contracts. Audit every consumer before changing or overloading their meaning.
- Add migrations only for genuine schema changes; include a conservative data migration when existing rows need reinterpretation.

## Agent And Datasource Rules

- Resolve outward products from `AgentTypeConfig.agent_product` while preserving legacy lookup compatibility.
- Prefer configuration-driven aliases such as `agent_name`, `agent_product`, and datasource `agent_product_filter`; do not hardcode product-specific branches.
- Registry/runtime values may select behavior, but must not replace outward `agent_product` identity.
- On upstream failure, preserve last known usable Agent state instead of marking all Agents unhealthy.
- Bound external connect/read timeouts and cache repeated status lookups when appropriate.

## Observability Queries

- Preserve both start and end time boundaries.
- Keep provider-specific time units and boundary semantics in the existing adapters.
- Avoid broad scans, all-column queries, and unbounded fan-out when a narrower existing query is available.
- Keep data source credentials out of code and logs.

## Implementation Workflow

1. Read current view, service/provider, schema, and tests.
2. Define the narrow behavioral contract and affected files.
3. Implement service/model logic before thin view wiring.
4. Register exports and URLs.
5. Add focused success, empty, authorization, upstream-error, and regression tests.
6. Keep unrelated refactors and formatting out of the diff.

## Verification

Run from `aivision-py-be/`:

```bash
uv run python manage.py check
uv run python manage.py test apps.core.tests.<relevant_module>
```

For V2/security-sensitive changes also run the relevant subset of:

```bash
uv run python manage.py test \
  apps.core.tests.test_v2_query \
  apps.core.tests.test_v2_encryption_integration \
  apps.core.tests.test_v2_security_audit \
  apps.core.tests.test_v1_security
```

Report existing warnings, timeouts, or unrelated baseline failures separately. Do not claim a suite passed when it timed out or was not run.

Before finishing, confirm:

- Response envelopes are consistent.
- Authentication and space access checks are present.
- `agent_product` remains outward-facing.
- External failures cannot block unrelated list/navigation behavior.
- Focused tests pass and no secrets appear in logs or diffs.
