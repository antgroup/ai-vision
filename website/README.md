# AI Vision website

The approved product website is maintained as static HTML, CSS, and JavaScript in `public/`. It supports Simplified Chinese, English, Japanese, and light/dark/system themes. No build step or third-party runtime is required.

## Preview

From the repository root:

```sh
python3 -m http.server 4321 --directory website/public --bind 127.0.0.1
```

Open http://127.0.0.1:4321/.

## Documentation links

The navigation Docs link and Docker/Kubernetes guide links follow the selected language. Mintlify publishes user documentation and illustrations from the separate `ai-vision-docs` repository.

The website is configured to use the published documentation:

```html
<meta name="aivision-docs-base-url" content="https://aivision.mintlify.site">
```

Routes are `/{language}/quickstart`, `/{language}/deployment/docker` and `/{language}/deployment/kubernetes`, with `zh-CN`, `en` and `ja` available for every route. Static HTML links also point to the live documentation before JavaScript runs.

The configured URL takes precedence in local previews as well. To preview documentation changes locally, run Mintlify from the separate documentation checkout and temporarily set the base to `http://localhost:3333`. An empty value uses that local preview on localhost or loopback, and falls back to `https://aivision.mintlify.site` on a published site. A base URL with a subpath is supported.

Keep the GitHub Pages website deployment independent from Mintlify. Deployment maintenance notes, configuration examples and development image release instructions remain in this product repository under [docs/deployment/](../docs/deployment/README.md).

## GitHub Pages

The default GitHub Pages project URL after publication is [antgroup.github.io/ai-vision](https://antgroup.github.io/ai-vision/).

The [Publish website workflow](../.github/workflows/website-pages.yml) deploys from `main` in the public `antgroup/ai-vision` repository. No build step or separate website repository is needed.

### First publication

Once this repository has been made public:

1. Open **Settings → Pages** and select **GitHub Actions** as the publishing source.
2. Merge the website PR into `main`. Website changes on `main` trigger the **Publish website** workflow automatically.
3. If the PR was already merged before the repository became public or Pages was configured, open **Actions → Publish website → Run workflow**, select `main`, and run it once. Changing repository visibility alone does not trigger this workflow.
4. Check that the deployment succeeds and open the URL shown in the `github-pages` environment. Enable **Enforce HTTPS** in Pages settings if it is not already enabled.

For the one-time source setup, see [GitHub's custom Pages workflow guide](https://docs.github.com/en/pages/getting-started-with-github-pages/using-custom-workflows-with-github-pages).

### Subsequent publications

Push or merge reviewed changes under `website/` into `main`. The workflow uploads `website/public` and deploys it automatically. Changes to the workflow itself also trigger publication. A manual run on `main` can retry a failed deployment.

The workflow publishes the static artifact directly; no `gh-pages` branch is required. Only files under `website/public` are included. Keep that directory limited to `index.html`, `styles.css`, `themes.css`, `app.js`, `preferences.js`, `translations.js`, `.nojekyll`, and the official brand files in `assets/`. All local references are relative and work at the project subpath. Maintainer instructions belong outside the published directory.

Task trajectories, diagnosis findings, usage numbers, and structured model responses are labelled demos. The 70/910 story is an anonymized usage example, with no promised cost reduction. The public announcement link remains reserved in the HTML footer comment.

## Brand assets

The navigation, footer, and favicon reuse the product frontend’s official AI Vision mark from `ai-vision/vfe/public/assets/aivision.svg` and wordmark paths from `ai-vision/vfe/src/components/layout/AppLogo.tsx`. The wordmark inherits the selected page theme; the mark retains its brand gradient. Keep these assets aligned with the frontend when the identity changes.

The hollow full stop is a separate website motif, kept outside the official wordmark. It connects the hero, the diagnosis return path, and the footer: use evidence from one task to improve the next. Loop accents animate once on entering view, honor the page motion control and reduced-motion preference, and remain visible without animation.
