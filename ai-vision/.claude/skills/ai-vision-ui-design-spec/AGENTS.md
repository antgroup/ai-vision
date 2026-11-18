# AI Vision UI Design Guidelines

> These guidelines apply to all AI coding tools, including Codex CLI, OpenCode, and Cursor.
> Claude Code users should use SKILL.md or place this file in the project root.

## Required Rules

Follow these guidelines when writing, changing, or reviewing frontend UI code, including HTML, CSS, React, Vue, and mini apps.

1. Read `references/design-tokens.md` in this directory first. It is the sole source of design tokens.
2. Use the defined values for every color, font size, corner radius, and spacing value. Do not invent alternatives.
3. Prefer CSS variables; see the template in section 5 of `design-tokens.md`.

## Core Constraints

- **Light mode**: these guidelines currently cover light mode only.
- **4 px grid**: all spacing must be a multiple of 4 px.
- **Restrained colors**: use varying opacity levels of `#000A1A` as the primary palette. Reserve color for semantic states: success, failure, and warning.
- **Font sizes**: only 12, 14, 16, 20, and 24 px are allowed.
- **Corner radii**: only 6, 8, 10, and 12 px are allowed; choose according to component height.
- **No shadows**: the specification defines no box shadows; do not add them.
- **No gradients**: do not use gradient colors.

## Self-Review Checklist

After generating code, check each item:

- [ ] Every color comes from the specification.
- [ ] Font sizes are limited to 12, 14, 16, 20, or 24 px.
- [ ] Corner radii are limited to 6, 8, 10, or 12 px.
- [ ] Spacing values are multiples of 4 px.
- [ ] No box shadows or gradients were added.
- [ ] Status colors are correct: success `#17A34A`, failure `#EE4444`, and warning `#EA5706`.
