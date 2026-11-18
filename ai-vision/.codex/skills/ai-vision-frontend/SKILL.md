---
name: ai-vision-frontend
description: AI Vision React + Vite + TypeScript frontend development workflow. Use when Codex needs to inspect, implement, debug, review, or verify frontend pages, Agent dashboards, API clients, schemas/types, adapters, profiles, widgets, Context state, loading/empty/error states, routing, localization, UI copy, trace/model presentation, usage completeness, Tailwind styling, shadcn composition, or browser behavior under the active vfe frontend.
---

# AI Vision Frontend

Use the repository code and root `AGENTS.md` as the source of truth. Preserve existing architecture and user changes.

## Scope

- Work only in `vfe/`; `aivision-fe/` is legacy.
- Never manually edit `vfe/src/components/ui/`. Install or update shadcn components through its CLI only.
- Do not modify backend code unless the request explicitly includes backend changes.
- Do not introduce dependencies without explaining the need first.

## Locate Ownership First

Before editing, identify:

1. Route/page entry.
2. API client and response envelope handling.
3. Shared or domain-local types/schema.
4. Adapter/hook/Context that owns state.
5. Final page, profile, widget, or Agent dashboard section.
6. Existing loading, empty, error, and retry behavior.

Prefer these locations:

- V2 API client: `vfe/src/agents/lib/observabilityV2Api.ts`
- V2 domain types: `vfe/src/agents/types/`
- V2 registry: `vfe/src/agents/registry/`
- V2 dashboards: `vfe/src/agents/dashboards/{product-or-runtime}/`
- Shared V2 hooks/components: `vfe/src/agents/shared/`
- Global API client: `vfe/src/lib/api.ts`
- Zod schemas: `vfe/src/lib/schemas/`
- Context state: `vfe/src/contexts/`
- Route pages: `vfe/src/pages/`

## Architecture Rules

For V2 Agent dashboards follow:

```text
registry -> DashboardEntry -> product dashboard -> scope -> section -> query -> adapter/API
```

- Keep new V2 code under `vfe/src/agents/`.
- Let sections choose query grouping through `useSectionQuery` or `useSectionQueries`.
- Registry aliases select UI implementation only. Preserve the original `agentProduct` in backend calls.
- Route CodeFuse dashboard/trace links through `agentDashboardRoute`.
- Extend existing API, schema, adapter, profile, and widget patterns before adding abstractions.

## API And Types

- Preserve `{ code, message, data }` responses.
- Use `agent_product` as the outward Agent field; do not blindly rename legacy `agent_type` references.
- Use `api` from `vfe/src/lib/api.ts` for V1 calls.
- Use functions in `observabilityV2Api.ts` for V2 calls and unwrap the envelope there.
- Define types beside the API or domain consumer unless multiple modules genuinely share them.
- Use `import type` for type-only imports.
- Do not hardcode API responses, business data, tokens, user IDs, or one-ticket UI state.

## React State

- Keep initial page loading separate from optional background refreshes.
- A non-critical health/status request must not disable unrelated Agent navigation.
- On background refresh failure, preserve the last usable data and expose a bounded error/fallback state.
- Ignore stale async responses after space, Agent, route, or time-range changes.
- Use TanStack Query for server state where the surrounding code already does.
- In Contexts, preserve existing URL/localStorage synchronization behavior.

## UI Rules

- Follow existing product design and root frontend guidance.
- Use `lucide-react` icons.
- Use Tailwind tokens and CSS variables; avoid hardcoded colors and inline styles except established dynamic cases.
- Use `t()` for new user-visible text and add locale keys consistently.
- Keep controls fully usable in loading, empty, error, disabled, and retry states.
- Do not remove focus styling globally or break keyboard navigation.

## Copy, Trace Presentation, and Usage

For copy/localization audits, model-span changes, or usage summaries, read the [Frontend UI Quality Guide](../../../docs/frontend-ui-quality-guide.md) before editing. It owns detailed examples, source pointers, and the acceptance matrix.

- Localize product-owned copy across all supported locales, including tooltips and accessible labels. Keep Chinese/Latin/number spacing readable; preserve raw telemetry, identifiers, and user content.
- Remove internal branding from product copy through display mappings, without breaking legacy fields or aliases.
- Reuse the shared model-span resolver and trace badges. Separate operation titles from model identifiers, preserve custom/original names, and keep search and selection consistent across views.
- Preserve unknown versus reported zero and known subtotal versus complete total. Prefer canonical backend usage semantics; never repair missing evidence by summing overlapping rollups.
- Verify rendered text and layout in the affected locales and themes. For real-model acceptance, follow the guide's provider-to-SDK-to-platform-to-UI evidence chain and state each data source's actual coverage.

## Time Filters

- Preserve both `start/from` and `end/to` boundaries.
- Use the resolved session/trace time range consistently across list, detail, download, and batch calls.
- Check query cost and reuse the existing range helpers before widening query windows.

## Implementation Workflow

1. Read the relevant files and contracts before editing.
2. State the narrow file set and behavioral change.
3. Extend existing helpers and state owners.
4. Keep unrelated formatting and refactors out of the diff.
5. Add focused tests where frontend infrastructure supports them.
6. Verify the affected flow in a browser when the request involves interaction or visual behavior.

## Verification

Run from `vfe/`:

```bash
npx eslint path/to/changed-file.tsx path/to/changed-file.ts
npm run typecheck
npm run build
```

After frontend code edits, run all three checks in that order and report each result explicitly: modified-file lint, TypeScript typecheck, and production build. Do not finish after reporting only a subset.

For documentation/skill-only edits, check links, referenced commands, skill metadata, and `git diff --check`; do not rerun the frontend or real-model stack solely for documentation changes.

If `npm run lint` is also run and reports errors, inspect every reported error and separate the results into:

- New errors introduced by the change, with count and locations.
- Existing full-project baseline errors, with count and a statement that they are unrelated to the change.

Report modified-file lint as passing only when the modified files contain no new lint errors. Never describe full-project lint as passing while any lint errors remain. Do not fix unrelated baseline failures without authorization.

Before finishing, confirm:

- `vfe/src/components/ui/` is untouched.
- Modified files have no new lint errors.
- Typecheck passes.
- Production build passes.
- API and frontend types agree.
- Loading, empty, error, and retry behavior is complete.
- Browser validation is claimed only if actually performed.
