# Ant AI Vision website

The approved product website is maintained as static HTML, CSS, and JavaScript in `docs/`. It supports Simplified Chinese, English, Japanese, and light/dark/system themes. No build step or third-party runtime is required.

## Preview

From the repository root:

```sh
python3 -m http.server 4321 --directory docs --bind 127.0.0.1
```

Open http://127.0.0.1:4321/.

## Documentation links

The navigation, deployment, integration, and feature guide links follow the selected language. Mintlify publishes user documentation and illustrations from the separate `ai-vision-docs` repository.

The website is configured to use the published documentation:

```html
<meta name="aivision-docs-base-url" content="https://aivision.mintlify.site">
```

The main entry points are `/{language}/introduction`, `/{language}/deployment/all-in-one`, `/{language}/deployment/docker`, and `/{language}/deployment/kubernetes`, with `zh-CN`, `en`, and `ja` available for each route. Static HTML links use the published Simplified Chinese routes before JavaScript runs.

The configured URL takes precedence in local previews as well. To preview documentation changes locally, run Mintlify from the separate documentation checkout and temporarily set the base to `http://localhost:3333`. An empty value uses that local preview on localhost or loopback, and falls back to `https://aivision.mintlify.site` on a published site. A base URL with a subpath is supported.

Keep the GitHub Pages website deployment independent from Mintlify. Deployment maintenance notes, configuration examples and development image release instructions remain in this product repository under [docs/deployment/](https://github.com/antgroup/ai-vision/tree/HEAD/docs/deployment).

## GitHub Pages

The default GitHub Pages project URL after publication is [antgroup.github.io/ai-vision](https://antgroup.github.io/ai-vision/).

This branch holds the static website and its maintenance notes. In the
`antgroup/ai-vision` repository, use **Settings → Pages → Deploy from a branch**
with `gh-pages` and `/docs`. The `docs/.nojekyll` file serves the static
files without Jekyll. GitHub Pages publishes changes to the selected source
automatically; review website changes before merging them.

Open the project URL after deployment and verify the relative assets, language
and theme controls, deployment dialog, and links to the live documentation.
Keep this README and the license outside the published folder.

Task trajectories, diagnosis findings, usage numbers, and structured model responses are labelled demos. The 70/910 story is an anonymized usage example, with no promised cost reduction. The public announcement link remains reserved in the HTML footer comment.

## Brand assets

The navigation, footer, and favicon reuse the product frontend’s official AI Vision mark from `ai-vision/vfe/public/assets/aivision.svg` and wordmark paths from `ai-vision/vfe/src/components/layout/AppLogo.tsx`. The wordmark inherits the selected page theme; the mark retains its brand gradient. Keep these assets aligned with the frontend when the identity changes.

The hollow full stop is a separate website motif, kept outside the official wordmark. It connects the hero, the diagnosis return path, and the footer: use evidence from one task to improve the next. Loop accents animate once on entering view, honor the page motion control and reduced-motion preference, and remain visible without animation.
