# VFE Frontend Directory Principles

This document defines the directory structure conventions for `vfe/src/`. AI assistants and contributors should follow these principles when adding or moving code.

## Top-Level Structure

```
vfe/src/
├── agents/          # Agent dashboard V2 business domain
├── features/        # Shared product domains (model connections)
├── components/      # Shared UI components (cross-domain)
├── contexts/        # React Context providers (cross-domain state)
├── hooks/           # Global custom hooks
├── lib/             # Global shared utilities and schemas
├── pages/           # Route-level page components
├── i18n/            # Internationalization
├── App.tsx          # App root
├── router.tsx       # Route definitions
└── main.tsx         # Entry point
```

## Core Principle: Co-locate with Business Domain

Files belong next to the code that uses them. If a file only serves one domain, it should live inside that domain's directory — not in a global grab-bag.

- Single-domain utility → place inside that domain (e.g., `agents/lib/`)
- Cross-domain utility → place in `lib/`
- Unused code → delete it, don't keep it "for later"

## Directory Rules

### `features/` — Shared Product Domains

`features/model-connections/` owns Space-scoped model connection forms, catalog, API, types, inventory, and detail UI. Integrations and diagnostic consumers use this same domain; each consumer retains its own prompts and runtime parameters. Legacy settings/diagnosis modules may contain thin compatibility exports, not a second implementation or store.

### `lib/` — Global Infrastructure Only

Only files used across multiple unrelated domains belong here:

| File | Purpose |
|------|---------|
| `utils.ts` | Tailwind `cn()` merge utility |
| `formatters.ts` | Number/duration/token/date formatting |
| `api.ts` | HTTP request layer |
| `schemas/` | Shared data contracts (observability types, space schema) |
| `timeFilter.ts` | Time range option definitions |
| `storageMigration.ts` | localStorage version migration |

Do NOT place agent-specific logic here. Agent utilities go in `agents/lib/`.

### `agents/` — Agent Dashboard V2 Domain

```
agents/
├── dashboards/     # Per-runtime dashboard implementations
│   ├── claudecode/   # ClaudeCode runtime dashboard
│   ├── codex/        # Codex runtime dashboard
│   ├── codefuse/     # CodeFuse runtime dashboard
│   └── common/       # Shared schema-compatible Agent dashboard
├── lib/            # Agent-domain utilities (routing, display, products, crypto, V2 API, adapter)
├── registry/       # Product-to-runtime map, runtime registry, capabilities
├── shared/         # Shared dashboard components and hooks (must have 2+ consumers)
├── traces/         # Trace entry and types
└── types/          # V2 domain type definitions
```

Rules:
- Runtime-specific dashboard directory names match the backend `runtime_type` value (e.g. `claudecode`, not `claude-code`)
- Runtime families with the same observability schema share `dashboards/common/`; `RUNTIME_REGISTRY` selects it by `runtime_type` while preserving the original product in API calls
- Product-level overrides get their own directory only when they have a genuinely distinct dashboard implementation
- Resolve an Agent's runtime with `resolveRuntimeType` in `registry/registry.ts`.
- Product overrides (different dashboard for the same runtime) go in `registry/registry.ts` `PRODUCT_OVERRIDES`; resolution checks the product override first, then falls back to `RUNTIME_REGISTRY`
- New agent-specific utilities go in `agents/lib/`, not `lib/`
- Shared dashboard components/hooks go in `agents/shared/`
- Do not add unused placeholder components — delete what isn't imported

### `components/` — Shared UI Components

```
components/
├── ui/             # shadcn/ui components (NEVER edit manually)
├── agents/guidance/  # Agent onboarding guidance and types
├── bizcharts/      # Business chart components (datatable, etc.)
├── traces/         # Shared Trace visualization components
├── layout/         # App shell, breadcrumb, sidebar
├── header/         # Header + agent selector
├── error/          # Error boundaries, guard components
└── ...             # Other cross-domain components
```

Rules:
- `components/ui/` is managed by shadcn CLI — never edit directly
- Keep domain-specific types with their consumers. Examples include `src/components/agents/guidance/types.ts` and `src/agents/types/langfuse.ts`.
- If a component only serves one page/domain, consider placing it there instead

Trace UI consistency:
- Reuse `traces-otel/shared/TraceBadge.tsx` for metadata, model names, and error labels across Chain, Graph, Timeline, and details. Do not create separate model-label styles for decision spans.
- Keep model identifiers neutral. Reserve status colors for confidence, errors, and explicit fallback or approval outcomes; retain chart colors where they identify span types or quantitative values.
- Reuse `labs/shared/LabsSection.tsx` for observability view enhancements: `variant="summary"` for user-input, decision, and MCP summaries; `variant="detail"` for decision and MCP span details. Keep its header, spacing, and tokens consistent in both themes.
- Declare the card's typed `feature` so the purple Enhanced view marker links to its localized documentation through `labs/shared/observabilityViewDocs.ts`. Keep category links and the independently controlled, neutral Beta maturity badge beside the heading, with wrapping and visible keyboard focus. The purple category color does not indicate trace health, confidence, completeness, or answer quality.
- Reuse these templates for Skill observability only after its view exists; do not create empty cards or unused templates for future features.

### `contexts/` — Cross-Domain State

React Context providers that are consumed across multiple domains. Examples: SpaceContext, AgentContext, SessionDrawerContext.

A Context is appropriate when:
- State/callbacks need to cross component boundaries without prop drilling
- A generic component needs optional behavior injection from a specific domain

### `hooks/` — Global Custom Hooks

Small, generic hooks used app-wide. Currently includes shadcn-required hooks and theme/tracking hooks.

Do not place domain-specific hooks here — those belong in their domain (e.g., `agents/shared/hooks/`).

### `pages/` — Route Pages

Each file corresponds to a route. Pages compose components from `components/` and `agents/` but contain minimal business logic themselves.

## Anti-Patterns to Avoid

1. **Orphan directories** — A directory with a single file that could live elsewhere
2. **Global grab-bags** — Dumping domain-specific code into `lib/` or `types/`
3. **Unused barrel exports** — If nothing imports a component, remove it
4. **Premature abstraction** — Don't create `shared/` components before they have 2+ consumers
5. **Separate `types/` directories** — Co-locate types with their consumers, not in a global `types/` folder

## Verification and PR Evidence

Follow the root [contribution guide](../../CONTRIBUTING.md) for screenshots, interaction checks and evidence. Changes to API clients, shared types, identity or space selection must cover the relevant backend contract and isolation behavior. Choose checks for the affected behavior, and label fixtures or mocked dependencies so local UI checks are not presented as full integration acceptance.

For UI copy, localization, model-span presentation, or token/cost summaries, read the [Frontend UI Quality Guide](../docs/frontend-ui-quality-guide.md). It defines product-copy versus raw-data boundaries, Chinese spacing, shared operation/model labels, unknown versus zero usage, and browser/real-model evidence requirements. Extend the shared helpers listed there instead of implementing separate rules per view.
