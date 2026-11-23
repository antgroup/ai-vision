# README illustrations

The root README is in [English](../../../README.md), with [Simplified Chinese](../../../README.zh-CN.md) and [Japanese](../../../README.ja.md) editions. This directory keeps earlier README illustrations and onboarding screenshots in English, Simplified Chinese, and Japanese.

| Asset | Subject |
| --- | --- |
| `observe.<locale>.webp` | Application and framework collection, runtime and usage dashboards, and investigation of individual runs. |
| `understand.<locale>.webp` | The same example run shown as a Chain tree, Graph, and Flow / Timeline, with a selected step and contextual details. |
| `improve.<locale>.webp` | Diagnostic scope and checks, human review of findings against trace evidence, and comparable quality results. |

The three groups of conceptual illustrations were created with the built-in ImageGen tool. The [prompt set](imagegen-prompts.md) records the copy, constraints, and localization instructions. Chinese editions establish the visual language; localized editions may condense copy and adjust spacing while preserving the same feature scope. The published WebP files preserve the original 1448 × 1086 dimensions and use quality 90. The original PNGs remain in Git history; requested prompt dimensions are not guaranteed export dimensions.

## Image size and automatic optimization

Prefer WebP for large illustrations. Keep SVG wordmarks as vectors and compare screenshot text, colors, and light/dark variants before replacing an image. Preserve useful resolution and update the corresponding links in every affected language. For example, using the [WebP encoder](https://developers.google.com/speed/webp/docs/cwebp):

```bash
cwebp -q 90 input.png -o docs/assets/readme/example.webp
```

The optional [image workflow](../../../.github/workflows/compress-readme-images.yml) uses [Calibre Image Actions](https://github.com/calibreapp/image-actions) to optimize changed JPEG, PNG, WebP, and AVIF files in this directory on same-repository PRs; if the GitHub file-list API fails, it scans this directory. It commits a result only when the file is at least 10% and 10 KiB smaller. This is quality-based compression, including PNG, so review the updated pictures before merging. Subsequent human commits may cause another optimization attempt.

Fork PRs use local compression and ordinary review. The workflow uses no personal token or `pull_request_target`; its checkout and processing scope stay within this directory. It requires Actions and the pinned third-party actions to be allowed. A committed workflow does not establish a successful hosted run. After a bot update, approve any waiting workflows and confirm that the latest SHA has the applicable checks; this optional image job is not a required merge check.

## Actual onboarding screenshots

| Asset pair | Content |
| --- | --- |
| `agent-market.zh-CN.light.png` / `agent-market.zh-CN.dark.png` | Chinese Agent Market separating custom Agent SDK integrations from dedicated application plugins |
| `codex-onboarding.zh-CN.light.png` / `codex-onboarding.zh-CN.dark.png` | Chinese Codex guide with client and Python prerequisites, restart and hook-trust instructions, collection checks, and the installation-command button |
| `agent-market.en.light.png` / `agent-market.en.dark.png` | English Agent Market |
| `agent-market.ja.light.png` / `agent-market.ja.dark.png` | Japanese Agent Market |

Market screenshots were refreshed on 2026-10-18 with Codex's in-app browser at its default 1280 × 720 viewport, in Chinese, English, and Japanese and both themes. They show the actual frontend and current integration catalog, backed by a local API fixture with a synthetic demo account, space, and connection state. No model or collection service is connected, and no installation token or reporting credential is displayed.

The unchanged Codex guide screenshots were captured from the earlier local demo deployment using Codex's in-app browser and dev-browser; their final files use dev-browser captures of the actual 640 × 552 card. They omit account controls and were taken before generating an installation command. All onboarding screenshots show product interfaces rather than ImageGen mockups; their sample data does not represent an external customer.

When adding a screenshot, use a `<picture>` with explicit dark/light sources and a light fallback. Match the screenshot and UI labels to the README language.

## Brand and language

- Keep the existing official [AI Vision wordmarks](../aivision-logo-light.svg) in the README header: dark ink on light pages and white on dark pages. Their lettering uses the original frontend vector outlines, with an explicit light / dark picture source and dark-ink fallback. Preview backgrounds must follow the same color scheme as those sources. Do not invent product logos inside illustrations; new capability pictures use only technical names and generic functional icons.
- Header badges are served by [Shields.io](https://shields.io/badges/static-badge), using its static badge format and `flat-square` style. Each links to the relevant section or license.
- Promote the Chinese interface and Chinese documentation only in the Chinese README. English and Japanese editions describe onboarding, integrations, diagnosis, deployment, and team usage without Chinese-UI promotional badges or copy.
- Keep the language editions aligned in feature scope. Provide descriptive alt text for each picture; the adjacent README text explains the same capabilities without requiring the images.

## Scope and sources

The capability illustrations are conceptual, not screenshots, deployment topology, or customer results. Example charts and findings do not assert measured gains, automatic code repair, or guaranteed quality. Diagnostic findings require review; compare similar tasks with the same checks and actual acceptance criteria.

Current implementation references:

- [Agent catalog](../../../ai-vision/agent-plugins/agents.json) and [plugin documentation](../../../ai-vision/agent-plugins/README.md).
- [Langfuse onboarding](../../../ai-vision/vfe/src/pages/LangfuseOnboardingPage.tsx) and [NeMo Relay onboarding](../../../ai-vision/vfe/src/pages/RelayOnboardingPage.tsx).
- [Trace views](../../../ai-vision/vfe/src/components/traces-lf/TraceDetailPanel/TraceDetailPanel.tsx).
- [Diagnostic task configuration](../../../ai-vision/vfe/src/components/diagnose/job/jobFormModel.ts).
- [Badcase review](../../../ai-vision/vfe/src/components/diagnose/badcase-detail/FeedbackBar.tsx) and [quality overview](../../../ai-vision/vfe/src/components/diagnose/badcase/QualityOverviewView.tsx).
- [Verification-after-edit check](../../../agentlens/src/agentlens/diagnoser/executors/rule/builtin/coding/test_execution.py).
