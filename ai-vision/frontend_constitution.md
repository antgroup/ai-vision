# Frontend Constitution (shadcn/ui)

These rules apply to all frontend code in `aivision-fe/`. They are hard constraints for Claude Code and shared reference for the team.

## Component Selection

### 1. Base components: use shadcn/ui first

- For common UI elements (Button, Input, Select, Table, Dialog, etc.), use components from `components/ui/` (shadcn official).
- Do not re-implement what shadcn already provides. Check https://ui.shadcn.com/docs/components before creating a new component.

### 2. Complex components: research before building

- Priority: shadcn official -> shadcn community/examples (e.g., data-table recipe) -> self-implement.
- When self-implementing, design for reusability — not just the immediate use case.
- File organization by complexity: single file for focused components, directory for components with multiple strongly-related sub-components (e.g., `agent-graph/`).

### 3. Do not modify `components/ui/` files

- shadcn-generated files in `components/ui/` are treated as external dependencies — do not edit them directly.
- Customize appearance via props, `className`, or global CSS overrides in `index.css`.
- If extended behavior is needed, create a wrapper component outside `ui/`.

## Style Rules

### 4. No hardcoded style values

- Use design system tokens from `index.css` and `tailwind.config.js`.
- `text-textPrimary`, `bg-bgPage`, `rounded-lg`
- `text-[#333]`, `bg-[#f5f5f5]`, `rounded-[6px]`

### 5. Extract repeated styles to CSS

- Tailwind utility classes are fine for one-off styling.
- When the same combination appears in 3+ places, extract to `index.css` using `@apply` with a semantic class name.
- `.page-title { @apply text-xl font-semibold text-textPrimary; }`
- Do not copy the same Tailwind class combinations across multiple files.

## Other Conventions

### 6. Icons: lucide-react only

- All icons come from `lucide-react`. Do not introduce other icon libraries.

### 7. i18n: all user-facing text must use `t()`

- Wrap all visible strings with the `t()` function from `react-i18next`.
- `<span>No data</span>`
- `<span>{t('common.noData')}</span>`

### 8. Component props: TypeScript-first

- Define props with TypeScript `interface` or `type`.
- Use `React.forwardRef` for components that may need ref access.
- Set `displayName` on forwardRef components.
