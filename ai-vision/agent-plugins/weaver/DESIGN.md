# Design

## Source of truth
- Status: Active
- Last refreshed: 2026-07-03
- Primary product surfaces: Weaver Guard local dashboard at `/dashboard`.
- Evidence reviewed: `weaver/weaver-guard/src/server/dashboard.html`, `weaver/weaver-guard/src/server/dashboard.css`, `weaver/weaver-guard/src/server/mod.rs`, `weaver/README.md`.

## Brand
- Personality: calm, technical, local-first validation console.
- Trust signals: dense diagnostics, clear rules, visible forwarding state, copyable evidence.
- Avoid: AI-styled gradients, blue/purple glow, glassmorphism, oversized marketing layout.

## Product goals
- Goals: make semantic validation status easy to scan and make Rego/rule diagnostics easy to inspect.
- Non-goals: marketing website, visual storytelling, brand-heavy decoration.
- Success signals: dashboard loads quickly, findings remain readable, controls follow familiar shadcn/ui defaults.

## Personas and jobs
- Primary personas: agent instrumentation developers, observability maintainers.
- User jobs: inspect recent OTLP payloads, find violations, review policy rules, copy repair context.
- Key contexts of use: local development, replay debugging, live hook validation.

## Information architecture
- Primary navigation: signal tabs for Traces, Events, Metrics.
- Core routes/screens: `/dashboard`, diagnostics tables, findings views, Rego rules, payload modals, API/guide modals.
- Content hierarchy: global metrics, policy/forwarding controls, signal details, backend embeds.

## Design principles
- Principle 1: Use quiet black/white defaults so diagnostic data carries the emphasis.
- Principle 2: Preserve semantic status colors only where they improve scanning.
- Tradeoffs: neutral UI reduces visual flourish; status badges still use restrained red/yellow/green.

## Visual language
- Color: shadcn/ui default zinc-style tokens; the dashboard defaults to light mode and only uses dark tokens when an explicit `.dark` class is present.
- Typography: system sans plus monospace for code, payloads, and paths.
- Spacing/layout rhythm: compact dashboard spacing with stable grid tracks.
- Shape/radius/elevation: 8px cards, 6px controls, minimal shadow, no glass blur.
- Motion: minimal tab/panel animation only.
- Imagery/iconography: none required for this developer console.

## Components
- Existing components to reuse: buttons, metric cards, tabs, badges, tables, modals, rule trees.
- New/changed components: theme tokens and component radii only.
- Variants and states: primary, secondary, outline, ghost, destructive; active tabs mirror shadcn tabs.
- Token/component ownership: `weaver/weaver-guard/src/server/dashboard.css`.

## Accessibility
- Target standard: readable contrast for local dashboard use.
- Keyboard/focus behavior: keep visible focus rings on buttons, tabs, selects, diagnosis controls.
- Contrast/readability: black/white defaults with muted foreground for secondary text.
- Screen-reader semantics: preserve existing semantic HTML and aria labels.
- Reduced motion and sensory considerations: avoid decorative motion and glow.

## Responsive behavior
- Supported breakpoints/devices: desktop-first with existing mobile collapse under 980px.
- Layout adaptations: preserve current grid-to-single-column behavior.
- Touch/hover differences: hover states must not be the only way to understand state.

## Interaction states
- Loading: keep existing loading text and table placeholders.
- Empty: keep dashed neutral empty panels.
- Error: destructive red only for destructive or violation/error states.
- Success: restrained green only for pass/forwarding states.
- Disabled: muted foreground/background.
- Offline/slow network, if applicable: keep recent-data and backend placeholders visible.

## Content voice
- Tone: concise operational Chinese/English labels already used by the dashboard.
- Terminology: keep Rego, OTLP, Trace, Event, Metric, Finding, Payload.
- Microcopy rules: no feature-tour copy in the main dashboard; put instructions in modals.

## Implementation constraints
- Framework/styling system: Rust `include_str!` static HTML/CSS/JS, no frontend build pipeline.
- Design-token constraints: use CSS custom properties that mirror shadcn/ui default black/white theme.
- Performance constraints: no external fonts, images, or heavy visual effects.
- Compatibility constraints: keep current dashboard routes and IDs stable for tests and JS.
- Test/screenshot expectations: run weaver-guard tests and verify served CSS/rendered styles after theme changes.

## Open questions
- [ ] None for the current shadcn/ui black/white theme request.
